"""
轮次化多标签 CSA — 统一多源/多目标搜索核心。

架构（RAPTOR 风格轮次化，解决中间站支配误杀长路线问题）：
- 第 r 轮只处理"r 次列车换乘"的旅程：换乘仅发生在轮次之间，同轮内只做
  同车次续乘传播。不同换乘次数的状态互不支配，跨夜/慢速续乘状态不会被
  "更早到达"的其他车次状态误杀，长路线（跨越多枢纽）不再断链。
- 每轮每站保留"最早到达的若干标签"（Pareto：到达时间/里程/地面次数），
  标签带前驱链，用于回溯构建类型化路线。
- 目标站标签同时作为中间站继续传播（多目标 fuzzy 场景可经一个目标站
  到达另一目标站）。

特性：
- 多源/多目标：exact 单站或 fuzzy 同城全部站共同参与搜索
- 类型化路径段：TrainSegment（铁路区段）与 InterstationTransferSegment（地面异站移动）
- 独立 footpath 松弛：到达站 A 后按用户配置时间生成同城站 B 的地面移动标签
- 城市级 transfer_at 约束
- 四档搜索模式（fast/balanced/thorough/complete）+ 超时与安全上限
- 目标导向剪枝：绕路距离下界 + 按换乘级别的最短总耗时下界（预扫描锁定）
- 完整段序列去重（区分不同上下车站、时刻和段类型）
- 搜索元数据（是否完整、中止原因、耗时、扫描/标签计数）
"""

import bisect
import heapq
import time as _time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from pyref.graph import RailwayGraph
from pyref.matcher import MatcherData, resolve_station_set, resolve_city_code, resolve_single


def _resolve_multi(stations: list[str], graph, matcher: MatcherData) -> list[str]:
    """多站精确解析：每站 resolve_single（精确单站），保序去重取并集。"""
    names: list[str] = []
    for s in stations:
        name = resolve_single(s, graph, matcher)
        if name not in names:
            names.append(name)
    return names
