#!/usr/bin/env python3
"""对比 pyref（连接级）与路线级 Rust 的路线 key 集合，输出差异模式。

用法: python rust/tools/compare_route_sets.py <rust_json> <from> <to> <profile>
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pyref"))
from pyref.csa import search
from pyref.graph import RailwayGraph
from pyref.matcher import build_matcher
from pyref.models import SearchRequest, TrainSegment

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CSV = os.path.join(ROOT, "data", "output", "车次时刻表.csv")
JS = os.path.join(ROOT, "data", "timetable", "station_name.js")
COORDS = os.path.join(ROOT, "data", "station_coords.json")


def route_key(segs):
    """从响应 segments 重建 route key（与 route_key_str 同构）。"""
    parts = []
    for s in segs:
        kind = s.get("type") or s.get("kind")
        if kind == "train":
            dep = s["depart"]["minutes"] if isinstance(s["depart"], dict) else s["depart"]
            arr = s["arrive"]["minutes"] if isinstance(s["arrive"], dict) else s["arrive"]
            code = s["train_code"]
            parts.append(f"train|{code}|{s['from_station']}|{s['to_station']}|{dep}|{arr}")
        else:
            dep2 = s.get("start") or s.get("depart")
            arr2 = s.get("end") or s.get("arrive")
            dep = dep2["minutes"] if isinstance(dep2, dict) else dep2
            arr = arr2["minutes"] if isinstance(arr2, dict) else arr2
            parts.append(f"inter|{s['from_station']}|{s['to_station']}|{dep}|{arr}")
    return "::".join(parts)


def main():
    rust_json = sys.argv[1]
    frm, to, prof = sys.argv[2], sys.argv[3], sys.argv[4]
    rust = json.load(open(rust_json, encoding="utf-8"))
    rust_keys = {route_key(r["segments"]) for r in rust["routes"]}

    g = RailwayGraph()
    g.build(CSV, JS)
    g.load_coords(COORDS)
    matcher = build_matcher(g, JS)
    # 与 Rust 一致：from_mode/to_mode 取 match_mode 默认（fuzzy），profile 独立
    req = SearchRequest(frm, to, "fuzzy", search_profile=prof)
    resp = search(g, req, matcher)
    py_keys = set()
    for seg in resp.routes:
        segs = []
        for seg in seg.segments:
            if isinstance(seg, TrainSegment):
                segs.append({"kind": "train", "train_code": seg.train_code,
                             "from_station": seg.from_station, "to_station": seg.to_station,
                             "depart": seg.depart_minutes, "arrive": seg.arrive_minutes})
            else:
                segs.append({"kind": "inter", "from_station": seg.from_station, "to_station": seg.to_station,
                             "depart": seg.start_minutes, "arrive": seg.end_minutes})
        py_keys.add(route_key(segs))

    only_py = sorted(py_keys - rust_keys)
    only_rust = sorted(rust_keys - py_keys)
    common = py_keys & rust_keys
    print(f"py={len(py_keys)} rust={len(rust_keys)} common={len(common)} "
          f"only_py={len(only_py)} only_rust={len(only_rust)}")
    print("── py 独有（前 8 条）──")
    for k in only_py[:8]:
        print(" ", k[:200])
    print("── rust 独有（前 5 条）──")
    for k in only_rust[:5]:
        print(" ", k[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
