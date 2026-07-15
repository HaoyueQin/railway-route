# 铁路出行路径规划 — railway-route-planner

基于路路通离线时刻表数据的本地铁路出行路径规划原型。项目使用自定义多源/多目标 CSA（Connection Scan Algorithm）变体，在全国时刻表上生成直达和多次换乘候选，并通过 CLI 或本地 Web GUI 展示。

> 当前是学习/研究阶段的 MVP，不查询余票，不处理正晚点，也不应直接作为真实出行决策依据。

## 当前能力

- 从路路通离线数据导出全国车次站次 CSV；
- 构建以车站为节点、相邻停站区间为运行记录的铁路图；
- **多源/多目标搜索**：`exact` 模式严格单站，`fuzzy` 模式将输入扩展为同城全部有效铁路站共同参与搜索；
- **类型化路径段**：`TrainSegment`（铁路区段）与 `InterstationTransferSegment`（地面异站移动）独立建模，同城真实铁路与地面方案可并存；
- **独立 footpath 松弛**：到达站 A 后按用户配置时间生成同城站 B 的地面移动状态，异站换乘真正生效；
- **城市级 `transfer_at` 约束**：指定换乘城市，仅返回在该城市至少发生一次换乘事件的路线；
- **四档搜索模式**：`fast` / `balanced` / `thorough` / `complete`，控制状态上限、结果上限与支配规则；`complete` 可因超时/状态上限中止并标记 `complete=false`；
- **完整段序列去重**：按车站、时刻、段类型区分不同路线，不再仅按车次序列去重；
- **跨日时间展示**：保留日偏移，CLI/API/GUI 统一显示"次日"标记；
- **CLI 与 API 共用参数校验**：非法参数返回结构化错误（CLI 错误码退出，API HTTP 400）；
- 支持中文站名、电报码、完整拼音和部分模糊匹配；
- **智能同城分组**：大城市（≥8站）全部同城，小城市仅真正同城站互联（排除异名县）；
- 同站换乘缓冲 0-1440 分钟可配（默认 15）；异站换乘 0-1440 分钟可配（默认 60）；
- 构建完成后可在同一 `RailwayGraph` 实例上安全重新加载数据；
- 提供命令行输出和本地 Web GUI；
- **40 个确定性 `unittest` 测试**，核心运行代码仅使用 Python 标准库。

## 重要限制

- 搜索结果经过 Pareto 剪枝和状态上限的**候选集合**，非完备枚举（complete 模式取消启发式剪枝但仍有硬边界）；
- 异站换乘时间当前统一使用用户配置值（默认 60 分钟），未基于实际距离；
- **无票价数据**：离线数据不含票价，无法显示总票价；
- 不考虑车次开行日期、余票、席位和实时晚点；
- 前端自定义下拉框存在浏览器兼容性问题（详见 HANDOVER.md 第 10 节）；
- 评分是候选集内归一化线性加权，非机器学习排序。

完整架构、数据口径、风险与推进路线见本地 `HANDOVER.md`。该文件被 `.gitignore` 忽略，不随普通提交进入仓库。

## 数据规模

| 指标 | 数量 |
|---|---:|
| 车次代码 | 14173 |
| 站次记录 | 128141 |
| 图中车站 | 3305 |
| 唯一有向相邻站对 | 17792 |
| 具体车次区间记录 | 113968 |
| 双日扫描记录 | 227936 |

"具体车次区间记录"与"唯一图边"不是同一口径：多个车次可以经过同一个相邻站对。

## 环境要求

- Python 3.10 或更高版本；
- 项目数据已放在 `data/timetable/` 和 `data/output/`；
- 无需安装第三方 Python 包。

## 快速开始

```bash
cd "D:\Project\railway route"

# 模糊匹配（同城全部站）
python src/main.py 北京 上海 --max 5

# 精确站名
python src/main.py 北京南 上海虹桥 --match-mode exact --max 5

# 电报码 / 拼音
python src/main.py VNP AOH --max 5

# 时间窗口
python src/main.py 北京南 上海虹桥 --depart-after 08:00 --depart-before 12:00 --arrive-before 20:00

# 调整换乘参数
python src/main.py 延安 深圳北 --same-transfer 20 --inter-transfer 90 --max 5

# 完整模式搜索
python src/main.py 延安 深圳北 --search-profile complete --timeout 30

# 启动本地 Web GUI
python src/main.py --gui --port 8000
```

