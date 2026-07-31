"""乘客视角人工审视：生成人类可读的完整方案输出，供逐条分析。

与 qa_sweep 的断言不同：本工具不做任何"通过/失败"判断，只把每个
组合的真实输出（直达全部 + 换乘前若干条，含每段车次/时刻/等待/耗时/
跨夜标注）写成可读文本，由人逐条对照现实常识审视。

用法: python tools/manual_review.py [--batch 0|1|2|3] [--limit N]
  --batch 0-3: 每批约 40 组合，输出 tools/review_batch{N}.txt
"""
import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.csa import search
from src.graph import RailwayGraph
from src.matcher import build_matcher, resolve_station_set
from src.models import SearchRequest, TrainSegment

CSV = "data/output/车次时刻表.csv"
JS = "data/timetable/station_name.js"

PAIRS = [
    ("北京南", "上海虹桥"), ("北京", "上海"), ("广州南", "深圳北"),
    ("成都东", "重庆北"), ("西安北", "郑州东"), ("武汉", "长沙南"),
    ("杭州东", "南京南"), ("天津", "石家庄"), ("沈阳北", "长春"),
    ("济南西", "青岛北"), ("合肥南", "南昌西"), ("福州", "厦门北"),
    ("曲阜东", "泰山"), ("苏州", "无锡"), ("佛山", "广州"), ("东莞", "深圳"),
    ("廊坊", "天津"), ("保定", "石家庄"), ("嘉兴南", "杭州东"), ("常州", "镇江"),
    ("徐州东", "蚌埠南"), ("淮南东", "合肥"), ("湖州", "长兴"), ("新乡东", "郑州东"),
    ("哈尔滨", "海口"), ("乌鲁木齐", "北京"), ("昆明", "哈尔滨"),
    ("三亚", "乌鲁木齐"), ("拉萨", "成都"), ("西宁", "广州"), ("兰州", "厦门"),
    ("呼和浩特", "上海"), ("银川", "西安"), ("贵阳北", "北京西"),
    ("延安", "深圳北"), ("格尔木", "兰州"), ("大同", "太原"), ("桂林北", "广州南"),
    ("大理", "昆明"), ("张家界西", "长沙"), ("遵义", "重庆"), ("六盘水", "贵阳"),
    ("宝鸡", "兰州西"), ("汉中", "西安北"), ("酒泉", "嘉峪关"),
    ("伊春", "佳木斯"), ("满洲里", "海拉尔"), ("延吉", "长春"), ("丹东", "沈阳"),
    ("通辽", "赤峰"), ("和田", "喀什"), ("库尔勒", "乌鲁木齐"), ("海晏", "西宁"),
]


def clock(m):
    d, m = divmod(m, 1440)
    h, mi = divmod(m, 60)
    s = f"{h:02d}:{mi:02d}"
    return ("次日 " if d == 1 else f"第{d+1}日 " if d > 1 else "") + s


def seg_desc(s):
    if hasattr(s, "train_code"):
        return f"{s.train_code} {s.from_station}{clock(s.depart_minutes)}→{s.to_station}{clock(s.arrive_minutes)} 乘{s.travel_minutes}分"
    return f"地面 {s.from_station}→{s.to_station} {clock(s.start_minutes)}→{clock(s.end_minutes)} {s.transfer_minutes}分"


