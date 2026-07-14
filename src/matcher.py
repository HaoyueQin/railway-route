"""
模糊匹配模块 — 参考 weather-image-generator 的设计。

支持：
- 精确站名（"北京南" → 北京南）
- 城市名（"北京" → 北京南,北京西,北京站,...）
- 去后缀（"北京市" → 同上）
- 部分匹配（"曲阜" → 曲阜东站）
- 拼音/电报码匹配（"VNP" → 北京南）
"""

import re
from collections import defaultdict


def build_matcher(graph, station_js_path: str):
    """
    构建匹配数据库。
    返回 (all_stations, city_map, telecode_map, name_index)
    """
    all_stations = list(graph.station_to_idx.keys())

    # 城市 → 站名列表
    city_to_stations: dict[str, list[str]] = defaultdict(list)
    # 电报码 → 站名
    telecode_to_name: dict[str, str] = {}
    # 拼音 → 站名列表
    pinyin_to_names: dict[str, list[str]] = defaultdict(list)

    with open(station_js_path, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"station_names\s*=\s*'(.*)'\s*$", text, re.DOTALL)
    content = m.group(1) if m else text.split("'", 1)[1].rsplit("'", 1)[0]

    for entry in content.split("@"):
        if not entry.strip():
            continue
        parts = entry.strip().split("|")
        if len(parts) < 7:
            continue
        name = parts[1]
        telecode = parts[2]
        pinyin = parts[3]
        city_code = parts[6]

        if name in graph.station_to_idx:
            city_to_stations[city_code].append(name)
            telecode_to_name[telecode] = name
            if pinyin:
                pinyin_to_names[pinyin.lower()].append(name)

    return all_stations, city_to_stations, telecode_to_name, pinyin_to_names


# 常见后缀（从 weather 项目参考）
_SUFFIXES = ['市', '区', '县', '省', '地区', '站']

def _strip_suffix(text: str) -> str:
    for s in _SUFFIXES:
        if text.endswith(s) and len(text) > len(s):
            return text[:-len(s)]
    return text

def _strip_all_suffixes(text: str) -> str:
    changed = True
    while changed:
        changed = False
        for s in _SUFFIXES:
            if text.endswith(s) and len(text) > len(s):
                text = text[:-len(s)]
                changed = True
                break
    return text


def fuzzy_match(query: str, graph, all_stations: list[str],
                city_map: dict, telecode_map: dict,
                pinyin_map: dict) -> list[tuple[int, str]]:
    """
    模糊匹配，返回 [(score, station_name), ...]，按分数降序。
    """
    q = query.strip().upper()
    q_lower = query.strip().lower()
    q_clean = _strip_all_suffixes(q_lower)

    results: list[tuple[int, str]] = []
    seen = set()

    def add(score: int, name: str):
        if name not in seen and name in graph.station_to_idx:
            seen.add(name)
            results.append((score, name))

    # ── 1. 精确站名匹配（最高优先级）──
    if q in graph.station_to_idx or query.strip() in graph.station_to_idx:
        add(200, query.strip())

    # ── 2. 电报码匹配 ──
    if q in telecode_map:
        add(190, telecode_map[q])

    # ── 3. 拼音匹配 ──
    if q_lower in pinyin_map:
        for name in pinyin_map[q_lower]:
            add(180, name)

    # ── 4. 城市名匹配（"北京" → 北京所有站）──
    # city_map 的 key 是 city_code（如"0357"），需要遍历站名找城市
    for city_code, st_list in city_map.items():
        for st in st_list:
            st_clean = _strip_all_suffixes(st.lower())
            # "北京" matches "北京"
            if q_clean == st_clean:
                for s in st_list:
                    add(170, s)
                break  # 只处理一次

    # ── 5. 站名去后缀匹配 ──
    for st in all_stations:
        st_clean = _strip_all_suffixes(st.lower())
        # "北京南" → strip → "北京南" (站不是后缀, 南也不是) — 不strip
        st_no_suffix = _strip_suffix(st.lower())
        if q_clean == st_no_suffix or q_clean == st_clean:
            add(160, st)

    # ── 6. 查询以站名结尾（"东曲阜"→"曲阜东"不行，需要"山东曲阜"→"曲阜"）──
    for st in all_stations:
        st_lower = st.lower()
        st_clean = _strip_all_suffixes(st_lower)
        if len(st_clean) >= 2 and q_clean.endswith(st_clean):
            add(140 + len(st_clean) * 2, st)
        elif len(q_clean) >= 2 and st_clean.endswith(q_clean):
            add(135 + len(q_clean) * 2, st)

    # ── 7. 包含匹配 ──
    for st in all_stations:
        st_lower = st.lower()
        st_clean = _strip_all_suffixes(st_lower)
        if len(q_clean) >= 2 and q_clean in st_clean:
            add(120 + len(q_clean), st)
        elif len(st_clean) >= 2 and st_clean in q_clean:
            add(110 + len(st_clean), st)

    results.sort(key=lambda x: -x[0])
    # 去重保留最高分
    final = []
    seen2 = set()
    for score, name in results:
        if name not in seen2:
            seen2.add(name)
            final.append((score, name))
    return final


def resolve_single(query: str, graph, all_stations, city_map,
                   telecode_map, pinyin_map) -> str:
    """解析为单个首选车站。"""
    matches = fuzzy_match(query, graph, all_stations, city_map, telecode_map, pinyin_map)
    if not matches:
        raise ValueError(f"未找到匹配的车站: {query}")
    # 优先选主站（不带方位词）
    q_clean = _strip_all_suffixes(query.strip().lower())
    for _, name in matches:
        if _strip_all_suffixes(name.lower()) == q_clean:
            return name
    return matches[0][1]
