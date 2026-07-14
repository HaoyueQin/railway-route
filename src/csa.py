"""
Connection Scan Algorithm (CSA) v3。

修复：
- 终点站不剪枝，收集所有到达状态（避免不同时段车次互杀）
- 中间站 Pareto 剪枝防止状态爆炸
- 支持出发时间段过滤 (earliest_depart, latest_depart)
- 跨日扫描
- 路径过滤 (绕路>3x, 均速<10km/h)
"""

from dataclasses import dataclass
from typing import Optional
from collections import defaultdict

from src.graph import RailwayGraph

MAX_PARETO_PER_NODE = 20
MAX_DETOUR_RATIO = 3.0
MIN_SPEED_KPH = 10.0


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


@dataclass
class ArrivalState:
    time: int
    distance: int
    transfers: int
    prev_conn: Optional["Connection"] = None
    prev_state: Optional["ArrivalState"] = None
    train_code: str = ""
    depart_time: int = 0  # 首次登车时间

    def dominates(self, other: "ArrivalState") -> bool:
        """宽松支配：用于中间站剪枝，不用于终点站。"""
        if self.time > other.time + 360:
            return False
        if self.distance > other.distance * 1.5:
            return False
        if self.transfers > other.transfers:
            return False
        return (self.time + 180 < other.time or
                self.distance < other.distance * 0.7 or
                self.transfers < other.transfers)


@dataclass
class SearchResult:
    stations: list[str]
    train_codes: list[str]
    depart_times: list[str]
    arrive_times: list[str]
    travel_minutes: list[int]
    distances: list[int]
    total_time: int
    total_distance: int
    transfers: int
    transfer_stations: list[str]
    first_depart: int = 0  # 首次发车时间(分钟)


def _compute_heuristic(graph, target):
    dist = {target: 0.0}
    import heapq
    heap = [(0.0, target)]
    while heap:
        d, cur = heapq.heappop(heap)
        if d > dist.get(cur, float("inf")): continue
        for prev_idx, edges in graph.edges.items():
            if cur in edges:
                nd = d + edges[cur].distance
                if nd < dist.get(prev_idx, float("inf")):
                    dist[prev_idx] = nd; heapq.heappush(heap, (nd, prev_idx))
    return dist


def search(graph: RailwayGraph, from_st: str, to_st: str,
           max_transfers: int = 3, max_results: int = 100,
           earliest_depart: int = 0, latest_depart: int = 2880,
           earliest_arrive: int = 0, latest_arrive: int = 5760,
           transfer_at: str = "") -> list[SearchResult]:
    sidx = graph.station_to_idx.get(from_st)
    eidx = graph.station_to_idx.get(to_st)
    if sidx is None or eidx is None:
        return []

    h_dist = _compute_heuristic(graph, eidx)
    straight_dist = h_dist.get(sidx, 100)

    # ── connections ──
    connections: list[Connection] = []
    for (f, t), trains in graph.edge_trains.items():
        for te in trains:
            dep = _pm(te.depart_time); arr = _pm(te.arrive_time)
            if arr < dep: arr += 24 * 60
            for day in range(2):
                connections.append(Connection(te.train_code, f, t,
                                              dep + day * 1440, arr + day * 1440,
                                              te.travel_minutes, te.distance,
                                              te.dist_cumulative, te.seq_from))
    connections.sort(key=lambda c: c.depart_minutes)

    # ── 中间站 Pareto + 终点全收 ──
    pareto: dict[int, list[ArrivalState]] = defaultdict(list)
    init = ArrivalState(time=-1, distance=0, transfers=-1, train_code="")
    pareto[sidx].append(init)
    dest_states: list[ArrivalState] = []

    for conn in connections:
        f, t = conn.from_station, conn.to_station
        for state in pareto.get(f, []):
            if state.transfers == -1:
                if not (earliest_depart <= conn.depart_minutes <= latest_depart):
                    continue
                new_tr, first_dep = 0, conn.depart_minutes
            elif state.train_code == conn.train_code:
                if state.time > conn.depart_minutes: continue
                new_tr, first_dep = state.transfers, state.depart_time
            else:
                if state.transfers >= max_transfers: continue
                if state.time + 90 > conn.depart_minutes: continue
                new_tr, first_dep = state.transfers + 1, state.depart_time

            new_state = ArrivalState(
                time=conn.arrive_minutes,
                distance=state.distance + conn.distance,
                transfers=new_tr,
                prev_conn=conn, prev_state=state, train_code=conn.train_code,
                depart_time=first_dep,
            )

            if t == eidx:
                # 终点站：不剪枝，全收
                if earliest_arrive <= conn.arrive_minutes <= latest_arrive:
                    dest_states.append(new_state)
            else:
                _add_pareto(pareto, t, new_state)

    # ── 回溯 + 过滤 ──
    results = []
    seen = set()
    for state in dest_states:
        route = _reconstruct(graph, state)
        if not route or route.total_time <= 0: continue
        if route.total_distance > straight_dist * MAX_DETOUR_RATIO: continue
        if route.total_distance > 0 and route.total_distance / (route.total_time / 60.0) < MIN_SPEED_KPH: continue
        key = tuple(route.train_codes)
        if key not in seen:
            seen.add(key)
            results.append(route)

    results.sort(key=lambda r: (r.transfers, r.total_time))
    return results[:max_results]


