//! 零依赖手写 HTTP/1.1 服务器（对齐 Python APIHandler 的路由与静态文件服务）。
//!
//! 支持 GET /api/search /api/match /api/train + 静态文件（/ /styles.css /app.js）。
//! query 参数 percent-decode（UTF-8）+ 号 → 空格（与 parse_qs 一致）。

use crate::api;
use crate::graph::Graph;
use crate::matcher::MatcherData;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};

/// percent-decode（对齐 urllib.parse.unquote_plus：+ → 空格，%XX → 字节，UTF-8）。
pub fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' if i + 2 < bytes.len() => {
                let hex = &s[i + 1..i + 3];
                if let Ok(v) = u8::from_str_radix(hex, 16) {
                    out.push(v);
                    i += 3;
                    continue;
                }
                out.push(b'%');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// 解析 query string → 参数表（空值保留；重复键取首个，与 parse_qs 取 [0] 一致）。
pub fn parse_query(qs: &str) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for pair in qs.split('&') {
        if pair.is_empty() {
            continue;
        }
        let (k, v) = match pair.split_once('=') {
            Some((k, v)) => (k, v),
            None => (pair, ""),
        };
        let k = percent_decode(k);
        let v = percent_decode(v);
        map.entry(k).or_insert(v);
    }
    map
}

fn send_response(stream: &mut TcpStream, status: u16, content_type: &str, body: &[u8]) {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        404 => "Not Found",
        500 => "Internal Server Error",
        _ => "Unknown",
    };
    let head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(body);
    let _ = stream.flush();
}

fn handle_connection(
    stream: &mut TcpStream,
    graph: &Graph,
    matcher: &MatcherData,
    web_dir: &std::path::Path,
) {
    let mut buf = Vec::with_capacity(4096);
    let mut chunk = [0u8; 4096];
    loop {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => {
                buf.extend_from_slice(&chunk[..n]);
                if buf.windows(4).any(|w| w == b"\r\n\r\n") {
                    break;
                }
                if buf.len() > 64 * 1024 {
                    break;
                }
            }
            Err(_) => return,
        }
    }
    let text = String::from_utf8_lossy(&buf);
    let request_line = text.lines().next().unwrap_or("");
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("");
    let target = parts.next().unwrap_or("/");
    if method != "GET" {
        send_response(stream, 404, "text/plain; charset=utf-8", b"Not Found");
        return;
    }
    let (path, qs) = match target.split_once('?') {
        Some((p, q)) => (p, Some(q)),
        None => (target, None),
    };
    let query = qs.map(parse_query).unwrap_or_default();

    // 路由（对齐 APIHandler.do_GET）
    let (status, content_type, body): (u16, String, Vec<u8>) = match path {
        "/api/search" => {
            let (status, payload) = api::api_search(graph, matcher, &query);
            let body = payload.to_string().into_bytes();
            (status, "application/json; charset=utf-8".to_string(), body)
        }
        "/api/match" => {
            let (status, payload) = api::api_match(graph, matcher, &query);
            let body = payload.to_string().into_bytes();
            (status, "application/json; charset=utf-8".to_string(), body)
        }
        "/api/train" => {
            let (status, payload) = api::api_train(graph, &query);
            let body = payload.to_string().into_bytes();
            (status, "application/json; charset=utf-8".to_string(), body)
        }
        "/" | "/index.html" => serve_static(web_dir, "index.html"),
        "/styles.css" => serve_static(web_dir, "styles.css"),
        "/app.js" => serve_static(web_dir, "app.js"),
        _ => (404, "text/plain; charset=utf-8".to_string(), b"Not Found".to_vec()),
    };
    send_response(stream, status, &content_type, &body);
}

fn serve_static(web_dir: &std::path::Path, name: &str) -> (u16, String, Vec<u8>) {
    let path = web_dir.join(name);
    let content_type = match name.rsplit('.').next().unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "js" => "application/javascript; charset=utf-8",
        _ => "application/octet-stream",
    };
    match std::fs::read(&path) {
        Ok(data) => (200, content_type.to_string(), data),
        Err(_) => (404, "text/plain; charset=utf-8".to_string(), b"Not Found".to_vec()),
    }
}

/// 启动 HTTP 服务（阻塞；指定端口被占用则递增，超 100 次后端口 0 兜底，对齐 _start_server）。
/// `on_ready` 在端口确定后立即回调（serve 本身阻塞在请求循环，
/// 调用方需要端口时（如 Tauri 窗口 URL）必须用回调而非等待返回值）。
pub fn serve_with_cb(
    graph: &Graph,
    matcher: &MatcherData,
    web_dir: &std::path::Path,
    port: u16,
    on_ready: impl FnOnce(u16),
) -> std::io::Result<()> {
    let listener = match TcpListener::bind(("127.0.0.1", port)) {
        Ok(l) => l,
        Err(_) => (port + 1..port.saturating_add(100))
            .find_map(|p| TcpListener::bind(("127.0.0.1", p)).ok())
            .or_else(|| TcpListener::bind(("127.0.0.1", 0)).ok())
            .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::AddrInUse, "端口均被占用"))?,
    };
    let actual = listener.local_addr()?.port();
    println!("HTTP 服务已启动: http://127.0.0.1:{actual}");
    on_ready(actual);
    for stream in listener.incoming() {
        match stream {
            Ok(mut s) => handle_connection(&mut s, graph, matcher, web_dir),
            Err(_) => continue,
        }
    }
    Ok(())
}

/// 启动 HTTP 服务（阻塞；端口在内部打印，调用方无需等待返回值）。
pub fn serve(
    graph: &Graph,
    matcher: &MatcherData,
    web_dir: &std::path::Path,
    port: u16,
) -> std::io::Result<u16> {
    let (tx, rx) = std::sync::mpsc::channel();
    serve_with_cb(graph, matcher, web_dir, port, move |p| {
        let _ = tx.send(p);
    })?;
    Ok(rx.recv().unwrap_or(0))
}
