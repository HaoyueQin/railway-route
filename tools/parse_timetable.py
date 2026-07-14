#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析路路通离线车次时刻表 t0.dat~t19.dat（纯离线，零联网）。

来源: https://github.com/shandongtlb/lltskb-tools (shanongtlb)
协议: 本项目使用该工具用于个人学习/研究铁路数据。

—— 数据链（全部离线，详见 docs/FINDINGS.md §6）：
  t.i  : readShort(车次数) + N×readUTF  → 车次全局 index → 车次名（有序）
  s.i  : 同结构                          → 站点 index    → 站名
  sp.dat: 每车次 12306 train_no（可选，本模块不依赖）
  车次记录定位:  桶号 = (车次index + 1) % 20 → 打开 t{桶}.dat，
                 顺扫记录，每条 = [2B 车次index(hi×255+lo)] + trainInfo 体，
                 下一条偏移 = cur + 17 + 站数×7，记录头==目标 index 即命中。
  trainInfo 体(15B 头 + 站序):
     [0]type [1-2]priceNo [3-6]startDate [7-10]endDate [11-12]? [13-14]站数
       startDate/endDate 是 7bit×4 打包(b0+b1×128+b2×16384+b3×2097152)，解出即 YYYYMMDD
     其后每站 7 字节:  [站idx 2B] [到达时] [到达分] [停留分] [里程 2B]   （2B 均 hi×255+lo）
  站名 = s.i.getName(站idx)。发车时刻 = 到达 + 停留分。
  plat.dat: [4B 条目数] + N×([4B 车次idx][4B 站idx][1B 长度][UTF8]) → 站台号
  s0~s9.dat: 车站反查。桶 = (站idx+1)%10，记录 = [站idx 2B][车次数 2B][车次idx × N]，

所有多字节整数是路路通特有的 **hi×255 + lo**（不是 ×256！）。

用法:
  python tools/parse_timetable.py G1              # 查单个车次
  python tools/parse_timetable.py --station 北京南  # 按车站反查停靠车次
  python tools/parse_timetable.py --all           # 全量导出 data/output/车次时刻表.csv
"""
import sys, os, csv, struct

BUCKETS = 20


def read_index(path):
    """读 .i 索引文件: readShort 计数 + N×readUTF(2B长度+utf8)。返回 index→字符串 列表。"""
    b = open(path, "rb").read()
    n = struct.unpack(">H", b[:2])[0]
    i, out = 2, []
    for _ in range(n):
        ln = struct.unpack(">H", b[i:i + 2])[0]; i += 2
        out.append(b[i:i + ln].decode("utf-8", "replace")); i += ln
    return out


def _u255(b, i):
    """路路通 2 字节整数: 高字节×255 + 低字节。"""
    return (b[i] & 0xFF) * 255 + (b[i + 1] & 0xFF)


class Timetable:
    def __init__(self, datadir):
        self.datadir = datadir
        self.trains = read_index(os.path.join(datadir, "t.i"))
        self.stations = read_index(os.path.join(datadir, "s.i"))
        self.tidx = {n: k for k, n in enumerate(self.trains)}
        self._bucket_cache = {}
        self._sbucket_cache = {}
        self.sidx = {}
        for k, n in enumerate(self.stations):
            self.sidx.setdefault(n, k)
        self.plat = self._load_plat(os.path.join(datadir, "plat.dat"))

    @staticmethod
    def _load_plat(path):
        plat = {}
        if not os.path.exists(path):
            return plat
        b = open(path, "rb").read()
        if len(b) < 4:
            return plat
        n = struct.unpack(">I", b[:4])[0]
        i = 4
        for _ in range(n):
            if i + 9 > len(b):
                break
            a = struct.unpack(">I", b[i:i + 4])[0]
            s = struct.unpack(">I", b[i + 4:i + 8])[0]
            ln = b[i + 8]
            plat[(a, s)] = b[i + 9:i + 9 + ln].decode("utf-8", "replace")
            i += 9 + ln
        return plat

    def _bucket(self, gidx):
        b = (gidx + 1) % BUCKETS
        if b not in self._bucket_cache:
            for name in ("t%d.dat" % b, "T%d.dat" % b):
                p = os.path.join(self.datadir, name)
                if os.path.exists(p):
                    self._bucket_cache[b] = open(p, "rb").read(); break
            else:
                self._bucket_cache[b] = b""
        return self._bucket_cache[b]

    def _record(self, gidx):
        data = self._bucket(gidx)
        i, n = 0, len(data)
        while i + 17 <= n:
            recidx = _u255(data, i)
            nsta = _u255(data, i + 15)
            nxt = i + 17 + nsta * 7
            if recidx == gidx:
                return data[i + 2:nxt]
            i = nxt
        return None

    def resolve(self, code):
        code = code.strip().upper()
        if code in self.tidx:
            return self.tidx[code]
        for name, idx in self.tidx.items():
            if code in name.split("/"):
                return idx
        return -1

    def stops(self, code):
        gidx = self.resolve(code)
        if gidx < 0:
            return None
        ti = self._record(gidx)
        if ti is None or len(ti) < 15:
            return None
        nsta = _u255(ti, 13)
        rows, off = [], 15
        for k in range(nsta):
            if off + 7 > len(ti):
                break
            sidx = _u255(ti, off)
            h, m, stop = ti[off + 2], ti[off + 3], ti[off + 4]
            dist = _u255(ti, off + 5)
            arr_min = h * 60 + m
            dep_min = (arr_min + stop) % (24 * 60)
            first, last = (k == 0), (k == nsta - 1)
            rows.append({
                "seq": k + 1,
                "name": self.stations[sidx] if sidx < len(self.stations) else "?%d" % sidx,
                "arrive": "" if first else "%02d:%02d" % (h, m),
                "depart": "" if last else "%02d:%02d" % divmod(dep_min, 60),
                "stop_min": 0 if (first or last) else stop,
                "dist_km": dist,
                "platform": self.plat.get((gidx, sidx), ""),
                "sidx": sidx,
            })
            off += 7
        return {
            "code": self.trains[gidx], "index": gidx, "bucket": (gidx + 1) % BUCKETS,
            "type": ti[0], "price_no": _u255(ti, 1),
            "start_date": ti[3] + ti[4] * 128 + ti[5] * 16384 + ti[6] * 2097152,
            "end_date": ti[7] + ti[8] * 128 + ti[9] * 16384 + ti[10] * 2097152,
            "stops": rows,
        }

    def _station_bucket(self, sidx):
        b = (sidx + 1) % 10
        if b not in self._sbucket_cache:
            for name in ("s%d.dat" % b, "S%d.dat" % b):
                p = os.path.join(self.datadir, name)
                if os.path.exists(p):
                    self._sbucket_cache[b] = open(p, "rb").read(); break
            else:
                self._sbucket_cache[b] = b""
        return self._sbucket_cache[b]

    def _train_indices_at(self, sidx):
        data = self._station_bucket(sidx)
        i, n = 0, len(data)
        while i + 4 <= n:
            rec = _u255(data, i)
            cnt = _u255(data, i + 2)
            nxt = i + 4 + cnt * 2
            if rec == sidx:
                return [_u255(data, i + 4 + k * 2) for k in range(cnt) if i + 4 + k * 2 + 1 < n]
            i = nxt
        return []

    def station(self, station_name):
        name = station_name.strip()
        if name not in self.sidx:
            return None
        sidx = self.sidx[name]
        rows = []
        for gidx in self._train_indices_at(sidx):
            if gidx >= len(self.trains):
                continue
            info = self.stops(self.trains[gidx])
            if not info:
                continue
            here = next((s for s in info["stops"] if s["sidx"] == sidx), None)
            if here is None:
                continue
            first, last = info["stops"][0], info["stops"][-1]
            rows.append({
                "code": info["code"],
                "arrive": here["arrive"], "depart": here["depart"],
                "platform": here["platform"], "dist_km": here["dist_km"],
                "from": first["name"], "to": last["name"],
                "terminal": here is first or here is last,
            })
        rows.sort(key=lambda r: r["arrive"] or r["depart"] or "99:99")
        return {"name": name, "sidx": sidx, "trains": rows}

    def export_all(self, outpath):
        ntrain = nrow = 0
        with open(outpath, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["车次", "序号", "站名", "到达", "发车", "停留分", "里程km", "站台"])
            for gidx, code in enumerate(self.trains):
                info = self.stops(code)
                if not info or not info["stops"]:
                    continue
                ntrain += 1
                for s in info["stops"]:
                    w.writerow([code, s["seq"], s["name"], s["arrive"],
                                s["depart"], s["stop_min"], s["dist_km"], s["platform"]])
                    nrow += 1
        return ntrain, nrow


def _project_datadir():
    """返回项目的数据目录。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "data", "timetable")


