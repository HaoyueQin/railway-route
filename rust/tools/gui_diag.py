#!/usr/bin/env python3
"""诊断：点击指定坐标后截图，验证焦点/命中。用法: python rust/tools/gui_diag.py <lx> <ly> [输出]
"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gui_smoke import find_window, capture, RECT  # noqa: E402

user32 = ctypes.windll.user32
lx = int(sys.argv[1])
ly = int(sys.argv[2])
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("gui-test-screenshots/diag.png")

hwnd = find_window()
user32.SetProcessDPIAware()
user32.ShowWindow(hwnd, 9)
user32.keybd_event(0x12, 0, 0, 0)
user32.SetForegroundWindow(hwnd)
user32.keybd_event(0x12, 0, 2, 0)
user32.BringWindowToTop(hwnd)
time.sleep(0.5)
r = RECT()
user32.GetWindowRect(hwnd, ctypes.byref(r))
scale = (r.right - r.left) / 1280.0
ax, ay = r.left + int(lx * scale), r.top + int(ly * scale)
print(f"窗口 {r.right-r.left}x{r.bottom-r.top} scale={scale:.2f} 点击物理 ({ax},{ay})")
user32.SetCursorPos(ax, ay)
time.sleep(0.2)
user32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.06)
user32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(1.0)
capture(hwnd, OUT)
print(f"已保存 {OUT}")
