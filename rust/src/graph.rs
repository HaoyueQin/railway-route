//! 图模型：站索引 / 相邻停站边 / 同城分组 / 换乘边 / 双日连接分桶 / 反向 Dijkstra 下界。
//!
//! 与 Python 版 src/graph.py 语义逐行对齐（master-v2 M2 对拍目标）。
//! 对拍基准（Python graph.build 实测，见 tools/dump_graph_stats.py）：
//!   站 3,305 / 唯一边 17,792 / TrainEdge 113,968 / 换乘边 33,926 /
//!   sorted_conns 225,454 / out_conns 非空桶 3,296 / 0 距离边跳过 1,241

use crate::data::{parse_minutes, parse_timetable_csv_ordered, parse_station_names_full};
use crate::json::Json;
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::fs;
use std::path::Path;

/// 同站换乘默认缓冲（分钟）——M3 CSA 换乘语义使用
#[allow(dead_code)]
pub const DEFAULT_SAME_TRANSFER_MINUTES: i32 = 15;
/// 异站（同城不同站）换乘默认缓冲（分钟）
pub const DEFAULT_INTER_TRANSFER_MINUTES: i32 = 60;

/// 图中一条边的摘要信息（不含具体车次列表）。
#[derive(Debug, Clone, Copy)]
pub struct EdgeInfo {
    pub min_time: i32,     // 该区段最快列车耗时（分钟）
    pub distance: u32,     // 里程（km）
    pub train_count: u32,  // 经过该边的车次数
}

/// 一条边在具体车次中的记录。
#[derive(Debug, Clone)]
#[allow(dead_code)] // seq_to 等字段供 M3 CSA 段序列输出使用
pub struct TrainEdge {
    pub train_code: String,
    pub seq_from: u32,
    pub seq_to: u32,
    pub depart_time: String,   // 发车时间 HH:MM
    pub arrive_time: String,   // 到达时间 HH:MM
    pub travel_minutes: i32,   // 区间运行时间（分钟）
    pub distance: u32,         // 区间里程（km）
    pub dist_cumulative: u32,  // 到达站的累计里程（从该车次起点算）
}

/// 一条 Connection（CSA 主循环直接消费）。
///
/// 双日模型：day0/day1 各一条；arrive 可能比 depart 晚跨天（arr < dep 时 +1440）。
#[derive(Debug, Clone)]
#[allow(dead_code)] // 字段供 M3 CSA 消费
pub struct Connection {
    pub depart_minutes: i32,
    pub train_code: String,
    pub from_idx: usize,
    pub to_idx: usize,
    pub arrive_minutes: i32,
    pub travel_minutes: i32,
    pub distance: u32,
    pub dist_cumulative: u32,
    pub seq_from: u32,
}

