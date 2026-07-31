import unittest

from src.models import (
    SEARCH_PROFILES,
    InterstationTransferSegment,
    RouteResult,
    SearchRequest,
    TrainSegment,
    format_absolute_minutes,
    route_key,
)


class SearchModelTest(unittest.TestCase):
    def test_request_defaults(self):
        request = SearchRequest(from_query="甲站", to_query="乙站")
        self.assertEqual(request.match_mode, "fuzzy")
        self.assertEqual(request.search_profile, "balanced")
        self.assertEqual(request.same_station_transfer_minutes, 15)
        self.assertEqual(request.interstation_transfer_minutes, 60)
        self.assertEqual(request.max_transfers, 3)

    def test_search_profiles_are_ordered(self):
        self.assertLess(
            SEARCH_PROFILES["fast"].max_states_per_station,
            SEARCH_PROFILES["balanced"].max_states_per_station,
        )
        self.assertLess(
            SEARCH_PROFILES["balanced"].max_states_per_station,
            SEARCH_PROFILES["thorough"].max_states_per_station,
        )
        # complete 每轮每站上限最大（受 state_limit 兜底）
        self.assertGreater(SEARCH_PROFILES["complete"].max_states_per_station,
                           SEARCH_PROFILES["thorough"].max_states_per_station)

    def test_chinese_day_offset_display(self):
        self.assertEqual(format_absolute_minutes(405)["display"], "06:45")
        self.assertEqual(format_absolute_minutes(1845)["display"], "次日 06:45")
        self.assertEqual(format_absolute_minutes(3285)["display"], "第3日 06:45")

    def test_route_key_distinguishes_station_time_and_segment_type(self):
        train_a = TrainSegment("T1", "甲站", "乙站", 480, 540, 60, 100)
        train_b = TrainSegment("T1", "甲东", "乙站", 480, 540, 60, 100)
        train_c = TrainSegment("T1", "甲站", "乙站", 1920, 1980, 60, 100)
        ground = InterstationTransferSegment("甲站", "乙站", 480, 540, 60, "001", "甲城")

        self.assertNotEqual(route_key((train_a,)), route_key((train_b,)))
        self.assertNotEqual(route_key((train_a,)), route_key((train_c,)))
        self.assertNotEqual(route_key((train_a,)), route_key((ground,)))
        self.assertEqual(route_key((train_a,)), route_key((train_a,)))

    def test_typed_route_serialization_preserves_segment_types(self):
        from src.main import typed_route_to_dict

        train = TrainSegment("T1", "甲站", "乙站", 1380, 1500, 120, 100)
        ground = InterstationTransferSegment("乙站", "乙东", 1500, 1560, 60, "002", "乙城")
        route = RouteResult(
            segments=(train, ground),
            actual_origin="甲站",
            actual_destination="乙东",
            first_departure=1380,
            final_arrival=1560,
            total_minutes=180,
            rail_distance=100,
            train_transfers=0,
            interstation_transfers=1,
            interstation_minutes=60,
        )

        data = typed_route_to_dict(route, 0.5)
        self.assertEqual(data["segments"][0]["type"], "train")
        self.assertEqual(data["segments"][1]["type"], "interstation")
        self.assertEqual(data["segments"][0]["arrive"]["display"], "次日 01:00")
        self.assertEqual(data["interstation_transfers"], 1)

    def test_typed_route_serialization_full_field_contract(self):
        """契约测试：typed_route_to_dict 输出必须包含前端 renderList/render 依赖的全部字段。"""
        from src.main import typed_route_to_dict

        train = TrainSegment("G1", "甲站", "乙站", 480, 600, 120, 300)
        ground = InterstationTransferSegment("乙站", "乙东", 600, 660, 60, "002", "乙城")
        route = RouteResult(
            segments=(train, ground),
            actual_origin="甲站",
            actual_destination="乙东",
            first_departure=480,
            final_arrival=660,
            total_minutes=180,
            rail_distance=300,
            train_transfers=0,
            interstation_transfers=1,
            interstation_minutes=60,
            transfer_cities=("乙城",),
        )
        data = typed_route_to_dict(route, 0.75)

        # 顶层字段
        self.assertEqual(data["score"], 0.75)
        self.assertEqual(data["actual_origin"], "甲站")
        self.assertEqual(data["actual_destination"], "乙东")
        self.assertEqual(data["total_minutes"], 180)
        self.assertEqual(data["rail_distance"], 300)
        self.assertEqual(data["train_transfers"], 0)
        self.assertEqual(data["interstation_transfers"], 1)
        self.assertEqual(data["interstation_minutes"], 60)
        self.assertEqual(list(data["transfer_cities"]), ["乙城"])

        # 时间字段统一为 {minutes, time, day_offset, display} 结构
        for key in ("first_departure", "final_arrival"):
            t = data[key]
            self.assertEqual(
                sorted(t.keys()), ["day_offset", "display", "minutes", "time"],
                f"{key} 缺少统一时间结构",
            )
        self.assertEqual(data["first_departure"]["minutes"], 480)
        self.assertEqual(data["final_arrival"]["minutes"], 660)

        # train 段字段
        t = data["segments"][0]
        self.assertEqual(t["type"], "train")
        for field in ("train_code", "from_station", "to_station", "depart", "arrive", "travel_minutes", "distance"):
            self.assertIn(field, t, f"train 段缺少字段 {field}")
        self.assertEqual(t["depart"]["display"], "08:00")
        self.assertEqual(t["arrive"]["display"], "10:00")
        self.assertEqual(t["travel_minutes"], 120)
        self.assertEqual(t["distance"], 300)

        # interstation 段字段
        g = data["segments"][1]
        self.assertEqual(g["type"], "interstation")
        for field in ("from_station", "to_station", "start", "end", "transfer_minutes", "city_code", "city_name", "estimate_source"):
            self.assertIn(field, g, f"interstation 段缺少字段 {field}")
        self.assertEqual(g["start"]["display"], "10:00")
        self.assertEqual(g["end"]["display"], "11:00")
        self.assertEqual(g["transfer_minutes"], 60)
        self.assertEqual(g["city_code"], "002")
        self.assertEqual(g["city_name"], "乙城")

        # transfer_cities 缺省时序列化为空列表
        bare = RouteResult(
            segments=(train,),
            actual_origin="甲站",
            actual_destination="乙站",
            first_departure=480,
            final_arrival=600,
            total_minutes=120,
            rail_distance=300,
            train_transfers=0,
            interstation_transfers=0,
            interstation_minutes=0,
        )
        self.assertEqual(list(typed_route_to_dict(bare)["transfer_cities"]), [])


if __name__ == "__main__":
    unittest.main()
