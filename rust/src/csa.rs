//! 轮次化多标签 CSA（对齐 Python src/csa.py 全语义）。
//!
//! 架构（RAPTOR 风格）：第 r 轮只处理 r 次列车换乘；换乘仅在轮间，
//! 同轮内只做同车次续乘传播；每轮每站按到达排序保留 ≤ 上限标签；
//! 直达由独立枚举提供（永远完整，不受剪枝/截断影响）。
//!
//! 对拍基准：rust/tools/m3_baseline.json（Python 210 组合结果集）。
//! 实现差异（不影响结果）：内部容器为 HashMap（Python dict 迭代序不同），
//! 对拍时两侧按规范化 route key 排序后逐项对比。

use crate::graph::Graph;
use crate::matcher::{resolve_city_code, resolve_single, resolve_station_set, MatcherData};

/// 多站精确解析：每站 resolve_single（精确单站），保序去重取并集。
fn resolve_multi(
    graph: &Graph,
    matcher: &MatcherData,
    stations: &[String],
) -> Result<Vec<String>, String> {
    let mut names: Vec<String> = Vec::new();
    for s in stations {
        let name = resolve_single(graph, matcher, s)?;
        if !names.contains(&name) {
            names.push(name);
        }
    }
    Ok(names)
}
use crate::models::{
    InterstationTransferSegment, PathSegment, RouteResult, SearchRequest, SearchResponse,
    TrainSegment,
};
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::rc::Rc;

// ── 过滤常量 ────────────────────────────────────────────

const MAX_DETOUR_RATIO: f64 = 3.0;
const MIN_SPEED_KPH: f64 = 10.0;
// transfer_at 约束时放宽：指定换乘城市必然引入绕路
const MAX_DETOUR_RATIO_CONSTRAINED: f64 = 5.0;
const CONSTRAINED_SLACK_PENALTY: i32 = 300;

/// 轮次标签（RAPTOR 风格多标签 CSA）
struct Label {
    station: usize,
    arrive: i32,
    train_code: Rc<str>, // footpath 标签为空串
    first_dep: i32,
    rail_distance: i32,
    train_xfers: usize,
    inter_xfers: usize,
    inter_minutes: i32,
    prev: Option<Rc<Label>>,
    conn: Option<ConnRef>, // 本跳连接（footpath 为 None）
    seg_kind: u8,          // 0=train 1=interstation
    matched_constraint: bool,
}

/// 连接引用：graph.out_conns[f][pos]（桶静态，搜索期间只读）
#[derive(Debug, Clone, Copy)]
struct ConnRef {
    f: usize,
    pos: usize,
}

fn conn_of<'a>(graph: &'a Graph, r: ConnRef) -> &'a crate::graph::Connection {
    &graph.out_conns[r.f][r.pos]
}

// ── 环形路线检测 ────────────────────────────────────────

fn has_repeated_station(route: &RouteResult) -> bool {
    // Python 语义：先记录段起点，再检查终点是否已访问。
    // 起点重复是正常换乘衔接（如 南京南 是前段终点 + 后段起点），不算重复；
    // 终点重复才是环形/折返路线（到达一个曾经到过的站）。
    let mut visited: HashSet<&str> = HashSet::new();
    for seg in &route.segments {
        let (from, to) = match seg {
            PathSegment::Train(s) => (s.from_station.as_str(), s.to_station.as_str()),
            PathSegment::Interstation(s) => (s.from_station.as_str(), s.to_station.as_str()),
        };
        visited.insert(from);
        if visited.contains(to) {
            return true;
        }
        visited.insert(to);
    }
    false
}

// ── 轮内标签插入 ────────────────────────────────────────

/// 轮内标签插入（主循环与 footpath 共用）。
///
/// 关键设计：轮内不做跨车次支配（不同车次到达时间不同但后续可达性不同）；
/// 同车次（同一天窗口内）只保留最早到达（code_arr O(1) 索引）；
/// 跨车次按到达时间排序共存，截断时优先保留不同车次；
/// 无换乘城市约束时跳过 matched 扫描（has_constraint=false 短路）。
fn insert_round_label(
    cur: &mut HashMap<usize, Vec<Rc<Label>>>,
    code_arr: &mut HashMap<usize, HashMap<Rc<str>, i32>>,
    station: usize,
    cand: Rc<Label>,
    state_limits: Option<usize>,
    has_constraint: bool,
) -> bool {
    match cur.get_mut(&station) {
        None => {
            let key = cand.train_code.clone();
            let arr = cand.arrive;
            cur.insert(station, vec![cand]);
            match code_arr.get_mut(&station) {
                None => {
                    let mut m = HashMap::new();
                    m.insert(key, arr);
                    code_arr.insert(station, m);
                }
                Some(m) => {
                    m.insert(key, arr);
                }
            }
            return true;
        }
        Some(lst) => {
            let prev_arr = code_arr.get(&station).and_then(|m| m.get(cand.train_code.as_ref())).copied();
            if let Some(prev) = prev_arr {
                if (prev - cand.arrive).abs() < 1440 {
                    if prev <= cand.arrive {
                        return false; // 同班次重复且不更早
                    }
                    // 旧标签被更新：删除旧标签（按 arrive 二分定位同 arrive 段再找同 code）
                    let mut lo = 0usize;
                    let mut hi = lst.len();
                    while lo < hi {
                        let mid = (lo + hi) / 2;
                        if lst[mid].arrive < prev {
                            lo = mid + 1;
                        } else {
                            hi = mid;
                        }
                    }
                    let mut i = lo;
                    while i < lst.len() && lst[i].arrive == prev {
                        if lst[i].train_code == cand.train_code {
                            lst.remove(i);
                            break;
                        }
                        i += 1;
                    }
                    if let Some(m) = code_arr.get_mut(&station) {
                        m.insert(cand.train_code.clone(), cand.arrive);
                    }
                }
            } else if let Some(m) = code_arr.get_mut(&station) {
                m.insert(cand.train_code.clone(), cand.arrive);
            }

            // 二分插入：arrive 升序；同 arrive 按车次号稳定排序
            let mut lo = 0usize;
            let mut hi = lst.len();
            while lo < hi {
                let mid = (lo + hi) / 2;
                if lst[mid].arrive < cand.arrive
                    || (lst[mid].arrive == cand.arrive && lst[mid].train_code < cand.train_code)
                {
                    lo = mid + 1;
                } else {
                    hi = mid;
                }
            }
            lst.insert(lo, cand);

            if let Some(limit) = state_limits {
                if lst.len() > limit {
                    // 车次去重截断：同车次保留最早到达，再按到达时间截断
                    let mut dedup: Vec<Rc<Label>> = Vec::new();
                    let mut prev_code: Option<&str> = None;
                    for lb in lst.iter() {
                        if prev_code.is_none() || lb.train_code.as_ref() != prev_code.unwrap() {
                            dedup.push(lb.clone());
                            prev_code = Some(lb.train_code.as_ref());
                        }
                    }
                    if dedup.len() > limit {
                        if !has_constraint || !dedup.iter().any(|lb| lb.matched_constraint) {
                            dedup.truncate(limit);
                        } else {
                            let matched: Vec<Rc<Label>> =
                                dedup.iter().filter(|lb| lb.matched_constraint).cloned().collect();
                            if matched.len() >= limit {
                                dedup = matched[..limit].to_vec();
                            } else {
                                let mut rest: Vec<Rc<Label>> = dedup
                                    .iter()
                                    .filter(|lb| !lb.matched_constraint)
                                    .cloned()
                                    .collect();
                                rest.truncate(limit - matched.len());
                                dedup = matched;
                                dedup.extend(rest);
                            }
                        }
                    }
                    *lst = dedup;
                    // 重建 code_arr 索引保持同步
                    let m: HashMap<Rc<str>, i32> = lst
                        .iter()
                        .map(|lb| (lb.train_code.clone(), lb.arrive))
                        .collect();
                    code_arr.insert(station, m);
                }
            }
            true
        }
    }
}

