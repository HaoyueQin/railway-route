//! 数据层：时刻表 CSV + station_name.js 解析（对齐 Python 版 src/graph.py）。
//!
//! 对拍基准（Python graph.build 实测）：
//!   CSV 128,141 行 / 14,173 车次 / 3,305 车站
//!   station_name.js 城市分组 city_groups（同城车站合并换乘）

use std::collections::HashMap;
use std::fs;
use std::path::Path;

/// 时刻表一行（原始字段，时间未归一化）
#[derive(Debug, Clone)]
pub struct StopRow {
    pub seq: u32,
    pub station: String,
    pub arrive_raw: String,
    pub depart_raw: String,
    pub distance_km: u32,
}

/// 解析时刻表 CSV → 按车次分组（组内按序号排序，车次按 CSV 首次出现顺序）。
///
/// 保序版本：与 Python dict 的插入序一致，用于对齐 "该边首个车次的里程"
/// （同一区段可能混有 0 里程的 Y 字头车次，Python 取首条记录的值）。
pub fn parse_timetable_csv_ordered(path: &Path) -> Result<Vec<(String, Vec<StopRow>)>, String> {
    let text = fs::read_to_string(path).map_err(|e| e.to_string())?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text); // UTF-8 BOM
    let mut order: Vec<String> = Vec::new();
    let mut grouped: HashMap<String, Vec<StopRow>> = HashMap::new();
    for (i, line) in text.lines().enumerate() {
        if i == 0 {
            continue; // 表头
        }
        if line.trim().is_empty() {
            continue;
        }
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 7 {
            continue;
        }
        let code = cols[0].trim().to_string();
        let seq: u32 = cols[1].trim().parse().unwrap_or(0);
        let station = cols[2].trim().to_string();
        let arrive = cols[3].trim().to_string();
        let depart = cols[4].trim().to_string();
        let dist: u32 = cols[6].trim().parse().unwrap_or(0);
        if !grouped.contains_key(&code) {
            order.push(code.clone());
        }
        grouped.entry(code).or_default().push(StopRow {
            seq,
            station,
            arrive_raw: arrive,
            depart_raw: depart,
            distance_km: dist,
        });
    }
    let mut out: Vec<(String, Vec<StopRow>)> = Vec::with_capacity(order.len());
    for code in order {
        let mut v = grouped.remove(&code).unwrap_or_default();
        v.sort_by_key(|r| r.seq);
        out.push((code, v));
    }
    Ok(out)
}

/// 解析时刻表 CSV → 按车次分组（组内按序号排序）。
pub fn parse_timetable_csv(path: &Path) -> Result<HashMap<String, Vec<StopRow>>, String> {
    Ok(parse_timetable_csv_ordered(path)?
        .into_iter()
        .collect::<HashMap<_, _>>())
}

/// "HH:MM" → 当日分钟数；空串 → None。
pub fn parse_minutes(t: &str) -> Option<i32> {
    let t = t.trim();
    if t.is_empty() {
        return None;
    }
    let mut parts = t.split(':');
    let h: i32 = parts.next()?.parse().ok()?;
    let m: i32 = parts.next()?.parse().ok()?;
    Some(h * 60 + m)
}

/// 车次全程停站（含跨午夜修正链，与 Python graph.py::_load_timetable 完全一致）。
///
/// 返回 (station_idx, dep_min, arr_min, seq, dist_cum)；
/// 始发无到达/终到无发车记 -1；同一车次内时刻单调递增（跨午夜 +1440，慢车可跨多天）。
pub fn build_train_stops(
    grouped: &HashMap<String, Vec<StopRow>>,
    station_to_idx: &HashMap<String, usize>,
) -> HashMap<String, Vec<(usize, i32, i32, u32, u32)>> {
    let mut out = HashMap::with_capacity(grouped.len());
    for (code, stops) in grouped {
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
            let sidx = *station_to_idx.get(&st.station).unwrap_or(&0);
            full.push((
                sidx,
                if dep_m != -1 { dep_m + offset } else { -1 },
                if arr_m != -1 { arr_m + offset } else { -1 },
                st.seq,
                st.distance_km,
            ));
        }
        out.insert(code.clone(), full);
    }
    out
}

/// station_name.js 单条条目（与 Python build_matcher 逐字段对齐）。
#[derive(Debug, Clone)]
pub struct StationEntry {
    pub name: String,       // 中文站名（parts[1]）
    pub telecode: String,   // 电报码（parts[2]）
    pub pinyin: String,     // 拼音（parts[3]）
    pub city_code: String,  // 城市代码（parts[6]，如 "0357" 表示北京）
    pub city_name: String,  // 城市名（parts[7]，可空）
}

/// 解析 station_name.js → 逐条 (city_code, city_name, station_name)。
///
/// 城市名可空（部分条目无第 8 字段）；与 Python 的 split 逻辑一致
/// （`station_names\s*=\s*'(.*)'`，城市名 = parts[7] 仅当存在时）。
pub fn parse_station_names_full(path: &Path) -> Result<Vec<StationEntry>, String> {
    let text = fs::read_to_string(path).map_err(|e| e.to_string())?;
    // 取 "station_names = '...'" 引号内内容（与 Python 的 split 逻辑一致）
    let start = text
        .find("station_names")
        .and_then(|i| text[i..].find('\''))
        .map(|i| i + 1)
        .ok_or("station_names 未找到")?;
    let end = text[start..].rfind('\'').map(|i| start + i).ok_or("引号未闭合")?;
    let content = &text[start + 1..end];
    let mut out = Vec::new();
    for entry in content.split('@') {
        let entry = entry.trim();
        if entry.is_empty() {
            continue;
        }
        let parts: Vec<&str> = entry.split('|').collect();
        if parts.len() < 7 {
            continue;
        }
        out.push(StationEntry {
            name: parts[1].to_string(), // 中文站名
            telecode: parts.get(2).map(|s| s.to_string()).unwrap_or_default(),
            pinyin: parts.get(3).map(|s| s.to_string()).unwrap_or_default(),
            city_code: parts[6].to_string(), // 城市代码（如 "0357" 表示北京）
            city_name: parts.get(7).map(|s| s.to_string()).unwrap_or_default(),
        });
    }
    Ok(out)
}

/// 解析 station_name.js → 城市分组 {city_code: [站名, ...]}（同城车站视为可步行换乘）。
pub fn parse_station_names_js(path: &Path) -> Result<HashMap<String, Vec<String>>, String> {
    let mut city_groups: HashMap<String, Vec<String>> = HashMap::new();
    for e in parse_station_names_full(path)? {
        city_groups.entry(e.city_code).or_default().push(e.name);
    }
    Ok(city_groups)
}
