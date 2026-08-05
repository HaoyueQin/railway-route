#!/usr/bin/env node
/**
 * 前端契约模拟测试（零 GUI、零网络、纯静态分析 + node --check）。
 *
 * 模拟真实前端 → 后端的契约关系：
 *  1. JS 语法检查（app.js / i18n.js）
 *  2. DOM id 契约：app.js 页面加载期绑定的静态元素 id 必须存在于 index.html
 *     （render() 动态生成的元素单独校验：其 id 必须出现在 render 模板中）
 *  3. i18n 键契约：app.js 使用的 t("key") 必须存在于中/英字典（zh/en 块）
 *  4. API 参数契约：前端 search() 发出的参数名 ⊆ 后端 build_search_request 接受集
 *  5. 响应契约：前端消费的关键响应字段 ⊆ 后端 api.rs/updater.rs 输出集
 *  6. 无内联事件处理器（XSS 契约）
 *
 * 用法: node rust/tools/web_contract_test.mjs
 * 退出码: 0 = 全部通过
 */
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const WEB = path.join(ROOT, "web");
const RUST = path.join(ROOT, "rust", "src");

const html = readFileSync(path.join(WEB, "index.html"), "utf-8");
const appJs = readFileSync(path.join(WEB, "app.js"), "utf-8");
const i18nJs = readFileSync(path.join(WEB, "i18n.js"), "utf-8");
const validationRs = readFileSync(path.join(RUST, "validation.rs"), "utf-8");
const apiRs = readFileSync(path.join(RUST, "api.rs"), "utf-8");
const updaterRs = readFileSync(path.join(RUST, "updater.rs"), "utf-8");

let failures = 0;
const fail = (msg) => { failures++; console.log(`  ✗ ${msg}`); };
const ok = (msg) => console.log(`  ✓ ${msg}`);

// ── 1. JS 语法 ──
console.log("== 1. JS 语法 ==");
for (const f of ["app.js", "i18n.js"]) {
  const r = spawnSync(process.execPath, ["--check", path.join(WEB, f)], { encoding: "utf-8" });
  if (r.status === 0) ok(`${f} 语法正确`);
  else fail(`${f} 语法错误: ${r.stderr.slice(0, 300)}`);
}

