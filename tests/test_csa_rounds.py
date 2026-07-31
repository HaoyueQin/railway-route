"""轮次化多标签 CSA 的新语义测试。

覆盖轮次化重构（RAPTOR 风格）引入/强调的行为：
- 多轮次换乘路线（0/1/2/3 转）与结果排序语义（转数优先）；
- 同车次多段续乘合并为单个 TrainSegment；
- 跨夜接续（当天到达、次日换乘）路线；
- max_transfers 边界（0 与恰好满足）；
- 同站换乘缓冲恰好相等/差 1 分钟的边界；
- 目标站作为中间站继续传播（多目标 fuzzy 经一个目标站到达另一目标站）；
- 轮内跨车次不支配（不同车次到同一站都保留，稍晚但独立的候选不丢失）；
- 时间窗口过滤（dep_after / arr_before）。
"""

import unittest

from src.csa import Label, _insert_round_label, search as csa_search
from src.models import SearchRequest, route_key
from tests.fixtures import build_fixture_graph


def _make_multihop_fixture():
    """甲→中1(T1 08:00-09:00)→中2(T2 09:30-10:30)→乙(T3 11:00-12:00)：2 次换乘链；
    另有直达慢车 T4（甲 08:30→乙 13:30, 5h, 0 转）。"""
    trains = {
        "T1": [
            {"name": "甲站", "depart": "08:00", "distance": 0},
            {"name": "中1站", "arrive": "09:00", "distance": 100},
        ],
        "T2": [
            {"name": "中1站", "depart": "09:30", "distance": 100},
            {"name": "中2站", "arrive": "10:30", "distance": 200},
        ],
        "T3": [
            {"name": "中2站", "depart": "11:00", "distance": 200},
            {"name": "乙站", "arrive": "12:00", "distance": 300},
        ],
        "T4": [
            {"name": "甲站", "depart": "08:30", "distance": 0},
            {"name": "乙站", "arrive": "13:30", "distance": 300},
        ],
    }
    stations = [
        {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan",
         "city_code": "001", "city_name": "甲城"},
        {"short": "z1", "name": "中1站", "telecode": "Z1A", "pinyin": "zhong1",
         "city_code": "002", "city_name": "中一城"},
        {"short": "z2", "name": "中2站", "telecode": "Z2A", "pinyin": "zhong2",
         "city_code": "003", "city_name": "中二城"},
        {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan",
         "city_code": "004", "city_name": "乙城"},
    ]
    return build_fixture_graph(trains, stations)


def _make_cross_night_fixture():
    """甲→换(T1 13:00-14:00)→乙(T2 次日 08:00-10:00)：跨夜换乘（等待约 18 小时）。"""
    trains = {
        "T1": [
            {"name": "甲站", "depart": "13:00", "distance": 0},
            {"name": "换站", "arrive": "14:00", "distance": 100},
        ],
        "T2": [
            {"name": "换站", "depart": "08:00", "distance": 100},
            {"name": "乙站", "arrive": "10:00", "distance": 200},
        ],
    }
    stations = [
        {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan",
         "city_code": "001", "city_name": "甲城"},
        {"short": "huan", "name": "换站", "telecode": "HAA", "pinyin": "huanzhan",
         "city_code": "002", "city_name": "换城"},
        {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan",
         "city_code": "003", "city_name": "乙城"},
    ]
    return build_fixture_graph(trains, stations)


def _make_target_transit_fixture():
    """甲→乙站(T1 08:00-10:00)、乙站→乙西(T2 11:00-12:00)；乙站与乙西同城。
    fuzzy 甲→乙城 的目标集合为 {乙站, 乙西}：可经目标站乙站中转到达另一目标站乙西。"""
    trains = {
        "T1": [
            {"name": "甲站", "depart": "08:00", "distance": 0},
            {"name": "乙站", "arrive": "10:00", "distance": 100},
        ],
        "T2": [
            {"name": "乙站", "depart": "11:00", "distance": 100},
            {"name": "乙西", "arrive": "12:00", "distance": 150},
        ],
    }
    stations = [
        {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan",
         "city_code": "001", "city_name": "甲城"},
        {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan",
         "city_code": "002", "city_name": "乙城"},
        {"short": "yix", "name": "乙西", "telecode": "YIX", "pinyin": "yixi",
         "city_code": "002", "city_name": "乙城"},
    ]
    return build_fixture_graph(trains, stations)


def _make_coexist_fixture():
    """甲→中(T1 08:00-09:00, 100km) 与 甲→中(T2 08:30-08:45, 50km)；
    中→乙(T3 10:00-11:00)。T2 更早更短（若跨车次支配会杀掉 T1 路线），
    但轮内不支配保证两条独立候选都保留。"""
    trains = {
        "T1": [
            {"name": "甲站", "depart": "08:00", "distance": 0},
            {"name": "中站", "arrive": "09:00", "distance": 100},
        ],
        "T2": [
            {"name": "甲站", "depart": "08:30", "distance": 0},
            {"name": "中站", "arrive": "08:45", "distance": 50},
        ],
        "T3": [
            {"name": "中站", "depart": "10:00", "distance": 100},
            {"name": "乙站", "arrive": "11:00", "distance": 200},
        ],
    }
    stations = [
        {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan",
         "city_code": "001", "city_name": "甲城"},
        {"short": "zhong", "name": "中站", "telecode": "ZHA", "pinyin": "zhongzhan",
         "city_code": "002", "city_name": "中城"},
        {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan",
         "city_code": "003", "city_name": "乙城"},
    ]
    return build_fixture_graph(trains, stations)