def _add_pareto(pareto, station_idx, new_state):
    existing = pareto.get(station_idx, [])
    for old in existing:
        if old.dominates(new_state): return False
    pareto[station_idx] = [old for old in existing if not new_state.dominates(old)]
    pareto[station_idx].append(new_state)
    if len(pareto[station_idx]) > MAX_PARETO_PER_NODE:
        pareto[station_idx].sort(key=lambda s: (s.transfers, s.time))
        pareto[station_idx] = pareto[station_idx][:MAX_PARETO_PER_NODE]
    return True


def _reconstruct(graph, state):
    segs = []; cur = state
    while cur and cur.prev_conn:
        segs.append(cur.prev_conn); cur = cur.prev_state
    if not segs: return None
    segs.reverse()

    merged = []
    for conn in segs:
        if merged and merged[-1].train_code == conn.train_code:
            prev = merged[-1]
            merged[-1] = Connection(prev.train_code, prev.from_station, conn.to_station,
                                    prev.depart_minutes, conn.arrive_minutes,
                                    conn.arrive_minutes - prev.depart_minutes,
                                    prev.distance + conn.distance,
                                    conn.dist_cumulative, prev.seq)
        else:
            merged.append(conn)

    stations = [graph.idx_to_station[merged[0].from_station]]
    codes, deps, arrs, travs, dists, xfer = [], [], [], [], [], []
    for i, conn in enumerate(merged):
        stations.append(graph.idx_to_station[conn.to_station])
        codes.append(conn.train_code)
        deps.append(_fmt(conn.depart_minutes))
        arrs.append(_fmt(conn.arrive_minutes))
        travs.append(conn.travel_minutes)
        dists.append(conn.distance)
        if i > 0 and conn.train_code != merged[i-1].train_code:
            xfer.append(graph.idx_to_station[conn.from_station])

    tt = merged[-1].arrive_minutes - merged[0].depart_minutes
    if tt < 0: tt += 24 * 60
    return SearchResult(stations, codes, deps, arrs, travs, dists,
                        tt, sum(c.distance for c in merged),
                        len(xfer), xfer, merged[0].depart_minutes)


def _pm(t):
    if not t or not t.strip(): return 0
    h, m = t.split(":"); return int(h) * 60 + int(m)

def _fmt(m):
    m = m % (24 * 60)
    return f"{m//60:02d}:{m%60:02d}"
