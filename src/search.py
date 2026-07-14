"""
多标准路径搜索（A* + Pareto 剪枝）。

关键改进：反向 Dijkstra 预计算到终点的最短距离作为启发式。
"""

import heapq
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from src.graph import RailwayGraph, MAX_TRANSFERS

MAX_PARETO_PER_NODE = 6
SPEED_FACTOR = 5.8  # 350 km/h ≈ 5.83 km/min，乐观估计（可纳启发式）


@dataclass
class PathState:
    station_idx: int
    total_time: int
    total_distance: int
    transfers: int
    prev_state: Optional["PathState"] = None
    edge_from: Optional[int] = None
    is_transfer: bool = False

    def __lt__(self, other):
        return self.total_time < other.total_time

    def dominates(self, other: "PathState") -> bool:
        if self.total_time > other.total_time:
            return False
        if self.total_distance > other.total_distance:
            return False
        if self.transfers > other.transfers:
            return False
        return (self.total_time < other.total_time or
                self.total_distance < other.total_distance or
                self.transfers < other.transfers)


@dataclass
class SearchResult:
    stations: list[str]
    total_time: int
    total_distance: int
    transfers: int
    transfer_stations: list[str]


def _compute_heuristic(graph: RailwayGraph, target: int) -> dict[int, float]:
    """反向 Dijkstra（仅距离权重）预计算每个站到 target 的最短距离（km）。"""
    dist = {target: 0.0}
    heap = [(0.0, target)]
    while heap:
        d, cur = heapq.heappop(heap)
        if d > dist.get(cur, float("inf")):
            continue
        # 反向：遍历所有入边
        for prev_idx, edges in graph.edges.items():
            if cur in edges:
                nd = d + edges[cur].distance
                if nd < dist.get(prev_idx, float("inf")):
                    dist[prev_idx] = nd
                    heapq.heappush(heap, (nd, prev_idx))
    return dist


def search(graph: RailwayGraph, from_station: str, to_station: str,
           alpha: float = 0.6, beta: float = 0.4) -> list[SearchResult]:
    if from_station not in graph.station_to_idx:
        raise ValueError(f"未找到车站: {from_station}")
    if to_station not in graph.station_to_idx:
        raise ValueError(f"未找到车站: {to_station}")

    start = graph.station_to_idx[from_station]
    end = graph.station_to_idx[to_station]

    # 预计算启发式
    h_dist = _compute_heuristic(graph, end)
    if start not in h_dist:
        return []  # 不可达

    def h(node: int) -> float:
        """启发式：到终点的估算时间（分钟）。"""
        return h_dist.get(node, 1e9) / SPEED_FACTOR

    pareto: dict[int, list[PathState]] = defaultdict(list)
    heap: list[tuple[float, PathState]] = []

    init_state = PathState(start, 0, 0, 0)
    heapq.heappush(heap, (h(start), init_state))
    pareto[start].append(init_state)

    results: list[SearchResult] = []
    expanded = 0

    while heap:
        _, cur = heapq.heappop(heap)
        expanded += 1

        if expanded > 1_000_000:
            break

        if cur.station_idx == end:
            results.append(_reconstruct(cur, graph))
            if len(results) >= 30:
                break
            continue

        # 1. 运行边
        for nxt_idx, edge_info in graph.edges.get(cur.station_idx, {}).items():
            if cur.prev_state and nxt_idx == cur.prev_state.station_idx:
                continue
            ns = PathState(nxt_idx,
                           cur.total_time + edge_info.min_time,
                           cur.total_distance + edge_info.distance,
                           cur.transfers,
                           prev_state=cur, edge_from=cur.station_idx)
            if _try_add_pareto(pareto, nxt_idx, ns):
                priority = cur.total_time + edge_info.min_time + h(nxt_idx)
                heapq.heappush(heap, (priority, ns))

        # 2. 换乘边
        if cur.transfers < MAX_TRANSFERS:
            # 2a. 同城异站
            for nxt_idx in graph.same_city_of.get(cur.station_idx, []):
                if cur.prev_state and nxt_idx == cur.prev_state.station_idx:
                    continue
                ns = PathState(nxt_idx,
                               cur.total_time + 90, cur.total_distance,
                               cur.transfers + 1,
                               prev_state=cur, edge_from=cur.station_idx,
                               is_transfer=True)
                if _try_add_pareto(pareto, nxt_idx, ns):
                    heapq.heappush(heap, (cur.total_time + 90 + h(nxt_idx), ns))

            # 2b. 同站换乘
            if not cur.is_transfer:
                ns = PathState(cur.station_idx,
                               cur.total_time + 90, cur.total_distance,
                               cur.transfers + 1,
                               prev_state=cur, edge_from=cur.station_idx,
                               is_transfer=True)
                if _try_add_pareto(pareto, cur.station_idx, ns):
                    heapq.heappush(heap, (cur.total_time + 90 + h(cur.station_idx), ns))

    results.sort(key=lambda r: (r.transfers, r.total_time))
    return results


def _try_add_pareto(pareto, node, new_state):
    existing = pareto.get(node, [])
    for old in existing:
        if old.dominates(new_state):
            return False
    pareto[node] = [old for old in existing if not new_state.dominates(old)]
    pareto[node].append(new_state)
    pareto[node].sort(key=lambda s: (s.transfers, s.total_time))
    if len(pareto[node]) > MAX_PARETO_PER_NODE:
        pareto[node] = pareto[node][:MAX_PARETO_PER_NODE]
    return True


def _reconstruct(state, graph):
    stations = []
    transfers_at = []
    cur = state
    while cur is not None:
        stations.append(graph.idx_to_station[cur.station_idx])
        if cur.is_transfer:
            transfers_at.append(graph.idx_to_station[cur.station_idx])
        cur = cur.prev_state
    stations.reverse()
    transfers_at.reverse()
    return SearchResult(
        stations=stations,
        total_time=state.total_time,
        total_distance=state.total_distance,
        transfers=state.transfers,
        transfer_stations=transfers_at,
    )
