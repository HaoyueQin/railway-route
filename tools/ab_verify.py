"""桶化 vs 全量扫描 A/B 一致性验证。

独立实现"全量扫描"参考版 search（复用 src/csa 的辅助函数，
仅主循环改为每轮全量扫描连接），与当前桶化实现对比：
- 结果集（route_key 集合）完全一致
- complete 标志一致

用法: python tools/ab_verify.py
"""
import bisect
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.csa import (
    MAX_DETOUR_RATIO,
    MIN_SPEED_KPH,
    Label,
    _CONN_ARR,
    _CONN_CODE,
    _CONN_DEP,
    _CONN_DIST,
    _CONN_F,
    _CONN_T,
    _expand_footpath,
    _has_repeated_station,
    _insert_round_label,
    _prescan_best_durations,
    _reconstruct_from_label,
    search as bucketed_search,
)
from src.graph import RailwayGraph
from src.matcher import build_matcher
from src.models import SEARCH_PROFILES, SearchRequest, TrainSegment, route_key
from src.validation import build_search_request

CSV = "data/output/车次时刻表.csv"
JS = "data/timetable/station_name.js"

QUERIES = [
    ("exact-fast-京沪", {"from": "北京南", "to": "上海虹桥", "match_mode": "exact", "search_profile": "fast"}),
    ("exact-balanced-京沪", {"from": "北京南", "to": "上海虹桥", "match_mode": "exact", "search_profile": "balanced"}),
    ("fuzzy-balanced-京沪", {"from": "北京", "to": "上海", "match_mode": "fuzzy", "search_profile": "balanced"}),
    ("fuzzy-complete-延深", {"from": "延安", "to": "深圳北", "match_mode": "fuzzy", "search_profile": "complete", "timeout": "60"}),
    ("exact-balanced-哈尔滨", {"from": "哈尔滨", "to": "昆明", "match_mode": "fuzzy", "search_profile": "balanced"}),
    ("fuzzy-balanced-乌市", {"from": "乌鲁木齐", "to": "三亚", "match_mode": "fuzzy", "search_profile": "balanced"}),
    ("exact-complete-广州", {"from": "广州", "to": "哈尔滨", "match_mode": "fuzzy", "search_profile": "complete"}),
]