/// 铁路网络图。
pub struct Graph {
    // 车站名 → 内部索引（按首次出现顺序分配，与 Python _get_or_create_station 语义一致）
    pub station_to_idx: HashMap<String, usize>,
    pub idx_to_station: Vec<String>,
    // 同城车站分组: city_code → [站名]（station_name.js 保序）
    pub city_groups: HashMap<String, Vec<String>>,
    // 站 → 城市代码 / 城市代码 → 城市名（build 后仅含图中有效车站）
    pub station_to_city_code: HashMap<usize, String>,
    pub city_code_to_name: HashMap<String, String>,
    // 邻接表（唯一有向边 → 摘要）
    pub edges: HashMap<(usize, usize), EdgeInfo>,
    // 反向邻接表: to_idx → [(from_idx, EdgeInfo)]（供反向 Dijkstra 使用）
    pub reverse_edges: Vec<Vec<(usize, EdgeInfo)>>,
    // 边 → 车次列表
    pub edge_trains: HashMap<(usize, usize), Vec<TrainEdge>>,
    // 同城换乘边（有向两两互联）集合 + 列表
    pub transfer_edge_set: HashSet<(usize, usize)>,
    pub transfer_edges: Vec<(usize, usize)>,
    // 电报码 → 站名（station_name.js parts[2]，坐标按电报码索引）
    pub telecode_to_name: HashMap<String, String>,
    // 快速索引: station_idx → [同城其他车站 idx]（不含自己）
    pub same_city_of: Vec<Vec<usize>>,
    // 出发索引: station_idx → [(车次, 序号, 发车 HH:MM)]
    pub departures: Vec<Vec<(String, u32, String)>>,
    // 车次全程停站: code → [(station_idx, dep_min, arr_min, seq, dist_cum)]
    // 始发无到达/终到无发车记 -1；时刻单调递增（跨午夜 +1440）
    pub train_stops: HashMap<String, Vec<(usize, i32, i32, u32, u32)>>,
    // 预排序双日 Connection（全局按 depart_minutes 升序）
    pub sorted_connections: Vec<Connection>,
    // 按出发站分桶（桶内按 depart_minutes 升序）
    pub out_conns: Vec<Vec<Connection>>,
    // 单目标距离下界缓存: target → 各站最短铁路距离（M3 直达枚举使用）
    #[allow(dead_code)]
    distance_cache: HashMap<usize, HashMap<usize, u64>>,
    // 5.1-1 异站换乘按距离估算：站 idx → GCJ-02 经纬度（12306 getStationAddress 抓取）
    pub coords: HashMap<usize, (f64, f64)>,
    // 同城站对 → 估算换乘分钟（无直达班次时的距离估算，构建时预计算）
    pub interstation_minutes: HashMap<(usize, usize), i32>,
    // 同城站对 → 确定性换乘分钟（直达班次/坐标距离分支预计算；
    // 无数据的站对搜索时回退用户配置值——footpath 热点查表）
    pub foot_times: HashMap<(usize, usize), i32>,
}

/// 城市行政后缀（用于同城归属判断）
const CITY_SUFFIXES: [&str; 8] = ["市", "县", "区", "站", "地区", "自治州", "盟", "林区"];

impl Graph {
    pub fn new() -> Self {
        Graph {
            station_to_idx: HashMap::new(),
            idx_to_station: Vec::new(),
            city_groups: HashMap::new(),
            station_to_city_code: HashMap::new(),
            city_code_to_name: HashMap::new(),
            edges: HashMap::new(),
            reverse_edges: Vec::new(),
            edge_trains: HashMap::new(),
            transfer_edge_set: HashSet::new(),
            transfer_edges: Vec::new(),
            telecode_to_name: HashMap::new(),
            same_city_of: Vec::new(),
            departures: Vec::new(),
            train_stops: HashMap::new(),
            sorted_connections: Vec::new(),
            out_conns: Vec::new(),
            distance_cache: HashMap::new(),
            coords: HashMap::new(),
            interstation_minutes: HashMap::new(),
            foot_times: HashMap::new(),
        }
    }

    pub fn station_count(&self) -> usize {
        self.idx_to_station.len()
    }

    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    pub fn transfer_count(&self) -> usize {
        self.transfer_edges.len()
    }

    /// 从时刻表 CSV 和 station_name.js 构建图（对齐 Python build() 步骤顺序）。
    pub fn build(&mut self, csv_path: &Path, station_js_path: &Path) -> Result<(), String> {
        self.load_station_cities(station_js_path)?;
        self.load_timetable(csv_path)?;
        self.add_transfer_edges();
        self.compute_edge_stats();
        self.build_reverse_edges();
        self.build_connections_cache();
        Ok(())
    }

    fn get_or_create_station(&mut self, name: &str) -> usize {
        if let Some(&idx) = self.station_to_idx.get(name) {
            return idx;
        }
        let idx = self.idx_to_station.len();
        self.station_to_idx.insert(name.to_string(), idx);
        self.idx_to_station.push(name.to_string());
        // 保持站级数组与索引同步（建边过程中会新增站）
        self.same_city_of.push(Vec::new());
        self.departures.push(Vec::new());
        idx
    }

    // ── 构建：城市分组 ──────────────────────────────

