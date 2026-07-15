import csv
import tempfile
from pathlib import Path

from src.graph import RailwayGraph
from src.matcher import build_matcher


def write_fixture_files(root: Path, trains: dict, stations: list[dict]) -> tuple[str, str]:
    """写出生产代码可直接读取的最小时刻表和站名元数据。"""
    csv_path = root / "timetable.csv"
    station_path = root / "station_name.js"

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["车次", "序号", "站名", "到达", "发车", "停留分", "里程km", "站台"])
        for code, stops in trains.items():
            for seq, stop in enumerate(stops, 1):
                writer.writerow([
                    code,
                    seq,
                    stop["name"],
                    stop.get("arrive", ""),
                    stop.get("depart", ""),
                    stop.get("stop", 0),
                    stop.get("distance", 0),
                    stop.get("platform", ""),
                ])

    entries = []
    for index, station in enumerate(stations):
        entries.append(
            "@{short}|{name}|{telecode}|{pinyin}|{short}|{index}|{city_code}|{city_name}|||".format(
                index=index,
                **station,
            )
        )
    station_path.write_text("var station_names ='" + "".join(entries) + "';", encoding="utf-8")
    return str(csv_path), str(station_path)


def build_fixture_graph(trains: dict, stations: list[dict]):
    """构建临时 RailwayGraph 和当前版本 matcher，调用方负责 tmp.cleanup()。"""
    tmp = tempfile.TemporaryDirectory()
    csv_path, station_path = write_fixture_files(Path(tmp.name), trains, stations)
    graph = RailwayGraph()
    graph.build(csv_path, station_path)
    matcher = build_matcher(graph, station_path)
    return graph, matcher, tmp
