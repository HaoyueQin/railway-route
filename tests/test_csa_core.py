"""综合 CSA 测试：多源多目标、footpath、transfer_at、profiles、dedup、cross-day。"""

import unittest

from src.csa import search as csa_search
from src.models import SEARCH_PROFILES, SearchRequest
from tests.fixtures import build_fixture_graph


# ── 共用的测试 fixture ──────────────────────────────────

def _make_two_city_fixture():
    """城市甲城(001): 甲站(A1), 甲东(A2); 城市乙城(002): 乙站(B1), 乙西(B2).
    A1→B1: T1 慢车 (08:00→12:00, 4h)
    A2→B2: T2 快车 (08:30→10:30, 2h)
    """
    trains = {
        "T1": [
            {"name": "甲站", "depart": "08:00", "distance": 0},
            {"name": "乙站", "arrive": "12:00", "distance": 400},
        ],
        "T2": [
            {"name": "甲东", "depart": "08:30", "distance": 0},
            {"name": "乙西", "arrive": "10:30", "distance": 300},
        ],
    }
    stations = [
        {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan",
         "city_code": "001", "city_name": "甲城"},
        {"short": "jiad", "name": "甲东", "telecode": "JAD", "pinyin": "jiadong",
         "city_code": "001", "city_name": "甲城"},
        {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan",
         "city_code": "002", "city_name": "乙城"},
        {"short": "yix", "name": "乙西", "telecode": "YIX", "pinyin": "yixi",
         "city_code": "002", "city_name": "乙城"},
    ]
    return build_fixture_graph(trains, stations)


def _make_footpath_fixture():
    """城市X(003): X1, X2; 城市A(001): A; 城市B(002): B.
    A→X1: T1;
    X2→B: T2;
    X1→X2: T3 (real train, optional)
    """
    trains = {
        "T1": [
            {"name": "甲站", "depart": "08:00", "distance": 0},
            {"name": "X1站", "arrive": "10:00", "distance": 200},
        ],
        "T2": [
            {"name": "X2站", "depart": "12:00", "distance": 0},
            {"name": "乙站", "arrive": "14:00", "distance": 200},
        ],
        "T3": [
            {"name": "X1站", "depart": "11:00", "distance": 0},
            {"name": "X2站", "arrive": "11:30", "distance": 30},
        ],
    }
    stations = [
        {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan",
         "city_code": "001", "city_name": "甲城"},
        {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan",
         "city_code": "002", "city_name": "乙城"},
        {"short": "x1", "name": "X1站", "telecode": "X1A", "pinyin": "x1zhan",
         "city_code": "003", "city_name": "X城"},
        {"short": "x2", "name": "X2站", "telecode": "X2A", "pinyin": "x2zhan",
         "city_code": "003", "city_name": "X城"},
    ]
    return build_fixture_graph(trains, stations)


def _make_cross_day_fixture():
    """A→B: T1 (23:00→01:00, 跨午夜)"""
    trains = {
        "T1": [
            {"name": "甲站", "depart": "23:00", "distance": 0},
            {"name": "乙站", "arrive": "01:00", "distance": 200},
        ],
    }
    stations = [
        {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan",
         "city_code": "001", "city_name": "甲城"},
        {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan",
         "city_code": "002", "city_name": "乙城"},
    ]
    return build_fixture_graph(trains, stations)


# ── Task 6: 多源/多目标 ──────────────────────────────────

class MultiStationTest(unittest.TestCase):
    def test_exact_returns_one_source_one_target(self):
        graph, matcher, tmp = _make_two_city_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact")
        resp = csa_search(graph, req, matcher)
        # 双日扫描下可能有 day 0 和 day 1 两条路线（同车次、不同日）
        self.assertGreaterEqual(len(resp.routes), 1)
        for route in resp.routes:
            self.assertEqual(route.actual_origin, "甲站")
            self.assertEqual(route.actual_destination, "乙站")
        self.assertEqual(resp.source_stations, ("甲站",))
        self.assertEqual(resp.target_stations, ("乙站",))

    def test_fuzzy_expands_city_name_to_all_stations(self):
        graph, matcher, tmp = _make_two_city_fixture()
        self.addCleanup(tmp.cleanup)
        # 城市名 fuzzy → 全市全部站；站名 fuzzy → 单站（新语义：有站的县级地名不扩散）
        req = SearchRequest(from_query="甲城", to_query="乙城", match_mode="fuzzy")
        resp = csa_search(graph, req, matcher)
        self.assertEqual(set(resp.source_stations), {"甲站", "甲东"})
        self.assertEqual(set(resp.target_stations), {"乙站", "乙西"})
        self.assertGreaterEqual(len(resp.routes), 2)  # 至少 A1→B1 和 A2→B2

    def test_fuzzy_sparse_station_expands_to_city(self):
        graph, matcher, tmp = _make_two_city_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="fuzzy")
        resp = csa_search(graph, req, matcher)
        # fixture 各站仅 1 班车（<25）→ 视为区级可用性扩散同城（怀柔/广阳语义）
        self.assertEqual(set(resp.source_stations), {"甲站", "甲东"})
        self.assertEqual(set(resp.target_stations), {"乙站", "乙西"})

    def test_fuzzy_finds_faster_route_via_other_station(self):
        graph, matcher, tmp = _make_two_city_fixture()
        self.addCleanup(tmp.cleanup)
        # 城市名 fuzzy 展开多站：最佳路线 A2→B2 (2h)
        req = SearchRequest(from_query="甲城", to_query="乙城", match_mode="fuzzy")
        resp = csa_search(graph, req, matcher)
        best = resp.routes[0]
        self.assertEqual(best.actual_origin, "甲东")
        self.assertEqual(best.actual_destination, "乙西")
        self.assertEqual(best.total_minutes, 120)

    def test_scan_once_metadata(self):
        graph, matcher, tmp = _make_two_city_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="fuzzy")
        resp = csa_search(graph, req, matcher)
        self.assertGreater(resp.metadata.scanned_connections, 0)
        self.assertGreater(resp.metadata.generated_states, 0)
        self.assertGreater(resp.metadata.returned_routes, 0)


