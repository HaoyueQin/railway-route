#!/usr/bin/env python3
"""
铁路出行路径规划 — 多源/多目标 CSA + 类型化路径段 + Web GUI + 参数校验。

用法:
  python src/main.py 北京 广州 --match-mode fuzzy
  python src/main.py 北京南 上海虹桥 --match-mode exact --max 5
  python src/main.py --gui
"""

import sys, os, time, argparse, json, webbrowser, pathlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.graph import RailwayGraph, DEFAULT_SAME_TRANSFER_MINUTES, DEFAULT_INTER_TRANSFER_MINUTES
from src.csa import search as csa_search
from src.cache import SearchCache, data_fingerprint, _request_key
from src.matcher import build_matcher, fuzzy_match
from src.models import (
    InterstationTransferSegment,
    RouteResult,
    TrainSegment,
    format_absolute_minutes,
)
from src.validation import (
    RequestValidationError,
    build_search_request,
)


# ═══════════════════════════════════════════════════════
#  评分（类型化 RouteResult）
# ═══════════════════════════════════════════════════════

def score_routes(routes: list[RouteResult]) -> list[tuple[float, RouteResult]]:
    if not routes:
        return []
    max_t = max(r.total_minutes for r in routes) or 1
    max_d = max(r.rail_distance for r in routes) or 1
    scored = []
    for r in routes:
        night_penalty = 0
        for seg in r.segments:
            if isinstance(seg, TrainSegment):
                for m in (seg.depart_minutes, seg.arrive_minutes):
                    h = (m % 1440) // 60
                    if h >= 23 or h < 6:
                        night_penalty += 1
            elif isinstance(seg, InterstationTransferSegment):
                for m in (seg.start_minutes, seg.end_minutes):
                    h = (m % 1440) // 60
                    if h >= 23 or h < 6:
                        night_penalty += 1
        inter_penalty = r.interstation_minutes / max(60, 1)
        s = (max(0, 1 - r.total_minutes / max_t) * 0.35 +
             max(0, 1 - (r.train_transfers + r.interstation_transfers * 0.5) / 4) * 0.25 +
             max(0, 1 - night_penalty * 0.1) * 0.10 +
             max(0, 1 - r.rail_distance / max_d) * 0.15 +
             max(0, 1 - inter_penalty / 300) * 0.15)
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return scored


def typed_route_to_dict(route: RouteResult, score: float = 0) -> dict:
    segments = []
    for segment in route.segments:
        if isinstance(segment, TrainSegment):
            segments.append({
                "type": "train",
                "train_code": segment.train_code,
                "from_station": segment.from_station,
                "to_station": segment.to_station,
                "depart": format_absolute_minutes(segment.depart_minutes),
                "arrive": format_absolute_minutes(segment.arrive_minutes),
                "travel_minutes": segment.travel_minutes,
                "distance": segment.distance,
            })
        elif isinstance(segment, InterstationTransferSegment):
            segments.append({
                "type": "interstation",
                "from_station": segment.from_station,
                "to_station": segment.to_station,
                "start": format_absolute_minutes(segment.start_minutes),
                "end": format_absolute_minutes(segment.end_minutes),
                "transfer_minutes": segment.transfer_minutes,
                "city_code": segment.city_code,
                "city_name": segment.city_name,
                "estimate_source": segment.estimate_source,
            })
    return {
        "score": round(score, 3),
        "actual_origin": route.actual_origin,
        "actual_destination": route.actual_destination,
        "first_departure": format_absolute_minutes(route.first_departure),
        "final_arrival": format_absolute_minutes(route.final_arrival),
        "total_minutes": route.total_minutes,
        "rail_distance": route.rail_distance,
        "train_transfers": route.train_transfers,
        "interstation_transfers": route.interstation_transfers,
        "interstation_minutes": route.interstation_minutes,
        "transfer_cities": list(route.transfer_cities),
        "segments": segments,
    }


# ═══════════════════════════════════════════════════════
#  Web GUI — Glassmorphism 铁路出行路径规划
#  前端为独立静态文件（web/index.html + styles.css + app.js）
# ═══════════════════════════════════════════════════════

def _base_dir() -> pathlib.Path:
    """应用根目录：源码运行时为仓库根，PyInstaller 打包后为 _MEIPASS 解压目录。"""
    if getattr(sys, "_MEIPASS", None):
        return pathlib.Path(sys._MEIPASS)
    return pathlib.Path(__file__).resolve().parent.parent


