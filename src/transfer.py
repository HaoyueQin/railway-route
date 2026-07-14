"""
枢纽站换乘搜索。

策略：尝试所有 reasonable 的换乘方案，而非盲目图搜索。
1. 直达：origin → destination
2. 一次换乘：origin → hub → destination
3. 两次换乘：origin → hub1 → hub2 → destination

枢纽站 = 出发/到达车次数量超过阈值的站（取其与 origin/destination 的交集）。
"""

from dataclasses import dataclass
from typing import Optional
from collections import defaultdict

from src.graph import RailwayGraph

MIN_HUB_TRAINS = 30  # 枢纽站最少车次数


@dataclass
class MatchedSegment:
    train_code: str
    from_station: str
    to_station: str
    depart_time: str
    arrive_time: str
    travel_minutes: int
    distance: int


@dataclass
class MatchedRoute:
    segments: list[MatchedSegment]
    transfer_stations: list[str]
    total_travel_minutes: int
    total_transfer_minutes: int
    total_minutes: int
    total_distance: int


# ── 主入口 ────────────────────────────────────────────

def search_transfers(graph: RailwayGraph, from_st: str, to_st: str,
                     max_transfers: int = 2,
                     max_results: int = 100) -> list[MatchedRoute]:
    """搜索所有换乘方案（含直达）。"""
    sidx = graph.station_to_idx.get(from_st)
    eidx = graph.station_to_idx.get(to_st)
    if sidx is None or eidx is None:
        return []

    all_routes: list[MatchedRoute] = []

    # 0. 直达
    all_routes.extend(_find_all_direct(graph, from_st, to_st))

    # 1. 一次换乘
    if max_transfers >= 1:
        hubs = _get_candidate_hubs(graph, sidx, eidx, from_st, to_st)
        for hub_name, hub_idx in hubs:
            seg1_list = _find_all_direct(graph, from_st, hub_name)
            seg2_list = _find_all_direct(graph, hub_name, to_st)
            for s1 in seg1_list:
                for s2 in seg2_list:
                    route = _combine_two(graph, s1, s2)
                    if route:
                        all_routes.append(route)

    # 2. 两次换乘
    if max_transfers >= 2:
        for hub1_name, hub1_idx in hubs:
            seg1_list = _find_all_direct(graph, from_st, hub1_name)
            for s2_hub_name, s2_hub_idx in _get_candidate_hubs(graph, hub1_idx, eidx, hub1_name, to_st):
                seg2_list = _find_all_direct(graph, hub1_name, s2_hub_name)
                seg3_list = _find_all_direct(graph, s2_hub_name, to_st)
                for s1 in seg1_list:
                    for s2 in seg2_list:
                        r12 = _combine_two(graph, s1, s2)
                        if not r12:
                            continue
                        for s3 in seg3_list:
                            r23 = _combine_two(graph, MatchedRoute(
                                segments=r12.segments[1:], transfer_stations=[],
                                total_travel_minutes=r12.segments[1].travel_minutes,
                                total_transfer_minutes=0,
                                total_minutes=r12.segments[1].travel_minutes,
                                total_distance=r12.segments[1].distance,
                            ), s3)
                            if r23:
                                # Merge with first segment
                                all_segs = [r12.segments[0]] + r23.segments
                                tt = sum(s.travel_minutes for s in all_segs)
                                td = sum(s.distance for s in all_segs)
                                tw = _compute_wait(all_segs)
                                transfers = _extract_transfers(all_segs)
                                all_routes.append(MatchedRoute(
                                    segments=all_segs, transfer_stations=transfers,
                                    total_travel_minutes=tt, total_transfer_minutes=tw,
                                    total_minutes=tt + tw, total_distance=td,
                                ))

    # 过滤 + 去重 + 排序
    all_routes = _filter_unreasonable(all_routes, graph, sidx, eidx)
    seen = set()
    unique = []
    for r in all_routes:
        key = tuple((s.train_code, s.depart_time) for s in r.segments)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique.sort(key=lambda r: (len(r.transfer_stations), r.total_minutes))
    return unique[:max_results]


# ── 内部 ──────────────────────────────────────────────