from pyref.models import (
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
# transfer_at 约束时放宽：指定换乘城市必然引入绕路（约束城市不在最短路径上），
# 固定 3 倍绕路比会误杀"必须经郑州/武汉"等贴近生活的真实需求
MAX_DETOUR_RATIO_CONSTRAINED = 5.0
CONSTRAINED_SLACK_PENALTY = 300  # 约束时耗时剪枝 slack 追加分钟数


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


# ── 连接字段序 ──────────────────────────────────────────
# 连接以原始 tuple 传递（graph.build_connections_cache 产出），字段序：
# (depart_minutes, train_code, from_idx, to_idx, arrive_minutes,
#  travel_minutes, distance, dist_cumulative, seq)
_CONN_DEP, _CONN_CODE, _CONN_F, _CONN_T, _CONN_ARR, _CONN_TRAVEL, _CONN_DIST, _CONN_CUM, _CONN_SEQ = range(9)


# ── 轮次标签（RAPTOR 风格多标签 CSA）──────────────────────

@dataclass(slots=True)
class Label:
    """一轮搜索中"到达某站"的一个标签。

    轮次化的核心：第 r 轮只处理"r 次换乘"的旅程，换乘只发生在轮次之间，
    同轮内仅同车次续乘传播。因此不同换乘次数的状态互不支配——跨夜/慢速
    续乘状态不会被"更早到达"的其他车次状态误杀，长路线在枢纽站不断链。
    """
    station: int                       # 当前站索引
    arrive: int                        # 到达绝对分钟
    train_code: str                    # 当前车次（footpath 标签为空串）
    first_dep: int                     # 首次发车绝对分钟
    rail_distance: int                 # 累计铁路里程
    train_xfers: int                   # 列车换乘次数（= 轮次 r）
    inter_xfers: int                   # 异站地面移动次数
    inter_minutes: int                 # 异站移动累计分钟
    prev: Optional["Label"] = None     # 父标签（回溯链）
    conn: Optional[tuple] = None       # 本跳连接 tuple（footpath 为 None）
    seg_kind: str = "train"            # "train" | "interstation"
    matched_constraint: bool = False


def _insert_round_label(
    cur: dict[int, list[Label]],
    station: int,
    cand: Label,
    state_limits: Optional[int],
    code_arr: dict,
    has_constraint: bool,
) -> bool:
    """轮内标签插入（主循环与 footpath 共用）。

    关键设计：轮内**不做跨车次支配**——同一车站的不同车次到达时间不同，
    但后续可达性也不同（车次终点/走向不同），跨车次支配会误杀"稍晚到达
    但能到达目标"的车次（如京沪 G1 被终点为青岛的早班车支配而断链）。
    - 同车次（同一天窗口内）只保留最早到达标签：code_arr 索引 O(1) 判定
      （每轮每站 {车次: 最早到达}），替代线性扫描——标签列表大时线性扫
      是主热点（cProfile：_insert_round_label 占 complete 查询 ~54%）；
    - 跨车次共存，按到达时间排序，截断时优先保留不同车次（多样性）；
    - 无换乘城市约束的查询跳过 matched 扫描（短路，绝大多数查询无约束）。
    """
    lst = cur.get(station)
    seen_map = code_arr.get(station)
    if lst is None:
        cur[station] = [cand]
        if seen_map is None:
            code_arr[station] = {cand.train_code: cand.arrive}
        else:
            seen_map[cand.train_code] = cand.arrive
        return cand
    if seen_map is None:
        seen_map = code_arr[station] = {}
    prev_arr = seen_map.get(cand.train_code)
    if prev_arr is not None and abs(prev_arr - cand.arrive) < 1440:
        # 同班次重复：保留更早到达
        if prev_arr <= cand.arrive:
            return None
        # 旧标签被更新：arrive 已知，二分定位同 arrive 段再找同 code 删除
        lo, hi = 0, len(lst)
        while lo < hi:
            mid = (lo + hi) // 2
            if lst[mid].arrive < prev_arr:
                lo = mid + 1
            else:
                hi = mid
        i = lo
        while i < len(lst) and lst[i].arrive == prev_arr:
            if lst[i].train_code == cand.train_code:
                del lst[i]
                break
            i += 1
        seen_map[cand.train_code] = cand.arrive
    elif prev_arr is None:
        seen_map[cand.train_code] = cand.arrive
    # 二分插入：保持 arrive 升序；同 arrive 段内按车次号稳定排序——
    # 消除扫描顺序敏感性（堆序 vs 全量序），否则同刻标签的先后依赖
    # 处理顺序，截断时保留集随之分歧（A/B 验证曾出现 full≠bkt）
    lo, hi = 0, len(lst)
    while lo < hi:
        mid = (lo + hi) // 2
        if (lst[mid].arrive < cand.arrive or
                (lst[mid].arrive == cand.arrive and lst[mid].train_code < cand.train_code)):
            lo = mid + 1
        else:
            hi = mid
    lst.insert(lo, cand)
    if state_limits is not None and len(lst) > state_limits:
        # 仅超限时做车次去重截断：列表按到达升序，同车次近似相邻，
        # 线性去重保留每个车次最早到达，再按到达时间截断
        dedup: list[Label] = []
        prev_code: Optional[str] = None
        for lb in lst:
            if lb.train_code != prev_code:
                dedup.append(lb)
                prev_code = lb.train_code
        if len(dedup) > state_limits:
            # 约束匹配标签优先保留：指定换乘城市的路线往往较慢
            # （如经郑州 vs 京沪快线），不能被快速路线挤掉截断。
            if not has_constraint or not any(lb.matched_constraint for lb in dedup):
                del dedup[state_limits:]
            else:
                matched_lb = [lb for lb in dedup if lb.matched_constraint]
                if len(matched_lb) >= state_limits:
                    dedup = matched_lb[:state_limits]
                else:
                    dedup = matched_lb + [lb for lb in dedup if not lb.matched_constraint]
                    del dedup[state_limits:]
        lst[:] = dedup
        # 截断删除了标签：重建 code_arr 索引保持同步，否则被删车次的
        # 后续同班次标签会被"索引仍在"误拒（且 full/bkt 截断时机不同会造成分歧）
        code_arr[station] = {lb.train_code: lb.arrive for lb in lst}
    return cand


# ── 回溯与段构建 ────────────────────────────────────────

def _reconstruct_from_label(
    graph: RailwayGraph,
    label: Label,
) -> Optional[RouteResult]:
    """从 Label 链回溯为类型化 RouteResult。"""
    segs_raw: list = []  # (kind, conn_tuple | (from, to, start, end, minutes, city_code, city_name))
    cur = label
    while cur is not None:
        if cur.seg_kind == "train" and cur.conn is not None:
            segs_raw.append(("train", cur.conn))
        elif cur.seg_kind == "interstation":
            prev = cur.prev
            if prev is not None:
                from_idx = prev.station
                to_idx = cur.station
                city_code = graph.station_to_city_code.get(from_idx, "")
                city_name = graph.city_code_to_name.get(city_code, "")
                start_m = prev.arrive
                end_m = cur.arrive
                segs_raw.append(("interstation", (
                    from_idx, to_idx, start_m, end_m,
                    cur.inter_minutes - prev.inter_minutes, city_code, city_name)))
        cur = cur.prev

    if not segs_raw:
        return None

    segs_raw.reverse()

    merged_train: list = []
    typed_segments: list = []
    for item in segs_raw:
        kind, data = item
        if kind == "train":
            merged_train.append(data)
        else:
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
        total_min += 2880

    rail_dist = sum(s.distance for s in typed_segments if isinstance(s, TrainSegment))

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
        train_transfers=label.train_xfers,
        interstation_transfers=label.inter_xfers,
        interstation_minutes=label.inter_minutes,
        transfer_cities=tuple(xfer_cities),
        matched_transfer_constraint=label.matched_constraint,
    )


