"""
车站与城市匹配模块。

支持精确站名、电报码、完整拼音、常见后缀和包含匹配；同时提供：
- exact：解析为单个车站；
- fuzzy：解析输入所属城市，并返回该城市全部有效铁路站；
- 城市解析：供 transfer_at 等城市级约束使用。
"""

import re
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class MatcherData:
    all_stations: list[str]
    city_to_stations: dict[str, list[str]]
    telecode_to_name: dict[str, str]
    pinyin_to_names: dict[str, list[str]]
    station_to_city_code: dict[str, str]
    city_name_to_code: dict[str, str]
    city_code_to_name: dict[str, str]
    # 预计算的站名规范化（避免每次查询重复 strip 全部站点）
    station_clean: dict[str, str] = None       # 站名 → 全后缀剥离
    station_no_suffix: dict[str, str] = None   # 站名 → 单后缀剥离


def build_matcher(graph, station_js_path: str) -> MatcherData:
    """从 station_name.js 构建结构化匹配索引。"""
    all_stations = list(graph.station_to_idx.keys())
    city_to_stations: dict[str, list[str]] = defaultdict(list)
    telecode_to_name: dict[str, str] = {}
    pinyin_to_names: dict[str, list[str]] = defaultdict(list)
    station_to_city_code: dict[str, str] = {}
    city_name_to_code: dict[str, str] = {}
    city_code_to_name: dict[str, str] = {}

    with open(station_js_path, "r", encoding="utf-8") as f:
        text = f.read()
    match = re.search(r"station_names\s*=\s*'(.*)'\s*$", text, re.DOTALL)
    content = match.group(1) if match else text.split("'", 1)[1].rsplit("'", 1)[0]

    for entry in content.split("@"):
        if not entry.strip():
            continue
        parts = entry.strip().split("|")
        if len(parts) < 8:
            continue
        name = parts[1]
        telecode = parts[2]
        pinyin = parts[3]
        city_code = parts[6]
        city_name = parts[7]

        if city_name:
            city_name_to_code[_strip_all_suffixes(city_name.lower())] = city_code
            city_code_to_name[city_code] = city_name

        if name not in graph.station_to_idx:
            continue
        city_to_stations[city_code].append(name)
        station_to_city_code[name] = city_code
        if telecode:
            telecode_to_name[telecode.upper()] = name
        if pinyin:
            pinyin_to_names[pinyin.lower()].append(name)

    station_clean = {
        name: _strip_all_suffixes(name.lower()) for name in all_stations
    }
    station_no_suffix = {
        name: _strip_suffix(name.lower()) for name in all_stations
    }

    return MatcherData(
        all_stations=all_stations,
        city_to_stations=dict(city_to_stations),
        telecode_to_name=telecode_to_name,
        pinyin_to_names=dict(pinyin_to_names),
        station_to_city_code=station_to_city_code,
        city_name_to_code=city_name_to_code,
        city_code_to_name=city_code_to_name,
        station_clean=station_clean,
        station_no_suffix=station_no_suffix,
    )


_SUFFIXES = ["市", "区", "县", "省", "地区", "站"]


def _strip_suffix(text: str) -> str:
    for suffix in _SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[:-len(suffix)]
    return text


def _strip_all_suffixes(text: str) -> str:
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[:-len(suffix)]
                changed = True
                break
    return text


