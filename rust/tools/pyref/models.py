"""统一搜索请求、路径段、结果与元数据模型。"""

from dataclasses import dataclass, field
from typing import Literal, Optional, Union


MatchMode = Literal["exact", "fuzzy"]
SearchProfile = Literal["fast", "balanced", "thorough", "complete"]


@dataclass(frozen=True)
class SearchProfileSettings:
    max_states_per_station: Optional[int]  # 每轮每站的标签上限（轮次化 CSA）
    max_results: Optional[int]
    use_relaxed_dominance: bool  # 保留字段（轮次化后轮内为严格 Pareto）
    default_timeout_seconds: int = 60
    state_limit: int = 1_000_000
    time_prune_slack: Optional[int] = None  # 目标导向耗时剪枝松弛（分钟）；None = 不剪


SEARCH_PROFILES: dict[str, SearchProfileSettings] = {
    "fast": SearchProfileSettings(4, None, True, 15, 200_000, 240),
    "balanced": SearchProfileSettings(8, None, True, 30, 1_500_000, 300),
    "thorough": SearchProfileSettings(16, None, False, 60, 3_000_000, 420),
    # complete 保留大标签上限与宽松时间窗（12h），仍受 state_limit 兜底
    "complete": SearchProfileSettings(24, None, False, 120, 8_000_000, 720),
}


@dataclass(frozen=True)
class SearchRequest:
    from_query: str
    to_query: str
    match_mode: MatchMode = "fuzzy"
    # 每端独立匹配模式（None = 跟随 match_mode）：如 from_mode="exact" + to_mode="fuzzy"
    from_mode: Optional[MatchMode] = None
    to_mode: Optional[MatchMode] = None
    search_profile: SearchProfile = "balanced"
    earliest_depart: int = 0
    latest_depart: int = 2880
    earliest_arrive: int = 0
    latest_arrive: int = 5760
    same_station_transfer_minutes: int = 15
    interstation_transfer_minutes: int = 60
    max_transfers: int = 3
    transfer_city_code: Optional[str] = None
    timeout_seconds: int = 30


@dataclass(frozen=True)
class TrainSegment:
    train_code: str
    from_station: str
    to_station: str
    depart_minutes: int
    arrive_minutes: int
    travel_minutes: int
    distance: int
    segment_type: Literal["train"] = field(default="train", init=False)


@dataclass(frozen=True)
class InterstationTransferSegment:
    from_station: str
    to_station: str
    start_minutes: int
    end_minutes: int
    transfer_minutes: int
    city_code: str
    city_name: str
    estimate_source: str = "user_default"
    segment_type: Literal["interstation"] = field(default="interstation", init=False)


PathSegment = Union[TrainSegment, InterstationTransferSegment]


@dataclass(frozen=True)
class RouteResult:
    segments: tuple[PathSegment, ...]
    actual_origin: str
    actual_destination: str
    first_departure: int
    final_arrival: int
    total_minutes: int
    rail_distance: int
    train_transfers: int
    interstation_transfers: int = 0
    interstation_minutes: int = 0
    transfer_cities: tuple[str, ...] = ()
    matched_transfer_constraint: bool = False


@dataclass(frozen=True)
class SearchMetadata:
    profile: str
    complete: bool = True
    stopped_reason: Optional[str] = None
    elapsed_ms: int = 0
    scanned_connections: int = 0
    generated_states: int = 0
    returned_routes: int = 0


@dataclass(frozen=True)
class SearchResponse:
    routes: tuple[RouteResult, ...]
    metadata: SearchMetadata
    source_stations: tuple[str, ...] = ()
    target_stations: tuple[str, ...] = ()


def format_absolute_minutes(minutes: int) -> dict[str, int | str]:
    """把绝对分钟转换为保留日偏移的统一 API/CLI/GUI 时间结构。"""
    day_offset, minute_of_day = divmod(minutes, 1440)
    hour, minute = divmod(minute_of_day, 60)
    clock = f"{hour:02d}:{minute:02d}"
    if day_offset == 0:
        display = clock
    elif day_offset == 1:
        display = f"次日 {clock}"
    else:
        display = f"第{day_offset + 1}日 {clock}"
    return {
        "minutes": minutes,
        "time": clock,
        "day_offset": day_offset,
        "display": display,
    }


def segment_key(segment: PathSegment) -> tuple:
    if isinstance(segment, TrainSegment):
        return (
            "train",
            segment.train_code,
            segment.from_station,
            segment.to_station,
            segment.depart_minutes,
            segment.arrive_minutes,
        )
    return (
        "interstation",
        segment.from_station,
        segment.to_station,
        segment.start_minutes,
        segment.end_minutes,
    )


def route_key(segments: tuple[PathSegment, ...]) -> tuple:
    return tuple(segment_key(segment) for segment in segments)
