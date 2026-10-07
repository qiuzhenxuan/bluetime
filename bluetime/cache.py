"""缓存层（SQLite 实现，可平移到云数据库）。

设计要点（对应「首个用户触发计算、其余命中缓存」的策略）：
1. 静态项（城市坐标、地形剖面）永久缓存；
2. 动态项（气象/评分）带 TTL，因为气象预报每 1~6 小时会更新，
   不能算完永久存，否则早上算的结果会一直用到晚上；
3. 防并发击穿：用 (scope, key) 主键 + status 标记做「单飞」，
   同一城市同时被多个用户请求时只有一个真正计算，其余走缓存或稍后重试。

云数据库适配：把 kv 表换成集合，(scope, key) 建唯一索引，
status/expires_at 语义完全一致。
"""

import json
import os
import sqlite3
import threading
import time

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    scope      TEXT NOT NULL,
    key        TEXT NOT NULL,
    status     TEXT NOT NULL,      -- 'computing' | 'ready'
    payload    TEXT,
    created_at REAL NOT NULL,
    expires_at REAL,               -- NULL 表示永久
    PRIMARY KEY (scope, key)
);
"""


class Cache:
    """本地 SQLite 缓存；线程安全。"""

    def __init__(self, path=None):
        self.path = path or os.path.join(os.path.dirname(__file__), ".bluetime_cache.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------- 基础读写 ----------

    def get_json(self, scope, key):
        """读取未过期且状态为 ready 的缓存。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT status, payload, expires_at FROM kv WHERE scope=? AND key=?",
                (scope, key)).fetchone()
        if not row:
            return None
        status, payload, expires_at = row
        if status != "ready" or payload is None:
            return None
        if expires_at is not None and expires_at < time.time():
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def set_json(self, scope, key, obj, ttl):
        """写入结果；ttl=0 表示永久。"""
        expires = None if ttl == 0 else time.time() + ttl
        payload = json.dumps(obj, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv (scope,key,status,payload,created_at,expires_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(scope,key) DO UPDATE SET "
                "status='ready', payload=excluded.payload, expires_at=excluded.expires_at",
                (scope, key, "ready", payload, time.time(), expires))
            self._conn.commit()

    # ---------- 单飞锁（防并发击穿） ----------

    def try_acquire(self, scope, key, lock_ttl=60):
        """尝试抢占计算权。返回 True 表示由本次调用负责计算。

        已有 ready 缓存时不抢占（调用方应先 get_json）。
        锁过期（持锁者崩溃）允许接管。
        """
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT status, expires_at FROM kv WHERE scope=? AND key=?",
                (scope, key)).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO kv (scope,key,status,payload,created_at,expires_at) "
                    "VALUES (?,?,?,NULL,?,?)",
                    (scope, key, "computing", now, now + lock_ttl))
                self._conn.commit()
                return True
            status, expires_at = row
            if status == "computing" and (expires_at is None or expires_at < now):
                self._conn.execute(
                    "UPDATE kv SET expires_at=? WHERE scope=? AND key=? AND status='computing'",
                    (now + lock_ttl, scope, key))
                self._conn.commit()
                return True
            return False

    def release(self, scope, key, obj=None, ttl=None):
        """计算完成：写入结果并解除锁。obj=None 表示计算失败，清除占位。"""
        if obj is None:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM kv WHERE scope=? AND key=? AND status='computing'",
                    (scope, key))
                self._conn.commit()
            return
        if ttl is None:
            ttl = config.CACHE["today_ttl"]
        self.set_json(scope, key, obj, ttl)

    # ---------- 领域封装 ----------

    @staticmethod
    def _wx_key(lat, lon, source=None):
        base = f"{round(lat, 4)},{round(lon, 4)}"
        return base if source is None else f"{base}|{source}"

    def get_weather(self, lat, lon, source=None):
        return self.get_json("weather", self._wx_key(lat, lon, source))

    def set_weather(self, lat, lon, bundle, ttl=None):
        blob = bundle.as_dict()
        blob["_series"] = {
            k: {"times": [t.isoformat() for t in v[0]], "values": v[1]}
            for k, v in bundle._hourly.items()
        }
        self.set_json("weather", self._wx_key(lat, lon, bundle.source), blob,
                      ttl if ttl is not None else config.CACHE["today_ttl"])
