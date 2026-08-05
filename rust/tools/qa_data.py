#!/usr/bin/env python3
"""时刻表数据体检：量化里程/时刻异常，为数据清洗规则化（交接文档 5.1-3）提供依据。

扫描 data/output/车次时刻表.csv，输出四类异常清单：
  1. 里程倒挂：站序内累计里程递减（nxt < cur）
  2. 区间速度异常：里程/纯运行时间 > 350 km/h（高铁物理上限附近，明显数据错误）
  3. 超短区间：G/D/C 车次区间纯运行 < 5 分钟（数据存疑，供人工复核）
  4. 跨夜车次：到达时刻 < 发车时刻（依赖时表"次日"语义，核对修正链）

用法: python rust/tools/qa_data.py            # 全量扫描
      python rust/tools/qa_data.py --top 15   # 只打印各类前 N 条（默认 10）
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "data" / "output" / "车次时刻表.csv"


def hm_to_min(s: str) -> int | None:
    """HH:MM → 分钟；空/坏 → None。"""
    if not s or ":" not in s:
        return None
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    inverted: list[tuple] = []       # (车次, 站名, 前站, 前里程, 后里程)
    overspeed: list[tuple] = []      # (车次, 区间, 里程km, 纯运行min, kmh)
    short_gap: list[tuple] = []      # (车次, 区间, 纯运行min)
    overnight: list[tuple] = []      # (车次, 始发, 终到, 发车, 到达)
    train_count = 0
    rows = 0

    with open(CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cur_train = None
        prev = None  # (站名, 到达, 发车, 停留, 里程)
        for r in reader:
            rows += 1
            train = r["车次"]
            if train != cur_train:
                if cur_train is not None and prev is not None:
                    dep0, arr_last = first_dep, last_arr
                    if dep0 is not None and arr_last is not None and arr_last < dep0:
                        overnight.append((cur_train, first_st, last_st, dep0, arr_last))
                cur_train, train_count = train, train_count + 1
                prev = None
                first_dep = first_st = last_arr = last_st = None
            name, dep, arr = r["站名"], r["发车"], r["到达"]
            try:
                dist = int(r["里程km"])
            except ValueError:
                dist = -1
            dep_m, arr_m = hm_to_min(dep), hm_to_min(arr)

            if first_dep is None and dep_m is not None:
                first_dep, first_st = dep_m, name
            if arr_m is not None:
                last_arr, last_st = arr_m, name

            if prev is not None:
                _, _, _, _, prev_dist = prev
                if dist >= 0 and prev_dist >= 0 and dist < prev_dist:
                    inverted.append((train, name, prev[0], prev_dist, dist))
                # 区间速度：纯运行时间 = 到达 - 发车（跨夜车次此处不计，另查）
                if dep_m is not None and arr_m is not None:
                    seg_min = arr_m - dep_m
                    seg_km = dist - prev_dist
                    if 0 < seg_min <= 60 and seg_km > 0:
                        kmh = seg_km / (seg_min / 60)
                        if kmh > 350:
                            overspeed.append((train, f"{prev[0]}→{name}", seg_km, seg_min, round(kmh)))
                        if train[0] in "GDC" and seg_min < 5:
                            short_gap.append((train, f"{prev[0]}→{name}", seg_min))
            prev = (name, arr_m, dep_m, 0, dist)

    # 收尾最后一个车次的跨夜检查
    if prev is not None and first_dep is not None and last_arr is not None and last_arr < first_dep:
        overnight.append((cur_train, first_st, last_st, first_dep, last_arr))

    print(f"CSV 共 {rows} 行 / {train_count} 车次")
    print(f"\n1) 里程倒挂: {len(inverted)} 处")
    for t, st, pre, a, b in inverted[: args.top]:
        print(f"   {t} {st}（前站 {pre} 里程 {a} → {b}）")
    print(f"\n2) 区间速度 >350km/h: {len(overspeed)} 处")
    for t, seg, km, mi, kmh in overspeed[: args.top]:
        print(f"   {t} {seg} {km}km/{mi}min = {kmh}km/h")
    print(f"\n3) G/D/C 超短区间(<5min): {len(short_gap)} 处")
    for t, seg, mi in short_gap[: args.top]:
        print(f"   {t} {seg} {mi}min")
    print(f"\n4) 跨夜车次(到达<发车): {len(overnight)} 个")
    for t, fs, ls, d, a in overnight[: args.top]:
        print(f"   {t} {fs}→{ls} 发{d // 60:02d}:{d % 60:02d} 到{a // 60:02d}:{a % 60:02d}")

    # 里程倒挂分布：按车次统计（供 5.1-3 清洗规则评估）
    by_train = Counter(t for t, *_ in inverted)
    if inverted:
        print(f"\n倒挂车次数: {len(by_train)}，最严重: {by_train.most_common(5)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
