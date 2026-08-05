#!/usr/bin/env python3
"""GUI 冒烟测试：定位 railway-route 主窗口并捕获截图（ctypes + GDI，零依赖）。

用法: python rust/tools/gui_smoke.py [输出png路径]
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("gui-test-screenshots/smoke_initial.png")


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def find_window() -> int:
    """按进程名定位 railway-route 的可见顶层主窗口句柄。"""
    import subprocess
    target_pid = 0
    for line in subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq railway-route.exe",
                                         "/FO", "CSV", "/NH"]).decode("gbk", "ignore").splitlines():
        parts = line.split('"')
        if len(parts) > 3 and "railway-route" in parts[1]:
            target_pid = int(parts[3])
            break
    if not target_pid:
        print("railway-route 进程未找到")
        return 0

    out = [0]

    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != target_pid:
            return True
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            out[0] = hwnd
            return False
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    if out[0]:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(out[0], buf, 256)
        print(f"找到窗口 hwnd={out[0]} 标题={buf.value}")
    return out[0]


def capture(hwnd: int, out: Path) -> tuple[int, int]:
    """PrintWindow 抓取窗口内容 → PNG（BGRA→RGBA，zlib 编码）。返回 (w, h)。"""
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    ok = user32.PrintWindow(hwnd, hdc_mem, 0x00000002)  # PW_RENDERFULLCONTENT
    if not ok:
        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, r.left, r.top, 0x00CC0020)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    raw = b""
    for i in range(h):
        row = bytes(buf[i * w * 4:(i + 1) * w * 4])
        rgba = bytearray(w * 4)
        for j in range(w):
            rgba[j * 4] = row[j * 4 + 2]
            rgba[j * 4 + 1] = row[j * 4 + 1]
            rgba[j * 4 + 2] = row[j * 4]
            rgba[j * 4 + 3] = row[j * 4 + 3]
        raw += b"\x00" + bytes(rgba)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)
    return w, h


def main() -> int:
    hwnd = find_window()
    if not hwnd:
        print("未找到 railway-route 主窗口")
        return 1
    user32.SetProcessDPIAware()
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.6)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    print(f"窗口: {r.right - r.left}x{r.bottom - r.top} @ ({r.left},{r.top})")
    w, h = capture(hwnd, OUT)
    print(f"已保存 {OUT} ({w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
