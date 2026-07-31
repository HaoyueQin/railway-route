"""性能剖析脚本：图构建 + 各档搜索的耗时分解（用于优化前后对比）。

用法:
  python tools/benchmark.py [--profile-import]
"""
import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.csa import search as csa_search
from src.graph import RailwayGraph
from src.matcher import build_matcher
from src.validation import build_search_request

CSV = "data/output/车次时刻表.csv"
JS = "data/timetable/station_name.js"

QUERIES = [
    ("exact-fast-京沪", {"from": "北京南", "to": "上海虹桥", "match_mode": "exact", "search_profile": "fast"}),
    ("exact-balanced-京沪", {"from": "北京南", "to": "上海虹桥", "match_mode": "exact", "search_profile": "balanced"}),
    ("fuzzy-balanced-京沪", {"from": "北京", "to": "上海", "match_mode": "fuzzy", "search_profile": "balanced"}),
    ("fuzzy-complete-延深", {"from": "延安", "to": "深圳北", "match_mode": "fuzzy", "search_profile": "complete", "timeout": "60"}),
]


def build_timings():
    t0 = time.perf_counter()
    graph = RailwayGraph()
    graph.build(csv_path=CSV, station_js_path=JS)
    t1 = time.perf_counter()
    matcher = build_matcher(graph, JS)
    t2 = time.perf_counter()
    print(f"图构建: {t1-t0:.3f}s | matcher: {t2-t1:.3f}s | 总计: {t2-t0:.3f}s")
    print(f"  节点 {graph.station_count} · 唯一边 {graph.edge_count} · "
          f"车次区间 {sum(len(v) for v in graph.edge_trains.values())} · "
          f"双日连接 {len(graph.sorted_connections)} · 同城对 {len(graph.transfer_edges)}")
    return graph, matcher


def run_queries(graph, matcher, queries=None):
    for name, params in (queries or QUERIES):
        request = build_search_request(params)
        t0 = time.perf_counter()
        resp = csa_search(graph, request, matcher)
        dt = time.perf_counter() - t0
        m = resp.metadata
        print(f"[{name:24s}] {dt:6.2f}s | 路线 {len(resp.routes):5d} | "
              f"扫描 {m.scanned_connections:7d} | 生成状态 {m.generated_states:8d} | "
              f"complete={m.complete}")


def profile_one(graph, matcher, name="exact-fast-京沪"):
    request = build_search_request(dict(QUERIES[0][1]))
    pr = cProfile.Profile()
    pr.enable()
    csa_search(graph, request, matcher)
    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(22)
    print(s.getvalue())


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    graph, matcher = build_timings()
    if mode == "profile":
        profile_one(graph, matcher)
    else:
        run_queries(graph, matcher)
