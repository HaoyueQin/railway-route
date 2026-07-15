"""统一搜索请求、路径段、结果与元数据模型。"""

from dataclasses import dataclass, field
from typing import Literal, Optional, Union


MatchMode = Literal["exact", "fuzzy"]
SearchProfile = Literal["fast", "balanced", "thorough", "complete"]


@dataclass(frozen=True)
class SearchProfileSettings:
    max_states_per_station: Optional[int]
    max_results: Optional[int]
    use_relaxed_dominance: bool
    default_timeout_seconds: int = 60
    state_limit: int = 1_000_000


SEARCH_PROFILES: dict[str, SearchProfileSettings] = {
    "fast": SearchProfileSettings(8, None, True, 15, 200_000),
    "balanced": SearchProfileSettings(20, None, True, 30, 500_000),
    "thorough": SearchProfileSettings(80, None, False, 60, 1_500_000),
    "complete": SearchProfileSettings(None, None, False, 120, 5_000_000),
}


@dataclass(frozen=True)
class SearchRequest:
    from_query: str
    to_query: str
    match_mode: MatchMode = "fuzzy"
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
