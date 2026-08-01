// Tauri 构建脚本：处理 tauri.conf.json（窗口/打包配置 → 生成 context）。
// 即使窗口由运行时动态创建（URL 端口在 server 启动后才确定），
// tauri-build 仍负责 bundle 与资源清单的生成。
fn main() {
    tauri_build::build()
}