    fn load_station_cities(&mut self, path: &Path) -> Result<(), String> {
        for e in parse_station_names_full(path)? {
            self.city_groups.entry(e.city_code.clone()).or_default().push(e.name.clone());
            if !e.city_name.is_empty() {
                self.city_code_to_name.insert(e.city_code, e.city_name);
            }
            self.telecode_to_name.insert(e.telecode.to_uppercase(), e.name);
        }
        Ok(())
    }

    // ── 构建：时刻表 → 边 ──────────────────────────

    /// 区间运行分钟数（跨天 +1440；缺时刻记 0，但边已过滤缺时刻）。
    fn time_diff(&self, depart: &str, arrive: &str) -> i32 {
        let d = parse_minutes(depart).unwrap_or(0);
        let a = parse_minutes(arrive).unwrap_or(0);
        let mut diff = a - d;
        if diff < 0 {
            diff += 1440;
        }
        diff
    }

    fn load_timetable(&mut self, csv_path: &Path) -> Result<(), String> {
        // 保序版本：与 Python dict 插入序一致，保证同区段多车次时 "首条" 相同
        let grouped = parse_timetable_csv_ordered(csv_path)?;
        self.train_stops.reserve(grouped.len());

        for (code, stops) in &grouped {
            // 车次全程停站（含跨午夜修正链）——与 Python _load_timetable 的 full_stops 一致
            let mut full: Vec<(usize, i32, i32, u32, u32)> = Vec::with_capacity(stops.len());
            let mut offset = 0i32;
            let mut prev_time = -1i32;
            for st in stops {
                let dep_m = parse_minutes(&st.depart_raw).unwrap_or(-1);
                let arr_m = parse_minutes(&st.arrive_raw).unwrap_or(-1);
                if dep_m != -1 && prev_time != -1 {
                    while dep_m + offset <= prev_time {
                        offset += 1440;
                    }
                }
                if arr_m != -1 {
                    while arr_m + offset <= prev_time {
                        offset += 1440;
                    }
                    prev_time = arr_m + offset;
                } else if dep_m != -1 {
                    // 始发站无到达时刻：以发车时刻推进基准
                    prev_time = dep_m + offset;
                }
                let sidx = self.get_or_create_station(&st.station);
                full.push((
                    sidx,
                    if dep_m != -1 { dep_m + offset } else { -1 },
                    if arr_m != -1 { arr_m + offset } else { -1 },
                    st.seq,
                    st.distance_km,
                ));
            }
            self.train_stops.insert(code.clone(), full);

            // 相邻停站 → 边
            for i in 0..stops.len().saturating_sub(1) {
                let cur = &stops[i];
                let nxt = &stops[i + 1];
                let depart = cur.depart_raw.trim();
                let arrive = nxt.arrive_raw.trim();
                if depart.is_empty() || arrive.is_empty() {
                    continue; // 跳过始发/终到站缺少时间的边
                }
                let travel = self.time_diff(depart, arrive);
                // 区间里程 = 相邻站累计里程差（负值取 0）
                let dist_seg = nxt.distance_km.saturating_sub(cur.distance_km);
                let dist_cum = nxt.distance_km;
                let from_idx = self.get_or_create_station(cur.station.trim());
                let to_idx = self.get_or_create_station(nxt.station.trim());

                self.edge_trains
                    .entry((from_idx, to_idx))
                    .or_default()
                    .push(TrainEdge {
                        train_code: code.clone(),
                        seq_from: cur.seq,
                        seq_to: nxt.seq,
                        depart_time: depart.to_string(),
                        arrive_time: arrive.to_string(),
                        travel_minutes: travel,
                        distance: dist_seg,
                        dist_cumulative: dist_cum,
                    });
                self.departures[from_idx].push((code.clone(), cur.seq, depart.to_string()));
            }
        }
        Ok(())
    }

    // ── 构建：同城换乘边 ──────────────────────────