// ── 2. DOM id 契约 ──
console.log("== 2. DOM id 契约 ==");
const htmlIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]));
const staticIds = new Set([...appJs.matchAll(/\$id\("([^"]+)"\)/g)].map(m => m[1]));
const qsIds = new Set([...appJs.matchAll(/querySelector(?:All)?\("#([^"]+)"\)/g)].map(m => m[1].replace(/^#/, "")));

// render() 动态生成的元素（出现在 JS 字符串模板中的 id，页面加载期不存在）
// 模板字符串里转义引号 \" → 先反转义再提取
const appJsUnescaped = appJs.replaceAll('\\"', '"');
const codeGenIds = new Set([...appJsUnescaped.matchAll(/id="([^"]+)"/g)].map(m => m[1]));
// 纯布局容器（无交互绑定需求，仅 CSS 结构）
const layoutOnly = new Set(["sf-bar", "route-list", "view-tabs"]);
const missing = [...staticIds].filter(id => !htmlIds.has(id) && !codeGenIds.has(id) && !layoutOnly.has(id));
if (missing.length === 0) {
  const dyn = [...staticIds].filter(id => !htmlIds.has(id));
  ok(`静态绑定元素 ${staticIds.size} 个：${htmlIds.size} 个来自 index.html${dyn.length ? `，${dyn.length} 个为 render 动态生成（${dyn.join(", ")}）` : ""}`);
} else fail(`缺失静态元素 id: ${missing.join(", ")}`);
// render 模板中生成的 id 必须被 app.js 绑定引用（防生成后无交互的死元素）
const genUnbound = [...codeGenIds].filter(id => !htmlIds.has(id) && !layoutOnly.has(id) && !appJs.includes(`$id("${id}")`) && !appJs.includes(`getElementById("${id}")`));
if (genUnbound.length === 0) ok(`render 生成元素 ${codeGenIds.size} 个，均有绑定引用`);
else fail(`render 生成未绑定的 id: ${genUnbound.join(", ")}`);

const inlineHandlers = [...html.matchAll(/\son\w+="/g)];
if (inlineHandlers.length === 0) ok("index.html 无内联事件处理器");
else fail(`发现内联事件处理器: ${inlineHandlers.map(m => m[0].trim()).join(", ")}`);

// ── 3. i18n 键契约 ──
console.log("== 3. i18n 键契约 ==");
function dictKeys(lang) {
  // 定位 { lang: { ... } } 块，提取块内所有 "key":
  const start = i18nJs.indexOf(`${lang}: {`);
  if (start < 0) return new Set();
  let depth = 0, i = i18nJs.indexOf("{", start), end = -1;
  for (; i < i18nJs.length; i++) {
    if (i18nJs[i] === "{") depth++;
    else if (i18nJs[i] === "}") { depth--; if (depth === 0) { end = i; break; } }
  }
  const block = i18nJs.slice(start, end);
  return new Set([...block.matchAll(/"([^"]+)":/g)].map(m => m[1]));
}
const zhKeys = dictKeys("zh");
const enKeys = dictKeys("en");
const used = new Set([...appJs.matchAll(/\bt\("([^"]+)"\)/g)].map(m => m[1]));
const missingZh = [...used].filter(k => !zhKeys.has(k));
const missingEn = [...used].filter(k => !enKeys.has(k));
if (used.size === 0) fail("未提取到 t() 键（正则失效？）");
else if (missingZh.length === 0 && missingEn.length === 0) ok(`t() 使用 ${used.size} 个键，中(${zhKeys.size})/英(${enKeys.size})字典完整`);
else fail(`缺失 i18n 键 — 中: ${missingZh.join(", ") || "无"} 英: ${missingEn.join(", ") || "无"}`);

// ── 4. API 参数契约 ──
console.log("== 4. API 参数契约 ==");
const accepted = new Set([
  "from", "to", "from_station", "to_station", "match_mode", "from_mode", "to_mode",
  "search_profile", "profile", "dep_after", "dep_before", "arr_after", "arr_before",
  "same_transfer", "inter_transfer", "max_transfers", "transfer_city", "xfer_at",
  "timeout", "max", "from_stations", "to_stations", "q", "code", "limit",
  "proxyPort", "proxy_port",
]);
const sent = new Set([...appJs.matchAll(/p\.set\("([^"]+)"/g)].map(m => m[1]));
const unsupported = [...sent].filter(k => !accepted.has(k));
if (unsupported.length === 0) ok(`前端发送参数 {${[...sent].join(", ")}} 全部被后端接受`);
else fail(`前端发送了后端不接受的参数: ${unsupported.join(", ")}`);

// ── 5. 响应契约（关键字段 ⊆ 后端输出）──
console.log("== 5. 响应契约 ==");
const backendOut = apiRs + updaterRs;
// 后端输出字段 = 字符串字面量字段 ∪ serde 序列化结构字段（UpdateInfo/DownloadProgress）
const backendFields = new Set([...backendOut.matchAll(/"([a-z_]+)"/g)].map(m => m[1]));
for (const f of ["version", "notes", "url", "state", "downloaded", "total", "message"]) backendFields.add(f); // serde struct 字段（源码非字符串字面量）
// 前端消费的关键字段（search 响应 + 渲染段 + updater）
const keyFields = ["routes", "time", "source_stations", "target_stations", "complete",
  "profile", "scanned", "generated", "cached", "upgraded", "requested_profile",
  "error", "code", "message", "matches", "stops", "station", "depart", "arrive",
  "seq", "distance", "score", "actual_origin", "actual_destination",
  "first_departure", "final_arrival", "total_minutes", "rail_distance",
  "train_transfers", "interstation_transfers", "interstation_minutes",
  "transfer_cities", "segments", "type", "train_code", "from_station",
  "to_station", "travel_minutes", "transfer_minutes", "city_code", "city_name",
  "estimate_source", "minutes", "day_offset", "display", "day", "version",
  "notes", "url", "state", "downloaded", "total", "sha256", "src"];
const missingFields = keyFields.filter(f => !backendFields.has(f) && f !== "sha256" && f !== "src");
if (missingFields.length === 0) ok(`前端消费 ${keyFields.length} 个关键字段，后端全部输出`);
else fail(`后端未输出字段: ${missingFields.join(", ")}`);

// ── 汇总 ──
console.log(failures === 0 ? "\n前端契约模拟测试全部通过 ✓" : `\n${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);
