import unittest
from pathlib import Path

from tests.fixtures import build_fixture_graph, write_fixture_files


class RailwayGraphLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.trains = {
            "T1": [
                {"name": "甲站", "depart": "08:00", "distance": 0},
                {"name": "乙站", "arrive": "09:00", "distance": 100},
            ]
        }
        self.stations = [
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
        ]

    def test_build_twice_does_not_duplicate_data(self):
        graph, matcher, tmp = build_fixture_graph(self.trains, self.stations)
        self.addCleanup(tmp.cleanup)
        first = (
            graph.station_count,
            graph.edge_count,
            len(graph.sorted_connections),
            graph.transfer_count,
        )

        csv_path, station_path = write_fixture_files(Path(tmp.name), self.trains, self.stations)
        graph.build(csv_path, station_path)
        second = (
            graph.station_count,
            graph.edge_count,
            len(graph.sorted_connections),
            graph.transfer_count,
        )

        self.assertEqual(first, second)

    def test_build_clears_distance_cache(self):
        graph, matcher, tmp = build_fixture_graph(self.trains, self.stations)
        self.addCleanup(tmp.cleanup)
        target = graph.station_to_idx["乙站"]

        distances = graph.get_reverse_distances(target)
        self.assertEqual(distances[graph.station_to_idx["甲站"]], 100)
        self.assertIn(target, graph.distance_cache)

        csv_path, station_path = write_fixture_files(Path(tmp.name), self.trains, self.stations)
        graph.build(csv_path, station_path)
        self.assertEqual(graph.distance_cache, {})


if __name__ == "__main__":
    unittest.main()
