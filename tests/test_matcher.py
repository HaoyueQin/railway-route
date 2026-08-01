import unittest

from src.matcher import resolve_city_code, resolve_station_set
from tests.fixtures import build_fixture_graph


class MatcherStationSetTest(unittest.TestCase):
    def setUp(self):
        self.graph, self.matcher, self.tmp = build_fixture_graph(
            {
                "T1": [
                    {"name": "甲站", "depart": "08:00", "distance": 0},
                    {"name": "乙站", "arrive": "09:00", "distance": 100},
                ],
                "T2": [
                    {"name": "甲东", "depart": "10:00", "distance": 0},
                    {"name": "乙站", "arrive": "11:00", "distance": 100},
                ],
            },
            [
                {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan", "city_code": "001", "city_name": "甲城"},
                {"short": "jiadong", "name": "甲东", "telecode": "JAD", "pinyin": "jiadong", "city_code": "001", "city_name": "甲城"},
                {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan", "city_code": "002", "city_name": "乙城"},
            ],
        )
        self.addCleanup(self.tmp.cleanup)

    def test_exact_mode_returns_one_station(self):
        self.assertEqual(resolve_station_set("甲站", "exact", self.graph, self.matcher), ["甲站"])

    def test_fuzzy_mode_sparse_station_expands_to_city(self):
        """新语义（贴近生活）：站名精确但出发班次稀疏（fixture 甲站仅 1 班 < 25）
        时视为区级可用性，扩散到所属市全部站（怀柔/广阳同理）。"""
        self.assertEqual(
            set(resolve_station_set("甲站", "fuzzy", self.graph, self.matcher)),
            {"甲站", "甲东"},
        )

    def test_fuzzy_mode_expands_city_name_to_all_stations(self):
        """城市名（甲城）→ 全市全部站。"""
        self.assertEqual(
            set(resolve_station_set("甲城", "fuzzy", self.graph, self.matcher)),
            {"甲站", "甲东"},
        )

    def test_fuzzy_mode_no_same_name_station_expands_to_city(self):
        """无同名站的地名（区/镇级，如通州→北京）→ 归并到所属城市全部站。"""
        self.assertEqual(
            set(resolve_station_set("甲东", "fuzzy", self.graph, self.matcher)),
            {"甲站", "甲东"},
        )
        self.assertEqual(
            set(resolve_station_set("甲城", "fuzzy", self.graph, self.matcher)),
            {"甲站", "甲东"},
        )

    def test_fuzzy_mode_accepts_city_name(self):
        self.assertEqual(
            set(resolve_station_set("甲城", "fuzzy", self.graph, self.matcher)),
            {"甲站", "甲东"},
        )

    def test_station_resolves_to_city_code(self):
        self.assertEqual(resolve_city_code("甲东", self.graph, self.matcher), "001")
        station_idx = self.graph.station_to_idx["甲东"]
        self.assertEqual(self.graph.station_to_city_code[station_idx], "001")
        self.assertEqual(self.graph.city_code_to_name["001"], "甲城")

    def test_city_prefix_station_expands_to_all(self):
        """站名以所属城市名开头（北京南/甲城南）→ 城市前缀扩散 → 所属市全部站。

        回归：北京南 fuzzy 曾只返回单站，导致北京丰台→新县直达方案查不到。"""
        graph, matcher, tmp = build_fixture_graph(
            {
                "T1": [{"name": "甲城南", "depart": "08:00", "distance": 0},
                       {"name": "乙站", "arrive": "09:00", "distance": 100}],
                "T2": [{"name": "甲站", "depart": "10:00", "distance": 0},
                       {"name": "乙站", "arrive": "11:00", "distance": 100}],
            },
            [
                {"short": "jcn", "name": "甲城南", "telecode": "JCN", "pinyin": "jiachengnan", "city_code": "001", "city_name": "甲城"},
                {"short": "jz", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan", "city_code": "001", "city_name": "甲城"},
                {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan", "city_code": "002", "city_name": "乙城"},
            ],
        )
        self.addCleanup(tmp.cleanup)
        self.assertEqual(
            set(resolve_station_set("甲城南", "fuzzy", graph, matcher)),
            {"甲城南", "甲站"},
        )

    def test_direction_twin_expands_to_city(self):
        """地名同名站规则：站名去方位后缀后的地名与组内其他站同名
        （曲阜东→曲阜，组名"济宁"非前缀）→ 同城扩散。"""
        graph, matcher, tmp = build_fixture_graph(
            {
                "T1": [{"name": "曲阜东", "depart": "08:00", "distance": 0},
                       {"name": "乙站", "arrive": "09:00", "distance": 100}],
                "T2": [{"name": "曲阜", "depart": "10:00", "distance": 0},
                       {"name": "乙站", "arrive": "11:00", "distance": 100}],
            },
            [
                {"short": "qfd", "name": "曲阜东", "telecode": "QFD", "pinyin": "qufudong", "city_code": "001", "city_name": "济宁"},
                {"short": "qf", "name": "曲阜", "telecode": "QF", "pinyin": "qufu", "city_code": "001", "city_name": "济宁"},
                {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan", "city_code": "002", "city_name": "乙城"},
            ],
        )
        self.addCleanup(tmp.cleanup)
        self.assertEqual(
            set(resolve_station_set("曲阜东", "fuzzy", graph, matcher)),
            {"曲阜东", "曲阜"},
        )

    def test_county_station_stays_single_when_rich(self):
        """班次充足的县级独立站（新县/燕郊）不扩散：即使组名非前缀。"""
        trains = {f"T{i}": [
            {"name": "新县", "depart": f"{8 + i % 12:02d}:00", "distance": 0},
            {"name": "乙站", "arrive": "09:00", "distance": 100},
        ] for i in range(1, 26)}  # 25 班 ≥ MIN_STATION_TRAINS_FOR_SINGLE
        graph, matcher, tmp = build_fixture_graph(
            trains,
            [
                {"short": "xx", "name": "新县", "telecode": "XXN", "pinyin": "xinxian", "city_code": "001", "city_name": "信阳"},
                {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan", "city_code": "002", "city_name": "乙城"},
            ],
        )
        self.addCleanup(tmp.cleanup)
        self.assertEqual(resolve_station_set("新县", "fuzzy", graph, matcher), ["新县"])

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_station_set("甲站", "wide", self.graph, self.matcher)


if __name__ == "__main__":
    unittest.main()
