/* ═══════════════════════════════════════════════════════
   铁路出行路径规划 — 前端行为（搜索/渲染/排序/筛选/下拉/动效）
   ═══════════════════════════════════════════════════════ */
"use strict";

// ── 状态（默认值取自设置；用户可在表单/设置面板中临时或持久调整）──
const _settings0 = loadSettings();
const _form = { matchMode: _settings0.matchMode, profile: _settings0.profile, fromMode: null, toMode: null };
// fromMode/toMode：null=跟随全局匹配模式；"fuzzy"/"exact"=该端独立选择
const _sf = { sort: "score", xfer: "all", city: "", view: "direct" }; // 排序筛选状态 + 直达/换乘视图
const _trainCache = new Map();                              // 车次全程时刻表缓存
let _routesData = null;

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

// ── 桌面应用模式：自绘标题栏（pywebview frameless 窗口）──
// 三重保障确保标题栏一定显示：
//   1) JS 桥已就绪（window.pywebview 立即可用）
//   2) pywebviewready 事件（桥注入晚于页面脚本时的标准时序）
//   3) 打包 URL 显式携带 ?app=1（run_app 加载时附加，兜底）
function initAppMode() {
  if (document.body.classList.contains("app-mode")) return;
  document.body.classList.add("app-mode");
  const tb = document.getElementById("titlebar");
  if (tb) tb.hidden = false;
  const min = document.getElementById("tb-min");
  const maxBtn = document.getElementById("tb-max");
  const close = document.getElementById("tb-close");
  if (min) min.addEventListener("click", () => { try { window.pywebview.api.minimize(); } catch (e) {} });
  if (close) close.addEventListener("click", () => { try { window.pywebview.api.close(); } catch (e) {} });
  // 最大化/还原：点击切换图标；双击标题栏拖动区也可切换（Windows 习惯）。
  // 图标状态以 js_api 返回值同步（而非本地猜测），双击/按钮/系统操作统一。
  const toggleMax = async () => {
    try {
      const maxed = await window.pywebview.api.toggle_maximize();
      if (maxed === null) return;
      maxBtn.classList.toggle("maxed", !!maxed);
      maxBtn.title = maxed ? t("tb.restore") : t("tb.max");
    } catch (e) {}
  };
  if (maxBtn) maxBtn.addEventListener("click", toggleMax);
  if (tb) {
    const brand = tb.querySelector(".tb-brand");
    if (brand) brand.addEventListener("dblclick", toggleMax);
  }
  initResizeHandles();
}

// frameless 窗口无系统调整大小边框：在窗口边缘放置透明热区，
// 拖拽时经 js_api 调用 resize（固定左上角缩放 → 右/下/右下角行为正确）。
function initResizeHandles() {
  if (document.getElementById("resize-e")) return;
  const mk = (id, cls, cursor) => {
    const el = document.createElement("div");
    el.id = id;
    el.className = "resize-handle " + cls;
    el.style.cursor = cursor;
    document.body.appendChild(el);
    return el;
  };
  const hE = mk("resize-e", "re-e", "ew-resize");
  const hS = mk("resize-s", "re-s", "ns-resize");
  const hSE = mk("resize-se", "re-se", "nwse-resize");
  const THROTTLE = 16;
  const startResize = (handle, mode) => e => {
    e.preventDefault();
    const startX = e.clientX, startY = e.clientY;
    const w0 = window.innerWidth, h0 = window.innerHeight;
    let last = 0;
    const move = ev => {
      const now = performance.now();
      if (now - last < THROTTLE) return;
      last = now;
      let nw = w0, nh = h0;
      if (mode === "e" || mode === "se") nw = Math.max(640, w0 + (ev.clientX - startX));
      if (mode === "s" || mode === "se") nh = Math.max(480, h0 + (ev.clientY - startY));
      try { window.pywebview.api.resize_window(nw, nh); } catch (err) {}
    };
    const up = () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
  };
  hE.addEventListener("pointerdown", startResize(hE, "e"));
  hS.addEventListener("pointerdown", startResize(hS, "s"));
  hSE.addEventListener("pointerdown", startResize(hSE, "se"));
}
if (window.pywebview) initAppMode();
window.addEventListener("pywebviewready", initAppMode);
if (new URLSearchParams(location.search).has("app")) initAppMode();