def _collapse_train_segments(graph: RailwayGraph, conns: list) -> list[TrainSegment]:
    """合并同车次连续连接 tuple 为单个 TrainSegment。"""
    if not conns:
        return []
    result: list[TrainSegment] = []
    current = conns[0]
    for conn in conns[1:]:
        if conn[_CONN_CODE] == current[_CONN_CODE]:
            current = (
                current[_CONN_DEP], current[_CONN_CODE], current[_CONN_F], conn[_CONN_T],
                conn[_CONN_ARR], conn[_CONN_ARR] - current[_CONN_DEP],
                current[_CONN_DIST] + conn[_CONN_DIST], conn[_CONN_CUM], current[_CONN_SEQ],
            )
        else:
            result.append(TrainSegment(
                train_code=current[_CONN_CODE],
                from_station=graph.idx_to_station[current[_CONN_F]],
                to_station=graph.idx_to_station[current[_CONN_T]],
                depart_minutes=current[_CONN_DEP],
                arrive_minutes=current[_CONN_ARR],
                travel_minutes=current[_CONN_TRAVEL],
                distance=current[_CONN_DIST],
            ))
            current = conn
    result.append(TrainSegment(
        train_code=current[_CONN_CODE],
        from_station=graph.idx_to_station[current[_CONN_F]],
        to_station=graph.idx_to_station[current[_CONN_T]],
        depart_minutes=current[_CONN_DEP],
        arrive_minutes=current[_CONN_ARR],
        travel_minutes=current[_CONN_TRAVEL],
        distance=current[_CONN_DIST],
    ))
    return result


# ── 独立直达枚举（直达永远完整）──────────────────────

def _collect_direct_routes(
    graph: RailwayGraph,
    request: SearchRequest,
    source_set: set[int],
    target_set: set[int],
) -> list[RouteResult]:
    """直达方案独立枚举：不受 CSA 标签截断 / 绕路 / 耗时剪枝影响。

    图只建相邻停站边，直达 = 同一车次连续乘坐（同车续乘链）。对源站的
    每条上车连接，沿 train_stops 全程停站表向后扫描：命中目标站即构成
    直达。时刻换算：train_stops 为发车日坐标系（跨夜链式 +1440），
    conn 为段内坐标系，day = (conn_dep - stops_dep) // 1440（可为负，
    表示"今天的班次 = 发车日的 +1/-1 天"），到达绝对时刻 = arr + day*1440。
    """
    out_conns = graph.out_conns
    train_stops = graph.train_stops
    idx_to_station = graph.idx_to_station
    routes: list[RouteResult] = []
    seen: set = set()   # (code, s, t)：双日连接只保留最早班次
    e_dep, l_dep = request.earliest_depart, request.latest_depart
    e_arr, l_arr = request.earliest_arrive, request.latest_arrive

    for s in source_set:
        for conn in out_conns[s]:
            dep_m = conn[_CONN_DEP]
            if not (e_dep <= dep_m <= l_dep):
                continue
            code = conn[_CONN_CODE]
            stops = train_stops.get(code)
            if not stops or stops[-1][4] == 0:
                continue  # 全程里程为 0 的车次（数据无效）：不参与直达枚举
            seq_f = conn[_CONN_SEQ]
            pos = next((k for k, st in enumerate(stops)
                        if st[0] == s and st[3] == seq_f), None)
            if pos is None or stops[pos][1] == -1:
                continue
            day = (dep_m - stops[pos][1]) // 1440
            base_cum = stops[pos][4]
            for st in stops[pos + 1:]:
                st_idx, _, arr2, _, cum = st
                if arr2 == -1 or st_idx not in target_set:
                    continue
                if (code, s, st_idx) in seen:
                    continue
                arr_abs = arr2 + day * 1440
                if not (e_arr <= arr_abs <= l_arr):
                    continue
                seen.add((code, s, st_idx))
                seg = TrainSegment(
                    train_code=code,
                    from_station=idx_to_station[s],
                    to_station=idx_to_station[st_idx],
                    depart_minutes=dep_m,
                    arrive_minutes=arr_abs,
                    travel_minutes=arr_abs - dep_m,
                    distance=max(0, cum - base_cum),
                )
                routes.append(RouteResult(
                    segments=(seg,),
                    actual_origin=idx_to_station[s],
                    actual_destination=idx_to_station[st_idx],
                    first_departure=dep_m,
                    final_arrival=arr_abs,
                    total_minutes=arr_abs - dep_m,
                    rail_distance=max(0, cum - base_cum),
                    train_transfers=0,
                    interstation_transfers=0,
                    transfer_cities=(),
                ))
    return routes


# ── 预扫描：每换乘级别最短总耗时 ─────────────────────────

