# 统一多站铁路搜索与异站换乘架构设计

> 日期：2026-07-15  
> 状态：用户已批准，尚未开始实施  
> 项目：`D:\Project\railway route`

## 1. 背景

当前项目使用自定义 CSA 变体搜索单一出发站到单一目的站的铁路候选。现状存在以下核心语义缺口：

- 城市名和站名最终都只解析成一个车站，不能让同城全部车站共同参与搜索；
- 同城异站索引和 UI 参数已存在，但搜索状态不会跨站移动；
- `transfer_at` 参数已传入搜索函数，但没有约束结果；
- 候选剪枝、去重、跨日展示、参数校验和缓存生命周期需要系统整理；
- 项目缺少自动化测试，架构调整容易产生静默回归。

本设计采用统一多源/多目标 CSA，并把同城地面移动建模为独立 footpath 状态转移。

## 2. 已确认的业务决策

### 2.1 准确匹配与模糊匹配

提供两种搜索范围：

- `exact`：严格使用解析出的单个出发站和目的站；
- `fuzzy`：无论输入城市名还是明确车站名，都定位其所属城市，并将该城市中实际存在于铁路图的全部车站作为起点或终点集合。

默认模式为 `fuzzy`。

例如在模糊模式下输入“北京南 → 上海虹桥”，实际搜索范围是北京全部有效铁路站到上海全部有效铁路站。结果必须显示实际采用的出发站和到达站。

### 2.2 铁路区段与地面异站移动

同城站间存在两类不同路径，必须分别保留：

1. **真实铁路区段**：如果北京朝阳到北京站之间存在合适列车，该列车作为普通 `TrainSegment`，完整保留车次和时刻，并正常增加列车换乘次数。
2. **地面异站移动**：旅客从北京朝阳通过地铁、打车或其他地面方式前往北京站，建模为 `InterstationTransferSegment`。

两类方案可以同时存在，互不覆盖。

### 2.3 异站换乘计数

从列车 A 下车，经地面异站移动后乘列车 B：

- `train_transfers` 增加 1；
- `interstation_transfers` 增加 1；
- 评分中对异站移动单独增加惩罚；
- GUI/CLI 明确展示地面移动站对和耗时。

### 2.4 异站换乘时间

本轮实现使用统一用户默认值：

- 默认 60 分钟；
- CLI、API 和 GUI 可调；
- 不再以“最短铁路区间时间 + 30 分钟”替代地面交通时间，因为真实铁路区间由正常 connection 扫描处理。

后续补充车站经纬度后，可按直线距离估算。用户提出的待标定初始公式为：

```text
estimated_minutes = 15 + ceil(distance_km / 20) × 60
```

该公式只记录为后续研究方向，在缺少真实样本前不固化到本轮实现。

### 2.5 指定换乘地点

`transfer_at` 是城市级“至少一次换乘”约束：

- 输入城市名或该城市任一车站名，都解析到所属城市；
- 路线至少有一次换乘事件发生在该城市；
- 同站换车和同城异站移动后换车都可满足；
- 仅乘车途经该城市但不换车不满足；
- 其他换乘仍可发生在别处。

### 2.6 开行日期

本轮不接入具体出行日期，也不依据车次有效起止日期过滤。界面和文档必须明确：结果没有校验指定日期是否实际开行。

## 3. 总体架构

采用以下数据流：

```text
SearchRequest
  → StationResolver（exact/fuzzy → source set / target set）
  → MultiSourceMultiTargetCSA
       ├── Train connection scan
       ├── Same-station transfer feasibility
       └── Interstation footpath relaxation
  → Route reconstruction（TrainSegment / InterstationTransferSegment）
  → Feasibility filters
  → Full-path deduplication
  → Scoring
  → CLI / API / GUI
```

搜索只扫描一次全国 connection 列表，不为每个起终点站组合重复运行全图。

## 4. 搜索请求模型

引入统一请求对象，至少包含：

```text
from_query
to_query
match_mode: exact | fuzzy
search_profile: fast | balanced | thorough | complete
earliest_depart
latest_depart
earliest_arrive
latest_arrive
same_station_transfer_minutes
interstation_transfer_minutes
max_transfers
transfer_city_constraint
timeout_seconds
```

