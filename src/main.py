#!/usr/bin/env python3
"""
铁路出行路径规划 — 多源/多目标 CSA + 类型化路径段 + Web GUI + 参数校验。

用法:
  python src/main.py 北京 广州 --match-mode fuzzy
  python src/main.py 北京南 上海虹桥 --match-mode exact --max 5
  python src/main.py --gui
"""

import sys, os, time, argparse, json, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.graph import RailwayGraph, DEFAULT_SAME_TRANSFER_MINUTES, DEFAULT_INTER_TRANSFER_MINUTES
from src.csa import search as csa_search
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
# ═══════════════════════════════════════════════════════

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>铁路出行路径规划</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:16px}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh;background:linear-gradient(135deg,#e0e7ff 0%,#fce7f3 40%,#d1fae5 70%,#e0e7ff 100%);background-attachment:fixed;color:#334155;line-height:1.6;overflow-x:hidden}

/* Decorative blobs */
.blob{position:fixed;border-radius:50%;filter:blur(100px);opacity:.35;z-index:-1;pointer-events:none}
.blob1{width:480px;height:480px;background:radial-gradient(circle,#818cf8,#c084fc);top:-120px;left:-80px;animation:bf 14s ease-in-out infinite alternate}
.blob2{width:520px;height:520px;background:radial-gradient(circle,#f472b6,#fb923c);top:40%;right:-130px;animation:bf 18s ease-in-out infinite alternate-reverse}
.blob3{width:380px;height:380px;background:radial-gradient(circle,#34d399,#2dd4bf);bottom:8%;left:12%;animation:bf 16s ease-in-out infinite alternate}
@keyframes bf{0%{transform:translate(0,0) scale(1)}33%{transform:translate(25px,-25px) scale(1.04)}66%{transform:translate(-15px,15px) scale(.96)}100%{transform:translate(15px,-8px) scale(1.01)}}

/* Nav */
.nv{position:sticky;top:0;z-index:100;padding:0 22px;background:rgba(255,255,255,.55);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border-bottom:1px solid rgba(255,255,255,.4)}
.nv-in{max-width:900px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;height:48px}
.nv-logo{font-weight:700;font-size:1rem;color:#4338ca} .nv-sub{font-size:.7rem;color:#94a3b8}

/* Main */
.app{max-width:900px;margin:0 auto;padding:28px 18px 56px}

/* Hero */
.hero{text-align:center;padding:36px 16px 28px}
.hero h1{font-size:1.7rem;font-weight:800;background:linear-gradient(135deg,#4338ca,#7c3aed);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px}
.hero p{font-size:.85rem;color:#94a3b8}

/* Card */
.cd{background:rgba(255,255,255,.4);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);border:1px solid rgba(255,255,255,.5);border-radius:18px;padding:22px;margin-bottom:10px;box-shadow:0 2px 12px rgba(0,0,0,.03)}
.cd-hd{font-size:.82rem;font-weight:700;color:#64748b;margin-bottom:16px;display:flex;align-items:center;gap:6px;letter-spacing:.2px}

/* Grid */
.g{display:grid;gap:12px;margin-bottom:12px}.g:last-child{margin-bottom:0}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:1fr 1fr 1fr}

/* Field */
.fld{display:flex;flex-direction:column;gap:4px}
.fld-lbl{font-size:.76rem;font-weight:600;color:#94a3b8}

/* Inputs - underline style */
.inp,.sel{width:100%;padding:10px 2px;font-size:.9rem;font-family:inherit;color:#334155;background:transparent;border:none;border-bottom:2px solid rgba(148,163,184,.18);border-radius:0;outline:none;transition:border-color .25s}
.inp:hover,.sel:hover{border-bottom-color:rgba(129,140,248,.3)}
.inp:focus,.sel:focus{border-bottom-color:#818cf8}
.inp::placeholder{color:#cbd5e1;font-size:.82rem}
.inp[type=time]{min-width:125px}
.sel{cursor:pointer;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10'%3E%3Cpath fill='%2394a3b8' d='M0 3l5 5 5-5z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 6px center;padding-right:22px}
.sel option{background:rgba(255,255,255,.92);color:#334155;padding:8px 12px}

/* Custom glass dropdown */
.cdd{position:relative;display:inline-block;width:100%}
.cdd-btn{width:100%;padding:10px 2px;font-size:.9rem;font-family:inherit;color:#334155;background:transparent;border:none;border-bottom:2px solid rgba(148,163,184,.18);text-align:left;cursor:pointer;outline:none;transition:border-color .25s;position:relative}
.cdd-btn::after{content:'';position:absolute;right:6px;top:50%;transform:translateY(-50%);border-left:5px solid transparent;border-right:5px solid transparent;border-top:6px solid #94a3b8}
.cdd-btn:hover{border-bottom-color:rgba(129,140,248,.3)}
.cdd.open .cdd-btn{border-bottom-color:#818cf8}
.cdd-panel{display:none;position:absolute;top:100%;left:0;right:0;z-index:200;margin-top:4px;background:rgba(255,255,255,.7);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.5);border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.1);overflow:hidden;max-height:240px;overflow-y:auto}
.cdd.open .cdd-panel{display:block}
.cdd-opt{padding:10px 14px;font-size:.88rem;color:#334155;cursor:pointer;transition:background .15s}
.cdd-opt:hover{background:rgba(129,140,248,.1);color:#6366f1}
.cdd-opt.sel{background:rgba(129,140,248,.08);color:#6366f1;font-weight:600}
.cdd select{position:absolute;opacity:0;pointer-events:none}

/* Slider row */
.sr{display:flex;align-items:center;gap:8px}
.sr input[type=range]{flex:1;accent-color:#818cf8;height:3px}
.sr input[type=number]{width:56px;text-align:center;padding:6px 2px;font-size:.82rem;font-family:inherit;color:#334155;background:transparent;border:none;border-bottom:2px solid rgba(148,163,184,.18);border-radius:0;outline:none}
.sr input[type=number]:focus{border-bottom-color:#818cf8}
.suf{font-size:.76rem;color:#94a3b8;min-width:26px}

/* Suggestions */
.sgs{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.sg{font-size:.73rem;padding:4px 12px;border-radius:20px;border:1px solid rgba(148,163,184,.18);background:transparent;color:#64748b;cursor:pointer;font-family:inherit;transition:all .2s}
.sg:hover{border-color:#818cf8;color:#6366f1;background:rgba(129,140,248,.05)}

/* Button */
.bt-w{text-align:center;padding:8px 0}
.bt{display:inline-block;padding:13px 56px;font-size:1rem;font-weight:700;font-family:inherit;color:#fff;background:linear-gradient(135deg,#6366f1,#8b5cf6);border:none;border-radius:14px;cursor:pointer;transition:all .25s;box-shadow:0 2px 14px rgba(99,102,241,.2)}
.bt:hover{background:linear-gradient(135deg,#4f46e5,#7c3aed);transform:translateY(-1px);box-shadow:0 6px 22px rgba(99,102,241,.3)}
.bt:active{transform:translateY(0)}

/* Meta */
.meta{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;font-size:.76rem;color:#94a3b8;padding:6px 0}
.mtag{font-size:.68rem;font-weight:600;padding:3px 10px;border-radius:6px;background:rgba(245,158,11,.1);color:#d97706}

/* Section header */
.sec-hd{font-size:.82rem;font-weight:700;color:#475569;margin:18px 0 8px;padding-bottom:6px;border-bottom:1px solid rgba(0,0,0,.05);display:flex;align-items:center;gap:8px}
.sec-hd .cnt{font-size:.72rem;font-weight:500;color:#94a3b8}

/* Route card */
.rc{background:rgba(255,255,255,.45);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,.5);border-radius:14px;margin-bottom:5px;overflow:hidden;cursor:pointer;transition:all .2s}
.rc:hover{border-color:rgba(129,140,248,.2);box-shadow:0 4px 16px rgba(0,0,0,.04)}
.rc-main{padding:10px 14px;display:flex;gap:10px;align-items:flex-start}
.rc-num{font-size:.78rem;font-weight:700;color:#cbd5e1;min-width:22px;text-align:center;padding-top:2px}
.rc-body{flex:1;min-width:0}
.rc-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:3px}
.rc-route{font-size:.95rem;font-weight:700;color:#1e293b}
.rc-time{font-size:.8rem;font-weight:600;color:#6366f1}

/* Horizontal flow */
.rc-flow{display:flex;align-items:flex-start;gap:0;flex-wrap:wrap;margin-bottom:3px}
.rc-node{display:flex;flex-direction:column;align-items:center;gap:1px}
.rc-node-name{font-size:.8rem;font-weight:700;color:#334155;white-space:nowrap}
.rc-node-time{font-size:.68rem;color:#94a3b8;white-space:nowrap}
.rc-arrow-node{display:flex;flex-direction:column;align-items:center;gap:1px;padding:0 4px}
.rc-arrow-code{font-size:.62rem;font-weight:700;padding:2px 6px;border-radius:3px;white-space:nowrap}
.rc-arrow-time{font-size:.6rem;color:#cbd5e1}
.rc-info{display:flex;gap:12px;flex-wrap:wrap;font-size:.74rem;color:#94a3b8;margin-top:2px}

/* Badges */
.badge{font-size:.66rem;font-weight:700;padding:2px 6px;border-radius:4px}
.bG{background:rgba(59,130,246,.1);color:#2563eb}.bD{background:rgba(34,197,94,.1);color:#16a34a}
.bC{background:rgba(249,115,22,.1);color:#ea580c}.bK{background:rgba(239,68,68,.1);color:#dc2626}
.bI{background:rgba(236,72,153,.1);color:#be185d}

/* Expanded detail */
.rc-detail{display:none;border-top:1px solid rgba(0,0,0,.04);padding:10px 18px 14px;background:rgba(248,250,252,.2)}
.rc.expanded .rc-detail{display:block}

/* Timetable */
.tt-wrap{display:flex;flex-direction:column;gap:6px}
.tt-card{background:rgba(255,255,255,.3);border-radius:10px;padding:10px 14px}
.tt-hd{font-size:.78rem;font-weight:600;color:#475569;margin-bottom:6px}
.tt-tbl{width:100%;border-collapse:collapse;font-size:.74rem}
.tt-tbl th,.tt-tbl td{padding:4px 8px;text-align:left;border-bottom:1px solid rgba(0,0,0,.03)}
.tt-tbl th{color:#94a3b8;font-weight:500;font-size:.68rem}
.tt-tbl td{color:#334155}
.tt-gnd{font-size:.74rem;color:#7c3aed;padding:6px 12px;background:rgba(139,92,246,.05);border-radius:8px;text-align:center}

/* Sort/filter */
.sf-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:4px 0 2px;margin-bottom:4px}
.sf-bar select,.sf-bar input{font-size:.74rem;padding:5px 10px;border:1px solid rgba(148,163,184,.15);border-radius:8px;background:rgba(255,255,255,.35);color:#475569;font-family:inherit;outline:none}
.sf-bar select:focus,.sf-bar input:focus{border-color:#818cf8}
.sf-bar label{font-size:.72rem;color:#94a3b8}

/* Empty state */
.empty{text-align:center;padding:56px 0;color:#cbd5e1;font-size:.88rem}

@media(max-width:768px){.g2,.g3{grid-template-columns:1fr}.app{padding:18px 12px 40px}.hero{padding:24px 12px 20px}.hero h1{font-size:1.35rem}.cd{padding:18px}.bt{width:100%}.nv-sub{display:none}}
</style></head><body>
<div class="blob blob1"></div>
<div class="blob blob2"></div>
<div class="blob blob3"></div>

<nav class="nv"><div class="nv-in">
<span class="nv-logo">🚄 铁路出行路径规划</span>
<span class="nv-sub">全国铁路出行方案 · 14,173 车次 · 3,305 车站</span>
</div></nav>

<div class="app">

<header class="hero">
<h1>铁路出行路径规划</h1>
<p>基于路路通离线时刻表的全国铁路出行方案搜索 · 直达与换乘 · 支持模糊多站匹配与同城异站换乘</p>
</header>

<div class="cd">
<div class="cd-hd">🔍 起终点</div>
<div class="g g2">
<div class="fld"><label class="fld-lbl">出发站 / 城市</label>
<input id="from" class="inp" placeholder="如：北京 / 北京南" autocomplete="off" oninput="suggest('from')"></div>
<div class="fld"><label class="fld-lbl">目的站 / 城市</label>
<input id="to" class="inp" placeholder="如：上海 / 上海虹桥" autocomplete="off" oninput="suggest('to')"></div>
</div>
<div id="from-sugg" class="sgs"></div>
<div id="to-sugg" class="sgs"></div>
<div class="g g2" style="margin-top:8px">
<div class="fld"><label class="fld-lbl">匹配模式</label>
<select id="match-mode" class="sel"><option value="fuzzy" selected>模糊匹配（同城全部站）</option><option value="exact">准确匹配（单站）</option></select></div>
<div class="fld"><label class="fld-lbl">搜索强度</label>
<select id="search-profile" class="sel"><option value="fast">快速</option><option value="balanced" selected>均衡</option><option value="thorough">全面</option><option value="complete">完整</option></select></div>
</div>
</div>

<div class="cd">
<div class="cd-hd">⏰ 时间约束（留空 = 全天）</div>
<div class="g g2">
<div class="fld"><label class="fld-lbl">出发不早于</label><input id="dep-after" class="inp" type="time"></div>
<div class="fld"><label class="fld-lbl">出发不晚于</label><input id="dep-before" class="inp" type="time"></div>
</div>
<div class="g g2">
<div class="fld"><label class="fld-lbl">到达不早于</label><input id="arr-after" class="inp" type="time"></div>
<div class="fld"><label class="fld-lbl">到达不晚于</label><input id="arr-before" class="inp" type="time"></div>
</div>
</div>

<div class="cd">
<div class="cd-hd">🔄 换乘设置</div>
<div class="g g3">
<div class="fld"><label class="fld-lbl">同站换乘时间（分钟）</label>
<div class="sr"><input type="range" id="same-rng" min="0" max="1440" value="15" oninput="syncRange('same')"><input type="number" id="same-num" min="0" max="1440" value="15" oninput="syncNum('same')"><span class="suf">min</span></div></div>
<div class="fld"><label class="fld-lbl">异站换乘时间（分钟）</label>
<div class="sr"><input type="range" id="inter-rng" min="0" max="1440" value="60" oninput="syncRange('inter')"><input type="number" id="inter-num" min="0" max="1440" value="60" oninput="syncNum('inter')"><span class="suf">min</span></div></div>
<div class="fld"><label class="fld-lbl">最大换乘次数</label>
<div class="sr"><input type="range" id="max-rng" min="0" max="10" value="3" oninput="syncRange('max')"><input type="number" id="max-num" min="0" max="10" value="3" oninput="syncNum('max')"><span class="suf">次</span></div></div>
</div>
<div class="g"><div class="fld"><label class="fld-lbl">指定换乘城市（留空 = 不限）</label>
<input id="xfer-at" class="inp" placeholder="如：武汉 / 郑州" autocomplete="off"></div></div>
</div>

<div class="bt-w"><button class="bt" onclick="search()">🚄 搜索出行方案</button></div>

<div id="results"></div>
</div>

<script>
function syncRange(which){
  let r=document.getElementById(which+'-rng'),n=document.getElementById(which+'-num');
  n.value=r.value
}
function syncNum(which){
  let r=document.getElementById(which+'-rng'),n=document.getElementById(which+'-num');
  r.value=n.value
}
async function suggest(which){
  let q=document.getElementById(which).value.trim();
  if(q.length<1)return;
  let r=await fetch('/api/match?q='+encodeURIComponent(q));
  let d=await r.json();
  let el=document.getElementById(which+'-sugg');
  el.innerHTML=d.matches.map(m=>'<button class="sg" onclick="document.getElementById(\''+which+'\').value=\''+m+'\';suggest(\''+which+'\')">'+m+'</button>').join('');
}
async function search(){
  let from=document.getElementById('from').value.trim(),to=document.getElementById('to').value.trim();
  if(!from||!to)return;
  let p=new URLSearchParams({from,to});
  p.set('match_mode',document.getElementById('match-mode').value);
  p.set('search_profile',document.getElementById('search-profile').value);
  function a(id){let v=document.getElementById(id).value;if(v)p.set(id.replace('-','_'),v)}
  a('dep-after');a('dep-before');a('arr-after');a('arr-before');
  p.set('same_transfer',document.getElementById('same-num').value);
  p.set('inter_transfer',document.getElementById('inter-num').value);
  p.set('max_transfers',document.getElementById('max-num').value);
  let xf=document.getElementById('xfer-at').value.trim();if(xf)p.set('xfer_at',xf);
  document.getElementById('results').innerHTML='<div class="empty"><span class="icon">🔍</span>搜索中…</div>';
  let r=await fetch('/api/search?'+p.toString());
  let d=await r.json();
  if(d.error){document.getElementById('results').innerHTML='<div class="empty"><span class="icon">⚠️</span>'+JSON.stringify(d.error)+'</div>';return}
  if(!d.routes||!d.routes.length){document.getElementById('results').innerHTML='<div class="empty"><span class="icon">📭</span>未找到方案</div>';return}
  render(d);
}
function bc(t){let c=t.charAt(0);return c=='G'?'bG':c=='D'?'bD':c=='C'?'bC':c=='K'||c=='T'||c=='Z'?'bK':'bG'}
function fm(m){return Math.floor(m/60)+'h'+(m%60).toString().padStart(2,'0')+'m'}
function td(t){return(t.display||t.time||'')}
function render(d){
  let direct=d.routes.filter(r=>r.train_transfers===0&&r.interstation_transfers===0);
  let transfer=d.routes.filter(r=>!(r.train_transfers===0&&r.interstation_transfers===0));
  window._routesData=d;

  let h='<div class="meta">';
  h+='<span>'+d.routes.length+' 个方案（直达 '+direct.length+' · 换乘 '+transfer.length+'）</span>';
  h+='<span>'+d.time+'s · 扫描 '+d.scanned+' 条</span>';
  if(!d.complete)h+='<span class="mtag">⚠ 搜索未完整</span>';
  h+='</div>';

  // Sort/filter bar
  h+='<div class="sf-bar">';
  h+='<label>排序</label><select id="sf-sort" onchange="reRender()"><option value="score">综合评分</option><option value="time">总耗时</option><option value="dist">总里程</option><option value="dep">出发时间</option></select>';
  h+='<label>筛选</label><select id="sf-xfer" onchange="reRender()"><option value="all">全部</option><option value="direct">仅直达</option><option value="same">仅同站换乘</option><option value="inter">含异站换乘</option></select>';
  h+='<input id="sf-city" placeholder="换乘城市" style="width:100px" oninput="reRender()">';
  h+='</div>';

  function buildTimetable(r){
    // Build 12306-style timetable table for each train segment
    let html='<div class="tt-wrap">';
    r.segments.forEach((s,si)=>{
      if(s.type!=='train') return;
      html+='<div class="tt-card">';
      html+='<div class="tt-hd"><span class="badge '+bc(s.train_code)+'">'+s.train_code+'</span> '+s.from_station+' → '+s.to_station+' · '+fm(s.travel_minutes)+' · '+s.distance+'km</div>';
      html+='<table class="tt-tbl"><tr><th>站名</th><th>到达</th><th>发车</th><th>停时</th></tr>';
      // Just show the boarding and alighting stations with times
      html+='<tr><td>'+s.from_station+'</td><td>-</td><td>'+td(s.depart)+'</td><td>-</td></tr>';
      html+='<tr><td>'+s.to_station+'</td><td>'+td(s.arrive)+'</td><td>-</td><td>-</td></tr>';
      html+='</table></div>';
    });
    // Ground transfers
    r.segments.forEach((s,si)=>{
      if(s.type!=='interstation') return;
      html+='<div class="tt-gnd">🚶 地面换乘 '+s.from_station+' → '+s.to_station+' · '+s.transfer_minutes+'min · '+s.city_name+'</div>';
    });
    html+='</div>';
    return html;
  }

  function renderGroup(routes,label,startIdx){
    if(!routes.length)return'';
    let g='<div class="sec-hd">'+label+'<span class="cnt">'+routes.length+' 条</span></div>';
    routes.forEach((r,i)=>{
      let scoreColor=r.score>=.6?'#16a34a':r.score>=.35?'#d97706':'#dc2626';
      let trainSegs=r.segments.filter(s=>s.type==='train');
      let totalH=fm(r.total_minutes);
      let firstDep=td(trainSegs[0]?.depart||r.first_departure);
      let lastArr=td(trainSegs[trainSegs.length-1]?.arrive||r.final_arrival);

      // Horizontal station flow
      let flowHtml='';
      r.segments.forEach((s,j)=>{
        if(j===0) flowHtml+='<div class="rc-node"><span class="rc-node-name">'+s.from_station+'</span><span class="rc-node-time">'+td(s.type==='train'?s.depart:s.start)+' 发</span></div>';
        if(s.type==='train') flowHtml+='<div class="rc-arrow-node"><span class="rc-arrow-code '+bc(s.train_code)+'">'+s.train_code+'</span><span class="rc-arrow-time">'+fm(s.travel_minutes)+'</span></div>';
        else flowHtml+='<div class="rc-arrow-node"><span class="rc-arrow-code" style="background:rgba(139,92,246,.08);color:#7c3aed">🚶'+s.transfer_minutes+'min</span></div>';
        flowHtml+='<div class="rc-node"><span class="rc-node-name">'+s.to_station+'</span><span class="rc-node-time">'+td(s.type==='train'?s.arrive:s.end)+' 到</span></div>';
      });

      let xferText='';
      if(r.train_transfers>0)xferText+='<span class="badge bC">换乘 '+r.train_transfers+' 次</span> ';
      if(r.interstation_transfers>0)xferText+='<span class="badge bI">地面 '+r.interstation_transfers+' 次</span> ';

      let tt=buildTimetable(r);

      g+='<div class="rc" onclick="this.classList.toggle(\'expanded\')">';
      g+='<div class="rc-main"><div class="rc-num" style="color:'+scoreColor+'">'+(startIdx+i+1)+'</div><div class="rc-body">';
      g+='<div class="rc-top"><span class="rc-route">'+r.actual_origin+' → '+r.actual_destination+'</span><span class="rc-time">'+firstDep+' → '+lastArr+'</span></div>';
      g+='<div class="rc-flow">'+flowHtml+'</div>';
      g+='<div class="rc-info"><span>⏱ '+totalH+'</span><span>🛤 '+r.rail_distance+'km</span>'+xferText+'</div>';
      g+='</div></div>';
      g+='<div class="rc-detail">'+tt+'</div>';
      g+='</div>';
    });
    return g;
  }
  h+=renderGroup(direct,'🚄 直达方案',0);
  h+=renderGroup(transfer,'🔄 换乘方案',direct.length);
  document.getElementById('results').innerHTML=h;
  setTimeout(initDropdowns,10);
}

function reRender(){
  if(!window._routesData)return;
  let d=window._routesData,routes=[...d.routes];
  let sb=document.getElementById('sf-sort')?.value||'score';
  let xf=document.getElementById('sf-xfer')?.value||'all';
  let cf=(document.getElementById('sf-city')?.value||'').trim();
  if(sb==='time')routes.sort((a,b)=>a.total_minutes-b.total_minutes);
  else if(sb==='dist')routes.sort((a,b)=>a.rail_distance-b.rail_distance);
  else if(sb==='dep')routes.sort((a,b)=>(a.first_departure?.minutes||0)-(b.first_departure?.minutes||0));
  if(xf==='direct')routes=routes.filter(r=>r.train_transfers===0&&r.interstation_transfers===0);
  else if(xf==='same')routes=routes.filter(r=>r.train_transfers>0&&r.interstation_transfers===0);
  else if(xf==='inter')routes=routes.filter(r=>r.interstation_transfers>0);
  if(cf)routes=routes.filter(r=>r.transfer_cities&&r.transfer_cities.some(c=>c.includes(cf)));
  if(sb!=='score'){let mT=Math.max(...routes.map(r=>r.total_minutes),1),mD=Math.max(...routes.map(r=>r.rail_distance),1);routes.forEach(r=>{r.score=Math.max(0,1-r.total_minutes/mT)*.5+Math.max(0,1-r.rail_distance/mD)*.3+Math.max(0,1-(r.train_transfers+r.interstation_transfers)/6)*.2})}
  render({...d,routes});
}

// Custom glass dropdowns
function wrapSelect(s){
  if(s.closest('.cdd'))return;
  var w=document.createElement('div');w.className='cdd';
  var b=document.createElement('div');b.className='cdd-btn';
  b.textContent=s.options[s.selectedIndex]?.text||'';
  var p=document.createElement('div');p.className='cdd-panel';
  for(var i=0;i<s.options.length;i++){
    var o=document.createElement('div');o.className='cdd-opt';
    o.textContent=s.options[i].text;
    if(i===s.selectedIndex)o.classList.add('sel');
    (function(idx){o.onclick=function(e){e.stopPropagation();
      s.selectedIndex=idx;s.dispatchEvent(new Event('change',{bubbles:true}));
      b.textContent=s.options[idx].text;
      p.querySelectorAll('.cdd-opt').forEach(function(x){x.classList.remove('sel')});
      o.classList.add('sel');w.classList.remove('open');
    }})(i);
    p.appendChild(o);
  }
  b.onclick=function(e){e.stopPropagation();
    document.querySelectorAll('.cdd.open').forEach(function(x){if(x!==w)x.classList.remove('open')});
    w.classList.toggle('open');
  };
  w.appendChild(b);w.appendChild(p);w.appendChild(s);
  s.parentNode.insertBefore(w,s);
}
document.querySelectorAll('select').forEach(wrapSelect);
  document.addEventListener('click',function(){document.querySelectorAll('.cdd.open').forEach(function(x){x.classList.remove('open')})});
function initDropdowns(){document.querySelectorAll('select').forEach(wrapSelect)}
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
        matches = fuzzy_match(q, self.graph, self.matcher)
        self._json({"matches": [m[1] for m in matches[:15]]})

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

        try:
            response = csa_search(self.graph, request, self.matcher)
            scored = score_routes(list(response.routes))
            results = [typed_route_to_dict(r, s) for s, r in scored]
            self._json({
                "routes": results,
                "time": round(time.time() - t0, 1),
                "source_stations": list(response.source_stations),
                "target_stations": list(response.target_stations),
                "complete": response.metadata.complete,
                "profile": response.metadata.profile,
                "scanned": response.metadata.scanned_connections,
                "generated": response.metadata.generated_states,
            })
        except ValueError as e:
            self._json({"error": {"code": "INTERNAL_ERROR", "message": str(e)}}, status=500)

    def _json(self, d, *, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
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
    p.add_argument("--match-mode", choices=["exact", "fuzzy"], default="fuzzy")
    p.add_argument("--search-profile", choices=["fast", "balanced", "thorough", "complete"], default="balanced")
    p.add_argument("--depart-after", default=""); p.add_argument("--depart-before", default="")
    p.add_argument("--arrive-before", default=""); p.add_argument("--gui", action="store_true")
    p.add_argument("--port", type=int, default=8000); p.add_argument("--max", type=int, default=15)
    p.add_argument("--same-transfer", type=int, default=DEFAULT_SAME_TRANSFER_MINUTES)
    p.add_argument("--inter-transfer", type=int, default=DEFAULT_INTER_TRANSFER_MINUTES)
    p.add_argument("--max-transfers", type=int, default=3)
    p.add_argument("--timeout", type=int, default=30)
    a = p.parse_args()

    print("加载全国铁路网络...", end=" ", flush=True); t0 = time.time()
    graph = RailwayGraph()
    graph.build(csv_path="data/output/车次时刻表.csv", station_js_path="data/timetable/station_name.js")
    matcher = build_matcher(graph, "data/timetable/station_name.js")
    print(f"({time.time()-t0:.1f}s)")

    if a.gui: run_gui(graph, matcher, a.port); return

    try:
        request = build_search_request({
            "from": a.from_station, "to": a.to_station,
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
