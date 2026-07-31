# Unified Multi-Station Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-station CSA MVP with a tested multi-source/multi-target search that supports exact/fuzzy station scope, explicit interstation footpaths, city-level transfer constraints, four search profiles, complete-result metadata, robust deduplication, cross-day display, validated CLI/API inputs, and accurate GUI output.

**Architecture:** Add focused request/result model and validation modules around a rewritten CSA core. The graph owns all derived indexes and caches; the matcher resolves queries to station sets or cities; the search scans train connections once, relaxes independent footpaths, reconstructs typed path segments, and returns result metadata. Every task uses standard-library `unittest`, updates `README.md` and `HANDOVER.md` immediately, and ends with a diff check. No task commits automatically.

**Tech Stack:** Python 3.10+, standard library only (`dataclasses`, `unittest`, `tempfile`, `http.server`, `urllib`, `time`, `heapq`, `bisect`).

## Global Constraints

- Default match mode is `fuzzy`; fuzzy expands any resolved station to all valid graph stations in its city.
- Exact mode uses one resolved station only.
- Real train travel and ground interstation transfer are separate segment types and may coexist as distinct candidates.
- Ground interstation transfer uses the user-supplied default, initially 60 minutes; no coordinate formula is implemented in this iteration.
- `transfer_at` is a city-level “at least one transfer event” constraint.
- Search profiles are `fast`, `balanced`, `thorough`, and `complete`.
- `complete` is bounded by the two-day horizon, time window, maximum transfers, timeout, and global state safety limit; interrupted output must report `complete=false`.
- Concrete operating dates are not implemented in this iteration.
- Use only Python standard-library dependencies.
- Do not run `git add`, `git commit`, or `git push` automatically.
- After each task: run focused tests, update `README.md` and `HANDOVER.md`, run `git diff --check`, inspect the task diff, then sign off the task.

---

## File Structure After Implementation

### New files

- `src/models.py` — immutable search request, typed path segments, route result, search metadata, profile settings.
- `src/validation.py` — shared parsing and range validation for CLI and HTTP API.
- `tests/__init__.py` — test package marker.
- `tests/fixtures.py` — temporary timetable/station metadata factory for deterministic networks.
- `tests/test_graph.py` — repeatable build, city indexes, and graph-owned distance-cache tests.
- `tests/test_matcher.py` — exact/fuzzy station-set and city resolution tests.
- `tests/test_models.py` — segment keys, cross-day formatting, request defaults.
- `tests/test_csa_multistation.py` — multi-source/multi-target train search tests.
- `tests/test_csa_footpaths.py` — ground interstation transfer and train/ground coexistence tests.
- `tests/test_csa_constraints.py` — transfer city, profiles, completion metadata, timeout/state-limit, and dedup tests.
- `tests/test_validation.py` — CLI/API value parsing tests.
- `tests/test_main_api.py` — HTTP handler request/response contract tests.

### Modified files

- `src/graph.py` — resettable build, station-to-city indexes, graph-owned distance cache, footpath lookup.
- `src/matcher.py` — structured matcher data, station-set resolution, city resolution.
- `src/csa.py` — multi-source/multi-target typed-segment search.
- `src/main.py` — validation, scoring, serialization, CLI arguments, API status codes, GUI controls/rendering.
- `README.md` — update after every delivered task.
- `HANDOVER.md` — update after every delivered task, including recovery point and real verification output.

---

### Task 1: Establish Deterministic Test Infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/fixtures.py`
- Create: `tests/test_smoke.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Produces: `build_fixture_graph(trains, stations) -> tuple[RailwayGraph, object, TemporaryDirectory]`; Task 1 preserves the current matcher tuple, and Task 3 migrates it to `MatcherData`.
- Produces: `write_fixture_files(root, trains, stations) -> tuple[str, str]`
- Consumes: current `RailwayGraph.build()` and `build_matcher()`.

- [x] **Step 1: Create the test package and fixture writer**

Use a compact dictionary input and write the same CSV/station metadata format consumed by production code:

```python
# tests/fixtures.py
import csv
import tempfile
from pathlib import Path

