# -*- coding: utf-8 -*-
"""Rust M4 对拍基准生成器（HTTP API 响应结构）。

对每个查询组合执行与 src/main.py APIHandler._search/_match/_train 完全相同的
流程（build_search_request → csa_search → score_routes → typed_route_to_dict），
dump 完整 API payload（含错误路径的 status/error），Rust 侧逐字段对比。

用法: python rust/tools/dump_m4.py [输出路径]
默认输出: rust/tools/m4_baseline.json
"""
import json
import os
import sys
import time

# pyref：Rust 对拍的 Python 参考实现（master 的 src/ 子集）
sys.path.insert(0, os.path.dirname(__file__))
from pyref.csa import search as csa_search  # noqa: E402
from pyref.graph import RailwayGraph  # noqa: E402
from pyref.main import score_routes, typed_route_to_dict  # noqa: E402
from pyref.matcher import build_matcher, fuzzy_match  # noqa: E402
from pyref.models import format_absolute_minutes  # noqa: E402
from pyref.validation import RequestValidationError, build_search_request  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CSV = os.path.join(ROOT, "data", "output", "车次时刻表.csv")
JS = os.path.join(ROOT, "data", "timetable", "station_name.js")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "m4_baseline.json")

# 查询组合（代表性：大城市/小站/约束/时间窗/每端模式/错误路径）
SEARCH_CASES = [
    dict(from_="北京南", to="上海虹桥"),
    dict(from_="北京", to="上海", search_profile="fast"),
    dict(from_="北京", to="上海", search_profile="complete"),
    dict(from_="乌鲁木齐", to="北京西", from_mode="exact", to_mode="exact"),
    dict(from_="广州", to="长沙", dep_after="12:00", dep_before="18:00"),
    dict(from_="哈尔滨", to="北京", arr_before="06:00"),
    dict(from_="武汉", to="郑州", transfer_city="西安"),
    dict(from_="北京", to="上海", max_transfers=2, search_profile="thorough"),
    dict(from_="深圳东", to="三明", search_profile="thorough"),
    dict(from_="燕郊", to="北京"),
    dict(from_="怀柔", to="北京"),
    dict(from_="北京", to="北京"),
    dict(from_="拉萨", to="成都"),
    dict(from_="合肥", to="南京", same_transfer=30, inter_transfer=90),
    dict(from_="new_york", to="上海"),
    dict(from_="北京", to="上海", search_profile="unknown"),
    dict(to="上海"),
    dict(from_="北京南", to="上海虹桥", dep_after="25:00"),
    dict(from_="东莞", to="深圳", match_mode="exact"),
]

MATCH_CASES = ["北京", "上海虹桥", "怀柔", "xinzheng", "乌鲁木齐"]

TRAIN_CASES = ["G1", "K1620/K1621", "Z9818", "NONEXIST"]


def run_search(graph, matcher, flat):
    """复现 APIHandler._search 的完整流程（含错误路径）。"""
    t0 = time.time()
    try:
        request = build_search_request(flat)
    except RequestValidationError as ve:
        return {"status": 400, "error": {"code": ve.code, "message": ve.message}}
    try:
        response = csa_search(graph, request, matcher)
        scored = score_routes(list(response.routes))
        results = [typed_route_to_dict(r, s) for s, r in scored]
        return {
            "status": 200,
            "payload": {
                "routes": results,
                "time": round(time.time() - t0, 1),
                "source_stations": list(response.source_stations),
                "target_stations": list(response.target_stations),
                "complete": response.metadata.complete,
                "profile": response.metadata.profile,
                "scanned": response.metadata.scanned_connections,
                "generated": response.metadata.generated_states,
                "cached": False,
            },
        }
    except ValueError as e:
        msg = str(e)
        if msg.startswith("未找到匹配的车站"):
            return {"status": 400, "error": {"code": "STATION_NOT_FOUND", "message": msg}}
        return {"status": 500, "error": {"code": "INTERNAL_ERROR", "message": msg}}


def run_train(graph, code):
    stops = graph.train_stops.get(code)
    if not stops:
        return {"status": 404, "error": {"code": "NOT_FOUND", "message": "未找到车次 " + code}}

    def fmt(m):
        if m < 0:
            return None
        return {"minutes": m, "time": "%02d:%02d" % ((m // 60) % 24, m % 60),
                "day": m // 1440, "display": "%02d:%02d" % ((m // 60) % 24, m % 60)
                + (("+" + str(m // 1440)) if m >= 1440 else "")}

    idx_to_station = graph.idx_to_station
    return {"status": 200, "payload": {
        "code": code,
        "stops": [
            {"station": idx_to_station[s[0]], "depart": fmt(s[1]), "arrive": fmt(s[2]),
             "seq": s[3], "distance": s[4]}
            for s in stops
        ],
    }}


def main():
    print("构建图...", end=" ", flush=True)
    t0 = time.perf_counter()
    graph = RailwayGraph()
    graph.build(CSV, JS)
    matcher = build_matcher(graph, JS)
    print(f"{time.perf_counter() - t0:.1f}s")

    search_cases = []
    for i, kw in enumerate(SEARCH_CASES):
        flat = {k.replace("_", ""): v if isinstance(v, str) else str(v)
                for k, v in kw.items()}
        # from_ → from
        flat = {("from" if k == "from" else k): v for k, v in flat.items()}
        case = run_search(graph, matcher, flat)
        search_cases.append({"params": {k: str(v) for k, v in kw.items()},
                             **case})
        print(f"  search {i + 1}/{len(SEARCH_CASES)} status={case['status']}")

    match_cases = [{"q": q, "payload": {"matches": [m[1] for m in fuzzy_match(q, graph, matcher)[:15]]}}
                   for q in MATCH_CASES]
    train_cases = [{"code": c, **run_train(graph, c)} for c in TRAIN_CASES]

    payload = {"search": search_cases, "match": match_cases, "train": train_cases}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {OUT}（search {len(search_cases)} + match {len(match_cases)} + train {len(train_cases)}）")


if __name__ == "__main__":
    main()