    /// 提取城市基础名（去除第一个行政后缀）。
    fn city_base_name(city_name: &str) -> &str {
        for sfx in CITY_SUFFIXES {
            if city_name.ends_with(sfx) && city_name.len() > sfx.len() {
                return &city_name[..city_name.len() - sfx.len()];
            }
        }
        city_name
    }

    /// 判断车站是否真正属于该城市的同城范围（与 Python _station_is_city_member 一致）：
    /// - 大城市（≥8 站）：所有站视为同城
    /// - 含城市基名：仅含城市基名的站算同城（排除异名县）
    /// - 不含城市基名：全部算同城
    fn station_is_city_member(
        station_name: &str,
        city_base: &str,
        city_station_count: usize,
        any_station_has_city_base: bool,
    ) -> bool {
        if city_station_count >= 8 {
            return true;
        }
        if any_station_has_city_base {
            return station_name.contains(city_base);
        }
        true
    }

    fn add_transfer_edges(&mut self) {
        // 先统计每个城市在图中的有效站数
        let mut city_graph_count: HashMap<&str, usize> = HashMap::new();
        for (city, names) in &self.city_groups {
            let cnt = names
                .iter()
                .filter(|n| self.station_to_idx.contains_key(*n))
                .count();
            city_graph_count.insert(city.as_str(), cnt);
        }

        for (city, names) in &self.city_groups {
            let city_name = self
                .city_code_to_name
                .get(city.as_str())
                .map(|s| s.as_str())
                .unwrap_or("");
            let city_base = Self::city_base_name(city_name);
            let graph_count = *city_graph_count.get(city.as_str()).unwrap_or(&0);

            // 任一车站名含城市基名（空基名视为全部匹配）
            let any_has_base = !city_base.is_empty()
                && names
                    .iter()
                    .filter(|n| self.station_to_idx.contains_key(*n))
                    .any(|n| n.contains(city_base));

            let mut indices = Vec::new();
            for name in names {
                if !self.station_to_idx.contains_key(name) {
                    continue;
                }
                if !Self::station_is_city_member(name, city_base, graph_count, any_has_base) {
                    continue;
                }
                let idx = self.station_to_idx[name];
                indices.push(idx);
                self.station_to_city_code.insert(idx, city.clone());
            }

            // 同城车站两两有向互联（不含自己）
            for &a in &indices {
                for &b in &indices {
                    if a != b {
                        self.transfer_edges.push((a, b));
                        self.transfer_edge_set.insert((a, b));
                        self.same_city_of[a].push(b);
                    }
                }
            }
        }
    }

    // ── 构建：边摘要 / 反向邻接 / 连接分桶 ──────────

    fn compute_edge_stats(&mut self) {
        for (&(f, t), trains) in &self.edge_trains {
            let min_time = trains.iter().map(|te| te.travel_minutes).min().unwrap_or(0);
            let dist = trains[0].distance; // 同一区段距离一致
            self.edges.insert(
                (f, t),
                EdgeInfo { min_time, distance: dist, train_count: trains.len() as u32 },
            );
        }
    }

    fn build_reverse_edges(&mut self) {
        self.reverse_edges = vec![Vec::new(); self.station_count()];
        for (&(f, t), info) in &self.edges {
            self.reverse_edges[t].push((f, *info));
        }
    }

    /// 预建并排序双日 Connection 列表（0 距离边不参与规划）。
    fn build_connections_cache(&mut self) {
        let mut conns = Vec::new();
        for (&(f, t), trains) in &self.edge_trains {
            for te in trains {
                if te.distance <= 0 {
                    continue; // 里程无效的边（Y 字头旅游列车等）：不参与规划
                }
                let dep = parse_minutes(&te.depart_time).unwrap_or(0);
                let mut arr = parse_minutes(&te.arrive_time).unwrap_or(0);
                if arr < dep {
                    arr += 1440;
                }
                for day in 0..2 {
                    conns.push(Connection {
                        depart_minutes: dep + day * 1440,
                        train_code: te.train_code.clone(),
                        from_idx: f,
                        to_idx: t,
                        arrive_minutes: arr + day * 1440,
                        travel_minutes: te.travel_minutes,
                        distance: te.distance,
                        dist_cumulative: te.dist_cumulative,
                        seq_from: te.seq_from,
                    });
                }
            }
        }
        conns.sort_by_key(|c| c.depart_minutes);
        // 按出发站分桶（conns 已全局有序，桶内自然有序）
        let mut buckets: Vec<Vec<Connection>> = vec![Vec::new(); self.station_count()];
        for conn in &conns {
            buckets[conn.from_idx].push(conn.clone());
        }
        self.out_conns = buckets;
        self.sorted_connections = conns;
    }