// ── 回溯与段构建 ────────────────────────────────────────

/// 合并同车次连续连接为单个 TrainSegment（对齐 _collapse_train_segments）。
///
/// 段距离 = 逐段累加非负区间里程（Connection.distance 构建时已负值取 0）。
/// 不能用"末段累计 - 首段累计"——里程数据可能倒挂（中间站累计非单调），
/// 差分会把倒挂段算成负里程（Python 逐段累加不受影响）。
fn collapse_train_segments(graph: &Graph, conns: &[ConnRef]) -> Vec<TrainSegment> {
    let mut result: Vec<TrainSegment> = Vec::new();
    if conns.is_empty() {
        return result;
    }
    let mut start = 0usize;
    let mut i = 1usize;
    while i <= conns.len() {
        let head = conn_of(graph, conns[start]);
        if i < conns.len() && conn_of(graph, conns[i]).train_code == head.train_code {
            i += 1; // 同车次续乘：继续合并
            continue;
        }
        let tail = conn_of(graph, conns[i - 1]);
        let mut dist = 0i32;
        for k in start..i {
            dist += conn_of(graph, conns[k]).distance as i32;
        }
        result.push(TrainSegment {
            train_code: head.train_code.to_string(),
            from_station: graph.idx_to_station[head.from_idx].clone(),
            to_station: graph.idx_to_station[tail.to_idx].clone(),
            depart_minutes: head.depart_minutes,
            arrive_minutes: tail.arrive_minutes,
            travel_minutes: tail.arrive_minutes - head.depart_minutes,
            distance: dist,
        });
        start = i;
        i += 1;
    }
    result
}

/// 从 Label 链回溯为类型化 RouteResult（对齐 _reconstruct_from_label）。
fn reconstruct_from_label(graph: &Graph, label: &Rc<Label>) -> Option<RouteResult> {
    enum RawSeg {
        Train(ConnRef),
        Inter((usize, usize, i32, i32, i32, String, String)),
    }
    let mut segs_raw: Vec<RawSeg> = Vec::new();
    let mut cur: Option<&Rc<Label>> = Some(label);
    while let Some(lb) = cur {
        if lb.seg_kind == 0 {
            if let Some(cr) = lb.conn {
                segs_raw.push(RawSeg::Train(cr));
            }
        } else if let Some(prev) = &lb.prev {
            let from_idx = prev.station;
            let to_idx = lb.station;
            let city_code = graph
                .station_to_city_code
                .get(&from_idx)
                .cloned()
                .unwrap_or_default();
            let city_name = graph
                .city_code_to_name
                .get(&city_code)
                .cloned()
                .unwrap_or_default();
            let start_m = prev.arrive;
            let end_m = lb.arrive;
            let xfer_m = lb.inter_minutes - prev.inter_minutes;
            segs_raw.push(RawSeg::Inter((from_idx, to_idx, start_m, end_m, xfer_m, city_code, city_name)));
        }
        cur = lb.prev.as_ref();
    }
    if segs_raw.is_empty() {
        return None;
    }
    segs_raw.reverse();

    let mut typed_segments: Vec<PathSegment> = Vec::new();
    let mut merged_train: Vec<ConnRef> = Vec::new();
    for item in segs_raw {
        match item {
            RawSeg::Train(cr) => merged_train.push(cr),
            RawSeg::Inter((from_idx, to_idx, start_m, end_m, xfer_m, ccode, cname)) => {
                if !merged_train.is_empty() {
                    for s in collapse_train_segments(graph, &merged_train) {
                        typed_segments.push(PathSegment::Train(s));
                    }
                    merged_train.clear();
                }
                typed_segments.push(PathSegment::Interstation(InterstationTransferSegment {
                    from_station: graph.idx_to_station[from_idx].clone(),
                    to_station: graph.idx_to_station[to_idx].clone(),
                    start_minutes: start_m,
                    end_minutes: end_m,
                    transfer_minutes: xfer_m,
                    city_code: ccode,
                    city_name: cname,
                }));
            }
        }
    }
    if !merged_train.is_empty() {
        for s in collapse_train_segments(graph, &merged_train) {
            typed_segments.push(PathSegment::Train(s));
        }
    }
    if typed_segments.is_empty() {
        return None;
    }

    let first_seg = &typed_segments[0];
    let last_seg = &typed_segments[typed_segments.len() - 1];
    let (actual_origin, first_dep) = match first_seg {
        PathSegment::Train(s) => (s.from_station.clone(), s.depart_minutes),
        PathSegment::Interstation(s) => (s.from_station.clone(), s.start_minutes),
    };
    let (actual_dest, final_arr) = match last_seg {
        PathSegment::Train(s) => (s.to_station.clone(), s.arrive_minutes),
        PathSegment::Interstation(s) => (s.to_station.clone(), s.end_minutes),
    };

    let mut total_min = final_arr - first_dep;
    if total_min < 0 {
        total_min += 2880;
    }

    let rail_dist: i32 = typed_segments
        .iter()
        .map(|s| match s {
            PathSegment::Train(t) => t.distance,
            PathSegment::Interstation(_) => 0,
        })
        .sum();

    let mut xfer_cities: Vec<String> = Vec::new();
    for i in 1..typed_segments.len() {
        let prev_seg = &typed_segments[i - 1];
        let curr_seg = &typed_segments[i];
        match (prev_seg, curr_seg) {
            (PathSegment::Train(p), PathSegment::Train(c)) => {
                if p.train_code != c.train_code {
                    let from_idx = graph
                        .station_to_idx
                        .get(&c.from_station)
                        .copied()
                        .unwrap_or(usize::MAX);
                    let city_code = graph.station_to_city_code.get(&from_idx).cloned().unwrap_or_default();
                    if !city_code.is_empty() {
                        let city_name = graph.city_code_to_name.get(&city_code).cloned().unwrap_or(city_code);
                        if !xfer_cities.contains(&city_name) {
                            xfer_cities.push(city_name);
                        }
                    }
                }
            }
            (PathSegment::Interstation(p), _) => {
                let city_name = graph
                    .city_code_to_name
                    .get(&p.city_code)
                    .cloned()
                    .unwrap_or_else(|| p.city_code.clone());
                if !xfer_cities.contains(&city_name) {
                    xfer_cities.push(city_name);
                }
            }
            _ => {}
        }
    }

    Some(RouteResult {
        segments: typed_segments,
        actual_origin,
        actual_destination: actual_dest,
        first_departure: first_dep,
        final_arrival: final_arr,
        total_minutes: total_min,
        rail_distance: rail_dist,
        train_transfers: label.train_xfers,
        interstation_transfers: label.inter_xfers,
        interstation_minutes: label.inter_minutes,
        transfer_cities: xfer_cities,
        matched_transfer_constraint: label.matched_constraint,
    })
}

