/* ═══════════════════════════════════════════════════════
   铁路出行路径规划 — 前端行为（搜索/渲染/排序/筛选/下拉/动效）
   ═══════════════════════════════════════════════════════ */
"use strict";

// ── 状态 ──
const _form = { matchMode: "fuzzy", profile: "balanced", fromMode: null, toMode: null };
// fromMode/toMode：null=跟随全局匹配模式；"fuzzy"/"exact"=该端独立选择
const _sf = { sort: "score", xfer: "all", city: "" };      // 排序筛选状态
const _trainCache = new Map();                              // 车次全程时刻表缓存
let _routesData = null;
let _showCount = 30;                                        // 结果分批展示数量

// ── 工具 ──
function $id(x) { return document.getElementById(x); }

// 线条 SVG 图标库（lucide 风格，stroke 1.8，无 emoji）
const _ICONS = {
  clock: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
  route: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M12 12L6 19"/><path d="M12 12l6-7"/><path d="M6 5l6 7"/></svg>',
  train: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="3" width="14" height="14" rx="3"/><path d="M5 9h14M9 3v6M15 3v6M8 17l-2 4M16 17l2 4"/></svg>',
  wait: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h14M5 21h14"/><path d="M7 3c0 6 10 6 10 0M7 21c0-6 10-6 10 0"/></svg>',
  walk: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="2"/><path d="M10 9l-2 4 3 2 1 6M10 9l3 1 3 5M10 9l-3 4"/></svg>',
  alert: '<svg class="ic ic-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3L2 20h20L12 3z"/><path d="M12 10v5"/><path d="M12 18h.01"/></svg>',
  bolt: '<svg class="ic ic-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z"/></svg>',
  direct: '<svg class="ic ic-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg>',
  repeat: '<svg class="ic ic-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg>',
  inbox: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13l3.5 7v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-7l3.5-7z"/></svg>',
};
function icon(name) { return _ICONS[name] || ""; }

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fm(m) { return Math.floor(m / 60) + "h" + (m % 60).toString().padStart(2, "0") + "m"; }

function td(t) { return (t && t.display) || ""; }

function bc(c) {
  const ch = String(c).charAt(0);
  return ch === "G" ? "bG" : ch === "D" ? "bD" : ch === "C" ? "bC" : (ch === "K" || ch === "T" || ch === "Z") ? "bK" : "bG";
}

function closeDropdowns() {
  document.querySelectorAll(".cdd.open").forEach(w => setDropdownState(w, false));
}

// 统一控制下拉开合：同时提升所在卡片/结果区的层叠上下文
function setDropdownState(w, open) {
  w.classList.toggle("open", open);
  document.querySelectorAll(".cd.z-top, #results.z-top").forEach(el => el.classList.remove("z-top"));
  if (open) {
    const lift = w.closest(".cd") || w.closest("#results");
    if (lift) lift.classList.add("z-top");
  }
}

// ── 自定义玻璃态下拉（纯 JS 构建）──
function buildDropdown(container, options, value, onChange) {
  container.innerHTML = "";
  const cur = options.find(o => o.value === value) || options[0];
  let current = value;
  const w = document.createElement("div");
  w.className = "cdd";
  const b = document.createElement("button");
  b.type = "button";
  b.className = "cdd-btn";
  b.textContent = cur.label;
  b.setAttribute("aria-expanded", "false");
  const p = document.createElement("div");
  p.className = "cdd-panel";
  options.forEach(o => {
    const d = document.createElement("div");
    d.className = "cdd-opt" + (o.value === current ? " sel" : "");
    d.textContent = o.label;
    d.addEventListener("click", () => {
      setDropdownState(w, false);
      b.setAttribute("aria-expanded", "false");
      if (o.value !== current) {
        current = o.value;
        b.textContent = o.label;
        p.querySelectorAll(".cdd-opt").forEach(x => x.classList.remove("sel"));
        d.classList.add("sel");
        onChange(o.value);
      }
    });
    p.appendChild(d);
  });
  // 三角按钮：点击 = 切换（开→关 / 关→开），不再只能展开
  b.addEventListener("click", e => {
    e.stopPropagation();
    const wasOpen = w.classList.contains("open");
    closeDropdowns();
    setDropdownState(w, !wasOpen);
    b.setAttribute("aria-expanded", String(!wasOpen));
  });
  w.appendChild(b);
  w.appendChild(p);
  container.appendChild(w);
}
document.addEventListener("click", closeDropdowns);

