//! 自动检查更新（Tauri 版）——对齐 Python 版 src/updater.py 的契约。
//!
//! 数据源：GitHub Releases API（免 token）。
//!   GET https://api.github.com/repos/HaoyueQin/railway-route/releases/latest
//!   → {tag_name, body, assets[].browser_download_url}
//!
//! 前端经 IPC 调用三个命令（与 pywebview 版同契约，前端共用一套轮询逻辑）：
//!   check_update(proxy_port)          → Ok(Some(info)) / Ok(None) / Err(msg)
//!   download_update(proxy_port)       → 后台线程流式下载 → 启动 NSIS 安装器
//!   get_download_progress()           → {state, downloaded, total, message}
//!
//! 代理：proxy_port 非空 → 显式代理；空 → 环境变量 HTTP(S)_PROXY；再无 → 直连。

use serde::{Deserialize, Serialize};
use std::io::Read;
use std::sync::Mutex;

/// 远端 release 信息（仅取用字段）。
#[derive(Deserialize)]
struct ReleaseInfo {
    tag_name: String,
    body: Option<String>,
    assets: Vec<Asset>,
}

#[derive(Deserialize)]
struct Asset {
    name: String,
    browser_download_url: String,
}

/// 返回给前端的更新信息。
#[derive(Clone, Serialize)]
pub struct UpdateInfo {
    pub version: String,
    pub notes: String,
    pub url: String,
}

/// 下载进度（前端轮询）。
#[derive(Clone, Serialize, Default)]
pub struct DownloadProgress {
    pub state: String,      // idle / downloading / done / err
    pub downloaded: u64,
    pub total: u64,
    pub message: String,
}

/// 应用级共享状态：待安装的更新 + 下载进度。
pub struct UpdaterState {
    pub pending: Mutex<Option<UpdateInfo>>,
    pub progress: std::sync::Arc<Mutex<DownloadProgress>>,
}

impl UpdaterState {
    pub fn new() -> Self {
        Self {
            pending: Mutex::new(None),
            progress: std::sync::Arc::new(Mutex::new(DownloadProgress {
                state: "idle".into(),
                ..Default::default()
            })),
        }
    }
}

/// semver 三段比较（对齐 Python updater.cmp_version：去 v 前缀、分段不等长补 0）。
pub fn cmp_version(a: &str, b: &str) -> std::cmp::Ordering {
    fn norm(s: &str) -> Vec<u64> {
        s.trim_start_matches(|c| c == 'v' || c == 'V')
            .split('.')
            .filter_map(|x| x.parse().ok())
            .collect()
    }
    let (pa, pb) = (norm(a), norm(b));
    for i in 0..pa.len().max(pb.len()) {
        let x = pa.get(i).copied().unwrap_or(0);
        let y = pb.get(i).copied().unwrap_or(0);
        if x != y {
            return x.cmp(&y);
        }
    }
    std::cmp::Ordering::Equal
}

fn api_latest() -> String {
    // 可被环境变量覆盖（测试注入 mock 服务器 / 换源），与 Python 版一致
    std::env::var("RAILWAY_ROUTE_UPDATE_URL").unwrap_or_else(|_| {
        "https://api.github.com/repos/HaoyueQin/railway-route/releases/latest".into()
    })
}

fn build_client(proxy_port: &str) -> reqwest::blocking::Client {
    let mut b = reqwest::blocking::Client::builder()
        .user_agent("railway-route-updater/1.0")
        .timeout(std::time::Duration::from_secs(20));
    let proxy = if !proxy_port.trim().is_empty() {
        Some(format!("http://127.0.0.1:{}", proxy_port.trim()))
    } else {
        std::env::var("HTTP_PROXY")
            .or_else(|_| std::env::var("HTTPS_PROXY"))
            .ok()
    };
    if let Some(p) = proxy {
        b = b.proxy(reqwest::Proxy::all(p).unwrap_or_else(|_| {
            // 代理配置无效时回退直连（不能因配置问题卡死更新功能）
            reqwest::Proxy::all("http://127.0.0.1:0").expect("无效代理占位")
        }));
    }
    b.build().expect("构建 HTTP 客户端失败")
}