默认值：

- `match_mode = fuzzy`；
- `search_profile = balanced`；
- 同站换乘 15 分钟；
- 异站换乘 60 分钟；
- 最大换乘 3 次；
- `complete` 默认超时 60 秒。

请求解析和验证应独立于 HTTP Handler、CLI 和算法核心。

## 5. 站点解析

### 5.1 精确模式

解析到一个明确车站。如果输入不能唯一解析，应返回明确错误或候选列表，不静默扩展同城站。

### 5.2 模糊模式

先将输入解析为一个代表车站或城市，再通过车站到城市的反向索引取得该城市全部有效铁路站。

需要在 matcher/graph 中提供：

- `station_to_city_code`；
- `city_code_to_name`；
- `resolve_station_set(query, mode)`；
- `resolve_city(query)`，供 `transfer_at` 使用。

只返回存在于 `graph.station_to_idx` 的站点。

## 6. 路径段与结果模型

### 6.1 TrainSegment

保存：

- 车次代码；
- 起终站索引和名称；
- 绝对发车/到达分钟；
- 区间运行时间；
- 铁路距离；
- 站序信息。

### 6.2 InterstationTransferSegment

保存：

- 同城起终站；
- 开始和结束绝对分钟；
- 移动时间；
- 城市代码和名称；
- `estimate_source = user_default`。

### 6.3 RouteResult

至少提供：

```text
segments
actual_origin
actual_destination
first_departure
final_arrival
total_minutes
rail_distance
train_transfers
interstation_transfers
interstation_minutes
transfer_cities
matched_transfer_constraint
```

兼容层可以暂时保留旧字段，但新接口和 GUI 应逐步转向统一段模型。

## 7. 状态模型与转移

状态至少记录：

- 当前站；
- 当前绝对时间；
- 累计铁路距离；
- 已乘列车数量或列车换乘次数；
- 异站移动次数和分钟；
- 当前车次；
- 首次发车时间；
- 前一状态和前一段；
- 是否刚执行地面移动；
- 是否满足指定换乘城市约束。

### 7.1 多源初始化

所有起点站都获得初始状态。模糊模式下不同起点站属于同一次搜索，不重复扫描 connections。

### 7.2 铁路 connection

- 初次登车必须满足首次发车窗口；
- 同车续乘不增加换乘；
- 换乘新车时检查同站缓冲或前置异站移动完成时间；
- 真实同城市内铁路区段按普通列车段处理。

### 7.3 footpath 松弛

列车到达站 A 后，可向 `same_city_of[A]` 中的站 B 生成地面移动状态：

- 时间增加用户设置的异站分钟数；
- 不增加铁路距离；
- 记录 `InterstationTransferSegment`；
- 不允许连续 footpath；
- 同一路径不允许通过 footpath 在同城站间循环；
- footpath 本身不额外增加列车换乘次数，下一次登上不同列车时形成一次列车换乘；
- 更新异站移动统计和指定换乘城市匹配状态。

需要防止 footpath 状态爆炸，状态支配维度必须包含当前站、时间、换乘、异站次数和指定约束状态。

### 7.4 多目标收集

任何目标站集合中的站都可收集终点状态。结果记录实际到达站。

## 8. 搜索模式

### 8.1 fast

- 强剪枝；
- 较低每站状态上限；
- 较低候选上限；
- 面向交互速度。

### 8.2 balanced

- 默认模式；
- 保持当前量级的状态上限和候选数量；
- 平衡速度与多样性。

### 8.3 thorough

- 更高状态上限；
- 更宽松的支配规则；
- 更高结果上限。

### 8.4 complete

在以下硬边界内取消启发式每站状态截断和宽松支配删除：

- 双日时间轴；
- 用户时间窗口；
- 最大换乘次数；
- 基本可行性规则。

资源保护：

- 默认超时 60 秒；
- 全局生成状态安全上限；
- 超时或达到上限时返回已有结果；
- 必须返回 `complete=false` 和 `stopped_reason`；
- 正常遍历结束返回 `complete=true`。

所有模式返回搜索元数据：

```text
profile
complete
stopped_reason
elapsed_ms
scanned_connections
generated_states
returned_routes
```

## 9. 去重

