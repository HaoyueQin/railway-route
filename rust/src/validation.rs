//! 搜索请求参数校验（对齐 Python src/validation.py::build_search_request）。

use crate::models::SearchRequest;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct ValidationError {
    pub code: String,
    pub message: String,
}

/// 解析 HH:MM 为分钟数；空串 → default；格式错误抛 INVALID_TIME（对齐 parse_time）。
pub fn parse_time(value: &str, default: i32) -> Result<i32, ValidationError> {
    let value = value.trim();
    if value.is_empty() {
        return Ok(default);
    }
    let parts: Vec<&str> = value.split(':').collect();
    if parts.len() != 2 {
        return Err(ValidationError {
            code: "INVALID_TIME".into(),
            message: format!("无效时间: {value}，应为 HH:MM"),
        });
    }
    let hour: i32 = match parts[0].parse() {
        Ok(h) => h,
        Err(_) => {
            return Err(ValidationError {
                code: "INVALID_TIME".into(),
                message: format!("无效时间: {value}，应为 HH:MM"),
            });
        }
    };
    let minute: i32 = match parts[1].parse() {
        Ok(m) => m,
        Err(_) => {
            return Err(ValidationError {
                code: "INVALID_TIME".into(),
                message: format!("无效时间: {value}，应为 HH:MM"),
            });
        }
    };
    if hour > 23 || minute > 59 {
        return Err(ValidationError {
            code: "INVALID_TIME".into(),
            message: format!("无效时间: {value}，应为 HH:MM"),
        });
    }
    Ok(hour * 60 + minute)
}

/// 有界整数参数（对齐 parse_bounded_int：空串 → default；非整数/越界抛错）。
pub fn parse_bounded_int(
    value: &str,
    name: &str,
    minimum: i32,
    maximum: i32,
    default: i32,
) -> Result<i32, ValidationError> {
    let value = value.trim();
    if value.is_empty() {
        return Ok(default);
    }
    let parsed: i32 = match value.parse() {
        Ok(v) => v,
        Err(_) => {
            return Err(ValidationError {
                code: format!("INVALID_{}", name.to_uppercase()),
                message: format!("{name} 必须是整数"),
            });
        }
    };
    if parsed < minimum || parsed > maximum {
        return Err(ValidationError {
            code: format!("INVALID_{}", name.to_uppercase()),
            message: format!("{name} 必须在 {minimum}–{maximum} 之间"),
        });
    }
    Ok(parsed)
}

/// 将 HTTP 查询参数转换为 SearchRequest（对齐 build_search_request）。
pub fn build_search_request(params: &HashMap<String, String>) -> Result<SearchRequest, ValidationError> {
    let get = |keys: &[&str]| -> String {
        for k in keys {
            if let Some(v) = params.get(*k) {
                if !v.is_empty() {
                    return v.clone();
                }
            }
        }
        String::new()
    };

    let from_q = get(&["from", "from_station"]);
    let to_q = get(&["to", "to_station"]);
    if from_q.is_empty() || to_q.is_empty() {
        return Err(ValidationError {
            code: "MISSING_STATION".into(),
            message: "缺少出发站或目的站".into(),
        });
    }

    let match_mode = get(&["match_mode"]);
    let match_mode = if match_mode.is_empty() { "fuzzy".to_string() } else { match_mode };
    if match_mode != "exact" && match_mode != "fuzzy" {
        return Err(ValidationError {
            code: "INVALID_MATCH_MODE".into(),
            message: "match_mode 必须是 exact 或 fuzzy".into(),
        });
    }

    let from_mode = get(&["from_mode"]);
    let to_mode = get(&["to_mode"]);
    for (name, val) in [("from_mode", from_mode.as_str()), ("to_mode", to_mode.as_str())] {
        if !val.is_empty() && val != "exact" && val != "fuzzy" {
            return Err(ValidationError {
                code: "INVALID_MATCH_MODE".into(),
                message: format!("{name} 必须是 exact 或 fuzzy"),
            });
        }
    }

    let profile = get(&["search_profile"]);
    let profile = if profile.is_empty() { "balanced".to_string() } else { profile };
    if !["fast", "balanced", "thorough", "complete"].contains(&profile.as_str()) {
        return Err(ValidationError {
            code: "INVALID_SEARCH_PROFILE".into(),
            message: "search_profile 必须是 fast/balanced/thorough/complete".into(),
        });
    }

    let dep_after = parse_time(&get(&["dep_after"]), 0)?;
    let dep_before = parse_time(&get(&["dep_before"]), 2880)?;
    let arr_after = parse_time(&get(&["arr_after"]), 0)?;
    let arr_before = parse_time(&get(&["arr_before"]), 5760)?;
    let same = parse_bounded_int(&get(&["same_transfer"]), "same_transfer", 0, 1440, 15)?;
    let inter = parse_bounded_int(&get(&["inter_transfer"]), "inter_transfer", 0, 1440, 60)?;
    let max_transfers = parse_bounded_int(&get(&["max_transfers"]), "max_transfers", 0, 10, 3)?;
    let timeout = parse_bounded_int(&get(&["timeout"]), "timeout", 1, 600, 30)?;

    let transfer_city = get(&["transfer_city", "xfer_at"]);

    Ok(SearchRequest {
        from_query: from_q,
        to_query: to_q,
        match_mode,
        from_mode: if from_mode.is_empty() { None } else { Some(from_mode) },
        to_mode: if to_mode.is_empty() { None } else { Some(to_mode) },
        search_profile: profile,
        earliest_depart: dep_after,
        latest_depart: dep_before,
        earliest_arrive: arr_after,
        latest_arrive: arr_before,
        same_station_transfer_minutes: same,
        interstation_transfer_minutes: inter,
        max_transfers: max_transfers as usize,
        transfer_city_code: if transfer_city.is_empty() { None } else { Some(transfer_city) },
        timeout_seconds: timeout as u64,
    })
}
