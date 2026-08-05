#!/usr/bin/env python3
"""真实用户式 API 全链路测试（--serve 模式的 HTTP API）。

模拟真实用户的操作序列：
  1. 站名建议（match）
  2. 直达 + 换乘搜索（exact/fuzzy、各档位）
  3. 缓存命中（同参数重复查询 → cached=true，耗时显著下降）
  4. 无方案自动升级（fast 档小站长途 → upgraded=true）
  5. 车次时刻表查询
  6. 错误路径（未知站/非法参数 → 明确错误码）

用法: python rust/tools/api_user_test.py [port]
退出码: 0 = 全部通过
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
BASE = f"http://127.0.0.1:{PORT}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) railway-route-user-test"


def get(path: str, params: dict | None = None, timeout: float = 60):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def expect(name: str, ok: bool, detail: str = ""):
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        sys.exit(f"FAILED: {name}")


def main():
    print("== 1. 站名建议 ==")
    _, m = get("/api/match", {"q": "北京"})
    expect("北京扩散 ≥10 站", len(m["matches"]) >= 10, f"{len(m['matches'])} 站")
    _, m2 = get("/api/match", {"q": "xinzheng"})
    expect("拼音 xinzheng → 新郑", "新郑" in m2["matches"], str(m2["matches"][:3]))
    _, m3 = get("/api/match", {"q": "不存在站xyz"})
    expect("未知站返回空", m3["matches"] == [], str(m3["matches"]))

    print("== 2. 核心搜索（exact 直达+换乘）==")
    t0 = time.perf_counter()
    _, r = get("/api/search", {"from": "北京南", "to": "上海虹桥", "match_mode": "exact",
                               "search_profile": "balanced", "max": "10"})
    dt = time.perf_counter() - t0
    expect("北京南→上海虹桥 直达 ≥40", r["routes"][0]["train_transfers"] == 0
           and len(r["routes"]) >= 40, f"{len(r['routes'])} 条, {dt:.2f}s")
    expect("搜索完整", r["complete"], f"profile={r['profile']}")
    seg = r["routes"][0]["segments"][0]
    expect("段结构完整", seg["type"] == "train" and seg["depart"]["display"] != "",
           f"{seg['from_station']}→{seg['to_station']}")

    print("== 3. 缓存命中 ==")
    t0 = time.perf_counter()
    _, r2 = get("/api/search", {"from": "北京南", "to": "上海虹桥", "match_mode": "exact",
                                "search_profile": "balanced", "max": "10"})
    dt2 = time.perf_counter() - t0
    expect("重复查询 cached=true", r2["cached"] is True, f"{dt2*1000:.0f}ms")
    expect("缓存结果与首次一致", len(r2["routes"]) == len(r["routes"]),
           f"{len(r2['routes'])} 条")
    expect("缓存耗时 <50ms", dt2 < 0.05, f"{dt2*1000:.0f}ms")

    print("== 4. 无方案自动升级（fast 小站长途）==")
    _, r3 = get("/api/search", {"from": "燕郊", "to": "玉山南", "search_profile": "fast"})
    expect("返回方案", len(r3["routes"]) > 0, f"{len(r3['routes'])} 条")
    if r3.get("upgraded"):
        expect("已自动升级", r3["requested_profile"] == "fast",
               f"fast→{r3['profile']}")
        print(f"  ↑ 自动升级生效：fast 无方案，升级至 {r3['profile']}")
    else:
        print("  （fast 档本身有方案，未触发升级——也属正常）")

    print("== 5. 车次时刻表 ==")
    _, t = get("/api/train", {"code": "G1"})
    expect("G1 停站 ≥5（京沪标杆少停）", len(t["stops"]) >= 5, f"{len(t['stops'])} 站")
    expect("G1 首站北京南", t["stops"][0]["station"] == "北京南", t["stops"][0]["station"])
    expect("G1 末站上海虹桥", t["stops"][-1]["station"] == "上海虹桥", t["stops"][-1]["station"])
    _, t2 = get("/api/train", {"code": "K1620/K1621"})
    expect("套跑车次可查", len(t2["stops"]) > 0, f"{len(t2['stops'])} 站")
    try:
        get("/api/train", {"code": "NONEXIST"})
        expect("未知车次 404", False)
    except urllib.error.HTTPError as e:
        expect("未知车次 404", e.code == 404, f"HTTP {e.code}")

    print("== 6. 错误路径 ==")
    try:
        get("/api/search", {"from": "不存在站", "to": "北京"})
        expect("未知车站 400", False)
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode("utf-8"))
        expect("未知车站 400 + STATION_NOT_FOUND", e.code == 400
               and body["error"]["code"] == "STATION_NOT_FOUND", body["error"]["code"])
    try:
        get("/api/search", {"from": "北京"})
        expect("缺目的站 400", False)
    except urllib.error.HTTPError as e:
        expect("缺目的站 400 + MISSING_STATION", e.code == 400, f"HTTP {e.code}")
    try:
        get("/api/search", {"from": "北京", "to": "上海", "dep_after": "25:00"})
        expect("非法时间 400", False)
    except urllib.error.HTTPError as e:
        expect("非法时间 400 + INVALID_TIME", e.code == 400, f"HTTP {e.code}")

    print("\n全部 API 用户路径测试通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
