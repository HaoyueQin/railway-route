//! HTTP API 响应构造（对齐 Python src/main.py::APIHandler 的 _search/_match/_train）。
//!
//! 注意 /api/train 的时刻格式与 /api/search 不同（train 用 {minutes,time,day,display}，
//! display 为 "HH:MM+N"；search 用 format_absolute_minutes 的 {minutes,time,day_offset,display}）。

use crate::csa;
use crate::graph::Graph;
use crate::json::Json;
use crate::matcher::{fuzzy_match, MatcherData};
use crate::models::{format_absolute_minutes, PathSegment, RouteResult, SearchRequest, SearchResponse};
use crate::score::score_routes;
use crate::validation::{build_search_request, ValidationError};
use std::collections::HashMap;

/// 时间结构 Json（对齐 format_absolute_minutes 的 {minutes,time,day_offset,display}）。
fn abs_minutes_json(minutes: i32) -> Json {
    let (m, time, day_offset, display) = format_absolute_minutes(minutes);
    Json::Object(
        [
            ("minutes".to_string(), Json::Number(m as f64)),
            ("time".to_string(), Json::String(time)),
            ("day_offset".to_string(), Json::Number(day_offset as f64)),
            ("display".to_string(), Json::String(display)),
        ]
        .into_iter()
        .collect(),
    )
}

/// 类型化段 → Json（对齐 typed_route_to_dict 的 segments 构造）。
fn segment_to_json(segment: &PathSegment) -> Json {
    match segment {
        PathSegment::Train(s) => Json::Object(
            [
                ("type".to_string(), Json::String("train".into())),
                ("train_code".to_string(), Json::String(s.train_code.clone())),
                ("from_station".to_string(), Json::String(s.from_station.clone())),
                ("to_station".to_string(), Json::String(s.to_station.clone())),
                ("depart".to_string(), abs_minutes_json(s.depart_minutes)),
                ("arrive".to_string(), abs_minutes_json(s.arrive_minutes)),
                ("travel_minutes".to_string(), Json::Number(s.travel_minutes as f64)),
                ("distance".to_string(), Json::Number(s.distance as f64)),
            ]
            .into_iter()
            .collect(),
        ),
        PathSegment::Interstation(s) => Json::Object(
            [
                ("type".to_string(), Json::String("interstation".into())),
                ("from_station".to_string(), Json::String(s.from_station.clone())),
                ("to_station".to_string(), Json::String(s.to_station.clone())),
                ("start".to_string(), abs_minutes_json(s.start_minutes)),
                ("end".to_string(), abs_minutes_json(s.end_minutes)),
                ("transfer_minutes".to_string(), Json::Number(s.transfer_minutes as f64)),
                ("city_code".to_string(), Json::String(s.city_code.clone())),
                ("city_name".to_string(), Json::String(s.city_name.clone())),
                ("estimate_source".to_string(), Json::String("user_default".into())),
            ]
            .into_iter()
            .collect(),
        ),
    }
}

/// 类型化路线 → Json（对齐 typed_route_to_dict；score 已 round 3 位）。
pub fn route_to_dict(route: &RouteResult, score: f64) -> Json {
    let segments: Vec<Json> = route.segments.iter().map(segment_to_json).collect();
    Json::Object(
        [
            ("score".to_string(), Json::Number((score * 1000.0).round() / 1000.0)),
            ("actual_origin".to_string(), Json::String(route.actual_origin.clone())),
            ("actual_destination".to_string(), Json::String(route.actual_destination.clone())),
            ("first_departure".to_string(), abs_minutes_json(route.first_departure)),
            ("final_arrival".to_string(), abs_minutes_json(route.final_arrival)),
            ("total_minutes".to_string(), Json::Number(route.total_minutes as f64)),
            ("rail_distance".to_string(), Json::Number(route.rail_distance as f64)),
            ("train_transfers".to_string(), Json::Number(route.train_transfers as f64)),
            (
                "interstation_transfers".to_string(),
                Json::Number(route.interstation_transfers as f64),
            ),
            ("interstation_minutes".to_string(), Json::Number(route.interstation_minutes as f64)),
            (
                "transfer_cities".to_string(),
                Json::Array(route.transfer_cities.iter().map(|c| Json::String(c.clone())).collect()),
            ),
            ("segments".to_string(), Json::Array(segments)),
        ]
        .into_iter()
        .collect(),
    )
}

