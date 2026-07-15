"""
铁路网络图构建模块。

从路路通全量时刻表 CSV 构建带权有向图：
- 节点：车站（3,305 个）
- 运行边：同一车次相邻停站之间（113,968 条有向边）
- 换乘边：同城车站之间（同站 15 分钟 / 异站 60 分钟默认）

边属性：最快耗时（分钟）、距离（km）

性能优化：
- sorted_connections：预排序的双日 Connection 列表，供 CSA 直接使用
- reverse_edges：反向邻接表，供启发式 Dijkstra 使用
"""

import csv
import heapq
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# 同站换乘默认时间（分钟）
DEFAULT_SAME_TRANSFER_MINUTES = 15

# 异站（同城不同站）换乘默认时间（分钟）
DEFAULT_INTER_TRANSFER_MINUTES = 60

# 最大换乘次数约束
MAX_TRANSFERS = 3


@dataclass
class EdgeInfo:
    """图中一条边的摘要信息（不含具体车次列表）。"""
    min_time: int       # 该区段最快列车耗时（分钟）
    distance: int       # 里程（km）
    train_count: int = 0  # 经过该边的车次数


@dataclass
class TrainEdge:
    """一条边在具体车次中的记录。"""
    train_code: str     # 车次名（如 G1）
    seq_from: int       # 出发站在该车次中的序号
    seq_to: int         # 到达站在该车次中的序号
    depart_time: str    # 发车时间 HH:MM（空字符串表示始发）
    arrive_time: str    # 到达时间 HH:MM（空字符串表示终到）
    travel_minutes: int # 区间运行时间（分钟）
    distance: int       # 区间里程（km）
    dist_cumulative: int = 0  # 到达站的累计里程（从该车次起点算）


