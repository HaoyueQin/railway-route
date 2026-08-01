//! 搜索请求 / 配置档位 / 路径段 / 结果模型（对齐 Python src/models.py）。

/// 每站每轮标签上限等搜索档位配置（对齐 SEARCH_PROFILES）。
#[derive(Debug, Clone, Copy)]
pub struct SearchProfileSettings {
    pub max_states_per_station: Option<usize>,
    pub max_results: Option<usize>,
    pub default_timeout_seconds: u64,
    pub state_limit: u64,
    pub time_prune_slack: Option<i32>,
}

pub const PROFILES: [(&str, SearchProfileSettings); 4] = [
    (
        "fast",
        SearchProfileSettings {
            max_states_per_station: Some(4),
            max_results: None,
            default_timeout_seconds: 15,
            state_limit: 200_000,
            time_prune_slack: Some(240),
        },
    ),
    (
        "balanced",
        SearchProfileSettings {
            max_states_per_station: Some(8),
            max_results: None,
            default_timeout_seconds: 30,
            state_limit: 1_500_000,
            time_prune_slack: Some(300),
        },
    ),
    (
        "thorough",
        SearchProfileSettings {
            max_states_per_station: Some(16),
            max_results: None,
            default_timeout_seconds: 60,
            state_limit: 3_000_000,
            time_prune_slack: Some(420),
        },
    ),
    (
        "complete",
        SearchProfileSettings {
            max_states_per_station: Some(24),
            max_results: None,
            default_timeout_seconds: 120,
            state_limit: 8_000_000,
            time_prune_slack: Some(720),
        },
    ),
];

pub fn profile_settings(name: &str) -> SearchProfileSettings {
    for (n, s) in PROFILES {
        if n == name {
            return s;
        }
    }
    PROFILES[1].1 // 默认 balanced
}

/// 统一搜索请求（对齐 SearchRequest）。
#[derive(Debug, Clone)]
pub struct SearchRequest {
    pub from_query: String,
    pub to_query: String,
    pub match_mode: String,          // "exact" | "fuzzy"
    pub from_mode: Option<String>,   // None = 跟随 match_mode
    pub to_mode: Option<String>,
    pub search_profile: String,
    pub earliest_depart: i32,
    pub latest_depart: i32,
    pub earliest_arrive: i32,
    pub latest_arrive: i32,
    pub same_station_transfer_minutes: i32,
    pub interstation_transfer_minutes: i32,
    pub max_transfers: usize,
    pub transfer_city_code: Option<String>,
    pub timeout_seconds: u64,
}

impl SearchRequest {
    pub fn new(from_query: &str, to_query: &str, profile: &str) -> Self {
        SearchRequest {
            from_query: from_query.to_string(),
            to_query: to_query.to_string(),
            match_mode: "fuzzy".into(),
            from_mode: None,
            to_mode: None,
            search_profile: profile.to_string(),
            earliest_depart: 0,
            latest_depart: 2880,
            earliest_arrive: 0,
            latest_arrive: 5760,
            same_station_transfer_minutes: 15,
            interstation_transfer_minutes: 60,
            max_transfers: 3,
            transfer_city_code: None,
            timeout_seconds: 30,
        }
    }
}

/// 铁路区段（对齐 TrainSegment）。
#[derive(Debug, Clone)]
pub struct TrainSegment {
    pub train_code: String,
    pub from_station: String,
    pub to_station: String,
    pub depart_minutes: i32,
    pub arrive_minutes: i32,
    pub travel_minutes: i32,
    pub distance: i32,
}

/// 异站（同城）地面移动段（对齐 InterstationTransferSegment）。
#[derive(Debug, Clone)]
pub struct InterstationTransferSegment {
    pub from_station: String,
    pub to_station: String,
    pub start_minutes: i32,
    pub end_minutes: i32,
    pub transfer_minutes: i32,
    pub city_code: String,
    pub city_name: String,
}

/// 类型化路径段
#[derive(Debug, Clone)]
pub enum PathSegment {
    Train(TrainSegment),
    Interstation(InterstationTransferSegment),
}

impl PathSegment {
    /// 规范化去重/对拍 key（与 Python segment_key 字段等价）
    pub fn seg_key(&self) -> String {
        match self {
            PathSegment::Train(s) => format!(
                "train|{}|{}|{}|{}|{}",
                s.train_code, s.from_station, s.to_station, s.depart_minutes, s.arrive_minutes
            ),
            PathSegment::Interstation(s) => format!(
                "inter|{}|{}|{}|{}",
                s.from_station, s.to_station, s.start_minutes, s.end_minutes
            ),
        }
    }
}

/// 路线结果（对齐 RouteResult）。
#[derive(Debug, Clone)]
pub struct RouteResult {
    pub segments: Vec<PathSegment>,
    pub actual_origin: String,
    pub actual_destination: String,
    pub first_departure: i32,
    pub final_arrival: i32,
    pub total_minutes: i32,
    pub rail_distance: i32,
    pub train_transfers: usize,
    pub interstation_transfers: usize,
    pub interstation_minutes: i32,
    pub transfer_cities: Vec<String>,
    pub matched_transfer_constraint: bool,
}

/// 搜索结果响应（对齐 SearchResponse）。
#[derive(Debug, Clone)]
pub struct SearchResponse {
    pub routes: Vec<RouteResult>,
    pub complete: bool,
    pub stopped_reason: Option<String>,
    pub elapsed_ms: u64,
    pub scanned_connections: u64,
    pub generated_states: u64,
    pub returned_routes: usize,
    pub source_stations: Vec<String>,
    pub target_stations: Vec<String>,
}

/// 把绝对分钟转换为保留日偏移的时间结构（对齐 format_absolute_minutes）。
/// 返回 (minutes, time "HH:MM", day_offset, display)。
pub fn format_absolute_minutes(minutes: i32) -> (i32, String, i32, String) {
    let day_offset = minutes.div_euclid(1440);
    let minute_of_day = minutes.rem_euclid(1440);
    let hour = minute_of_day / 60;
    let minute = minute_of_day % 60;
    let clock = format!("{hour:02}:{minute:02}");
    let display = if day_offset == 0 {
        clock.clone()
    } else if day_offset == 1 {
        format!("次日 {clock}")
    } else {
        format!("第{}日 {clock}", day_offset + 1)
    };
    (minutes, clock, day_offset, display)
}
