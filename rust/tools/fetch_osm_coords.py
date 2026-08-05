#!/usr/bin/env python3
"""用 OpenStreetMap（Overpass API）补齐 12306 缺失的车站坐标。

背景（交接文档 5.1-1）：12306 getStationAddress 覆盖 2878/3375 站，497 站无数据
（新站/小站/停办站）。OSM railway=station 节点覆盖较全，可补齐。

- 输入：data/station_coords.json（12306 已抓）+ data/timetable/station_name.js（站名全量）
- 输出：合并后的 data/station_coords.json（新条目带 "src": "osm"，WGS84）
- 特性：只查缺失站；Overpass 一次全量查询（中国范围 railway=station）；
  站名精确匹配（OSM name 不带"站"后缀，与路路通站名一致）；
  已覆盖的站不动（12306 GCJ-02 优先）。

用法: python rust/tools/fetch_osm_coords.py
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "station_coords.json"
STATION_JS = ROOT / "data" / "timetable" / "station_name.js"
OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "railway-route-data-tool/1.0 (learning project)"
PROXY = "http://127.0.0.1:8897"  # 本机代理（网络不畅时使用；直连失败自动回退）


def parse_stations(text: str) -> dict[str, str]:
    """station_name.js → {电报码: 站名}"""
    m = re.search(r"var station_names\s*=\s*'([^']*)'", text)
    out = {}
    for rec in m.group(1).split("@"):
        if not rec:
            continue
        parts = rec.split("|")
        if len(parts) >= 3 and parts[0] and parts[2]:
            out[parts[2]] = parts[1]
    return out


def query_osm_stations(names: list[str]) -> dict[str, tuple[float, float]]:
    """Overpass 按站名分批查询（regex，每批 40 名；小查询避免主站 504）。"""
    import urllib.parse

    osm_by_name: dict[str, tuple[float, float]] = {}
    handlers = [urllib.request.HTTPSHandler()]
    proxy_handler = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    direct = urllib.request.build_opener(*handlers)
    proxied = urllib.request.build_opener(proxy_handler, *handlers)
    for i in range(0, len(names), 40):
        batch = names[i:i + 40]
        # 正则转义（站名含 |、() 等字符的场景）
        esc = "|".join(re.escape(n) for n in batch)
        q = (
            '[out:json][timeout:40];'
            f'node["railway"="station"]["name"~"^({esc})$"];out body;'
        )
        data = urllib.parse.urlencode({"data": q}).encode()
        req = urllib.request.Request(OVERPASS, data=data, headers={"User-Agent": UA})
        elements = None
        for opener, label in ((proxied, "代理"), (direct, "直连")):
            try:
                with opener.open(req, timeout=60) as resp:
                    elements = json.loads(resp.read().decode("utf-8"))["elements"]
                break
            except Exception as e:
                print(f"  [warn] {label} 失败: {e}", flush=True)
        if elements is None:
            continue
        for e in elements:
            name = (e.get("tags") or {}).get("name", "")
            if name and e.get("lat") is not None:
                osm_by_name.setdefault(name, (e["lat"], e["lon"]))
        print(f"  OSM 批次 {i // 40 + 1}/{(len(names) + 39) // 40}：命中 {len(osm_by_name)}", flush=True)
    return osm_by_name


def main():
    coords = json.loads(OUT.read_text(encoding="utf-8"))
    stations = parse_stations(STATION_JS.read_text(encoding="utf-8"))
    missing = [c for c in stations if c not in coords]
    missing_names = sorted({stations[c] for c in missing})
    print(f"已有 {len(coords)} 站，缺失 {len(missing_names)} 个站名，查询 OSM...", flush=True)

    osm_by_name = query_osm_stations(missing_names)
    print(f"OSM 命中 {len(osm_by_name)} 个缺失站名", flush=True)

    added = 0
    for code in missing:
        name = stations[code]
        if name in osm_by_name:
            lat, lng = osm_by_name[name]
            coords[code] = {"lat": lat, "lng": lng, "name": name, "src": "osm"}
            added += 1
    OUT.write_text(json.dumps(coords, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"补齐 {added} 站（OSM/WGS84），总计 {len(coords)}/{len(stations)} 站坐标 → {OUT}")
    still = [c for c in stations if c not in coords]
    if still:
        print(f"仍缺失 {len(still)} 站：{', '.join(stations[c] for c in still[:20])}")
    return 0


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main())