// ── 独立直达枚举（直达永远完整）──────────────────────

/// 直达方案独立枚举：不受 CSA 标签截断 / 绕路 / 耗时剪枝影响。
fn collect_direct_routes(
    graph: &Graph,
    request: &SearchRequest,
    source_set: &HashSet<usize>,
    target_set: &HashSet<usize>,
) -> Vec<RouteResult> {
    let mut routes: Vec<RouteResult> = Vec::new();
    let mut seen: HashSet<(String, usize, usize)> = HashSet::new(); // (code, s, t)
    let (e_dep, l_dep) = (request.earliest_depart, request.latest_depart);
    let (e_arr, l_arr) = (request.earliest_arrive, request.latest_arrive);

    for &s in source_set {
        for conn in &graph.out_conns[s] {
            let dep_m = conn.depart_minutes;
            if !(e_dep <= dep_m && dep_m <= l_dep) {
                continue;
            }
            let stops = match graph.train_stops.get(&conn.train_code) {
                Some(st) => st,
                None => continue,
            };
            if stops.is_empty() || stops[stops.len() - 1].4 == 0 {
                continue; // 全程里程为 0 的车次（数据无效）：不参与直达枚举
            }
            let seq_f = conn.seq_from;
            let pos = match stops.iter().position(|st| st.0 == s && st.3 == seq_f) {
                Some(p) => p,
                None => continue,
            };
            if stops[pos].1 == -1 {
                continue;
            }
            let day = (dep_m - stops[pos].1).div_euclid(1440); // Python // 语义（可为负）
            let base_cum = stops[pos].4;
            for st in &stops[pos + 1..] {
                let (st_idx, _, arr2, _, cum) = *st;
                if arr2 == -1 || !target_set.contains(&st_idx) {
                    continue;
                }
                if seen.contains(&(conn.train_code.clone(), s, st_idx)) {
                    continue;
                }
                let arr_abs = arr2 + day * 1440;
                if !(e_arr <= arr_abs && arr_abs <= l_arr) {
                    continue;
                }
                seen.insert((conn.train_code.clone(), s, st_idx));
                let dist = (cum as i32 - base_cum as i32).max(0);
                routes.push(RouteResult {
                    segments: vec![PathSegment::Train(TrainSegment {
                        train_code: conn.train_code.to_string(),
                        from_station: graph.idx_to_station[s].clone(),
                        to_station: graph.idx_to_station[st_idx].clone(),
                        depart_minutes: dep_m,
                        arrive_minutes: arr_abs,
                        travel_minutes: arr_abs - dep_m,
                        distance: dist,
                    })],
                    actual_origin: graph.idx_to_station[s].clone(),
                    actual_destination: graph.idx_to_station[st_idx].clone(),
                    first_departure: dep_m,
                    final_arrival: arr_abs,
                    total_minutes: arr_abs - dep_m,
                    rail_distance: dist,
                    train_transfers: 0,
                    interstation_transfers: 0,
                    interstation_minutes: 0,
                    transfer_cities: Vec::new(),
                    matched_transfer_constraint: false,
                });
            }
        }
    }
    routes
}

// ── 预扫描：每换乘级别最短总耗时 ─────────────────────────

type PrescanTag = (i32, Rc<str>, i32); // (arrive, code, first_departure)

