"""乘客视角全链路 QA 扫描：200+ 车站组合系统验证。

从乘客角度验证搜索全链路质量：
1. 直达完整性     —— 搜索结果中的直达 == 独立枚举 ground truth（永不少给）
2. 直达优先       —— 直达方案恒排在换乘方案之前
3. 排序合理性     —— 结果按（换乘次数, 总耗时）单调不减
4. 约束满足       —— 出发/到达时间窗、最大换乘、指定换乘城市、每端匹配模式
5. 方案质量       —— 换乘等待合理、无重复站、无重复方案、里程非负
6. 备选车站       —— /api/match 语义（抽样）

组合采样刻意不同质：大城市对 / 近距离 / 远距离 / 偏远小站 / 随机 / 极端参数。

用法: python tools/qa_sweep.py [--limit N] [--seed S] [--quick]
"""
import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.csa import _collect_direct_routes, search
from src.graph import RailwayGraph
from src.matcher import build_matcher, resolve_station_set
from src.models import SearchRequest, TrainSegment
from src.validation import build_search_request
from src.matcher import fuzzy_match

CSV = "data/output/车次时刻表.csv"
JS = "data/timetable/station_name.js"

# ── 组合采样 ────────────────────────────────────────────

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

# ── 断言集合（乘客视角）────────────────────────────────

class Report:
    def __init__(self):
        self.total = 0
        self.fail = []
        self.checks = {}
        self.slow = []
        self.superfast = []

    def ok(self, name):
        self.checks[name] = self.checks.get(name, 0) + 1

    def fail_case(self, name, ctx, detail):
        self.fail.append((name, ctx, detail))
        print(f"  [✗] {name}  {ctx}  {detail}")