def _prescan_best_durations(
    graph: RailwayGraph,
    request: SearchRequest,
    source_set: set[int],
    target_set: set[int],
) -> list[Optional[int]]:
    """多标签 CSA（每站每换乘级别保留最早到达）的"每换乘级别最短总耗时"。

    返回 best[k]（k=0..max_transfers）：k 次换乘内可达的最短总耗时（分钟），
    None 表示该转数级别无可行路线。用于主搜索的耗时剪枝基准：
    剪枝按状态的当前转数匹配对应级别，避免"少换乘但稍慢"的合理路线
    （如 1 转跨夜 vs 3 转稍快）被"最快路线"基准误杀。

    关键：**单标签"最早到达支配"会丢失直达基准**——footpath 标签（晚到但
    转数多）会支配"同车次续乘链"（早登车 0 转），导致 G1 北京南→上海虹桥
    的 0 转标签在沧州西被 footpath 标签支配而断链，best_by[0] 永久缺失，
    主循环第 0 轮失去耗时剪枝而状态爆炸。因此每站按换乘级别各保留一个
    最早到达标签（每站 ≤ max_transfers+1 个，状态量可控）。
    包含同站换乘缓冲、max_transfers 约束与同城 footpath 松弛（与主搜索一致）。
    采用按站分桶 + 堆归并迭代：只处理"有标签的站"的出发连接。
    """
    out_conns = graph.out_conns
    # 标签: station -> {xfers: (arrive_minutes, train_code, first_departure)}
    # source 站含伪标签 0:(-1,...)（s_time==-1 表示初始登车）
    tags: dict[int, dict[int, tuple[int, str, int]]] = {
        s: {0: (-1, "", -1)} for s in source_set}
    earliest_depart = request.earliest_depart
    latest_depart = request.latest_depart
    earliest_arrive = request.earliest_arrive
    latest_arrive = request.latest_arrive
    same_buffer = request.same_station_transfer_minutes
    max_transfers = request.max_transfers
    foot_time = request.interstation_transfer_minutes
    same_city_of = graph.same_city_of
    best_by: list[Optional[int]] = [None] * (max_transfers + 1)

    def _update(t: int, arr_m: int, code: str, first_dep: int, new_xfers: int) -> bool:
        """标签更新（每换乘级别最早到达优先）；到达目标站时刷新该级别最短耗时。
        返回是否更新成功（仅更新成功时才值得做 footpath 扩散）。"""
        level = tags.get(t)
        if level is None:
            level = {}
            tags[t] = level
        old = level.get(new_xfers)
        if old is not None and arr_m >= old[0]:
            return False
        level[new_xfers] = (arr_m, code, first_dep)
        # 该站全局最早到达（迭代器起点/回退依据）
        min_arr = min(v[0] for v in level.values() if v[0] >= 0)
        _sync_heap(heap, pos_of, earliest_of, out_conns, t, min_arr)
        if t in target_set and earliest_arrive <= arr_m <= latest_arrive:
            duration = arr_m - first_dep
            if best_by[new_xfers] is None or duration < best_by[new_xfers]:
                best_by[new_xfers] = duration
        return True

    # 堆归并：每站一个迭代器（起始 = 该站最早可登车时刻），全局按 dep 升序弹出
    heap: list[tuple[int, int, int]] = []
    pos_of: dict[int, int] = {}
    earliest_of: dict[int, int] = {}
    for s in source_set:
        pos = bisect.bisect_left(out_conns[s], (earliest_depart,))
        if pos < len(out_conns[s]):
            heap.append((out_conns[s][pos][0], s, pos))
            pos_of[s] = pos
            earliest_of[s] = -1  # 伪标签：后续真实标签不会更早，无需回退
    heapq.heapify(heap)

    while heap:
        _, f, pos = heapq.heappop(heap)
        if pos_of.get(f) != pos:
            continue  # 过期条目：迭代器已回退，由新条目接管
        raw = out_conns[f][pos]
        dep_m = raw[_CONN_DEP]
        code = raw[_CONN_CODE]
        t = raw[_CONN_T]
        arr_m = raw[_CONN_ARR]
        tag = tags.get(f)
        nxt = pos + 1
        if nxt < len(out_conns[f]):
            pos_of[f] = nxt
            heapq.heappush(heap, (out_conns[f][nxt][0], f, nxt))
        if tag is None:
            continue

        pseudo = tag.get(0)
        if pseudo is not None and pseudo[0] == -1:
            # 初始登车（仅 source 站，且该站尚未有真实 0 转标签）
            if not (earliest_depart <= dep_m <= latest_depart):
                continue
            first_dep, new_xfers = dep_m, 0
        else:
            # 同车次续乘：该车次各转数级别中不晚于 dep_m 的最早标签
            cont = None
            for s_xfers, (s_time, s_code, s_first) in tag.items():
                if s_code == code and s_time != -1 and s_time <= dep_m:
                    if cont is None or s_time < cont[0]:
                        cont = (s_time, s_first, s_xfers)
            if cont is not None:
                first_dep, new_xfers = cont[1], cont[2]
            else:
                # 换乘：遍历各转数级别（各自缓冲 + 转数上限）
                for s_xfers, (s_time, s_code, s_first) in tag.items():
                    if s_time == -1:
                        continue
                    if s_xfers + 1 > max_transfers:
                        continue
                    if s_time + same_buffer > dep_m:
                        continue
                    _update(t, arr_m, code, s_first, s_xfers + 1)
                continue

        # footpath 松弛：仅当列车到达更新了 t 的标签时，才向同城 other 扩散
        # （footpath 标签恒晚于同次到达，重复到达不更新标签则扩散必被覆盖拒绝）。
        if _update(t, arr_m, code, first_dep, new_xfers):
            partners = same_city_of.get(t)
            if partners:
                fp_arr = arr_m + foot_time
                for other in partners:
                    if other != t:
                        _update(other, fp_arr, "", first_dep, new_xfers)

    # 前缀最小化：best[k] = k 转"以内"的最短耗时
    cur = None
    for k in range(max_transfers + 1):
        if best_by[k] is not None and (cur is None or best_by[k] < cur):
            cur = best_by[k]
        best_by[k] = cur
    return best_by


