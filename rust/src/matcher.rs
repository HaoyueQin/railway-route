//! 车站与城市匹配（对齐 Python src/matcher.py 规则 v3）。
//!
//! exact：解析为单个车站；fuzzy：城市扩散 / 区归市 / 县镇单站 / 密度启发。
//! 顺序敏感点：all_stations 与 city_to_stations 必须保序（Python dict 插入序），
//! 同分建议的先后取决于该顺序——Rust 侧 all_stations 取 graph.idx_to_station
//! （CSV 首次出现序），city_to_stations 取 station_name.js 顺序。

use crate::data::{parse_station_names_full, StationEntry};
use crate::graph::Graph;
use std::collections::HashMap;
use std::path::Path;

/// 站名"可用性"阈值（对齐 MIN_STATION_TRAINS_FOR_SINGLE = 25）
const MIN_STATION_TRAINS_FOR_SINGLE: usize = 25;

/// 城市行政后缀（对齐 matcher._SUFFIXES）
const SUFFIXES: [&str; 6] = ["市", "区", "县", "省", "地区", "站"];

#[derive(Debug, Clone)]
pub struct MatcherData {
    /// 全部站（CSV 首次出现序，= graph.idx_to_station）
    pub all_stations: Vec<String>,
    /// 城市 → 该市在图中存在的站（station_name.js 顺序）
    pub city_to_stations: HashMap<String, Vec<String>>,
    /// 电报码（大写）→ 站名
    pub telecode_to_name: HashMap<String, String>,
    /// 拼音（小写）→ [站名]
    pub pinyin_to_names: HashMap<String, Vec<String>>,
    /// 站名 → 城市代码
    pub station_to_city_code: HashMap<String, String>,
    /// 城市名（全后缀剥离，小写）→ 城市代码
    pub city_name_to_code: HashMap<String, String>,
    /// 城市代码 → 城市名（M4 /api/match 建议层使用）
    #[allow(dead_code)]
    pub city_code_to_name: HashMap<String, String>,
    /// 站名 → 全后缀剥离 / 单后缀剥离（预计算）
    pub station_clean: HashMap<String, String>,
    pub station_no_suffix: HashMap<String, String>,
}

/// 去除单个行政后缀（对齐 _strip_suffix）
pub fn strip_suffix(text: &str) -> String {
    for suffix in SUFFIXES {
        if text.ends_with(suffix) && text.len() > suffix.len() {
            return text[..text.len() - suffix.len()].to_string();
        }
    }
    text.to_string()
}

/// 去除全部行政后缀（对齐 _strip_all_suffixes）
pub fn strip_all_suffixes(text: &str) -> String {
    let mut result = text.to_string();
    loop {
        let mut changed = false;
        for suffix in SUFFIXES {
            if result.ends_with(suffix) && result.len() > suffix.len() {
                result = result[..result.len() - suffix.len()].to_string();
                changed = true;
                break;
            }
        }
        if !changed {
            return result;
        }
    }
}

