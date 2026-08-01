"""CLI 和 HTTP API 共用的搜索请求解析与校验。"""

import re
from collections.abc import Mapping

from pyref.models import SEARCH_PROFILES, SearchRequest


_TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2})$")


class RequestValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _value(mapping: Mapping, key: str, default=""):
    value = mapping.get(key, default)
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    return value


def parse_time(value: str, *, default: int | None = None) -> int:
    value = "" if value is None else str(value).strip()
    if not value:
        if default is None:
            raise RequestValidationError("INVALID_TIME", "时间不能为空")
        return default
    match = _TIME_PATTERN.fullmatch(value)
    if not match:
        raise RequestValidationError("INVALID_TIME", f"无效时间: {value}，应为 HH:MM")
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        raise RequestValidationError("INVALID_TIME", f"无效时间: {value}，应为 HH:MM")
    return hour * 60 + minute


def parse_bounded_int(value, name: str, minimum: int, maximum: int, default: int) -> int:
    value = "" if value is None else str(value).strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise RequestValidationError(f"INVALID_{name.upper()}", f"{name} 必须是整数")
    if parsed < minimum or parsed > maximum:
        raise RequestValidationError(
            f"INVALID_{name.upper()}",
            f"{name} 必须在 {minimum}–{maximum} 之间",
        )
    return parsed


def build_search_request(mapping: Mapping) -> SearchRequest:
    """将来自 CLI 或 HTTP 的原始参数转换为 SearchRequest。"""
    from_q = _value(mapping, "from") or _value(mapping, "from_station")
    to_q = _value(mapping, "to") or _value(mapping, "to_station")
    if not from_q or not to_q:
        raise RequestValidationError("MISSING_STATION", "缺少出发站或目的站")

    match_mode = _value(mapping, "match_mode", "fuzzy")
    if match_mode not in ("exact", "fuzzy"):
        raise RequestValidationError("INVALID_MATCH_MODE", "match_mode 必须是 exact 或 fuzzy")

    # 每端独立匹配模式（from_mode/to_mode）：None = 跟随 match_mode
    from_mode = _value(mapping, "from_mode", None) or None
    to_mode = _value(mapping, "to_mode", None) or None
    for name, val in (("from_mode", from_mode), ("to_mode", to_mode)):
        if val is not None and val not in ("exact", "fuzzy"):
            raise RequestValidationError("INVALID_MATCH_MODE", f"{name} 必须是 exact 或 fuzzy")

    profile = _value(mapping, "search_profile", "balanced")
    if profile not in SEARCH_PROFILES:
        raise RequestValidationError("INVALID_SEARCH_PROFILE", "search_profile 必须是 fast/balanced/thorough/complete")

    dep_after = parse_time(_value(mapping, "dep_after"), default=0)
    dep_before = parse_time(_value(mapping, "dep_before"), default=2880)
    arr_after = parse_time(_value(mapping, "arr_after"), default=0)
    arr_before = parse_time(_value(mapping, "arr_before"), default=5760)

    same = parse_bounded_int(
        _value(mapping, "same_transfer"),
        "same_transfer",
        0,
        1440,
        15,
    )
    inter = parse_bounded_int(
        _value(mapping, "inter_transfer"),
        "inter_transfer",
        0,
        1440,
        60,
    )
    max_transfers = parse_bounded_int(
        _value(mapping, "max_transfers"),
        "max_transfers",
        0,
        10,
        3,
    )
    timeout = parse_bounded_int(
        _value(mapping, "timeout"),
        "timeout",
        1,
        600,
        30,
    )

    transfer_city = _value(mapping, "transfer_city") or _value(mapping, "xfer_at") or None

    return SearchRequest(
        from_query=from_q,
        to_query=to_q,
        match_mode=match_mode,
        from_mode=from_mode,
        to_mode=to_mode,
        search_profile=profile,
        earliest_depart=dep_after,
        latest_depart=dep_before,
        earliest_arrive=arr_after,
        latest_arrive=arr_before,
        same_station_transfer_minutes=same,
        interstation_transfer_minutes=inter,
        max_transfers=max_transfers,
        transfer_city_code=transfer_city,
        timeout_seconds=timeout,
    )
