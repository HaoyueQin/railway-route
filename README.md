# railway-route — 铁路出行路径规划（Rust + Tauri 版）

基于路路通离线时刻表数据的本地铁路出行路径规划器。使用自定义多源/多目标 CSA（Connection Scan Algorithm）变体 + 轮次化多标签（RAPTOR 风格），在全国时刻表上生成直达和多次换乘候选。本分支（master-v2）为 **Rust 重写 + Tauri 2 桌面应用**，毫秒级启动、单 exe 分发。

> 当前是学习/研究阶段的成果，不查询余票，不处理正晚点，也不应直接作为真实出行决策依据。
> Python 实现（含 CLI / pywebview 桌面版）在 `master` 分支保留，作为稳定基线与跨语言对拍基准。

## 当前能力

- 从路路通离线数据导出全国车次站次 CSV；
- 构建以车站为节点、相邻停站区间为运行记录的铁路图（Rust 实现，与 Python 版逐项对拍）；
- **直达优先**：独立直达枚举不受剪枝/截断影响——直达方案永远完整且排在换乘之前；
- **多源/多目标搜索**：`exact` 模式严格单站，`fuzzy` 模式扩展为同城全部有效铁路站；每端可独立设置模式；
- **类型化路径段**：铁路区段与地面异站移动独立建模；
- **城市级 `transfer_at` 约束**：指定换乘城市，仅返回在该城市换乘的路线；
- **四档搜索模式**：`fast` / `balanced` / `thorough` / `complete`；超时/状态上限中止时标记 `complete=false`；
- **匹配规则贴近生活**：城市名→全市；区→所属市（怀柔→北京）；县/镇班次充足→单站（新县/燕郊）；班次稀疏→扩散（广阳→廊坊）；"北京西"→同城全部车站；
- **玻璃态 Web 前端**：多色流动背景（Canvas 实时渲染）、内联 SVG 图标、时:分滚轮控件、直达/换乘双视图、六维排序与筛选；
- **Tauri 2 桌面应用**：frameless 无边框窗口 + 自绘标题栏三件套（最小化/最大化/关闭）+ 边缘调整大小热区，NSIS 单 exe 安装包（含数据与前端资源）；
- **跨语言对拍**：M1-M4 里程碑与 Python 参考实现（`rust/tools/pyref/`）全量对拍 0 失败。

## 重要限制

- 搜索结果经过剪枝和状态上限的**候选集合**，非完备枚举（complete 模式取消启发式剪枝但仍有硬边界）；
- 异站换乘时间当前统一使用用户配置值（默认 60 分钟），未基于实际距离；
- **无票价数据**：离线数据不含票价；
- 不考虑车次开行日期、余票、席位和实时晚点；
- 个别车次里程/时刻字段存疑（数据本身问题，算法忠实呈现）；
- 评分是候选集内归一化线性加权，非机器学习排序。

## 数据规模

| 指标 | 数量 |
|---|---:|
| 车次代码 | 14173 |
| 站次记录 | 128141 |
| 图中车站 | 3305 |
| 唯一有向相邻站对 | 17792 |
| 具体车次区间记录 | 113968 |
| 双日扫描记录 | 227936 |

## 环境要求

- Rust 1.96+（edition 2024）；
- 项目数据已放在 `data/timetable/` 和 `data/output/`（不入库，需自行获取）；
- 打包需要 `cargo install tauri-cli --version "^2"`（cargo 源建议 tuna 镜像）。

## 快速开始

```bash
cd rust

# 桌面应用（推荐）：Tauri frameless 窗口，数据/前端自动探测
cargo run --release -- --app

# 本地 Web GUI（浏览器访问 http://127.0.0.1:8800）
cargo run --release -- --serve 8800

# 全量对拍（M1 数据层 + M2 图构建 + M3 匹配搜索 + M4 HTTP API）
cargo run --release

# 打包 NSIS 安装包（产物 target/release/bundle/nsis/）
cargo tauri build
```

## 对拍与验证

`rust/tools/` 下的对拍工具链（本分支自包含，不依赖 master 的 Python 代码）：

- `pyref/`：Python 参考实现副本（master 的 `src/` 对拍子集，两分支同步修改）；
- `dump_graph_stats.py` / `dump_m3.py` / `dump_m4.py`：生成 M2/M3/M4 对拍基准（`*_baseline.json`，不入库，可重新生成）；
- 修改算法后：`python rust/tools/dump_m3.py` 重新生成基准 → `cargo run --release` 全量对拍必须 0 失败。

完整架构、数据口径、风险与推进路线见本地 `HANDOVER.md`（被 `.gitignore` 忽略，不随普通提交进入仓库）。