/// 多标签 CSA 的"每换乘级别最短总耗时"（对齐 _prescan_best_durations）。
///
/// 单标签最早到达支配会丢失直达基准（footpath 标签支配同车次续乘链），
/// 因此每站按换乘级别各保留一个最早到达标签（每站 ≤ max_transfers+1 个）。
#[allow(clippy::too_many_arguments)]
fn prescan_best_durations(
    graph: &Graph,
    request: &SearchRequest,
    source_set: &HashSet<usize>,
    target_set: &HashSet<usize>,
) -> Vec<Option<i32>> {
    let out_conns = &graph.out_conns;
    // 标签: station → Vec[(xfers, (arrive, code, first_dep))]，Vec 保插入序；
    // HashMap 仅按站索引访问（不遍历），同车次续乘的"并列最早级别"选择依赖 Vec 迭代序，
    // 必须与 Python 逐位一致
    // source 站含伪标签 (0, (-1, "", -1))
    let mut tags: HashMap<usize, Vec<(usize, PrescanTag)>> = HashMap::new();
    for &s in source_set {
        tags.insert(s, vec![(0usize, (-1, Rc::<str>::from(""), -1))]);
    }
    let (earliest_depart, latest_depart) = (request.earliest_depart, request.latest_depart);
    let (earliest_arrive, latest_arrive) = (request.earliest_arrive, request.latest_arrive);
    let same_buffer = request.same_station_transfer_minutes;
    let max_transfers = request.max_transfers;
    let foot_time = request.interstation_transfer_minutes;
    let same_city_of = &graph.same_city_of;
    let mut best_by: Vec<Option<i32>> = vec![None; max_transfers + 1];

    // 堆归并：每站一个迭代器（起始 = 该站最早可登车时刻）
    let mut heap: BinaryHeap<Reverse<(i32, usize, usize)>> = BinaryHeap::new();
    let mut pos_of: HashMap<usize, usize> = HashMap::new();
    let mut earliest_of: HashMap<usize, i32> = HashMap::new();
    for &s in source_set {
        let pos = out_conns[s].partition_point(|c| c.depart_minutes < earliest_depart);
        if pos < out_conns[s].len() {
            heap.push(Reverse((out_conns[s][pos].depart_minutes, s, pos)));
            pos_of.insert(s, pos);
            earliest_of.insert(s, -1); // 伪标签
        }
    }

    // _sync_heap（标签到达变早时回退迭代器起点）
    fn sync_heap(
        heap: &mut BinaryHeap<Reverse<(i32, usize, usize)>>,
        pos_of: &mut HashMap<usize, usize>,
        earliest_of: &mut HashMap<usize, i32>,
        out_conns: &[Vec<crate::graph::Connection>],
        station: usize,
        min_arr: i32,
    ) {
        let old = earliest_of.get(&station).copied();
        if let Some(o) = old {
            if min_arr >= o {
                return; // 未变早
            }
        }
        earliest_of.insert(station, min_arr);
        let tp = out_conns[station].partition_point(|c| c.depart_minutes < min_arr);
        if tp >= out_conns[station].len() {
            return;
        }
        let old_pos = pos_of.get(&station).copied();
        match old_pos {
            None => {
                pos_of.insert(station, tp);
                heap.push(Reverse((out_conns[station][tp].depart_minutes, station, tp)));
            }
            Some(old_pos) if tp < old_pos => {
                pos_of.insert(station, tp);
                heap.push(Reverse((out_conns[station][tp].depart_minutes, station, tp)));
            }
            _ => {}
        }
    }

    // 标签更新（对齐 _update）：返回是否更新成功（仅成功时才做 footpath 扩散）
    macro_rules! update {
        ($t:expr, $arr_m:expr, $code:expr, $first_dep:expr, $new_xfers:expr) => {{
            let level = tags.entry($t).or_default();
            let old = level
                .iter()
                .find(|(k, _)| *k == $new_xfers)
                .map(|(_, v)| v.clone());
            let updated = match old {
                Some((old_arr, _, _)) => !($arr_m >= old_arr),
                None => true,
            };
            if updated {
                match level.iter_mut().find(|(k, _)| *k == $new_xfers) {
                    Some(slot) => {
                        slot.1 = ($arr_m, $code.clone(), $first_dep);
                    }
                    None => level.push(($new_xfers, ($arr_m, $code.clone(), $first_dep))),
                }
                let min_arr = level
                    .iter()
                    .filter(|(_, (a, _, _))| *a >= 0)
                    .map(|(_, (a, _, _))| *a)
                    .min();
                if let Some(ma) = min_arr {
                    sync_heap(&mut heap, &mut pos_of, &mut earliest_of, out_conns, $t, ma);
                }
                if target_set.contains(&$t) && earliest_arrive <= $arr_m && $arr_m <= latest_arrive {
                    let duration = $arr_m - $first_dep;
                    let bd = best_by[$new_xfers];
                    if bd.is_none() || duration < bd.unwrap() {
                        best_by[$new_xfers] = Some(duration);
                    }
                }
                true
            } else {
                false
            }
        }};
    }

    while let Some(Reverse((dep_m, f, pos))) = heap.pop() {
        if pos_of.get(&f) != Some(&pos) {
            continue; // 过期条目（回退产生），不参与早停判定（与 pyref 逐位一致）
        }
        // 提前终止（安全前提：双日模型中连接满足 arrive ≥ depart，跨夜已 +1440）：
        // best_by 全部锁定后，任何新标签 duration = arr - first_dep
        // ≥ dep_m - latest_depart（登车窗口上界）；下界超过全部基准则后续不可能改善
        // 任何 best_by[k] → 跳过剩余连接（基准不变 → 主循环剪枝不变 → 结果集不变）。
        if best_by.iter().all(|b| b.is_some()) {
            let max_best = best_by.iter().filter_map(|b| *b).max().unwrap();
            if dep_m > max_best + latest_depart {
                if std::env::var_os("RAILWAY_ROUTE_TIMING").is_some() {
                    eprintln!("[prescan-stop] dep_m={dep_m} max_best={max_best} latest_depart={latest_depart}");
                }
                break;
            }
        }
        let raw = &out_conns[f][pos];
        let code = Rc::<str>::from(raw.train_code.as_str());
        let t = raw.to_idx;
        let arr_m = raw.arrive_minutes;
        let nxt = pos + 1;
        if nxt < out_conns[f].len() {
            pos_of.insert(f, nxt);
            heap.push(Reverse((out_conns[f][nxt].depart_minutes, f, nxt)));
        }
        // 快照本站标签（保插入序；避免与 update! 的 tags 可变借用冲突，Rc 克隆廉价）
        let tag_snapshot: Vec<(usize, PrescanTag)> = match tags.get(&f) {
            Some(t) => t.clone(),
            None => continue,
        };

        let pseudo = tag_snapshot
            .iter()
            .find(|(k, _)| *k == 0)
            .map(|(_, v)| v.clone());
        if let Some((p_time, _, _)) = pseudo {
            if p_time == -1 {
                // 初始登车（仅 source 站，且该站尚未有真实 0 转标签）
                if !(earliest_depart <= dep_m && dep_m <= latest_depart) {
                    continue;
                }
                let first_dep = dep_m;
                let new_xfers = 0usize;
                if update!(t, arr_m, code, first_dep, new_xfers) {
                    let partners = &same_city_of[t];
                    if !partners.is_empty() {
                        for &other in partners {
                            if other != t {
                                // 与主搜索 expand_footpath 同源：按对查表（无数据回退固定值）
                                let ft = match graph.foot_times.get(&(t, other)) {
                                    Some(&f) => f,
                                    None => foot_time,
                                };
                                update!(other, arr_m + ft, Rc::<str>::from(""), first_dep, new_xfers);
                            }
                        }
                    }
                }
                continue;
            }
        }
        // 同车次续乘：该车次各转数级别中不晚于 dep_m 的最早标签
        let mut cont: Option<(i32, i32, usize)> = None; // (s_time, s_first, s_xfers)
        for &(s_xfers, (s_time, ref s_code, s_first)) in &tag_snapshot {
            if s_code.as_ref() == code.as_ref() && s_time != -1 && s_time <= dep_m {
                if cont.is_none() || s_time < cont.unwrap().0 {
                    cont = Some((s_time, s_first, s_xfers));
                }
            }
        }
        if let Some((_, s_first, s_xfers)) = cont {
            let first_dep = s_first;
            let new_xfers = s_xfers;
            if update!(t, arr_m, code, first_dep, new_xfers) {
                let partners = &same_city_of[t];
                if !partners.is_empty() {
                    for &other in partners {
                        if other != t {
                            // 与主搜索 expand_footpath 同源：按对查表（无数据回退固定值）
                            let ft = match graph.foot_times.get(&(t, other)) {
                                Some(&f) => f,
                                None => foot_time,
                            };
                            update!(other, arr_m + ft, Rc::<str>::from(""), first_dep, new_xfers);
                        }
                    }
                }
            }
        } else {
            // 换乘：遍历各转数级别（各自缓冲 + 转数上限）
            for &(s_xfers, (s_time, _, s_first)) in &tag_snapshot {
                if s_time == -1 {
                    continue;
                }
                if s_xfers + 1 > max_transfers {
                    continue;
                }
                if s_time + same_buffer > dep_m {
                    continue;
                }
                update!(t, arr_m, code.clone(), s_first, s_xfers + 1);
            }
        }
    }

    // 前缀最小化：best[k] = k 转"以内"的最短耗时
    let raw_best = best_by.clone();
    let mut cur: Option<i32> = None;
    for k in 0..=max_transfers {
        if let Some(b) = best_by[k] {
            if cur.is_none() || b < cur.unwrap() {
                cur = Some(b);
            }
        }
        best_by[k] = cur;
    }
    if std::env::var_os("RAILWAY_ROUTE_TIMING").is_some() {
        eprintln!("[prescan] raw_best={raw_best:?} final={best_by:?}");
    }
    best_by
}

