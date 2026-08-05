# 捕获 railway-route 主窗口截图（GUI 冒烟测试用）
# 用法: powershell -ExecutionPolicy Bypass -File tools/capture_window.ps1 <输出png> [窗口标题前缀]
param(
    [string]$Out = "$PSScriptRoot\..\gui-test-screenshots\smoke_initial.png",
    [string]$Title = "铁路出行路径规划"
)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")] public static extern IntPtr FindWindow(string cls, string title);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
[Win32]::SetProcessDPIAware() | Out-Null
$h = [Win32]::FindWindow($null, $Title)
if ($h -eq [IntPtr]::Zero) {
    # 按标题前缀模糊找（可能带版本后缀）
    Get-Process railway-route -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.MainWindowTitle -like "$Title*") { $h = $_.MainWindowHandle }
    }
}
if ($h -eq [IntPtr]::Zero) { Write-Error "未找到窗口 $Title"; exit 1 }
[Win32]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 400
$r = New-Object Win32+RECT
[Win32]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $hh = $r.Bottom - $r.Top
Write-Host "窗口: ${w}x${hh} @ ($($r.Left),$($r.Top))"
$bmp = New-Object System.Drawing.Bitmap($w, $hh)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Host "已保存 $Out"
