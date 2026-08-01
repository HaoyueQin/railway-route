/* ═══════════════════════════════════════════════════════
   railway-route — i18n + 设置（语言/主题/默认值，localStorage 持久化）
   在 app.js 之前加载。提供：
     t(key, vars)        取当前语言文案（{n} 模板替换）
     getSettings()       当前设置（默认值合并）
     saveSettings(patch) 更新并持久化
     applyLang()         应用语言（data-i18n 静态文本 + 动态回调）
     applyTheme()        应用主题（light/dark/system → data-theme）
   ═══════════════════════════════════════════════════════ */
"use strict";

const I18N = {
  zh: {
    "app.title": "铁路出行路径规划",
    "app.sub": "全国铁路出行方案 · 14,173 车次 · 3,305 车站",
    "hero.desc": "输入出发与目的站，一站式对比直达与换乘方案 · 支持同城多站与异站换乘 · 覆盖全国铁路时刻表",

    "search.from": "出发站 / 城市",
    "search.to": "目的站 / 城市",
    "search.fromPh": "如：北京 / 北京南",
    "search.toPh": "如：上海 / 上海虹桥",
    "search.all": "全部站",
    "search.this": "本站",
    "search.modeAll": "当前：该端全部站（点击仅本站）",
    "search.modeThis": "当前：仅本站（点击切回该端全部站）",
    "search.swap": "交换起终点",
    "search.go": "搜索出行方案",
    "search.loading": "搜索中…",
    "search.netErr": "网络错误，请确认服务已启动",

    "adv.toggle": "高级选项",
    "adv.time": "时间约束",
    "adv.timeHint": "（留空 = 全天）",
    "adv.depAfter": "出发不早于",
    "adv.depBefore": "出发不晚于",
    "adv.arrAfter": "到达不早于",
    "adv.arrBefore": "到达不晚于",
    "adv.clear": "清除",
    "adv.xfer": "换乘设置",
    "adv.same": "同站换乘",
    "adv.inter": "异站换乘",
    "adv.maxXfer": "最大换乘",
    "adv.xferAt": "指定换乘城市（留空 = 不限）",
    "adv.xferAtPh": "如：武汉 / 郑州",
    "adv.profile": "搜索强度",
    "adv.pFast": "快速",
    "adv.pBalanced": "均衡",
    "adv.pThorough": "全面",
    "adv.pComplete": "完整",
    "adv.profileHint": "档位越高搜索越完整，耗时越长",

    "wheel.h": "时",
    "wheel.m": "分",

    "meta.plans": "{n} 个方案（直达 {d} · 换乘 {x}）",
    "meta.time": "{t}s · 扫描 {n} 条",
    "meta.cached": "缓存命中",
    "meta.incomplete": "搜索未完整",
    "view.direct": "直达方案",
    "view.xfer": "换乘方案",

    "sf.sort": "排序",
    "sf.filter": "筛选",
    "sf.cityPh": "换乘城市/站名",
    "sf.reset": "重置",
    "sf.score": "综合评分",
    "sf.time": "总耗时",
    "sf.dist": "总里程",
    "sf.dep": "出发时间",
    "sf.arr": "到达时间",
    "sf.xfer": "换乘次数",
    "sf.all": "全部",
    "sf.onlyDirect": "仅直达",
    "sf.onlySame": "仅同站换乘",
    "sf.onlyInter": "含异站换乘",

    "rc.transferN": "换乘 {n} 次",
    "rc.groundN": "地面 {n} 次",
    "rc.wait": "等 {t}",
    "rc.ride": "乘 {t}",
    "rc.arrive": "到",
    "rc.depart": "发",
    "rc.group": "{n} 条",

    "tt.loading": "加载时刻表…",
    "tt.none": "暂无时刻表数据",
    "tt.od": "始发 <b>{o}</b> · 终到 <b>{d}</b>",
    "tt.collapse": "▴ 收起完整时刻表",
    "tt.expand": "▾ 查看完整时刻表",
    "tt.expandN": "▾ 查看完整 {n} 站时刻表",
    "tt.ground": "地面换乘",
    "tt.thStation": "站名",
    "tt.thArrive": "到达",
    "tt.thDepart": "发车",
    "tt.thStay": "停时",

    "empty.noMatch": "当前视图下无匹配方案",

    "tb.min": "最小化",
    "tb.max": "最大化",
    "tb.restore": "还原",
    "tb.close": "关闭",
    "tb.settings": "设置",
    "tb.lang": "切换语言（中文 / English）",

    "set.title": "设置",
    "set.close": "关闭",
    "set.lang": "界面语言",
    "set.zh": "中文",
    "set.en": "English",
    "set.theme": "外观主题",
    "set.themeLight": "浅色",
    "set.themeDark": "深色",
    "set.themeSystem": "跟随系统",
    "set.search": "搜索默认值",
    "set.profile": "搜索强度",
    "set.sameXfer": "同站换乘（分钟）",
    "set.interXfer": "异站换乘（分钟）",
    "set.maxXfer": "最大换乘",
    "set.matchMode": "匹配模式",
    "set.matchFuzzy": "同城扩展（模糊）",
    "set.matchExact": "仅本站（精确）",
    "set.update": "检查更新",
    "set.autoCheck": "启动时自动检查更新",
    "set.proxy": "更新代理（空 = 系统代理）",
    "set.proxyPh": "如 127.0.0.1:8897",
    "set.checkNow": "检查更新",
    "set.curVer": "当前版本：{v}",
    "set.latest": "已是最新版本",
    "set.newVer": "发现新版本 {v}",
    "set.download": "下载并安装",
    "set.downloading": "下载中 {p}%",
    "set.downloadDone": "下载完成，正在启动安装程序…",
    "set.checkErr": "检查更新失败：{e}",
    "set.dlErr": "下载失败：{e}",
    "set.noRelease": "暂无可用版本",
    "set.reset": "恢复默认设置",
    "set.saved": "设置已保存",
  },
  en: {
    "app.title": "Railway Route Planner",
    "app.sub": "National railway trip planning · 14,173 trains · 3,305 stations",
    "hero.desc": "Enter origin and destination to compare direct and transfer options · Same-city multi-station & inter-city transfer support · Full national timetable coverage",

    "search.from": "From Station / City",
    "search.to": "To Station / City",
    "search.fromPh": "e.g. Beijing / Beijing South",
    "search.toPh": "e.g. Shanghai / Shanghai Hongqiao",
    "search.all": "All Stations",
    "search.this": "This Station",
    "search.modeAll": "Currently: all stations in city (click for this station only)",
    "search.modeThis": "Currently: this station only (click for all stations)",
    "search.swap": "Swap origin & destination",
    "search.go": "Search Routes",
    "search.loading": "Searching…",
    "search.netErr": "Network error. Is the service running?",

    "adv.toggle": "Advanced Options",
    "adv.time": "Time Constraints",
    "adv.timeHint": "(empty = all day)",
    "adv.depAfter": "Depart no earlier than",
    "adv.depBefore": "Depart no later than",
    "adv.arrAfter": "Arrive no earlier than",
    "adv.arrBefore": "Arrive no later than",
    "adv.clear": "Clear",
    "adv.xfer": "Transfer Settings",
    "adv.same": "Same-station transfer",
    "adv.inter": "Inter-station transfer",
    "adv.maxXfer": "Max transfers",
    "adv.xferAt": "Transfer city (empty = any)",
    "adv.xferAtPh": "e.g. Wuhan / Zhengzhou",
    "adv.profile": "Search Intensity",
    "adv.pFast": "Fast",
    "adv.pBalanced": "Balanced",
    "adv.pThorough": "Thorough",
    "adv.pComplete": "Complete",
    "adv.profileHint": "Higher intensity gives more complete results, takes longer",

    "wheel.h": "h",
    "wheel.m": "min",

    "meta.plans": "{n} plans (direct {d} · transfer {x})",
    "meta.time": "{t}s · scanned {n}",
    "meta.cached": "cached",
    "meta.incomplete": "search incomplete",
    "view.direct": "Direct Routes",
    "view.xfer": "Transfer Routes",

    "sf.sort": "Sort",
    "sf.filter": "Filter",
    "sf.cityPh": "Transfer city/station",
    "sf.reset": "Reset",
    "sf.score": "Score",
    "sf.time": "Duration",
    "sf.dist": "Distance",
    "sf.dep": "Departure",
    "sf.arr": "Arrival",
    "sf.xfer": "Transfers",
    "sf.all": "All",
    "sf.onlyDirect": "Direct only",
    "sf.onlySame": "Same-station only",
    "sf.onlyInter": "With inter-station",

    "rc.transferN": "{n} transfers",
    "rc.groundN": "ground {n}",
    "rc.wait": "wait {t}",
    "rc.ride": "ride {t}",
    "rc.arrive": "arr",
    "rc.depart": "dep",
    "rc.group": "{n} routes",

    "tt.loading": "Loading timetable…",
    "tt.none": "No timetable data",
    "tt.od": "Origin <b>{o}</b> · Terminal <b>{d}</b>",
    "tt.collapse": "▴ Collapse full timetable",
    "tt.expand": "▾ View full timetable",
    "tt.expandN": "▾ View full {n}-stop timetable",
    "tt.ground": "ground transfer",
    "tt.thStation": "Station",
    "tt.thArrive": "Arrive",
    "tt.thDepart": "Depart",
    "tt.thStay": "Stop",

    "empty.noMatch": "No matching routes in this view",

    "tb.min": "Minimize",
    "tb.max": "Maximize",
    "tb.restore": "Restore",
    "tb.close": "Close",
    "tb.settings": "Settings",
    "tb.lang": "Switch language (中文 / English)",

    "set.title": "Settings",
    "set.close": "Close",
    "set.lang": "Language",
    "set.zh": "中文",
    "set.en": "English",
    "set.theme": "Theme",
    "set.themeLight": "Light",
    "set.themeDark": "Dark",
    "set.themeSystem": "System",
    "set.search": "Search Defaults",
    "set.profile": "Search intensity",
    "set.sameXfer": "Same-station transfer (min)",
    "set.interXfer": "Inter-station transfer (min)",
    "set.maxXfer": "Max transfers",
    "set.matchMode": "Match mode",
    "set.matchFuzzy": "Same-city expansion (fuzzy)",
    "set.matchExact": "This station only (exact)",
    "set.update": "Check for Updates",
    "set.autoCheck": "Auto-check on startup",
    "set.proxy": "Update proxy (empty = system)",
    "set.proxyPh": "e.g. 127.0.0.1:8897",
    "set.checkNow": "Check for Updates",
    "set.curVer": "Current version: {v}",
    "set.latest": "You are up to date",
    "set.newVer": "New version {v} available",
    "set.download": "Download & Install",
    "set.downloading": "Downloading {p}%",
    "set.downloadDone": "Download complete, launching installer…",
    "set.checkErr": "Update check failed: {e}",
    "set.dlErr": "Download failed: {e}",
    "set.noRelease": "No release available",
    "set.reset": "Reset to defaults",
    "set.saved": "Settings saved",
  },
};