from src.graph import RailwayGraph
from src.matcher import build_matcher


def write_fixture_files(root: Path, trains: dict, stations: list[dict]):
    csv_path = root / "timetable.csv"
    station_path = root / "station_name.js"

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["车次", "序号", "站名", "到达", "发车", "停留分", "里程km", "站台"])
        for code, stops in trains.items():
            for seq, stop in enumerate(stops, 1):
                writer.writerow([
                    code, seq, stop["name"], stop.get("arrive", ""),
                    stop.get("depart", ""), stop.get("stop", 0),
                    stop.get("distance", 0), stop.get("platform", ""),
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
    tmp = tempfile.TemporaryDirectory()
    csv_path, station_path = write_fixture_files(Path(tmp.name), trains, stations)
    graph = RailwayGraph()
    graph.build(csv_path, station_path)
    matcher = build_matcher(graph, station_path)
    return graph, matcher, tmp
```

- [x] **Step 2: Write a baseline smoke test**

```python
# tests/test_smoke.py
import unittest
from tests.fixtures import build_fixture_graph


class SmokeTest(unittest.TestCase):
    def test_fixture_builds_one_connection(self):
        graph, matcher, tmp = build_fixture_graph(
            {"T1": [
                {"name": "甲站", "depart": "08:00", "distance": 0},
                {"name": "乙站", "arrive": "09:00", "distance": 100},
            ]},
            [
                {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan", "city_code": "001", "city_name": "甲城"},
                {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan", "city_code": "002", "city_name": "乙城"},
            ],
        )
        self.addCleanup(tmp.cleanup)
        self.assertEqual(graph.station_count, 2)
        self.assertEqual(len(graph.sorted_connections), 2)
        self.assertEqual(len(matcher[0]), 2)
```

- [x] **Step 3: Run the test**

Run:

```bash
python -m unittest tests.test_smoke -v
```

Expected: one passing test.

- [x] **Step 4: Document the test harness**

Update `README.md` verification section and `HANDOVER.md` recovery point to state that deterministic temporary fixture tests now exist and Task 1 is complete.

- [x] **Step 5: Inspect the task diff**

```bash
git diff --check
git diff -- tests README.md
```

Review `HANDOVER.md` directly because it is ignored by Git.

---

### Task 2: Make RailwayGraph Rebuildable and Own Its Distance Cache

**Files:**
- Modify: `src/graph.py`
- Modify: `src/csa.py`
- Create: `tests/test_graph.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Produces: `RailwayGraph.reset() -> None`
- Produces: `RailwayGraph.distance_cache: dict[int, dict[int, float]]`
- Produces: `RailwayGraph.get_reverse_distances(target: int) -> dict[int, float]`
- Consumes: `reverse_edges` built by `RailwayGraph.build()`.

- [x] **Step 1: Write failing rebuild and cache tests**

```python
# tests/test_graph.py
import unittest
from tests.fixtures import build_fixture_graph, write_fixture_files
from pathlib import Path


class RailwayGraphLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.trains = {"T1": [
            {"name": "甲站", "depart": "08:00", "distance": 0},
            {"name": "乙站", "arrive": "09:00", "distance": 100},
        ]}
        self.stations = [
            {"short": "jia", "name": "甲站", "telecode": "JAA", "pinyin": "jiazhan", "city_code": "001", "city_name": "甲城"},
            {"short": "yi", "name": "乙站", "telecode": "YAA", "pinyin": "yizhan", "city_code": "002", "city_name": "乙城"},
        ]

    def test_build_twice_does_not_duplicate_data(self):
        graph, matcher, tmp = build_fixture_graph(self.trains, self.stations)
        self.addCleanup(tmp.cleanup)
        first = (graph.station_count, graph.edge_count, len(graph.sorted_connections))
        csv_path, station_path = write_fixture_files(Path(tmp.name), self.trains, self.stations)
        graph.build(csv_path, station_path)
        second = (graph.station_count, graph.edge_count, len(graph.sorted_connections))
        self.assertEqual(first, second)

    def test_build_clears_distance_cache(self):
        graph, matcher, tmp = build_fixture_graph(self.trains, self.stations)
        self.addCleanup(tmp.cleanup)
        target = graph.station_to_idx["乙站"]
        graph.get_reverse_distances(target)
        self.assertIn(target, graph.distance_cache)
        csv_path, station_path = write_fixture_files(Path(tmp.name), self.trains, self.stations)
        graph.build(csv_path, station_path)
        self.assertEqual(graph.distance_cache, {})
```

- [x] **Step 2: Run tests and confirm failure**

```bash
python -m unittest tests.test_graph -v
```

Expected: failures because `reset`, `distance_cache`, or `get_reverse_distances` do not exist and repeated build duplicates structures.

- [x] **Step 3: Implement graph reset and graph-owned reverse-distance cache**

Move every mutable graph container initialization into `reset()`, call it from `__init__()` and at the start of `build()`. Implement reverse Dijkstra in `get_reverse_distances()` using `heapq`; cache by target on the graph instance.

Remove module-level `functools.lru_cache` from `src/csa.py`; replace `_get_heuristic(graph, target)` with `graph.get_reverse_distances(target)`.

- [x] **Step 4: Run focused and smoke tests**

```bash
python -m unittest tests.test_graph tests.test_smoke -v
python -m py_compile src/graph.py src/csa.py
```

Expected: all pass.

- [x] **Step 5: Update documents immediately**

Update `README.md` lifecycle note and `HANDOVER.md` current recovery point: repeat build and cache lifetime are fixed and verified. Remove the old open-risk entries.

- [x] **Step 6: Inspect the task diff**

```bash
git diff --check
git diff -- src/graph.py src/csa.py tests/test_graph.py README.md
```

---

### Task 3: Add Structured Matcher Data and Exact/Fuzzy Station-Set Resolution

**Files:**
- Modify: `src/matcher.py`
- Modify: `src/graph.py`
- Create: `tests/test_matcher.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Produces: `MatcherData` dataclass.
- Produces: `resolve_station_set(query: str, mode: str, graph: RailwayGraph, matcher: MatcherData) -> list[str]`
- Produces: `resolve_city_code(query: str, graph: RailwayGraph, matcher: MatcherData) -> str`
- Produces: `graph.station_to_city_code: dict[int, str]`
- Produces: `graph.city_code_to_name: dict[str, str]`.

- [x] **Step 1: Write failing matcher tests**

Use a fixture where “甲城” has “甲站” and “甲东”, and “乙城” has “乙站”. Assert:

```python
self.assertEqual(resolve_station_set("甲站", "exact", graph, matcher), ["甲站"])
self.assertEqual(set(resolve_station_set("甲站", "fuzzy", graph, matcher)), {"甲站", "甲东"})
self.assertEqual(set(resolve_station_set("甲城", "fuzzy", graph, matcher)), {"甲站", "甲东"})
self.assertEqual(resolve_city_code("甲东", graph, matcher), "001")
```

Also assert invalid mode raises `ValueError`.

- [x] **Step 2: Confirm failures**

```bash
python -m unittest tests.test_matcher -v
```

- [x] **Step 3: Implement structured matcher indexes**

Introduce:

```python
@dataclass
class MatcherData:
    all_stations: list[str]
    city_to_stations: dict[str, list[str]]
    telecode_to_name: dict[str, str]
    pinyin_to_names: dict[str, list[str]]
    station_to_city_code: dict[str, str]
    city_name_to_code: dict[str, str]
    city_code_to_name: dict[str, str]
```

Keep compatibility by updating all production callers in the same task; do not maintain both tuple and dataclass APIs indefinitely.

Exact mode uses `resolve_single`; fuzzy mode resolves a representative station/city, finds its city code, and returns all graph-valid stations for that city.

- [x] **Step 4: Run tests**

```bash
python -m unittest tests.test_matcher tests.test_smoke -v
python -m py_compile src/matcher.py src/main.py
```

- [x] **Step 5: Update docs**

Document that the resolver capability exists, but the current CSA entry point is not yet multi-source until Task 6. Mark this explicit intermediate state in HANDOVER.

- [x] **Step 6: Diff check**

```bash
git diff --check
git diff -- src/matcher.py src/graph.py tests/test_matcher.py README.md
```

---

### Task 4: Introduce Search Request, Typed Segments, Results, Profiles, and Time Formatting

**Files:**
- Create: `src/models.py`
- Create: `tests/test_models.py`
- Modify: `src/main.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Produces: `SearchRequest`, `TrainSegment`, `InterstationTransferSegment`, `RouteResult`, `SearchMetadata`, `SearchResponse`, `SearchProfileSettings`.
- Produces: `SEARCH_PROFILES` mapping.
- Produces: `format_absolute_minutes(minutes: int) -> dict[str, int | str]`.
- Produces: `segment_key(segment) -> tuple` and `route_key(route) -> tuple`.

- [x] **Step 1: Write failing model tests**

Test defaults (`fuzzy`, `balanced`, 15, 60, 3), Chinese day-offset rendering, and full segment keys distinguishing different stations/times/types.

```python
self.assertEqual(format_absolute_minutes(405)["display"], "06:45")
self.assertEqual(format_absolute_minutes(1845)["display"], "次日 06:45")
self.assertEqual(format_absolute_minutes(3285)["display"], "第3日 06:45")
```

- [x] **Step 2: Confirm failure**

```bash
python -m unittest tests.test_models -v
```

- [x] **Step 3: Implement minimal immutable models**

Use `@dataclass(frozen=True)` for request and segments. Keep route segment order as `tuple[TrainSegment | InterstationTransferSegment, ...]`.

Define profile settings explicitly, for example:

```python
SEARCH_PROFILES = {
    "fast": SearchProfileSettings(max_states_per_station=8, max_results=30, use_relaxed_dominance=True),
    "balanced": SearchProfileSettings(max_states_per_station=20, max_results=100, use_relaxed_dominance=True),
    "thorough": SearchProfileSettings(max_states_per_station=80, max_results=300, use_relaxed_dominance=False),
    "complete": SearchProfileSettings(max_states_per_station=None, max_results=None, use_relaxed_dominance=False),
}
```

Exact numeric thresholds may be tuned later, but these values must be explicit and tested.

- [x] **Step 4: Add compatibility serialization helpers in main.py**

Do not switch the search core yet. Add helpers capable of serializing the new models and retain old serialization only until the core migration task.

- [x] **Step 5: Run tests**

```bash
python -m unittest tests.test_models -v
python -m py_compile src/models.py src/main.py
```

- [x] **Step 6: Update docs and inspect diff**

Record the new canonical model and profile values in README/HANDOVER.

```bash
git diff --check
git diff -- src/models.py src/main.py tests/test_models.py README.md
```

---

### Task 5: Add Shared Request Validation

**Files:**
- Create: `src/validation.py`
- Create: `tests/test_validation.py`
- Modify: `src/main.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Produces: `RequestValidationError(code: str, message: str)`.
- Produces: `parse_time(value: str, *, default: int | None = None) -> int`.
- Produces: `parse_bounded_int(value, name, minimum, maximum, default) -> int`.
- Produces: `build_search_request(mapping) -> SearchRequest`.

- [ ] **Step 1: Write validation tests**

Cover valid/invalid `HH:MM`, modes, profiles, negative/huge transfer times, max transfers, and complete timeout.

- [ ] **Step 2: Confirm failures**

```bash
python -m unittest tests.test_validation -v
```

- [ ] **Step 3: Implement shared validation**

Use strict regex/time parsing. Proposed bounds:

- same-station: 0–240 minutes;
- interstation: 1–360 minutes;
- max transfers: 0–6;
- timeout: 1–600 seconds.

Return stable error codes such as `INVALID_TIME`, `INVALID_MATCH_MODE`, `INVALID_SEARCH_PROFILE`, `INVALID_TRANSFER_MINUTES`.

- [ ] **Step 4: Wire CLI argument choices and ranges**

Add `--match-mode`, `--search-profile`, `--max-transfers`, and `--timeout`. Keep default fuzzy/balanced.

- [ ] **Step 5: Run tests and help output**

```bash
python -m unittest tests.test_validation -v
python src/main.py --help
```

- [ ] **Step 6: Update docs and inspect diff**

```bash
git diff --check
git diff -- src/validation.py src/main.py tests/test_validation.py README.md
```

---

### Task 6: Rewrite the Train-Only Core as Multi-Source/Multi-Target CSA

**Files:**
- Rewrite: `src/csa.py`
- Create: `tests/test_csa_multistation.py`
- Modify: `src/main.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Consumes: `SearchRequest`, `MatcherData`, source and target station lists.
- Produces: `search(graph, request, matcher) -> SearchResponse`.
- Produces: train-only typed `RouteResult` before footpaths are added.

- [ ] **Step 1: Write failing multi-source/multi-target tests**

Construct a city A with A1/A2 and city B with B1/B2:

- A1→B1 is slow;
- A2→B2 is fast;
- exact A1/B1 returns the slow route;
- fuzzy A1/B1 expands cities and returns A2→B2 first;
- the algorithm scans the cached connection list once, exposed through metadata.

- [ ] **Step 2: Confirm failures**

```bash
python -m unittest tests.test_csa_multistation -v
```

- [ ] **Step 3: Implement source-set initialization and target-set collection**

Refactor state and reconstruction around typed segments. Preserve same-train continuation and same-station transfer logic. Do not add footpaths yet.

- [ ] **Step 4: Replace production entry points**

CLI/API build `SearchRequest`, resolve source/target sets, and call the new search signature. Keep GUI output functional using typed train segments.

- [ ] **Step 5: Run regression tests**

```bash
python -m unittest tests.test_smoke tests.test_matcher tests.test_models tests.test_validation tests.test_csa_multistation -v
python src/main.py 北京南 上海虹桥 --match-mode exact --max 1
python src/main.py 北京南 上海虹桥 --match-mode fuzzy --max 1
```

- [ ] **Step 6: Update docs immediately**

Mark exact/fuzzy multi-source/multi-target as implemented and verified. Record actual source/target station sets for a representative query.

- [ ] **Step 7: Inspect diff**

```bash
git diff --check
git diff -- src/csa.py src/main.py tests/test_csa_multistation.py README.md
```

---

### Task 7: Implement Independent Interstation Footpaths

**Files:**
- Modify: `src/graph.py`
- Modify: `src/csa.py`
- Create: `tests/test_csa_footpaths.py`
- Modify: `src/main.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Produces: `graph.get_interstation_targets(station_idx) -> tuple[int, ...]`.
- Produces: `InterstationTransferSegment` states and route reconstruction.
- Removes: railway-time-based `get_interstation_transfer_time()` behavior.

- [ ] **Step 1: Write failing footpath tests**

Create city X with X1/X2 and trains:

- A→X1;
- X2→B;
- X1→X2 also has an optional real train.

Assert:

- without footpath, A→B is unavailable unless the city train is used;
- with a 60-minute footpath, A→X1→ground→X2→B exists;
- the real X1→X2 train route also exists;
- the two route keys differ;
- changing interstation minutes changes feasibility;
- no route contains consecutive footpath segments.

- [ ] **Step 2: Confirm failures**

```bash
python -m unittest tests.test_csa_footpaths -v
```

- [ ] **Step 3: Implement footpath relaxation**

Generate one-hop same-city ground states after train arrival. Track `last_segment_kind`, interstation count/minutes, and prevent same-city ground loops.

Do not use train travel time for footpaths. The request’s `interstation_transfer_minutes` is the sole current duration source.

- [ ] **Step 4: Update scoring and serialization**

Add a separate interstation penalty. Serialize ground segments distinctly. Preserve train transfer count semantics.

- [ ] **Step 5: Run tests**

```bash
python -m unittest tests.test_csa_footpaths tests.test_csa_multistation -v
```

- [ ] **Step 6: Update docs immediately**

Mark ground interstation transfer as implemented, explain the temporary uniform-duration model, and retain the future coordinate formula as a planned upgrade.

- [ ] **Step 7: Inspect diff**

```bash
git diff --check
git diff -- src/graph.py src/csa.py src/main.py tests/test_csa_footpaths.py README.md
```

---

### Task 8: Implement City-Level transfer_at Constraint

**Files:**
- Modify: `src/matcher.py`
- Modify: `src/csa.py`
- Create: `tests/test_csa_constraints.py`
- Modify: `src/main.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Consumes: `resolve_city_code()`.
- State field: `matched_transfer_constraint: bool`.
- Result filter: if a city constraint exists, only matched routes are returned.

- [ ] **Step 1: Write failing constraint tests**

Cover:

- same-station train change in the city satisfies;
- footpath followed by a new train in the city satisfies;
- merely passing through the city on one train does not satisfy;
- routes may also transfer elsewhere;
- input station name resolves to its city.

- [ ] **Step 2: Confirm failure**

```bash
python -m unittest tests.test_csa_constraints.CityTransferConstraintTest -v
```

- [ ] **Step 3: Implement state update and final filtering**

Set the flag only when a transfer event occurs in the constrained city, not on ordinary arrival or same-train continuation.

- [ ] **Step 4: Run tests**

```bash
python -m unittest tests.test_csa_constraints.CityTransferConstraintTest -v
```

- [ ] **Step 5: Update docs and inspect diff**

```bash
git diff --check
git diff -- src/matcher.py src/csa.py src/main.py tests/test_csa_constraints.py README.md
```

---

### Task 9: Implement Search Profiles, Completion Metadata, Timeout, and Safety Limits

**Files:**
- Modify: `src/models.py`
- Modify: `src/csa.py`
- Modify: `tests/test_csa_constraints.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Consumes: `SEARCH_PROFILES`.
- Produces: `SearchMetadata.complete`, `stopped_reason`, `elapsed_ms`, `scanned_connections`, `generated_states`, `returned_routes`.

- [ ] **Step 1: Add failing profile tests**

Assert explicit settings, fast ≤ balanced ≤ thorough state/result limits, complete has no per-station truncation, normal complete run reports true, forced state-limit/timeout reports false and a reason.

Use an injectable monotonic clock or a very small explicit `state_limit` in tests; do not rely on flaky sleep timing.

- [ ] **Step 2: Confirm failures**

```bash
python -m unittest tests.test_csa_constraints.SearchProfileTest -v
```

- [ ] **Step 3: Implement profile-driven state handling**

Move all hardcoded state/result limits into profile settings. Complete mode may still use strict dominance for identical-or-worse states to avoid exact duplicates, but must not use relaxed heuristic deletion or per-station truncation.

Check timeout and global state count during scanning and footpath generation.

- [ ] **Step 4: Run tests**

```bash
python -m unittest tests.test_csa_constraints.SearchProfileTest -v
```

- [ ] **Step 5: Update docs and inspect diff**

Document exact profile values and complete-mode semantics.

```bash
git diff --check
git diff -- src/models.py src/csa.py tests/test_csa_constraints.py README.md
```

---

### Task 10: Replace Train-Code Dedup with Full Typed Path Dedup

**Files:**
- Modify: `src/models.py`
- Modify: `src/csa.py`
- Modify: `tests/test_csa_constraints.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Consumes: `route_key(route)` from models.
- Removes: `tuple(route.train_codes)` dedup.

- [ ] **Step 1: Write failing dedup tests**

Generate routes that share train codes but differ in boarding station, alighting station, absolute time, or ground-vs-train city connection. Assert all distinct routes remain and exact duplicates collapse.

- [ ] **Step 2: Confirm failure**

```bash
python -m unittest tests.test_csa_constraints.RouteDedupTest -v
```

- [ ] **Step 3: Replace dedup key**

Use the complete ordered segment tuple. Keep dedup after reconstruction so it sees merged train segments and ground segments.

- [ ] **Step 4: Run tests and update docs**

```bash
python -m unittest tests.test_models tests.test_csa_constraints.RouteDedupTest -v
git diff --check
```

Update HANDOVER risk list to mark coarse dedup fixed.

---

### Task 11: Complete Cross-Day Serialization and Display

**Files:**
- Modify: `src/main.py`
- Modify: `src/models.py`
- Create: `tests/test_main_api.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Consumes: `format_absolute_minutes()`.
- API times: `{minutes, time, day_offset, display}`.

- [ ] **Step 1: Write failing serialization tests**

Build a typed route crossing midnight and assert route/segment departure and arrival JSON include correct day offsets and Chinese display.

- [ ] **Step 2: Confirm failure**

```bash
python -m unittest tests.test_main_api.CrossDaySerializationTest -v
```

- [ ] **Step 3: Use one serializer for CLI/API/GUI**

Remove `% 1440`-only strings from canonical results. CLI prints `次日`; GUI consumes `display`.

- [ ] **Step 4: Run tests**

```bash
python -m unittest tests.test_models tests.test_main_api.CrossDaySerializationTest -v
```

- [ ] **Step 5: Update docs and inspect diff**

```bash
git diff --check
git diff -- src/models.py src/main.py tests/test_main_api.py README.md
```

---

### Task 12: Return Structured HTTP 400 Errors and Validate API Requests

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main_api.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Consumes: `build_search_request()` and `RequestValidationError`.
- Produces: HTTP 400 `{ "error": { "code": ..., "message": ... } }`.

- [ ] **Step 1: Write failing API tests**

Start an ephemeral local HTTP server on port 0 in a thread. Request invalid integer, invalid time, invalid mode/profile, and unknown station. Assert HTTP 400 and stable error codes. Assert a valid request returns HTTP 200.

- [ ] **Step 2: Confirm failure**

```bash
python -m unittest tests.test_main_api.ApiValidationTest -v
```

- [ ] **Step 3: Implement status-aware JSON responses**

Change `_json` to accept `status=200`; catch validation errors before search; keep internal unexpected errors as HTTP 500 without exposing stack traces.

- [ ] **Step 4: Run tests**

```bash
python -m unittest tests.test_validation tests.test_main_api.ApiValidationTest -v
```

- [ ] **Step 5: Update docs and inspect diff**

```bash
git diff --check
git diff -- src/main.py tests/test_main_api.py README.md
```

---

### Task 13: Update GUI for Match Mode, Search Profile, Ground Segments, and Completeness

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main_api.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Sends: `match_mode`, `search_profile`, `max_transfers`, `timeout_seconds`.
- Renders: train and interstation segment types; metadata completeness.

- [ ] **Step 1: Add HTML contract tests**

Assert `HTML` contains controls with stable IDs:

```text
match-mode
search-profile
max-transfers
search-timeout
same-transfer
inter-transfer
xfer-at
```

Assert renderer branches on `segment.type === 'train'` and `segment.type === 'interstation'`, and shows incomplete warning.

- [ ] **Step 2: Confirm failure**

```bash
python -m unittest tests.test_main_api.GuiContractTest -v
```

- [ ] **Step 3: Update GUI controls and rendering**

Default fuzzy/balanced. Rename “指定换乘站” to “指定换乘城市”. Display actual origin/destination, train transfer count, interstation count/minutes, Chinese day offsets, and completion status.

- [ ] **Step 4: Run GUI contract and API tests**

```bash
python -m unittest tests.test_main_api -v
```

- [ ] **Step 5: Manual local GUI smoke test**

```bash
python src/main.py --gui --port 8080
```

Manually verify one exact query, one fuzzy query, one ground-transfer fixture/API response if exposed, and one invalid parameter response. Stop only the launched server process.

- [ ] **Step 6: Update docs and inspect diff**

```bash
git diff --check
git diff -- src/main.py tests/test_main_api.py README.md
```

---

### Task 14: Rework Scoring for Typed Routes and Explicit Interstation Penalty

**Files:**
- Modify: `src/main.py`
- Create: `tests/test_scoring.py`
- Modify: `README.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Consumes: typed `RouteResult`.
- Produces: score components including interstation penalty.

- [ ] **Step 1: Write failing scoring tests**

Construct two otherwise equivalent routes, one with a ground interstation segment. Assert the ground route scores lower. Test empty input, equal max values, and cross-day times.

- [ ] **Step 2: Confirm failure**

```bash
python -m unittest tests.test_scoring -v
```

- [ ] **Step 3: Implement explicit components**

Retain the current simple linear baseline but add a documented interstation penalty. Avoid boolean precedence bugs in night-time counting by extracting a helper.

- [ ] **Step 4: Run tests and update docs**

```bash
python -m unittest tests.test_scoring -v
git diff --check
```

Document exact weights as current defaults, not universal truth.

---

### Task 15: Full Regression, National-Data Smoke, Performance Recording, and Final Documentation Audit

**Files:**
- Modify: `README.md`
- Modify: `HANDOVER.md`
- Modify: `docs/superpowers/specs/2026-07-15-unified-multistation-search-design.md` only if implementation intentionally differs from the approved design; record the rationale.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified release candidate and accurate documentation.

- [ ] **Step 1: Run the full automated suite**

```bash
python -m unittest discover -s tests -v
python -m py_compile src/*.py tools/parse_timetable.py
```

Expected: all tests pass.

- [ ] **Step 2: Run national-data smoke queries**

```bash
python src/main.py 北京南 上海虹桥 --match-mode exact --search-profile balanced --max 3
python src/main.py 北京南 上海虹桥 --match-mode fuzzy --search-profile balanced --max 3
python src/main.py 延安 深圳北 --match-mode fuzzy --search-profile balanced --max 3
```

Capture actual load time, search time, actual origin/destination, route counts, completeness, and first candidates. Do not preserve old numerical claims if outputs change.

- [ ] **Step 3: Verify known feature behavior**

Use automated or targeted commands to prove:

- exact/fuzzy station scope differs;
- interstation minutes alter a fixture result;
- train and ground variants coexist;
- transfer city filters all returned routes;
- complete interruption is labelled incomplete;
- repeated build counts match;
- invalid API inputs return 400;
- cross-day display includes day offsets.

- [ ] **Step 4: Audit README and HANDOVER line by line**

Remove every stale “not implemented” item that is now verified; retain date filtering as unimplemented; record any remaining limitations. Ensure HANDOVER recovery point states implementation status task by task.

- [ ] **Step 5: Inspect all pending changes**

```bash
git status --short
git diff --check
git diff --stat
git diff -- README.md src tests docs/superpowers
```

Also read ignored `HANDOVER.md` directly.

- [ ] **Step 6: Independent review gate**

Run the available review tool/subagent over all changed files. Resolve correctness findings, rerun the full suite, and record reviewed paths. If the review capability is host-broken, report that limitation explicitly and require human diff review before commit.

- [ ] **Step 7: Stop before commit**

Present the full diff summary and verification evidence to the user. Do not stage, commit, or push. Commit identity confirmation remains a separate mandatory human gate.
