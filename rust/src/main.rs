//! 铁路出行路径规划 — Rust 重构（master-v2）
//!
//! 里程碑 M2：图构建 + 与 Python 版数量/抽样对拍。
//! 里程碑 M3：匹配规则 v3 + CSA 搜索 + 210 组合结果集对拍。
//! 运行: cargo run --release（数据文件在 ../data/output，不入库，需自行获取）
//!
//! 对拍基准: rust/tools/m2_baseline.json（tools/dump_graph_stats.py）
//!           rust/tools/m3_baseline.json（tools/dump_m3.py）

mod api;
mod csa;
mod data;
mod graph;
mod http;
mod updater;
mod json;
mod matcher;
mod models;
mod score;
mod validation;

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use graph::Graph;
use json::Json;
use models::{PathSegment, RouteResult, SearchRequest};

fn main() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap(); // rust/ 的上级 = 仓库根
    let csv = root.join("data/output/车次时刻表.csv");
    let js = root.join("data/timetable/station_name.js");
    let baseline = root.join("rust/tools/m2_baseline.json");

    let args: Vec<String> = std::env::args().collect();

    // ── --app 模式：Tauri 桌面窗口（frameless + 自绘标题栏，前端零改动）──
    if args.iter().any(|a| a == "--app") {
        let (data_root, web_dir) = find_app_resources();
        run_tauri(
            &data_root.join("output/车次时刻表.csv"),
            &data_root.join("timetable/station_name.js"),
            &web_dir,
        );
        return;
    }

    // ── --serve 模式：构建图 + 启动 HTTP 服务（M5 桌面窗口的 API 底座）──
    if args.iter().any(|a| a == "--serve") {
        let port = args
            .iter()
            .position(|a| a == "--serve")
            .and_then(|i| args.get(i + 1))
            .and_then(|s| s.parse::<u16>().ok())
            .unwrap_or(8000);
        let mut g = Graph::new();
        g.build(&csv, &js).expect("构建图失败");
        let matcher = matcher::build_matcher(&g, &js).expect("构建 matcher 失败");
        let web_dir = root.join("web");
        println!("数据加载完成（{} 站 / {} 车次）", g.station_count(), g.train_stops.len());
        http::serve(&g, &matcher, &web_dir, port).expect("HTTP 服务失败");
        return;
    }

    // ── M1 数据层（保留回归）──
    m1_data_checks(&csv, &js);

    // ── M2 图构建 ──
    let mut g = Graph::new();
    g.build(&csv, &js).expect("构建图失败");
    let conns = g.sorted_connections.len();
    let conn_buckets_nonempty = g.out_conns.iter().filter(|b| !b.is_empty()).count();
    let same_city_nonempty = g.same_city_of.iter().filter(|v| !v.is_empty()).count();
    let same_city_neighbors: usize = g.same_city_of.iter().map(|v| v.len()).sum();
    let train_edges: usize = g.edge_trains.values().map(|v| v.len()).sum();
    let departures: usize = g.departures.iter().map(|v| v.len()).sum();

    println!(
        "graph: stations={} edges={} train_edges={} departures={}",
        g.station_count(),
        g.edge_count(),
        train_edges,
        departures
    );
    println!(
        "transfers: edges={} set={} nonempty_stations={} neighbors={} city_covered={}",
        g.transfer_count(),
        g.transfer_edge_set.len(),
        same_city_nonempty,
        same_city_neighbors,
        g.station_to_city_code.len()
    );
    println!(
        "conns: sorted={conns} buckets={} nonempty_buckets={conn_buckets_nonempty}",
        g.out_conns.len()
    );

    // 数量断言（Python graph.build 实测基准）
    assert_eq!(g.station_count(), 3_305, "站数不一致");
    assert_eq!(g.edge_count(), 17_792, "唯一边数不一致");
    assert_eq!(train_edges, 113_968, "TrainEdge 数不一致");
    assert_eq!(departures, 113_968, "departures 数不一致");
    assert_eq!(g.transfer_count(), 33_926, "换乘边数不一致");
    assert_eq!(g.transfer_edge_set.len(), 33_926, "换乘边集合数不一致");
    assert_eq!(same_city_nonempty, 2_332, "同城非空站数不一致");
    assert_eq!(same_city_neighbors, 33_926, "同城邻居总数不一致");
    assert_eq!(g.station_to_city_code.len(), 2_469, "同城覆盖站数不一致");
    assert_eq!(g.city_groups.len(), 428, "城市分组数不一致");
    assert_eq!(conns, 225_454, "sorted_connections 数不一致");
    assert_eq!(g.out_conns.len(), 3_305, "out_conns 桶数不一致");
    assert_eq!(conn_buckets_nonempty, 3_296, "out_conns 非空桶数不一致");

    // 结构自检：桶内按发车升序；transfer 对称且与 same_city_of 一致
    for b in &g.out_conns {
        assert!(
            b.windows(2).all(|w| w[0].depart_minutes <= w[1].depart_minutes),
            "out_conns 桶内未按发车排序"
        );
    }
    for &(a, b) in &g.transfer_edges {
        assert!(g.transfer_edge_set.contains(&(b, a)), "transfer 不对称: {a}->{b}");
        assert!(g.same_city_of[a].contains(&b), "same_city_of 与 transfer 不一致: {a}->{b}");
    }
    // 全局 sorted_connections 升序
    assert!(
        g.sorted_connections
            .windows(2)
            .all(|w| w[0].depart_minutes <= w[1].depart_minutes),
        "sorted_connections 未全局排序"
    );

    // ── 逐项对拍（站名做 key）──
    let payload = json::parse(&fs::read_to_string(&baseline).expect("读 m2_baseline.json 失败"))
        .expect("解析 m2_baseline.json 失败");
    let stats = payload.as_object().unwrap().get("stats").unwrap().as_object().unwrap();
    assert_eq!(stats.len(), 14, "stats 字段数变化，需同步 dump 脚本");
    let mut passed = 0u64;
    let mut failed = 0u64;

    macro_rules! check {
        ($cond:expr, $($msg:tt)*) => {
            if $cond {
                passed += 1;
            } else {
                failed += 1;
                println!("  ✗ {}", format!($($msg)*));
            }
        };
    }

    // stats 逐字段对拍（通用数字比较）
    for (k, v) in stats {
        let expected = v.as_f64().unwrap();
        let actual = match k.as_str() {
            "stations" => g.station_count() as f64,
            "edge_count" => g.edge_count() as f64,
            "train_edges" => train_edges as f64,
            "departures" => departures as f64,
            "transfer_edges" => g.transfer_count() as f64,
            "transfer_edge_set" => g.transfer_edge_set.len() as f64,
            "same_city_nonempty" => same_city_nonempty as f64,
            "same_city_neighbors" => same_city_neighbors as f64,
            "city_covered_stations" => g.station_to_city_code.len() as f64,
            "city_groups" => g.city_groups.len() as f64,
            "sorted_conns" => conns as f64,
            "out_conns_buckets" => g.out_conns.len() as f64,
            "out_conns_nonempty" => conn_buckets_nonempty as f64,
            "zero_dist_edges" => {
                let n = g
                    .edge_trains
                    .values()
                    .flat_map(|v| v.iter())
                    .filter(|te| te.distance <= 0)
                    .count() as f64;
                n
            }
            other => panic!("未知 stats 字段 {other}"),
        };
        check!((actual - expected).abs() < 0.5, "stats.{k}: 期望 {expected} 实际 {actual}");
    }

    let obj = payload.as_object().unwrap();

    // same_city_sample: [站A, 站B, 期望]
    for row in obj.get("same_city_sample").unwrap().as_array().unwrap() {
        let r = row.as_array().unwrap();
        let a = g.station_to_idx[r[0].as_str().unwrap()];
        let b = g.station_to_idx[r[1].as_str().unwrap()];
        let expected = r[2].as_bool().unwrap();
        let actual = g.is_same_city(a, b);
        check!(
            actual == expected,
            "is_same_city({}, {}): 期望 {expected} 实际 {actual}",
            r[0].as_str().unwrap(),
            r[1].as_str().unwrap()
        );
    }

    // transfer_time_sample: [站A, 站B, 期望分钟]
    for row in obj.get("transfer_time_sample").unwrap().as_array().unwrap() {
        let r = row.as_array().unwrap();
        let a = g.station_to_idx[r[0].as_str().unwrap()];
        let b = g.station_to_idx[r[1].as_str().unwrap()];
        let expected = r[2].as_f64().unwrap();
        let actual = g.get_interstation_transfer_time(a, b, graph::DEFAULT_INTER_TRANSFER_MINUTES) as f64;
        check!(
            actual == expected,
            "transfer_time({}, {}): 期望 {expected} 实际 {actual}",
            r[0].as_str().unwrap(),
            r[1].as_str().unwrap()
        );
    }

    // edge_sample: [from, to, min_time, distance, train_count]
    for row in obj.get("edge_sample").unwrap().as_array().unwrap() {
        let r = row.as_array().unwrap();
        let f = g.station_to_idx[r[0].as_str().unwrap()];
        let t = g.station_to_idx[r[1].as_str().unwrap()];
        let info = g.get_edge_info(f, t);
        check!(info.is_some(), "边({}, {}) 不存在", r[0].as_str().unwrap(), r[1].as_str().unwrap());
        if let Some(info) = info {
            let vals = [info.min_time as f64, info.distance as f64, info.train_count as f64];
            for (i, expected) in [2, 3, 4].iter().enumerate() {
                let exp = r[*expected].as_f64().unwrap();
                check!(
                    vals[i] == exp,
                    "边({}, {}) 字段{i}: 期望 {exp} 实际 {}",
                    r[0].as_str().unwrap(),
                    r[1].as_str().unwrap(),
                    vals[i]
                );
            }
        }
    }

    // dist_lb / time_lb: {目标站: {站: 下界}}（全量逐点对拍）
    for (key, use_time) in [("dist_lb", false), ("time_lb", true)] {
        let lb_obj = obj.get(key).unwrap().as_object().unwrap();
        for (target_name, stations) in lb_obj {
            let target = g.station_to_idx[target_name.as_str()];
            let d = if use_time {
                g.get_multi_source_times(&[target])
            } else {
                g.get_multi_source_distances(&[target])
            };
            let stations = stations.as_object().unwrap();
            if d.len() != stations.len() {
                check!(false, "{key}[{target_name}] 可达站数: 期望 {} 实际 {}", stations.len(), d.len());
            }
            for (station_name, expected) in stations {
                let sidx = g.station_to_idx[station_name.as_str()];
                match d.get(&sidx) {
                    Some(actual) => {
                        let exp = expected.as_f64().unwrap();
                        check!(
                            (*actual as f64 - exp).abs() < 0.001,
                            "{key}[{target_name}][{station_name}]: 期望 {exp} 实际 {actual}"
                        );
                    }
                    None => {
                        check!(false, "{key}[{target_name}][{station_name}]: Rust 侧不可达");
                    }
                }
            }
        }
    }

    println!("\nM2 对拍: {passed} 项通过, {failed} 项失败");
    if failed > 0 {
        panic!("M2 对拍失败");
    }
    println!("M2 图构建对拍通过 ✓");

    // ── M3 匹配 + CSA 搜索（210 组合结果集对拍）──
    let matcher = matcher::build_matcher(&g, &js).expect("构建 matcher 失败");
    let m3_baseline = root.join("rust/tools/m3_baseline.json");
    if !m3_baseline.exists() {
        println!("\n（跳过 M3 对拍：{m3_baseline:?} 不存在，先运行 python rust/tools/dump_m3.py）");
        return;
    }
    verify_m3(&g, &matcher, &m3_baseline);

    // ── M4 HTTP API（响应结构与 Python 逐字段对拍）──
    let m4_baseline = root.join("rust/tools/m4_baseline.json");
    if !m4_baseline.exists() {
        println!("\n（跳过 M4 对拍：{m4_baseline:?} 不存在，先运行 python rust/tools/dump_m4.py）");
        return;
    }
    verify_m4(&g, &matcher, &m4_baseline);
}