// ── 设置（localStorage 持久化）──
const SETTINGS_KEY = "rr_settings";
const SETTINGS_DEFAULTS = {
  lang: "zh",            // zh / en
  theme: "light",        // light / dark / system
  profile: "balanced",   // fast / balanced / thorough / complete
  sameTransfer: 15,      // 同站换乘分钟
  interTransfer: 60,     // 异站换乘分钟
  maxTransfers: 3,       // 最大换乘
  matchMode: "fuzzy",    // fuzzy / exact（全局默认匹配模式）
  autoCheckUpdate: true, // 启动时自动检查更新
  proxyPort: "",         // 更新代理 127.0.0.1:8897；空 = 系统代理
};

let _settings = null;

function loadSettings() {
  if (_settings) return _settings;
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}"); } catch (e) {}
  _settings = { ...SETTINGS_DEFAULTS, ...stored };
  return _settings;
}

function saveSettings(patch) {
  _settings = { ...loadSettings(), ...patch };
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(_settings)); } catch (e) {}
}

// ── 取文案：t("meta.plans", {n:3,d:1,x:2}) ──
function t(key, vars) {
  let s = (I18N[loadSettings().lang] || I18N.zh)[key];
  if (s === undefined) s = I18N.zh[key];
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      s = s.split("{" + k + "}").join(v);
    }
  }
  return s;
}

