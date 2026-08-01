"""打包桌面应用：PyInstaller onefile + 自定义应用图标。

产物: dist/railway-route.exe（双击即启动桌面应用，自带图标）

用法: python tools/build_app.py
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "assets" / "icon.ico"
SEP = os.pathsep  # Windows 为 ';'


def main():
    if not ICON.exists():
        sys.exit("缺少 assets/icon.ico——先运行 python tools/make_icon.py")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--noconsole",  # windowed：双击不弹命令行窗口（错误写 %LOCALAPPDATA%\railway-route\app.log）
        "--name", "railway-route",
        "--icon", str(ICON),
        # 前端与数据随包分发（frozen 时 _base_dir() 指向解压目录）
        "--add-data", f"web{SEP}web",
        "--add-data", f"data/output{SEP}data/output",
        "--add-data", f"data/timetable{SEP}data/timetable",
        # pywebview 后端按需加载，需显式收集
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "webview.platforms.winforms",
        str(ROOT / "src" / "main.py"),
    ]
    print("打包中（PyInstaller onefile）...")
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"\n完成: {ROOT / 'dist' / 'railway-route.exe'}")


if __name__ == "__main__":
    main()