def fuzzy_match(query: str, graph, matcher: MatcherData) -> list[tuple[int, str]]:
    """返回按分数降序排列的 ``(score, station_name)``。"""
    raw = query.strip()
    q_upper = raw.upper()
    q_lower = raw.lower()
    q_clean = _strip_all_suffixes(q_lower)
    results: list[tuple[int, str]] = []
    seen: set[str] = set()

    def add(score: int, name: str):
        if name not in seen and name in graph.station_to_idx:
            seen.add(name)
            results.append((score, name))

    if raw in graph.station_to_idx:
        add(200, raw)

    if q_upper in matcher.telecode_to_name:
        add(190, matcher.telecode_to_name[q_upper])

    if q_lower in matcher.pinyin_to_names:
        for name in matcher.pinyin_to_names[q_lower]:
            add(180, name)

    city_code = matcher.city_name_to_code.get(q_clean)
    if city_code:
        for name in matcher.city_to_stations.get(city_code, []):
            add(170, name)

    for station in matcher.all_stations:
        station_clean = matcher.station_clean[station]
        station_no_suffix = matcher.station_no_suffix[station]
        if q_clean == station_no_suffix or q_clean == station_clean:
            add(160, station)

    for station in matcher.all_stations:
        station_clean = matcher.station_clean[station]
        if len(station_clean) >= 2 and q_clean.endswith(station_clean):
            add(140 + len(station_clean) * 2, station)
        elif len(q_clean) >= 2 and station_clean.endswith(q_clean):
            add(135 + len(q_clean) * 2, station)

    for station in matcher.all_stations:
        station_clean = matcher.station_clean[station]
        if len(q_clean) >= 2 and q_clean in station_clean:
            add(120 + len(q_clean), station)
        elif len(station_clean) >= 2 and station_clean in q_clean:
            add(110 + len(station_clean), station)

    # 城市前缀扩散：q 以某城市名开头（北京西/上海虹桥/广州南）→ 该城市全部站。
    # 贴近生活：输入"北京西"应能看到北京市所有站（同城换乘提示），而非仅有精确站与城市名。
    for city_name, code in matcher.city_name_to_code.items():
        if len(city_name) >= 2 and q_clean.startswith(city_name):
            for name in matcher.city_to_stations.get(code, []):
                add(160, name)
            break

    # 同城兜底扩散：已匹配到的车站满足以下任一条件 → 归并其所属城市全部站：
    #   a) 班次稀疏（<25 班）的区级/县级地名（怀柔/广阳）→ 归市；
    #   b) 站名去方位后缀（东/西/南/北）后的地名与同城组内其他站同名
    #      （霸州西→"霸州"→组内有霸州/霸州北）→ 视为同城地名；
    # 班次充足的独立站（燕郊 43 班/新县 35 班）与县级单站保持独立，
    # 不扩散到市级（贴近生活：输入"新县"只应看到新县，而非信阳市全部站）
    if results:
        city_codes = set()
        for _, name in results[:8]:
            idx = graph.station_to_idx.get(name)
            if idx is None:
                continue
            cc = matcher.station_to_city_code.get(name)
            if not cc:
                continue
            n_dep = len(graph.departures.get(idx, ()))
            if n_dep >= MIN_STATION_TRAINS_FOR_SINGLE:
                # 班次充足：仅当"地名同名站"存在才扩散（霸州西→霸州）
                base = name[:-1] if name[-1:] in ("东", "西", "南", "北") else name
                same_group = matcher.city_to_stations.get(cc, [])
                if not any(s == base and s != name for s in same_group):
                    continue
            city_codes.add(cc)
        for cc in city_codes:
            for name in matcher.city_to_stations.get(cc, []):
                add(105, name)

    results.sort(key=lambda item: -item[0])
    return results


def resolve_single(query: str, graph, matcher: MatcherData) -> str:
    """解析为单个首选车站，保持现有单站搜索兼容行为。"""
    matches = fuzzy_match(query, graph, matcher)
    if not matches:
        raise ValueError(f"未找到匹配的车站: {query}")

    query_clean = _strip_all_suffixes(query.strip().lower())
    for _, name in matches:
        if _strip_all_suffixes(name.lower()) == query_clean:
            return name
    return matches[0][1]


def resolve_city_code(query: str, graph, matcher: MatcherData) -> str:
    """将城市名或任一有效车站输入解析为城市代码。"""
    query_clean = _strip_all_suffixes(query.strip().lower())
    city_code = matcher.city_name_to_code.get(query_clean)
    if city_code:
        return city_code

    station = resolve_single(query, graph, matcher)
    city_code = matcher.station_to_city_code.get(station)
    if not city_code:
        raise ValueError(f"未找到车站所属城市: {query}")
    return city_code


# 站名"可用性"阈值：出发车次 >= 该值时视为班次充足的独立车站（如燕郊 43 班、新县 35 班），
# 模糊输入按单站处理；班次稀疏的站（怀柔 10 班、广阳 17 班）视为区级地名，扩散到所属市
MIN_STATION_TRAINS_FOR_SINGLE = 25


