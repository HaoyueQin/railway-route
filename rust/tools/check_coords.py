#!/usr/bin/env python3
"""验证 5.1-1 的坐标加载与换乘估算（pyref 侧，与 Rust 语义对拍前哨）。

用法: python rust/tools/check_coords.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pyref"))
from pyref.graph import RailwayGraph, est_transfer_minutes, haversine_km

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CSV = os.path.join(ROOT, "data", "output", "车次时刻表.csv")
JS = os.path.join(ROOT, "data", "timetable", "station_name.js")
COORDS = os.path.join(ROOT, "data", "station_coords.json")

# 1) 估算函数边界
cases = [
    (0.0, 30), (5.0, 30), (10.0, 30), (10.1, 45), (20.0, 45),
    (20.1, 60), (50.0, 90), (100.0, 165), (200.0, 180), (9999.0, 180),
]
for d, want in cases:
    got = est_transfer_minutes(d)
    assert got == want, f"est_transfer_minutes({d}) = {got}, want {want}"
print("估算函数: 10 组边界全过")

# 2) Haversine 已知距离（北京站↔上海虹桥 ≈ 1068km 直线）
beijing = (39.902785, 116.427677)
shanghai = (31.1981, 121.3197)  # 上海虹桥
d = haversine_km(beijing, shanghai)
assert 1000 < d < 1150, f"京沪直线距离异常: {d}km"
print(f"Haversine 京沪直线: {d:.0f}km（合理区间 1000-1150）")

# 3) 坐标加载 + 同城站对估算
g = RailwayGraph()
g.build(CSV, JS)
g.load_coords(COORDS)
print(f"图: {g.station_count} 站；坐标: {len(g.coords)} 站；同城对估算: {len(g.interstation_minutes)} 对")
assert len(g.coords) > 2000, "坐标覆盖不足"
# 同城对覆盖质量：transfer_edges 中两端都有坐标的比例
covered = sum(1 for a, b in g.transfer_edges if a in g.coords and b in g.coords)
total = len(g.transfer_edges)
print(f"同城站对覆盖: {covered}/{total} ({covered / total:.0%})")
assert covered / total > 0.8, "同城站对坐标覆盖不足"
bj = g.station_to_idx.get("北京")
bjx = g.station_to_idx.get("北京西")
bhn = g.station_to_idx.get("怀柔南")
if bj is not None and bjx is not None:
    ft = g.get_interstation_transfer_time(bj, bjx, 60)
    assert ft >= 30, f"北京→北京西换乘估算异常: {ft}"
    print(f"北京→北京西 换乘估算: {ft}min")
if bjx is not None and bhn is not None:
    ft2 = g.get_interstation_transfer_time(bjx, bhn, 60)
    print(f"北京西→怀柔南 换乘估算: {ft2}min（原固定 60min，距离估算应 >60）")
    assert ft2 > 60, "北京西→怀柔南 大跨度应 >60min"
print("坐标加载与同城估算: OK")