def _log_error(msg: str):
    """windowed 打包后控制台不可见：异常写入本地日志便于排查。"""
    try:
        log_dir = pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "铁路出行路径规划"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "app.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _install_excepthook():
    """打包（windowed）后未捕获异常会静默退出：落盘日志 + 提示用户。"""
    if not getattr(sys, "frozen", False):
        return

    def hook(tp, val, tb):
        import traceback
        msg = "".join(traceback.format_exception(tp, val, tb))
        _log_error(msg)
        print(msg, file=sys.stderr)
        print(f"启动失败，日志已写入 %LOCALAPPDATA%\\铁路出行路径规划\\app.log", file=sys.stderr)

    sys.excepthook = hook


WEB_DIR = _base_dir() / "web"
DATA_DIR = _base_dir() / "data"
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


class APIHandler(BaseHTTPRequestHandler):
    graph = None
    matcher = None
    cache = None          # SearchCache 实例（run_gui 注入；None = 不缓存）
    data_fp = "unknown"   # 时刻表数据指纹（数据更新后缓存自动失效）

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            self._serve_static("index.html")
        elif p.path in ("/styles.css", "/app.js"):
            self._serve_static(p.path.lstrip("/"))
        elif p.path == "/api/match":
            self._match(parse_qs(p.query))
        elif p.path == "/api/search":
            self._search(parse_qs(p.query))
        elif p.path == "/api/train":
            self._train(parse_qs(p.query))
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_static(self, name):
        """从 web/ 目录读取前端静态文件（index.html / styles.css / app.js）。"""
        f = WEB_DIR / name
        try:
            data = f.read_bytes()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", STATIC_TYPES.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _match(self, qs):
        q = qs.get("q", [""])[0]
        matches = fuzzy_match(q, self.graph, self.matcher)
        self._json({"matches": [m[1] for m in matches[:15]]})

    def _train(self, qs):
        """车次全程时刻表：始发终到站 + 全部停站时刻（含跨夜 +N 天标记）。"""
        code = qs.get("code", [""])[0].strip()
        stops = self.graph.train_stops.get(code)
        if not stops:
            self._json({"error": {"code": "NOT_FOUND", "message": "未找到车次 " + code}}, status=404)
            return

        def fmt(m):
            if m < 0:
                return None
            return {"minutes": m, "time": "%02d:%02d" % ((m // 60) % 24, m % 60),
                    "day": m // 1440, "display": "%02d:%02d" % ((m // 60) % 24, m % 60)
                    + (("+" + str(m // 1440)) if m >= 1440 else "")}

        idx_to_station = self.graph.idx_to_station
        self._json({
            "code": code,
            "stops": [
                {"station": idx_to_station[s[0]], "depart": fmt(s[1]), "arrive": fmt(s[2]),
                 "seq": s[3], "distance": s[4]}
                for s in stops
            ],
        })

    def _search(self, qs):
        flat: dict[str, str] = {k: (v[0] if v else "") for k, v in qs.items()}
        if "from" not in flat and "from_station" in flat:
            flat["from"] = flat["from_station"]
        if "to" not in flat and "to_station" in flat:
            flat["to"] = flat["to_station"]

        t0 = time.time()
        try:
            request = build_search_request(flat)
        except RequestValidationError as ve:
            self._json({"error": {"code": ve.code, "message": ve.message}}, status=400)
            return

        # 查询缓存（同参数重复查询秒回；数据更新后指纹变化自动失效）
        cache_key = None
        if self.cache is not None:
            cache_key = _request_key(request, self.data_fp)
            cached = self.cache.get(cache_key)
            if cached is not None:
                body = json.loads(cached)
                body["cached"] = True
                self._json(body)
                return

        try:
            response = csa_search(self.graph, request, self.matcher)
            scored = score_routes(list(response.routes))
            results = [typed_route_to_dict(r, s) for s, r in scored]
            payload = {
                "routes": results,
                "time": round(time.time() - t0, 1),
                "source_stations": list(response.source_stations),
                "target_stations": list(response.target_stations),
                "complete": response.metadata.complete,
                "profile": response.metadata.profile,
                "scanned": response.metadata.scanned_connections,
                "generated": response.metadata.generated_states,
                "cached": False,
            }
            if cache_key is not None:
                self.cache.put(cache_key, json.dumps(payload, ensure_ascii=False))
            self._json(payload)
        except ValueError as e:
            # 车站解析失败是用户输入问题（非内部错误）：400 友好提示，前端直接展示
            msg = str(e)
            if msg.startswith("未找到匹配的车站"):
                self._json({"error": {"code": "STATION_NOT_FOUND", "message": msg}}, status=400)
            else:
                self._json({"error": {"code": "INTERNAL_ERROR", "message": msg}}, status=500)

    def _json(self, d, *, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(d, ensure_ascii=False).encode("utf-8"))

    def log_message(self, f, *a): pass


def _start_server(graph, matcher, port):
    """启动本地 HTTP 服务（供 GUI / WebView 共用）。

    端口策略：优先指定端口 → 被占用/落入 Windows 排除范围（Hyper-V/WSL 保留段，
    本机曾见 7954~8053 整段排除）时递增尝试 → 兜底用端口 0 由系统随机分配，
    保证桌面应用在任何机器上都能启动。
    """
    APIHandler.graph = graph; APIHandler.matcher = matcher
    APIHandler.cache = SearchCache()
    APIHandler.data_fp = data_fingerprint(str(DATA_DIR / "output" / "车次时刻表.csv"))
    for p in range(port, port + 20):
        try:
            s = HTTPServer(("127.0.0.1", p), APIHandler)
            return s, p
        except OSError:
            continue
    s = HTTPServer(("127.0.0.1", 0), APIHandler)  # 系统随机分配，永不冲突
    return s, s.server_address[1]


def run_gui(graph, matcher, port=8000):
    s, actual = _start_server(graph, matcher, port)
    url = f"http://127.0.0.1:{actual}"
    print(f"\n  GUI: {url}\n")
    webbrowser.open(url)
    try: s.serve_forever()
    except KeyboardInterrupt: print("\n关闭"); s.shutdown()


def run_app(graph, matcher, port=8000, title="铁路出行路径规划"):
    """桌面应用模式：pywebview（系统 WebView2）承载现有前端。

    - frameless 无系统边框：前端自绘标题栏（拖动/最小化/关闭），
      配合 -webkit-app-region 拖拽区，完全自有窗口观感
    - 无 pywebview 时自动回退浏览器模式
    - webview 启动失败（如缺 WebView2 运行时）也回退浏览器模式，不崩溃
    """
    try:
        import webview  # 第三方可选依赖（pip install pywebview）
    except ImportError:
        print("未安装 pywebview（pip install pywebview 可启用桌面窗口），回退浏览器模式")
        run_gui(graph, matcher, port)
        return
    s, actual = _start_server(graph, matcher, port)
    # 服务在独立线程运行：webview.start() 是主事件循环，不能阻塞在 serve_forever
    import threading
    threading.Thread(target=s.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{actual}"
    print(f"\n  桌面应用: {url}（关闭窗口即退出）\n")

    class WindowApi:
        """暴露给前端标题栏的窗口控制（仅最小化/关闭，不泄露其他能力）。"""
        def minimize(self):
            try:
                webview.windows[0].minimize()
            except Exception:
                pass

        def close(self):
            try:
                webview.windows[0].destroy()
            except Exception:
                pass

    try:
        webview.create_window(
            title, url,
            width=1280, height=880, min_size=(980, 640),
            frameless=True,          # 无系统边框，标题栏由前端自绘
            background_color="#e0e7ff",
            js_api=WindowApi(),
        )
        webview.start()
    except Exception as e:
        _log_error(f"webview 启动失败，回退浏览器模式: {e}")
        print(f"webview 启动失败（{e}），回退浏览器模式")
        webbrowser.open(url)
        try: s.serve_forever()
        except KeyboardInterrupt: pass
    finally:
        s.shutdown()


def main():
    _install_excepthook()
    p = argparse.ArgumentParser()
    p.add_argument("from_station", nargs="?", default="曲阜东")
    p.add_argument("to_station", nargs="?", default="广州南")
    p.add_argument("--match-mode", choices=["exact", "fuzzy"], default="fuzzy")
    p.add_argument("--from-mode", choices=["exact", "fuzzy"], default=None,
                   help="出发端独立匹配模式（默认跟随 --match-mode）")
    p.add_argument("--to-mode", choices=["exact", "fuzzy"], default=None,
                   help="目的端独立匹配模式（默认跟随 --match-mode）")
    p.add_argument("--search-profile", choices=["fast", "balanced", "thorough", "complete"], default="balanced")
    p.add_argument("--depart-after", default=""); p.add_argument("--depart-before", default="")
    p.add_argument("--arrive-before", default=""); p.add_argument("--gui", action="store_true",
                   help="浏览器模式（无 pywebview 时的回退）")
    p.add_argument("--app", action="store_true", help="桌面应用模式（pywebview 承载，推荐）")
    p.add_argument("--port", type=int, default=8000); p.add_argument("--max", type=int, default=15)
    p.add_argument("--same-transfer", type=int, default=DEFAULT_SAME_TRANSFER_MINUTES)
    p.add_argument("--inter-transfer", type=int, default=DEFAULT_INTER_TRANSFER_MINUTES)
    p.add_argument("--max-transfers", type=int, default=3)
    p.add_argument("--timeout", type=int, default=30)
    a = p.parse_args()

    print("加载全国铁路网络...", end=" ", flush=True); t0 = time.time()
    graph = RailwayGraph()
    graph.build(csv_path=str(DATA_DIR / "output" / "车次时刻表.csv"),
                station_js_path=str(DATA_DIR / "timetable" / "station_name.js"))
    matcher = build_matcher(graph, str(DATA_DIR / "timetable" / "station_name.js"))
    print(f"({time.time()-t0:.1f}s)")

    if a.app or getattr(sys, "frozen", False):
        run_app(graph, matcher, a.port); return
    if a.gui: run_gui(graph, matcher, a.port); return

    try:
        request = build_search_request({
            "from": a.from_station, "to": a.to_station,
            "from_mode": a.from_mode, "to_mode": a.to_mode,
            "match_mode": a.match_mode, "search_profile": a.search_profile,
            "dep_after": a.depart_after, "dep_before": a.depart_before,
            "arr_before": a.arrive_before,
            "same_transfer": str(a.same_transfer), "inter_transfer": str(a.inter_transfer),
            "max_transfers": str(a.max_transfers), "timeout": str(a.timeout),
        })
    except RequestValidationError as ve:
        print(f"参数错误 [{ve.code}]: {ve.message}")
        sys.exit(2)

    t1 = time.time()
    response = csa_search(graph, request, matcher)
    elapsed = time.time() - t1
    routes = list(response.routes)

    print(f"\n出发站集合: {', '.join(response.source_stations)}")
    print(f"目的站集合: {', '.join(response.target_stations)}")
    print(f"模式: {response.metadata.profile} | "
          f"扫描 {response.metadata.scanned_connections} 条 · "
          f"生成 {response.metadata.generated_states} 状态 · "
          f"{'完整' if response.metadata.complete else '未完整'}")
    
    # 分直达/换乘统计
    direct = [r for r in routes if r.train_transfers == 0 and r.interstation_transfers == 0]
    xfer = [r for r in routes if not (r.train_transfers == 0 and r.interstation_transfers == 0)]
    print(f"直达 {len(direct)} 条 + 换乘 {len(xfer)} 条 = {len(routes)} 个方案 ({elapsed:.1f}s)\n")

    scored = score_routes(routes)
    for i, (s, r) in enumerate(scored[:a.max]):
        train_segs = [seg for seg in r.segments if isinstance(seg, TrainSegment)]
        codes = " | ".join(t.train_code for t in train_segs)
        extra = ""
        if r.interstation_transfers > 0:
            extra = f" +{r.interstation_transfers}地面({r.interstation_minutes}min)"
        print(f"[{i+1:2d}] {s:.3f} | {codes:40s} | "
              f"{r.total_minutes//60}h{r.total_minutes%60:02d}m | {r.rail_distance}km | "
              f"换乘{r.train_transfers}次{extra}")
        print(f"     实际: {r.actual_origin} → {r.actual_destination}")

        for seg in r.segments:
            if isinstance(seg, TrainSegment):
                dep_fmt = format_absolute_minutes(seg.depart_minutes)
                arr_fmt = format_absolute_minutes(seg.arrive_minutes)
                print(f"     {seg.train_code:10s} {seg.from_station:8s} {dep_fmt['display']:10s} → "
                      f"{seg.to_station:8s} {arr_fmt['display']:10s}  "
                      f"({seg.travel_minutes}min, {seg.distance}km)")
            elif isinstance(seg, InterstationTransferSegment):
                start_fmt = format_absolute_minutes(seg.start_minutes)
                end_fmt = format_absolute_minutes(seg.end_minutes)
                print(f"     {'[地面]':10s} {seg.from_station:8s} {start_fmt['display']:10s} → "
                      f"{seg.to_station:8s} {end_fmt['display']:10s}  "
                      f"({seg.transfer_minutes}min, {seg.city_name})")
        if r.transfer_cities:
            print(f"     ╚ 换乘城市: {', '.join(r.transfer_cities)}")
        print()


if __name__ == "__main__": main()
