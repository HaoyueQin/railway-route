"""
Connection Scan Algorithm — 统一多源/多目标搜索核心。

特性：
- 多源/多目标：exact 单站或 fuzzy 同城全部站共同参与搜索
- 类型化路径段：TrainSegment（铁路区段）与 InterstationTransferSegment（地面异站移动）
- 独立 footpath 松弛：到达站 A 后按用户配置时间生成同城站 B 的地面移动状态
- 城市级 transfer_at 约束
- 四档搜索模式（fast/balanced/thorough/complete）+ 超时与安全上限
- 完整段序列去重（区分不同上下车站、时刻和段类型）
- 搜索元数据（是否完整、中止原因、耗时、扫描/状态/结果计数）
"""

import bisect
import time as _time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from src.graph import RailwayGraph
from src.matcher import MatcherData, resolve_station_set, resolve_city_code
from src.models import (
    InterstationTransferSegment,
    RouteResult,
    SearchMetadata,
    SearchProfileSettings,
    SearchRequest,
    SearchResponse,
    SEARCH_PROFILES,
    TrainSegment,
    route_key,
)

# ── 过滤常量 ────────────────────────────────────────────

MAX_DETOUR_RATIO = 3.0
MIN_SPEED_KPH = 10.0


# ── 环形路线检测 ────────────────────────────────────────

def _has_repeated_station(route) -> bool:
    """检测路线中是否有重复出现的车站（环形/折返路线）。
    如果到达一个曾经到过的站，则是重复。"""
    visited: set[str] = set()
    for seg in route.segments:
        visited.add(seg.from_station)
        if seg.to_station in visited:
            return True
        visited.add(seg.to_station)
    return False


# ── Connection ──────────────────────────────────────────

@dataclass
class Connection:
    train_code: str
    from_station: int
    to_station: int
    depart_minutes: int
    arrive_minutes: int
    travel_minutes: int
    distance: int
    dist_cumulative: int
    seq: int


def _tuple_to_conn(t: tuple) -> Connection:
    return Connection(
        train_code=t[1],
        from_station=t[2],
        to_station=t[3],
        depart_minutes=t[0],
        arrive_minutes=t[4],
        travel_minutes=t[5],
        distance=t[6],
        dist_cumulative=t[7],
        seq=t[8],
    )


# ── 搜索状态 ────────────────────────────────────────────

@dataclass
class SearchState:
    station: int                          # 当前站索引
    time: int                             # 当前绝对分钟
    rail_distance: int                    # 累计铁路里程
    train_transfers: int                  # 列车换乘次数
    interstation_transfers: int = 0       # 异站移动次数
    interstation_minutes: int = 0         # 异站移动累计分钟
    train_code: str = ""                  # 当前车次
    first_departure: int = 0              # 首次发车绝对分钟
    prev_state: Optional["SearchState"] = None
    prev_conn: Optional[Connection] = None
    prev_segment_kind: str = ""           # "train" | "interstation" | ""
    matched_transfer_constraint: bool = False


# ── 支配（Pareto）────────────────────────────────────────

def _dominates_relaxed(a: SearchState, b: SearchState) -> bool:
    """宽松支配：用于中间站剪枝（非 complete 模式）。"""
    if a.time > b.time + 360:
        return False
    if a.rail_distance > b.rail_distance * 1.5:
        return False
    if a.train_transfers > b.train_transfers:
        return False
    if a.interstation_transfers > b.interstation_transfers:
        return False
    return (a.time + 180 < b.time or
            a.rail_distance < b.rail_distance * 0.7 or
            a.train_transfers < b.train_transfers or
            a.interstation_transfers < b.interstation_transfers)


def _dominates_strict(a: SearchState, b: SearchState) -> bool:
    """严格支配：complete 模式用于排除完全被占优的重复状态。"""
    return (a.time <= b.time and
            a.rail_distance <= b.rail_distance and
            a.train_transfers <= b.train_transfers and
            a.interstation_transfers <= b.interstation_transfers and
            (a.time < b.time or
             a.rail_distance < b.rail_distance or
             a.train_transfers < b.train_transfers or
             a.interstation_transfers < b.interstation_transfers))


