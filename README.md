# 铁路出行路径规划 — railway-route-planner

基于路路通离线时刻表数据的本地铁路出行路径规划器。项目使用自定义多源/多目标 CSA（Connection Scan Algorithm）变体 + 轮次化多标签（RAPTOR 风格），在全国时刻表上生成直达和多次换乘候选，提供 **CLI / 本地 Web GUI / 桌面应用（pywebview）** 三种使用方式。

> 当前是学习/研究阶段的成果，不查询余票，不处理正晚点，也不应直接作为真实出行决策依据。

## 当前能力

- 从路路通离线数据导出全国车次站次 CSV；
- 构建以车站为节点、相邻停站区间为运行记录的铁路图；
- **直达优先**：独立直达枚举不受剪枝/截断影响——直达方案永远完整且排在换乘之前；
- **多源/多目标搜索**：`exact` 模式严格单站，`fuzzy` 模式扩展为同城全部有效铁路站；每端可独立设置模式（半精确半模糊）；
- **类型化路径段**：`TrainSegment`（铁路区段）与 `InterstationTransferSegment`（地面异站移动）独立建模；
- **城市级 `transfer_at` 约束**：指定换乘城市，仅返回在该城市换乘的路线（约束时剪枝自动放宽）；
- **四档搜索模式**：`fast` / `balanced` / `thorough` / `complete`；超时/状态上限中止时标记 `complete=false`，前端显示"搜索未完整"；
- **换乘语义贴近乘客**：最大换乘次数计入地面换乘；同车次跨轮自环排除；跨日时间统一"次日/N日"展示；
- **匹配规则贴近生活**：城市名→全市；区→所属市（怀柔→北京）；县/镇班次充足→单站（新县/燕郊）；班次稀疏→扩散（广阳→廊坊）；
- **玻璃态 Web 前端**：多色流动背景（Canvas 实时渲染）、宋体标题层 + 黑体正文 + 数字等宽、内联 SVG 图标（零 emoji）、时:分滚轮控件（整值顿挫动效）、六维排序与筛选；
- **桌面应用**：pywebview frameless 无边框窗口 + 自绘标题栏 + 自定义应用图标，可打包独立 exe；
- **查询缓存**：内存 LRU + SQLite 持久化（数据指纹失效）；
- **80 个确定性 `unittest` 测试** + A/B 双实现验证 + 210 组合乘客视角 QA，核心运行代码仅用 Python 标准库。

## 重要限制

- 搜索结果经过剪枝和状态上限的**候选集合**，非完备枚举（complete 模式取消启发式剪枝但仍有硬边界）；
- 异站换乘时间当前统一使用用户配置值（默认 60 分钟），未基于实际距离；
- **无票价数据**：离线数据不含票价；
- 不考虑车次开行日期、余票、席位和实时晚点；
- 个别车次里程/时刻字段存疑（数据本身问题，算法忠实呈现）；
- 评分是候选集内归一化线性加权，非机器学习排序。

完整架构、数据口径、风险与推进路线见本地 `HANDOVER.md`（被 `.gitignore` 忽略，不随普通提交进入仓库）。

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

- Python 3.10 或更高版本；
- 项目数据已放在 `data/timetable/` 和 `data/output/`；
- 可选：`pip install pywebview`（桌面窗口模式）、`pip install pyinstaller`（打包 exe）；缺失时自动回退浏览器模式。

## 快速开始

```bash
cd "D:\Project\railway route"

# 桌面应用（推荐）：frameless 自绘窗口 + 应用图标
python src/main.py --app

# 本地 Web GUI（浏览器）
python src/main.py --gui --port 8000

# 打包独立 exe（自带图标，产物 dist/铁路出行路径规划.exe）
python tools/build_app.py

# CLI 查询
python src/main.py 北京 上海 --max 5
python src/main.py 北京南 上海虹桥 --match-mode exact --max 5
python src/main.py 延安 深圳北 --search-profile complete --timeout 30
```

### CLI 参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `from_station` | 曲阜东 | 出发站输入 |
| `to_station` | 广州南 | 目的站输入 |
| `--match-mode` | fuzzy | 匹配模式：`exact` 单站 / `fuzzy` 同城扩展 |
| `--from-mode` / `--to-mode` | 跟随全局 | 每端独立匹配模式 |
| `--search-profile` | balanced | 搜索强度：`fast` / `balanced` / `thorough` / `complete` |
| `--depart-after` / `--depart-before` | 空 | 首次发车时间窗 |
| `--arrive-before` | 空 | 最终到达不晚于 |
| `--same-transfer` | 15 | 同站换乘缓冲（分钟） |
| `--inter-transfer` | 60 | 异站换乘缓冲（分钟） |
| `--max-transfers` | 3 | 最大换乘次数（列车+地面） |
| `--timeout` | 30 | 搜索超时秒数 |
| `--max` | 15 | CLI 最多展示条数 |
| `--app` | false | 桌面应用模式（pywebview，缺失时回退浏览器） |
| `--gui` | false | 浏览器 GUI 模式 |
| `--port` | 8000 | 监听端口 |

