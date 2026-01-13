from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .schemas import CacheStats, CommandResult


@dataclass
class CachedResult:
    stdout: str
    stderr: str
    return_code: int


class CommandCache:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()

    def _init_db(self) -> None:
        with self._cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS command_cache (
                    cache_key TEXT PRIMARY KEY,
                    cmd TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    stdout TEXT NOT NULL,
                    stderr TEXT NOT NULL,
                    return_code INTEGER NOT NULL,
                    executed_at TEXT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_repo ON command_cache(repo)
            """)

    @staticmethod
    def _make_key(cmd: str, repo: str) -> str:
        combined = f"{repo}:{cmd}"
        return hashlib.sha256(combined.encode()).hexdigest()

    def get(self, cmd: str, repo: str) -> CachedResult | None:
        cache_key = self._make_key(cmd, repo)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT stdout, stderr, return_code FROM command_cache WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()

        with self._lock:
            if row:
                self._hits += 1
                return CachedResult(
                    stdout=row["stdout"],
                    stderr=row["stderr"],
                    return_code=row["return_code"],
                )
            self._misses += 1
            return None

    def set(self, cmd: str, repo: str, result: CommandResult) -> None:
        cache_key = self._make_key(cmd, repo)
        executed_at = datetime.now(timezone.utc).isoformat()

        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO command_cache
                (cache_key, cmd, repo, stdout, stderr, return_code, executed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    cmd,
                    repo,
                    result.stdout,
                    result.stderr,
                    result.return_code,
                    executed_at,
                ),
            )

    def stats(self) -> CacheStats:
        with self._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM command_cache")
            row = cursor.fetchone()
            total = row["cnt"] if row else 0

        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0.0

            return CacheStats(
                total_entries=total,
                hits=self._hits,
                misses=self._misses,
                hit_rate=hit_rate,
            )

    def clear(self) -> int:
        with self._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM command_cache")
            row = cursor.fetchone()
            count = row["cnt"] if row else 0
            cursor.execute("DELETE FROM command_cache")
            return count

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