// ── 时间滚轮（时:分）：
// 离散滚轮步进（每格 1 单位，跨整值有顿挫脉冲）+ 拖拽惯性吸附
const ITEM_H = 26;
const RULE_TOP = 52;   // 选中行顶部距可视区顶部（= (130 - 26) / 2，选中数字居中）
const WHEEL_ITEMS = { h: 24, m: 60 };   // 小时 0-23 / 分钟 0-59
const _wheelState = {
  same: { h: Math.floor((_settings0.sameTransfer || 15) / 60), m: (_settings0.sameTransfer || 15) % 60 },
  inter: { h: Math.floor((_settings0.interTransfer || 60) / 60), m: (_settings0.interTransfer || 60) % 60 },
};
_form.sameMin = _settings0.sameTransfer || 15; _form.interMin = _settings0.interTransfer || 60;
// 时间约束（4 个字段：null = 全天不限）
_form.times = { depAfter: null, depBefore: null, arrAfter: null, arrBefore: null };

// 单个滚轮（动态构建；换乘设置与时间约束选择器共用）
function createWheel(host, kind, initValue, onValue) {
  const max = WHEEL_ITEMS[kind] - 1;
  const w = document.createElement("div");
  w.className = "wheel";
  const view = document.createElement("div");
  view.className = "wheel-view";
  const itemsEl = document.createElement("div");
  itemsEl.className = "wheel-items";
  const frag = document.createDocumentFragment();
  for (let v = 0; v <= max; v++) {
    const it = document.createElement("div");
    it.className = "wheel-item";
    it.dataset.v = v;
    it.textContent = String(v).padStart(2, "0");
    frag.appendChild(it);
  }
  itemsEl.appendChild(frag);
  view.appendChild(itemsEl);
  const mask = document.createElement("div");
  mask.className = "wheel-mask";
  const rule = document.createElement("div");
  rule.className = "wheel-rule";
  w.appendChild(view); w.appendChild(mask); w.appendChild(rule);
  host.appendChild(w);

  const setSel = v => {
    itemsEl.querySelectorAll(".wheel-item").forEach(it => it.classList.toggle("sel", +it.dataset.v === v));
  };
  const render = () => {
    itemsEl.style.setProperty("--wy", (RULE_TOP - pos * ITEM_H) + "px");
    setSel(Math.max(0, Math.min(max, Math.round(pos))));
  };
  let pos = Math.max(0, Math.min(max, initValue));
  render();

  const tick = () => {
    w.classList.remove("stepped");
    void w.offsetWidth;
    w.classList.add("stepped");
  };
  // 滚轮步进：跨整值瞬间触发顿挫脉冲
  const step = d => {
    const np = Math.max(0, Math.min(max, pos + d));
    if (np === pos) return;
    pos = np;
    render();
    tick();
    onValue(Math.round(pos));
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
  // 需用坐标反查实际命中的 item）；阻止冒泡：滚轮在时间选择器面板内时，
  // 点击不应触发 document 的"点击外部关闭面板"
  w.addEventListener("click", e => {
    e.stopPropagation();
    const hit = document.elementFromPoint(e.clientX, e.clientY);
    const it = hit && hit.closest ? hit.closest(".wheel-item") : null;
    if (!it) return;
    step(+it.dataset.v - Math.round(pos));
  });

  // 拖拽 + 惯性 + 吸附
  let dragging = false, startY = 0, startPos = 0, lastY = 0, lastT = 0, vels = [], lastInt = Math.round(pos);
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
    const curInt = Math.round(np);
    if (curInt !== lastInt) { lastInt = curInt; tick(); }
  });
  const finish = e => {
    if (!dragging) return;
    dragging = false;
    w.classList.remove("dragging");
    const vel = vels.length ? vels.reduce((a, b) => a + b, 0) / vels.length : 0;
    const target = Math.round(pos - vel * 0.18);
    const finalPos = Math.max(0, Math.min(max, target));
    itemsEl.style.transition = "transform .38s cubic-bezier(.22,1,.36,1)";
    pos = finalPos; render();
    setTimeout(() => { itemsEl.style.transition = ""; }, 400);
    onValue(finalPos);
  };
  w.addEventListener("pointerup", finish);
  w.addEventListener("pointercancel", finish);

  return {
    setValue(v) { pos = Math.max(0, Math.min(max, v)); render(); },
  };
}

