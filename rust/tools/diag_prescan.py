#!/usr/bin/env python3
"""pyref 单查询诊断：打印 prescan 早停前后的 best_by 与主循环统计。

用法: python rust/tools/diag_prescan.py 武汉 长沙南 balanced
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pyref"))
import pyref.csa as csa_mod
from pyref.csa import search
from pyref.graph import RailwayGraph
from pyref.matcher import build_matcher
from pyref.models import SearchRequest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CSV = os.path.join(ROOT, "data", "output", "车次时刻表.csv")
JS = os.path.join(ROOT, "data", "timetable", "station_name.js")
COORDS = os.path.join(ROOT, "data", "station_coords.json")

frm = sys.argv[1] if len(sys.argv) > 1 else "武汉"
to = sys.argv[2] if len(sys.argv) > 2 else "长沙南"
prof = sys.argv[3] if len(sys.argv) > 3 else "balanced"

g = RailwayGraph()
g.build(CSV, JS)
g.load_coords(COORDS)
matcher = build_matcher(g, JS)
req = SearchRequest(frm, to, search_profile=prof)

# 1) prescan 早停诊断：monkey-patch 打印 best_by
orig_prescan = csa_mod._prescan_best_durations


def traced(graph, request, source_set, target_set):
    t0 = time.perf_counter()
    result = orig_prescan(graph, request, source_set, target_set)
    print(f"[diag] prescan: {time.perf_counter()-t0:.3f}s best_by={list(result)}")
    return result


csa_mod._prescan_best_durations = traced

t0 = time.perf_counter()
resp = search(g, req, matcher)
print(f"[diag] search: {time.perf_counter()-t0:.2f}s routes={len(resp.routes)} "
      f"complete={resp.metadata.complete} scanned={resp.metadata.scanned_connections} "
      f"generated={resp.metadata.generated_states}")