fn error_json(code: &str, message: &str) -> Json {
    Json::Object(
        [
            (
                "error".to_string(),
                Json::Object(
                    [
                        ("code".to_string(), Json::String(code.to_string())),
                        ("message".to_string(), Json::String(message.to_string())),
                    ]
                    .into_iter()
                    .collect(),
                ),
            )
        ]
        .into_iter()
        .collect(),
    )
}

/// 下一档搜索强度（无方案自动升级链 fast→balanced→thorough→complete）。
fn next_profile(p: &str) -> Option<&'static str> {
    match p {
        "fast" => Some("balanced"),
        "balanced" => Some("thorough"),
        "thorough" => Some("complete"),
        _ => None,
    }
}

/// 单次搜索 → (搜索响应, 完整 payload)。错误直接转 HTTP 错误响应。
fn build_payload(
    graph: &Graph,
    matcher: &MatcherData,
    request: &SearchRequest,
    t0: std::time::Instant,
) -> Result<(SearchResponse, Json), (u16, Json)> {
    let response = match csa::search(graph, matcher, request) {
        Ok(resp) => resp,
        Err(msg) => {
            if msg.starts_with("未找到匹配的车站") {
                return Err((400, error_json("STATION_NOT_FOUND", &msg)));
            }
            eprintln!("[api] internal error: {msg}"); // 日志保留内部信息，响应只回通用消息
            return Err((500, error_json("INTERNAL_ERROR", "搜索内部错误，请重试")));
        }
    };
    let scored = score_routes(&response.routes);
    let routes: Vec<Json> = scored
        .iter()
        .map(|(s, idx)| route_to_dict(&response.routes[*idx], *s))
        .collect();
    let time = t0.elapsed().as_secs_f64();
    let time = (time * 10.0).round() / 10.0; // round(x, 1)
    let payload = Json::Object(
        [
            ("routes".to_string(), Json::Array(routes)),
            ("time".to_string(), Json::Number(time)),
            (
                "source_stations".to_string(),
                Json::Array(
                    response
                        .source_stations
                        .iter()
                        .map(|s| Json::String(s.clone()))
                        .collect(),
                ),
            ),
            (
                "target_stations".to_string(),
                Json::Array(
                    response
                        .target_stations
                        .iter()
                        .map(|s| Json::String(s.clone()))
                        .collect(),
                ),
            ),
            ("complete".to_string(), Json::Bool(response.complete)),
            ("profile".to_string(), Json::String(request.search_profile.clone())),
            ("scanned".to_string(), Json::Number(response.scanned_connections as f64)),
            ("generated".to_string(), Json::Number(response.generated_states as f64)),
            ("cached".to_string(), Json::Bool(false)),
        ]
        .into_iter()
        .collect(),
    );
    Ok((response, payload))
}

/// 搜索 + 无方案自动升级：fast 档可能截断小站长途组合（如燕郊→玉山南），
/// 无方案且未完整时按档位链自动升级重搜（至 complete 为止）。
/// 升级发生时 payload 附加 requested_profile（用户所选档位）与 upgraded=true。
fn run_with_upgrade(
    graph: &Graph,
    matcher: &MatcherData,
    request: &SearchRequest,
) -> Result<Json, (u16, Json)> {
    let t0 = std::time::Instant::now();
    let mut cur = request.clone();
    let mut upgraded = false;
    let (mut resp, mut payload) = build_payload(graph, matcher, &cur, t0)?;
    while resp.routes.is_empty() && !resp.complete {
        let next = match next_profile(&cur.search_profile) {
            Some(n) => n,
            None => break, // 已是 complete：真实无路，返回空结果
        };
        cur.search_profile = next.to_string();
        upgraded = true;
        let (r2, p2) = build_payload(graph, matcher, &cur, t0)?;
        resp = r2;
        payload = p2;
    }
    if upgraded {
        if let Json::Object(ref mut o) = payload {
            o.insert("upgraded".to_string(), Json::Bool(true));
            o.insert(
                "requested_profile".to_string(),
                Json::String(request.search_profile.clone()),
            );
        }
    }
    Ok(payload)
}

