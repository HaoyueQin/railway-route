#!/usr/bin/env python3
"""性能基准：Rust --serve 模式各档位代表查询耗时/工作量（纯 HTTP，无 GUI）。

用法: python rust/tools/bench_rust.py [port]
输出: 每查询的 耗时/结果数/扫描连接/生成标签/是否完整
"""
import json
import sys
import time
import urllib.parse
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
BASE = f"http://127.0.0.1:{PORT}"

QUERIES = [
    ("北京南→上海虹桥 exact", dict(frm="北京南", to="上海虹桥", match_mode="exact")),
    ("北京→上海 fuzzy", dict(frm="北京", to="上海")),
    ("延安→深圳北 fuzzy", dict(frm="延安", to="深圳北")),
    ("乌鲁木齐→北京 fuzzy", dict(frm="乌鲁木齐", to="北京")),
    ("哈尔滨→海口 fuzzy", dict(frm="哈尔滨", to="海口")),
    ("燕郊→玉山南 fuzzy", dict(frm="燕郊", to="玉山南")),
    ("拉萨→哈尔滨 fuzzy", dict(frm="拉萨", to="哈尔滨")),
]
PROFILES = ["fast", "balanced", "thorough", "complete"]


def run(params: dict) -> dict:
    params = {("from" if k == "frm" else k): v for k, v in params.items()}
    url = BASE + "/api/search?" + urllib.parse.urlencode(params)
    t0 = time.perf_counter()
    with urllib.request.urlopen(url, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8"))
    return {"ms": (time.perf_counter() - t0) * 1000, **d}


def main() -> int:
    print(f"{'查询':<22}{'档位':<10}{'耗时ms':>8}{'结果':>6}{'扫描':>10}{'标签':>12}  完整")
    print("-" * 84)
    for label, base in QUERIES:
        for prof in PROFILES:
            d = run({**base, "search_profile": prof, "timeout": "60"})
            print(f"{label:<22}{prof:<10}{d['ms']:>8.0f}{len(d['routes']):>6}"
                  f"{d['scanned']:>10}{d['generated']:>12}  {'✓' if d['complete'] else '✗'}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
