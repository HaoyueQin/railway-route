# -*- coding: utf-8 -*-
"""Rust M3 对拍基准生成器（210 组合结果集）。

组合构造与 master 分支 tools/qa_sweep.py 的 main() 完全一致（固定对 + 随机
分层采样 + 极端参数），但组合列表由本脚本输出，Rust 侧直接消费——无需复现
Python random 序列。每个组合输出:
  - resolve_station_set 结果（source/target 站列表，保序）
  - search 全部结果（route 完整字段序列化）
  - metadata（complete / stopped_reason / returned_routes）

参考实现: 本目录 pyref/（master 分支 src/ 的对拍用子集，分支间同步）。

用法: python rust/tools/dump_m3.py [输出路径]
默认输出: rust/tools/m3_baseline.json
"""
import json
import os
import random
import sys
import time

# pyref：Rust 对拍的 Python 参考实现（master 的 src/ 子集）
sys.path.insert(0, os.path.dirname(__file__))
from pyref.csa import search  # noqa: E402
from pyref.graph import RailwayGraph  # noqa: E402
from pyref.matcher import build_matcher  # noqa: E402
from pyref.models import (  # noqa: E402
    InterstationTransferSegment,
    SearchRequest,
    TrainSegment,
)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CSV = os.path.join(ROOT, "data", "output", "车次时刻表.csv")
JS = os.path.join(ROOT, "data", "timetable", "station_name.js")
COORDS = os.path.join(ROOT, "data", "station_coords.json")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "m3_baseline.json")
SEED = 42  # 与 qa_sweep 默认一致

# ── 组合采样（与 master tools/qa_sweep.py 的 PAIRS/RANDOM_STATIONS 保持同步）──

PAIRS = [
    # 大城市对
    ("北京南", "上海虹桥"), ("北京", "上海"), ("广州南", "深圳北"),
    ("成都东", "重庆北"), ("西安北", "郑州东"), ("武汉", "长沙南"),
    ("杭州东", "南京南"), ("天津", "石家庄"), ("沈阳北", "长春"),
    ("济南西", "青岛北"), ("合肥南", "南昌西"), ("福州", "厦门北"),
    # 近距离
    ("曲阜东", "泰山"), ("苏州", "无锡"), ("佛山", "广州"), ("东莞", "深圳"),
    ("廊坊", "天津"), ("保定", "石家庄"), ("嘉兴南", "杭州东"), ("常州", "镇江"),
    ("徐州东", "蚌埠南"), ("淮南东", "合肥"), ("湖州", "长兴"), ("新乡东", "郑州东"),
    # 远距离
    ("哈尔滨", "海口"), ("乌鲁木齐", "北京"), ("昆明", "哈尔滨"),
    ("三亚", "乌鲁木齐"), ("拉萨", "成都"), ("西宁", "广州"), ("兰州", "厦门"),
    ("呼和浩特", "上海"), ("银川", "西安"), ("贵阳北", "北京西"),
    # 中西部 / 偏远
    ("延安", "深圳北"), ("格尔木", "兰州"), ("大同", "太原"), ("桂林北", "广州南"),
    ("大理", "昆明"), ("张家界西", "长沙"), ("遵义", "重庆"), ("六盘水", "贵阳"),
    ("宝鸡", "兰州西"), ("汉中", "西安北"), ("酒泉", "嘉峪关"),
    # 小站 / 低密度
    ("伊春", "佳木斯"), ("满洲里", "海拉尔"), ("延吉", "长春"), ("丹东", "沈阳"),
    ("通辽", "赤峰"), ("和田", "喀什"), ("库尔勒", "乌鲁木齐"), ("海晏", "西宁"),
]

RANDOM_STATIONS = [
    "北京", "上海", "广州", "深圳", "成都", "重庆", "西安", "郑州", "武汉", "长沙",
    "杭州", "南京", "天津", "石家庄", "沈阳", "长春", "哈尔滨", "济南", "青岛",
    "合肥", "南昌", "福州", "厦门", "昆明", "贵阳", "南宁", "海口", "兰州",
    "西宁", "银川", "呼和浩特", "乌鲁木齐", "拉萨", "太原", "大连", "苏州",
    "无锡", "常州", "徐州", "温州", "宁波", "绍兴", "台州", "金华", "嘉兴",
    "湖州", "衢州", "丽水", "黄山", "芜湖", "蚌埠", "安庆", "阜阳", "六安",
    "淮南", "滁州", "马鞍山", "铜陵", "宣城", "池州", "曲阜", "泰山", "潍坊",
    "烟台", "威海", "日照", "临沂", "淄博", "东营", "聊城", "德州", "滨州",
    "菏泽", "济宁", "枣庄", "泰州", "扬州", "镇江", "南通", "盐城", "淮安",
    "连云港", "宿迁", "邯郸", "邢台", "沧州", "衡水", "张家口", "承德", "秦皇岛",
    "唐山", "保定", "廊坊", "通辽", "赤峰", "呼伦贝尔", "乌兰察布", "巴彦淖尔",
    "鄂尔多斯", "包头", "临河", "乌海", "庆阳", "平凉", "天水", "陇南",
    "定西", "临夏", "甘南", "武威", "金昌", "张掖", "酒泉", "嘉峪关", "玉门",
    "敦煌", "哈密", "吐鲁番", "昌吉", "石河子", "奎屯", "伊宁", "博乐",
    "塔城", "阿勒泰", "阿克苏", "阿图什", "喀什", "和田", "库尔勒", "拉萨",
    "日喀则", "林芝", "昌都", "山南", "那曲", "遵义", "安顺", "毕节", "铜仁",
    "六盘水", "黔东南", "黔南", "黔西南", "大理", "丽江", "迪庆", "保山",
    "德宏", "临沧", "普洱", "西双版纳", "红河", "文山", "楚雄", "玉溪", "昭通",
    "曲靖", "桂林", "柳州", "梧州", "北海", "防城港", "钦州", "贵港", "玉林",
    "百色", "贺州", "河池", "来宾", "崇左", "海口", "三亚", "儋州", "琼海",
    "万宁", "东方", "文昌", "陵水", "澄迈", "定安", "屯昌", "保亭", "琼中",
    "乐东", "临高", "白沙", "昌江", "五指山",
]


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
    graph.load_coords(COORDS)
    matcher = build_matcher(graph, JS)
    print(f"{time.perf_counter() - t0:.1f}s（坐标 {len(graph.coords)} 站 / 同城对 {len(graph.interstation_minutes)}）")

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
