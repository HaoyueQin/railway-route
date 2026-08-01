# -*- coding: utf-8 -*-
"""Rust M3 对拍基准生成器（210 组合结果集）。

组合构造与 tools/qa_sweep.py 的 main() 完全一致（固定对 + 随机分层采样 +
极端参数），但组合列表由本脚本输出，Rust 侧直接消费——无需复现 Python
random 序列。每个组合输出:
  - resolve_station_set 结果（source/target 站列表，保序）
  - search 全部结果（route 完整字段序列化）
  - metadata（complete / stopped_reason / returned_routes）

用法: python rust/tools/dump_m3.py [输出路径]
默认输出: rust/tools/m3_baseline.json
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.csa import search  # noqa: E402
from src.graph import RailwayGraph  # noqa: E402
from src.matcher import build_matcher  # noqa: E402
from src.models import (  # noqa: E402
    InterstationTransferSegment,
    SearchRequest,
    TrainSegment,
)
# 组合定义与 qa_sweep 共享（固定对/随机池/极端参数）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from qa_sweep import PAIRS, RANDOM_STATIONS  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CSV = os.path.join(ROOT, "data", "output", "车次时刻表.csv")
JS = os.path.join(ROOT, "data", "timetable", "station_name.js")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "m3_baseline.json")
SEED = 42  # 与 qa_sweep 默认一致


def build_combos(graph, rng):
    """与 tools/qa_sweep.py main() 相同的组合构造（保持同步）。"""
    combos = [(*p, "balanced") for p in PAIRS]
    combos += [(*p, "fast") for p in PAIRS[:12]]
    combos += [(*p, "complete") for p in PAIRS[12:20]]
    all_stations = sorted(graph.station_to_idx.keys())
    rng.shuffle(all_stations)
    hot = [n for n in all_stations if len(graph.out_conns[graph.station_to_idx[n]]) >= 50]
    cold = [n for n in all_stations if len(graph.out_conns[graph.station_to_idx[n]]) < 50]
    for _ in range(130):
        pool = hot if rng.random() < 0.6 else cold
        frm, to = rng.sample(pool, 2)
        prof = rng.choice(["fast", "balanced", "balanced", "thorough"])
        combos.append((frm, to, prof))
    combos.append(("北京", "北京", "balanced"))
    combos += [
        ("北京南", "上海虹桥", "balanced", "仅直达", dict(max_transfers=0)),
        ("武汉", "郑州", "balanced", "指定经西安", dict(transfer_city_code="西安")),
        ("广州", "长沙", "balanced", "下午出发", dict(earliest_depart=12 * 60, latest_depart=18 * 60)),
        ("哈尔滨", "北京", "balanced", "夜间到达", dict(latest_arrive=6 * 60)),
        ("乌鲁木齐", "北京西", "balanced", "exact端点", dict(from_mode="exact", to_mode="exact")),
        ("北京", "上海", "thorough", "换乘上限2", dict(max_transfers=2)),
    ]
    return combos


def seg_to_json(s):
    if isinstance(s, TrainSegment):
        return {"k": "train", "c": s.train_code, "f": s.from_station, "t": s.to_station,
                "d": s.depart_minutes, "a": s.arrive_minutes,
                "tr": s.travel_minutes, "di": s.distance}
    assert isinstance(s, InterstationTransferSegment), type(s)
    return {"k": "inter", "f": s.from_station, "t": s.to_station,
            "s": s.start_minutes, "e": s.end_minutes, "m": s.transfer_minutes,
            "cc": s.city_code, "cn": s.city_name}


def route_to_json(r):
    return {
        "segs": [seg_to_json(s) for s in r.segments],
        "ao": r.actual_origin, "ad": r.actual_destination,
        "fd": r.first_departure, "fa": r.final_arrival,
        "tm": r.total_minutes, "rd": r.rail_distance,
        "tt": r.train_transfers, "it": r.interstation_transfers,
        "im": r.interstation_minutes, "tc": list(r.transfer_cities),
        "mc": r.matched_transfer_constraint,
    }


def main():
    print("构建图...", end=" ", flush=True)
    t0 = time.perf_counter()
    graph = RailwayGraph()
    graph.build(CSV, JS)
    matcher = build_matcher(graph, JS)
    print(f"{time.perf_counter() - t0:.1f}s")

    rng = random.Random(SEED)
    combos = build_combos(graph, rng)
    print(f"组合数: {len(combos)}")

    cases = []
    t_start = time.perf_counter()
    for i, c in enumerate(combos):
        frm, to = c[0], c[1]
        profile = c[2] if len(c) > 2 else "balanced"
        kw = c[4] if len(c) > 4 else {}
        req = SearchRequest(from_query=frm, to_query=to, search_profile=profile, **kw)
        resp = search(graph, req, matcher)
        case = {
            "from": frm, "to": to, "profile": profile, "kw": {k: v for k, v in kw.items()},
            "src": list(resp.source_stations), "tgt": list(resp.target_stations),
            "routes": [route_to_json(r) for r in resp.routes],
            "complete": resp.metadata.complete,
            "stopped": resp.metadata.stopped_reason,
            "returned": resp.metadata.returned_routes,
        }
        cases.append(case)
        if (i + 1) % 25 == 0:
            print(f"  ... {i + 1}/{len(combos)} ({time.perf_counter() - t_start:.0f}s)", flush=True)

    payload = {"seed": SEED, "cases": cases}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n{len(cases)} 组合已写入 {OUT}（用时 {time.perf_counter() - t_start:.0f}s）")


if __name__ == "__main__":
    main()