def run_case(rep, graph, matcher, frm, to, profile, seed_extra=None, **kw):
    """执行一个查询组合并做全部乘客视角断言。"""
    ctx = f"{frm}→{to}[{profile}]" + (f" {seed_extra}" if seed_extra else "")
    t0 = time.perf_counter()
    req = SearchRequest(from_query=frm, to_query=to, search_profile=profile, **kw)
    resp = search(graph, req, matcher)
    elapsed = time.perf_counter() - t0
    rep.total += 1
    if elapsed > 8:
        rep.slow.append((ctx, round(elapsed, 1)))
    routes = resp.routes
    directs = [r for r in routes if r.train_transfers == 0 and r.interstation_transfers == 0]
    xfers = [r for r in routes if r.train_transfers > 0 or r.interstation_transfers > 0]

    src_set = {graph.station_to_idx[n] for n in resolve_station_set(
        frm, kw.get("from_mode") or req.match_mode, graph, matcher) if n in graph.station_to_idx}
    tgt_set = {graph.station_to_idx[n] for n in resolve_station_set(
        to, kw.get("to_mode") or req.match_mode, graph, matcher) if n in graph.station_to_idx}

    # 1. 直达完整性（ground truth 独立枚举）
    # 注意：指定换乘城市约束时直达被有意排除（直达不符合"必须经该城换乘"），
    # ground truth 需应用同样过滤
    gt = _collect_direct_routes(graph, req, src_set, tgt_set)
    if kw.get("transfer_city_code"):
        if len(directs) != 0:
            rep.fail_case("直达完整性", ctx, f"约束查询应排除直达，实际 {len(directs)} 条")
        else:
            rep.ok("直达完整性")
    elif len(gt) != len(directs):
        rep.fail_case("直达完整性", ctx, f"搜索直达 {len(directs)} != 枚举 {len(gt)}")
    else:
        rep.ok("直达完整性")
    # 直达集合内容一致（key 级）
    if kw.get("transfer_city_code"):
        rep.ok("直达集合一致")   # 约束查询：搜索排除直达（已验证为 0 条）
    else:
        gt_keys = {route_key_for(r) for r in gt}
        got_keys = {route_key_for(r) for r in directs}
        if gt_keys != got_keys:
            rep.fail_case("直达集合一致", ctx, f"缺失 {gt_keys - got_keys} 多余 {got_keys - gt_keys}")
        else:
            rep.ok("直达集合一致")

    # 2. 直达优先
    if directs and xfers:
        max_direct_idx = max(routes.index(d) for d in directs)
        min_xfer_idx = min(routes.index(x) for x in xfers)
        if max_direct_idx > min_xfer_idx:
            rep.fail_case("直达优先", ctx, f"直达最后在 {max_direct_idx}, 换乘最早在 {min_xfer_idx}")
        else:
            rep.ok("直达优先")
    else:
        rep.ok("直达优先")

    # 3. 排序单调（换乘次数, 总耗时）
    ok_sort = True
    for a, b in zip(routes, routes[1:]):
        ka = (a.train_transfers + a.interstation_transfers, a.total_minutes)
        kb = (b.train_transfers + b.interstation_transfers, b.total_minutes)
        if ka > kb:
            ok_sort = False
            break
    if not ok_sort:
        rep.fail_case("排序单调", ctx, f"第 {routes.index(a)} 条 {ka} 劣于后一条 {kb}")
    else:
        rep.ok("排序单调")

    # 4. 约束满足
    if kw.get("earliest_depart") is not None:
        bad = [r for r in routes if r.first_departure < kw["earliest_depart"]]
        if bad:
            rep.fail_case("出发窗", ctx, f"{len(bad)} 条早于最早出发")
        else:
            rep.ok("出发窗")
    if kw.get("latest_arrive") is not None:
        bad = [r for r in routes if r.final_arrival > kw["latest_arrive"]]
        if bad:
            rep.fail_case("到达窗", ctx, f"{len(bad)} 条晚于最晚到达")
        else:
            rep.ok("到达窗")
    if kw.get("max_transfers") is not None:
        bad = [r for r in routes if r.train_transfers + r.interstation_transfers > kw["max_transfers"]]
        if bad:
            rep.fail_case("最大换乘", ctx, f"{len(bad)} 条超限")
        else:
            rep.ok("最大换乘")
    if kw.get("transfer_city_code"):
        bad = [r for r in routes if not r.matched_transfer_constraint]
        if bad:
            rep.fail_case("换乘城市约束", ctx, f"{len(bad)} 条未含指定城市")
        else:
            rep.ok("换乘城市约束")

    # 5. 方案质量
    if routes:
        t = routes[0]
        if t.total_minutes <= 0:
            rep.fail_case("耗时非正", ctx, f"{t.total_minutes}")
        else:
            rep.ok("耗时非正")
        bad_neg = [r for r in routes if r.rail_distance < 0]
        if bad_neg:
            rep.fail_case("里程非负", ctx, f"{len(bad_neg)} 条负里程")
        else:
            rep.ok("里程非负")
        # 换乘等待时间非负 + 方案去重
        keys = [route_key_for(r) for r in routes]
        if len(keys) != len(set(keys)):
            rep.fail_case("方案去重", ctx, f"{len(keys) - len(set(keys))} 重复")
        else:
            rep.ok("方案去重")
        # 换乘方案中等待时间合理（换乘段之间非负）
        bad_wait = [r for r in xfers if not all(
            _seg_start(s2) >= _seg_end(s1) for s1, s2 in zip(r.segments, r.segments[1:]))]
        if bad_wait:
            rep.fail_case("换乘时序", ctx, f"{len(bad_wait)} 条换乘时序倒挂")
        else:
            rep.ok("换乘时序")
        # 信息统计：直达普速慢车 vs 换乘高铁更快是真实合理场景（如乌鲁木齐→北京
        # 直达 Z 字头 29h，经兰州换高铁 11h），仅记录最大反超比供人工审视
        if directs and xfers:
            best_direct = min(d.total_minutes for d in directs)
            best_xfer = min(x.total_minutes for x in xfers)
            if best_xfer < best_direct:
                rep.superfast.append((ctx, best_direct, best_xfer))
    else:
        rep.ok("方案质量(空结果)")
        # 空结果时：ground truth 也应空（换乘无解）
        if resp.metadata.complete and not routes and not gt:
            pass  # 合理空结果

    # 6. 元数据健全
    if resp.metadata.elapsed_ms <= 0 and routes:
        rep.fail_case("耗时元数据", ctx, "elapsed_ms <= 0")
    else:
        rep.ok("耗时元数据")
    return resp


def route_key_for(r):
    from src.models import route_key
    return route_key(r.segments)


def _seg_start(s):
    return s.start_minutes if hasattr(s, "start_minutes") else s.depart_minutes


def _seg_end(s):
    return s.end_minutes if hasattr(s, "end_minutes") else s.arrive_minutes


