#!/usr/bin/env python3
"""GUI 全流程冒烟：输入出发/目的站 → 点击搜索 → 截图验证结果。

用法: python rust/tools/gui_search_flow.py [from站] [to站] [输出png]
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gui_smoke import find_window, capture, RECT  # noqa: E402

user32 = ctypes.windll.user32

FROM = sys.argv[1] if len(sys.argv) > 1 else "曲阜东"
TO = sys.argv[2] if len(sys.argv) > 2 else "广州南"
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("gui-test-screenshots/smoke_search2.png")


def set_clipboard(text: str):
    """设置剪贴板文本（PowerShell Set-Clipboard，规避剪贴板锁竞争）。"""
    import subprocess
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Set-Clipboard -Value @'{text}'"],
        check=False, timeout=10)


def click_abs(x: int, y: int):
    user32.SetCursorPos(x, y)
    time.sleep(0.15)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.3)


def send_ctrl_v():
    """Ctrl+V 粘贴（keybd_event）。"""
    VK_CONTROL = 0x11
    VK_V = 0x56
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 2, 0)  # KEYEVENTF_KEYUP
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.4)


def main() -> int:
    hwnd = find_window()
    if not hwnd:
        return 1
    user32.SetProcessDPIAware()
    user32.ShowWindow(hwnd, 9)
    # Alt 键技巧激活前台（Windows 前台锁定：直接 SetForegroundWindow 对后台进程常失败）
    user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU down
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)  # VK_MENU up
    user32.BringWindowToTop(hwnd)
    time.sleep(0.6)
    fg = user32.GetForegroundWindow()
    print(f"前台窗口: {fg} (目标 {hwnd}) {'✓' if fg == hwnd else '✗ 未激活'}")
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    ox, oy = r.left, r.top
    # 逻辑 1280x880 → 物理 = 逻辑 × 1.5（屏幕 DPI 150%）
    scale = (r.right - r.left) / 1280.0
    print(f"窗口 @ ({ox},{oy}) scale={scale:.2f}")

    def pt(lx, ly):
        return int(ox + lx * scale), int(oy + ly * scale)

    # 1. 出发站输入框（逻辑 ~330,330）→ 粘贴 FROM
    fx, fy = pt(330, 330)
    click_abs(fx, fy)
    set_clipboard(FROM)
    send_ctrl_v()
    # 2. 目的站输入框（逻辑 ~660,330）→ 粘贴 TO
    tx, ty = pt(660, 330)
    click_abs(tx, ty)
    set_clipboard(TO)
    send_ctrl_v()
    time.sleep(0.5)
    # 3. 搜索按钮（逻辑 ~1120,330）
    sx, sy = pt(1120, 330)
    click_abs(sx, sy)
    print(f"已输入 {FROM}→{TO} 并点击搜索")
    time.sleep(6)
    w, h = capture(hwnd, OUT)
    print(f"已保存 {OUT} ({w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
