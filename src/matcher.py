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

    return MatcherData(
        all_stations=all_stations,
        city_to_stations=dict(city_to_stations),
        telecode_to_name=telecode_to_name,
        pinyin_to_names=dict(pinyin_to_names),
        station_to_city_code=station_to_city_code,
        city_name_to_code=city_name_to_code,
        city_code_to_name=city_code_to_name,
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
        station_clean = _strip_all_suffixes(station.lower())
        station_no_suffix = _strip_suffix(station.lower())
        if q_clean == station_no_suffix or q_clean == station_clean:
            add(160, station)

    for station in matcher.all_stations:
        station_clean = _strip_all_suffixes(station.lower())
        if len(station_clean) >= 2 and q_clean.endswith(station_clean):
            add(140 + len(station_clean) * 2, station)
        elif len(q_clean) >= 2 and station_clean.endswith(q_clean):
            add(135 + len(q_clean) * 2, station)

    for station in matcher.all_stations:
        station_clean = _strip_all_suffixes(station.lower())
        if len(q_clean) >= 2 and q_clean in station_clean:
            add(120 + len(q_clean), station)
        elif len(station_clean) >= 2 and station_clean in q_clean:
            add(110 + len(station_clean), station)

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


def resolve_station_set(query: str, mode: str, graph, matcher: MatcherData) -> list[str]:
    """按 exact/fuzzy 模式解析单站或同城全部有效站。"""
    if mode == "exact":
        return [resolve_single(query, graph, matcher)]
    if mode != "fuzzy":
        raise ValueError(f"未知匹配模式: {mode}")

    city_code = resolve_city_code(query, graph, matcher)
    stations = matcher.city_to_stations.get(city_code, [])
    if not stations:
        raise ValueError(f"未找到城市内有效铁路站: {query}")
    return list(stations)