def resolve_station_set(query: str, mode: str, graph, matcher: MatcherData) -> list[str]:
    """按 exact/fuzzy 模式解析单站或同城全部有效站。

    fuzzy 的贴近生活规则（2026-08 修正，兼顾区/县/镇的行政级别语义）：
    1. 输入是**城市名**（信阳/北京/廊坊）→ 该市全部站；
    2. 带"区"后缀（怀柔区/广阳区）→ 区级地名 → 归并到所属市全部站；
    3. 带"县/镇/乡"后缀 → 县级地名 → 单站（有站时）；
    4. 无后缀但图中存在该站名：
       - 站名以所属城市名开头（北京南/信阳东）→ 城市前缀扩散 → 所属市全部站；
       - 站名去方位后缀后的地名与组内其他站同名（曲阜东→曲阜）→ 同城扩散；
       - 出发车次 >= 25（燕郊/新县）→ 班次充足 → 单站；
       - 班次稀疏（怀柔/广阳）→ 区级可用性 → 扩散到所属市；
    5. 否则既有模糊匹配（通州→北京通州→北京市全部站）。
    """
    if mode == "exact":
        return [resolve_single(query, graph, matcher)]
    if mode != "fuzzy":
        raise ValueError(f"未知匹配模式: {mode}")

    q = query.strip()

    # 规则 1：城市名（去行政后缀）→ 全市扩散
    city_code = matcher.city_name_to_code.get(_strip_all_suffixes(q.lower()))
    if city_code:
        stations = matcher.city_to_stations.get(city_code, [])
        if stations:
            return list(stations)

    # 规则 2：带"区"后缀 → 区级地名归并所属市（怀柔区/广阳区 → 北京/廊坊）
    if q.endswith("区") and q[:-1] in graph.station_to_idx:
        idx = graph.station_to_idx[q[:-1]]
        stations = matcher.city_to_stations.get(graph.station_to_city_code.get(idx, ""), [])
        if stations:
            return list(stations)

    # 规则 3：带"县/镇/乡"后缀 → 县级地名单站（有站时；无站走规则 5）
    if q.endswith(("县", "镇", "乡")) and q in graph.station_to_idx:
        return [q]

    # 规则 4：无后缀站名存在
    if q in graph.station_to_idx:
        idx = graph.station_to_idx[q]
        city_code = graph.station_to_city_code.get(idx, "")
        city_name = graph.city_code_to_name.get(city_code, "")
        # 站名以所属城市名开头（北京南/信阳东/廊坊北）→ 城市前缀扩散
        # → 所属市全部站（"北京西"→北京全部站；区/县/镇由规则 2/3 先行拦截）
        if city_name and q.startswith(city_name):
            stations = matcher.city_to_stations.get(city_code, [])
            if stations:
                return list(stations)
            return [q]
        # 地名同名站规则：站名去方位后缀后的地名与组内其他站同名
        # （曲阜东→曲阜、霸州西→霸州）→ 同城扩散（与 fuzzy_match 兜底规则一致）
        stations = matcher.city_to_stations.get(city_code, [])
        base = q[:-1] if q[-1:] in ("东", "西", "南", "北") else q
        if any(s != q and s == base for s in stations):
            return list(stations)
        # 班次充足（燕郊 43 班/新县 35 班）→ 独立车站 → 单站
        n_dep = len(graph.departures.get(idx, ()))
        if n_dep >= MIN_STATION_TRAINS_FOR_SINGLE:
            return [q]
        # 班次稀疏（怀柔 10 班/广阳 17 班）→ 区级可用性差 → 扩散所属市
        stations = matcher.city_to_stations.get(city_code, [])
        if stations:
            return list(stations)
        return [q]

    # 规则 5：既有模糊（后缀/包含/拼音），无同名站的地名归并到所属城市
    city_code = resolve_city_code(q, graph, matcher)
    stations = matcher.city_to_stations.get(city_code, [])
    if not stations:
        raise ValueError(f"未找到城市内有效铁路站: {query}")
    return list(stations)