def fmt_route(r, idx):
    lines = [f"  {idx}) 总{r.total_minutes}分({r.total_minutes//60}h{r.total_minutes%60:02d}m) "
             f"{r.train_transfers}车换+{r.interstation_transfers}地 {r.rail_distance}km"]
    for i, s in enumerate(r.segments):
        lines.append(f"      └ {seg_desc(s)}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    graph = RailwayGraph()
    graph.build(csv_path=CSV, station_js_path=JS)
    matcher = build_matcher(graph, JS)

    rng = random.Random(42)
    all_stations = sorted(graph.station_to_idx.keys())
    rng.shuffle(all_stations)
    hot = [n for n in all_stations if len(graph.out_conns[graph.station_to_idx[n]]) >= 50]
    cold = [n for n in all_stations if len(graph.out_conns[graph.station_to_idx[n]]) < 50]

    combos = [(*p, "balanced") for p in PAIRS]
    combos += [(*p, "fast") for p in PAIRS[:12]]
    combos += [(*p, "complete") for p in PAIRS[12:20]]
    for _ in range(80):
        pool = hot if rng.random() < 0.6 else cold
        frm, to = rng.sample(pool, 2)
        prof = rng.choice(["fast", "balanced", "balanced", "thorough"])
        combos.append((frm, to, prof))
    combos += [
        ("北京", "北京", "balanced"),
        ("北京南", "上海虹桥", "balanced", dict(max_transfers=0)),
        ("武汉", "郑州", "balanced", dict(transfer_city_code="西安")),
        ("广州", "长沙", "balanced", dict(earliest_depart=720, latest_depart=1080)),
        ("哈尔滨", "北京", "balanced", dict(latest_arrive=360)),
        ("乌鲁木齐", "北京西", "balanced", dict(from_mode="exact", to_mode="exact")),
        ("北京", "上海", "thorough", dict(max_transfers=2)),
    ]
    if a.limit:
        combos = combos[:a.limit]
    B = 40
    batch = combos[a.batch * B:(a.batch + 1) * B]
    out = [f"══ 人工审视批次 {a.batch}（{len(batch)} 组合）══\n"]
    for n, combo in enumerate(batch):
        frm, to, prof = combo[0], combo[1], combo[2]
        kw = combo[3] if len(combo) > 3 else {}
        t0 = time.perf_counter()
        req = SearchRequest(from_query=frm, to_query=to, search_profile=prof, **kw)
        resp = search(graph, req, matcher)
        dt = time.perf_counter() - t0
        routes = resp.routes
        directs = [r for r in routes if r.train_transfers == 0 and r.interstation_transfers == 0]
        xfers = [r for r in routes if not (r.train_transfers == 0 and r.interstation_transfers == 0)]
        src_names = resolve_station_set(frm, kw.get("from_mode") or req.match_mode, graph, matcher)
        tgt_names = resolve_station_set(to, kw.get("to_mode") or req.match_mode, graph, matcher)
        out.append(f"\n{'─'*70}\n#{n+1:03d} {frm} → {to} [{prof}]"
                   f"{' ' + str(kw) if kw else ''}  {dt:.1f}s")
        out.append(f"  解析: 出发站 {src_names} | 到达站 {tgt_names}")
        if not routes:
            out.append("  ⚠ 无任何方案（complete=%s）" % resp.metadata.complete)
            continue
        if directs:
            best = min(directs, key=lambda r: r.total_minutes)
            out.append(f"  直达 {len(directs)} 条（最快 {best.total_minutes//60}h{best.total_minutes%60:02d}m "
                       f"{best.segments[0].train_code} {clock(best.first_departure)}→{clock(best.final_arrival)}）:")
            for i, r in enumerate(directs[:30]):
                out.append(f"     {i+1:2d}) {r.segments[0].train_code} "
                           f"{clock(r.first_departure)}→{clock(r.final_arrival)} "
                           f"{r.total_minutes//60}h{r.total_minutes%60:02d}m {r.rail_distance}km")
            if len(directs) > 30:
                out.append(f"     … 共 {len(directs)} 条")
        if xfers:
            out.append(f"  换乘 {len(xfers)} 条（最快 {xfers[0].total_minutes//60}h{xfers[0].total_minutes%60:02d}m）:")
            for i, r in enumerate(xfers[:6]):
                out.append(fmt_route(r, i + 1))
            if len(xfers) > 6:
                out.append(f"     … 共 {len(xfers)} 条")
    p = Path(__file__).resolve().parent.parent / f"tools/review_batch{a.batch}.txt"
    p.write_text("\n".join(out), encoding="utf-8")
    print(f"已生成 {p}")


if __name__ == "__main__":
    main()
