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
        # 电报码 → 站名（station_name.js parts[2]，坐标按电报码索引）
        self.telecode_to_name: dict[str, str] = {}
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
        # 车次全程停站: train_code → [(station_idx, dep_min, arr_min, seq, dist_cum), ...]
        # 供前端显示始发终到站、上/下一站时刻与完整时刻表（构建期填充）
        self.train_stops: dict[str, list[tuple]] = {}
        # 预排序 Connection 列表（供 CSA 直接使用，build() 后填充）
        self.sorted_connections: list = []
        # 按出发站分桶的双日 Connection（桶内按 depart_minutes 升序，build() 后填充）
        # CSA 主循环用堆归并只迭代"有标签的站"，避免全量扫描所有连接
        self.out_conns: list[list] = []
        # 目标站 → 各站最短铁路距离（反向 Dijkstra 结果）
        self.distance_cache: dict[int, dict[int, float]] = {}
        # 5.1-1 异站换乘按距离估算：站 idx → (lat, lng)（12306 GCJ-02）
        self.coords: dict[int, tuple[float, float]] = {}
        # 同城站对 → 估算换乘分钟（无直达班次时的距离估算，load_coords 时预计算）
        self.interstation_minutes: dict[tuple[int, int], int] = {}
        # 同城站对 → 确定性换乘分钟（直达班次/坐标距离预计算；无数据对回退用户配置）
        self.foot_times: dict[tuple[int, int], int] = {}

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
            telecode = parts[2]   # 电报码
            self.city_groups[city_code].append(name)
            self.telecode_to_name[telecode] = name
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

            # 车次全程停站（供前端显示始发终到站 / 上站上一站·下站下一站时刻）
            # (station_idx, dep_min, arr_min, seq, dist_cum)；始发无到达/终到无发车记 -1；
            # 同一车次内时刻单调递增（跨午夜 +1440，慢车可跨多天）
            full_stops: list[tuple] = []
            offset = 0
            prev_time = -1
            for st in stops:
                dep_raw = st["发车"].strip()
                arr_raw = st["到达"].strip()
                dep_m = _parse_minutes(dep_raw) if dep_raw else -1
                arr_m = _parse_minutes(arr_raw) if arr_raw else -1
                if dep_m != -1 and prev_time != -1:
                    while dep_m + offset <= prev_time:
                        offset += 1440
                if arr_m != -1:
                    while arr_m + offset <= prev_time:
                        offset += 1440
                    prev_time = arr_m + offset
                elif dep_m != -1:
                    # 始发站无到达时刻：以发车时刻推进基准，
                    # 否则下一站"01:00 到达"与 prev_time=-1 比较恒为 False，跨夜修正失效
                    prev_time = dep_m + offset
                st_idx = self._get_or_create_station(st["站名"])
                full_stops.append((st_idx, dep_m + offset if dep_m != -1 else -1,
                                   arr_m + offset if arr_m != -1 else -1,
                                   int(st["序号"]), int(st["里程km"])))
            self.train_stops[code] = full_stops

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
                if te.distance <= 0:
                    continue  # 里程无效的边（Y 字头旅游列车等数据缺里程）：不参与规划
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
        # 按出发站分桶：conns 已按 dep 全局排序，桶内自然有序
        buckets: list[list] = [[] for _ in range(self.station_count)]
        for conn in conns:
            buckets[conn[2]].append(conn)
        self.out_conns = buckets

    # ── 查询 ────────────────────────────────────────────

    def get_reverse_distances(self, target: int) -> dict[int, float]:
        """返回各站到 target 的最短铁路距离，并由当前图实例缓存。

        跳过里程为 0 的运行边：部分车次（如旅游列车）里程数据缺失为 0，
        作为下界会制造"免费捷径"（如 衡水→兰州 0km），使绕路过滤/剪枝失真。
        """
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
                if info.distance <= 0:
                    continue  # 里程缺失段不作为距离下界
                candidate = distance + info.distance
                if candidate < distances.get(previous, float("inf")):
                    distances[previous] = candidate
                    heapq.heappush(heap, (candidate, previous))

        self.distance_cache[target] = distances
        return distances

    def _reverse_dijkstra(self, sources: list[int], attr: str) -> dict[int, float]:
        """反向多源 Dijkstra：各站到 sources 中任一目标的最小代价。

        attr: "distance"（铁路里程）或 "min_time"（最快运行时间下界）。
        跳过代价为 0 的运行边（数据缺失段不作为下界，避免假捷径）。
        不缓存（单次调用约 10ms，由 csa 每次查询调用一次）。
        """
        distances: dict[int, float] = {s: 0.0 for s in sources}
        heap = [(0.0, s) for s in sources]
        heapq.heapify(heap)
        while heap:
            cost, current = heapq.heappop(heap)
            if cost > distances.get(current, float("inf")):
                continue
            for previous, info in self.reverse_edges.get(current, {}).items():
                edge_cost = getattr(info, attr)
                if edge_cost <= 0:
                    continue  # 数据缺失段不作为下界
                candidate = cost + edge_cost
                if candidate < distances.get(previous, float("inf")):
                    distances[previous] = candidate
                    heapq.heappush(heap, (candidate, previous))
        return distances

    def get_multi_source_distances(self, targets: list[int]) -> dict[int, float]:
        """各站到任一目标站的最短铁路距离（多目标质量改进）。"""
        return self._reverse_dijkstra(targets, "distance")

    def get_multi_source_times(self, targets: list[int]) -> dict[int, float]:
        """各站到任一目标站的最快运行时间下界（目标导向剪枝用）。

        下界基于区段最快列车耗时，不含换乘缓冲与地面移动，恒不大于真实剩余时间。
        """
        return self._reverse_dijkstra(targets, "min_time")

    def get_edge_info(self, from_idx: int, to_idx: int) -> Optional[EdgeInfo]:
        """获取运行边摘要信息。"""
        return self.edges.get(from_idx, {}).get(to_idx)

    def get_interstation_transfer_time(
        self, from_idx: int, to_idx: int,
        default_minutes: int = DEFAULT_INTER_TRANSFER_MINUTES
    ) -> int:
        """异站换乘时间估算：
        1) 有直达班次 → 最短旅行时间 + 30 分钟（同城有市郊/城际线，坐车比地面快）；
        2) 无直达班次但同城有坐标 → 按直线距离估算（10km 内 30min，每 10km +15min）；
        3) 数据不足 → 回退默认值。"""
        edge = self.edges.get(from_idx, {}).get(to_idx)
        if edge is not None:
            return edge.min_time + 30
        if (from_idx, to_idx) in self.interstation_minutes:
            return self.interstation_minutes[(from_idx, to_idx)]
        return default_minutes

    def load_coords(self, json_path: str):
        """加载车站坐标（data/station_coords.json，12306 GCJ-02）并预计算同城站对换乘分钟。
        文件缺失/格式异常时静默跳过（回退固定换乘时间，不阻断启动）。"""
        import json as _json
        try:
            with open(json_path, encoding="utf-8") as f:
                data = _json.load(f)
        except (OSError, ValueError):
            return
        # 电报码索引（两数据源同体系，比站名匹配更可靠）
        matched = 0
        for code, rec in data.items():
            try:
                lat, lng = float(rec["lat"]), float(rec["lng"])
            except (KeyError, TypeError, ValueError):
                continue
            name = self.telecode_to_name.get(code)
            if name is None:
                continue
            idx = self.station_to_idx.get(name)
            if idx is not None:
                self.coords[idx] = (lat, lng)
                matched += 1
        print(f"坐标加载: {matched} 站（电报码索引）")
        for a, b in self.transfer_edges:
            ca, cb = self.coords.get(a), self.coords.get(b)
            if ca is not None and cb is not None:
                d = haversine_km(ca, cb)
                self.interstation_minutes[(a, b)] = est_transfer_minutes(d)
        # 确定性换乘分钟预计算（直达班次/坐标距离；无数据对搜索时回退用户配置）
        for a, b in self.transfer_edges:
            edge = self.edges.get(a, {}).get(b)
            if edge is not None:
                self.foot_times[(a, b)] = edge.min_time + 30
            elif (a, b) in self.interstation_minutes:
                self.foot_times[(a, b)] = self.interstation_minutes[(a, b)]

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

def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """直线距离（km，Haversine）。坐标来自 12306（GCJ-02 偏移量级 ~500m，
    对 10km 级换乘估算误差 <5%，可接受；如需高精度可后续转 WGS84）。"""
    import math

    r = 6371.0
    la1, lo1 = math.radians(a[0]), math.radians(a[1])
    la2, lo2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = la2 - la1, lo2 - lo1
    h = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
    # 浮点误差可能使 h 略超 [0,1]，clamp 防 asin 抛 ValueError
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, h))))


def est_transfer_minutes(dist_km: float) -> int:
    """距离 → 地面换乘估算分钟（交接文档 5.1-1 约定：10km 内 30min，
    每 10km +15min；上限 180min 防极端站对）。"""
    m = 30 + math_ceil(max(0.0, dist_km - 10.0) / 10.0) * 15
    return min(m, 180)


def math_ceil(x: float) -> int:
    """向上取整（避免顶部 import math 影响模块加载，保持最小依赖）。"""
    return -int(-x // 1)


def _parse_minutes(t: str) -> int:
    """解析 HH:MM 为分钟数，空字符串或无效值返回 0。"""
    if not t or not t.strip():
        return 0
    h, m = t.split(":")
    return int(h) * 60 + int(m)