# ── 内联辅助（剪枝/收集/footpath）────────────────────────

def _sync_heap(
    heap: list,
    pos_of: dict,
    earliest_of: dict,
    out_conns: list,
    station: int,
    min_arr: int,
) -> None:
    """确保站 station 的迭代器覆盖 [bisect(min_arr), ...) 的出发连接。

    关键：标签到达时间**非单调**——footpath 标签（晚）可能先插入，
    之后列车标签（更早）覆盖。更早标签的可登车连接（dep 在首标签
    bisect 之前）会被跳过，需要回退迭代器起点。

    回退触发条件 = **标签真正变早**（min_arr < 已记录的最早到达），
    不能按"位置比较"（迭代器推进后位置恒大于首标签位置，会导致
    每条连接处理完都回退 → 反复重处理同一条连接 → 乒乓死循环）。
    回退区间 = 变早幅度对应的连接数，有限；旧堆条目通过 pos_of
    比对标记过期，pop 时丢弃（不推进），由新条目接管。
    """
    old_earliest = earliest_of.get(station)
    if old_earliest is not None and min_arr >= old_earliest:
        return  # 未变早：迭代器起点已覆盖，无需任何操作
    earliest_of[station] = min_arr
    tp = bisect.bisect_left(out_conns[station], (min_arr,))
    if tp >= len(out_conns[station]):
        return
    old = pos_of.get(station)
    if old is None:
        pos_of[station] = tp
        heapq.heappush(heap, (out_conns[station][tp][0], station, tp))
    elif tp < old:
        pos_of[station] = tp
        heapq.heappush(heap, (out_conns[station][tp][0], station, tp))


def _enqueue_fp_targets(heap: list, pos_of: dict, earliest_of: dict, out_conns: list,
                          cur: dict, inserted: list) -> None:
    """footpath 标签插入成功的站同步迭代器（可能回退起点）。"""
    for o, fp_arr in inserted:
        lst_o = cur.get(o)
        if lst_o:
            _sync_heap(heap, pos_of, earliest_of, out_conns, o, lst_o[0].arrive)


def _prune_by_duration(
    t: int,
    arr_m: int,
    first_dep: int,
    xfers: int,
    target_flag: list[int],
    prune_slack: Optional[int],
    best_durations: list[Optional[int]],
    h_time_arr: list[float],
) -> bool:
    """目标导向耗时剪枝：状态的换乘级别基准 = 该级别内已知最短总耗时；
    已耗时 + 到目标最快剩余时间 超过 基准+slack 的中间状态，
    其扩展路线在同换乘级别内必然显著慢于已知最优（终点状态豁免）。"""
    if prune_slack is None or target_flag[t]:
        return False
    bd = best_durations[xfers] if best_durations else None
    if bd is None:
        return False
    return (arr_m - first_dep) + h_time_arr[t] > bd + prune_slack