// ── 剪枝 / footpath ─────────────────────────────────────

fn prune_by_duration(
    t: usize,
    arr_m: i32,
    first_dep: i32,
    xfers: usize,
    target_flag: &[bool],
    prune_slack: Option<i32>,
    best_durations: &[Option<i32>],
    h_time_arr: &[f64],
) -> bool {
    if prune_slack.is_none() || target_flag[t] {
        return false;
    }
    let slack = prune_slack.unwrap();
    let bd = match best_durations.get(xfers).copied().flatten() {
        Some(b) => b,
        None => return false,
    };
    (arr_m - first_dep) as f64 + h_time_arr[t] > bd as f64 + slack as f64
}

/// 同城 footpath 松弛（对齐 _expand_footpath）：仅从列车段标签扩散。
/// 返回插入成功的 (other, fp_arr)——调用方须将这些站加入堆。
#[allow(clippy::too_many_arguments)]
fn expand_footpath(
    cur: &mut HashMap<usize, Vec<Rc<Label>>>,
    code_arr: &mut HashMap<usize, HashMap<Rc<str>, i32>>,
    graph: &Graph,
    cand: &Rc<Label>,
    t: usize,
    arr_m: i32,
    fp_done: &mut HashSet<(usize, i32)>,
    same_city_arr: &[Vec<usize>],
    foot_time: i32,
    h_dist_arr: &[f64],
    h_time_arr: &[f64],
    detour_limit: f64,
    prune_slack: Option<i32>,
    best_durations: &[Option<i32>],
    target_flag: &[bool],
    constraint_city: Option<&str>,
    city_of: &HashMap<usize, String>,
    state_limits: Option<usize>,
    has_constraint: bool,
    max_transfers: usize,
) -> Vec<(usize, i32)> {
    if cand.train_xfers + cand.inter_xfers + 1 > max_transfers {
        return Vec::new(); // 地面换乘计入总换乘上限（乘客视角）
    }
    let partners = &same_city_arr[t];
    if partners.is_empty() {
        return Vec::new();
    }
    if fp_done.contains(&(t, arr_m)) {
        return Vec::new(); // 同一 (站, 到达时刻) 只扩散一次
    }
    fp_done.insert((t, arr_m));
    let mut inserted: Vec<(usize, i32)> = Vec::new();
    for &other in partners {
        if other == t {
            continue;
        }
        // 5.1-1 异站换乘按对估算：直达/坐标预计算表（无数据对回退固定值）
        let ft = match graph.foot_times.get(&(t, other)) {
            Some(&f) => f,
            None => foot_time,
        };
        let fp_arr = arr_m + ft;
        if cand.rail_distance as f64 + h_dist_arr[other] > detour_limit {
            continue;
        }
        if prune_by_duration(
            other,
            fp_arr,
            cand.first_dep,
            cand.train_xfers,
            target_flag,
            prune_slack,
            best_durations,
            h_time_arr,
        ) {
            continue;
        }
        // 前置同码检查：other 已有不晚于 fp_arr 的 footpath 标签则跳过
        if let Some(lst) = cur.get(&other) {
            if lst.iter().any(|lb| lb.train_code.is_empty() && lb.arrive <= fp_arr) {
                continue;
            }
        }
        let fp = Rc::new(Label {
            station: other,
            arrive: fp_arr,
            train_code: Rc::from(""),
            first_dep: cand.first_dep,
            rail_distance: cand.rail_distance,
            train_xfers: cand.train_xfers,
            inter_xfers: cand.inter_xfers + 1,
            inter_minutes: cand.inter_minutes + ft,
            prev: Some(cand.clone()),
            conn: None,
            seg_kind: 1,
            matched_constraint: cand.matched_constraint
                || (constraint_city.is_some() && city_of.get(&t).map(|c| c.as_str()) == constraint_city),
        });
        if insert_round_label(cur, code_arr, other, fp, state_limits, has_constraint) {
            inserted.push((other, fp_arr));
        }
    }
    inserted
}

// ── 核心搜索 ────────────────────────────────────────────