    // ── 查询 ─────────────────────────────────────────

    /// 反向多源 Dijkstra：各站到 sources 中任一目标的最小代价。
    ///
    /// attr: distance（铁路里程 km）或 min_time（最快运行时间下界，分钟）。
    /// 代价为整数（Python 侧 float 整数累加，数值一致）。
    /// 跳过代价为 0 的运行边（数据缺失段不作为下界，避免假捷径）。
    fn reverse_dijkstra(&self, sources: &[usize], attr: DijkstraAttr) -> HashMap<usize, u64> {
        let mut distances: HashMap<usize, u64> = HashMap::new();
        let mut heap: BinaryHeap<Reverse<(u64, usize)>> = BinaryHeap::new();
        for &s in sources {
            distances.insert(s, 0);
            heap.push(Reverse((0, s)));
        }
        while let Some(Reverse((cost, current))) = heap.pop() {
            if cost > distances[&current] {
                continue;
            }
            for (prev, info) in &self.reverse_edges[current] {
                let edge_cost = match attr {
                    DijkstraAttr::Distance => info.distance as u64,
                    DijkstraAttr::MinTime => info.min_time as u64,
                };
                if edge_cost == 0 {
                    continue; // 数据缺失段不作为下界
                }
                let candidate = cost + edge_cost;
                if candidate < *distances.get(prev).unwrap_or(&u64::MAX) {
                    distances.insert(*prev, candidate);
                    heap.push(Reverse((candidate, *prev)));
                }
            }
        }
        distances
    }

    /// 各站到任一目标站的最短铁路距离（多目标质量改进）。
    pub fn get_multi_source_distances(&self, targets: &[usize]) -> HashMap<usize, u64> {
        self.reverse_dijkstra(targets, DijkstraAttr::Distance)
    }

    /// 各站到任一目标站的最快运行时间下界（目标导向剪枝用）。
    pub fn get_multi_source_times(&self, targets: &[usize]) -> HashMap<usize, u64> {
        self.reverse_dijkstra(targets, DijkstraAttr::MinTime)
    }

    /// 各站到 target 的最短铁路距离（缓存版，单目标；M3 直达枚举使用）。
    #[allow(dead_code)]
    pub fn get_reverse_distances(&mut self, target: usize) -> &HashMap<usize, u64> {
        if !self.distance_cache.contains_key(&target) {
            let d = self.reverse_dijkstra(&[target], DijkstraAttr::Distance);
            self.distance_cache.insert(target, d);
        }
        &self.distance_cache[&target]
    }

    pub fn get_edge_info(&self, from_idx: usize, to_idx: usize) -> Option<&EdgeInfo> {
        self.edges.get(&(from_idx, to_idx))
    }

    /// 异站换乘时间估算：
    /// 1) 有直达班次 → 最短旅行时间 + 30 分钟（同城有市郊/城际线，坐车比地面快）；
    ///    预计算 edges.min_time 查表（O(1)），避免每次遍历车次列表——footpath 热点；
    /// 2) 无直达班次但同城有坐标 → 按直线距离估算（10km 内 30min，每 10km +15min）；
    /// 3) 数据不足 → 回退用户配置默认值。
    pub fn get_interstation_transfer_time(
        &self,
        from_idx: usize,
        to_idx: usize,
        default_minutes: i32,
    ) -> i32 {
        if let Some(edge) = self.edges.get(&(from_idx, to_idx)) {
            return edge.min_time + 30;
        }
        if let Some(&m) = self.interstation_minutes.get(&(from_idx, to_idx)) {
            return m;
        }
        default_minutes
    }

