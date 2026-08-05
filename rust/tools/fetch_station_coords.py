#!/usr/bin/env python3
"""抓取 12306 车站坐标（GCJ-02），供异站地面换乘时间按距离估算使用。

数据源：12306 微信小程序公开接口 getStationAddress（返回车站地址与经纬度）。
- 输入：data/timetable/station_name.js（站电报码全量，与项目时刻表同源）
- 输出：data/station_coords.json  {电报码: {"lat": .., "lng": .., "name": ..}}
- 特性：断点续传（已抓坐标跳过）、低频（0.2s 间隔）、超时重试、进度输出。

用法：
    python rust/tools/fetch_station_coords.py            # 全量抓取
    python rust/tools/fetch_station_coords.py --limit 20 # 小批量验证
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # 仓库根（D:\Project\railway route）
STATION_JS = ROOT / "data" / "timetable" / "station_name.js"
OUT = ROOT / "data" / "station_coords.json"
API = ("https://mobile.12306.cn/wxxcx/wechat/bigScreen/getStationAddress"
       "?stationCode={code}")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 MicroMessenger/7.0.20")
INTERVAL = 0.2   # 请求间隔秒（12306 有 IP 风控，保持低频）
TIMEOUT = 10     # 单请求超时秒
RETRIES = 3      # 失败重试次数


def parse_stations(text: str):
    """station_name.js → {电报码: 站名}（保序，@bjn|北京南|VNP|...）"""
    m = re.search(r"var station_names\s*=\s*'([^']*)'", text)
    if not m:
        raise SystemExit("station_name.js 格式无法识别")
    out = {}
    for rec in m.group(1).split("@"):
        if not rec:
            continue
        parts = rec.split("|")
        if len(parts) >= 3 and parts[0] and parts[2]:
            out[parts[2]] = parts[1]  # 电报码 → 站名
    return out


def fetch_one(code: str) -> dict | None:
    url = API.format(code=code)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    d = data.get("data") or {}
    lat, lng = d.get("latitude"), d.get("longitute")
    if not lat or not lng:
        return None
    return {"lat": float(lat), "lng": float(lng),
            "name": d.get("stationName", "")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 站（验证用）")
    ap.add_argument("--interval", type=float, default=INTERVAL)
    args = ap.parse_args()

    stations = parse_stations(STATION_JS.read_text(encoding="utf-8"))
    coords = {}
    if OUT.exists():
        coords = json.loads(OUT.read_text(encoding="utf-8"))
    todo = []
    for c, n in stations.items():
        if c in coords:
            continue
        if args.limit and len(coords) + len(todo) >= args.limit:
            break
        todo.append((c, n))
    total = len(stations)
    done = len(coords)
    print(f"全量 {total} 站，已完成 {done}，待抓 {len(todo)}", flush=True)
    t0 = time.time()
    for i, (code, name) in enumerate(todo, 1):
        for attempt in range(1, RETRIES + 1):
            try:
                got = fetch_one(code)
                break
            except Exception as e:
                if attempt == RETRIES:
                    print(f"[skip] {code} {name}: {e}", flush=True)
                    got = None
                else:
                    time.sleep(1.5 * attempt)
        if got:
            got["name"] = got["name"] or name
            coords[code] = got
        if i % 50 == 0 or i == len(todo):
            OUT.write_text(json.dumps(coords, ensure_ascii=False, indent=0),
                           encoding="utf-8")
            el = time.time() - t0
            rate = i / el if el else 0
            print(f"进度 {done + i}/{total}  (当前批 {i}, {rate:.1f} 站/s, "
                  f"剩余约 {(len(todo)-i)/rate/60:.1f} min)", flush=True)
        time.sleep(args.interval)
    OUT.write_text(json.dumps(coords, ensure_ascii=False, indent=0),
                   encoding="utf-8")
    missing = [c for c, _ in todo if c not in coords]
    print(f"完成：{len(coords)}/{total} 站坐标 → {OUT}")
    if missing:
        print(f"无数据 {len(missing)} 站：{', '.join(missing[:20])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