def _project_outdir():
    here = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(here, "..", "data", "output")
    os.makedirs(d, exist_ok=True)
    return d


def main(argv):
    datadir = _project_datadir()
    args, stations = [], []
    i = 0
    while i < len(argv):
        if argv[i] == "--data":
            datadir = argv[i + 1]; i += 2
        elif argv[i] in ("--station", "-s"):
            stations.append(argv[i + 1]); i += 2
        else:
            args.append(argv[i]); i += 1

    tt = Timetable(datadir)

    if stations:
        for name in stations:
            info = tt.station(name)
            if not info:
                print("未找到车站: %s" % name); continue
            print("═ %s 停靠车次 %d 趟（按到达排序） ═" % (info["name"], len(info["trains"])))
            print(" 车次      到达    发车    站台  始发→终到")
            for t in info["trains"]:
                print(" %-8s  %-6s  %-6s  %-3s   %s→%s" % (
                    t["code"], t["arrive"] or "始发", t["depart"] or "终到",
                    t["platform"] or "—", t["from"], t["to"]))
        return

    if not args or args[0] == "--all":
        outpath = os.path.join(_project_outdir(), "车次时刻表.csv")
        ntrain, nrow = tt.export_all(outpath)
        print("全量导出: %d 车次 / %d 站次 → %s" % (ntrain, nrow, outpath))
        print("(共 %d 车次索引, %d 站名)" % (len(tt.trains), len(tt.stations)))
        return

    for code in args:
        info = tt.stops(code)
        if not info:
            print("未找到车次: %s" % code); continue
        print("═ %s  (index=%d 桶=%d type=%d 站数=%d  开行 %d~%d) ═" %
              (info["code"], info["index"], info["bucket"], info["type"],
               len(info["stops"]), info["start_date"], info["end_date"]))
        print(" 序 站名          到达    发车    停留  里程km 站台")
        for s in info["stops"]:
            print(" %2d %-10s  %-6s  %-6s  %3s   %5d  %s" % (
                s["seq"], s["name"], s["arrive"] or "----", s["depart"] or "----",
                (str(s["stop_min"]) + "分") if s["stop_min"] else "-", s["dist_km"],
                s["platform"] or "—"))


if __name__ == "__main__":
    main(sys.argv[1:])
