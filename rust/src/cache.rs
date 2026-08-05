//! 查询结果缓存（对齐 Python pyref/cache.py 的 SearchCache 内存 LRU 语义）。
//!
//! - 内存 LRU：上限 300 条目 / 80MB 总量，超限按最旧淘汰
//! - 缓存键 = 规范化请求参数 + 数据指纹（CSV mtime+size，数据更新自动失效）
//! - 命中时返回缓存 JSON 并标记 cached=true（前端可显示"缓存命中"）
//!
//! Rust 侧数据在运行时从文件加载（`_up_/` → `data/` → 仓库根 路径探测），
//! 指纹与 Python 侧同源（文件 mtime_ns + size）。

use crate::models::SearchRequest;
use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_ENTRIES: usize = 300; // 内存缓存上限（条目数）
const MAX_ENTRY_BYTES: usize = 400_000; // 单条目上限（约 400KB，防止超大结果撑爆内存）
const MAX_TOTAL_BYTES: usize = 80_000_000; // 内存缓存总量上限（80MB）

/// 线程安全由调用方保证（`Mutex<SearchCache>`）；结构自身无锁。
pub struct SearchCache {
    mem: HashMap<String, (f64, String)>, // key -> (最近访问秒, body)
    mem_bytes: usize,
}

impl SearchCache {
    pub fn new() -> Self {
        SearchCache {
            mem: HashMap::new(),
            mem_bytes: 0,
        }
    }

    pub fn get(&mut self, key: &str) -> Option<&str> {
        let hit = self.mem.get_mut(key)?;
        hit.0 = now_secs();
        Some(hit.1.as_str())
    }

    pub fn put(&mut self, key: String, body: String) {
        if body.is_empty() || body.len() > MAX_ENTRY_BYTES {
            return;
        }
        if let Some(old) = self.mem.remove(&key) {
            self.mem_bytes = self.mem_bytes.saturating_sub(old.1.len());
        }
        self.mem_bytes += body.len();
        self.mem.insert(key, (now_secs(), body));
        self.trim();
    }

    /// 内存超限时按最近使用淘汰（LRU）。
    fn trim(&mut self) {
        while self.mem.len() > MAX_ENTRIES || self.mem_bytes > MAX_TOTAL_BYTES {
            let oldest = self
                .mem
                .iter()
                .min_by(|a, b| a.1 .0.partial_cmp(&b.1 .0).unwrap_or(std::cmp::Ordering::Equal))
                .map(|(k, _)| k.clone());
            match oldest {
                Some(k) => {
                    if let Some(v) = self.mem.remove(&k) {
                        self.mem_bytes = self.mem_bytes.saturating_sub(v.1.len());
                    }
                }
                None => break,
            }
        }
    }
}

impl Default for SearchCache {
    fn default() -> Self {
        Self::new()
    }
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// 时刻表数据指纹：文件 mtime_ns + 大小（数据更新后旧缓存自动失效）。
pub fn data_fingerprint(csv_path: &Path) -> String {
    match fs::metadata(csv_path) {
        Ok(md) => {
            let mtime = md
                .modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| d.as_nanos())
                .unwrap_or(0);
            format!("{mtime}:{}", md.len())
        }
        Err(_) => "unknown".into(),
    }
}

/// 规范化请求参数 → 缓存键（字段顺序固定，None 折叠；含多站列表与数据指纹）。
pub fn request_key(req: &SearchRequest, fingerprint: &str) -> String {
    let from_stations = req
        .from_stations
        .as_ref()
        .map(|v| v.join(","))
        .unwrap_or_default();
    let to_stations = req
        .to_stations
        .as_ref()
        .map(|v| v.join(","))
        .unwrap_or_default();
    [
        req.from_query.as_str(),
        req.to_query.as_str(),
        req.match_mode.as_str(),
        req.from_mode.as_deref().unwrap_or(""),
        req.to_mode.as_deref().unwrap_or(""),
        from_stations.as_str(),
        to_stations.as_str(),
        req.search_profile.as_str(),
        &req.earliest_depart.to_string(),
        &req.latest_depart.to_string(),
        &req.earliest_arrive.to_string(),
        &req.latest_arrive.to_string(),
        &req.same_station_transfer_minutes.to_string(),
        &req.interstation_transfer_minutes.to_string(),
        &req.max_transfers.to_string(),
        req.transfer_city_code.as_deref().unwrap_or(""),
        &req.timeout_seconds.to_string(),
        fingerprint,
    ]
    .join("\x1f")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req() -> SearchRequest {
        let mut r = SearchRequest::new("北京", "上海", "balanced");
        r.interstation_transfer_minutes = 60;
        r
    }

    #[test]
    fn put_get_roundtrip() {
        let mut c = SearchCache::new();
        let k = request_key(&req(), "fp1");
        assert!(c.get(&k).is_none());
        c.put(k.clone(), "{\"routes\":[]}".into());
        assert_eq!(c.get(&k), Some("{\"routes\":[]}"));
    }

    #[test]
    fn key_differs_on_param_change() {
        let a = request_key(&req(), "fp1");
        let mut b = req();
        b.search_profile = "fast".into();
        let kb = request_key(&b, "fp1");
        assert_ne!(a, kb);
        let mut c = req();
        c.from_stations = Some(vec!["北京南".into(), "北京".into()]);
        assert_ne!(a, request_key(&c, "fp1"));
        assert_ne!(a, request_key(&req(), "fp2")); // 指纹不同 → 键不同
    }

    #[test]
    fn lru_evicts_oldest() {
        let mut c = SearchCache::new();
        c.put("k1".into(), "a".repeat(10));
        c.put("k2".into(), "b".repeat(10));
        c.put("k3".into(), "c".repeat(10));
        // 手动触发淘汰：条目数超过 MAX_ENTRIES 才会淘汰，这里直接测 trim 行为
        c.mem_bytes = MAX_TOTAL_BYTES + 1; // 强制超限
        c.put("k4".into(), "d".repeat(10));
        assert!(!c.mem.contains_key("k1"));
        assert!(c.mem.contains_key("k4"));
    }

    #[test]
    fn fingerprint_changes_with_file() {
        let dir = std::env::temp_dir();
        let p = dir.join("cache_fp_test.csv");
        std::fs::write(&p, b"a").unwrap();
        let f1 = data_fingerprint(&p);
        std::fs::write(&p, b"ab").unwrap();
        let f2 = data_fingerprint(&p);
        std::fs::remove_file(&p).ok();
        assert_ne!(f1, f2);
        assert_ne!(data_fingerprint(Path::new("NONEXISTENT")), f1);
    }
}