/// ── Tauri 桌面应用模式 ──
/// HTTP server 在后台线程提供 /api/* 与静态文件，窗口动态创建
/// （端口启动后才知道，无法写死在 tauri.conf.json 的 windows[].url），
/// frameless 无系统边框 → 前端自绘标题栏（-webkit-app-region 拖动 +
/// window.__TAURI__ 窗口控制，withGlobalTauri 全局注入）。
///
/// 资源路径探测顺序（打包后数据与前端必须随 exe 分发）：
/// 1. exe 同级 _up_/data（Tauri 2 NSIS 安装布局：resources 装在 _up_ 子目录）
/// 2. exe 同级 data/（手动拷贝/绿色分发布局）
/// 3. exe 同级 resources/data（Tauri v2 可能的子目录布局）
/// 4. 仓库根 data/（开发模式 cargo run）
fn find_app_resources() -> (std::path::PathBuf, std::path::PathBuf) {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()));
    let manifest_root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(d) = &exe_dir {
        candidates.push(d.join("_up_/data"));
        candidates.push(d.join("data"));
        candidates.push(d.join("resources/data"));
    }
    candidates.push(manifest_root.join("data"));
    for data_root in &candidates {
        let (csv, js) = (
            data_root.join("output/车次时刻表.csv"),
            data_root.join("timetable/station_name.js"),
        );
        if csv.exists() && js.exists() {
            // web 目录与 data 同根（_up_/web、exe 旁 web/ 或仓库根 web/）
            let web = data_root
                .parent()
                .map(|r| {
                    if r.join("web/index.html").exists() {
                        r.join("web")
                    } else {
                        manifest_root.join("web")
                    }
                })
                .unwrap_or_else(|| manifest_root.join("web"));
            return (data_root.clone(), web);
        }
    }
    eprintln!(
        "未找到数据目录：需存在 data/output/车次时刻表.csv 与 data/timetable/station_name.js\n\
         尝试过：{}",
        candidates
            .iter()
            .map(|p| p.display().to_string())
            .collect::<Vec<_>>()
            .join(" / ")
    );
    std::process::exit(1);
}

