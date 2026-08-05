#!/usr/bin/env python3
"""GUI 交互冒烟：点击搜索按钮 → 截图验证结果渲染。

用法: python rust/tools/gui_click.py <x> <y> <输出png>
坐标用物理像素（窗口内相对坐标）。
"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gui_smoke import find_window, RECT  # noqa: E402

user32 = ctypes.windll.user32

x = int(sys.argv[1])
y = int(sys.argv[2])
out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("gui-test-screenshots/smoke_clicked.png")

hwnd = find_window()
if not hwnd:
    sys.exit("未找到窗口")
user32.SetProcessDPIAware()
user32.SetForegroundWindow(hwnd)
user32.BringWindowToTop(hwnd)
r = RECT()
user32.GetWindowRect(hwnd, ctypes.byref(r))
abs_x, abs_y = r.left + x, r.top + y
print(f"点击 ({abs_x},{abs_y}) (窗口内 {x},{y})")

# mouse_event 左键点击（绝对坐标，需要先 SetCursorPos）
user32.SetCursorPos(abs_x, abs_y)
time.sleep(0.2)
user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
time.sleep(0.08)
user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP

time.sleep(4)  # 等待搜索完成渲染
from gui_smoke import capture  # noqa: E402

capture(hwnd, out)
print(f"已保存 {out}")