def _expand_footpath(
    cur: dict[int, list[Label]],
    cand: Label,
    graph: RailwayGraph,
    t: int,
    arr_m: int,
    fp_done: set,
    rail: int,
    xfers: int,
    first_dep: int,
    same_city_arr: list,
    foot_time: int,
    h_dist_arr: list[float],
    h_time_arr: list[float],
    detour_limit: float,
    prune_slack: Optional[int],
    best_durations: list[Optional[int]],
    target_flag: list[int],
    constraint_city: Optional[str],
    city_of: dict,
    state_limits: Optional[int],
    code_arr: dict,
    has_constraint: bool,
    max_transfers: int,
) -> list:
    """同城 footpath 松弛：列车到达站 t 后向同城 other 生成地面移动标签。

    仅从列车段标签扩散（cand.seg_kind == "train"），链式 footpath 不会形成；
    footpath 标签加入本轮标签集，作为下一轮的换乘来源（登车计一次列车换乘）。
    返回插入成功的 (other, fp_arr) 列表——调用方须将 these 站加入堆
    （桶化迭代下，未入堆的站其出发连接不会被处理，footpath 换乘会丢失）。
    """
    if cand.seg_kind != "train":
        return []
    if xfers + cand.inter_xfers + 1 > max_transfers:
        return []   # 地面换乘计入总换乘上限（乘客视角：出站换乘也是换乘）
    partners = same_city_arr[t]
    if not partners:
        return []
    # 同一 (站, 到达时刻) 的多个标签只扩散一次（footpath 标签仅依赖到达时刻）
    if (t, arr_m) in fp_done:
        return []
    fp_done.add((t, arr_m))
    inserted: list = []
    for other in partners:
        if other == t:
            continue
        fp_arr = arr_m + foot_time
        if rail + h_dist_arr[other] > detour_limit:
            continue
        if _prune_by_duration(other, fp_arr, first_dep=first_dep, xfers=xfers,
                              target_flag=target_flag, prune_slack=prune_slack,
                              best_durations=best_durations, h_time_arr=h_time_arr):
            continue
        # 前置同码检查：other 已有不晚于 fp_arr 的 footpath 标签则跳过
        # （footpath 标签同码 ""，绝大多数重复尝试会被此检查拦截，省去插入开销）
        lst_o = cur.get(other)
        if lst_o:
            skip = False
            for lb in lst_o:
                if lb.train_code == "" and lb.arrive <= fp_arr:
                    skip = True
                    break
            if skip:
                continue
        fp = Label(
            station=other, arrive=fp_arr, train_code="", first_dep=first_dep,
            rail_distance=rail, train_xfers=xfers, inter_xfers=cand.inter_xfers + 1,
            inter_minutes=cand.inter_minutes + foot_time,
            prev=cand, conn=None, seg_kind="interstation",
            matched_constraint=cand.matched_constraint or (
                constraint_city is not None and city_of.get(t, "") == constraint_city))
        if _insert_round_label(cur, other, fp, state_limits, code_arr, has_constraint) is not None:
            inserted.append((other, fp_arr))
    return inserted


# ── 核心搜索（轮次化多标签 CSA）──────────────────────────