/// 统一多源/多目标轮次化 CSA 搜索入口（对齐 search()）。
pub fn search(
    graph: &Graph,
    matcher: &MatcherData,
    request: &SearchRequest,
) -> Result<SearchResponse, String> {
    let t_start = std::time::Instant::now();
    let settings = crate::models::profile_settings(&request.search_profile);
    let timeout = request.timeout_seconds.min(settings.default_timeout_seconds);

    // ── 解析起终点集合（每端可独立 exact/fuzzy；
    //    多站精确：from_stations/to_stations 非空时逐站精确解析取并集）──
    let source_names = match &request.from_stations {
        Some(list) => resolve_multi(graph, matcher, list)?,
        None => {
            let from_mode = request.from_mode.as_deref().unwrap_or(&request.match_mode);
            resolve_station_set(graph, matcher, &request.from_query, from_mode)?
        }
    };
    let target_names = match &request.to_stations {
        Some(list) => resolve_multi(graph, matcher, list)?,
        None => {
            let to_mode = request.to_mode.as_deref().unwrap_or(&request.match_mode);
            resolve_station_set(graph, matcher, &request.to_query, to_mode)?
        }
    };
    let source_set: HashSet<usize> = source_names
        .iter()
        .filter_map(|n| graph.station_to_idx.get(n).copied())
        .collect();
    let target_set: HashSet<usize> = target_names
        .iter()
        .filter_map(|n| graph.station_to_idx.get(n).copied())
        .collect();
    if source_set.is_empty() || target_set.is_empty() {
        return Ok(SearchResponse {
            routes: Vec::new(),
            complete: true,
            stopped_reason: None,
            elapsed_ms: t_start.elapsed().as_millis() as u64,
            scanned_connections: 0,
            generated_states: 0,
            returned_routes: 0,
            source_stations: source_names,
            target_stations: target_names,
        });
    }

    // ── 解析 transfer_at 城市约束 ──
    let constraint_city: Option<String> = match &request.transfer_city_code {
        Some(code) => resolve_city_code(graph, matcher, code).ok(),
        None => None,
    };


    // ── 反向下界（绕路过滤 + 耗时剪枝）──
    let mut targets_list: Vec<usize> = target_set.iter().copied().collect();
    targets_list.sort();
    let h_dist = graph.get_multi_source_distances(&targets_list);
    let h_time = graph.get_multi_source_times(&targets_list);
    let straight_dist = source_set
        .iter()
        .map(|s| h_dist.get(s).copied().unwrap_or(100) as f64)
        .fold(None, |acc: Option<f64>, v| Some(acc.map_or(v, |a: f64| a.min(v))))
        .unwrap_or(100.0);
    let (detour_limit, prune_slack) = match &constraint_city {
        Some(_) => (
            straight_dist * MAX_DETOUR_RATIO_CONSTRAINED,
            settings.time_prune_slack.map(|s| s + CONSTRAINED_SLACK_PENALTY),
        ),
        None => (
            straight_dist * MAX_DETOUR_RATIO,
            settings.time_prune_slack,
        ),
    };

    let n_stations = graph.station_count();
    let h_dist_arr: Vec<f64> = (0..n_stations).map(|i| h_dist.get(&i).copied().unwrap_or(0) as f64).collect();
    let h_time_arr: Vec<f64> = (0..n_stations).map(|i| h_time.get(&i).copied().unwrap_or(0) as f64).collect();
    let target_flag: Vec<bool> = (0..n_stations).map(|i| target_set.contains(&i)).collect();
    let source_flag: Vec<bool> = (0..n_stations).map(|i| source_set.contains(&i)).collect();



    // 耗时剪枝基准：预扫描锁定"每换乘级别的最短总耗时"
    let t_prescan = std::time::Instant::now();
    let best_durations: Vec<Option<i32>> =
        prescan_best_durations(graph, request, &source_set, &target_set);
    let prescan_ms = t_prescan.elapsed().as_millis();


    let out_conns = &graph.out_conns;
    let max_transfers = request.max_transfers;
    let rounds = max_transfers + 1;
    let state_limits = settings.max_states_per_station;
    let state_limit = settings.state_limit;
    let city_of = &graph.station_to_city_code;
    let (earliest_depart, latest_depart) = (request.earliest_depart, request.latest_depart);
    let (earliest_arrive, latest_arrive) = (request.earliest_arrive, request.latest_arrive);
    let same_buffer = request.same_station_transfer_minutes;
    let foot_time = request.interstation_transfer_minutes;

    let mut round_labels: Vec<HashMap<usize, Vec<Rc<Label>>>> =
        (0..rounds).map(|_| HashMap::new()).collect();
    let mut dest_labels: Vec<Vec<Rc<Label>>> = (0..rounds).map(|_| Vec::new()).collect();
    let mut scanned: u64 = 0;
    let mut generated: u64 = 0;
    let mut complete = true;
    let mut stopped_reason: Option<String> = None;

    let has_constraint = constraint_city.is_some();
    for r in 0..rounds {
        let mut cur: HashMap<usize, Vec<Rc<Label>>> = HashMap::new();
        let mut code_arr: HashMap<usize, HashMap<Rc<str>, i32>> = HashMap::new();
        let mut fp_done: HashSet<(usize, i32)> = HashSet::new();
        let prev_round = if r > 0 { Some(&round_labels[r - 1]) } else { None };
        let is_first = r == 0;

        // 活动站 → 桶起始位置：堆归并
        let mut heap: BinaryHeap<Reverse<(i32, usize, usize)>> = BinaryHeap::new();
        let mut pos_of: HashMap<usize, usize> = HashMap::new();
        let mut earliest_of: HashMap<usize, i32> = HashMap::new();
        if is_first {
            for &s in &source_set {
                let pos = out_conns[s].partition_point(|c| c.depart_minutes < earliest_depart);
                if pos < out_conns[s].len() {
                    heap.push(Reverse((out_conns[s][pos].depart_minutes, s, pos)));
                    pos_of.insert(s, pos);
                    earliest_of.insert(s, -1); // 伪标签
                }
            }
        } else if let Some(prev) = prev_round {
            for (f, lst) in prev {
                let min_arr = lst.iter().map(|lb| lb.arrive).min().unwrap();
                let pos = out_conns[*f].partition_point(|c| c.depart_minutes < min_arr + same_buffer);
                if pos < out_conns[*f].len() {
                    heap.push(Reverse((out_conns[*f][pos].depart_minutes, *f, pos)));
                    pos_of.insert(*f, pos);
                    earliest_of.insert(*f, min_arr + same_buffer);
                }
            }
        }

        let mut processed: u64 = 0;
        let mut stopped = false;
        while let Some(Reverse((_, f, pos))) = heap.pop() {
            if pos_of.get(&f) != Some(&pos) {
                continue; // 过期条目
            }
            let bucket = &out_conns[f];
            let is_src_ok = is_first && source_flag[f];
            let cl = cur.get(&f).cloned(); // 快照：段内 cur[f] 不会被修改（无自环边）
            let pl = prev_round.and_then(|p| p.get(&f)).cloned();
            let mut pos = pos;
            loop {
                let raw = &bucket[pos];
                let dep_m = raw.depart_minutes;
                let code = Rc::<str>::from(raw.train_code.as_str());
                let t = raw.to_idx;
                let arr_m = raw.arrive_minutes;
                let dist = raw.distance as i32;

                processed += 1;
                scanned += 1;
                if processed % 20000 == 0 {
                    // 注意：elapsed 为毫秒，timeout 为秒（Python 侧以秒比较）
                    let elapsed_ms = t_start.elapsed().as_millis() as u64;
                    if elapsed_ms > timeout * 1000 {
                        stopped_reason = Some("timeout".to_string());
                        complete = false;
                        stopped = true;
                        break;
                    }
                    if generated > state_limit {
                        stopped_reason = Some("state_limit".to_string());
                        complete = false;
                        stopped = true;
                        break;
                    }
                }

                // ── 来源 1：初始登车（仅第 0 轮）──
                if is_src_ok && earliest_depart <= dep_m && dep_m <= latest_depart {
                    generated += 1;
                    let rail = dist;
                    if rail as f64 + h_dist_arr[t] <= detour_limit {
                        let cand = Rc::new(Label {
                            station: t,
                            arrive: arr_m,
                            train_code: code.clone(),
                            first_dep: dep_m,
                            rail_distance: rail,
                            train_xfers: 0,
                            inter_xfers: 0,
                            inter_minutes: 0,
                            prev: None,
                            conn: Some(ConnRef { f, pos }),
                            seg_kind: 0,
                            matched_constraint: false,
                        });
                        if insert_round_label(&mut cur, &mut code_arr, t, cand.clone(), state_limits, has_constraint) {
                            if let Some(lst_t) = cur.get(&t) {
                                sync_heap_main(&mut heap, &mut pos_of, &mut earliest_of, out_conns, t, lst_t[0].arrive);
                            }
                            if !graph.same_city_of[t].is_empty() {
                                let inserted = expand_footpath(
                                    &mut cur, &mut code_arr, graph, &cand, t, arr_m, &mut fp_done,
                                    &graph.same_city_of, foot_time, &h_dist_arr, &h_time_arr, detour_limit,
                                    prune_slack, &best_durations, &target_flag,
                                    constraint_city.as_deref(), city_of, state_limits, has_constraint,
                                    max_transfers,
                                );
                                enqueue_fp_targets(&mut heap, &mut pos_of, &mut earliest_of, out_conns, &cur, &inserted);
                            }
                        }
                    }
                }

                // ── 来源 2：同车续乘（本轮即时标签）──
                if let Some(cl) = &cl {
                    for lb in cl.iter() {
                        if lb.arrive > dep_m {
                            break; // 列表按到达升序
                        }
                        if lb.train_code.as_ref() != code.as_ref() {
                            continue;
                        }
                        generated += 1;
                        let rail = lb.rail_distance + dist;
                        if rail as f64 + h_dist_arr[t] > detour_limit {
                            continue;
                        }
                        if prune_slack.is_some() && !target_flag[t] {
                            if let Some(bd) = best_durations.get(lb.train_xfers).copied().flatten() {
                                if (arr_m - lb.first_dep) as f64 + h_time_arr[t]
                                    > bd as f64 + prune_slack.unwrap() as f64
                                {
                                    continue;
                                }
                            }
                        }
                        let cand = Rc::new(Label {
                            station: t,
                            arrive: arr_m,
                            train_code: code.clone(),
                            first_dep: lb.first_dep,
                            rail_distance: rail,
                            train_xfers: lb.train_xfers,
                            inter_xfers: lb.inter_xfers,
                            inter_minutes: lb.inter_minutes,
                            prev: Some(lb.clone()),
                            conn: Some(ConnRef { f, pos }),
                            seg_kind: 0,
                            matched_constraint: lb.matched_constraint,
                        });
                        if insert_round_label(&mut cur, &mut code_arr, t, cand.clone(), state_limits, has_constraint) {
                            if let Some(lst_t) = cur.get(&t) {
                                sync_heap_main(&mut heap, &mut pos_of, &mut earliest_of, out_conns, t, lst_t[0].arrive);
                            }
                            if !graph.same_city_of[t].is_empty() {
                                let inserted = expand_footpath(
                                    &mut cur, &mut code_arr, graph, &cand, t, arr_m, &mut fp_done,
                                    &graph.same_city_of, foot_time, &h_dist_arr, &h_time_arr, detour_limit,
                                    prune_slack, &best_durations, &target_flag,
                                    constraint_city.as_deref(), city_of, state_limits, has_constraint,
                                    max_transfers,
                                );
                                enqueue_fp_targets(&mut heap, &mut pos_of, &mut earliest_of, out_conns, &cur, &inserted);
                            }
                        }
                    }
                }

                // ── 来源 3：换乘（上一轮标签）──
                if let Some(pl) = &pl {
                    for lb in pl.iter() {
                        if lb.arrive + same_buffer > dep_m {
                            break;
                        }
                        if lb.train_xfers + lb.inter_xfers + 1 > max_transfers {
                            continue;
                        }
                        // 同车次跨轮"换乘"一律无效（同趟应续乘）
                        if lb.train_code.as_ref() == code.as_ref() {
                            continue;
                        }
                        generated += 1;
                        let rail = lb.rail_distance + dist;
                        if rail as f64 + h_dist_arr[t] > detour_limit {
                            continue;
                        }
                        let mut matched = lb.matched_constraint;
                        if constraint_city.is_some() && !matched
                            && city_of.get(&f).map(|c| c.as_str()) == constraint_city.as_deref()
                        {
                            matched = true;
                        }
                        if prune_slack.is_some() && !target_flag[t] {
                            let xfers_new = lb.train_xfers + 1;
                            if let Some(bd) = best_durations.get(xfers_new).copied().flatten() {
                                if (arr_m - lb.first_dep) as f64 + h_time_arr[t]
                                    > bd as f64 + prune_slack.unwrap() as f64
                                {
                                    continue;
                                }
                            }
                        }
                        let cand = Rc::new(Label {
                            station: t,
                            arrive: arr_m,
                            train_code: code.clone(),
                            first_dep: lb.first_dep,
                            rail_distance: rail,
                            train_xfers: lb.train_xfers + 1,
                            inter_xfers: lb.inter_xfers,
                            inter_minutes: lb.inter_minutes,
                            prev: Some(lb.clone()),
                            conn: Some(ConnRef { f, pos }),
                            seg_kind: 0,
                            matched_constraint: matched,
                        });
                        if insert_round_label(&mut cur, &mut code_arr, t, cand.clone(), state_limits, has_constraint) {
                            if let Some(lst_t) = cur.get(&t) {
                                sync_heap_main(&mut heap, &mut pos_of, &mut earliest_of, out_conns, t, lst_t[0].arrive);
                            }
                            if !graph.same_city_of[t].is_empty() {
                                let inserted = expand_footpath(
                                    &mut cur, &mut code_arr, graph, &cand, t, arr_m, &mut fp_done,
                                    &graph.same_city_of, foot_time, &h_dist_arr, &h_time_arr, detour_limit,
                                    prune_slack, &best_durations, &target_flag,
                                    constraint_city.as_deref(), city_of, state_limits, has_constraint,
                                    max_transfers,
                                );
                                enqueue_fp_targets(&mut heap, &mut pos_of, &mut earliest_of, out_conns, &cur, &inserted);
                            }
                        }
                    }
                }

                // 批处理推进
                let nxt = pos + 1;
                if nxt >= bucket.len() {
                    break; // 本站连接耗尽
                }
                if let Some(top) = heap.peek() {
                    if bucket[nxt].depart_minutes > (top.0).0 {
                        pos_of.insert(f, nxt);
                        heap.push(Reverse((bucket[nxt].depart_minutes, f, nxt)));
                        break;
                    }
                }
                pos = nxt;
                pos_of.insert(f, nxt);
            }
            if stopped {
                break;
            }
        }

        // 轮末：从最终标签收集目标站（避免插入时收集导致已淘汰标签残留）
        for (st, lst) in &cur {
            if target_flag[*st] {
                for lb in lst {
                    if earliest_arrive <= lb.arrive && lb.arrive <= latest_arrive {
                        dest_labels[r].push(lb.clone());
                    }
                }
            }
        }

        round_labels[r] = cur;
        if !complete {
            break;
        }
    }

    // ── 回溯、过滤、去重 ──
    // 直达方案由独立枚举提供；CSA 第 0 轮仅作为换乘轮的标签基础。
    let mut direct_routes = collect_direct_routes(graph, request, &source_set, &target_set);
    if constraint_city.is_some() {
        direct_routes = Vec::new(); // 约束查询：直达（无换乘）不符合要求，全部排除
    }
    let mut results: Vec<RouteResult> = Vec::new();
    let mut seen_keys: HashSet<String> = HashSet::new();

    // 回溯：第 0 轮仅作为换乘轮的标签基础，从第 1 轮起收集目标站标签
    for r in 1..rounds {
        for lb in &dest_labels[r] {
            if constraint_city.is_some() && !lb.matched_constraint {
                continue;
            }
            let route = match reconstruct_from_label(graph, lb) {
                Some(rt) => rt,
                None => continue,
            };
            if route.total_minutes <= 0 {
                continue;
            }
            if route.rail_distance as f64 > detour_limit {
                continue;
            }
            // 速度过滤基于铁路行驶时间（不含跨夜等待/地面移动）
            let travel_minutes: i32 = route
                .segments
                .iter()
                .map(|s| match s {
                    PathSegment::Train(t) => t.travel_minutes,
                    PathSegment::Interstation(_) => 0,
                })
                .sum();
            if route.rail_distance > 0
                && travel_minutes > 0
                && route.rail_distance as f64 / ((travel_minutes as f64 / 60.0).max(0.01)) < MIN_SPEED_KPH
            {
                continue;
            }
            if has_repeated_station(&route) {
                continue;
            }
            let key = route_key_str(&route);
            if !seen_keys.contains(&key) {
                seen_keys.insert(key);
                results.push(route);
            }
        }
    }

// 合并直达 + 换乘，按（换乘次数, 总耗时）排序 → 直达天然在最前
    let mut all_routes = direct_routes;
    all_routes.extend(results);
    all_routes.sort_by_key(|r| (r.train_transfers + r.interstation_transfers, r.total_minutes));

    if let Some(max_results) = settings.max_results {
        if all_routes.len() > max_results {
            let direct_cnt = all_routes
                .iter()
                .filter(|r| r.train_transfers == 0 && r.interstation_transfers == 0)
                .count();
            all_routes.truncate(max_results.max(direct_cnt));
        }
    }

    let returned = all_routes.len();
    if std::env::var_os("RAILWAY_ROUTE_TIMING").is_some() {
        eprintln!(
            "[timing] prescan={prescan_ms}ms total={}ms scanned={scanned} generated={generated}",
            t_start.elapsed().as_millis()
        );
    }
    Ok(SearchResponse {
        routes: all_routes,
        complete,
        stopped_reason,
        elapsed_ms: t_start.elapsed().as_millis() as u64,
        scanned_connections: scanned,
        generated_states: generated,
        returned_routes: returned,
        source_stations: source_names,
        target_stations: target_names,
    })
}