/// 从 station_name.js 构建结构化匹配索引（对齐 build_matcher）。
pub fn build_matcher(graph: &Graph, station_js_path: &Path) -> Result<MatcherData, String> {
    let entries: Vec<StationEntry> = parse_station_names_full(station_js_path)?;
    let mut city_to_stations: HashMap<String, Vec<String>> = HashMap::new();
    let mut telecode_to_name: HashMap<String, String> = HashMap::new();
    let mut pinyin_to_names: HashMap<String, Vec<String>> = HashMap::new();
    let mut station_to_city_code: HashMap<String, String> = HashMap::new();
    let mut city_name_to_code: HashMap<String, String> = HashMap::new();
    let mut city_code_to_name: HashMap<String, String> = HashMap::new();

    for e in &entries {
        if !e.city_name.is_empty() {
            // 城市名索引不过滤"站是否在图中"（与 Python 一致）
            city_name_to_code
                .insert(strip_all_suffixes(&e.city_name.to_lowercase()), e.city_code.clone());
            city_code_to_name.insert(e.city_code.clone(), e.city_name.clone());
        }
        if !graph.station_to_idx.contains_key(&e.name) {
            continue;
        }
        city_to_stations.entry(e.city_code.clone()).or_default().push(e.name.clone());
        station_to_city_code.insert(e.name.clone(), e.city_code.clone());
        if !e.telecode.is_empty() {
            telecode_to_name.insert(e.telecode.to_uppercase(), e.name.clone());
        }
        if !e.pinyin.is_empty() {
            pinyin_to_names.entry(e.pinyin.to_lowercase()).or_default().push(e.name.clone());
        }
    }

    let mut station_clean = HashMap::with_capacity(graph.station_count());
    let mut station_no_suffix = HashMap::with_capacity(graph.station_count());
    for name in &graph.idx_to_station {
        station_clean.insert(name.clone(), strip_all_suffixes(&name.to_lowercase()));
        station_no_suffix.insert(name.clone(), strip_suffix(&name.to_lowercase()));
    }

    Ok(MatcherData {
        all_stations: graph.idx_to_station.clone(),
        city_to_stations,
        telecode_to_name,
        pinyin_to_names,
        station_to_city_code,
        city_name_to_code,
        city_code_to_name,
        station_clean,
        station_no_suffix,
    })
}

/// 按分数降序返回 (score, station_name)（同分保序 = add 顺序，对齐 Python 稳定排序）。
pub fn fuzzy_match(graph: &Graph, matcher: &MatcherData, query: &str) -> Vec<(i32, String)> {
    let raw = query.trim().to_string();
    let q_upper = raw.to_uppercase();
    let q_lower = raw.to_lowercase();
    let q_clean = strip_all_suffixes(&q_lower);
    let mut results: Vec<(i32, String)> = Vec::new();

    macro_rules! add {
        ($score:expr, $name:expr) => {{
            let name = $name;
            if graph.station_to_idx.contains_key(&name)
                && !results.iter().any(|(_, n)| *n == name)
            {
                results.push(($score, name));
            }
        }};
    }

    if graph.station_to_idx.contains_key(&raw) {
        add!(200, raw.clone());
    }
    if let Some(name) = matcher.telecode_to_name.get(&q_upper) {
        add!(190, name.clone());
    }
    if let Some(names) = matcher.pinyin_to_names.get(&q_lower) {
        for name in names {
            add!(180, name.clone());
        }
    }
    if let Some(city_code) = matcher.city_name_to_code.get(&q_clean) {
        if let Some(stations) = matcher.city_to_stations.get(city_code) {
            for name in stations {
                add!(170, name.clone());
            }
        }
    }
    for station in &matcher.all_stations {
        let station_clean = &matcher.station_clean[station];
        let station_no_suffix = &matcher.station_no_suffix[station];
        if q_clean == *station_no_suffix || q_clean == *station_clean {
            add!(160, station.clone());
        }
    }
    for station in &matcher.all_stations {
        let station_clean = &matcher.station_clean[station];
        if station_clean.len() >= 2 && q_clean.ends_with(station_clean) {
            add!(140 + station_clean.len() as i32 * 2, station.clone());
        } else if q_clean.len() >= 2 && station_clean.ends_with(&q_clean) {
            add!(135 + q_clean.len() as i32 * 2, station.clone());
        }
    }
    for station in &matcher.all_stations {
        let station_clean = &matcher.station_clean[station];
        if q_clean.len() >= 2 && station_clean.contains(&q_clean) {
            add!(120 + q_clean.len() as i32, station.clone());
        } else if station_clean.len() >= 2 && q_clean.contains(station_clean) {
            add!(110 + station_clean.len() as i32, station.clone());
        }
    }
    // sort 稳定（Rust sort_by 为稳定排序，与 Python 一致）
    results.sort_by(|a, b| b.0.cmp(&a.0));
    results
}

