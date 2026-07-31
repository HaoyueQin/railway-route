"""查询结果缓存：同参数重复查询秒回（空间换时间，贴近真实使用场景）。

- 内存 LRU（限制条目数与总大小，防止内存超限）+ SQLite 磁盘持久化
- 缓存键 = 规范化请求参数 + 数据指纹（时刻表 CSV 的 mtime+size，数据更新自动失效）
- 命中时返回缓存结果并标记 cached=True

为什么不用 GPU：CSA 是串行数据依赖的图扫描算法（每个连接的处理依赖前序
标签状态），GPU 的 SIMT 并行模型不适合；分支密集、小对象操作，数据搬运
开销远大于收益。CPU 上的实际加速手段就是"重复查询不再重算"——缓存。
"""
import json
import sqlite3
import time
from pathlib import Path

from src.models import SearchRequest

_CACHE_DB = Path(__file__).resolve().parent.parent / ".search_cache.sqlite"
_MAX_ENTRIES = 300        # 内存缓存上限（条目数）
_MAX_ENTRY_BYTES = 400_000  # 单条目上限（约 400KB，防止超大结果撑爆内存）
_MAX_TOTAL_BYTES = 80_000_000  # 内存缓存总量上限（80MB，远低于 1G 内存约束）


def _request_key(request: SearchRequest, data_fingerprint: str) -> str:
    """规范化请求参数 → 缓存键（字段顺序固定，None 折叠）。"""
    parts = [
        request.from_query, request.to_query,
        request.match_mode, request.from_mode or "", request.to_mode or "",
        request.search_profile,
        request.earliest_depart, request.latest_depart,
        request.earliest_arrive, request.latest_arrive,
        request.same_station_transfer_minutes,
        request.interstation_transfer_minutes,
        request.max_transfers,
        request.transfer_city_code or "",
        request.timeout_seconds,
        data_fingerprint,
    ]
    return json.dumps(parts, ensure_ascii=False)


def data_fingerprint(csv_path: str) -> str:
    """时刻表数据指纹：文件 mtime + 大小（数据更新后旧缓存自动失效）。"""
    try:
        p = Path(csv_path)
        st = p.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "unknown"


class SearchCache:
    """线程安全（GIL 保护）的查询缓存：内存 LRU + SQLite 磁盘持久化。"""

    def __init__(self, db_path: str | Path | None = None, enabled: bool = True):
        self.enabled = enabled
        self._mem: dict[str, tuple[float, str]] = {}  # key -> (timestamp, json)
        self._mem_bytes = 0
        self._db = Path(db_path) if db_path else _CACHE_DB
        self._conn: sqlite3.Connection | None = None
        if enabled:
            try:
                self._conn = sqlite3.connect(str(self._db), timeout=5)
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS search_cache "
                    "(key TEXT PRIMARY KEY, body TEXT, ts REAL)")
                self._conn.commit()
            except sqlite3.Error:
                self._conn = None

    def get(self, key: str):
        if not self.enabled:
            return None
        hit = self._mem.get(key)
        if hit is not None:
            return hit[1]
        if self._conn is not None:
            try:
                row = self._conn.execute(
                    "SELECT body FROM search_cache WHERE key=?", (key,)).fetchone()
            except sqlite3.Error:
                return None
            if row:
                body = row[0]
                if len(body) <= _MAX_ENTRY_BYTES:
                    self._mem[key] = (time.time(), body)
                    self._mem_bytes += len(body)
                    self._trim_mem()
                return body
        return None

    def put(self, key: str, body: str) -> None:
        if not self.enabled or not body or len(body) > _MAX_ENTRY_BYTES:
            return
        old = self._mem.get(key)
        if old is not None:
            self._mem_bytes -= len(old[1])
        self._mem[key] = (time.time(), body)
        self._mem_bytes += len(body)
        self._trim_mem()
        if self._conn is not None:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO search_cache(key, body, ts) VALUES(?,?,?)",
                    (key, body, time.time()))
                self._conn.commit()
            except sqlite3.Error:
                pass

    def _trim_mem(self) -> None:
        """内存超限时按最近使用淘汰（LRU）。"""
        while len(self._mem) > _MAX_ENTRIES or self._mem_bytes > _MAX_TOTAL_BYTES:
            if not self._mem:
                break
            oldest_key = min(self._mem, key=lambda k: self._mem[k][0])
            self._mem_bytes -= len(self._mem[oldest_key][1])
            del self._mem[oldest_key]

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