/// 规范化 route key（与 Python route_key 等价，用于去重与对拍排序）
fn route_key_str(route: &RouteResult) -> String {
    route
        .segments
        .iter()
        .map(|s| s.seg_key())
        .collect::<Vec<_>>()
        .join("::")
}

// ── 主循环共享的堆同步（对齐 _sync_heap）──

fn sync_heap_main(
    heap: &mut BinaryHeap<Reverse<(i32, usize, usize)>>,
    pos_of: &mut HashMap<usize, usize>,
    earliest_of: &mut HashMap<usize, i32>,
    out_conns: &[Vec<crate::graph::Connection>],
    station: usize,
    min_arr: i32,
) {
    let old = earliest_of.get(&station).copied();
    if let Some(o) = old {
        if min_arr >= o {
            return; // 未变早
        }
    }
    earliest_of.insert(station, min_arr);
    let tp = out_conns[station].partition_point(|c| c.depart_minutes < min_arr);
    if tp >= out_conns[station].len() {
        return;
    }
    let old_pos = pos_of.get(&station).copied();
    match old_pos {
        None => {
            pos_of.insert(station, tp);
            heap.push(Reverse((out_conns[station][tp].depart_minutes, station, tp)));
        }
        Some(old_pos) if tp < old_pos => {
            pos_of.insert(station, tp);
            heap.push(Reverse((out_conns[station][tp].depart_minutes, station, tp)));
        }
        _ => {}
    }
}