def _make_buffer_fixture():
    """甲→中(T1 08:00-09:00)、中→乙(T2 09:15-10:00)：换乘缓冲恰好 15 分钟。"""
    trains = {
        "T1": [
            {"name": "甲站", "depart": "08:00", "distance": 0},
            {"name": "中站", "arrive": "09:00", "distance": 100},
        ],
        "T2": [
            {"name": "中站", "depart": "09:15", "distance": 100},
            {"name": "乙站", "arrive": "10:00", "distance": 200},
        ],
    }
    stations = [
        {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan",
         "city_code": "001", "city_name": "甲城"},
        {"short": "zhong", "name": "中站", "telecode": "ZHA", "pinyin": "zhongzhan",
         "city_code": "002", "city_name": "中城"},
        {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan",
         "city_code": "003", "city_name": "乙城"},
    ]
    return build_fixture_graph(trains, stations)


class RoundSemanticsTest(unittest.TestCase):
    def test_zero_and_two_transfer_routes_coexist(self):
        graph, matcher, tmp = _make_multihop_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact")
        resp = csa_search(graph, req, matcher)
        xfers = {r.train_transfers for r in resp.routes}
        self.assertIn(0, xfers, "应有 0 转直达（T4）")
        self.assertIn(2, xfers, "应有 2 转换乘路线（T1→T2→T3）")
        # 2 转路线：T1→T2→T3 三段车次
        two = [r for r in resp.routes if r.train_transfers == 2]
        self.assertTrue(any(
            [getattr(s, "train_code", "") for s in r.segments] == ["T1", "T2", "T3"]
            for r in two), "2 转路线应为 T1→T2→T3")
        # 2 转路线总耗时 = 08:00→12:00 = 240 分钟
        self.assertEqual(two[0].total_minutes, 240)

    def test_sort_prefers_fewer_transfers(self):
        graph, matcher, tmp = _make_multihop_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact")
        resp = csa_search(graph, req, matcher)
        # 排序语义：(换乘次数, 总耗时)——0 转 T4（300min）先于 2 转（240min）
        self.assertEqual(resp.routes[0].train_transfers, 0)
        self.assertEqual(resp.routes[0].total_minutes, 300)

    def test_same_train_continuation_merged(self):
        graph, matcher, tmp = _make_multihop_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact")
        resp = csa_search(graph, req, matcher)
        for route in resp.routes:
            train_segs = [s for s in route.segments
                          if getattr(s, "segment_type", "") == "train"]
            codes = [s.train_code for s in train_segs]
            # 同一车次在路线中不应出现多次（续乘段已合并）
            self.assertEqual(len(codes), len(set(codes)),
                             f"同车次续乘未合并: {codes}")

    def test_metadata_complete_on_small_graph(self):
        graph, matcher, tmp = _make_multihop_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact")
        resp = csa_search(graph, req, matcher)
        self.assertTrue(resp.metadata.complete)


class CrossNightTest(unittest.TestCase):
    def test_cross_night_transfer_route_found(self):
        graph, matcher, tmp = _make_cross_night_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact")
        resp = csa_search(graph, req, matcher)
        self.assertGreaterEqual(len(resp.routes), 1, "跨夜换乘路线应存在")
        route = resp.routes[0]
        self.assertEqual(route.train_transfers, 1)
        # T1 13:00 出发，T2 次日 10:00 到达：总耗时 = 1440+600-780 = 1260 分钟
        self.assertEqual(route.total_minutes, 1260)
        self.assertEqual(route.first_departure, 780)
        self.assertEqual(route.final_arrival, 2040)


class MaxTransfersTest(unittest.TestCase):
    def test_max_transfers_zero_only_direct(self):
        graph, matcher, tmp = _make_multihop_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            max_transfers=0)
        resp = csa_search(graph, req, matcher)
        self.assertTrue(all(r.train_transfers == 0 for r in resp.routes),
                        "max_transfers=0 时只应有直达")
        self.assertGreaterEqual(len(resp.routes), 1)

    def test_max_transfers_two_allows_two_hop(self):
        graph, matcher, tmp = _make_multihop_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            max_transfers=2)
        resp = csa_search(graph, req, matcher)
        self.assertTrue(all(r.train_transfers <= 2 for r in resp.routes))
        self.assertTrue(any(r.train_transfers == 2 for r in resp.routes))


