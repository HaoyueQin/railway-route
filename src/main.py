#!/usr/bin/env python3
"""
铁路换乘路径规划 — CSA + 模糊匹配 + Web GUI + 约束式偏好。

用法:
  python src/main.py 北京 广州          # 城市名自动匹配车站
  python src/main.py 曲阜东 广州南 --depart-after 8:00 --arrive-before 20:00
  python src/main.py --gui
"""

import sys, os, time, argparse, json, webbrowser, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.graph import RailwayGraph
from src.csa import search as csa_search
from src.matcher import build_matcher, fuzzy_match, resolve_single


# ═══════════════════════════════════════════════════════
#  评分
# ═══════════════════════════════════════════════════════

def score_routes(routes):
    if not routes: return []
    max_t = max(r.total_time for r in routes) or 1
    max_d = max(r.total_distance for r in routes) or 1
    scored = []
    for r in routes:
        night_p = sum(1 for t in r.depart_times + r.arrive_times
                      if t.strip() and int(t.split(":")[0]) in range(23, 24) or
                      int(t.split(":")[0]) in range(0, 6))
        s = (max(0, 1 - r.total_time / max_t) * 0.45 +
             max(0, 1 - r.transfers / 4) * 0.30 +
             max(0, 1 - night_p * 0.15) * 0.10 +
             max(0, 1 - r.total_distance / max_d) * 0.15)
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return scored


def route_to_dict(r, score=0):
    return {
        "score": round(score, 3),
        "total_time": f"{r.total_time//60}h{r.total_time%60:02d}m",
        "total_minutes": r.total_time,
        "total_distance": r.total_distance,
        "transfers": r.transfers,
        "transfer_stations": r.transfer_stations,
        "first_depart": f"{r.first_depart//60:02d}:{r.first_depart%60:02d}",
        "segments": [{
            "train_code": r.train_codes[i],
            "from_station": r.stations[i],
            "to_station": r.stations[i + 1],
            "depart_time": r.depart_times[i],
            "arrive_time": r.arrive_times[i],
            "travel_minutes": r.travel_minutes[i],
            "distance": r.distances[i],
        } for i in range(len(r.train_codes))],
    }