/// footpath 标签插入成功的站同步迭代器（对齐 _enqueue_fp_targets）。
fn enqueue_fp_targets(
    heap: &mut BinaryHeap<Reverse<(i32, usize, usize)>>,
    pos_of: &mut HashMap<usize, usize>,
    earliest_of: &mut HashMap<usize, i32>,
    out_conns: &[Vec<crate::graph::Connection>],
    cur: &HashMap<usize, Vec<Rc<Label>>>,
    inserted: &[(usize, i32)],
) {
    for &(o, _) in inserted {
        if let Some(lst) = cur.get(&o) {
            if !lst.is_empty() {
                sync_heap_main(heap, pos_of, earliest_of, out_conns, o, lst[0].arrive);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    /// day 计算必须与 Python `//`（向下取整）一致：`(dep - base) // 1440`。
    /// Rust `/` 向零截断，负值场景（凌晨班次参照前日停靠）会产生 -1 天偏差。
    #[test]
    fn day_calc_matches_python_floor_div() {
        let cases: &[(i32, i32, i32)] = &[
            (1440, 0, 1),     // 整除
            (1500, 0, 1),     // 正余数
            (-1, 0, -1),      // Python -1//1440 = -1；Rust -1/1440 = 0
            (-1440, 0, -1),   // 整除负
            (1200, 1440, -1), // 凌晨班次参照前日停靠
            (0, 2880, -2),    // 跨两日
        ];
        for &(dep, base, expect) in cases {
            assert_eq!((dep - base).div_euclid(1440), expect, "dep={dep} base={base}");
            assert_eq!(
                (dep - base).div_euclid(1440),
                ((dep - base) as f64 / 1440.0).floor() as i32,
                "div_euclid != floor 语义: dep={dep} base={base}"
            );
        }
    }
}