// ── 表单下拉初始化 ──
// 匹配模式由每端"全部站/本站"按钮独立控制（见 setEndMode），全局默认 fuzzy；
// 搜索强度为分段按钮组（位于高级选项面板内）。
document.querySelectorAll("#dd-search-profile .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    _form.profile = btn.dataset.v;
    document.querySelectorAll("#dd-search-profile .seg-btn").forEach(b =>
      b.classList.toggle("sel", b === btn));
  });
});

// ── 时间滚轮（时:分）：
// 离散滚轮步进（每格 1 单位，跨整值有顿挫脉冲）+ 拖拽惯性吸附
const ITEM_H = 26;
const WHEEL_ITEMS = { h: 24, m: 60 };   // 小时 0-23 / 分钟 0-59
const _wheelState = { same: { h: 0, m: 15 }, inter: { h: 1, m: 0 } };
_form.sameMin = 15; _form.interMin = 60;

function initWheel(rowId, key, onChange) {
  const row = $id(rowId);
  const wheels = row.querySelectorAll(".wheel");
  const onSel = (h, m) => {
    _wheelState[key] = { h, m };
    _form[key === "same" ? "sameMin" : "interMin"] = h * 60 + m;
    row.querySelectorAll(".wheel").forEach(w => {
      const kind = w.dataset.kind;
      const v = kind === "h" ? h : m;
      w.querySelectorAll(".wheel-item").forEach(it => it.classList.toggle("sel", +it.dataset.v === v));
    });
    if (onChange) onChange(h, m);
  };
  wheels.forEach(w => {
    const kind = w.dataset.kind;
    const max = WHEEL_ITEMS[kind] - 1;
    const view = w.querySelector(".wheel-view");
    const itemsEl = w.querySelector(".wheel-items");
    const frag = document.createDocumentFragment();
    for (let v = 0; v <= max; v++) {
      const it = document.createElement("div");
      it.className = "wheel-item";
      it.dataset.v = v;
      it.textContent = String(v).padStart(2, "0");
      frag.appendChild(it);
    }
    itemsEl.appendChild(frag);
    const setSel = v => {
      itemsEl.querySelectorAll(".wheel-item").forEach(it => it.classList.toggle("sel", +it.dataset.v === v));
    };
    const render = () => {
      itemsEl.style.setProperty("--wy", (-(pos) * ITEM_H) + "px");
      // 高亮可视区内最接近选中线的项
      setSel(Math.max(0, Math.min(max, Math.round(pos))));
    };
    let pos = Math.max(0, Math.min(max, kind === "h" ? _wheelState[key].h : _wheelState[key].m));
    render();

    // 滚轮步进：跨整值瞬间触发顿挫脉冲
    const step = d => {
      const np = Math.max(0, Math.min(max, pos + d));
      if (np === pos) return;
      pos = np;
      render();
      w.classList.remove("stepped");
      void w.offsetWidth;             // 重启动画
      w.classList.add("stepped");
      if (kind === "h") onSel(pos, _wheelState[key].m); else onSel(_wheelState[key].h, pos);
    };
    w.addEventListener("wheel", e => {
      e.preventDefault();
      step(e.deltaY > 0 ? 1 : -1);
    }, { passive: false });
    // 键盘步进（可聚焦控件）
    w.tabIndex = 0;
    w.addEventListener("keydown", e => {
      if (e.key === "ArrowUp") { e.preventDefault(); step(-1); }
      else if (e.key === "ArrowDown") { e.preventDefault(); step(1); }
    });
    // 点击某项直接选择（pointer capture 会把 click 目标归到捕获元素，
    // 需用坐标反查实际命中的 item）
    w.addEventListener("click", e => {
      const hit = document.elementFromPoint(e.clientX, e.clientY);
      const it = hit && hit.closest ? hit.closest(".wheel-item") : null;
      if (!it) return;
      step(+it.dataset.v - Math.round(pos));
    });

    // 拖拽 + 惯性 + 吸附
    let dragging = false, startY = 0, startPos = 0, lastY = 0, lastT = 0, vels = [], lastInt = Math.round(pos);
    const tick = () => {
      w.classList.remove("stepped");
      void w.offsetWidth;
      w.classList.add("stepped");
    };
    w.addEventListener("pointerdown", e => {
      dragging = true;
      w.classList.add("dragging");
      startY = e.clientY; startPos = pos; lastY = e.clientY; lastT = performance.now();
      vels = []; lastInt = Math.round(pos);
      w.setPointerCapture(e.pointerId);
    });
    w.addEventListener("pointermove", e => {
      if (!dragging) return;
      const dy = e.clientY - startY;
      const now = performance.now();
      const dt = now - lastT;
      if (dt > 8) { vels.push((e.clientY - lastY) / dt); if (vels.length > 6) vels.shift(); lastY = e.clientY; lastT = now; }
      const np = Math.max(-1.5, Math.min(max + 1.5, startPos - dy / ITEM_H));
      pos = np; render();
      // 划过整值瞬间：顿挫脉冲（视觉咔哒感）
      const curInt = Math.round(np);
      if (curInt !== lastInt) { lastInt = curInt; tick(); }
    });
    const finish = e => {
      if (!dragging) return;
      dragging = false;
      w.classList.remove("dragging");
      const vel = vels.length ? vels.reduce((a, b) => a + b, 0) / vels.length : 0;
      const target = Math.round(pos - vel * 0.18);   // 惯性偏移
      const finalPos = Math.max(0, Math.min(max, target));
      itemsEl.style.transition = "transform .38s cubic-bezier(.22,1,.36,1)";
      pos = finalPos; render();
      setTimeout(() => { itemsEl.style.transition = ""; }, 400);
      if (kind === "h") onSel(finalPos, _wheelState[key].m); else onSel(_wheelState[key].h, finalPos);
    };
    w.addEventListener("pointerup", finish);
    w.addEventListener("pointercancel", finish);
  });
  onSel(_wheelState[key].h, _wheelState[key].m);
}