/// 处理 /api/search（对齐 APIHandler._search；time 字段用 elapsed 毫秒换算秒）。
/// `cache` 为 Some((缓存, 数据指纹)) 时启用结果缓存：同参数重复查询秒回，
/// 命中时返回缓存体并把 cached 置 true（对齐 Python SearchCache 语义）。
pub fn api_search(
    graph: &Graph,
    matcher: &MatcherData,
    query: &HashMap<String, String>,
    cache: Option<(&mut crate::cache::SearchCache, &str)>,
) -> (u16, Json) {
    let request: SearchRequest = match build_search_request(query) {
        Ok(r) => r,
        Err(ValidationError { code, message }) => return (400, error_json(&code, &message)),
    };
    // ── 查询缓存（同参数重复查询秒回；数据更新后指纹变化自动失效）──
    if let Some((c, fp)) = cache {
        let key = crate::cache::request_key(&request, fp);
        if let Some(body) = c.get(&key) {
            match crate::json::parse(body) {
                Ok(mut obj) => {
                    if let Json::Object(ref mut o) = obj {
                        o.insert("cached".to_string(), Json::Bool(true));
                    }
                    return (200, obj);
                }
                Err(e) => eprintln!("[cache] parse 失败: {e} (len={})", body.len()),
            }
        }
        let payload = match run_with_upgrade(graph, matcher, &request) {
            Ok(p) => p,
            Err(e) => return e,
        };
        c.put(key, payload.to_string());
        return (200, payload);
    }
    match run_with_upgrade(graph, matcher, &request) {
        Ok(payload) => (200, payload),
        Err(e) => e,
    }
}

/// 处理 /api/match（对齐 APIHandler._match：默认前 15 个建议，limit 可放大）。
pub fn api_match(
    graph: &Graph,
    matcher: &MatcherData,
    query: &HashMap<String, String>,
) -> (u16, Json) {
    let q = query.get("q").cloned().unwrap_or_default();
    let limit = query
        .get("limit")
        .and_then(|s| s.parse::<usize>().ok())
        .map(|n| n.clamp(1, 500))
        .unwrap_or(15);
    let matches = fuzzy_match(graph, matcher, &q);
    let names: Vec<Json> = matches.iter().take(limit).map(|(_, n)| Json::String(n.clone())).collect();
    (
        200,
        Json::Object(
            [("matches".to_string(), Json::Array(names))].into_iter().collect(),
        ),
    )
}

/// 处理 /api/train（对齐 APIHandler._train；时刻格式 {minutes,time,day,display}）。
pub fn api_train(graph: &Graph, query: &HashMap<String, String>) -> (u16, Json) {
    let code = query.get("code").cloned().unwrap_or_default();
    let code = code.trim().to_string();
    let stops = match graph.train_stops.get(&code) {
        Some(st) => st,
        None => {
            return (
                404,
                error_json("NOT_FOUND", &format!("未找到车次 {code}")),
            );
        }
    };
    fn fmt_minutes(m: i32) -> Json {
        if m < 0 {
            return Json::Null;
        }
        let hour = (m / 60) % 24;
        let minute = m % 60;
        let clock = format!("{hour:02}:{minute:02}");
        let day = m / 1440;
        let display = if m >= 1440 { format!("{clock}+{day}") } else { clock.clone() };
        Json::Object(
            [
                ("minutes".to_string(), Json::Number(m as f64)),
                ("time".to_string(), Json::String(clock)),
                ("day".to_string(), Json::Number(day as f64)),
                ("display".to_string(), Json::String(display)),
            ]
            .into_iter()
            .collect(),
        )
    }
    let stop_json: Vec<Json> = stops
        .iter()
        .map(|(sidx, dep, arr, seq, dist)| {
            Json::Object(
                [
                    ("station".to_string(), Json::String(graph.idx_to_station[*sidx].clone())),
                    ("depart".to_string(), fmt_minutes(*dep)),
                    ("arrive".to_string(), fmt_minutes(*arr)),
                    ("seq".to_string(), Json::Number(*seq as f64)),
                    ("distance".to_string(), Json::Number(*dist as f64)),
                ]
                .into_iter()
                .collect(),
            )
        })
        .collect();
    (
        200,
        Json::Object(
            [
                ("code".to_string(), Json::String(code)),
                ("stops".to_string(), Json::Array(stop_json)),
            ]
            .into_iter()
            .collect(),
        ),
    )
}