def search(
    graph: RailwayGraph,
    request: SearchRequest,
    matcher: MatcherData,
) -> SearchResponse:
    """统一多源/多目标轮次化 CSA 搜索入口。"""
    t_start = _time.perf_counter()
    settings = SEARCH_PROFILES.get(request.search_profile, SEARCH_PROFILES["balanced"])
    timeout = min(request.timeout_seconds, settings.default_timeout_seconds)

    # ── 解析起终点集合（每端可独立 exact/fuzzy：如 乌鲁木齐(全站)→北京西(单站)；
    #    多站精确：from_stations/to_stations 非空时按列表逐站精确解析取并集）──
    if request.from_stations:
        source_names = _resolve_multi(request.from_stations, graph, matcher)
    else:
        source_names = resolve_station_set(
            request.from_query, request.from_mode or request.match_mode, graph, matcher)
    if request.to_stations:
        target_names = _resolve_multi(request.to_stations, graph, matcher)
    else:
        target_names = resolve_station_set(
            request.to_query, request.to_mode or request.match_mode, graph, matcher)
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

    # ── 反向下界（绕路过滤 + 耗时剪枝，多目标取任一目标的最小值） ──
    targets_list = sorted(target_set)
    h_dist = graph.get_multi_source_distances(targets_list)
    h_time = graph.get_multi_source_times(targets_list)
    straight_dist = min((h_dist.get(s, 100) for s in source_set), default=100)
    # 剪枝强度随约束动态调节（贴近生活）：用户指定换乘城市时放宽绕路比与耗时
    # slack——"必须经某城"的路线必然偏离最短路径，固定剪枝会误杀合理方案
    if constraint_city is not None:
        detour_limit = straight_dist * MAX_DETOUR_RATIO_CONSTRAINED
        prune_slack = settings.time_prune_slack + CONSTRAINED_SLACK_PENALTY
    else:
        detour_limit = straight_dist * MAX_DETOUR_RATIO
        prune_slack = settings.time_prune_slack

    # 热路径按站索引的列表（list 索引比 dict.get 快）
    n_stations = graph.station_count
    h_dist_arr = [h_dist.get(i, 0) for i in range(n_stations)]
    h_time_arr = [h_time.get(i, 0) for i in range(n_stations)]
    same_city_arr = [graph.same_city_of.get(i, ()) for i in range(n_stations)]
    target_flag = [1 if i in target_set else 0 for i in range(n_stations)]
    source_flag = [1 if i in source_set else 0 for i in range(n_stations)]

    # 耗时剪枝基准：预扫描锁定"每换乘级别的最短总耗时"
    best_durations: list[Optional[int]] = _prescan_best_durations(
        graph, request, source_set, target_set)

    conns = graph.sorted_connections
    out_conns = graph.out_conns
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

    round_labels: list[dict[int, list[Label]]] = [dict() for _ in range(rounds)]
    dest_labels: list[list[Label]] = [[] for _ in range(rounds)]
    scanned = 0
    generated = 0
    complete = True
    stopped_reason: Optional[str] = None

    has_constraint = constraint_city is not None
    for r in range(rounds):
        cur: dict[int, list[Label]] = {}
        code_arr: dict = {}   # 轮内每站 {车次: 最早到达}，O(1) 同车次去重
        fp_done: set = set()
        prev_round = round_labels[r - 1] if r > 0 else None
        is_first = (r == 0)

        # 活动站 → 桶起始位置：堆归并只迭代"有标签/可能换乘"的站，
        # 替代全量扫描所有连接（无标签站的连接纯属白扫）
        heap: list[tuple[int, int, int]] = []  # (dep_m, from_idx, pos_in_bucket)
        pos_of: dict[int, int] = {}            # 站 → 当前有效迭代器位置
        earliest_of: dict[int, int] = {}       # 站 → 已记录的最早标签到达
        if is_first:
            for s in source_set:
                pos = bisect.bisect_left(out_conns[s], (earliest_depart,))
                if pos < len(out_conns[s]):
                    heap.append((out_conns[s][pos][0], s, pos))
                    pos_of[s] = pos
                    earliest_of[s] = -1  # 伪标签：登车检查由时间窗保证，无需回退
        else:
            for f, lst in prev_round.items():
                # 换乘最早登车 = 本站上轮最早到达 + 缓冲
                min_arr = min(lb.arrive for lb in lst)
                pos = bisect.bisect_left(out_conns[f], (min_arr + same_buffer,))
                if pos < len(out_conns[f]):
                    heap.append((out_conns[f][pos][0], f, pos))
                    pos_of[f] = pos
                    # 本轮 cur[f] 标签到达 >= 该时刻（连接 dep + travel），不会变早
                    earliest_of[f] = min_arr + same_buffer
        heapq.heapify(heap)

        processed = 0
        stopped = False
        while heap:
            _, f, pos = heapq.heappop(heap)
            if pos_of.get(f) != pos:
                continue  # 过期条目：迭代器已回退，由新条目接管
            bucket = out_conns[f]
            # 段内 f 固定：来源检查的站级状态只取一次
            is_src_ok = is_first and source_flag[f]
            cl = cur.get(f)
            pl = prev_round.get(f) if prev_round is not None else None
            while True:
                raw = bucket[pos]
                dep_m = raw[_CONN_DEP]
                code = raw[_CONN_CODE]
                t = raw[_CONN_T]
                arr_m = raw[_CONN_ARR]
                dist = raw[_CONN_DIST]

                processed += 1
                scanned += 1
                if processed % 20000 == 0:
                    elapsed = _time.perf_counter() - t_start
                    if elapsed > timeout:
                        stopped_reason = "timeout"
                        complete = False
                        stopped = True
                        break
                    if generated > state_limit:
                        stopped_reason = "state_limit"
                        complete = False
                        stopped = True
                        break

                # ── 来源 1：初始登车（仅第 0 轮）──
                if is_src_ok and earliest_depart <= dep_m <= latest_depart:
                    generated += 1
                    rail = dist
                    if rail + h_dist_arr[t] <= detour_limit:
                        cand = Label(
                            station=t, arrive=arr_m, train_code=code, first_dep=dep_m,
                            rail_distance=rail, train_xfers=0, inter_xfers=0,
                            inter_minutes=0, prev=None, conn=raw, seg_kind="train",
                            matched_constraint=False)
                        if _insert_round_label(cur, t, cand, state_limits, code_arr, has_constraint) is not None:
                            lst_t = cur.get(t)
                            _sync_heap(heap, pos_of, earliest_of, out_conns, t, lst_t[0].arrive)
                            if same_city_arr[t]:
                                _enqueue_fp_targets(heap, pos_of, earliest_of, out_conns, cur,
                                                     _expand_footpath(
                                    cur, cand, graph, t, arr_m, fp_done, rail, 0, dep_m,
                                    same_city_arr, foot_time, h_dist_arr, h_time_arr,
                                    detour_limit, prune_slack, best_durations, target_flag,
                                    constraint_city, city_of, state_limits, code_arr, has_constraint, max_transfers))

                # ── 来源 2：同车续乘（本轮即时标签）──
                if cl:
                    for lb in cl:
                        if lb.arrive > dep_m:
                            break  # 列表按到达升序，之后的标签更晚，必不满足登车
                        if lb.train_code != code:
                            continue
                        generated += 1
                        rail = lb.rail_distance + dist
                        if rail + h_dist_arr[t] > detour_limit:
                            continue
                        # 目标导向耗时剪枝（内联）
                        if (prune_slack is not None and not target_flag[t]):
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
                            lst_t = cur.get(t)
                            _sync_heap(heap, pos_of, earliest_of, out_conns, t, lst_t[0].arrive)
                            if same_city_arr[t]:
                                _enqueue_fp_targets(heap, pos_of, earliest_of, out_conns, cur,
                                                     _expand_footpath(
                                    cur, cand, graph, t, arr_m, fp_done, rail, lb.train_xfers,
                                    lb.first_dep, same_city_arr, foot_time, h_dist_arr,
                                    h_time_arr, detour_limit, prune_slack, best_durations,
                                    target_flag, constraint_city, city_of, state_limits, code_arr, has_constraint, max_transfers))

                # ── 来源 3：换乘（上一轮标签）──
                if pl:
                    for lb in pl:
                            if lb.arrive + same_buffer > dep_m:
                                break  # 列表按到达升序，之后的标签更晚，必不满足缓冲
                            if lb.train_xfers + lb.inter_xfers + 1 > max_transfers:
                                continue
                            # 同车次跨轮"换乘"一律无效（含停站时刻倒挂等数据异常
                            # 导致的 arrive != dep 情形）：同趟应续乘，隔天同车次
                            # 乘客也不会换乘——跳过，避免"单段+1次换乘"假路线
                            if lb.train_code == code:
                                continue
                            generated += 1
                            rail = lb.rail_distance + dist
                            if rail + h_dist_arr[t] > detour_limit:
                                continue
                            matched = lb.matched_constraint
                            if constraint_city and not matched and city_of.get(f, "") == constraint_city:
                                matched = True
                            if (prune_slack is not None and not target_flag[t]):
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
                                lst_t = cur.get(t)
                                _sync_heap(heap, pos_of, earliest_of, out_conns, t, lst_t[0].arrive)
                                if same_city_arr[t]:
                                    _enqueue_fp_targets(heap, pos_of, earliest_of, out_conns, cur,
                                                         _expand_footpath(
                                        cur, cand, graph, t, arr_m, fp_done, rail, lb.train_xfers + 1,
                                        lb.first_dep, same_city_arr, foot_time, h_dist_arr,
                                        h_time_arr, detour_limit, prune_slack, best_durations,
                                        target_flag, constraint_city, city_of, state_limits, code_arr, has_constraint, max_transfers))

                # 批处理推进：本站下一连接 dep 不超过堆中最小值时，段内继续
                # （堆保证全局时间序；段内处理产生的回退/新站入堆会改变堆顶，
                # 下一轮比较自然停止本段，先处理更早的新站）
                nxt = pos + 1
                if nxt >= len(bucket):
                    break  # 本站连接耗尽
                if heap and bucket[nxt][0] > heap[0][0]:
                    pos_of[f] = nxt
                    heapq.heappush(heap, (bucket[nxt][0], f, nxt))
                    break
                pos = nxt
                pos_of[f] = nxt
            if stopped:
                break

        # 轮末：从最终标签收集目标站（避免插入时收集导致已淘汰标签残留）
        for st, lst in cur.items():
            if target_flag[st]:
                for lb in lst:
                    if earliest_arrive <= lb.arrive <= latest_arrive:
                        dest_labels[r].append(lb)

        round_labels[r] = cur
        if not complete:
            break

    # ── 回溯、过滤、去重 ──
    # 直达方案由独立枚举提供（永远完整、不受剪枝截断影响）；
    # CSA 第 0 轮仅作为换乘轮的标签基础，其直达标签不再作为结果输出。
    direct_routes = _collect_direct_routes(graph, request, source_set, target_set)
    if constraint_city is not None:
        # 指定换乘城市约束：直达（无换乘）不符合"必须经该城市换乘"，全部排除
        direct_routes = []
    results: list[RouteResult] = []
    seen_keys: set = set()

    for r in range(1, rounds):
        for lb in dest_labels[r]:
            if constraint_city and not lb.matched_constraint:
                continue
            route = _reconstruct_from_label(graph, lb)
            if not route or route.total_minutes <= 0:
                continue
            if route.rail_distance > detour_limit:
                continue
            # 速度过滤基于铁路行驶时间（不含跨夜等待/地面移动）：
            # 总耗时含班次间隔等待，会误杀"行驶正常但等次日车"的合法跨夜路线
            travel_minutes = sum(
                s.travel_minutes for s in route.segments
                if isinstance(s, TrainSegment))
            if (route.rail_distance > 0 and travel_minutes > 0 and
                    route.rail_distance / max(travel_minutes / 60.0, 0.01) < MIN_SPEED_KPH):
                continue
            if _has_repeated_station(route):
                continue
            key = route_key(route.segments)
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(route)

    # 合并直达 + 换乘，按（换乘次数, 总耗时）排序 → 直达天然在最前
    all_routes = direct_routes + results
    all_routes.sort(key=lambda r: (r.train_transfers + r.interstation_transfers, r.total_minutes))

    max_results = settings.max_results
    if max_results is not None and len(all_routes) > max_results:
        # 直达永远完整：截断只作用于换乘部分（直达数量恒为上限下限）
        direct_cnt = sum(1 for r in all_routes
                         if r.train_transfers == 0 and r.interstation_transfers == 0)
        all_routes = all_routes[:max(max_results, direct_cnt)]

    elapsed_ms = int((_time.perf_counter() - t_start) * 1000)

    return SearchResponse(
        routes=tuple(all_routes),
        metadata=SearchMetadata(
            profile=request.search_profile,
            complete=complete,
            stopped_reason=stopped_reason,
            elapsed_ms=elapsed_ms,
            scanned_connections=scanned,
            generated_states=generated,
            returned_routes=len(all_routes),
        ),
        source_stations=tuple(source_names),
        target_stations=tuple(target_names),
    )
