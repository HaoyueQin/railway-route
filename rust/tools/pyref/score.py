"""
路径评分与排序模块（Phase 3）。

对匹配后的换乘方案进行多维度评分，按人性化偏好排序。
V1：Sigmoid 平滑加权评分函数。
"""

import math
from dataclasses import dataclass

from matcher import MatchedRoute


def sigmoid(x: float, k: float = 1.0, midpoint: float = 0.0) -> float:
    """Sigmoid 平滑函数：1 / (1 + exp(-k * (x - midpoint)))。"""
    try:
        return 1.0 / (1.0 + math.exp(-k * (x - midpoint)))
    except OverflowError:
        return 0.0 if k * (x - midpoint) < 0 else 1.0


@dataclass
class ScoredRoute:
    """评分后的路径方案。"""
    route: MatchedRoute
    score: float
    scores_detail: dict[str, float]  # 各维度得分明细


def score_routes(routes: list[MatchedRoute]) -> list[ScoredRoute]:
    """
    对路径方案评分并排序（分数越高越好）。

    评分维度：
    1. 总耗时（惩罚长耗时）
    2. 换乘次数（惩罚换乘）
    3. 凌晨时段（惩罚 23:00-06:00 的乘车或换乘）
    4. 里程效率（惩罚绕路）

    权重可调，当前为初始经验值。
    """
    if not routes:
        return []

    # 归一化参考值
    max_time = max(r.total_minutes for r in routes) or 1
    max_dist = max(r.total_distance for r in routes) or 1
    max_transfers = max(len(r.transfer_stations) for r in routes) or 1

    scored = []
    for route in routes:
        details = {}

        # 1. 总耗时得分（越短越高，Sigmoid 平滑）
        time_ratio = route.total_minutes / max_time
        details["耗时"] = 1.0 - sigmoid(time_ratio, k=4.0, midpoint=0.5)

        # 2. 换乘次数得分（越少越高）
        transfers = len(route.transfer_stations)
        details["换乘"] = 1.0 - sigmoid(transfers, k=2.0, midpoint=1.0)

        # 3. 凌晨惩罚（检查每段的发车/到达时间）
        midnight_penalty = _midnight_penalty(route)
        details["凌晨"] = 1.0 - midnight_penalty

        # 4. 里程效率（越短越高）
        dist_ratio = route.total_distance / max_dist
        details["里程"] = 1.0 - sigmoid(dist_ratio, k=3.0, midpoint=0.5)

        # 加权总分
        weights = {
            "耗时": 0.40,
            "换乘": 0.30,
            "凌晨": 0.15,
            "里程": 0.15,
        }
        total = sum(details[k] * weights[k] for k in weights)

        scored.append(ScoredRoute(route=route, score=total, scores_detail=details))

    # 按分数降序
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def _midnight_penalty(route: MatchedRoute) -> float:
    """计算凌晨时段惩罚（0~1，越高越差）。"""
    bonus_count = 0
    for seg in route.segments:
        # 发车时间
        if seg.depart_time:
            h = int(seg.depart_time.split(":")[0])
            if 23 <= h or h < 6:
                bonus_count += 1
        # 到达时间
        if seg.arrive_time:
            h = int(seg.arrive_time.split(":")[0])
            if 23 <= h or h < 6:
                bonus_count += 1

    # 换乘等待中跨凌晨
    # （简化：检查换乘等待时间是否包含凌晨时段）
    for i in range(len(route.segments) - 1):
        arrive = route.segments[i].arrive_time
        depart = route.segments[i + 1].depart_time
        if arrive and depart:
            ah = int(arrive.split(":")[0])
            dh = int(depart.split(":")[0])
            # 如果到达在深夜，发车在凌晨后
            if ah >= 22 and (dh < 6 or dh < ah):
                bonus_count += 1

    if bonus_count == 0:
        return 0.0
    # 使用平滑函数
    return sigmoid(bonus_count, k=1.5, midpoint=1.0)


def format_route(scored: ScoredRoute) -> str:
    """格式化输出一条路径。"""
    r = scored.route
    lines = []
    lines.append(f"  {'─' * 60}")
    lines.append(f"  得分: {scored.score:.3f}  "
                 f"| 总耗时: {r.total_minutes // 60}h{r.total_minutes % 60:02d}m  "
                 f"| 换乘: {len(r.transfer_stations)}次  "
                 f"| 里程: {r.total_distance}km")
    lines.append(f"  详细: 耗时={scored.scores_detail['耗时']:.2f}  "
                 f"换乘={scored.scores_detail['换乘']:.2f}  "
                 f"凌晨={scored.scores_detail['凌晨']:.2f}  "
                 f"里程={scored.scores_detail['里程']:.2f}")

    for i, seg in enumerate(r.segments):
        lines.append(f"  [{i + 1}] {seg.train_code}  "
                     f"{seg.from_station} {seg.depart_time} → "
                     f"{seg.to_station} {seg.arrive_time}  "
                     f"({seg.travel_minutes}分钟, {seg.distance}km)")

        # 如果有换乘，在段之间标注
        if i < len(r.segments) - 1:
            lines.append(f"       ╚ 换乘 @" + r.transfer_stations[i] if i < len(r.transfer_stations) else " ╚ 换乘")

    return "\n".join(lines)