def _add_pareto(
    pareto: dict[int, list[SearchState]],
    station: int,
    new_state: SearchState,
    settings: SearchProfileSettings,
) -> bool:
    existing = pareto.get(station, [])
    dominates = _dominates_relaxed if settings.use_relaxed_dominance else _dominates_strict

    for old in existing:
        if dominates(old, new_state):
            return False

    pareto[station] = [old for old in existing if not dominates(new_state, old)]
    pareto[station].append(new_state)

    max_states = settings.max_states_per_station
    if max_states is not None and len(pareto[station]) > max_states:
        pareto[station].sort(key=lambda s: (
            s.train_transfers + s.interstation_transfers, s.time))
        pareto[station] = pareto[station][:max_states]
    return True


# ── 回溯与段构建 ────────────────────────────────────────

def _reconstruct_typed(
    graph: RailwayGraph,
    state: SearchState,
) -> Optional[RouteResult]:
    """从 SearchState 链回溯为类型化 RouteResult。"""
    segs_raw: list = []  # (kind, Connection | (from, to, start, end, minutes, city_code))
    cur = state
    while cur and cur.prev_segment_kind:
        if cur.prev_segment_kind == "train" and cur.prev_conn:
            segs_raw.append(("train", cur.prev_conn))
        elif cur.prev_segment_kind == "interstation":
            # Interstation metadata stored in prev_state via a marker
            prev = cur.prev_state
            if prev is not None:
                from_idx = prev.station
                to_idx = cur.station
                city_code = graph.station_to_city_code.get(from_idx, "")
                city_name = graph.city_code_to_name.get(city_code, "")
                start_m = prev.time
                end_m = cur.time
                segs_raw.append(("interstation", (from_idx, to_idx, start_m, end_m,
                                                  cur.interstation_minutes - (prev.interstation_minutes if prev else 0),
                                                  city_code, city_name)))
        cur = cur.prev_state

    if not segs_raw:
        return None

    segs_raw.reverse()

    # 合并同车次连续铁路段
    merged_train: list[Connection] = []
    typed_segments: list = []

    for item in segs_raw:
        kind, data = item
        if kind == "train":
            merged_train.append(data)
        elif kind == "interstation":
            if merged_train:
                typed_segments.extend(_collapse_train_segments(graph, merged_train))
                merged_train = []
            from_idx, to_idx, start_m, end_m, xfer_m, ccode, cname = data
            typed_segments.append(InterstationTransferSegment(
                from_station=graph.idx_to_station[from_idx],
                to_station=graph.idx_to_station[to_idx],
                start_minutes=start_m,
                end_minutes=end_m,
                transfer_minutes=xfer_m,
                city_code=ccode,
                city_name=cname,
            ))
    if merged_train:
        typed_segments.extend(_collapse_train_segments(graph, merged_train))

    if not typed_segments:
        return None

    first_seg = typed_segments[0]
    last_seg = typed_segments[-1]

    if isinstance(first_seg, TrainSegment):
        actual_origin = first_seg.from_station
        first_dep = first_seg.depart_minutes
    else:
        actual_origin = first_seg.from_station
        first_dep = first_seg.start_minutes

    if isinstance(last_seg, TrainSegment):
        actual_dest = last_seg.to_station
        final_arr = last_seg.arrive_minutes
    else:
        actual_dest = last_seg.to_station
        final_arr = last_seg.end_minutes

    total_min = final_arr - first_dep
    if total_min < 0:
        total_min += 2880  # 最多跨两日

    rail_dist = sum(s.distance for s in typed_segments if isinstance(s, TrainSegment))
    train_xfer = state.train_transfers
    inter_xfer = state.interstation_transfers
    inter_min = state.interstation_minutes

    # 收集换乘城市
    xfer_cities: list[str] = []
    for i in range(1, len(typed_segments)):
        prev_seg = typed_segments[i - 1]
        curr_seg = typed_segments[i]
        if isinstance(prev_seg, TrainSegment) and isinstance(curr_seg, TrainSegment):
            if prev_seg.train_code != curr_seg.train_code:
                city_code = graph.station_to_city_code.get(
                    graph.station_to_idx.get(curr_seg.from_station, -1), "")
                if city_code:
                    city_name = graph.city_code_to_name.get(city_code, city_code)
                    if city_name not in xfer_cities:
                        xfer_cities.append(city_name)
        elif isinstance(prev_seg, InterstationTransferSegment):
            city_code = prev_seg.city_code
            city_name = graph.city_code_to_name.get(city_code, city_code)
            if city_name not in xfer_cities:
                xfer_cities.append(city_name)

    return RouteResult(
        segments=tuple(typed_segments),
        actual_origin=actual_origin,
        actual_destination=actual_dest,
        first_departure=first_dep,
        final_arrival=final_arr,
        total_minutes=total_min,
        rail_distance=rail_dist,
        train_transfers=train_xfer,
        interstation_transfers=inter_xfer,
        interstation_minutes=inter_min,
        transfer_cities=tuple(xfer_cities),
        matched_transfer_constraint=state.matched_transfer_constraint,
    )


