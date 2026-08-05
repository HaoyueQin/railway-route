#!/usr/bin/env python3
"""pyref 热点分析：对代表查询做 cProfile，输出耗时 Top 函数。

用法: python rust/tools/profile_pyref.py [--profile balanced] [--top 25]
"""
import argparse
import cProfile
import io
import os
import pstats
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pyref"))
from pyref.csa import search
from pyref.graph import RailwayGraph
from pyref.matcher import build_matcher
from pyref.models import SearchRequest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CSV = os.path.join(ROOT, "data", "output", "车次时刻表.csv")
JS = os.path.join(ROOT, "data", "timetable", "station_name.js")
COORDS = os.path.join(ROOT, "data", "station_coords.json")

QUERIES = [
    ("北京南", "上海虹桥", "balanced"),
    ("北京", "上海", "balanced"),
    ("延安", "深圳北", "complete"),
    ("乌鲁木齐", "北京", "balanced"),
    ("哈尔滨", "海口", "thorough"),
    ("昆明", "哈尔滨", "balanced"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="balanced")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    print("构建图...", end=" ", flush=True)
    t0 = time.perf_counter()
    g = RailwayGraph()
    g.build(CSV, JS)
    g.load_coords(COORDS)
    matcher = build_matcher(g, JS)
    print(f"{time.perf_counter() - t0:.1f}s")

    for frm, to, prof in QUERIES:
        if prof != args.profile and args.profile != "all":
            continue
        req = SearchRequest(frm, to, search_profile=prof)
        print(f"\n=== {frm} → {to} ({prof}) ===")
        t0 = time.perf_counter()
        resp = search(g, req, matcher)
        print(f"耗时 {time.perf_counter() - t0:.2f}s  结果 {len(resp.routes)}  "
              f"complete={resp.metadata.complete}  scanned={resp.metadata.scanned_connections}  "
              f"generated={resp.metadata.generated_states}")

        pr = cProfile.Profile()
        pr.enable()
        resp = search(g, req, matcher)
        pr.disable()
        s = io.StringIO()
        ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
        ps.print_stats(args.top)
        lines = s.getvalue().splitlines()
        # 跳过文件头 5 行，保留函数表
        for ln in lines[5:]:
            if "pyref" in ln or "built-in" in ln:
                print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