initWheel("same-wheel", "same");
initWheel("inter-wheel", "inter");

// 最大换乘滑块与数字同步
["max"].forEach(k => {
  const r = $id(k + "-rng"), n = $id(k + "-num");
  r.addEventListener("input", () => { n.value = r.value; });
  n.addEventListener("input", () => { r.value = n.value; });
});

// ── 站名建议 ──
async function suggest(which) {
  const q = $id(which).value.trim();
  const el = $id(which + "-sugg");
  el.innerHTML = "";
  if (q.length < 1) return;
  let d;
  try {
    d = await fetch("/api/match?q=" + encodeURIComponent(q)).then(r => r.json());
  } catch (e) { return; }
  (d.matches || []).slice(0, 15).forEach(m => {
    const b = document.createElement("button");
    b.className = "sg";
    b.textContent = m;
    b.addEventListener("click", () => { $id(which).value = m; suggest(which); });
    el.appendChild(b);
  });
}
$id("from").addEventListener("input", () => suggest("from"));
$id("to").addEventListener("input", () => suggest("to"));

// ── 每端独立匹配模式：全部站(fuzzy) / 本站(exact) ──
function setEndMode(key, mode) {
  _form[key] = mode;
  const btn = $id(key.replace("Mode", "-mode"));
  btn.textContent = mode === "exact" ? "本站" : "全部站";
  btn.classList.toggle("on", mode === "exact");
  btn.title = mode === "exact" ? "当前：仅本站（点击切回该端全部站）" : "当前：该端全部站（点击仅本站）";
}
$id("from-mode").addEventListener("click", () => setEndMode("fromMode", _form.fromMode === "exact" ? "fuzzy" : "exact"));
$id("to-mode").addEventListener("click", () => setEndMode("toMode", _form.toMode === "exact" ? "fuzzy" : "exact"));