旧的车次序列去重过粗。新 key 使用完整段序列：

铁路段：

```text
train, train_code, from_station, to_station,
absolute_departure, absolute_arrival
```

地面段：

```text
interstation, from_station, to_station,
start_time, end_time
```

这样可以区分不同上下车站、跨日偏移、真实铁路市内段和地面异站移动。

## 10. 跨日时间

内部统一使用绝对分钟。API 的每个时间字段返回：

```json
{
  "minutes": 1845,
  "time": "06:45",
  "day_offset": 1,
  "display": "次日 06:45"
}
```

显示规则：

- 首日：`06:45`；
- 次日：`次日 06:45`；
- 第三日：`第3日 06:45`。

CLI、API 和 GUI 共享同一格式化语义。

## 11. API 与 CLI 校验

统一校验：

- `match_mode` 和 `search_profile` 枚举；
- `HH:MM` 时间格式；
- 同站/异站换乘分钟范围；
- 最大换乘次数范围；
- 完整模式超时范围；
- 起终点和指定换乘城市可解析。

HTTP 非法请求返回 400 和结构化 JSON 错误。CLI 使用 `argparse` 的类型和 `choices`。

## 12. RailwayGraph 生命周期

`RailwayGraph.build()` 开始时重置所有原始与派生容器，使同一实例重复构建得到与新实例相同的数量和内容。

反向距离缓存移入图对象：

```text
graph.distance_cache[target] = distances
```

重新 build 时清空，避免模块级 `lru_cache` 持有旧图实例。

## 13. GUI

新增：

- `模糊匹配（默认） / 准确匹配`；
- `快速 / 均衡 / 全面 / 完整`；
- 最大换乘次数；
- 完整模式超时；
- 城市级指定换乘；
- 完整性状态和中止原因；
- 实际起终点站；
- 跨日中文标记；
- 铁路段和地面异站段的不同样式；
- 列车换乘次数、异站次数和异站总分钟。

尚未生效的旧控件不能继续无提示展示。

## 14. 测试策略

使用标准库 `unittest`，建立临时 CSV 与 station_name.js 的小型合成网络。

必须覆盖：

1. 精确模式单站；
2. 模糊模式同城站集合；
3. 多源多目标搜索；
4. 同站换乘边界；
5. 地面异站转移；
6. 同城真实铁路与地面方案并存；
7. 城市级 `transfer_at`；
8. 仅途经指定城市不满足；
9. 完整段序列去重；
10. 四档搜索模式；
11. complete 正常完成；
12. complete 超时/状态上限中止；
13. 跨日 day_offset；
14. API 非法参数；
15. 重复 build；
16. 图内缓存重建清空。

全国数据只做可选冒烟测试，不把具体车次长期写死为单元测试断言。

## 15. 实施和文档同步规则

每个独立步骤严格执行：

```text
修改代码
→ 运行对应自动化测试
→ 更新 README.md 和 HANDOVER.md
→ 检查 diff
→ 签收该步骤
```

实施顺序：

1. 固化本设计与 HANDOVER 恢复点；
2. 建立测试基础设施；
3. 修复 graph 重复 build 和缓存；
4. 实现站点集合解析；
5. 引入请求和路径段模型；
6. 多源多目标铁路搜索；
7. footpath；
8. `transfer_at`；
9. 四档模式与完整性元数据；
10. 去重；
11. 跨日；
12. 校验；
13. GUI；
14. 全国数据回归与性能测试；
15. 最终文档复核。

## 16. 验收标准

- exact 和 fuzzy 产生符合定义的不同站点范围；
- fuzzy 输入明确站名仍扩展其所属城市；
- 一次扫描完成多源多目标搜索；
- 同城真实铁路方案与地面移动方案同时保留；
- 调整异站时间会改变专门构造的异站候选；
- `transfer_at` 返回的每条路线至少一次在指定城市换乘；
- 去重不再合并不同上下车站、时刻或段类型；
- 跨日时间包含 day_offset 和中文显示；
- complete 中止时明确标记未完整；
- 重复 build 不产生重复数据；
- 无模块级缓存持有旧图；
- API 非法参数返回明确错误；
- 自动化测试覆盖核心边界；
- README 和 HANDOVER 与实现及验证结果同步。
