# -*- coding: utf-8 -*-
"""Rust M2 对拍基准生成器。

构建 Python 版铁路图（src/graph.py），输出:
  1. 图结构统计量（Rust 端硬编码断言用）
  2. 可复现抽样对拍数据（JSON，站名做 key，Rust 端逐项对比）

用法: python rust/tools/dump_graph_stats.py [输出路径]
默认输出: rust/tools/m2_baseline.json
"""
import json
import os
import random
import sys

# pyref：Rust 对拍的 Python 参考实现（master 的 src/ 子集）
sys.path.insert(0, os.path.dirname(__file__))
from pyref.graph import RailwayGraph  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CSV = os.path.join(ROOT, "data", "output", "车次时刻表.csv")
JS = os.path.join(ROOT, "data", "timetable", "station_name.js")
COORDS = os.path.join(ROOT, "data", "station_coords.json")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "m2_baseline.json")
SEED = 20260801


def main():
    g = RailwayGraph()
    g.build(CSV, JS)
    g.load_coords(COORDS)
    n = g.station_count
    idx_to_name = g.idx_to_station

    # ── 1. 统计量 ──
    zero_dist_edges = sum(
        1 for trains in g.edge_trains.values() for te in trains if te.distance <= 0)
    stats = {
        "stations": n,
        "edge_count": g.edge_count,               # 唯一有向边数
        "train_edges": sum(len(v) for v in g.edge_trains.values()),
        "departures": sum(len(v) for v in g.departures.values()),
        "transfer_edges": g.transfer_count,       # 有向同城换乘边对
        "transfer_edge_set": len(g.transfer_edge_set),
        "same_city_nonempty": sum(1 for v in g.same_city_of.values() if v),
        "same_city_neighbors": sum(len(v) for v in g.same_city_of.values()),
        "city_covered_stations": len(g.station_to_city_code),
        "city_groups": len(g.city_groups),
        "sorted_conns": len(g.sorted_connections),
        "out_conns_buckets": len(g.out_conns),
        "out_conns_nonempty": sum(1 for b in g.out_conns if b),
        "zero_dist_edges": zero_dist_edges,
    }
    # 桶内有序性（depart_minutes 升序）自检
    for b in g.out_conns:
        deps = [c[0] for c in b]
        assert deps == sorted(deps), "out_conns 桶内未按发车排序"
    # transfer 集合对称性自检
    for (a, b) in g.transfer_edges:
        assert (b, a) in g.transfer_edge_set, "transfer 不对称"
        assert a in g.same_city_of and b in g.same_city_of[a], "same_city_of 不一致"

    # ── 2. 抽样对拍（固定种子，可复现）──
    rng = random.Random(SEED)
    sample_stations = rng.sample(range(n), 300)

    same_city_sample = []      # [站A, 站B, is_same_city]
    transfer_time_sample = []  # [站A, 站B, 异站换乘分钟]
    for _ in range(300):
        a, b = rng.sample(range(n), 2)
        same_city_sample.append([idx_to_name[a], idx_to_name[b], g.is_same_city(a, b)])
        transfer_time_sample.append(
            [idx_to_name[a], idx_to_name[b], g.get_interstation_transfer_time(a, b)])

    edge_sample = []           # [from, to, min_time, distance, train_count]
    for _ in range(300):
        f = rng.choice(range(n))
        neigh = list(g.edges.get(f, {}))
        if not neigh:
            continue
        t = rng.choice(neigh)
        info = g.edges[f][t]
        edge_sample.append([idx_to_name[f], idx_to_name[t],
                            info.min_time, info.distance, info.train_count])

    # 反向 Dijkstra 下界：3 个随机目标 × 全站（distance + min_time）
    targets = rng.sample(range(n), 3)
    dist_lb = {}
    time_lb = {}
    for t in targets:
        name = idx_to_name[t]
        dist_lb[name] = {idx_to_name[s]: round(v, 1)
                         for s, v in g.get_multi_source_distances([t]).items()}
        time_lb[name] = {idx_to_name[s]: round(v, 1)
                         for s, v in g.get_multi_source_times([t]).items()}

    payload = {
        "seed": SEED,
        "stats": stats,
        "same_city_sample": same_city_sample,
        "transfer_time_sample": transfer_time_sample,
        "edge_sample": edge_sample,
        "dist_lb": dist_lb,
        "time_lb": time_lb,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(json.dumps(stats, indent=1, ensure_ascii=False))
    print(f"\n对拍基准已写入 {OUT}")


if __name__ == "__main__":
    main()
