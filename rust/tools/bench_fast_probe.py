#!/usr/bin/env python3
"""fast 档耗时实证：同一查询 fast/balanced 交替重复 N 次取中位数（排除噪声）。

用法: python rust/tools/bench_fast_probe.py [port]
"""
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
BASE = f"http://127.0.0.1:{PORT}"
REPEAT = 7

CASES = [
    ("北京南→上海虹桥", dict(frm="北京南", to="上海虹桥", match_mode="exact")),
    ("北京→上海", dict(frm="北京", to="上海")),
    ("哈尔滨→海口", dict(frm="哈尔滨", to="海口")),
]


def run_once(params: dict) -> dict:
    params = {("from" if k == "frm" else k): v for k, v in params.items()}
    url = BASE + "/api/search?" + urllib.parse.urlencode(params)
    t0 = time.perf_counter()
    with urllib.request.urlopen(url, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8"))
    return {"ms": (time.perf_counter() - t0) * 1000, **d}


def main() -> int:
    print(f"{'查询':<16}{'档位':<10}{'中位数ms':>9}{'最小ms':>8}{'最大ms':>8}"
          f"{'扫描':>10}{'标签':>12}  完整")
    print("-" * 82)
    for label, base in CASES:
        for prof in ("fast", "balanced"):
            samples = []
            for i in range(REPEAT):
                d = run_once({**base, "search_profile": prof, "timeout": "60"})
                samples.append((d["ms"], d["scanned"], d["generated"], d["complete"]))
            ms = statistics.median(s for s, *_ in samples)
            mn = min(s for s, *_ in samples)
            mx = max(s for s, *_ in samples)
            _, sc, ge, cp = samples[REPEAT // 2]
            print(f"{label:<16}{prof:<10}{ms:>9.0f}{mn:>8.0f}{mx:>8.0f}"
                  f"{sc:>10}{ge:>12}  {'✓' if cp else '✗'}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