/// Tauri 应用共享数据（搜索/匹配直接内存调用，零 HTTP 零端口）。
pub struct AppData {
    pub graph: Graph,
    pub matcher: matcher::MatcherData,
}

/// 自研 Json → serde_json（IPC 返回给前端）。
fn json_to_serde(j: &Json) -> serde_json::Value {
    match j {
        Json::Null => serde_json::Value::Null,
        Json::Bool(b) => serde_json::Value::Bool(*b),
        Json::Number(n) => serde_json::Value::from(*n),
        Json::String(s) => serde_json::Value::String(s.clone()),
        Json::Array(a) => serde_json::Value::Array(a.iter().map(json_to_serde).collect()),
        Json::Object(o) => serde_json::Value::Object(
            o.iter().map(|(k, v)| (k.clone(), json_to_serde(v))).collect(),
        ),
    }
}

/// 包装 api.rs 的 HTTP 处理器为 Tauri command（参数表同 HTTP query，响应同契约）。
fn api_command<F>(f: F) -> serde_json::Value
where
    F: FnOnce() -> (u16, Json),
{
    let (_, body) = f();
    json_to_serde(&body)
}

#[tauri::command]
fn api_search(state: tauri::State<'_, AppData>, params: std::collections::HashMap<String, String>) -> serde_json::Value {
    api_command(|| api::api_search(&state.graph, &state.matcher, &params))
}

#[tauri::command]
fn api_match(state: tauri::State<'_, AppData>, params: std::collections::HashMap<String, String>) -> serde_json::Value {
    api_command(|| api::api_match(&state.graph, &state.matcher, &params))
}

#[tauri::command]
fn api_train(state: tauri::State<'_, AppData>, params: std::collections::HashMap<String, String>) -> serde_json::Value {
    api_command(|| api::api_train(&state.graph, &params))
}

#[tauri::command]
fn api_appinfo() -> serde_json::Value {
    serde_json::json!({ "name": "railway-route", "version": env!("CARGO_PKG_VERSION") })
}