# ═══════════════════════════════════════════════════════
#  Web GUI — 约束式偏好 + 模糊匹配
# ═══════════════════════════════════════════════════════

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>铁路换乘规划</title>
<style>
:root{--bg:#f2f2f7;--card:#fff;--text:#1c1c1e;--sub:#8e8e93;--sep:#e5e5ea;--blue:#007aff;--green:#34c759;--orange:#ff9500;--red:#ff3b30;--shadow:0 1px 3px rgba(0,0,0,.08);--radius:14px}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}
.header{background:var(--card);border-bottom:1px solid var(--sep);padding:10px 16px;text-align:center;position:sticky;top:0;z-index:100}
.header h1{font-size:17px;font-weight:600}.header .sub{font-size:11px;color:var(--sub)}
.main{max-width:640px;margin:0 auto;padding:12px 16px 40px}
.card{background:var(--card);border-radius:var(--radius);padding:14px 16px;margin-bottom:10px;box-shadow:var(--shadow)}
.card-title{font-size:13px;font-weight:600;color:var(--sub);margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}
.row{display:flex;gap:8px;align-items:center}
.row input,.row select{flex:1;border:1.5px solid var(--sep);border-radius:10px;padding:10px 12px;font-size:15px;outline:none;min-width:0;background:var(--card)}
.row input:focus,.row select:focus{border-color:var(--blue)}
.row .icon{color:var(--sub);font-size:18px;flex-shrink:0}
.btn{background:var(--blue);color:#fff;border:none;padding:12px 24px;border-radius:12px;font-size:15px;font-weight:600;cursor:pointer;width:100%}
.btn:active{opacity:.8}
.time-row{display:flex;gap:8px;align-items:center;font-size:13px}
.time-row input{width:70px;border:1.5px solid var(--sep);border-radius:8px;padding:6px 8px;font-size:13px;text-align:center}
.time-row span{color:var(--sub)}
.hint{font-size:11px;color:var(--sub);margin-top:6px}
.suggestions{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.sugg{padding:4px 10px;background:#f0f0f5;border-radius:100px;font-size:12px;cursor:pointer;border:none}
.sugg:hover{background:var(--blue);color:#fff}
.meta{font-size:12px;color:var(--sub);padding:4px 0;display:flex;justify-content:space-between}
.route-card{background:var(--card);border-radius:var(--radius);margin-bottom:8px;box-shadow:var(--shadow);overflow:hidden;cursor:pointer}
.route-summary{padding:12px 14px;display:flex;gap:10px;align-items:center}
.route-rank{font-size:13px;color:var(--sub);min-width:22px;text-align:center}
.route-body{flex:1;min-width:0}
.route-stations{font-size:16px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.route-info{font-size:12px;color:var(--sub);margin-top:2px;display:flex;gap:10px;flex-wrap:wrap}
.badge{font-size:10px;font-weight:600;padding:2px 7px;border-radius:100px}
.bg{background:#e8f1ff;color:var(--blue)}.bd{background:#e8f8ed;color:var(--green)}
.bc{background:#fff3e0;color:var(--orange)}.bk{background:#ffeaea;color:var(--red)}
.bt{background:#fff3e0;color:var(--orange)}
.route-detail{display:none;border-top:1px solid var(--sep);padding:0 14px 12px}
.route-card.expanded .route-detail{display:block}
.timeline{position:relative;padding-left:28px;margin-top:8px}
.timeline::before{content:'';position:absolute;left:10px;top:6px;bottom:6px;width:2px;background:var(--sep)}
.tl-node{position:absolute;left:-21px;width:10px;height:10px;border-radius:50%;background:var(--blue);border:2px solid #fff;box-shadow:0 0 0 1px var(--blue);top:3px}
.tl-node.end{width:8px;height:8px;left:-20px;top:4px;background:#fff;box-shadow:0 0 0 2px var(--blue)}
.tl-item{padding:4px 0;position:relative}
.tl-code{font-weight:700;font-size:13px;margin-bottom:1px}
.tl-st{font-size:14px;font-weight:500}
.tl-tm{font-size:11px;color:var(--sub)}
.tl-xfer{margin:2px 0 2px -28px;padding:5px 8px 5px 34px;background:#fff9f0;border-radius:6px;font-size:11px;position:relative}
.tl-xfer .tl-node{background:var(--orange);box-shadow:0 0 0 1px var(--orange)}
.status{text-align:center;padding:48px 0;color:var(--sub);font-size:14px}
</style></head><body>
<div class="header"><h1>铁路换乘路径规划</h1><div class="sub">Connection Scan Algorithm · 全国 14173 车次 · 3305 车站</div></div>
<div class="main">

<div class="card">
<div class="card-title">起终点（支持站名 / 城市名）</div>
<div class="row">
<input id="from" placeholder="出发站或城市" value="曲阜" autocomplete="off" oninput="suggest('from')">
<span class="icon">→</span>
<input id="to" placeholder="目的站或城市" value="广州" autocomplete="off" oninput="suggest('to')">
</div>
<div id="from-sugg" class="suggestions"></div>
<div id="to-sugg" class="suggestions"></div>
<div class="hint">例: "北京" 匹配所有北京车站；"北京南" 精确匹配；"曲阜" 匹配曲阜东站</div>
</div>

<div class="card">
<div class="card-title">时间约束（可选）</div>
<div class="time-row">
<span>出发不早于</span><input id="dep-after" type="time" value="06:00" placeholder="06:00">
<span>不晚于</span><input id="dep-before" type="time" value="23:00" placeholder="23:00">
</div>
<div class="time-row" style="margin-top:6px">
<span>到达不早于</span><input id="arr-after" type="time" value="00:00" placeholder="00:00">
<span>不晚于</span><input id="arr-before" type="time" value="23:59" placeholder="23:59">
</div>
</div>

<div class="card">
<div class="card-title">换乘约束（可选）</div>
<div class="row">
<input id="xfer-at" placeholder="指定换乘站 (留空=任意)">
</div>
<div id="xfer-sugg" class="suggestions"></div>
</div>

<button class="btn" onclick="search()">搜索换乘方案</button>

<div id="results" style="margin-top:12px"><div class="status">输入起终点，点击搜索</div></div>
</div>

<script>
let matchCache={};
async function suggest(which){
  let q=document.getElementById(which).value.trim();
  if(q.length<1)return;
  let r=await fetch('/api/match?q='+encodeURIComponent(q));
  let d=await r.json();
  let el=document.getElementById(which+'-sugg');
  el.innerHTML=d.matches.map(m=>'<button class="sugg" onclick="document.getElementById(\''+which+'\').value=\''+m+'\';suggest(\''+which+'\')">'+m+'</button>').join('');
}
async function search(){
  let from=document.getElementById('from').value.trim(),to=document.getElementById('to').value.trim();
  let da=document.getElementById('dep-after').value,db=document.getElementById('dep-before').value;
  let aa=document.getElementById('arr-after').value,ab=document.getElementById('arr-before').value;
  let xf=document.getElementById('xfer-at').value.trim();
  if(!from||!to)return;
  let params=new URLSearchParams({from,to});
  if(da)params.set('dep_after',da);if(db)params.set('dep_before',db);
  if(aa)params.set('arr_after',aa);if(ab)params.set('arr_before',ab);
  if(xf)params.set('xfer_at',xf);
  document.getElementById('results').innerHTML='<div class="status">搜索中…</div>';
  let r=await fetch('/api/search?'+params.toString());
  let d=await r.json();
  if(d.error){document.getElementById('results').innerHTML='<div class="status">'+d.error+'</div>';return}
  if(!d.routes||!d.routes.length){document.getElementById('results').innerHTML='<div class="status">未找到方案</div>';return}
  render(d);
}
function bc(code){let t=code.charAt(0);return t=='G'?'bg':t=='D'?'bd':t=='C'?'bc':t=='K'||t=='T'||t=='Z'?'bk':''}
function fm(m){return Math.floor(m/60)+'h'+(m%60).toString().padStart(2,'0')+'m'}
function render(d){
  let h='<div class="meta"><span>'+d.from_station+' → '+d.to_station+' · '+d.routes.length+' 个方案</span><span>'+d.time+'s</span></div>';
  d.routes.forEach((r,i)=>{
    let sc=r.score>=.6?'color:var(--green)':r.score>=.35?'color:var(--orange)':'color:var(--red)';
    let tl='';
    r.segments.forEach((s,j)=>{
      tl+='<div class="tl-item"><div class="tl-node'+(j==r.segments.length-1?' end':'')+'"></div>';
      tl+='<div class="tl-code"><span class="badge '+bc(s.train_code)+'">'+s.train_code+'</span></div>';
      tl+='<div class="tl-st">'+s.from_station+'</div>';
      tl+='<div class="tl-tm">'+s.depart_time+' 发 → 运行 '+fm(s.travel_minutes)+' · '+s.distance+'km</div></div>';
      if(j<r.segments.length-1){
        let ts=r.transfer_stations[j]||s.to_station;
        tl+='<div class="tl-xfer"><div class="tl-node"></div>换乘 @ <b>'+ts+'</b> · 等待 ≥90分钟</div>';
      }
    });
    let ls=r.segments[r.segments.length-1];
    tl+='<div class="tl-item"><div class="tl-node end"></div><div class="tl-st" style="font-weight:600">'+ls.to_station+'</div><div class="tl-tm">'+ls.arrive_time+' 到达</div></div>';
    h+='<div class="route-card" onclick="this.classList.toggle(\'expanded\')"><div class="route-summary"><div class="route-rank" style="'+sc+'">'+(i+1)+'</div><div class="route-body"><div class="route-stations">'+r.segments[0].from_station+' → '+ls.to_station+'</div><div class="route-info"><span>'+r.total_time+'</span><span>'+r.total_distance+'km</span>';
    if(r.transfers==0)h+='<span style="color:var(--green)">直达</span>';
    else h+='<span class="badge bt">换乘 '+r.transfers+' 次</span>';
    h+='</div></div></div><div class="route-detail"><div class="timeline">'+tl+'</div></div></div>';
  });
  document.getElementById('results').innerHTML=h;
}
</script></body></html>'''


class APIHandler(BaseHTTPRequestHandler):
    graph = None
    matcher = None

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/": self._html()
        elif p.path == "/api/match": self._match(parse_qs(p.query))
        elif p.path == "/api/search": self._search(parse_qs(p.query))
        else: self.send_response(404); self.end_headers()

    def _html(self):
        self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(HTML.encode("utf-8"))

    def _match(self, qs):
        q = qs.get("q", [""])[0]
        stations, city_map, telecode_map, pinyin_map = self.matcher
        matches = fuzzy_match(q, self.graph, stations, city_map, telecode_map, pinyin_map)
        self._json({"matches": [m[1] for m in matches[:15]]})

    def _search(self, qs):
        f = qs.get("from", [""])[0]; t = qs.get("to", [""])[0]
        da = qs.get("dep_after", [""])[0]; db = qs.get("dep_before", [""])[0]
        aa = qs.get("arr_after", [""])[0]; ab = qs.get("arr_before", [""])[0]
        xf = qs.get("xfer_at", [""])[0]

        stations, city_map, telecode_map, pinyin_map = self.matcher
        t0 = time.time()
        try:
            from_st = resolve_single(f, self.graph, stations, city_map, telecode_map, pinyin_map)
            to_st = resolve_single(t, self.graph, stations, city_map, telecode_map, pinyin_map)

            def pt(ts): return int(ts.split(":")[0])*60+int(ts.split(":")[1]) if ts else 0
            ed, ld = pt(da) if da else 0, pt(db) if db else 2880
            ea, la = pt(aa) if aa else 0, pt(ab) if ab else 5760

            routes = csa_search(self.graph, from_st, to_st,
                                earliest_depart=ed, latest_depart=ld,
                                earliest_arrive=ea, latest_arrive=la,
                                transfer_at=xf)
            scored = score_routes(routes)
            results = [route_to_dict(r, s) for s, r in scored[:50]]
            self._json({
                "routes": results,
                "time": round(time.time() - t0, 1),
                "from_station": from_st,
                "to_station": to_st,
            })
        except ValueError as e:
            self._json({"error": str(e)})

    def _json(self, d):
        self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.end_headers()
        self.wfile.write(json.dumps(d, ensure_ascii=False).encode("utf-8"))

    def log_message(self, f, *a): pass


def run_gui(graph, matcher, port=8000):
    APIHandler.graph = graph; APIHandler.matcher = matcher
    s = HTTPServer(("127.0.0.1", port), APIHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  GUI: {url}\n")
    webbrowser.open(url)
    try: s.serve_forever()
    except KeyboardInterrupt: print("\n关闭"); s.shutdown()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("from_station", nargs="?", default="曲阜东")
    p.add_argument("to_station", nargs="?", default="广州南")
    p.add_argument("--depart-after", default=""); p.add_argument("--depart-before", default="")
    p.add_argument("--arrive-before", default=""); p.add_argument("--gui", action="store_true")
    p.add_argument("--port", type=int, default=8000); p.add_argument("--max", type=int, default=15)
    a = p.parse_args()

    print("加载全国铁路网络...", end=" ", flush=True); t0 = time.time()
    graph = RailwayGraph()
    graph.build(csv_path="data/output/车次时刻表.csv", station_js_path="data/timetable/station_name.js")
    matcher = build_matcher(graph, "data/timetable/station_name.js")
    print(f"({time.time()-t0:.1f}s)")

    if a.gui: run_gui(graph, matcher, a.port); return

    def pt(ts): return int(ts.split(":")[0])*60+int(ts.split(":")[1]) if ts else 0
    stations, city_map, telecode_map, pinyin_map = matcher
    from_st = resolve_single(a.from_station, graph, stations, city_map, telecode_map, pinyin_map)
    to_st = resolve_single(a.to_station, graph, stations, city_map, telecode_map, pinyin_map)
    print(f"车站: {from_st} → {to_st}")

    t1 = time.time()
    routes = csa_search(graph, from_st, to_st,
                        earliest_depart=pt(a.depart_after),
                        latest_depart=pt(a.depart_before) or 2880,
                        earliest_arrive=0,
                        latest_arrive=pt(a.arrive_before) or 5760)
    print(f"找到 {len(routes)} 个方案 ({time.time()-t1:.1f}s)\n")
    scored = score_routes(routes)
    for i, (s, r) in enumerate(scored[:a.max]):
        segs = " | ".join(r.train_codes)
        print(f"[{i+1:2d}] {s:.3f} | {segs:35s} | {r.total_time//60}h{r.total_time%60:02d}m | {r.total_distance}km | 换乘{r.transfers}次")
        for j in range(len(r.train_codes)):
            print(f"     {r.train_codes[j]:10s} {r.stations[j]:8s} {r.depart_times[j]} → {r.stations[j+1]:8s} {r.arrive_times[j]}  ({r.travel_minutes[j]}min, {r.distances[j]}km)")
        if r.transfer_stations: print(f"     ╚ 换乘 @ {', '.join(r.transfer_stations)}")
        print()


if __name__ == "__main__": main()
