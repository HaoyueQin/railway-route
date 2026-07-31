//! 铁路出行路径规划 — Rust 重构（master-v2）
//!
//! 里程碑 M1：数据层解析 + 与 Python 版统计对拍。
//! 运行: cargo run --release（数据文件在 ../data/output，不入库，需自行获取）

mod data;

use std::collections::HashMap;
use std::path::Path;

fn main() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap(); // rust/ 的上级 = 仓库根
    let csv = root.join("data/output/车次时刻表.csv");
    let js = root.join("data/timetable/station_name.js");

    // ── 时刻表 CSV ──
    let grouped = data::parse_timetable_csv(&csv).expect("解析时刻表 CSV 失败");
    let rows: usize = grouped.values().map(|v| v.len()).sum();
    let mut station_to_idx: HashMap<String, usize> = HashMap::new();
    for stops in grouped.values() {
        for s in stops {
            station_to_idx.entry(s.station.clone()).or_insert(0);
        }
    }
    println!("CSV: rows={rows} trains={} stations={}", grouped.len(), station_to_idx.len());

    // 对拍基准（Python graph.build 实测）：
    assert_eq!(rows, 128_141, "行数不一致");
    assert_eq!(grouped.len(), 14_173, "车次数不一致");
    assert_eq!(station_to_idx.len(), 3_305, "站数不一致");

    // ── station_name.js 城市分组 ──
    let city_groups = data::parse_station_names_js(&js).expect("解析 station_name.js 失败");
    let cities: usize = city_groups.len();
    let grouped_stations: usize = city_groups.values().map(|v| v.len()).sum();
    println!("station.js: cities={cities} grouped_stations={grouped_stations}");
    assert_eq!(cities, 428, "城市分组数不一致");
    assert_eq!(grouped_stations, 3_375, "城市分组覆盖站数不一致");

    // ── 跨夜修正链（train_stops）──
    let train_stops = data::build_train_stops(&grouped, &station_to_idx);
    assert_eq!(train_stops.len(), 14_173, "train_stops 车次数不一致");
    // 抽查：同一车次内时刻单调性（G531B 曾有时刻倒挂数据）
    let mut max_days = 0i32;
    for stops in train_stops.values() {
        for w in stops.windows(2) {
            let (_, _, a1, _, _) = w[0];
            let (_, d2, _, _, _) = w[1];
            if a1 != -1 && d2 != -1 {
                let gap = d2 - a1;
                if gap < 0 {
                    panic!("时刻倒挂: 站间 gap={gap}");
                }
                max_days = max_days.max(gap / 1440);
            }
        }
    }
    println!("train_stops: {} 车次, 最长连续运行 {} 天, 无时刻倒挂", train_stops.len(), max_days + 1);

    println!("\nM1 数据层对拍通过 ✓");
}