### 当前 CLI 参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `from_station` | 曲阜东 | 出发站输入 |
| `to_station` | 广州南 | 目的站输入 |
| `--match-mode` | fuzzy | 匹配模式：`exact` 单站 / `fuzzy` 同城扩展 |
| `--search-profile` | balanced | 搜索强度：`fast` / `balanced` / `thorough` / `complete` |
| `--depart-after` | 空 | 首次发车不早于 |
| `--depart-before` | 空 | 首次发车不晚于 |
| `--arrive-before` | 空 | 最终到达不晚于 |
| `--same-transfer` | 15 | 同站换乘缓冲（0-1440 分钟） |
| `--inter-transfer` | 60 | 异站换乘缓冲（0-1440 分钟） |
| `--max-transfers` | 3 | 最大换乘次数（0–10） |
| `--timeout` | 30 | 搜索超时秒数（1–600） |
| `--max` | 15 | CLI 最多展示多少条结果 |
| `--gui` | false | 启动本地 Web GUI |
| `--port` | 8000 | GUI 监听端口 |

## 当前实测

以下为当前数据和代码在开发笔记本上的代表性单次运行结果：

| 查询 | 首条候选 | 加载 | 搜索 |
|---|---|---:|---:|
| 北京南→上海虹桥 (exact) | G1, 4h54m, 直达 | 0.6s | 0.9s |
| 北京南→上海虹桥 (fuzzy) | G3, 4h41m, 北京南→上海 | 0.6s | 1.2s |
| 延安→深圳北 (complete) | D15→G409→G6067, 12h53m, 2换乘 | 0.6s | 3.5s (2396条) |

结果和耗时会随时刻表版本、机器、搜索模式和剪枝规则变化。这些数字不是稳定性能承诺。

## 项目结构

```text
railway route/
├── README.md                    # 本文件
├── HANDOVER.md                  # 本地完整交接文档，被 Git 忽略
├── data/
│   ├── w.db                     # 路路通离线包备份，被 Git 忽略
│   ├── timetable/               # 解压后的二进制文件，被 Git 忽略
│   └── output/
│       └── 车次时刻表.csv        # 全量导出，被 Git 忽略
├── tools/
│   └── parse_timetable.py       # 二进制解析、车次查询、车站反查、CSV 导出
├── src/
│   ├── graph.py                 # 图、索引、双日扫描缓存
│   ├── csa.py                   # 多源/多目标 CSA 搜索核心
│   ├── matcher.py               # 输入匹配与站点集合解析
│   ├── models.py                # 统一搜索请求、路径段、结果与元数据模型
│   ├── validation.py            # CLI 与 API 共用参数校验
│   ├── main.py                  # 评分、CLI、Web GUI/API
│   ├── search.py                # 旧搜索实现，当前入口未使用
│   ├── score.py                 # 旧评分实现，当前入口未使用
│   └── transfer.py              # 旧换乘实现，当前入口未使用
└── tests/
    ├── fixtures.py              # 临时时刻表/站名元数据工厂
    ├── test_smoke.py            # 冒烟测试
    ├── test_graph.py            # 图生命周期测试
    ├── test_matcher.py          # 站点集合解析测试
    ├── test_models.py           # 模型与序列化测试
    ├── test_validation.py       # 参数校验测试
    └── test_csa_core.py         # CSA 核心功能测试
```

## 验证

```bash
# 全部 40 个确定性测试
python -m unittest discover -s tests -v

# 核心模块语法检查
python -m py_compile src/*.py

# 基础冒烟查询
python src/main.py 北京南 上海虹桥 --match-mode exact --max 3
python src/main.py 北京南 上海虹桥 --match-mode fuzzy --max 3
python src/main.py 延安 深圳北 --match-mode fuzzy --search-profile complete --max 3
```

## 常见问题

### 双击 start.bat 后出现两个页面？

已修复。`start.bat` 仅启动服务器并打开 `http://127.0.0.1:8000`。

### 下拉框选项为什么不是玻璃态？

浏览器原生 `<select>` 下拉面板不支持 CSS `backdrop-filter`。已用 JS 自定义下拉组件替代，但存在兼容性问题。这是已知问题，详见 HANDOVER.md 第 10 节。

### 端口 8000 被占用

```bash
python src/main.py --gui --port 8080
```

## 近期路线

1. 修复自定义下拉框浏览器兼容性问题（当前最优先）；
2. 进一步紧凑前端布局；
3. 补充车次完整经停站数据到展开时刻表；
4. 基于车站坐标估算异站换乘时间；
5. 完善评分配置（用户可调权重）；
6. 端口占用友好处理与桌面打包；
7. 开行日期过滤。

## 数据来源与致谢

- [路路通时刻表](http://www.lltskb.com) — 离线时刻表数据来源；
- [shandongtlb/lltskb-tools](https://github.com/shandongtlb/lltskb-tools) — 二进制格式逆向与解析参考；
- [MichealWangYZ/lltskb-rail-query](https://github.com/MichealWangYZ/lltskb-rail-query) — 格式资料与工具参考。

数据版权归相应权利方所有。本项目仅供个人学习和研究，不保证数据完整性、实时性、可售性或路径建议适合真实出行。