/// 拉取最新 release（返回 Ok(Some) 有新版 / Ok(None) 无更新 / Err 网络或解析失败）。
/// 404（无 release）也返回 Ok(None)，与 Python 版 "no-release" 语义一致。
pub fn fetch_latest(proxy_port: &str) -> Result<Option<UpdateInfo>, String> {
    let client = build_client(proxy_port);
    let resp = client
        .get(api_latest())
        .header("Accept", "application/vnd.github+json")
        .send()
        .map_err(|e| e.to_string())?;
    if resp.status() == reqwest::StatusCode::NOT_FOUND {
        return Ok(None);
    }
    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status().as_u16()));
    }
    let text = resp.text().map_err(|e| e.to_string())?;
    let info: ReleaseInfo = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    let version = info.tag_name.trim_start_matches(|c| c == 'v' || c == 'V').to_string();
    let url = info
        .assets
        .iter()
        .find(|a| a.name.ends_with("-setup.exe"))
        .or_else(|| info.assets.iter().find(|a| a.name.ends_with(".exe")))
        .map(|a| a.browser_download_url.clone());
    let Some(url) = url else {
        return Err("release 无可用安装包".into());
    };
    Ok(Some(UpdateInfo {
        version,
        notes: info.body.unwrap_or_default(),
        url,
    }))
}

/// 流式下载安装包到 %TEMP%/railway-route-setup.exe（64KB 分块 + 进度回调）。
pub fn download(url: &str, proxy_port: &str, on_progress: &dyn Fn(u64, u64)) -> Result<std::path::PathBuf, String> {
    let client = build_client(proxy_port);
    let mut resp = client
        .get(url)
        .send()
        .map_err(|e| e.to_string())?;
    let total = resp.content_length().unwrap_or(0);
    let path = std::env::temp_dir().join("railway-route-setup.exe");
    let mut file = std::fs::File::create(&path).map_err(|e| e.to_string())?;
    let mut buf = [0u8; 64 * 1024];
    let mut got: u64 = 0;
    loop {
        let n = resp.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        std::io::Write::write_all(&mut file, &buf[..n]).map_err(|e| e.to_string())?;
        got += n as u64;
        on_progress(got, total);
    }
    Ok(path)
}

/// 启动安装器（NSIS 静默更新模式；装完自动重启应用）。
pub fn launch_installer(path: &std::path::Path) {
    let _ = std::process::Command::new("cmd")
        .args(["/c", "start", "", path.to_string_lossy().as_ref()])
        .spawn();
}

// ── Tauri 命令（与 pywebview 版同契约，前端共用）────────────────

#[tauri::command]
pub fn check_update(
    state: tauri::State<'_, UpdaterState>,
    proxy_port: String,
) -> Result<Option<UpdateInfo>, String> {
    let current = env!("CARGO_PKG_VERSION");
    match fetch_latest(&proxy_port)? {
        Some(info) => {
            if cmp_version(current, &info.version) == std::cmp::Ordering::Less {
                *state.pending.lock().unwrap() = Some(info.clone());
                Ok(Some(info))
            } else {
                Ok(None)
            }
        }
        None => Ok(None),
    }
}

#[tauri::command]
pub fn download_update(
    state: tauri::State<'_, UpdaterState>,
    proxy_port: String,
) -> Result<(), String> {
    let pending = state.pending.lock().unwrap().clone();
    let Some(info) = pending else {
        return Err("没有待安装的更新（请先检查更新）".into());
    };
    {
        let mut p = state.progress.lock().unwrap();
        if p.state == "downloading" {
            return Err("已有下载任务进行中".into());
        }
        *p = DownloadProgress {
            state: "downloading".into(),
            ..Default::default()
        };
    }
    let progress_arc = std::sync::Arc::clone(&state.progress);
    std::thread::spawn(move || {
        let result = download(&info.url, &proxy_port, &|got, total| {
            if let Ok(mut p) = progress_arc.lock() {
                p.downloaded = got;
                p.total = total;
            }
        });
        match result {
            Ok(path) => {
                if let Ok(mut p) = progress_arc.lock() {
                    p.state = "done".into();
                }
                launch_installer(&path);
            }
            Err(e) => {
                if let Ok(mut p) = progress_arc.lock() {
                    p.state = "err".into();
                    p.message = e;
                }
            }
        }
    });
    Ok(())
}

#[tauri::command]
pub fn get_download_progress(state: tauri::State<'_, UpdaterState>) -> DownloadProgress {
    state.progress.lock().unwrap().clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cmp_version_basic() {
        use std::cmp::Ordering::*;
        let cases = [
            ("1.0.0", "1.0.0", Equal),
            ("v1.2.0", "1.2.0", Equal),
            ("1.0.0", "1.0.1", Less),
            ("1.0", "1.0.0", Equal),
            ("1.1.0", "1.0.9", Greater),
            ("2.0.0", "1.9.9", Greater),
            ("0.9", "1.0", Less),
            ("V1.0.0", "1.0.1", Less),
        ];
        for (a, b, want) in cases {
            assert_eq!(cmp_version(a, b), want, "{a} vs {b}");
        }
    }
}