/// 解析为单个首选车站（对齐 resolve_single）。
pub fn resolve_single(graph: &Graph, matcher: &MatcherData, query: &str) -> Result<String, String> {
    let matches = fuzzy_match(graph, matcher, query);
    if matches.is_empty() {
        return Err(format!("未找到匹配的车站: {query}"));
    }
    let query_clean = strip_all_suffixes(&query.trim().to_lowercase());
    for (_, name) in &matches {
        if strip_all_suffixes(&name.to_lowercase()) == query_clean {
            return Ok(name.clone());
        }
    }
    Ok(matches[0].1.clone())
}

/// 将城市名或任一有效车站输入解析为城市代码（对齐 resolve_city_code）。
pub fn resolve_city_code(graph: &Graph, matcher: &MatcherData, query: &str) -> Result<String, String> {
    let query_clean = strip_all_suffixes(&query.trim().to_lowercase());
    if let Some(code) = matcher.city_name_to_code.get(&query_clean) {
        return Ok(code.clone());
    }
    let station = resolve_single(graph, matcher, query)?;
    if let Some(code) = matcher.station_to_city_code.get(&station) {
        return Ok(code.clone());
    }
    Err(format!("未找到车站所属城市: {query}"))
}

/// 按 exact/fuzzy 模式解析单站或同城全部有效站（对齐 resolve_station_set 规则 1-5）。
pub fn resolve_station_set(
    graph: &Graph,
    matcher: &MatcherData,
    query: &str,
    mode: &str,
) -> Result<Vec<String>, String> {
    if mode == "exact" {
        return Ok(vec![resolve_single(graph, matcher, query)?]);
    }
    if mode != "fuzzy" {
        return Err(format!("未知匹配模式: {mode}"));
    }
    let q = query.trim();

    // 规则 1：城市名（去行政后缀）→ 全市扩散
    let cleaned = strip_all_suffixes(&q.to_lowercase());
    if let Some(city_code) = matcher.city_name_to_code.get(&cleaned) {
        if let Some(stations) = matcher.city_to_stations.get(city_code) {
            if !stations.is_empty() {
                return Ok(stations.clone());
            }
        }
    }

    // 规则 2：带"区"后缀 → 区级地名归并所属市
    if let Some(base) = q.strip_suffix('区') {
        if let Some(idx) = graph.station_to_idx.get(base) {
            let code = graph.station_to_city_code.get(idx).cloned().unwrap_or_default();
            if let Some(stations) = matcher.city_to_stations.get(&code) {
                if !stations.is_empty() {
                    return Ok(stations.clone());
                }
            }
        }
    }

    // 规则 3：带"县/镇/乡"后缀 → 县级地名单站
    if (q.ends_with('县') || q.ends_with('镇') || q.ends_with('乡')) && graph.station_to_idx.contains_key(q) {
        return Ok(vec![q.to_string()]);
    }

    // 规则 4：无后缀站名存在
    if let Some(&idx) = graph.station_to_idx.get(q) {
        let city_code = graph.station_to_city_code.get(&idx).cloned().unwrap_or_default();
        let city_name = graph
            .city_code_to_name
            .get(&city_code)
            .cloned()
            .unwrap_or_default();
        if !city_name.is_empty() && q.starts_with(&city_name) {
            return Ok(vec![q.to_string()]);
        }
        let n_dep = graph.departures[idx].len();
        if n_dep >= MIN_STATION_TRAINS_FOR_SINGLE {
            return Ok(vec![q.to_string()]);
        }
        if let Some(stations) = matcher.city_to_stations.get(&city_code) {
            if !stations.is_empty() {
                return Ok(stations.clone());
            }
        }
        return Ok(vec![q.to_string()]);
    }

    // 规则 5：既有模糊匹配，无同名站的地名归并到所属城市
    let city_code = resolve_city_code(graph, matcher, q)?;
    match matcher.city_to_stations.get(&city_code) {
        Some(stations) if !stations.is_empty() => Ok(stations.clone()),
        _ => Err(format!("未找到城市内有效铁路站: {query}")),
    }
}