def full_scan_search(graph, request, matcher):
    """参考实现：与桶化版共享全部辅助逻辑，仅主循环改为每轮全量扫描。"""
    t_start = time.perf_counter()
    settings = SEARCH_PROFILES.get(request.search_profile, SEARCH_PROFILES["balanced"])
    timeout = min(request.timeout_seconds, settings.default_timeout_seconds)

    from src.matcher import resolve_station_set
    source_names = resolve_station_set(request.from_query, request.match_mode, graph, matcher)
    target_names = resolve_station_set(request.to_query, request.match_mode, graph, matcher)
    source_set = {graph.station_to_idx[n] for n in source_names if n in graph.station_to_idx}
    target_set = {graph.station_to_idx[n] for n in target_names if n in graph.station_to_idx}
    if not source_set or not target_set:
        from src.models import SearchMetadata, SearchResponse
        return SearchResponse(
            routes=(),
            metadata=SearchMetadata(profile=request.search_profile, complete=True),
            source_stations=tuple(source_names),
            target_stations=tuple(target_names),
        )

    constraint_city = None
    if request.transfer_city_code:
        try:
            from src.matcher import resolve_city_code
            constraint_city = resolve_city_code(request.transfer_city_code, graph, matcher)
        except ValueError:
            constraint_city = None

    targets_list = sorted(target_set)
    h_dist = graph.get_multi_source_distances(targets_list)
    h_time = graph.get_multi_source_times(targets_list)
    straight_dist = min((h_dist.get(s, 100) for s in source_set), default=100)
    detour_limit = straight_dist * MAX_DETOUR_RATIO
    prune_slack = settings.time_prune_slack

    n_stations = graph.station_count
    h_dist_arr = [h_dist.get(i, 0) for i in range(n_stations)]
    h_time_arr = [h_time.get(i, 0) for i in range(n_stations)]
    same_city_arr = [graph.same_city_of.get(i, ()) for i in range(n_stations)]
    target_flag = [1 if i in target_set else 0 for i in range(n_stations)]
    source_flag = [1 if i in source_set else 0 for i in range(n_stations)]

    best_durations = _prescan_best_durations(graph, request, source_set, target_set)

    conns = graph.sorted_connections
    start_idx = bisect.bisect_left(conns, (request.earliest_depart,))
    max_transfers = request.max_transfers
    rounds = max_transfers + 1
    state_limits = settings.max_states_per_station
    state_limit = settings.state_limit
    city_of = graph.station_to_city_code
    earliest_depart = request.earliest_depart
    latest_depart = request.latest_depart
    earliest_arrive = request.earliest_arrive
    latest_arrive = request.latest_arrive
    same_buffer = request.same_station_transfer_minutes
    foot_time = request.interstation_transfer_minutes

    round_labels = [dict() for _ in range(rounds)]
    dest_labels = [[] for _ in range(rounds)]
    scanned = 0
    generated = 0
    complete = True
    stopped_reason = None

    has_constraint = constraint_city is not None
    for r in range(rounds):
        cur = {}
        code_arr = {}
        fp_done = set()
        prev_round = round_labels[r - 1] if r > 0 else None
        is_first = (r == 0)

        if is_first:
            scan_start = start_idx
        else:
            min_prev_arr = min(
                (lb.arrive for lst in prev_round.values() for lb in lst), default=None)
            scan_start = start_idx if min_prev_arr is None else bisect.bisect_left(
                conns, (min_prev_arr + same_buffer,))

        for raw in conns[scan_start:]:
            scanned += 1
            if scanned % 50000 == 0:
                if time.perf_counter() - t_start > timeout:
                    stopped_reason = "timeout"
                    complete = False
                    break
                if generated > state_limit:
                    stopped_reason = "state_limit"
                    complete = False
                    break

            dep_m = raw[_CONN_DEP]
            code = raw[_CONN_CODE]
            f = raw[_CONN_F]
            t = raw[_CONN_T]
            arr_m = raw[_CONN_ARR]
            dist = raw[_CONN_DIST]

            if is_first and source_flag[f] and earliest_depart <= dep_m <= latest_depart:
                generated += 1
                rail = dist
                if rail + h_dist_arr[t] <= detour_limit:
                    cand = Label(
                        station=t, arrive=arr_m, train_code=code, first_dep=dep_m,
                        rail_distance=rail, train_xfers=0, inter_xfers=0,
                        inter_minutes=0, prev=None, conn=raw, seg_kind="train",
                        matched_constraint=False)
                    if _insert_round_label(cur, t, cand, state_limits, code_arr, has_constraint) is not None:
                        if same_city_arr[t]:
                            _expand_footpath(
                                cur, cand, graph, t, arr_m, fp_done, rail, 0, dep_m,
                                same_city_arr, foot_time, h_dist_arr, h_time_arr,
                                detour_limit, prune_slack, best_durations, target_flag,
                                constraint_city, city_of, state_limits,
                                code_arr, has_constraint, max_transfers)

            cl = cur.get(f)
            if cl:
                for lb in cl:
                    if lb.arrive > dep_m:
                        break
                    if lb.train_code != code:
                        continue
                    generated += 1
                    rail = lb.rail_distance + dist
                    if rail + h_dist_arr[t] > detour_limit:
                        continue
                    if prune_slack is not None and not target_flag[t]:
                        bd = best_durations[lb.train_xfers]
                        if bd is not None and (arr_m - lb.first_dep) + h_time_arr[t] > bd + prune_slack:
                            continue
                    cand = Label(
                        station=t, arrive=arr_m, train_code=code, first_dep=lb.first_dep,
                        rail_distance=rail, train_xfers=lb.train_xfers,
                        inter_xfers=lb.inter_xfers, inter_minutes=lb.inter_minutes,
                        prev=lb, conn=raw, seg_kind="train",
                        matched_constraint=lb.matched_constraint)
                    if _insert_round_label(cur, t, cand, state_limits, code_arr, has_constraint) is not None:
                        if same_city_arr[t]:
                            _expand_footpath(
                                cur, cand, graph, t, arr_m, fp_done, rail, lb.train_xfers,
                                lb.first_dep, same_city_arr, foot_time, h_dist_arr,
                                h_time_arr, detour_limit, prune_slack, best_durations,
                                target_flag, constraint_city, city_of, state_limits,
                                code_arr, has_constraint, max_transfers)

            if prev_round is not None:
                pl = prev_round.get(f)
                if pl:
                    for lb in pl:
                        if lb.arrive + same_buffer > dep_m:
                            break
                        if lb.train_xfers + lb.inter_xfers + 1 > max_transfers:
                            continue
                        if lb.train_code == code:
                            continue
                        generated += 1
                        rail = lb.rail_distance + dist
                        if rail + h_dist_arr[t] > detour_limit:
                            continue
                        matched = lb.matched_constraint
                        if constraint_city and not matched and city_of.get(f, "") == constraint_city:
                            matched = True
                        if prune_slack is not None and not target_flag[t]:
                            bd = best_durations[lb.train_xfers + 1]
                            if bd is not None and (arr_m - lb.first_dep) + h_time_arr[t] > bd + prune_slack:
                                continue
                        cand = Label(
                            station=t, arrive=arr_m, train_code=code,
                            first_dep=lb.first_dep, rail_distance=rail,
                            train_xfers=lb.train_xfers + 1,
                            inter_xfers=lb.inter_xfers,
                            inter_minutes=lb.inter_minutes,
                            prev=lb, conn=raw, seg_kind="train",
                            matched_constraint=matched)
                        if _insert_round_label(cur, t, cand, state_limits, code_arr, has_constraint) is not None:
                            if same_city_arr[t]:
                                _expand_footpath(
                                    cur, cand, graph, t, arr_m, fp_done, rail, lb.train_xfers + 1,
                                    lb.first_dep, same_city_arr, foot_time, h_dist_arr,
                                    h_time_arr, detour_limit, prune_slack, best_durations,
                                    target_flag, constraint_city, city_of, state_limits,
                                code_arr, has_constraint, max_transfers)

        for st, lst in cur.items():
            if target_flag[st]:
                for lb in lst:
                    if earliest_arrive <= lb.arrive <= latest_arrive:
                        dest_labels[r].append(lb)

        round_labels[r] = cur
        if not complete:
            break

    # 直达与主搜索同步：独立直达枚举（完整）+ 换乘（r>=1），与桶化版一致
    from src.csa import _collect_direct_routes
    direct_routes = _collect_direct_routes(graph, request, source_set, target_set)

    results = []
    seen_keys = set()
    for r in range(1, rounds):
        for lb in dest_labels[r]:
            if constraint_city and not lb.matched_constraint:
                continue
            route = _reconstruct_from_label(graph, lb)
            if not route or route.total_minutes <= 0:
                continue
            if route.rail_distance > straight_dist * MAX_DETOUR_RATIO:
                continue
            travel_minutes = sum(
                s.travel_minutes for s in route.segments if isinstance(s, TrainSegment))
            if (route.rail_distance > 0 and travel_minutes > 0 and
                    route.rail_distance / max(travel_minutes / 60.0, 0.01) < MIN_SPEED_KPH):
                continue
            if _has_repeated_station(route):
                continue
            key = route_key(route.segments)
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(route)

    all_routes = direct_routes + results
    all_routes.sort(key=lambda r: (r.train_transfers + r.interstation_transfers, r.total_minutes))
    max_results = settings.max_results
    if max_results is not None and len(all_routes) > max_results:
        direct_cnt = sum(1 for r in all_routes
                         if r.train_transfers == 0 and r.interstation_transfers == 0)
        all_routes = all_routes[:max(max_results, direct_cnt)]
    results = all_routes

    from src.models import SearchMetadata, SearchResponse
    return SearchResponse(
        routes=tuple(results),
        metadata=SearchMetadata(
            profile=request.search_profile,
            complete=complete,
            stopped_reason=stopped_reason,
            elapsed_ms=int((time.perf_counter() - t_start) * 1000),
            scanned_connections=scanned,
            generated_states=generated,
            returned_routes=len(results),
        ),
        source_stations=tuple(source_names),
        target_stations=tuple(target_names),
    )