class RailwayGraph:
    """全国铁路网络图。"""

    def __init__(self):
        self.reset()

    def reset(self):
        """清空原始与派生数据，使同一实例可安全重新 build。"""
        # 车站名 → 内部索引
        self.station_to_idx: dict[str, int] = {}
        # 内部索引 → 车站名
        self.idx_to_station: list[str] = []
        # 同城车站分组: city_code → [station_name]
        self.city_groups: dict[str, list[str]] = defaultdict(list)
        # 站点/城市反向索引（build 后仅包含图中有效车站）
        self.station_to_city_code: dict[int, str] = {}
        self.city_code_to_name: dict[str, str] = {}
        # 邻接表: from_idx → {to_idx: EdgeInfo}
        self.edges: dict[int, dict[int, EdgeInfo]] = defaultdict(dict)
        # 反向邻接表: to_idx → {from_idx: EdgeInfo}（供反向 Dijkstra 使用）
        self.reverse_edges: dict[int, dict[int, EdgeInfo]] = defaultdict(dict)
        # 边 → 车次列表: (from_idx, to_idx) → [TrainEdge]
        self.edge_trains: dict[tuple[int, int], list[TrainEdge]] = defaultdict(list)
        # 同城换乘边（集合，O(1) 查询）: {(idx_a, idx_b), ...}
        self.transfer_edge_set: set[tuple[int, int]] = set()
        # 同城换乘边列表（保持向后兼容）
        self.transfer_edges: list[tuple[int, int]] = []
        # 快速索引: station_idx → [同城其他车站的 idx]
        self.same_city_of: dict[int, list[int]] = defaultdict(list)
        # 出发索引: station_idx → [(train_code, seq, depart_time), ...]
        self.departures: dict[int, list[tuple[str, int, str]]] = defaultdict(list)
        # 预排序 Connection 列表（供 CSA 直接使用，build() 后填充）
        self.sorted_connections: list = []
        # 目标站 → 各站最短铁路距离（反向 Dijkstra 结果）
        self.distance_cache: dict[int, dict[int, float]] = {}

    # ── 构建 ────────────────────────────────────────────

    def build(self, csv_path: str, station_js_path: str):
        """从时刻表 CSV 和 station_name.js 构建图。"""
        self.reset()
        self._load_station_cities(station_js_path)
        self._load_timetable(csv_path)
        self._add_transfer_edges()
        self._compute_edge_stats()
        self._build_reverse_edges()
        self.build_connections_cache()

    def _load_station_cities(self, path: str):
        """从 station_name.js 提取同城车站分组。"""
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        match = re.search(r"station_names\s*=\s*'(.*)'\s*$", text, re.DOTALL)
        content = match.group(1) if match else text.split("'", 1)[1].rsplit("'", 1)[0]

        for entry in content.split("@"):
            if not entry.strip():
                continue
            parts = entry.strip().split("|")
            if len(parts) < 7:
                continue
            name = parts[1]       # 中文站名
            city_code = parts[6]  # 城市代码（如 "0357" 表示北京）
            city_name = parts[7] if len(parts) > 7 else ""
            self.city_groups[city_code].append(name)
            if city_name:
                self.city_code_to_name[city_code] = city_name

    def _get_or_create_station(self, name: str) -> int:
        if name not in self.station_to_idx:
            self.station_to_idx[name] = len(self.idx_to_station)
            self.idx_to_station.append(name)
        return self.station_to_idx[name]

    def _parse_time(self, t: str) -> Optional[int]:
        """解析 HH:MM 为分钟数，空字符串返回 None。"""
        t = t.strip()
        if not t:
            return None
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    def _time_diff(self, depart: str, arrive: str) -> int:
        """计算区间运行分钟数（处理跨天情况）。"""
        d = self._parse_time(depart)
        a = self._parse_time(arrive)
        if d is None or a is None:
            return 0
        diff = a - d
        if diff < 0:
            diff += 24 * 60  # 跨天
        return diff

    def _load_timetable(self, csv_path: str):
        """加载全量时刻表 CSV，构建边和车次列表。"""
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # 按车次分组
        train_stops: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            train_stops[row["车次"]].append(row)

        for code, stops in train_stops.items():
            # 按序号排序
            stops.sort(key=lambda s: int(s["序号"]))

            # 相邻停站 → 边
            for i in range(len(stops) - 1):
                cur = stops[i]
                nxt = stops[i + 1]

                from_name = cur["站名"]
                to_name = nxt["站名"]
                depart = cur["发车"].strip()
                arrive = nxt["到达"].strip()

                if not depart or not arrive:
                    continue  # 跳过始发/终到站缺少时间的边

                travel = self._time_diff(depart, arrive)
                # 区间里程 = 相邻站累计里程差
                dist_seg = int(nxt["里程km"]) - int(cur["里程km"])
                if dist_seg < 0:
                    dist_seg = 0
                dist_cum = int(nxt["里程km"])  # 到达站的累计里程
                seq_f = int(cur["序号"])
                seq_t = int(nxt["序号"])

                from_idx = self._get_or_create_station(from_name)
                to_idx = self._get_or_create_station(to_name)

                self.edge_trains[(from_idx, to_idx)].append(TrainEdge(
                    train_code=code,
                    seq_from=seq_f,
                    seq_to=seq_t,
                    depart_time=depart,
                    arrive_time=arrive,
                    travel_minutes=travel,
                    distance=dist_seg,
                    dist_cumulative=dist_cum,
                ))
                self.departures[from_idx].append((code, seq_f, depart))

    _CITY_SUFFIXES = ["市", "县", "区", "站", "地区", "自治州", "盟", "林区"]

    @classmethod
    def _city_base_name(cls, city_name: str) -> str:
        """提取城市基础名（去除行政后缀），用于同城判断。"""
        result = city_name
        for sfx in cls._CITY_SUFFIXES:
            if result.endswith(sfx) and len(result) > len(sfx):
                result = result[:-len(sfx)]
                break
        return result

    @classmethod
    def _station_is_city_member(cls, station_name: str, city_base: str, city_station_count: int,
                                 any_station_has_city_base: bool) -> bool:
        """判断车站是否真正属于该城市的同城范围。

        规则：
        - 大城市（≥8）：所有站视为同城
        - 含城市基名：仅含城市基名的车站算同城（排除异名县）
        - 不含城市基名：全部算同城（测试 fixture 等场景）
        """
        if city_station_count >= 8:
            return True
        if any_station_has_city_base:
            return city_base in station_name
        return True

    def _add_transfer_edges(self):
        """为同城车站添加换乘边和快速索引。"""
        # 先统计每个城市在图中的有效站数
        city_graph_count: dict[str, int] = {}
        for city, station_names in self.city_groups.items():
            count = sum(1 for n in station_names if n in self.station_to_idx)
            city_graph_count[city] = count

        for city, station_names in self.city_groups.items():
            city_name = self.city_code_to_name.get(city, "")
            city_base = self._city_base_name(city_name)
            graph_count = city_graph_count.get(city, 0)

            # 检查是否有任一车站名包含城市基名（空基名视为全部匹配）
            graph_names = [n for n in station_names if n in self.station_to_idx]
            if not city_base:
                any_has_base = False  # 无基名 → 全部归入
            else:
                any_has_base = any(city_base in n for n in graph_names)

            indices = []
            for name in graph_names:
                if not self._station_is_city_member(name, city_base, graph_count, any_has_base):
                    continue
                station_idx = self.station_to_idx[name]
                indices.append(station_idx)
                self.station_to_city_code[station_idx] = city

            # 同城车站两两互联
            for i in range(len(indices)):
                for j in range(len(indices)):
                    if i != j:
                        pair = (indices[i], indices[j])
                        self.transfer_edges.append(pair)
                        self.transfer_edge_set.add(pair)
                        self.same_city_of[indices[i]].append(indices[j])

    def _compute_edge_stats(self):
        """为每条唯一边计算摘要信息。"""
        for (f, t), trains in self.edge_trains.items():
            min_time = min(te.travel_minutes for te in trains)
            # 取第一个车次的距离（同一区段距离一致）
            dist = trains[0].distance
            self.edges[f][t] = EdgeInfo(
                min_time=min_time,
                distance=dist,
                train_count=len(trains),
            )

    def _build_reverse_edges(self):
        """构建反向邻接表，供反向 Dijkstra 启发式使用。"""
        for f, neighbors in self.edges.items():
            for t, info in neighbors.items():
                self.reverse_edges[t][f] = info

    def build_connections_cache(self):
        """预建并排序双日 Connection 列表，之后每次查询直接使用。"""
        # 避免循环引用：Connection 定义在 csa.py，这里用简单 tuple 存储
        # 格式: (depart_minutes, train_code, from_idx, to_idx,
        #        arrive_minutes, travel_minutes, distance, dist_cumulative, seq)
        conns = []
        for (f, t), trains in self.edge_trains.items():
            for te in trains:
                dep = _parse_minutes(te.depart_time)
                arr = _parse_minutes(te.arrive_time)
                if arr < dep:
                    arr += 1440
                for day in range(2):
                    conns.append((
                        dep + day * 1440,   # depart_minutes
                        te.train_code,
                        f, t,
                        arr + day * 1440,   # arrive_minutes
                        te.travel_minutes,
                        te.distance,
                        te.dist_cumulative,
                        te.seq_from,
                    ))
        conns.sort(key=lambda c: c[0])
        self.sorted_connections = conns

    # ── 查询 ────────────────────────────────────────────

    def get_reverse_distances(self, target: int) -> dict[int, float]:
        """返回各站到 target 的最短铁路距离，并由当前图实例缓存。"""
        cached = self.distance_cache.get(target)
        if cached is not None:
            return cached

        distances: dict[int, float] = {target: 0.0}
        heap = [(0.0, target)]
        while heap:
            distance, current = heapq.heappop(heap)
            if distance > distances.get(current, float("inf")):
                continue
            for previous, info in self.reverse_edges.get(current, {}).items():
                candidate = distance + info.distance
                if candidate < distances.get(previous, float("inf")):
                    distances[previous] = candidate
                    heapq.heappush(heap, (candidate, previous))

        self.distance_cache[target] = distances
        return distances

    def get_edge_info(self, from_idx: int, to_idx: int) -> Optional[EdgeInfo]:
        """获取运行边摘要信息。"""
        return self.edges.get(from_idx, {}).get(to_idx)

    def get_interstation_transfer_time(
        self, from_idx: int, to_idx: int,
        default_minutes: int = DEFAULT_INTER_TRANSFER_MINUTES
    ) -> int:
        """异站换乘时间估算：查有无直达班次，有则取最短旅行时间+30分钟，否则用默认值。"""
        key = (from_idx, to_idx)
        if key in self.edge_trains:
            min_travel = min(te.travel_minutes for te in self.edge_trains[key])
            return min_travel + 30
        return default_minutes

    def is_same_city(self, a: int, b: int) -> bool:
        """判断两个车站是否同城。"""
        return (a, b) in self.transfer_edge_set or (b, a) in self.transfer_edge_set

    def __len__(self):
        return len(self.idx_to_station)

    @property
    def station_count(self) -> int:
        return len(self.idx_to_station)

    @property
    def edge_count(self) -> int:
        return sum(len(v) for v in self.edges.values())

    @property
    def transfer_count(self) -> int:
        return len(self.transfer_edges)


# ── 模块级辅助函数 ──────────────────────────────────────

def _parse_minutes(t: str) -> int:
    """解析 HH:MM 为分钟数，空字符串或无效值返回 0。"""
    if not t or not t.strip():
        return 0
    h, m = t.split(":")
    return int(h) * 60 + int(m)
