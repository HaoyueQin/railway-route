import unittest

from tests.fixtures import build_fixture_graph


class SmokeTest(unittest.TestCase):
    def test_fixture_builds_one_connection(self):
        graph, matcher, tmp = build_fixture_graph(
            {
                "T1": [
                    {"name": "甲站", "depart": "08:00", "distance": 0},
                    {"name": "乙站", "arrive": "09:00", "distance": 100},
                ]
            },
            [
                {
                    "short": "jia",
                    "name": "甲站",
                    "telecode": "JAA",
                    "pinyin": "jiazhan",
                    "city_code": "001",
                    "city_name": "甲城",
                },
                {
                    "short": "yi",
                    "name": "乙站",
                    "telecode": "YAA",
                    "pinyin": "yizhan",
                    "city_code": "002",
                    "city_name": "乙城",
                },
            ],
        )
        self.addCleanup(tmp.cleanup)

        self.assertEqual(graph.station_count, 2)
        self.assertEqual(graph.edge_count, 1)
        self.assertEqual(len(graph.sorted_connections), 2)
        self.assertEqual(len(matcher.all_stations), 2)


if __name__ == "__main__":
    unittest.main()