// ── 应用语言：静态 data-i18n / data-i18n-ph / data-i18n-title + 动态回调 ──
let _onLang = [];

function registerLang(cb) { _onLang.push(cb); }

function applyLang() {
  const lang = loadSettings().lang;
  document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
  document.title = t("app.title");
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPh);
  });
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.title = t(el.dataset.i18nTitle);
    if (el.hasAttribute("aria-label")) el.setAttribute("aria-label", t(el.dataset.i18nTitle));
  });
  _onLang.forEach(cb => { try { cb(lang); } catch (e) {} });
  // 语言快捷按钮显示目标语言
  document.querySelectorAll("[data-lang-toggle]").forEach(btn => {
    btn.textContent = lang === "zh" ? "EN" : "中";
  });
}

// ── 应用主题：light / dark / system → data-theme + canvas 重着色回调 ──
let _onTheme = [];

function registerTheme(cb) { _onTheme.push(cb); }

function resolveTheme() {
  const t = loadSettings().theme;
  if (t !== "system") return t;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme() {
  const theme = resolveTheme();
  document.documentElement.dataset.theme = theme;
  _onTheme.forEach(cb => { try { cb(theme); } catch (e) {} });
  document.querySelectorAll("[data-theme-opt]").forEach(el => {
    el.classList.toggle("sel", el.dataset.themeOpt === loadSettings().theme);
  });
}

// ── 初始化（页面加载即应用已存偏好）──
applyLang();
applyTheme();