    /// 加载车站坐标（data/station_coords.json，12306 GCJ-02）并预计算同城站对换乘分钟。
    /// 文件缺失/格式异常时静默跳过（回退固定换乘时间，不阻断启动）。
    pub fn load_coords(&mut self, path: &Path) {
        let Ok(text) = fs::read_to_string(path) else {
            return;
        };
        let Ok(obj) = crate::json::parse(&text) else {
            eprintln!("坐标文件格式无效，跳过：{}", path.display());
            return;
        };
        let Json::Object(map) = obj else {
            return;
        };
        // 电报码索引（两数据源同体系，比站名匹配更可靠）：
        // 电报码 → 路路通站名 → 图内站 idx
        let mut matched = 0usize;
        for (code, v) in map {
            let Json::Object(fields) = v else { continue };
            let (Some(Json::Number(lat)), Some(Json::Number(lng))) =
                (fields.get("lat"), fields.get("lng"))
            else {
                continue;
            };
            let name = match self.telecode_to_name.get(&code.to_uppercase()) {
                Some(n) => n.as_str(),
                None => continue,
            };
            if let Some(&idx) = self.station_to_idx.get(name) {
                self.coords.insert(idx, (*lat, *lng));
                matched += 1;
            }
        }
        eprintln!("坐标加载: {} 站（电报码索引）", matched);
        // 同城站对距离 → 换乘分钟（仅对图中有效的同城对有坐标的站对）
        for &(a, b) in &self.transfer_edges {
            if let (Some(ca), Some(cb)) = (self.coords.get(&a), self.coords.get(&b)) {
                let d = haversine_km(*ca, *cb);
                self.interstation_minutes.insert((a, b), est_transfer_minutes(d));
            }
        }
        // 确定性换乘分钟预计算（直达班次/坐标距离；无数据对搜索时回退用户配置）
        for &(a, b) in &self.transfer_edges {
            if let Some(edge) = self.edges.get(&(a, b)) {
                self.foot_times.insert((a, b), edge.min_time + 30);
            } else if let Some(&m) = self.interstation_minutes.get(&(a, b)) {
                self.foot_times.insert((a, b), m);
            }
        }
    }

    pub fn is_same_city(&self, a: usize, b: usize) -> bool {
        self.transfer_edge_set.contains(&(a, b)) || self.transfer_edge_set.contains(&(b, a))
    }
}

/// 直线距离（km，Haversine）。坐标来自 12306（GCJ-02 偏移量级 ~500m，
/// 对 10km 级换乘估算误差 <5%，可接受；如需高精度可后续转 WGS84）。
fn haversine_km(a: (f64, f64), b: (f64, f64)) -> f64 {
    const R: f64 = 6371.0;
    let (la1, lo1) = (a.0.to_radians(), a.1.to_radians());
    let (la2, lo2) = (b.0.to_radians(), b.1.to_radians());
    let dlat = la2 - la1;
    let dlon = lo2 - lo1;
    let h = (dlat / 2.0).sin().powi(2) + la1.cos() * la2.cos() * (dlon / 2.0).sin().powi(2);
    // 浮点误差可能使 h 略超 [0,1]，clamp 防 asin(NaN)
    2.0 * R * h.clamp(0.0, 1.0).sqrt().asin()
}

/// 距离 → 地面换乘估算分钟（交接文档 5.1-1 约定：10km 内 30min，每 10km +15min，
/// 上限 180min 防极端站对）。
fn est_transfer_minutes(dist_km: f64) -> i32 {
    let m = 30 + ((dist_km - 10.0).max(0.0) / 10.0).ceil() as i32 * 15;
    m.min(180)
}

#[derive(Debug, Clone, Copy)]
enum DijkstraAttr {
    Distance,
    MinTime,
}