class BufferBoundaryTest(unittest.TestCase):
    def test_exact_buffer_minutes_ok(self):
        graph, matcher, tmp = _make_buffer_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            same_station_transfer_minutes=15)
        resp = csa_search(graph, req, matcher)
        self.assertGreaterEqual(len(resp.routes), 1, "恰好 15 分钟缓冲应可行")

    def test_buffer_one_minute_short_forces_next_day(self):
        """16 分钟缓冲差 1 分钟：当天 09:15 的 T2 换不上，
        但算法应正确找到跨夜路线（等 24h 乘次日 T2）——真实铁路语义。"""
        graph, matcher, tmp = _make_buffer_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            same_station_transfer_minutes=16)
        resp = csa_search(graph, req, matcher)
        self.assertEqual(len(resp.routes), 1, "应仅剩跨夜路线")
        r = resp.routes[0]
        self.assertEqual(r.total_minutes, 1560,
                         "应只能乘次日 T2（跨夜等待），而非当天 09:15 的 T2")
        self.assertNotEqual(r.total_minutes, 120, "当天换乘应被缓冲阻止")


class TargetTransitTest(unittest.TestCase):
    def test_transit_via_target_station(self):
        graph, matcher, tmp = _make_target_transit_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲城", to_query="乙城", match_mode="fuzzy")
        resp = csa_search(graph, req, matcher)
        self.assertEqual(set(resp.target_stations), {"乙站", "乙西"})
        dests = {r.actual_destination for r in resp.routes}
        self.assertIn("乙站", dests, "应直达乙站（0 转）")
        self.assertIn("乙西", dests, "应能到达乙西（经乙站中转或同城地面）")
        # 到乙西的路线：火车 1 转（T2）或 0 转 + 1 地面（footpath）
        to_west = [r for r in resp.routes if r.actual_destination == "乙西"]
        self.assertTrue(any(r.train_transfers == 1 for r in to_west),
                        "应存在经乙站换乘 T2 到达乙西的 1 转路线")


class RoundLabelInsertTest(unittest.TestCase):
    def _mk_label(self, arrive, code, first_dep, rail=100, xfers=0):
        return Label(station=1, arrive=arrive, train_code=code, first_dep=first_dep,
                     rail_distance=rail, train_xfers=xfers, inter_xfers=0,
                     inter_minutes=0)

    def test_no_cross_train_dominance(self):
        # 轮内不同车次共存：T2 更早更短也不支配 T1（跨车次不支配）
        cur: dict = {}
        ca: dict = {}
        self.assertIsNotNone(_insert_round_label(cur, 1, self._mk_label(540, "T1", 480, 100), 8, ca, False))
        self.assertIsNotNone(_insert_round_label(cur, 1, self._mk_label(525, "T2", 510, 50), 8, ca, False))
        self.assertEqual(len(cur[1]), 2, "不同车次标签应共存")

    def test_same_train_earlier_wins(self):
        cur: dict = {}
        ca: dict = {}
        _insert_round_label(cur, 1, self._mk_label(540, "T1", 480), 8, ca, False)
        # 同车次更晚到达被拒绝
        self.assertIsNone(_insert_round_label(cur, 1, self._mk_label(600, "T1", 480), 8, ca, False))
        self.assertEqual(len(cur[1]), 1)

    def test_same_train_cross_day_coexists(self):
        cur: dict = {}
        ca: dict = {}
        _insert_round_label(cur, 1, self._mk_label(540, "T1", 480), 8, ca, False)
        # 同车次跨日（差 1440 分钟）允许共存（次日班次独立成线）
        self.assertIsNotNone(_insert_round_label(cur, 1, self._mk_label(1980, "T1", 1920), 8, ca, False))
        self.assertEqual(len(cur[1]), 2)

    def test_capacity_truncation_keeps_diversity(self):
        cur: dict = {}
        for i in range(10):
            self.assertIsNotNone(
                _insert_round_label(cur, 1, self._mk_label(500 + i, f"T{i}", 400 + i, 100), 4, {}, False))
        self.assertLessEqual(len(cur[1]), 4, "超过上限应截断")
        codes = {lb.train_code for lb in cur[1]}
        self.assertEqual(len(codes), len(cur[1]), "截断应保留不同车次多样性")


class TimeWindowTest(unittest.TestCase):
    def test_depart_after_filters_direct(self):
        graph, matcher, tmp = _make_multihop_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            earliest_depart=9 * 60)
        resp = csa_search(graph, req, matcher)
        for route in resp.routes:
            self.assertGreaterEqual(route.first_departure, 9 * 60)

    def test_arrive_before_filters(self):
        graph, matcher, tmp = _make_multihop_fixture()
        self.addCleanup(tmp.cleanup)
        req = SearchRequest(from_query="甲站", to_query="乙站", match_mode="exact",
                            latest_arrive=12 * 60)
        resp = csa_search(graph, req, matcher)
        for route in resp.routes:
            self.assertLessEqual(route.final_arrival, 12 * 60)
        # T4（13:30 到）被排除
        self.assertFalse(any(r.total_minutes == 300 for r in resp.routes))


if __name__ == "__main__":
    unittest.main()
