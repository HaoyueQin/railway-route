# -*- coding: utf-8 -*-
"""updater 模块测试：版本对比 + 更新检查/下载（本地 mock 服务器）。"""
import json
import os
import sys
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.updater import cmp_version, download, fetch_latest

FAKE_BYTES = b"FAKE-INSTALLER-BYTES" * 4000  # 80KB


class _MockAPI(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/latest":
            body = json.dumps({
                "tag_name": "v2.0.0",
                "body": "更新说明",
                "assets": [{"name": "railway-route-setup.exe",
                            "browser_download_url": f"http://127.0.0.1:{_MockAPI.port}/pkg.exe"}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/pkg.exe":
            self.send_response(200)
            self.send_header("Content-Length", str(len(FAKE_BYTES)))
            self.end_headers()
            self.wfile.write(FAKE_BYTES)
        elif self.path == "/none":
            self.send_response(404)
            self.end_headers()
        else:
            self.send_response(500)
            self.end_headers()

    def log_message(self, *a):
        pass


class UpdaterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _MockAPI)
        _MockAPI.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _url(self, path):
        return f"http://127.0.0.1:{_MockAPI.port}{path}"

    # ── cmp_version ──
    def test_cmp_version_basic(self):
        cases = [
            ("1.0.0", "1.0.0", 0), ("v1.2.0", "1.2.0", 0),
            ("1.0.0", "1.0.1", -1), ("1.0", "1.0.0", 0),
            ("1.1.0", "1.0.9", 1), ("2.0.0", "1.9.9", 1),
            ("0.9", "1.0", -1), ("V1.0.0", "1.0.1", -1),
        ]
        for a, b, want in cases:
            self.assertEqual(cmp_version(a, b), want, (a, b))

    # ── fetch_latest ──
    def test_fetch_latest_ok(self):
        with mock.patch.dict(os.environ, {"RAILWAY_ROUTE_UPDATE_URL": self._url("/latest")}):
            info = fetch_latest()
        self.assertEqual(info["status"], "ok")
        self.assertEqual(info["latest"], "2.0.0")
        self.assertEqual(info["notes"], "更新说明")
        self.assertTrue(info["url"].endswith("/pkg.exe"))

    def test_fetch_latest_no_release(self):
        with mock.patch.dict(os.environ, {"RAILWAY_ROUTE_UPDATE_URL": self._url("/none")}):
            info = fetch_latest()
        self.assertEqual(info, {"status": "no-release"})

    def test_fetch_latest_network_error(self):
        with mock.patch.dict(os.environ, {"RAILWAY_ROUTE_UPDATE_URL": "http://127.0.0.1:59999/latest"}):
            info = fetch_latest(timeout=3)
        self.assertEqual(info["status"], "err")
        self.assertTrue(info["message"])

    # ── download ──
    def test_download_progress(self):
        progress = []
        d = download(self._url("/pkg.exe"),
                     on_progress=lambda g, t: progress.append((g, t)))
        self.assertEqual(d["status"], "ok")
        self.assertTrue(os.path.exists(d["path"]))
        self.assertEqual(progress[-1][1], len(FAKE_BYTES))
        self.assertEqual(progress[-1][0], len(FAKE_BYTES))
        self.assertGreaterEqual(len(progress), 1)
        os.remove(d["path"])


if __name__ == "__main__":
    unittest.main()