def _collapse_train_segments(graph: RailwayGraph, conns: list[Connection]) -> list[TrainSegment]:
    """合并同车次连续 Connection 为单个 TrainSegment。"""
    if not conns:
        return []
    result: list[TrainSegment] = []
    current = conns[0]
    for conn in conns[1:]:
        if conn.train_code == current.train_code:
            current = Connection(
                current.train_code, current.from_station, conn.to_station,
                current.depart_minutes, conn.arrive_minutes,
                conn.arrive_minutes - current.depart_minutes,
                current.distance + conn.distance,
                conn.dist_cumulative, current.seq,
            )
        else:
            result.append(TrainSegment(
                train_code=current.train_code,
                from_station=graph.idx_to_station[current.from_station],
                to_station=graph.idx_to_station[current.to_station],
                depart_minutes=current.depart_minutes,
                arrive_minutes=current.arrive_minutes,
                travel_minutes=current.travel_minutes,
                distance=current.distance,
            ))
            current = conn
    result.append(TrainSegment(
        train_code=current.train_code,
        from_station=graph.idx_to_station[current.from_station],
        to_station=graph.idx_to_station[current.to_station],
        depart_minutes=current.depart_minutes,
        arrive_minutes=current.arrive_minutes,
        travel_minutes=current.travel_minutes,
        distance=current.distance,
    ))
    return result


# ── 核心搜索 ────────────────────────────────────────────