// ── 搜索 ──
async function search() {
  const from = $id("from").value.trim(), to = $id("to").value.trim();
  if (!from || !to) return;
  const p = new URLSearchParams({ from, to, match_mode: _form.matchMode, search_profile: _form.profile });
  if (_form.fromMode) p.set("from_mode", _form.fromMode);
  if (_form.toMode) p.set("to_mode", _form.toMode);
  ["dep-after", "dep-before", "arr-after", "arr-before"].forEach(id => {
    const v = $id(id).value;
    if (v) p.set(id.replace("-", "_"), v);
  });
  p.set("same_transfer", _form.sameMin);
  p.set("inter_transfer", _form.interMin);
  p.set("max_transfers", $id("max-num").value);
  const xf = $id("xfer-at").value.trim();
  if (xf) p.set("xfer_at", xf);
  _showCount = 30;
  $id("results").innerHTML = '<div class="empty search-loading"><span class="spinner" aria-hidden="true"></span>搜索中…</div>';
  let d;
  try {
    d = await fetch("/api/search?" + p.toString()).then(r => r.json());
  } catch (e) {
    $id("results").innerHTML = '<div class="empty">网络错误，请确认服务已启动</div>';
    return;
  }
  if (d.error) {
    $id("results").innerHTML = '<div class="empty">' + icon("alert") + ' ' + esc(d.error.code + ": " + d.error.message) + "</div>";
    return;
  }
  if (!d.routes || !d.routes.length) {
    $id("results").innerHTML = '<div class="empty"></div>';
    return;
  }
  _routesData = d;
  render();
}

// ── 渲染：meta + 排序筛选条 + 路线列表 ──
function render() {
  if (!_routesData) return;
  const d = _routesData;
  const direct = d.routes.filter(r => r.train_transfers === 0 && r.interstation_transfers === 0);
  const xfer = d.routes.filter(r => !(r.train_transfers === 0 && r.interstation_transfers === 0));

  let h = '<div class="meta">'
    + "<span>" + d.routes.length + " 个方案（直达 " + direct.length + " · 换乘 " + xfer.length + "）</span>"
    + "<span>" + d.time + "s · 扫描 " + d.scanned + " 条</span>"
    + (d.cached ? '<span class="mtag-hit">' + icon("bolt") + ' 缓存命中</span>' : "")
    + (d.complete ? "" : '<span class="mtag">' + icon("alert") + ' 搜索未完整</span>')
    + "</div>";

  h += '<div class="sf-bar" id="sf-bar">'
    + '<label>排序</label><span id="sf-sort-c"></span>'
    + '<label>筛选</label><span id="sf-xfer-c"></span>'
    + '<input id="sf-city" placeholder="换乘城市/站名">'
    + '<button class="sf-clear" id="sf-clear" type="button">重置</button>'
    + '</div>'
    + '<div id="route-list"></div>';

  $id("results").innerHTML = h;

  buildDropdown($id("sf-sort-c"), [
    { value: "score", label: "综合评分" },
    { value: "time", label: "总耗时" },
    { value: "dist", label: "总里程" },
    { value: "dep", label: "出发时间" },
    { value: "arr", label: "到达时间" },
    { value: "xfer", label: "换乘次数" },
  ], _sf.sort, v => { _sf.sort = v; renderList(); });

  buildDropdown($id("sf-xfer-c"), [
    { value: "all", label: "全部" },
    { value: "direct", label: "仅直达" },
    { value: "same", label: "仅同站换乘" },
    { value: "inter", label: "含异站换乘" },
  ], _sf.xfer, v => { _sf.xfer = v; renderList(); });

  $id("sf-city").value = _sf.city;
  $id("sf-city").addEventListener("input", () => { _sf.city = $id("sf-city").value.trim(); renderList(); });
  $id("sf-clear").addEventListener("click", () => { _sf.sort = "score"; _sf.xfer = "all"; _sf.city = ""; _showCount = 30; render(); });

  // 事件委托：卡片展开/收起 + 完整时刻表折叠 + 加载更多
  $id("route-list").addEventListener("click", e => {
    const more = e.target.closest(".tt-more");
    if (more) {
      const holder = more.closest(".tt-card");
      if (holder) {
        holder.classList.toggle("full");
        more.textContent = holder.classList.contains("full") ? "▴ 收起完整时刻表" : "▾ 查看完整时刻表";
      }
      return;
    }
    if (e.target.id === "load-more") {
      _showCount += 30;
      renderList();
      return;
    }
    const card = e.target.closest(".rc");
    if (card && !e.target.closest(".cdd, .tt-more, #load-more")) {
      card.classList.toggle("expanded");
      if (card.classList.contains("expanded")) ensureTimetable(card);
    }
  });

  renderList();
}