def main():
    t0 = time.perf_counter()
    graph = RailwayGraph()
    graph.build(CSV, JS)
    matcher = build_matcher(graph, JS)
    print(f"build: {time.perf_counter()-t0:.2f}s")

    all_ok = True
    for name, params in QUERIES:
        request = build_search_request(params)
        t0 = time.perf_counter()
        full = full_scan_search(graph, request, matcher)
        t_full = time.perf_counter() - t0
        t0 = time.perf_counter()
        bkt = bucketed_search(graph, request, matcher)
        t_bkt = time.perf_counter() - t0

        full_keys = {route_key(r.segments) for r in full.routes}
        bkt_keys = {route_key(r.segments) for r in bkt.routes}
        miss = full_keys - bkt_keys
        extra = bkt_keys - full_keys
        ok = (not miss and not extra and full.metadata.complete == bkt.metadata.complete)
        all_ok &= ok
        status = "OK " if ok else "FAIL"
        print(f"[{status}] {name:26s} full={len(full.routes):4d}({t_full:5.2f}s,{full.metadata.complete}) "
              f"bkt={len(bkt.routes):4d}({t_bkt:5.2f}s,{bkt.metadata.complete}) "
              f"miss={len(miss)} extra={len(extra)}")
        for k in list(miss)[:3]:
            print(f"    missing: {k[:90]}")
        for k in list(extra)[:3]:
            print(f"    extra:   {k[:90]}")

    print("\n结论:", "全部一致 ✓" if all_ok else "存在差异 ✗")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