fn run_tauri(csv: &Path, js: &Path, _web_dir: &Path) {
    let mut g = Graph::new();
    g.build(csv, js).expect("构建图失败");
    let matcher = matcher::build_matcher(&g, js).expect("构建 matcher 失败");
    let n_stations = g.station_count();
    let n_trains = g.train_stops.len();
    println!("数据加载完成（{n_stations} 站 / {n_trains} 车次），Tauri 窗口（内置资源，零端口）");

    tauri::Builder::default()
        .manage(AppData { graph: g, matcher })
        .manage(updater::UpdaterState::new())
        .invoke_handler(tauri::generate_handler![
            api_search,
            api_match,
            api_train,
            api_appinfo,
            updater::check_update,
            updater::download_update,
            updater::get_download_progress,
        ])
        .setup(move |app| {
            // 内置资源（frontendDist ../web 打包进 exe），不加载外部 URL
            let _win = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::default(),
            )
                .title("铁路出行路径规划")
                .inner_size(1280.0, 880.0)
                .min_inner_size(980.0, 640.0)
                .resizable(true)
                .decorations(false) // frameless：前端自绘标题栏
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("Tauri 运行失败");
}

/// M1 数据层回归（与 d8fa3df 相同的断言基准）。
fn m1_data_checks(csv: &Path, js: &Path) {
    let grouped = data::parse_timetable_csv(csv).expect("解析时刻表 CSV 失败");
    let rows: usize = grouped.values().map(|v| v.len()).sum();
    let mut station_to_idx: HashMap<String, usize> = HashMap::new();
    for stops in grouped.values() {
        for s in stops {
            station_to_idx.entry(s.station.clone()).or_insert(0);
        }
    }
    assert_eq!(rows, 128_141, "行数不一致");
    assert_eq!(grouped.len(), 14_173, "车次数不一致");
    assert_eq!(station_to_idx.len(), 3_305, "站数不一致");

    let city_groups = data::parse_station_names_js(js).expect("解析 station_name.js 失败");
    assert_eq!(city_groups.len(), 428, "城市分组数不一致");
    let grouped_stations: usize = city_groups.values().map(|v| v.len()).sum();
    assert_eq!(grouped_stations, 3_375, "城市分组覆盖站数不一致");

    // 跨夜修正链（train_stops）：时刻单调，无倒挂
    let train_stops = data::build_train_stops(&grouped, &station_to_idx);
    assert_eq!(train_stops.len(), 14_173, "train_stops 车次数不一致");
    let mut max_days = 0i32;
    for stops in train_stops.values() {
        for w in stops.windows(2) {
            let (_, _, a1, _, _) = w[0];
            let (_, d2, _, _, _) = w[1];
            if a1 != -1 && d2 != -1 {
                let gap = d2 - a1;
                assert!(gap >= 0, "时刻倒挂: 站间 gap={gap}");
                max_days = max_days.max(gap / 1440);
            }
        }
    }
    println!("M1 数据层回归 ✓ (rows={rows} trains={} stations={} cities={} grouped_stations={grouped_stations} 最长连续 {max_days} 天)",
        grouped.len(), station_to_idx.len(), city_groups.len());
}

// ── M3 对拍 ─────────────────────────────────────────────

/// 从 baseline JSON 的 kw 构造 SearchRequest（对齐 Python SearchRequest **kw）。
fn request_from_json(from: &str, to: &str, profile: &str, kw: Option<&HashMap<String, Json>>) -> SearchRequest {
    let mut req = SearchRequest::new(from, to, profile);
    if let Some(kw) = kw {
        for (k, v) in kw {
            match k.as_str() {
                "max_transfers" => req.max_transfers = v.as_f64().unwrap() as usize,
                "transfer_city_code" => {
                    req.transfer_city_code = Some(v.as_str().unwrap().to_string());
                }
                "earliest_depart" => req.earliest_depart = v.as_f64().unwrap() as i32,
                "latest_depart" => req.latest_depart = v.as_f64().unwrap() as i32,
                "latest_arrive" => req.latest_arrive = v.as_f64().unwrap() as i32,
                "from_mode" => req.from_mode = Some(v.as_str().unwrap().to_string()),
                "to_mode" => req.to_mode = Some(v.as_str().unwrap().to_string()),
                other => panic!("未知 kw 字段: {other}"),
            }
        }
    }
    req
}

/// Python route JSON 的规范化 key（与 Rust route_key_str 相同格式）。
fn py_route_key(route: &Json) -> String {
    let o = route.as_object().unwrap();
    let segs = o.get("segs").unwrap().as_array().unwrap();
    segs.iter()
        .map(|s| {
            let so = s.as_object().unwrap();
            match so.get("k").unwrap().as_str().unwrap() {
                "train" => format!(
                    "train|{}|{}|{}|{}|{}",
                    so.get("c").unwrap().as_str().unwrap(),
                    so.get("f").unwrap().as_str().unwrap(),
                    so.get("t").unwrap().as_str().unwrap(),
                    so.get("d").unwrap().as_f64().unwrap() as i32,
                    so.get("a").unwrap().as_f64().unwrap() as i32,
                ),
                "inter" => format!(
                    "inter|{}|{}|{}|{}",
                    so.get("f").unwrap().as_str().unwrap(),
                    so.get("t").unwrap().as_str().unwrap(),
                    so.get("s").unwrap().as_f64().unwrap() as i32,
                    so.get("e").unwrap().as_f64().unwrap() as i32,
                ),
                other => panic!("未知段类型 {other}"),
            }
        })
        .collect::<Vec<_>>()
        .join("::")
}

fn rust_route_key(route: &RouteResult) -> String {
    route
        .segments
        .iter()
        .map(|s| s.seg_key())
        .collect::<Vec<_>>()
        .join("::")
}

/// M4 的 route JSON（typed_route_to_dict 结构）规范化 key。
fn m4_route_key(route: &Json) -> String {
    let o = route.as_object().unwrap();
    let segs = o.get("segments").unwrap().as_array().unwrap();
    segs.iter()
        .map(|s| {
            let so = s.as_object().unwrap();
            match so.get("type").unwrap().as_str().unwrap() {
                "train" => format!(
                    "train|{}|{}|{}|{}|{}",
                    so.get("train_code").unwrap().as_str().unwrap(),
                    so.get("from_station").unwrap().as_str().unwrap(),
                    so.get("to_station").unwrap().as_str().unwrap(),
                    so.get("depart").unwrap().as_object().unwrap().get("minutes").unwrap().as_f64().unwrap() as i32,
                    so.get("arrive").unwrap().as_object().unwrap().get("minutes").unwrap().as_f64().unwrap() as i32,
                ),
                "interstation" => format!(
                    "inter|{}|{}|{}|{}",
                    so.get("from_station").unwrap().as_str().unwrap(),
                    so.get("to_station").unwrap().as_str().unwrap(),
                    so.get("start").unwrap().as_object().unwrap().get("minutes").unwrap().as_f64().unwrap() as i32,
                    so.get("end").unwrap().as_object().unwrap().get("minutes").unwrap().as_f64().unwrap() as i32,
                ),
                other => panic!("未知段类型 {other}"),
            }
        })
        .collect::<Vec<_>>()
        .join("::")
}

fn num(o: &HashMap<String, Json>, key: &str) -> i32 {
    o.get(key).unwrap().as_f64().unwrap() as i32
}

/// 逐字段对比 Python route JSON 与 Rust RouteResult，返回差异列表。
fn compare_route(py: &Json, rust: &RouteResult) -> Vec<String> {
    let mut diffs = Vec::new();
    let o = py.as_object().unwrap();

    let py_segs = o.get("segs").unwrap().as_array().unwrap();
    if py_segs.len() != rust.segments.len() {
        diffs.push(format!("段数 {} != {}", py_segs.len(), rust.segments.len()));
    }
    for (i, (ps, rs)) in py_segs.iter().zip(rust.segments.iter()).enumerate() {
        let po = ps.as_object().unwrap();
        match po.get("k").unwrap().as_str().unwrap() {
            "train" => {
                let rs = match rs {
                    PathSegment::Train(t) => t,
                    _ => {
                        diffs.push(format!("seg{i} 类型不同: py=train rust=inter"));
                        continue;
                    }
                };
                for (pk, rv) in [
                    ("c", rs.train_code.as_str()),
                    ("f", rs.from_station.as_str()),
                    ("t", rs.to_station.as_str()),
                ] {
                    let pv = po.get(pk).unwrap().as_str().unwrap();
                    if pv != rv {
                        diffs.push(format!("seg{i}.{pk}: py={pv} rust={rv}"));
                    }
                }
                for (pk, rv) in [
                    ("d", rs.depart_minutes),
                    ("a", rs.arrive_minutes),
                    ("tr", rs.travel_minutes),
                    ("di", rs.distance),
                ] {
                    let pv = num(po, pk);
                    if pv != rv {
                        diffs.push(format!("seg{i}.{pk}: py={pv} rust={rv}"));
                    }
                }
            }
            "inter" => {
                let rs = match rs {
                    PathSegment::Interstation(s) => s,
                    _ => {
                        diffs.push(format!("seg{i} 类型不同: py=inter rust=train"));
                        continue;
                    }
                };
                for (pk, rv) in [
                    ("f", rs.from_station.as_str()),
                    ("t", rs.to_station.as_str()),
                    ("cc", rs.city_code.as_str()),
                    ("cn", rs.city_name.as_str()),
                ] {
                    let pv = po.get(pk).unwrap().as_str().unwrap();
                    if pv != rv {
                        diffs.push(format!("seg{i}.{pk}: py={pv} rust={rv}"));
                    }
                }
                for (pk, rv) in [
                    ("s", rs.start_minutes),
                    ("e", rs.end_minutes),
                    ("m", rs.transfer_minutes),
                ] {
                    let pv = num(po, pk);
                    if pv != rv {
                        diffs.push(format!("seg{i}.{pk}: py={pv} rust={rv}"));
                    }
                }
            }
            other => diffs.push(format!("seg{i} 未知类型 {other}")),
        }
    }

    for (pk, rv) in [
        ("ao", rust.actual_origin.as_str()),
        ("ad", rust.actual_destination.as_str()),
    ] {
        let pv = o.get(pk).unwrap().as_str().unwrap();
        if pv != rv {
            diffs.push(format!("{pk}: py={pv} rust={rv}"));
        }
    }
    for (pk, rv) in [
        ("fd", rust.first_departure),
        ("fa", rust.final_arrival),
        ("tm", rust.total_minutes),
        ("rd", rust.rail_distance),
        ("im", rust.interstation_minutes),
    ] {
        let pv = num(o, pk);
        if pv != rv {
            diffs.push(format!("{pk}: py={pv} rust={rv}"));
        }
    }
    for (pk, rv) in [
        ("tt", rust.train_transfers as i32),
        ("it", rust.interstation_transfers as i32),
    ] {
        let pv = num(o, pk);
        if pv != rv {
            diffs.push(format!("{pk}: py={pv} rust={rv}"));
        }
    }
    // transfer_cities（保序数组）
    let py_tc = o.get("tc").unwrap().as_array().unwrap();
    let py_tc: Vec<&str> = py_tc.iter().map(|v| v.as_str().unwrap()).collect();
    let rust_tc: Vec<&str> = rust.transfer_cities.iter().map(|s| s.as_str()).collect();
    if py_tc != rust_tc {
        diffs.push(format!("tc: py={py_tc:?} rust={rust_tc:?}"));
    }
    let py_mc = o.get("mc").unwrap().as_bool().unwrap();
    if py_mc != rust.matched_transfer_constraint {
        diffs.push(format!("mc: py={py_mc} rust={}", rust.matched_transfer_constraint));
    }
    diffs
}

/// M3 对拍：读 m3_baseline.json，逐组合跑搜索并全字段对比。
fn verify_m3(g: &Graph, matcher: &matcher::MatcherData, baseline_path: &Path) {
    let payload = json::parse(&fs::read_to_string(baseline_path).expect("读 m3_baseline.json 失败"))
        .expect("解析 m3_baseline.json 失败");
    let cases = payload
        .as_object()
        .unwrap()
        .get("cases")
        .unwrap()
        .as_array()
        .unwrap();
    println!("\nM3 对拍: {} 个组合", cases.len());

    let mut passed = 0u64;
    let mut failed = 0u64;
    let mut diff_lines: Vec<String> = Vec::new();
    let mut total_scanned: u64 = 0;
    let mut total_generated: u64 = 0;
    let mut total_elapsed: u64 = 0;
    let mut total_returned: usize = 0;

    for case in cases.iter() {
        let co = case.as_object().unwrap();
        let frm = co.get("from").unwrap().as_str().unwrap();
        let to = co.get("to").unwrap().as_str().unwrap();
        let profile = co.get("profile").unwrap().as_str().unwrap();
        let kw = co.get("kw").unwrap().as_object();
        let ctx = format!("{frm}→{to}[{profile}]");
        let req = request_from_json(frm, to, profile, kw);
        let resp = csa::search(g, matcher, &req).expect("search 失败");

        let mut case_diffs: Vec<String> = Vec::new();

        // source/target 站列表（保序）
        let py_src = co.get("src").unwrap().as_array().unwrap();
        let rust_src: Vec<&str> = resp.source_stations.iter().map(|s| s.as_str()).collect();
        if py_src.iter().map(|v| v.as_str().unwrap()).collect::<Vec<_>>() != rust_src {
            case_diffs.push(format!(
                "src 站列表: py={} rust={}",
                py_src.iter().map(|v| v.as_str().unwrap()).collect::<Vec<_>>().join(","),
                rust_src.join(",")
            ));
        }
        let py_tgt = co.get("tgt").unwrap().as_array().unwrap();
        let rust_tgt: Vec<&str> = resp.target_stations.iter().map(|s| s.as_str()).collect();
        if py_tgt.iter().map(|v| v.as_str().unwrap()).collect::<Vec<_>>() != rust_tgt {
            case_diffs.push(format!(
                "tgt 站列表: py={} rust={}",
                py_tgt.iter().map(|v| v.as_str().unwrap()).collect::<Vec<_>>().join(","),
                rust_tgt.join(",")
            ));
        }

        // routes：两侧按 route key 排序后逐项对比
        let py_routes = co.get("routes").unwrap().as_array().unwrap();
        let mut py_pairs: Vec<(String, &Json)> =
            py_routes.iter().map(|r| (py_route_key(r), r)).collect();
        let mut rust_pairs: Vec<(String, &RouteResult)> = resp
            .routes
            .iter()
            .map(|r| (rust_route_key(r), r))
            .collect();
        py_pairs.sort_by(|a, b| a.0.cmp(&b.0));
        rust_pairs.sort_by(|a, b| a.0.cmp(&b.0));

        if py_pairs.len() != rust_pairs.len() {
            case_diffs.push(format!(
                "route 数: py={} rust={}",
                py_pairs.len(),
                rust_pairs.len()
            ));
            // 集合差诊断：py 有 rust 无（前 5 条）
            let rust_keys: HashSet<&str> = rust_pairs.iter().map(|(k, _)| k.as_str()).collect();
            let missing: Vec<&str> = py_pairs
                .iter()
                .filter(|(k, _)| !rust_keys.contains(k.as_str()))
                .map(|(k, _)| k.as_str())
                .take(5)
                .collect();
            for k in missing {
                case_diffs.push(format!("    py 独有: {k}"));
            }
            let py_keys: HashSet<&str> = py_pairs.iter().map(|(k, _)| k.as_str()).collect();
            let extra: Vec<&str> = rust_pairs
                .iter()
                .filter(|(k, _)| !py_keys.contains(k.as_str()))
                .map(|(k, _)| k.as_str())
                .take(5)
                .collect();
            for k in extra {
                case_diffs.push(format!("    rust 独有: {k}"));
            }
        }
        for (i, (py, rust)) in py_pairs.iter().zip(rust_pairs.iter()).enumerate() {
            if py.0 != rust.0 {
                case_diffs.push(format!(
                    "route[{i}] key 不同:\n    py  = {}\n    rust= {}",
                    py.0, rust.0
                ));
                continue;
            }
            for d in compare_route(py.1, rust.1) {
                case_diffs.push(format!("route[{i}] {d}"));
            }
        }

        // metadata
        let py_complete = co.get("complete").unwrap().as_bool().unwrap();
        if py_complete != resp.complete {
            case_diffs.push(format!("complete: py={py_complete} rust={}", resp.complete));
        }
        let py_stopped = co.get("stopped").unwrap().as_str().map(|s| s.to_string());
        if py_stopped != resp.stopped_reason {
            case_diffs.push(format!(
                "stopped_reason: py={py_stopped:?} rust={:?}",
                resp.stopped_reason
            ));
        }

        if case_diffs.is_empty() {
            passed += 1;
        } else {
            failed += 1;
            diff_lines.push(format!("✗ [{ctx}]"));
            for d in &case_diffs {
                diff_lines.push(format!("    {d}"));
            }
            diff_lines.push(format!(
                "    rust metadata: scanned={} generated={} elapsed={}ms complete={} stopped={:?}",
                resp.scanned_connections, resp.generated_states, resp.elapsed_ms, resp.complete, resp.stopped_reason
            ));
        }
        total_scanned += resp.scanned_connections;
        total_generated += resp.generated_states;
        total_elapsed += resp.elapsed_ms;
        total_returned += resp.returned_routes;
    }

    println!(
        "M3 对拍: {passed} 组合通过, {failed} 组合失败（累计扫描 {total_scanned} 连接 / 生成 {total_generated} 标签 / {total_elapsed}ms / {total_returned} 条路线）"
    );
    if failed > 0 {
        for line in diff_lines.iter().take(60) {
            println!("{line}");
        }
        panic!("M3 对拍失败");
    }
    println!("M3 匹配 + CSA 搜索对拍通过 ✓");
}

// ── M4 对拍 ─────────────────────────────────────────────

/// 递归对比两个 Json（数字容差 1e-6；score 为 round(3) 结果放宽到 0.0015
/// 覆盖 round 半偶 vs half-away 的 0.001 边界差；返回差异描述列表）。
fn json_diff(a: &Json, b: &Json, path: &str) -> Vec<String> {
    let mut diffs = Vec::new();
    match (a, b) {
        (Json::Number(x), Json::Number(y)) => {
            let tol = if path.contains("score") { 0.0015 } else { 1e-6 };
            if (x - y).abs() > tol {
                diffs.push(format!("{path}: py={x} rust={y}"));
            }
        }
        (Json::String(x), Json::String(y)) => {
            if x != y {
                diffs.push(format!("{path}: py={x:?} rust={y:?}"));
            }
        }
        (Json::Bool(x), Json::Bool(y)) => {
            if x != y {
                diffs.push(format!("{path}: py={x} rust={y}"));
            }
        }
        (Json::Null, Json::Null) => {}
        (Json::Array(x), Json::Array(y)) => {
            if x.len() != y.len() {
                diffs.push(format!("{path}: 长度 py={} rust={}", x.len(), y.len()));
            }
            for (i, (xv, yv)) in x.iter().zip(y.iter()).enumerate() {
                diffs.extend(json_diff(xv, yv, &format!("{path}[{i}]")));
            }
        }
        (Json::Object(x), Json::Object(y)) => {
            for (k, xv) in x {
                match y.get(k) {
                    Some(yv) => diffs.extend(json_diff(xv, yv, &format!("{path}.{k}"))),
                    None => diffs.push(format!("{path}.{k}: py 有 rust 无")),
                }
            }
            for k in y.keys() {
                if !x.contains_key(k) {
                    diffs.push(format!("{path}.{k}: rust 有 py 无"));
                }
            }
        }
        _ => diffs.push(format!("{path}: 类型不同 py={a:?} rust={b:?}")),
    }
    diffs
}

/// M4 对拍：读 m4_baseline.json，逐 case 复现 API 流程并全字段对比。
fn verify_m4(g: &Graph, matcher: &matcher::MatcherData, baseline_path: &Path) {
    let payload = json::parse(&fs::read_to_string(baseline_path).expect("读 m4_baseline.json 失败"))
        .expect("解析 m4_baseline.json 失败");
    let obj = payload.as_object().unwrap();
    let search_cases = obj.get("search").unwrap().as_array().unwrap();
    let match_cases = obj.get("match").unwrap().as_array().unwrap();
    let train_cases = obj.get("train").unwrap().as_array().unwrap();
    println!(
        "\nM4 对拍: search {} + match {} + train {}",
        search_cases.len(),
        match_cases.len(),
        train_cases.len()
    );

    let mut passed = 0u64;
    let mut failed = 0u64;
    let mut diff_lines: Vec<String> = Vec::new();

    // ── search cases ──
    for (i, case) in search_cases.iter().enumerate() {
        let co = case.as_object().unwrap();
        let params_obj = co.get("params").unwrap().as_object().unwrap();
        // 还原 query 参数表（dump 的 params 键为 from_/dep_after 等，转回 API 键）
        let mut query: HashMap<String, String> = HashMap::new();
        for (k, v) in params_obj {
            let key = if k == "from_" { "from".to_string() } else { k.replace('_', "") };
            query.insert(key, v.as_str().unwrap().to_string());
        }
        let (status, rust_json) = api::api_search(g, matcher, &query);
        let mut case_diffs: Vec<String> = Vec::new();
        let py_status = co.get("status").unwrap().as_f64().unwrap() as u16;
        if status != py_status {
            case_diffs.push(format!("status: py={py_status} rust={status}"));
        }
        if let Some(py_payload) = co.get("payload") {
            // 跳过 time 字段（墙钟不可比）；score 字段用 0.0011 容差
            if let (Json::Object(pyo), Json::Object(rusto)) = (py_payload, &rust_json) {
                for (k, v) in rusto {
                    if k == "time" {
                        continue;
                    }
                    match pyo.get(k) {
                        Some(pv) => {
                            if k == "routes" {
                                // 同分路线顺序依赖 dict/set 迭代序（Py 自身运行间不稳定），
                                // 契约是"score 降序 + 同分等价"：按 (score, key) 排序后逐字段对比
                                let mut ra: Vec<(f64, String, &Json)> = v
                                    .as_array()
                                    .unwrap()
                                    .iter()
                                    .map(|r| {
                                        let o = r.as_object().unwrap();
                                        let s = o.get("score").unwrap().as_f64().unwrap();
                                        (s, m4_route_key(r), r)
                                    })
                                    .collect();
                                let mut pa: Vec<(f64, String, &Json)> = pv
                                    .as_array()
                                    .unwrap()
                                    .iter()
                                    .map(|r| {
                                        let o = r.as_object().unwrap();
                                        let s = o.get("score").unwrap().as_f64().unwrap();
                                        (s, m4_route_key(r), r)
                                    })
                                    .collect();
                                ra.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap().then(a.1.cmp(&b.1)));
                                pa.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap().then(a.1.cmp(&b.1)));
                                if ra.len() != pa.len() {
                                    case_diffs.push(format!(
                                        "routes 长度: py={} rust={}",
                                        pa.len(),
                                        ra.len()
                                    ));
                                }
                                for (ri, ((rs, rk, rv), (ps, pk, pvv))) in ra.iter().zip(pa.iter()).enumerate() {
                                    if (rs - ps).abs() > 0.002 {
                                        case_diffs.push(format!(
                                            "routes[{ri}].score: py={ps} rust={rs} (key={rk})"
                                        ));
                                    }
                                    if rk != pk {
                                        case_diffs.push(format!(
                                            "routes[{ri}] key 不同: py={pk} rust={rk}"
                                        ));
                                        continue;
                                    }
                                    let d = json_diff(rv, pvv, &format!("routes[{ri}]"));
                                    case_diffs.extend(d);
                                }
                            } else {
                                case_diffs.extend(json_diff(v, pv, k));
                            }
                        }
                        None => case_diffs.push(format!("{k}: py 无此字段")),
                    }
                }
            }
        } else if let Some(py_err) = co.get("error") {
            // py dump 存内层 {code, message}；rust_json 是 {"error": {...}} 包装
            let rust_err = rust_json.as_object().unwrap().get("error").unwrap();
            case_diffs.extend(json_diff(rust_err, py_err, "error"));
        }
        if case_diffs.is_empty() {
            passed += 1;
        } else {
            failed += 1;
            diff_lines.push(format!(
                "✗ [search#{i} {}→{}]",
                query.get("from").cloned().unwrap_or_default(),
                query.get("to").cloned().unwrap_or_default()
            ));
            for d in case_diffs.iter().take(10) {
                diff_lines.push(format!("    {d}"));
            }
        }
    }

    // ── match cases ──
    for case in match_cases {
        let co = case.as_object().unwrap();
        let q = co.get("q").unwrap().as_str().unwrap();
        let mut query = HashMap::new();
        query.insert("q".to_string(), q.to_string());
        let (status, rust_json) = api::api_match(g, matcher, &query);
        let py_payload = co.get("payload").unwrap();
        let mut case_diffs = json_diff(&rust_json, py_payload, "payload");
        if status != 200 {
            case_diffs.push(format!("status: py=200 rust={status}"));
        }
        if case_diffs.is_empty() {
            passed += 1;
        } else {
            failed += 1;
            diff_lines.push(format!("✗ [match {q}]"));
            diff_lines.extend(case_diffs.iter().take(6).map(|d| format!("    {d}")));
        }
    }

    // ── train cases ──
    for case in train_cases {
        let co = case.as_object().unwrap();
        let code = co.get("code").unwrap().as_str().unwrap();
        let mut query = HashMap::new();
        query.insert("code".to_string(), code.to_string());
        let (status, rust_json) = api::api_train(g, &query);
        let py_status = co.get("status").unwrap().as_f64().unwrap() as u16;
        let mut case_diffs = Vec::new();
        if status != py_status {
            case_diffs.push(format!("status: py={py_status} rust={status}"));
        }
        if let Some(py_payload) = co.get("payload") {
            case_diffs.extend(json_diff(&rust_json, py_payload, "payload"));
        } else if let Some(py_err) = co.get("error") {
            // py dump 存内层 {code, message}；rust_json 是 {"error": {...}} 包装
            let rust_err = rust_json.as_object().unwrap().get("error").unwrap();
            case_diffs.extend(json_diff(rust_err, py_err, "error"));
        }
        if case_diffs.is_empty() {
            passed += 1;
        } else {
            failed += 1;
            diff_lines.push(format!("✗ [train {code}]"));
            diff_lines.extend(case_diffs.iter().take(6).map(|d| format!("    {d}")));
        }
    }

    println!("M4 对拍: {passed} 项通过, {failed} 项失败");
    if failed > 0 {
        for line in diff_lines.iter().take(60) {
            println!("{line}");
        }
        panic!("M4 对拍失败");
    }
    println!("M4 HTTP API 对拍通过 ✓");
}
