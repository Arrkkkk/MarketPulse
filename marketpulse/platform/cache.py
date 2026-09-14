"""Tiered cache: in-process memory, then SQLite on disk.

Replaces the two competing caches the app used to run (`st.session_state`
dicts shadowing `@st.cache_data`), neither of which was shared between users.
One process-wide cache means the free-tier upstream quotas are spent once
rather than once per visitor.

Reads walk the tiers in order and back-fill memory on a disk hit. Writes go to
every tier. Keys carry a version prefix so bumping `CACHE_VERSION` invalidates
everything without anyone having to find and delete a file.

A Redis tier slots in beside SQLite when there is more than one process to
share between; there is currently one, so adding it now would be ceremony.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from marketpulse.platform.serde import deserialize, serialize
from marketpulse.platform.telemetry import get_logger

T = TypeVar("T")

logger = get_logger("platform.cache")

CACHE_VERSION = "v1"
_MAX_KEY_LENGTH = 200
DEFAULT_DB_PATH = Path.home() / ".marketpulse" / "cache.sqlite3"

#: TTLs by data class, in seconds. Chosen from how fast the underlying thing
#: actually changes, not from a single global guess.
TTL_QUOTE = 30
TTL_INTRADAY = 5 * 60
TTL_DAILY_HISTORY = 12 * 60 * 60
TTL_PROFILE = 24 * 60 * 60
TTL_NEWS = 60 * 60
TTL_AI_ANALYSIS = 6 * 60 * 60


def make_key(base: str, **params: Any) -> str:
    """A deterministic cache key from a base name and keyword parameters.

    Parameters are sorted, so argument order never produces a second entry for
    the same logical request. Long keys collapse to a digest.
    """
    if params:
        suffix = ":".join(f"{k}={params[k]}" for k in sorted(params))
        key = f"{base}:{suffix}"
    else:
        key = base
    if len(key) > _MAX_KEY_LENGTH:
        key = f"{base}:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
    return key


class _MemoryTier:
    """Bounded in-process tier. Evicts nearest-expiry first when full."""

    def __init__(self, max_items: int = 512) -> None:
        self._max_items = max_items
        self._store: dict[str, tuple[bytes, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            blob, expires_at = entry
            if expires_at <= time.time():
                del self._store[key]
                return None
            return blob

    def set(self, key: str, blob: bytes, expires_at: float) -> None:
        with self._lock:
            self._store[key] = (blob, expires_at)
            if len(self._store) > self._max_items:
                for stale in sorted(self._store, key=lambda k: self._store[k][1])[
                    : len(self._store) - self._max_items
                ]:
                    del self._store[stale]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class _SqliteTier:
    """Durable tier. Survives restarts, shared by every session in the process."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                "  key TEXT PRIMARY KEY,"
                "  value BLOB NOT NULL,"
                "  expires_at REAL NOT NULL"
                ")"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON cache(expires_at)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, key: str) -> tuple[bytes, float] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        blob, expires_at = row
        if expires_at <= time.time():
            self.delete(key)
            return None
        return blob, expires_at

    def set(self, key: str, blob: bytes, expires_at: float) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO cache(key, value, expires_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "expires_at=excluded.expires_at",
                (key, blob, expires_at),
            )

    def delete(self, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))

    def purge_expired(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM cache WHERE expires_at <= ?", (time.time(),))
            return cur.rowcount

    def clear(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM cache")


class Cache:
    """The cache facade. Owns key versioning; tiers never see a raw key."""

    def __init__(self, db_path: Path | str | None = None, max_memory_items: int = 512) -> None:
        self._memory = _MemoryTier(max_memory_items)
        self._disk = _SqliteTier(Path(db_path) if db_path else DEFAULT_DB_PATH)
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _versioned(key: str) -> str:
        return f"{CACHE_VERSION}:{key}"

    def get(self, key: str) -> Any | None:
        vkey = self._versioned(key)
        blob = self._memory.get(vkey)
        if blob is not None:
            self.hits += 1
            return deserialize(blob)
        found = self._disk.get(vkey)
        if found is not None:
            blob, expires_at = found
            self._memory.set(vkey, blob, expires_at)  # back-fill
            self.hits += 1
            return deserialize(blob)
        self.misses += 1
        return None

    def set(self, key: str, value: Any, ttl: float) -> None:
        vkey = self._versioned(key)
        blob = serialize(value)
        expires_at = time.time() + ttl
        self._memory.set(vkey, blob, expires_at)
        self._disk.set(vkey, blob, expires_at)

    def get_or_set(self, key: str, ttl: float, producer: Callable[[], T]) -> T:
        """Return the cached value, else call `producer` and cache its result.

        Only successes are stored. If `producer` raises, the exception
        propagates untouched and nothing is written — a failure must never be
        cached as if it were data.
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        value = producer()
        if value is not None:
            self.set(key, value, ttl)
        return value

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def stats(self) -> dict[str, Any]:
        return {"hits": self.hits, "misses": self.misses, "hit_rate": round(self.hit_rate, 4)}

    def clear(self) -> None:
        self._memory.clear()
        self._disk.clear()
        self.hits = self.misses = 0


_default_cache: Cache | None = None
_cache_lock = threading.Lock()


def get_cache() -> Cache:
    """The process-wide cache singleton."""
    global _default_cache
    with _cache_lock:
        if _default_cache is None:
            _default_cache = Cache()
        return _default_cache