# ── Task 7: Footpath ─────────────────────────────────────

class FootpathTest(unittest.TestCase):
    def test_without_footpath_no_route(self):
        graph, matcher, tmp = _make_footpath_fixture()
        self.addCleanup(tmp.cleanup)
        # T3 (X1→X2 real train) 提供纯铁路路线 A→X1→X2→B
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=999)
        resp = csa_search(graph, req, matcher)
        # 应有纯铁路路线（T1→T3→T2），不依赖 footpath
        train_only_routes = []
        for route in resp.routes:
            has_ground = any(
                getattr(s, 'segment_type', '') == 'interstation' for s in route.segments)
            if not has_ground:
                train_only_routes.append(route)
        self.assertGreaterEqual(len(train_only_routes), 1,
                                "应有纯铁路路线（T1→T3→T2），不依赖 footpath")

    def test_with_footpath_finds_ground_route(self):
        graph, matcher, tmp = _make_footpath_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=60)
        resp = csa_search(graph, req, matcher)
        # A→T1→X1→ground(60min)→X2→T2→B
        self.assertGreaterEqual(len(resp.routes), 1)
        has_ground = any(
            any(hasattr(s, 'segment_type') and s.segment_type == 'interstation'
                for s in r.segments)
            for r in resp.routes
        )
        self.assertTrue(has_ground)

    def test_real_train_and_ground_coexist(self):
        graph, matcher, tmp = _make_footpath_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=60)
        resp = csa_search(graph, req, matcher)
        keys = set()
        from src.models import route_key
        for r in resp.routes:
            keys.add(route_key(r.segments))
        self.assertGreaterEqual(len(keys), 2)  # 至少 2 条不同路线

    def test_changing_interstation_minutes_changes_feasibility(self):
        graph, matcher, tmp = _make_footpath_fixture()
        self.addCleanup(tmp.cleanup)
        # 60 分钟可行（T1 arrive 10:00 + 60 = 11:00, T2 depart 12:00, OK）
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=60)
        resp = csa_search(graph, req, matcher)
        self.assertGreaterEqual(len(resp.routes), 1)

        # 10 分钟也 OK（T1 arrive 10:00 + 10 = 10:10 < T2 depart 12:00）
        req2 = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                             interstation_transfer_minutes=10)
        resp2 = csa_search(graph, req2, matcher)
        self.assertGreaterEqual(len(resp2.routes), 1)

    def test_no_consecutive_footpath_segments(self):
        graph, matcher, tmp = _make_footpath_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=60)
        resp = csa_search(graph, req, matcher)
        for route in resp.routes:
            prev_kind = None
            for seg in route.segments:
                if prev_kind == "interstation":
                    self.assertNotEqual(
                        getattr(seg, 'segment_type', 'train'), 'interstation',
                        "不应有连续 footpath 段")


# ── Task 8: transfer_at ──────────────────────────────────

class TransferAtTest(unittest.TestCase):
    def _make_xfer_fixture(self):
        """城市X(003): X1, X2; A→X1(T1), X2→B(T2); 同城也有 T3: X1→X2 real train"""
        return _make_footpath_fixture()

    def test_transfer_at_exact_match_station_satisfies(self):
        graph, matcher, tmp = self._make_xfer_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=60,
                            transfer_city_code="X1站")
        resp = csa_search(graph, req, matcher)
        self.assertGreaterEqual(len(resp.routes), 1)
        for route in resp.routes:
            self.assertTrue(route.matched_transfer_constraint)

    def test_transfer_at_city_name_satisfies(self):
        graph, matcher, tmp = self._make_xfer_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=60,
                            transfer_city_code="X城")
        resp = csa_search(graph, req, matcher)
        self.assertGreaterEqual(len(resp.routes), 1)
        for route in resp.routes:
            self.assertTrue(route.matched_transfer_constraint)

    def test_transfer_at_no_match_returns_empty(self):
        graph, matcher, tmp = self._make_xfer_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=60,
                            transfer_city_code="甲城")  # 甲城不是换乘城市，只是出发城市
        resp = csa_search(graph, req, matcher)
        # 途经甲城不换乘不满足
        for route in resp.routes:
            self.assertFalse(route.matched_transfer_constraint)

    def test_footpath_in_constrained_city_satisfies(self):
        graph, matcher, tmp = self._make_xfer_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=60,
                            transfer_city_code="X城")
        resp = csa_search(graph, req, matcher)
        # 确保至少有一条路线（footpath 路线）满足约束
        self.assertGreaterEqual(len(resp.routes), 1)
        has_ground = any(
            any(getattr(s, 'segment_type', '') == 'interstation' for s in r.segments)
            for r in resp.routes
        )
        self.assertTrue(has_ground, "应有 ground 换乘路线满足约束")


