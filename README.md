# 铁路换乘路径规划 — railway-route-planner

基于路路通离线时刻表数据，构建优于12306的铁路换乘路径规划引擎。

## 数据来源

本项目使用[路路通时刻表](http://www.lltskb.com)的离线数据包（Windows版 `w.db`）。
数据版权归中国铁路及路路通所有，本项目仅供个人学习/研究使用。

## 项目结构

```
railway route/
├── README.md                    # 本文件
├── HANDOVER.md                  # 项目交接文档（含调研结论与推进路线）
├── .gitignore
├── data/
│   ├── w.db                     # 路路通离线数据包（ZIP格式）
│   ├── timetable/               # 解压后的时刻表二进制文件
│   │   ├── t.i, s.i             # 车次/站名索引
│   │   ├── T0~T19.dat           # 车次时刻表（20个分片桶）
│   │   ├── S0~S9.dat            # 车站→车次反查索引
│   │   ├── plat.dat             # 站台数据
│   │   ├── station_name.js      # 站名/电报码/城市分组
│   │   ├── routes.dat           # 线路→站序
│   │   └── ...                  # 其他辅助数据
│   └── output/
│       └── 车次时刻表.csv        # 全量导出（14173车次/128141站次）
├── tools/
│   └── parse_timetable.py       # 路路通时刻表解析工具
└── src/                         # 项目源代码（未来）
```

## 数据更新

### Windows版（w.db）

运行项目根目录下的"路路通时刻表.exe"，点击更新按钮，软件会自动下载最新数据到
`%APPDATA%/Roaming/lltskb/w.db`。然后将 w.db 复制到 `data/` 目录，解压覆盖
`data/timetable/` 即可。

```bash
cp ~/AppData/Roaming/lltskb/w.db data/w.db
cd data/timetable && unzip -o ../w.db
```

重新生成全量CSV：

```bash
python tools/parse_timetable.py --all
```

### Android版（an.db）

Android版的离线数据包可直接从路路通CDN下载（无需App）：

```bash
curl -L "http://down.lltskb.com/an.db" -o an.db
unzip -o an.db -d data/timetable/
```

当前数据版本：20260718

## 解析工具

本项目使用的 `tools/parse_timetable.py` 基于 [shandongtlb/lltskb-tools](https://github.com/shandongtlb/lltskb-tools)，
感谢作者对路路通二进制格式的逆向工作。

同时感谢 [MichealWangYZ/lltskb-rail-query](https://github.com/MichealWangYZ/lltskb-rail-query)
项目提供的格式文档参考。

### parse_timetable.py 用法

```bash
# 查询单个车次
python tools/parse_timetable.py G1

# 查询多个车次
python tools/parse_timetable.py G1 C1001 1461

# 车站反查（某站停靠的所有车次）
python tools/parse_timetable.py --station 北京南

# 全量导出CSV
python tools/parse_timetable.py --all

# 指定数据目录
python tools/parse_timetable.py --data /path/to/data G1
```
