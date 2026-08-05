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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{PathSegment, TrainSegment};

    fn route(
        total_minutes: i32,
        train_xfers: usize,
        inter_xfers: usize,
        rail_distance: i32,
        inter_minutes: i32,
        depart: i32,
        arrive: i32,
    ) -> RouteResult {
        RouteResult {
            segments: vec![PathSegment::Train(TrainSegment {
                train_code: "G1".into(),
                from_station: "北京南".into(),
                to_station: "上海虹桥".into(),
                depart_minutes: depart,
                arrive_minutes: arrive,
                travel_minutes: arrive - depart,
                distance: rail_distance,
            })],
            actual_origin: "北京南".into(),
            actual_destination: "上海虹桥".into(),
            first_departure: depart,
            final_arrival: arrive,
            total_minutes,
            rail_distance,
            train_transfers: train_xfers,
            interstation_transfers: inter_xfers,
            interstation_minutes: inter_minutes,
            transfer_cities: Vec::new(),
            matched_transfer_constraint: false,
        }
    }

    #[test]
    fn empty_input() {
        assert!(score_routes(&[]).is_empty());
    }

    #[test]
    fn faster_route_scores_higher() {
        // 白天、无换乘、同距离：4h vs 8h
        let fast = route(240, 0, 0, 1200, 0, 480, 720);
        let slow = route(480, 0, 0, 1200, 0, 480, 960);
        let scored = score_routes(&[fast, slow]);
        assert!(scored[0].0 > scored[1].0);
    }

    #[test]
    fn night_travel_penalized() {
        let day = route(300, 0, 0, 1000, 0, 480, 780); // 08:00-13:00
        let night = route(300, 0, 0, 1000, 0, 1380, 240); // 23:00-04:00(+1) 全夜间
        let scored = score_routes(&[day, night]);
        assert!(scored[0].0 > scored[1].0);
    }

    #[test]
    fn fewer_transfers_scores_higher() {
        let direct = route(300, 0, 0, 1000, 0, 480, 780);
        let xfer = route(300, 1, 0, 1000, 0, 480, 780);
        let scored = score_routes(&[direct, xfer]);
        assert!(scored[0].0 > scored[1].0);
    }

    #[test]
    fn stable_order_for_ties() {
        let a = route(300, 0, 0, 1000, 0, 480, 780);
        let b = route(300, 0, 0, 1000, 0, 480, 780);
        let c = route(300, 0, 0, 1000, 0, 480, 780);
        let scored = score_routes(&[a, b, c]);
        let idxs: Vec<usize> = scored.iter().map(|(_, i)| *i).collect();
        assert_eq!(idxs, vec![0, 1, 2]); // 同分保持原序
    }

    #[test]
    fn score_in_zero_one_range() {
        let routes = vec![
            route(200, 0, 0, 800, 0, 480, 680),
            route(600, 3, 1, 2000, 120, 300, 900),
        ];
        for (s, _) in score_routes(&routes) {
            assert!((0.0..=1.0).contains(&s));
        }
    }
}
