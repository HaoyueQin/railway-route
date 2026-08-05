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
    // 安全：非 2xx（限流页/错误页）不落盘，避免把垃圾内容交给安装器
    if !resp.status().is_success() {
        return Err(format!("下载失败: HTTP {}", resp.status().as_u16()));
    }
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

/// 计算文件 SHA-256（hex 小写）。
pub fn sha256_hex(path: &std::path::Path) -> Result<String, String> {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    let mut f = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut buf = [0u8; 64 * 1024];
    loop {
        let n = f.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hex_lower(&hasher.finalize()))
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0x0f) as usize] as char);
    }
    s
}

/// 从 sha256sum 文本（`<64hex>  <文件名>`）中提取校验值；格式无效 → None。
pub fn parse_sha256_text(text: &str) -> Option<String> {
    text.split_whitespace()
        .find(|w| w.len() == 64 && w.chars().all(|c| c.is_ascii_hexdigit()))
        .map(|w| w.to_ascii_lowercase())
}

/// 拉取安装包同名的 `.sha256` sidecar 文件（gh release 发布时生成）。
/// 404 → Ok(None)（旧 release 无校验信息，向后兼容）；其他失败 → Err。
pub fn fetch_sha256(url: &str, proxy_port: &str) -> Result<Option<String>, String> {
    let client = build_client(proxy_port);
    let resp = client
        .get(format!("{url}.sha256"))
        .send()
        .map_err(|e| e.to_string())?;
    if resp.status() == reqwest::StatusCode::NOT_FOUND {
        return Ok(None);
    }
    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status().as_u16()));
    }
    let text = resp.text().map_err(|e| e.to_string())?;
    // 格式：`<64hex>  <文件名>`（sha256sum 输出）；取第一个 64 位 hex 词
    match parse_sha256_text(&text) {
        Some(h) => Ok(Some(h)),
        None => Err(format!("校验文件格式无效: {text:.100}")),
    }
}

/// 下载 + SHA256 完整性校验：expected 为 None（旧 release 无校验信息）时
/// **拒绝自动安装**（完整性无法保障，降级为提示用户手动下载）。
/// 校验失败会删除已下载文件并返回错误（防中间人/损坏）。
pub fn download_verified(
    url: &str,
    proxy_port: &str,
    expected: Option<&str>,
    on_progress: &dyn Fn(u64, u64),
) -> Result<std::path::PathBuf, String> {
    let path = download(url, proxy_port, on_progress)?;
    match expected {
        Some(hex) => {
            let actual = sha256_hex(&path)?;
            if !actual.eq_ignore_ascii_case(hex) {
                let _ = std::fs::remove_file(&path);
                return Err(format!(
                    "下载完整性校验失败（SHA256 不匹配，预期 {hex}，实际 {actual}），已删除文件，请重试"
                ));
            }
            Ok(path)
        }
        None => {
            let _ = std::fs::remove_file(&path);
            Err("该版本无 SHA256 校验信息，为安全起见已取消自动安装，请从官网手动下载".into())
        }
    }
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
    let url = info.url.clone();
    std::thread::spawn(move || {
        // 5.2-4 下载完整性校验：先拉同名 .sha256 sidecar（404 视为旧 release 无校验信息，跳过）
        let expected = match fetch_sha256(&url, &proxy_port) {
            Ok(v) => v,
            Err(e) => {
                if let Ok(mut p) = progress_arc.lock() {
                    p.state = "err".into();
                    p.message = format!("校验文件获取失败：{e}");
                }
                return;
            }
        };
        let result = download_verified(&url, &proxy_port, expected.as_deref(), &|got, total| {
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

    #[test]
    fn sha256_known_vector() {
        // "abc" 的 SHA-256（FIPS 180-4 标准向量）
        let dir = std::env::temp_dir();
        let p = dir.join("updater_sha_test.txt");
        std::fs::write(&p, b"abc").unwrap();
        let hex = sha256_hex(&p).unwrap();
        std::fs::remove_file(&p).ok();
        assert_eq!(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn sha256_differs_on_content_change() {
        let dir = std::env::temp_dir();
        let p = dir.join("updater_sha_test2.bin");
        std::fs::write(&p, vec![0u8; 1024]).unwrap();
        let a = sha256_hex(&p).unwrap();
        std::fs::write(&p, vec![1u8; 1024]).unwrap();
        let b = sha256_hex(&p).unwrap();
        std::fs::remove_file(&p).ok();
        assert_ne!(a, b);
    }

    #[test]
    fn parse_sha256_line_formats() {
        let hex = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        assert_eq!(
            parse_sha256_text(&format!("{hex}  railway-route_0.1.0_x64-setup.exe")),
            Some(hex.to_string())
        );
        assert_eq!(parse_sha256_text(&format!("{hex} *file.bin")), Some(hex.to_string()));
        assert_eq!(parse_sha256_text("abc"), None);
        assert_eq!(parse_sha256_text(""), None);
        assert_eq!(parse_sha256_text(&format!("{hex}abc extra")), None); // 65 hex 不是合法词
    }
}