// ── 排序 + 筛选 + 重渲染列表 ──
function renderList() {
  const list = $id("route-list");
  if (!list || !_routesData) return;
  let routes = [..._routesData.routes];
  const { sort, xfer, city } = _sf;

  if (xfer === "direct") routes = routes.filter(r => r.train_transfers === 0 && r.interstation_transfers === 0);
  else if (xfer === "same") routes = routes.filter(r => r.train_transfers > 0 && r.interstation_transfers === 0);
  else if (xfer === "inter") routes = routes.filter(r => r.interstation_transfers > 0);
  if (city) {
    routes = routes.filter(r => {
      if (r.transfer_cities && r.transfer_cities.some(c => c.includes(city))) return true;
      return r.segments.some(s => s.from_station.includes(city) || s.to_station.includes(city));
    });
  }

  if (sort === "time") routes.sort((a, b) => a.total_minutes - b.total_minutes);
  else if (sort === "dist") routes.sort((a, b) => a.rail_distance - b.rail_distance);
  else if (sort === "dep") routes.sort((a, b) => a.first_departure.minutes - b.first_departure.minutes);
  else if (sort === "arr") routes.sort((a, b) => a.final_arrival.minutes - b.final_arrival.minutes);
  else if (sort === "xfer") routes.sort((a, b) =>
    (a.train_transfers + a.interstation_transfers) - (b.train_transfers + b.interstation_transfers));

  const direct = routes.filter(r => r.train_transfers === 0 && r.interstation_transfers === 0);
  const xferRoutes = routes.filter(r => !(r.train_transfers === 0 && r.interstation_transfers === 0));

  if (!routes.length) {
    list.innerHTML = '<div class="empty">' + icon("inbox") + ' 当前筛选条件下无匹配方案</div>';
    return;
  }

  // 分批展示：前 _showCount 条 + 加载更多（大批量结果不全量渲染 DOM）
  const shown = routes.slice(0, _showCount);
  const shownDirect = shown.filter(r => r.train_transfers === 0 && r.interstation_transfers === 0);
  const shownXfer = shown.filter(r => !(r.train_transfers === 0 && r.interstation_transfers === 0));
  let h = renderGroup(shownDirect, "直达方案", 0, direct.length) + renderGroup(shownXfer, "换乘方案", shownDirect.length, xferRoutes.length);
  if (routes.length > _showCount) {
    h += '<div class="lm-w"><button id="load-more" class="sf-clear" type="button">显示更多（剩余 ' + (routes.length - _showCount) + " 条）</button></div>";
  }
  list.innerHTML = h;
}

// ── 路线卡片分组（含入场动效 stagger）──
function renderGroup(routes, label, startIdx, totalInGroup) {
  if (!routes.length) return "";
  const grpIcon = label.includes("直达") ? icon("direct") : icon("repeat");
  let g = '<div class="sec-hd">' + grpIcon + label + '<span class="cnt">' + totalInGroup + " 条</span></div>";
  routes.forEach((r, i) => {
    const numCls = r.score >= 0.6 ? "n-good" : r.score >= 0.35 ? "n-mid" : "n-bad";
    const trainSegs = r.segments.filter(s => s.type === "train");
    const firstDep = trainSegs.length ? td(trainSegs[0].depart) : td(r.first_departure);
    const lastArr = trainSegs.length ? td(trainSegs[trainSegs.length - 1].arrive) : td(r.final_arrival);
    let xferText = "";
    if (r.train_transfers > 0) xferText += '<span class="badge bC">换乘 ' + r.train_transfers + " 次</span> ";
    if (r.interstation_transfers > 0) xferText += '<span class="badge bI">地面 ' + r.interstation_transfers + " 次</span> ";

    // 贴生活：乘坐时长 + 等待时长（换乘时展示，等待 = 总耗时 - 行驶 - 地面）
    const travelMin = trainSegs.reduce((a, s) => a + (s.travel_minutes || 0), 0);
    const groundMin = r.interstation_minutes || 0;
    const waitMin = r.total_minutes - travelMin - groundMin;
    let waitText = "";
    if (waitMin > 0) waitText = '<span class="rc-wait">' + icon("wait") + ' 等 ' + fm(waitMin) + "</span>";

    // 入场动效：前 12 张卡片错峰淡入（transform/opacity → GPU 合成）
    const delay = Math.min(i, 11) * 45;

    g += '<div class="rc" style="animation-delay:' + delay + 'ms">'
      + '<div class="rc-main"><div class="rc-num ' + numCls + '">' + (startIdx + i + 1) + "</div><div class=\"rc-body\">"
      + '<div class="rc-top"><span class="rc-route">' + esc(r.actual_origin) + " → " + esc(r.actual_destination)
      + '</span><span class="rc-time">' + esc(firstDep) + " → " + esc(lastArr) + "</span></div>"
      + '<div class="rc-flow">' + buildRouteFlow(r) + "</div>"
      + '<div class="rc-info"><span class="rc-stat">' + icon("clock") + ' ' + fm(r.total_minutes) + "</span>"
      + '<span class="rc-stat">' + icon("route") + ' ' + r.rail_distance + "km</span>"
      + '<span class="rc-stat">' + icon("train") + ' 乘 ' + fm(travelMin) + "</span>" + waitText + xferText + "</div>"
      + "</div></div>"
      + '<div class="rc-detail"><div class="rc-detail-inner"><div class="tt-wrap">'
      + buildTimetableSkeleton(r)
      + "</div></div></div>"
      + "</div>";
  });
  return g;
}

