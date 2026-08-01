# -*- coding: utf-8 -*-
"""pyref 参考实现入口：评分与序列化（对拍基准生成用）。

抽取自 master 分支的 src/main.py 36-112 行（score_routes + typed_route_to_dict），
只保留 Rust 对拍所需部分。与 master 的 src/main.py 保持同步（改动需两侧同改）。
"""
from pyref.models import (
    InterstationTransferSegment,
    RouteResult,
    TrainSegment,
    format_absolute_minutes,
)


def score_routes(routes: list[RouteResult]) -> list[tuple[float, RouteResult]]:
    if not routes:
        return []
    max_t = max(r.total_minutes for r in routes) or 1
    max_d = max(r.rail_distance for r in routes) or 1
    scored = []
    for r in routes:
        night_penalty = 0
        for seg in r.segments:
            if isinstance(seg, TrainSegment):
                for m in (seg.depart_minutes, seg.arrive_minutes):
                    h = (m % 1440) // 60
                    if h >= 23 or h < 6:
                        night_penalty += 1
            elif isinstance(seg, InterstationTransferSegment):
                for m in (seg.start_minutes, seg.end_minutes):
                    h = (m % 1440) // 60
                    if h >= 23 or h < 6:
                        night_penalty += 1
        inter_penalty = r.interstation_minutes / max(60, 1)
        s = (max(0, 1 - r.total_minutes / max_t) * 0.35 +
             max(0, 1 - (r.train_transfers + r.interstation_transfers * 0.5) / 4) * 0.25 +
             max(0, 1 - night_penalty * 0.1) * 0.10 +
             max(0, 1 - r.rail_distance / max_d) * 0.15 +
             max(0, 1 - inter_penalty / 300) * 0.15)
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return scored


def typed_route_to_dict(route: RouteResult, score: float = 0) -> dict:
    segments = []
    for segment in route.segments:
        if isinstance(segment, TrainSegment):
            segments.append({
                "type": "train",
                "train_code": segment.train_code,
                "from_station": segment.from_station,
                "to_station": segment.to_station,
                "depart": format_absolute_minutes(segment.depart_minutes),
                "arrive": format_absolute_minutes(segment.arrive_minutes),
                "travel_minutes": segment.travel_minutes,
                "distance": segment.distance,
            })
        elif isinstance(segment, InterstationTransferSegment):
            segments.append({
                "type": "interstation",
                "from_station": segment.from_station,
                "to_station": segment.to_station,
                "start": format_absolute_minutes(segment.start_minutes),
                "end": format_absolute_minutes(segment.end_minutes),
                "transfer_minutes": segment.transfer_minutes,
                "city_code": segment.city_code,
                "city_name": segment.city_name,
                "estimate_source": segment.estimate_source,
            })
    return {
        "score": round(score, 3),
        "actual_origin": route.actual_origin,
        "actual_destination": route.actual_destination,
        "first_departure": format_absolute_minutes(route.first_departure),
        "final_arrival": format_absolute_minutes(route.final_arrival),
        "total_minutes": route.total_minutes,
        "rail_distance": route.rail_distance,
        "train_transfers": route.train_transfers,
        "interstation_transfers": route.interstation_transfers,
        "interstation_minutes": route.interstation_minutes,
        "transfer_cities": list(route.transfer_cities),
        "segments": segments,
    }