## 当前实测

以下为 2026-08-01 当前数据和代码在开发笔记本上的代表性单次运行结果（机器负载会影响数字）：

| 查询 | 直达 | 结果数 | 搜索 |
|---|---|---|---:|
| 北京南→上海虹桥 (exact, fast) | 41 | 49 | 0.8s |
| 北京南→上海虹桥 (exact, balanced) | 41 | 64 | 1.4s（完整） |
| 北京→上海 (fuzzy, balanced) | 56 | 289 | 2.2s（完整） |
| 延安→深圳北 (fuzzy, complete) | 0 | 54 | 9.1s（完整） |
| 乌鲁木齐→北京 (fuzzy, balanced) | 2 | 514 | 0.9s |

正确性验证：`tools/ab_verify.py`（桶化版 vs 全量扫描参考实现，7 个代表查询 miss=0/extra=0）、`tools/qa_sweep.py`（**210 组合 × 16 类检查 = 1857 项 0 失败**，含直达完整性对照独立枚举 ground truth）、`tools/manual_review.py`（210 组合可读输出人工审视）。重复查询命中缓存 ~0.02s。内存占用约 170MB（RSS）。

结果和耗时会随时刻表版本、机器、搜索模式和剪枝规则变化，不是稳定性能承诺。

## 项目结构

```text
railway route/
├── README.md                    # 本文件
├── HANDOVER.md                  # 本地完整交接文档，被 Git 忽略
├── assets/
│   └── icon.ico                 # 应用图标（tools/make_icon.py 生成）
├── data/                        # 时刻表数据，被 Git 忽略
├── tools/
│   ├── parse_timetable.py       # 二进制解析与 CSV 导出
│   ├── benchmark.py             # 性能基准
│   ├── ab_verify.py             # A/B 双实现一致性验证
│   ├── qa_sweep.py              # 210 组合乘客视角全链路检查
│   ├── manual_review.py         # 人工审视可读输出
│   ├── make_icon.py             # 应用图标生成
│   └── build_app.py             # PyInstaller 打包 exe
├── src/
│   ├── graph.py                 # 图、索引、双日扫描缓存、反向 Dijkstra 下界
│   ├── csa.py                   # 多源/多目标 CSA 搜索核心 + 独立直达枚举
│   ├── matcher.py               # 输入匹配与站点集合解析（规则 v3）
│   ├── models.py                # 统一搜索请求、路径段、结果与元数据模型
│   ├── validation.py            # CLI 与 API 共用参数校验
│   ├── main.py                  # CLI、HTTP API、GUI 与桌面应用启动
│   ├── cache.py                 # 查询缓存（LRU + SQLite）
│   └── score.py                 # 结果评分
├── web/
│   ├── index.html               # GUI 页面骨架（含桌面应用自绘标题栏）
│   ├── styles.css               # 玻璃态样式（字体体系/滚轮/流动背景）
│   └── app.js                   # GUI 行为（搜索/渲染/排序筛选/滚轮/标题栏）
└── tests/                       # 80 个 unittest（核心语义 + 前端契约）
```

## 验证

```bash
# 全部 80 个确定性测试
python -m unittest discover -s tests -q

# A/B 双实现一致性
python tools/ab_verify.py

# 210 组合乘客视角 QA
python tools/qa_sweep.py

# 性能基准
python tools/benchmark.py
```

## 常见问题

### 桌面应用窗口如何关闭/最小化？

窗口无系统边框（frameless），使用左上角自绘标题栏：右侧「—」最小化、「×」关闭；拖动标题栏移动窗口。

### 没有安装 pywebview 会怎样？

`--app` 自动回退浏览器模式，功能不变。安装 `pip install pywebview` 后即为桌面窗口。

### 端口 8000 被占用或无法启动

```bash
python src/main.py --gui --port 8765
```

部分 Windows 机器的 Hyper-V 会把一段端口列入排除范围，8000 可能无法绑定。可用 `netsh interface ipv4 show excludedportrange protocol=tcp` 检查，换一个空闲端口即可。

## 近期路线

1. 基于车站坐标估算异站换乘时间（替代固定 60 分钟）；
2. 完善评分配置（用户可调权重）；
3. 开行日期过滤；
4. exe 数字签名（消除 SmartScreen 提示）。

## 数据来源与致谢

- [路路通时刻表](http://www.lltskb.com) — 离线时刻表数据来源；
- [shandongtlb/lltskb-tools](https://github.com/shandongtlb/lltskb-tools) — 二进制格式逆向与解析参考；
- [MichealWangYZ/lltskb-rail-query](https://github.com/MichealWangYZ/lltskb-rail-query) — 格式资料与工具参考。

数据版权归相应权利方所有。本项目仅供个人学习和研究，不保证数据完整性、实时性、可售性或路径建议适合真实出行。