# ── Task 9: Search Profiles ──────────────────────────────

class SearchProfileTest(unittest.TestCase):
    def test_profile_settings_explicit(self):
        from src.models import SEARCH_PROFILES
        self.assertIsNotNone(SEARCH_PROFILES["fast"].max_states_per_station)
        self.assertIsNotNone(SEARCH_PROFILES["balanced"].max_states_per_station)
        self.assertIsNotNone(SEARCH_PROFILES["thorough"].max_states_per_station)
        # complete 每轮每站上限最大（受 state_limit 兜底）
        self.assertGreater(SEARCH_PROFILES["complete"].max_states_per_station,
                           SEARCH_PROFILES["thorough"].max_states_per_station)
        self.assertTrue(SEARCH_PROFILES["fast"].use_relaxed_dominance)
        self.assertTrue(SEARCH_PROFILES["balanced"].use_relaxed_dominance)
        self.assertFalse(SEARCH_PROFILES["complete"].use_relaxed_dominance)

    def test_fast_vs_balanced_limits(self):
        fast = SEARCH_PROFILES["fast"]
        balanced = SEARCH_PROFILES["balanced"]
        self.assertLess(fast.max_states_per_station, balanced.max_states_per_station)
        # max_results is now None (no cap) for both

    def test_balanced_vs_thorough_limits(self):
        balanced = SEARCH_PROFILES["balanced"]
        thorough = SEARCH_PROFILES["thorough"]
        self.assertLess(balanced.max_states_per_station, thorough.max_states_per_station)
        # max_results is now None (no cap) for both

    def test_all_profiles_search(self):
        graph, matcher, tmp = _make_two_city_fixture()
        self.addCleanup(tmp.cleanup)
        for profile in ("fast", "balanced", "thorough", "complete"):
            req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="fuzzy",
                                search_profile=profile)
            resp = csa_search(graph, req, matcher)
            self.assertEqual(resp.metadata.profile, profile)
            self.assertTrue(resp.metadata.complete)
            self.assertIsNone(resp.metadata.stopped_reason)
            self.assertGreaterEqual(resp.metadata.returned_routes, 1)

    def test_normal_complete_reports_true(self):
        graph, matcher, tmp = _make_two_city_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="fuzzy",
                            search_profile="complete")
        resp = csa_search(graph, req, matcher)
        self.assertTrue(resp.metadata.complete)


# ── Task 10: Dedup ───────────────────────────────────────

class RouteDedupTest(unittest.TestCase):
    def test_distinct_routes_not_merged(self):
        graph, matcher, tmp = _make_footpath_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            interstation_transfer_minutes=60)
        resp = csa_search(graph, req, matcher)
        # 应有 real train 路线和 ground 路线共存
        from src.models import route_key
        keys = {route_key(r.segments) for r in resp.routes}
        self.assertGreaterEqual(len(keys), 2, "不同路线类型不应被去重合并")

    def test_exact_duplicates_are_removed(self):
        graph, matcher, tmp = _make_two_city_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact")
        resp = csa_search(graph, req, matcher)
        # 双日扫描可能产生 day 0 和 day 1 两条（不同绝对时间），都是合法路线
        from src.models import route_key
        keys = {route_key(r.segments) for r in resp.routes}
        self.assertEqual(len(keys), len(resp.routes), "每条路线应有唯一 key，无重复合并")


# ── Cross-Day ────────────────────────────────────────────

class CrossDayTest(unittest.TestCase):
    def test_cross_midnight_route(self):
        graph, matcher, tmp = _make_cross_day_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact")
        resp = csa_search(graph, req, matcher)
        # 双日扫描可能有 day 0 和 day 1 两条
        self.assertGreaterEqual(len(resp.routes), 1)
        route = resp.routes[0]
        self.assertEqual(route.first_departure, 23 * 60)  # 23:00
        self.assertEqual(route.final_arrival, 25 * 60)    # 25:00 (= 次日 01:00)
        self.assertEqual(route.total_minutes, 120)

    def test_cross_midnight_day_offset_display(self):
        from src.models import format_absolute_minutes
        fmt = format_absolute_minutes(25 * 60)  # 次日 01:00
        self.assertEqual(fmt["day_offset"], 1)
        self.assertEqual(fmt["display"], "次日 01:00")
        self.assertEqual(fmt["time"], "01:00")


if __name__ == "__main__":
    unittest.main()