// ── 路线流：紧凑节点 + 箭头 ──
function buildRouteFlow(r) {
  const ev = [];
  r.segments.forEach(s => {
    const depT = s.type === "train" ? td(s.depart) : td(s.start);
    const arrT = s.type === "train" ? td(s.arrive) : td(s.end);
    ev.push({ station: s.from_station, dep: depT });
    if (s.type === "train") {
      ev.push({ train: s.train_code, dur: fm(s.travel_minutes), cls: bc(s.train_code) });
    } else {
      ev.push({ train: null, dur: s.transfer_minutes + "min", cls: "bI", sub: s.city_name || "" });
    }
    ev.push({ station: s.to_station, arr: arrT });
  });
  let h = "";
  for (let i = 0; i < ev.length; i++) {
    const e = ev[i];
    if (e.train !== undefined) {
      h += '<div class="rc-arrow-node"><span class="rc-arrow-code ' + e.cls + '">'
        + (e.train ? esc(e.train) : icon("walk") + " " + e.dur) + "</span><span class=\"rc-arrow-time\">"
        + esc(e.dur) + (e.sub ? " · " + esc(e.sub) : "") + "</span></div>";
      continue;
    }
    const next = ev[i + 1];
    if (e.arr !== undefined && next && next.station === e.station && next.dep !== undefined) {
      h += '<div class="rc-node"><span class="rc-node-name">' + esc(e.station)
        + '</span><span class="rc-node-time">' + esc(e.arr) + "到 · " + esc(next.dep) + "发</span></div>";
      i++;
    } else if (e.dep !== undefined) {
      h += '<div class="rc-node"><span class="rc-node-name">' + esc(e.station)
        + '</span><span class="rc-node-time">' + esc(e.dep) + "发</span></div>";
    } else if (e.arr !== undefined) {
      h += '<div class="rc-node"><span class="rc-node-name">' + esc(e.station)
        + '</span><span class="rc-node-time">' + esc(e.arr) + "到</span></div>";
    }
  }
  return h;
}

// ── 展开详情骨架：每段列车卡片（时刻表数据 lazy 加载后填充）──
function buildTimetableSkeleton(r) {
  let h = "";
  r.segments.forEach((s, i) => {
    if (s.type === "train") {
      h += '<div class="tt-card" data-code="' + esc(s.train_code) + '" data-from="' + esc(s.from_station)
        + '" data-to="' + esc(s.to_station) + '">'
        + '<div class="tt-hd"><span class="badge ' + bc(s.train_code) + '">' + esc(s.train_code) + "</span> "
        + '<span class="tt-seg">' + esc(s.from_station) + " → " + esc(s.to_station) + " · " + fm(s.travel_minutes) + " · " + s.distance + "km</span>"
        + '<span class="tt-route"></span>'
        + "</div>"
        + '<div class="tt-body"><div class="tt-load"><span class="spinner" aria-hidden="true"></span>加载时刻表…</div></div>'
        + "</div>";
    } else {
      h += '<div class="tt-gnd">' + icon("walk") + ' 地面换乘 ' + esc(s.from_station) + " → " + esc(s.to_station)
        + " · " + s.transfer_minutes + "min · " + esc(s.city_name || "") + "</div>";
    }
  });
  return h;
}