def search(
    graph: RailwayGraph,
    request: SearchRequest,
    matcher: MatcherData,
) -> SearchResponse:
    """统一多源/多目标 CSA 搜索入口。"""
    t_start = _time.perf_counter()
    settings = SEARCH_PROFILES.get(request.search_profile, SEARCH_PROFILES["balanced"])
    timeout = min(request.timeout_seconds, settings.default_timeout_seconds)

    # ── 解析起终点集合 ──
    source_names = resolve_station_set(request.from_query, request.match_mode, graph, matcher)
    target_names = resolve_station_set(request.to_query, request.match_mode, graph, matcher)
    source_set = {graph.station_to_idx[n] for n in source_names if n in graph.station_to_idx}
    target_set = {graph.station_to_idx[n] for n in target_names if n in graph.station_to_idx}
    if not source_set or not target_set:
        return SearchResponse(
            routes=(),
            metadata=SearchMetadata(profile=request.search_profile, complete=True),
            source_stations=tuple(source_names),
            target_stations=tuple(target_names),
        )

    # ── 解析 transfer_at 城市约束 ──
    constraint_city: Optional[str] = None
    if request.transfer_city_code:
        try:
            constraint_city = resolve_city_code(request.transfer_city_code, graph, matcher)
        except ValueError:
            constraint_city = None

    # ── 反向距离（绕路过滤用） ──
    ref_target = next(iter(target_set))
    h_dist = graph.get_reverse_distances(ref_target)
    straight_dist = min((h_dist.get(s, 100) for s in source_set), default=100)

    # ── 初始化 ──
    raw_conns = graph.sorted_connections
    start_idx = bisect.bisect_left(raw_conns, (request.earliest_depart,))

    pareto: dict[int, list[SearchState]] = defaultdict(list)
    for s in source_set:
        init = SearchState(station=s, time=-1, rail_distance=0, train_transfers=-1)
        pareto[s].append(init)
    dest_states: list[SearchState] = []

    scanned = 0
    generated = 0
    state_limit = settings.state_limit
    stopped_reason: Optional[str] = None
    complete = True

    # ── 主扫描 ──
    for raw in raw_conns[start_idx:]:
        scanned += 1
        if scanned % 50000 == 0:
            elapsed = _time.perf_counter() - t_start
            if elapsed > timeout:
                stopped_reason = "timeout"
                complete = False
                break
            if generated > state_limit:
                stopped_reason = "state_limit"
                complete = False
                break

        conn = _tuple_to_conn(raw)
        f, t = conn.from_station, conn.to_station

        for state in pareto.get(f, []):
            # ── 登车判断 ──
            if state.train_transfers == -1:
                # 初始状态
                if not (request.earliest_depart <= conn.depart_minutes <= request.latest_depart):
                    continue
                new_tr, first_dep = 0, conn.depart_minutes
                new_inter_tr, new_inter_min = 0, 0
                matched = False
            elif state.train_code == conn.train_code:
                # 同车续乘
                if state.time > conn.depart_minutes:
                    continue
                new_tr = state.train_transfers
                first_dep = state.first_departure
                new_inter_tr = state.interstation_transfers
                new_inter_min = state.interstation_minutes
                matched = state.matched_transfer_constraint
            else:
                # 换乘新车
                if state.train_transfers + 1 > request.max_transfers:
                    continue
                needed = request.same_station_transfer_minutes
                if state.time + needed > conn.depart_minutes:
                    continue
                new_tr = state.train_transfers + 1
                first_dep = state.first_departure
                new_inter_tr = state.interstation_transfers
                new_inter_min = state.interstation_minutes
                # 检查是否在约束城市换乘
                matched = state.matched_transfer_constraint
                if constraint_city and not matched:
                    city_of_f = graph.station_to_city_code.get(f, "")
                    if city_of_f == constraint_city:
                        matched = True

            new_state = SearchState(
                station=t,
                time=conn.arrive_minutes,
                rail_distance=state.rail_distance + conn.distance,
                train_transfers=new_tr,
                interstation_transfers=new_inter_tr,
                interstation_minutes=new_inter_min,
                train_code=conn.train_code,
                first_departure=first_dep,
                prev_state=state,
                prev_conn=conn,
                prev_segment_kind="train",
                matched_transfer_constraint=matched,
            )
            generated += 1

            if t in target_set:
                if request.earliest_arrive <= conn.arrive_minutes <= request.latest_arrive:
                    dest_states.append(new_state)
            else:
                _add_pareto(pareto, t, new_state, settings)

            # ── footpath 松弛 ──
            same_city = graph.same_city_of.get(t, [])
            if same_city and state.prev_segment_kind != "interstation":
                for other in same_city:
                    if other == t:
                        continue
                    foot_time = request.interstation_transfer_minutes
                    fp_arrive = conn.arrive_minutes + foot_time
                    fp_state = SearchState(
                        station=other,
                        time=fp_arrive,
                        rail_distance=new_state.rail_distance,
                        train_transfers=new_state.train_transfers,
                        interstation_transfers=new_state.interstation_transfers + 1,
                        interstation_minutes=new_state.interstation_minutes + foot_time,
                        train_code="",
                        first_departure=new_state.first_departure,
                        prev_state=new_state,
                        prev_conn=None,
                        prev_segment_kind="interstation",
                        matched_transfer_constraint=(
                            new_state.matched_transfer_constraint or (
                                constraint_city is not None and
                                graph.station_to_city_code.get(t, "") == constraint_city
                            )
                        ),
                    )
                    generated += 1
                    # footpath 仅作中间换乘，不作为最终到站（末段必须是火车）
                    _add_pareto(pareto, other, fp_state, settings)

    # ── 回溯、过滤、去重 ──
    results: list[RouteResult] = []
    seen_keys: set = set()

    for state in dest_states:
        if constraint_city and not state.matched_transfer_constraint:
            continue
        route = _reconstruct_typed(graph, state)
        if not route or route.total_minutes <= 0:
            continue
        if route.rail_distance > straight_dist * MAX_DETOUR_RATIO:
            continue
        if route.rail_distance > 0 and route.rail_distance / max(route.total_minutes / 60.0, 0.01) < MIN_SPEED_KPH:
            continue
        # 过滤环形路线：同一车站（除起终点外）不得重复出现
        if _has_repeated_station(route):
            continue
        key = route_key(route.segments)
        if key not in seen_keys:
            seen_keys.add(key)
            results.append(route)

    results.sort(key=lambda r: (r.train_transfers + r.interstation_transfers, r.total_minutes))

    max_results = settings.max_results
    if max_results is not None:
        results = results[:max_results]

    elapsed_ms = int((_time.perf_counter() - t_start) * 1000)

    return SearchResponse(
        routes=tuple(results),
        metadata=SearchMetadata(
            profile=request.search_profile,
            complete=complete,
            stopped_reason=stopped_reason,
            elapsed_ms=elapsed_ms,
            scanned_connections=scanned,
            generated_states=generated,
            returned_routes=len(results),
        ),
        source_stations=tuple(source_names),
        target_stations=tuple(target_names),
    )
