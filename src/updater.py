# -*- coding: utf-8 -*-
"""自动检查更新（Python 版）。

数据源：GitHub Releases API（免 token，未认证限流 60 次/h，桌面应用频率足够）。
  GET https://api.github.com/repos/HaoyueQin/railway-route/releases/latest
  → {tag_name, body, assets[].browser_download_url}

- 版本对比：semver 三段（去 v 前缀、分段不等长）
- 网络失败 ≠ 无新版本：错误信息透传，前端区分展示
- 下载：流式 64KB 分块 + 进度回调，落盘 %TEMP%\\railway-route-setup.exe
- 代理：显式端口（127.0.0.1:PORT，本机场景 8897）或系统代理（None）
"""
import json
import os
import ssl
import tempfile
import urllib.request

REPO = "HaoyueQin/railway-route"


def _api_latest():
    """可被环境变量覆盖（测试注入 mock 服务器 / 换源），函数级读取便于运行时切换。"""
    return os.environ.get(
        "RAILWAY_ROUTE_UPDATE_URL",
        f"https://api.github.com/repos/{REPO}/releases/latest",
    )
UA = "railway-route-updater/1.0"
SETUP_NAME = "railway-route-setup.exe"


def _build_opener(proxy_port):
    """urllib opener：proxy_port 非空 → 显式代理；None → 系统代理。"""
    handlers = []
    if proxy_port:
        proxy = f"http://127.0.0.1:{proxy_port}"
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler(urllib.request.getproxies()))
    ctx = ssl.create_default_context()
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def cmp_version(a: str, b: str) -> int:
    """semver 三段比较：a < b 返回 -1；a > b 返回 1；相等返回 0。

    去 v/V 前缀、按 '.' 分段转数字比较、分段不等长时短者视为 0 补齐。
    """
    def norm(s):
        return [int(x) for x in str(s).lstrip("vV").split(".")]
    pa, pb = norm(a), norm(b)
    for i in range(max(len(pa), len(pb))):
        x = pa[i] if i < len(pa) else 0
        y = pb[i] if i < len(pb) else 0
        if x != y:
            return -1 if x < y else 1
    return 0


def fetch_latest(proxy_port=None, timeout=15):
    """拉取最新 release 信息。

    返回 {"status": "ok", "latest": tag(去 v), "notes": body, "url": 安装包直链}
         {"status": "no-release"}    仓库无 release（API 404）
         {"status": "err", "message": 具体错误}
    """
    try:
        opener = _build_opener(proxy_port)
        req = urllib.request.Request(_api_latest(), headers={
            "User-Agent": UA,
            "Accept": "application/vnd.github+json",
        })
        with opener.open(req, timeout=timeout) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "no-release"}
        return {"status": "err", "message": f"HTTP {e.code}"}
    except Exception as e:
        return {"status": "err", "message": str(e)}

    tag = str(d.get("tag_name", "")).lstrip("vV")
    url = None
    for a in d.get("assets", []):
        name = (a.get("name") or "").lower()
        if name.endswith((".exe", ".msi")) and "setup" in name or name.endswith("-setup.exe"):
            url = a.get("browser_download_url")
            break
    if not url:
        for a in d.get("assets", []):
            n = (a.get("name") or "").lower()
            if n.endswith(".exe"):
                url = a.get("browser_download_url")
                break
    return {"status": "ok", "latest": tag, "notes": d.get("body") or "",
            "url": url, "published": d.get("published_at") or ""}


def download(url, on_progress=None, proxy_port=None, timeout=60):
    """流式下载安装包到 %TEMP%\\railway-route-setup.exe。

    on_progress(downloaded_bytes, total_bytes)：分块回调（64KB）。
    返回 {"status": "ok", "path": 本地路径} 或 {"status": "err", "message"}。
    """
    path = os.path.join(tempfile.gettempdir(), SETUP_NAME)
    try:
        opener = _build_opener(proxy_port)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with opener.open(req, timeout=timeout) as r:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            with open(path, "wb") as f:
                while True:
                    chunk = r.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if on_progress:
                        on_progress(got, total)
        return {"status": "ok", "path": path}
    except Exception as e:
        return {"status": "err", "message": str(e)}