// ── 车次全程时刻表（带缓存）──
async function fetchTrain(code) {
  if (_trainCache.has(code)) return _trainCache.get(code);
  try {
    const d = await fetch("/api/train?code=" + encodeURIComponent(code)).then(r => r.json());
    _trainCache.set(code, d);
    return d;
  } catch (e) {
    return null;
  }
}

// ── 填充展开详情：始发终到 + 上车上一站→下车下一站区间 + 完整折叠 ──
function fillTimetable(card, data, fromName, toName) {
  const stops = (data && data.stops) || [];
  const hd = card.querySelector(".tt-route");
  if (stops.length) {
    hd.innerHTML = '始发 <b>' + esc(stops[0].station) + "</b> · 终到 <b>" + esc(stops[stops.length - 1].station) + "</b>";
  }
  const body = card.querySelector(".tt-body");
  if (!stops.length) {
    body.innerHTML = '<div class="tt-load">暂无时刻表数据</div>';
    return;
  }
  const fromIdx = stops.findIndex(s => s.station === fromName);
  const toIdx = stops.findIndex(s => s.station === toName);
  if (fromIdx < 0 || toIdx < 0 || fromIdx > toIdx) {
    body.innerHTML = '<table class="tt-tbl">' + stops.map(rowHtml).join("") + "</table>";
    return;
  }
  // 区间：上车站上一站 → 下车站下一站（含始发/终到则从首/尾起）
  const startIdx = Math.max(0, fromIdx - 1);
  const endIdx = Math.min(stops.length - 1, toIdx + 1);
  const segRows = stops.slice(startIdx, endIdx + 1).map((s, i) =>
    rowHtml(s, i + startIdx === fromIdx ? "hl-from" : i + startIdx === toIdx ? "hl-to" : ""));
  // 完整时刻表（折叠）
  const fullRows = stops.map((s, i) => rowHtml(s, i === fromIdx ? "hl-from" : i === toIdx ? "hl-to" : ""));
  let h = '<table class="tt-tbl"><tr><th>站名</th><th>到达</th><th>发车</th><th>停时</th></tr>' + segRows.join("") + "</table>";
  if (stops.length > endIdx - startIdx + 1) {
    h += '<button class="tt-more" type="button">▾ 查看完整 ' + stops.length + " 站时刻表</button>"
      + '<div class="tt-full"><table class="tt-tbl"><tr><th>站名</th><th>到达</th><th>发车</th><th>停时</th></tr>'
      + fullRows.join("") + "</table></div>";
  }
  body.innerHTML = h;
}

function rowHtml(s, cls) {
  const arr = s.arrive ? td(s.arrive) : "-";
  const dep = s.depart ? td(s.depart) : "-";
  let stay = "-";
  if (s.arrive && s.depart) stay = (s.depart.minutes - s.arrive.minutes) + "min";
  return '<tr class="' + cls + '"><td>' + esc(s.station) + "</td><td>" + esc(arr) + "</td><td>" + esc(dep) + "</td><td>" + stay + "</td></tr>";
}

// ── 展开时懒加载该卡片全部车次的全程时刻表 ──
async function ensureTimetable(card) {
  const cards = card.querySelectorAll(".tt-card[data-code]");
  const pending = [];
  cards.forEach(c => {
    if (!c.dataset.loaded) {
      c.dataset.loaded = "1";
      pending.push(fetchTrain(c.dataset.code).then(data => {
        fillTimetable(c, data, c.dataset.from, c.dataset.to);
      }));
    }
  });
  await Promise.all(pending);
}

// ── 事件绑定 ──
$id("btn-search").addEventListener("click", search);
$id("from").addEventListener("keydown", e => { if (e.key === "Enter") search(); });
$id("to").addEventListener("keydown", e => { if (e.key === "Enter") search(); });

