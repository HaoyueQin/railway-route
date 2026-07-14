"""
铁路网络图构建模块。

从路路通全量时刻表 CSV 构建带权有向图：
- 节点：车站（3,305 个）
- 运行边：同一车次相邻停站之间（113,968 条有向边）
- 换乘边：同城车站之间（统一 90 分钟）

边属性：最快耗时（分钟）、距离（km）
"""

import csv
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# 同站换乘统一时间（分钟）
DEFAULT_TRANSFER_MINUTES = 90

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
        # 车站名 → 内部索引
        self.station_to_idx: dict[str, int] = {}
        # 内部索引 → 车站名
        self.idx_to_station: list[str] = []
        # 同城车站分组: city_name → [station_indices]
        self.city_groups: dict[str, list[int]] = defaultdict(list)
        # 邻接表: from_idx → {to_idx: EdgeInfo}
        self.edges: dict[int, dict[int, EdgeInfo]] = defaultdict(dict)
        # 边 → 车次列表: (from_idx, to_idx) → [TrainEdge]
        self.edge_trains: dict[tuple[int, int], list[TrainEdge]] = defaultdict(list)
        # 同城换乘边: [(idx_a, idx_b), ...]
        self.transfer_edges: list[tuple[int, int]] = []
        # 快速索引: station_idx → [同城其他车站的 idx]
        self.same_city_of: dict[int, list[int]] = defaultdict(list)
        # 出发索引: station_idx → [(train_code, seq, depart_time), ...]
        self.departures: dict[int, list[tuple[str, int, str]]] = defaultdict(list)

    # ── 构建 ────────────────────────────────────────────

    def build(self, csv_path: str, station_js_path: str):
        """从时刻表 CSV 和 station_name.js 构建图。"""
        self._load_station_cities(station_js_path)
        self._load_timetable(csv_path)
        self._add_transfer_edges()
        self._compute_edge_stats()

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
            self.city_groups[city_code].append(name)

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

    def _add_transfer_edges(self):
        """为同城车站添加换乘边和快速索引。"""
        for city, station_names in self.city_groups.items():
            indices = []
            for name in station_names:
                if name in self.station_to_idx:
                    indices.append(self.station_to_idx[name])
            # 同城所有车站两两互联
            for i in range(len(indices)):
                for j in range(len(indices)):
                    if i != j:
                        self.transfer_edges.append((indices[i], indices[j]))
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

    # ── 查询 ────────────────────────────────────────────

    def get_edge_info(self, from_idx: int, to_idx: int) -> Optional[EdgeInfo]:
        """获取运行边摘要信息。"""
        return self.edges.get(from_idx, {}).get(to_idx)

    def get_transfer_time(self, from_idx: int, to_idx: int) -> int:
        """获取换乘耗时。同城车站 → DEFAULT_TRANSFER_MINUTES，否则返回极大值（不可换乘）。"""
        if (from_idx, to_idx) in self.transfer_edges or \
           (to_idx, from_idx) in self.transfer_edges:
            return DEFAULT_TRANSFER_MINUTES
        # 非同城也可能有合理的换乘（同站换乘），也返回默认值
        # 后续可以根据车站名是否相同来判断同站/异站
        if from_idx == to_idx:
            return 0
        return 10 ** 9  # 不可换乘

    def is_same_city(self, a: int, b: int) -> bool:
        """判断两个车站是否同城。"""
        return (a, b) in self.transfer_edges or (b, a) in self.transfer_edges

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