// 换乘设置的"时:分"滚轮对（同站/异站）
function initWheel(rowId, key, onChange) {
  const row = $id(rowId);
  row.innerHTML = "";
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
  const hW = createWheel(row, "h", _wheelState[key].h, v => onSel(v, _wheelState[key].m));
  const unitH = document.createElement("span"); unitH.className = "wheel-unit"; unitH.textContent = t("wheel.h");
  const colon = document.createElement("span"); colon.className = "wheel-colon"; colon.textContent = ":";
  const mW = createWheel(row, "m", _wheelState[key].m, v => onSel(_wheelState[key].h, v));
  const unitM = document.createElement("span"); unitM.className = "wheel-unit"; unitM.textContent = t("wheel.m");
  row.appendChild(unitH); row.appendChild(colon); row.appendChild(unitM);
  void hW; void mW;
}

initWheel("same-wheel", "same");
initWheel("inter-wheel", "inter");

// 时间约束选择器：外壳形似输入框，点击弹出滚轮面板（与换乘设置同款滚轮）
function initTimePicker(id, key) {
  const el = $id(id);
  const valEl = el.querySelector(".tp-val");
  const pop = el.querySelector(".tp-pop");
  const row = pop.querySelector(".wheel-row");
  let h = 0, m = 0;
  const paint = () => {
    const empty = valEl.dataset.empty === "1";
    valEl.textContent = empty ? "--:--" : String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
    valEl.classList.toggle("tp-set", !empty);
    _form.times[key] = empty ? null : h * 60 + m;
  };
  const setPop = open => {
    pop.classList.toggle("open", open);
    el.classList.toggle("open", open);
    el.setAttribute("aria-expanded", String(open));
  };
  createWheel(row, "h", h, v => { h = v; valEl.dataset.empty = "0"; paint(); });
  const colon = document.createElement("span"); colon.className = "wheel-colon"; colon.textContent = ":";
  createWheel(row, "m", m, v => { m = v; valEl.dataset.empty = "0"; paint(); });
  row.appendChild(colon);
  pop.querySelector(".tp-clear").addEventListener("click", e => {
    e.stopPropagation();
    valEl.dataset.empty = "1";
    paint();
    setPop(false);
  });
  el.addEventListener("click", e => {
    e.stopPropagation();
    setPop(!pop.classList.contains("open"));
  });
}
["dep-after", "dep-before", "arr-after", "arr-before"].forEach((id, i) => {
  initTimePicker(id, ["depAfter", "depBefore", "arrAfter", "arrBefore"][i]);
});
document.addEventListener("click", () => {
  document.querySelectorAll(".time-pick.open").forEach(el => {
    el.classList.remove("open");
    const pop = el.querySelector(".tp-pop");
    if (pop) pop.classList.remove("open");
  });
});

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
  btn.textContent = mode === "exact" ? t("search.this") : t("search.all");
  btn.classList.toggle("on", mode === "exact");
  btn.title = mode === "exact" ? t("search.modeThis") : t("search.modeAll");
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
  // 时间约束（滚轮选择器；null = 不限）
  const times = [
    ["depAfter", "dep_after"], ["depBefore", "dep_before"],
    ["arrAfter", "arr_after"], ["arrBefore", "arr_before"],
  ];
  times.forEach(([k, param]) => {
    const v = _form.times[k];
    if (v !== null && v !== undefined) p.set(param, String(Math.floor(v / 60)).padStart(2, "0") + ":" + String(v % 60).padStart(2, "0"));
  });
  p.set("same_transfer", _form.sameMin);
  p.set("inter_transfer", _form.interMin);
  p.set("max_transfers", $id("max-num").value);
  const xf = $id("xfer-at").value.trim();
  if (xf) p.set("xfer_at", xf);
  $id("results").innerHTML = '<div class="empty search-loading"><span class="spinner" aria-hidden="true"></span>' + t("search.loading") + "</div>";
  let d;
  try {
    d = await fetch("/api/search?" + p.toString()).then(r => r.json());
  } catch (e) {
    $id("results").innerHTML = '<div class="empty">' + t("search.netErr") + "</div>";
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

// ── 渲染：meta + 直达/换乘视图 Tab + 排序筛选条 + 路线列表 ──
function render() {
  if (!_routesData) return;
  const d = _routesData;
  const direct = d.routes.filter(r => r.train_transfers === 0 && r.interstation_transfers === 0);
  const xfer = d.routes.filter(r => !(r.train_transfers === 0 && r.interstation_transfers === 0));
  if (!_sf.view || (_sf.view !== "direct" && _sf.view !== "xfer")) _sf.view = "direct";
  // 当前视图无结果而另一视图有结果 → 自动切到有结果的视图（避免空视图开局）
  if ((_sf.view === "direct" && direct.length === 0 && xfer.length > 0) ||
      (_sf.view === "xfer" && xfer.length === 0 && direct.length > 0)) {
    _sf.view = _sf.view === "direct" ? "xfer" : "direct";
  }

  let h = '<div class="meta">'
    + "<span>" + t("meta.plans", { n: d.routes.length, d: direct.length, x: xfer.length }) + "</span>"
    + "<span>" + t("meta.time", { t: d.time, n: d.scanned }) + "</span>"
    + (d.cached ? '<span class="mtag-hit">' + icon("bolt") + " " + t("meta.cached") + "</span>" : "")
    + (d.complete ? "" : '<span class="mtag">' + icon("alert") + " " + t("meta.incomplete") + "</span>")
    + "</div>";

  // 直达 / 换乘 视图切换（左右两个按钮，居中）
  h += '<div class="view-tabs">'
    + '<button type="button" class="vt-btn' + (_sf.view === "direct" ? " sel" : "") + '" data-view="direct">'
    + icon("direct") + t("view.direct") + ' <span class="vt-cnt">' + direct.length + "</span></button>"
    + '<button type="button" class="vt-btn' + (_sf.view === "xfer" ? " sel" : "") + '" data-view="xfer">'
    + icon("repeat") + t("view.xfer") + ' <span class="vt-cnt">' + xfer.length + "</span></button>"
    + "</div>";

  h += '<div class="sf-bar" id="sf-bar">'
    + "<label>" + t("sf.sort") + "</label><span id=\"sf-sort-c\"></span>"
    + "<label>" + t("sf.filter") + "</label><span id=\"sf-xfer-c\"></span>"
    + '<input id="sf-city" placeholder="' + t("sf.cityPh") + '">'
    + '<button class="sf-clear" id="sf-clear" type="button">' + t("sf.reset") + "</button>"
    + "</div>"
    + '<div id="route-list"></div>';

  $id("results").innerHTML = h;

  buildDropdown($id("sf-sort-c"), [
    { value: "score", label: t("sf.score") },
    { value: "time", label: t("sf.time") },
    { value: "dist", label: t("sf.dist") },
    { value: "dep", label: t("sf.dep") },
    { value: "arr", label: t("sf.arr") },
    { value: "xfer", label: t("sf.xfer") },
  ], _sf.sort, v => { _sf.sort = v; renderList(); });

  buildDropdown($id("sf-xfer-c"), [
    { value: "all", label: t("sf.all") },
    { value: "direct", label: t("sf.onlyDirect") },
    { value: "same", label: t("sf.onlySame") },
    { value: "inter", label: t("sf.onlyInter") },
  ], _sf.xfer, v => { _sf.xfer = v; renderList(); });

  $id("sf-city").value = _sf.city;
  $id("sf-city").addEventListener("input", () => { _sf.city = $id("sf-city").value.trim(); renderList(); });
  $id("sf-clear").addEventListener("click", () => { _sf.sort = "score"; _sf.xfer = "all"; _sf.city = ""; render(); });

  // 事件委托（绑 #results 而非 #route-list：vt-btn 在 route-list 之外）
  $id("results").addEventListener("click", e => {
    const vt = e.target.closest(".vt-btn");
    if (vt) {
      _sf.view = vt.dataset.view;
      document.querySelectorAll(".vt-btn").forEach(b => b.classList.toggle("sel", b === vt));
      renderList();
      return;
    }
    const more = e.target.closest(".tt-more");
    if (more) {
      const holder = more.closest(".tt-card");
      if (holder) {
        holder.classList.toggle("full");
        more.textContent = holder.classList.contains("full") ? t("tt.collapse") : t("tt.expand");
      }
      return;
    }
  });

  renderList();
}

// ── 排序 + 筛选 + 重渲染列表 ──
function renderList() {
  const list = $id("route-list");
  if (!list || !_routesData) return;
  let routes = [..._routesData.routes];
  const { sort, xfer, city, view } = _sf;

  // 视图 Tab：直达 / 换乘（完全展开，不分批）
  if (view === "direct") routes = routes.filter(r => r.train_transfers === 0 && r.interstation_transfers === 0);
  else if (view === "xfer") routes = routes.filter(r => !(r.train_transfers === 0 && r.interstation_transfers === 0));

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

  if (!routes.length) {
    list.innerHTML = '<div class="empty">' + icon("inbox") + " " + t("empty.noMatch") + "</div>";
    return;
  }

  const label = view === "direct" ? t("view.direct") : t("view.xfer");
  list.innerHTML = renderGroup(routes, label, view === "direct", 0, routes.length);
  // 完全展开：卡片默认展开，时刻表懒加载
  list.querySelectorAll(".rc").forEach(card => {
    card.classList.add("expanded");
    ensureTimetable(card);
  });
}

// ── 路线卡片分组（含入场动效 stagger）──
function renderGroup(routes, label, isDirect, startIdx, totalInGroup) {
  if (!routes.length) return "";
  const grpIcon = isDirect ? icon("direct") : icon("repeat");
  let g = '<div class="sec-hd">' + grpIcon + label + '<span class="cnt">' + t("rc.group", { n: totalInGroup }) + "</span></div>";
  routes.forEach((r, i) => {
    const numCls = r.score >= 0.6 ? "n-good" : r.score >= 0.35 ? "n-mid" : "n-bad";
    const trainSegs = r.segments.filter(s => s.type === "train");
    const firstDep = trainSegs.length ? td(trainSegs[0].depart) : td(r.first_departure);
    const lastArr = trainSegs.length ? td(trainSegs[trainSegs.length - 1].arrive) : td(r.final_arrival);
    let xferText = "";
    if (r.train_transfers > 0) xferText += '<span class="badge bC">' + t("rc.transferN", { n: r.train_transfers }) + "</span> ";
    if (r.interstation_transfers > 0) xferText += '<span class="badge bI">' + t("rc.groundN", { n: r.interstation_transfers }) + "</span> ";

    // 贴生活：乘坐时长 + 等待时长（换乘时展示，等待 = 总耗时 - 行驶 - 地面）
    const travelMin = trainSegs.reduce((a, s) => a + (s.travel_minutes || 0), 0);
    const groundMin = r.interstation_minutes || 0;
    const waitMin = r.total_minutes - travelMin - groundMin;
    let waitText = "";
    if (waitMin > 0) waitText = '<span class="rc-wait">' + icon("wait") + " " + t("rc.wait", { t: fm(waitMin) }) + "</span>";

    // 入场动效：前 12 张卡片错峰淡入（transform/opacity → GPU 合成）
    const delay = Math.min(i, 11) * 45;

    g += '<div class="rc" style="animation-delay:' + delay + 'ms">'
      + '<div class="rc-main"><div class="rc-num ' + numCls + '">' + (startIdx + i + 1) + "</div><div class=\"rc-body\">"
      + '<div class="rc-top"><span class="rc-route">' + esc(r.actual_origin) + " → " + esc(r.actual_destination)
      + '</span><span class="rc-time">' + esc(firstDep) + " → " + esc(lastArr) + "</span></div>"
      + '<div class="rc-flow">' + buildRouteFlow(r) + "</div>"
      + '<div class="rc-info"><span class="rc-stat">' + icon("clock") + " " + fm(r.total_minutes) + "</span>"
      + '<span class="rc-stat">' + icon("route") + " " + r.rail_distance + "km</span>"
      + '<span class="rc-stat">' + icon("train") + " " + t("rc.ride", { t: fm(travelMin) }) + "</span>" + waitText + xferText + "</div>"
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
        + '</span><span class="rc-node-time">' + esc(e.arr) + t("rc.arrive") + " · " + esc(next.dep) + t("rc.depart") + "</span></div>";
      i++;
    } else if (e.dep !== undefined) {
      h += '<div class="rc-node"><span class="rc-node-name">' + esc(e.station)
        + '</span><span class="rc-node-time">' + esc(e.dep) + t("rc.depart") + "</span></div>";
    } else if (e.arr !== undefined) {
      h += '<div class="rc-node"><span class="rc-node-name">' + esc(e.station)
        + '</span><span class="rc-node-time">' + esc(e.arr) + t("rc.arrive") + "</span></div>";
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
        + '<div class="tt-body"><div class="tt-load"><span class="spinner" aria-hidden="true"></span>' + t("tt.loading") + "</div></div>"
        + "</div>";
    } else {
      h += '<div class="tt-gnd">' + icon("walk") + " " + t("tt.ground") + " " + esc(s.from_station) + " → " + esc(s.to_station)
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
    hd.innerHTML = t("tt.od", { o: esc(stops[0].station), d: esc(stops[stops.length - 1].station) });
  }
  const body = card.querySelector(".tt-body");
  if (!stops.length) {
    body.innerHTML = '<div class="tt-load">' + t("tt.none") + "</div>";
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
  let h = '<table class="tt-tbl"><tr><th>' + t("tt.thStation") + "</th><th>" + t("tt.thArrive") + "</th><th>" + t("tt.thDepart") + "</th><th>" + t("tt.thStay") + "</th></tr>" + segRows.join("") + "</table>";
  if (stops.length > endIdx - startIdx + 1) {
    h += '<button class="tt-more" type="button">' + t("tt.expandN", { n: stops.length }) + "</button>"
      + '<div class="tt-full"><table class="tt-tbl"><tr><th>' + t("tt.thStation") + "</th><th>" + t("tt.thArrive") + "</th><th>" + t("tt.thDepart") + "</th><th>" + t("tt.thStay") + "</th></tr>"
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
   设置面板（i18n.js 提供 loadSettings/saveSettings/t/applyLang/applyTheme）
   ═══════════════════════════════════════════════════════ */
let _appVersion = "0.0.0";

async function initAppVersion() {
  try {
    const d = await fetch("/api/appinfo").then(r => r.json());
    if (d && d.version) _appVersion = d.version;
  } catch (e) {}
  syncSettingsUI();
}

function clampInt(v, lo, hi, dft) {
  const n = parseInt(v, 10);
  if (Number.isNaN(n)) return dft;
  return Math.max(lo, Math.min(hi, n));
}

function segVal(sel) {
  const selBtn = sel.querySelector(".seg-btn.sel");
  return selBtn ? selBtn.dataset.v : null;
}

function selSeg(sel, v) {
  sel.querySelectorAll(".seg-btn").forEach(b => b.classList.toggle("sel", b.dataset.v === v));
}

function openSettings() { $id("set-mask").hidden = false; syncSettingsUI(); }
function closeSettings() { $id("set-mask").hidden = true; }

function syncSettingsUI() {
  const s = loadSettings();
  selSeg($id("set-lang"), s.lang);
  selSeg($id("set-theme"), s.theme);
  selSeg($id("set-profile"), s.profile);
  selSeg($id("set-match"), s.matchMode);
  $id("set-same").value = s.sameTransfer;
  $id("set-inter").value = s.interTransfer;
  $id("set-max").value = s.maxTransfers;
  $id("set-autocheck").checked = !!s.autoCheckUpdate;
  $id("set-proxy").value = s.proxyPort || "";
  $id("set-ver").textContent = t("set.curVer", { v: _appVersion });
  $id("set-saved").hidden = true;
}

function collectSettings() {
  return {
    lang: segVal($id("set-lang")) || "zh",
    theme: segVal($id("set-theme")) || "light",
    profile: segVal($id("set-profile")) || "balanced",
    matchMode: segVal($id("set-match")) || "fuzzy",
    sameTransfer: clampInt($id("set-same").value, 0, 180, 15),
    interTransfer: clampInt($id("set-inter").value, 0, 600, 60),
    maxTransfers: clampInt($id("set-max").value, 0, 10, 3),
    autoCheckUpdate: $id("set-autocheck").checked,
    proxyPort: $id("set-proxy").value.trim(),
  };
}

function saveFromPanel() {
  saveSettings(collectSettings());
  applyLang();
  applyTheme();
  const s = loadSettings();
  // 表单默认值联动（不改动当前已调整的表单值，仅同步全局匹配模式按钮态）
  if (_form.fromMode) setEndMode("fromMode", _form.fromMode);
  if (_form.toMode) setEndMode("toMode", _form.toMode);
  if (s.maxTransfers !== undefined) { $id("max-num").value = s.maxTransfers; $id("max-rng").value = s.maxTransfers; }
  const sv = $id("set-saved");
  sv.hidden = false;
  clearTimeout(sv._t);
  sv._t = setTimeout(() => { sv.hidden = true; }, 1800);
}

// 标题栏 + 导航栏的语言快捷切换按钮
document.querySelectorAll("[data-lang-toggle]").forEach(btn => {
  btn.addEventListener("click", () => {
    const next = loadSettings().lang === "zh" ? "en" : "zh";
    saveSettings({ lang: next });
    applyLang();
    if (_form.fromMode) setEndMode("fromMode", _form.fromMode);
    if (_form.toMode) setEndMode("toMode", _form.toMode);
    initWheel("same-wheel", "same");
    initWheel("inter-wheel", "inter");
    if (_routesData) render();
  });
});

// 设置入口（标题栏齿轮 + 导航栏齿轮）
document.querySelectorAll("#tb-settings, #nv-settings").forEach(btn => {
  btn.addEventListener("click", openSettings);
});
$id("set-close").addEventListener("click", closeSettings);
$id("set-mask").addEventListener("click", e => { if (e.target === $id("set-mask")) closeSettings(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSettings(); });

// 语言 / 主题 / 搜索默认值控件：改动即保存生效
function bindSegSave(sel, key) {
  sel.addEventListener("click", e => {
    const b = e.target.closest(".seg-btn");
    if (!b) return;
    selSeg(sel, b.dataset.v);
    saveFromPanel();
  });
}
bindSegSave($id("set-lang"), "lang");
bindSegSave($id("set-theme"), "theme");
bindSegSave($id("set-profile"), "profile");
bindSegSave($id("set-match"), "matchMode");
["set-same", "set-inter", "set-max"].forEach(id => {
  $id(id).addEventListener("change", saveFromPanel);
});
$id("set-autocheck").addEventListener("change", saveFromPanel);
$id("set-proxy").addEventListener("change", saveFromPanel);

// 恢复默认
$id("set-reset").addEventListener("click", () => {
  saveSettings({ ...SETTINGS_DEFAULTS });
  applyLang();
  applyTheme();
  syncSettingsUI();
  saveFromPanel();
});

/* ═══════════════════════════════════════════════════════
   检查更新（桌面模式经 JS 桥；浏览器模式提示不可用）
   ═══════════════════════════════════════════════════════ */
const _updateUI = {
  msgEl: null, notesEl: null, barEl: null, txtEl: null, dlBtn: null,
};

function hasUpdateBridge() {
  return !!(window.pywebview && window.pywebview.api && window.pywebview.api.check_update)
    || !!(window.__TAURI__ && window.__TAURI__.core);
}

function setUpdateMsg(text, cls) {
  _updateUI.msgEl.textContent = text;
  _updateUI.msgEl.className = "set-upd-msg" + (cls ? " " + cls : "");
}

async function doCheckUpdate() {
  const s = loadSettings();
  const btn = $id("set-check");
  btn.disabled = true;
  setUpdateMsg("");
  _updateUI.notesEl.innerHTML = "";
  _updateUI.dlBtn.hidden = true;
  try {
    let r;
    if (window.pywebview && window.pywebview.api && window.pywebview.api.check_update) {
      r = await window.pywebview.api.check_update(s.proxyPort);
    } else if (window.__TAURI__ && window.__TAURI__.core) {
      r = await window.__TAURI__.core.invoke("check_update", { proxyPort: s.proxyPort });
    } else {
      setUpdateMsg(t("set.checkErr", { e: "browser mode" }), "err");
      return;
    }
    if (r.status === "ok" && r.latest) {
      setUpdateMsg(t("set.newVer", { v: r.latest }), "new");
      if (r.notes) _updateUI.notesEl.innerHTML = "<pre>" + esc(r.notes) + "</pre>";
      _updateUI.dlBtn.hidden = false;
    } else if (r.status === "ok") {
      setUpdateMsg(t("set.latest"), "ok");
    } else if (r.status === "no-release") {
      setUpdateMsg(t("set.noRelease"), "ok");
    } else {
      setUpdateMsg(t("set.checkErr", { e: r.message || "unknown" }), "err");
    }
  } catch (e) {
    setUpdateMsg(t("set.checkErr", { e: String(e && e.message || e) }), "err");
  } finally {
    btn.disabled = false;
  }
}

async function doDownload() {
  const s = loadSettings();
  const dl = () => {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.download_update) {
      return window.pywebview.api.download_update(s.proxyPort);
    }
    if (window.__TAURI__ && window.__TAURI__.core) {
      return window.__TAURI__.core.invoke("download_update", { proxyPort: s.proxyPort });
    }
    return Promise.reject(new Error("no bridge"));
  };
  _updateUI.dlBtn.hidden = true;
  _updateUI.barEl.hidden = false;
  const poll = async () => {
    for (let i = 0; i < 1200; i++) {           // 最长约 30 分钟
      await new Promise(r => setTimeout(r, 1500));
      let p = { state: "err", message: "lost" };
      try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.get_download_progress) {
          p = await window.pywebview.api.get_download_progress();
        } else if (window.__TAURI__ && window.__TAURI__.core) {
          p = await window.__TAURI__.core.invoke("get_download_progress");
        }
      } catch (e) { p = { state: "err", message: String(e) }; }
      if (p.state === "done") {
        _updateUI.barEl.hidden = true;
        setUpdateMsg(t("set.downloadDone"), "ok");
        return;
      }
      if (p.state === "err") {
        _updateUI.barEl.hidden = true;
        setUpdateMsg(t("set.dlErr", { e: p.message || "unknown" }), "err");
        return;
      }
      const pct = Math.round((p.downloaded || 0) * 100 / Math.max(1, p.total || 1));
      _updateUI.barEl.querySelector("#set-progress-bar").style.width = pct + "%";
      _updateUI.txtEl.textContent = t("set.downloading", { p: pct });
    }
  };
  try {
    const r = await dl();
    if (r && r.error) { _updateUI.barEl.hidden = true; setUpdateMsg(t("set.dlErr", { e: r.error }), "err"); return; }
    poll();
  } catch (e) {
    _updateUI.barEl.hidden = true;
    setUpdateMsg(t("set.dlErr", { e: String(e && e.message || e) }), "err");
  }
}

function initUpdateUI() {
  _updateUI.msgEl = $id("set-upd-msg");
  _updateUI.notesEl = $id("set-upd-notes");
  _updateUI.barEl = $id("set-progress");
  _updateUI.txtEl = $id("set-progress-txt");
  _updateUI.barEl.querySelector("#set-progress-bar"); // 校验存在
  const dl = document.createElement("button");
  dl.type = "button";
  dl.className = "bt bt-sm";
  dl.textContent = t("set.download");
  dl.hidden = true;
  dl.addEventListener("click", doDownload);
  _updateUI.dlBtn = dl;
  $id("set-upd-msg").after(dl);
  $id("set-check").addEventListener("click", doCheckUpdate);
  if (!hasUpdateBridge()) {
    $id("set-check").disabled = true;
    $id("set-check").title = "Web browser mode: update check requires the desktop app";
  }
  // 启动时自动检查（桌面模式 + 设置开启）
  const s = loadSettings();
  if (s.autoCheckUpdate && hasUpdateBridge()) {
    setTimeout(() => { try { doCheckUpdate(); } catch (e) {} }, 2500);
  }
}

// 语言切换时重建动态文案（滚轮单位/端点按钮/结果区）
registerLang(() => {
  initWheel("same-wheel", "same");
  initWheel("inter-wheel", "inter");
  if (_form.fromMode) setEndMode("fromMode", _form.fromMode);
  if (_form.toMode) setEndMode("toMode", _form.toMode);
  if (_routesData) render();
  syncSettingsUI();
});

// 最大换乘默认值来自设置
$id("max-num").value = loadSettings().maxTransfers;
$id("max-rng").value = loadSettings().maxTransfers;

initAppVersion();
initUpdateUI();

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
  // 深色主题：降低明度（暗色底上柔和发光）
  const PALETTES = {
    light: [
      [129, 140, 248], [192, 132, 252], [244, 114, 182],
      [251, 146, 60], [52, 211, 153], [45, 212, 191], [251, 191, 36],
    ],
    dark: [
      [99, 102, 241], [168, 85, 247], [236, 72, 153],
      [217, 119, 6], [16, 185, 129], [20, 184, 166], [217, 164, 6],
    ],
  };
  let PALETTE = PALETTES[document.documentElement.dataset.theme] || PALETTES.light;
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
  // 主题切换：换色板并重绘（静态一帧或继续动画循环）
  registerTheme(theme => {
    PALETTE = PALETTES[theme] || PALETTES.light;
    spawn();
    if (reduced) draw(0);
  });
  addEventListener("resize", () => { resize(); if (reduced) draw(0); });
  if (reduced) { draw(0); return; }                // 减少动效偏好：只渲染静态一帧
  const loop = (ts) => { draw(ts); requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
})();