// 高级选项折叠（手风琴）
$id("adv-toggle").addEventListener("click", () => {
  const panel = $id("adv-panel");
  const btn = $id("adv-toggle").querySelector(".adv-btn");
  const open = !panel.classList.contains("open");
  panel.classList.toggle("open", open);
  btn.classList.toggle("open", open);
  btn.setAttribute("aria-expanded", String(open));
});

// 交换起终点
$id("btn-swap").addEventListener("click", () => {
  const f = $id("from").value, t = $id("to").value;
  $id("from").value = t;
  $id("to").value = f;
  suggest("from");
  suggest("to");
});

/* ═══════════════════════════════════════════════════════
   实时流动背景：Canvas 2D 多色块融合渲染
   思路（调研自开源社区 FluidGradient 等方案）：多层半透明大
   径向渐变"色块"逐帧重新计算位置并叠加，色块交融、自然流动；
   运动由互质多频正弦叠加驱动（平滑伪随机漫游），初始相位/频率/
   位置/半径/透明度每次加载随机 → 图案不可预测且始终缓慢缓和
   （周期约 30-120s）；canvas 渐变自身柔和，无需 filter:blur，
   逐帧绘制由 GPU 合成，开销极低。
   ═══════════════════════════════════════════════════════ */
(() => {
  const cv = document.getElementById("bg-canvas");
  if (!cv || !cv.getContext) return;                 // 无 canvas 时保留 CSS 渐变底图
  const ctx = cv.getContext("2d");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // 保持原多色配色：indigo/violet · pink/orange · emerald/teal · amber
  const PALETTE = [
    [129, 140, 248], [192, 132, 252], [244, 114, 182],
    [251, 146, 60], [52, 211, 153], [45, 212, 191], [251, 191, 36],
  ];
  const rand = (a, b) => a + Math.random() * (b - a);

  let W = 0, H = 0, blobs = [];

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth; H = window.innerHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    cv.style.width = W + "px"; cv.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function spawn() {
    blobs = [];
    const n = 6;
    for (let i = 0; i < n; i++) {
      const c = PALETTE[i % PALETTE.length];
      blobs.push({
        c,
        cx: rand(0, 1), cy: rand(0, 1),            // 归一化中心
        r: rand(0.30, 0.52),                       // 归一化半径（大色块）
        ph: rand(0, Math.PI * 2),                  // 随机相位
        s1: rand(0.00010, 0.00022),                // 慢频（周期约 30-60s）
        s2: rand(0.00005, 0.00011),                // 更慢频（约 60-120s，与 s1 互质感）
        a1: rand(0.13, 0.24), a2: rand(0.08, 0.17),// 漂移幅度（归一化）
        o1: rand(0.26, 0.36), o2: rand(0.08, 0.14),// 渐变透明度（柔和交融）
      });
    }
  }

  function draw(t) {
    ctx.clearRect(0, 0, W, H);
    for (const b of blobs) {
      const u = t * b.s1, v = t * b.s2;
      // 多频正弦叠加：位置平滑漫游；频率/相位各异 → 各色块轨迹互不相同
      const x = (b.cx + Math.sin(u + b.ph) * b.a1 + Math.sin(v + b.ph * 1.7) * b.a2) * W;
      const y = (b.cy + Math.cos(u * 0.8 + b.ph * 2.1) * b.a1 + Math.sin(v * 1.3 + b.ph) * b.a2) * H;
      const r = b.r * Math.min(W, H) * (1 + 0.10 * Math.sin(t * 0.00008 + b.ph * 3)); // 缓慢呼吸
      const c = b.c;
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},${b.o1})`);
      g.addColorStop(0.62, `rgba(${c[0]},${c[1]},${c[2]},${b.o2})`);
      g.addColorStop(1, `rgba(${c[0]},${c[1]},${c[2]},0)`);
      ctx.fillStyle = g;
      ctx.fillRect(x - r, y - r, 2 * r, 2 * r);
    }
  }

  resize();
  spawn();
  addEventListener("resize", () => { resize(); if (reduced) draw(0); });
  if (reduced) { draw(0); return; }                // 减少动效偏好：只渲染静态一帧
  const loop = (ts) => { draw(ts); requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
})();
