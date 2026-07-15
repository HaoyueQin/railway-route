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

    def test_fuzzy_mode_expands_explicit_station_to_city(self):
        self.assertEqual(
            set(resolve_station_set("甲站", "fuzzy", self.graph, self.matcher)),
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

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_station_set("甲站", "wide", self.graph, self.matcher)


if __name__ == "__main__":
    unittest.main()