def _find_all_direct(graph, from_st: str, to_st: str) -> list[MatchedRoute]:
    """两站之间所有直达方案。"""
    sidx = graph.station_to_idx.get(from_st)
    eidx = graph.station_to_idx.get(to_st)
    if sidx is None or eidx is None:
        return []

    results = []
    seen = set()
    for code, seq, dep in graph.departures.get(sidx, []):
        if code in seen:
            continue
        end_te = _scan_forward(graph, code, seq, eidx)
        if end_te:
            seen.add(code)
            travel = _pm(end_te.arrive_time) - _pm(dep)
            if travel < 0:
                travel += 24 * 60
            results.append(MatchedRoute(
                segments=[MatchedSegment(code, from_st, to_st, dep,
                                         end_te.arrive_time, travel,
                                         end_te.dist_cumulative)],
                transfer_stations=[], total_travel_minutes=travel,
                total_transfer_minutes=0, total_minutes=travel,
                total_distance=end_te.dist_cumulative,
            ))
    results.sort(key=lambda r: r.total_minutes)
    return results


def _scan_forward(graph, train_code, from_seq, target_idx):
    best = None
    for (f, t), trains in graph.edge_trains.items():
        if t == target_idx:
            for te in trains:
                if te.train_code == train_code and te.seq_from >= from_seq:
                    if best is None or te.seq_from < best.seq_from:
                        best = te
    return best


def _get_candidate_hubs(graph, origin_idx: int, dest_idx: int,
                        origin_name: str, dest_name: str) -> list[tuple[str, int]]:
    """找候选枢纽站：既可以从 origin 到达，也可以到达 dest。"""
    # 从 origin 出发能到达的站
    reachable = set()
    for code, seq, dep in graph.departures.get(origin_idx, []):
        # 沿车次向前找所有到达站
        for (f, t), trains in graph.edge_trains.items():
            for te in trains:
                if te.train_code == code and te.seq_from >= seq:
                    reachable.add((t, graph.idx_to_station[t]))

    # 能到达 dest 的站
    can_reach_dest = set()
    # 反向查：哪些站有车次能到 dest
    for (f, t), trains in graph.edge_trains.items():
        if t == dest_idx:
            for te in trains:
                can_reach_dest.add((f, graph.idx_to_station[f]))

    # 再检查这些站的出发站是否也能到 dest
    # 简化：找有足够多出发车次的站作为枢纽
    candidates = []
    for idx, name in reachable:
        if name in (origin_name, dest_name):
            continue
        n_trains = len(graph.departures.get(idx, []))
        if n_trains >= MIN_HUB_TRAINS or (idx, name) in can_reach_dest:
            candidates.append((name, idx))

    # 去重 + 按车次数降序
    candidates.sort(key=lambda x: -len(graph.departures.get(x[1], [])))
    seen = set()
    unique = []
    for name, idx in candidates:
        if name not in seen:
            seen.add(name)
            unique.append((name, idx))
    return unique[:30]  # 最多 30 个候选枢纽


def _combine_two(graph, r1: MatchedRoute, r2: MatchedRoute) -> Optional[MatchedRoute]:
    """尝试拼接两段直达方案（换乘衔接检查）。"""
    if not r1.segments or not r2.segments:
        return None
    s1 = r1.segments[-1]
    s2 = r2.segments[0]
    arr = _pm(s1.arrive_time)
    dep = _pm(s2.depart_time)
    if dep < arr:
        dep += 24 * 60
    wait = dep - arr
    if wait < 90:
        return None
    all_segs = r1.segments + r2.segments
    tt = r1.total_travel_minutes + r2.total_travel_minutes
    td = r1.total_distance + r2.total_distance
    return MatchedRoute(
        segments=all_segs,
        transfer_stations=[s1.to_station],
        total_travel_minutes=tt,
        total_transfer_minutes=wait,
        total_minutes=tt + wait,
        total_distance=td,
    )


def _filter_unreasonable(routes, graph, sidx, eidx):
    """过滤不合理的路径。"""
    # 用启发式距离作为基准
    from src.search import _compute_heuristic
    h = _compute_heuristic(graph, eidx)
    straight = h.get(sidx, 100)
    if straight < 10:
        straight = 100

    MAX_DETOUR = 3.0       # 最多绕路 3 倍
    MAX_MIN_PER_KM = 2.5   # 每公里最多 2.5 分钟（24 km/h 均速）

    filtered = []
    for r in routes:
        detour = r.total_distance / straight
        rate = r.total_minutes / max(r.total_distance, 1)
        if detour <= MAX_DETOUR and rate <= MAX_MIN_PER_KM:
            filtered.append(r)
    return filtered


def _pm(t):
    if not t or not t.strip():
        return 0
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _compute_wait(segs):
    total = 0
    for i in range(len(segs) - 1):
        arr = _pm(segs[i].arrive_time)
        dep = _pm(segs[i + 1].depart_time)
        if dep < arr:
            dep += 24 * 60
        total += dep - arr
    return total


def _extract_transfers(segs):
    return [segs[i].to_station for i in range(len(segs) - 1)]
