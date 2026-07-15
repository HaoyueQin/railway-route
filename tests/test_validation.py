import unittest

from src.validation import (
    RequestValidationError,
    build_search_request,
    parse_bounded_int,
    parse_time,
)


class ValidationTest(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(parse_time("08:05"), 485)
        self.assertEqual(parse_time("", default=123), 123)
        for invalid in ("8:05", "24:00", "12:60", "abc"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(RequestValidationError):
                    parse_time(invalid)

    def test_parse_bounded_int(self):
        self.assertEqual(parse_bounded_int("15", "same_transfer", 0, 240, 10), 15)
        self.assertEqual(parse_bounded_int("", "same_transfer", 0, 240, 10), 10)
        for invalid in ("x", "-1", "241"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(RequestValidationError):
                    parse_bounded_int(invalid, "same_transfer", 0, 240, 10)

    def test_build_request_defaults(self):
        request = build_search_request({"from": "甲站", "to": "乙站"})
        self.assertEqual(request.match_mode, "fuzzy")
        self.assertEqual(request.search_profile, "balanced")
        self.assertEqual(request.same_station_transfer_minutes, 15)
        self.assertEqual(request.interstation_transfer_minutes, 60)
        self.assertEqual(request.max_transfers, 3)

    def test_build_request_custom_values(self):
        request = build_search_request({
            "from": "甲站",
            "to": "乙站",
            "match_mode": "exact",
            "search_profile": "complete",
            "dep_after": "08:00",
            "dep_before": "12:00",
            "arr_after": "13:00",
            "arr_before": "23:00",
            "same_transfer": "20",
            "inter_transfer": "90",
            "max_transfers": "4",
            "timeout": "120",
        })
        self.assertEqual(request.match_mode, "exact")
        self.assertEqual(request.search_profile, "complete")
        self.assertEqual(request.earliest_depart, 480)
        self.assertEqual(request.latest_depart, 720)
        self.assertEqual(request.earliest_arrive, 780)
        self.assertEqual(request.latest_arrive, 1380)
        self.assertEqual(request.timeout_seconds, 120)

    def test_invalid_modes_and_ranges(self):
        cases = [
            ({"from": "甲", "to": "乙", "match_mode": "wide"}, "INVALID_MATCH_MODE"),
            ({"from": "甲", "to": "乙", "search_profile": "huge"}, "INVALID_SEARCH_PROFILE"),
            ({"from": "甲", "to": "乙", "inter_transfer": "1441"}, "INVALID_INTER_TRANSFER"),
            ({"from": "甲", "to": "乙", "max_transfers": "11"}, "INVALID_MAX_TRANSFERS"),
            ({"from": "甲", "to": "乙", "timeout": "0"}, "INVALID_TIMEOUT"),
            ({"from": "", "to": "乙"}, "MISSING_STATION"),
        ]
        for mapping, code in cases:
            with self.subTest(mapping=mapping):
                with self.assertRaises(RequestValidationError) as caught:
                    build_search_request(mapping)
                self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
