//! 路径评分（对齐 Python src/main.py::score_routes 六维评分 + 降序稳定排序）。

use crate::models::{PathSegment, RouteResult};
use std::cmp::Ordering;

/// 对路线按综合分降序评分（分数越高越好；同分保持原序）。
/// 返回 (score, 原始索引) 列表。
pub fn score_routes(routes: &[RouteResult]) -> Vec<(f64, usize)> {
    if routes.is_empty() {
        return Vec::new();
    }
    let max_t = routes.iter().map(|r| r.total_minutes).max().unwrap().max(1) as f64;
    let max_d = routes.iter().map(|r| r.rail_distance).max().unwrap().max(1) as f64;
    let mut scored: Vec<(f64, usize)> = Vec::with_capacity(routes.len());
    for (idx, r) in routes.iter().enumerate() {
        // 凌晨时段惩罚：23:00-06:00 的乘车/换乘（对齐 Python 逐段逐时刻判断）
        let mut night_penalty = 0f64;
        for seg in &r.segments {
            match seg {
                PathSegment::Train(s) => {
                    for m in [s.depart_minutes, s.arrive_minutes] {
                        let h = m.rem_euclid(1440) / 60;
                        if h >= 23 || h < 6 {
                            night_penalty += 1.0;
                        }
                    }
                }
                PathSegment::Interstation(s) => {
                    for m in [s.start_minutes, s.end_minutes] {
                        let h = m.rem_euclid(1440) / 60;
                        if h >= 23 || h < 6 {
                            night_penalty += 1.0;
                        }
                    }
                }
            }
        }
        let inter_penalty = r.interstation_minutes as f64 / 60.0f64.max(1.0);
        let s = 0.0f64.max(1.0 - r.total_minutes as f64 / max_t) * 0.35
            + 0.0f64.max(1.0 - (r.train_transfers as f64 + r.interstation_transfers as f64 * 0.5) / 4.0) * 0.25
            + 0.0f64.max(1.0 - night_penalty * 0.1) * 0.10
            + 0.0f64.max(1.0 - r.rail_distance as f64 / max_d) * 0.15
            + 0.0f64.max(1.0 - inter_penalty / 300.0) * 0.15;
        scored.push((s, idx));
    }
    // 稳定降序（Python sort 稳定 + key=-s）
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(Ordering::Equal));
    scored
}