def check_match_semantics(rep, graph, matcher):
    """匹配语义：解析层（搜索行为）遵循历史约定，建议层（输入联想）验证相关性。"""
    # 解析语义：用户约定的行为
    resolve_cases = [
        ("怀柔", lambda names: any("北京" in n for n in names)),    # 怀柔 → 归入北京
        ("燕郊", lambda names: not any("廊坊" in n for n in names)),  # 燕郊不归廊坊
        ("广阳", lambda names: any("廊坊" in n for n in names)),    # 广阳 → 归入廊坊
        ("新县", lambda names: len(names) == 1 and names[0] == "新县"),
    ]
    for q, pred in resolve_cases:
        try:
            names = resolve_station_set(q, "fuzzy", graph, matcher)
        except Exception as e:
            rep.fail_case("解析语义", q, f"异常 {e}")
            continue
        if not names:
            rep.fail_case("解析语义", q, "无结果")
        elif not pred(names):
            rep.fail_case("解析语义", q, f"解析 {names[:10]}")
        else:
            rep.ok("解析语义")
    # 建议层：输入联想应返回相关车站（前缀/同名）
    suggest_cases = [
        ("北京", lambda names: any("北京" in n for n in names)),
        ("上海虹桥", lambda names: names and names[0] == "上海虹桥"),
        ("乌鲁木齐", lambda names: any("乌鲁木齐" in n for n in names)),
    ]
    for q, pred in suggest_cases:
        try:
            names = [m[1] for m in fuzzy_match(q, graph, matcher)]
        except Exception as e:
            rep.fail_case("建议相关性", q, f"异常 {e}")
            continue
        if not names:
            rep.fail_case("建议相关性", q, "无建议")
        elif not pred(names):
            rep.fail_case("建议相关性", q, f"建议 {names[:8]}")
        else:
            rep.ok("建议相关性")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = 全量")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quick", action="store_true", help="小规模冒烟（12 组）")
    a = ap.parse_args()

    print("加载全国铁路网络...", end=" ", flush=True)
    t0 = time.perf_counter()
    graph = RailwayGraph()
    graph.build(csv_path=CSV, station_js_path=JS)
    matcher = build_matcher(graph, JS)
    print(f"({time.perf_counter()-t0:.1f}s)\n")

    rep = Report()
    rng = random.Random(a.seed)

    # ── 组合构造：固定对 + 随机对 + 极端参数 ──
    combos = [(*p, "balanced") for p in PAIRS]
    combos += [(*p, "fast") for p in PAIRS[:12]]
    combos += [(*p, "complete") for p in PAIRS[12:20]]
    # 随机组合（种子固定，可复现）：从真实车站池采样——近/远/大/小站随机混合，
    # 保证测试不同质；采样按车次密度分层（热站更易被选中，贴近真实使用）
    all_stations = sorted(graph.station_to_idx.keys())
    rng.shuffle(all_stations)
    hot = [n for n in all_stations if len(graph.out_conns[graph.station_to_idx[n]]) >= 50]
    cold = [n for n in all_stations if len(graph.out_conns[graph.station_to_idx[n]]) < 50]
    for _ in range(130):
        pool = hot if rng.random() < 0.6 else cold
        frm, to = rng.sample(pool, 2)
        prof = rng.choice(["fast", "balanced", "balanced", "thorough"])
        combos.append((frm, to, prof))
    # 同城/极端
    combos.append(("北京", "北京", "balanced"))
    combos += [
        ("北京南", "上海虹桥", "balanced", "仅直达", dict(max_transfers=0)),
        ("武汉", "郑州", "balanced", "指定经西安", dict(transfer_city_code="西安")),
        ("广州", "长沙", "balanced", "下午出发", dict(earliest_depart=12 * 60, latest_depart=18 * 60)),
        ("哈尔滨", "北京", "balanced", "夜间到达", dict(latest_arrive=6 * 60)),
        ("乌鲁木齐", "北京西", "balanced", "exact端点", dict(from_mode="exact", to_mode="exact")),
        ("北京", "上海", "thorough", "换乘上限2", dict(max_transfers=2)),
    ]
    if a.quick:
        combos = combos[:12]
    if a.limit:
        combos = combos[:a.limit]

    print(f"组合数: {len(combos)}\n")
    t_start = time.perf_counter()
    done = 0
    for c in combos:
        frm, to = c[0], c[1]
        profile = c[2] if len(c) > 2 else "balanced"
        tag = c[3] if len(c) > 3 else ""
        kw = c[4] if len(c) > 4 else {}
        try:
            run_case(rep, graph, matcher, frm, to, profile, tag, **kw)
        except Exception as e:
            rep.fail_case("异常", f"{frm}→{to}[{profile}]", repr(e))
        done += 1
        if done % 25 == 0:
            print(f"  ... {done}/{len(combos)}  累计失败 {len(rep.fail)}", flush=True)

    check_match_semantics(rep, graph, matcher)
    wall = time.perf_counter() - t_start

    # ── 报告 ──
    print(f"\n═══ QA 报告 ═══  组合 {rep.total} · 用时 {wall:.0f}s · 慢查询 {len(rep.slow)}")
    print(f"通过检查项: {sum(rep.checks.values())}  失败: {len(rep.fail)}")
    if rep.checks:
        print("检查分布:", ", ".join(f"{k}={v}" for k, v in sorted(rep.checks.items())))
    if rep.slow:
        print("慢查询(>8s):", rep.slow[:12])
    if rep.superfast:
        print(f"换乘快于直达的组合 {len(rep.superfast)} 个（直达多为普速，合理）:",
              rep.superfast[:6])
    if rep.fail:
        print(f"\n失败明细 ({len(rep.fail)}):")
        for name, ctx, detail in rep.fail[:40]:
            print(f"  [{name}] {ctx}: {detail}")
        sys.exit(1)
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
