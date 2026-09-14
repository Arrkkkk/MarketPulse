"""In-process latency and counter metrics.

Enough to answer "is it fast, and is the cache working" without standing up
Prometheus for a two-container app. Phase 9 adds a real exporter; the
recording call sites here are the ones that will feed it, so that change is
a backend swap rather than re-instrumenting everything.

Percentiles are computed over a bounded ring of recent samples. A mean would
hide exactly the problem worth finding — p95 is where a cold cache miss or a
throttled upstream shows up, and an average buries it under fast cache hits.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager

#: Samples retained per label. At a few requests a second this covers a
#: useful recent window without unbounded growth.
WINDOW = 512


class Metrics:
    def __init__(self, window: int = WINDOW) -> None:
        self._window = window
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window))
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, label: str, milliseconds: float) -> None:
        with self._lock:
            self._latencies[label].append(milliseconds)

    def increment(self, label: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[label] += amount

    @contextmanager
    def timer(self, label: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(label, (time.perf_counter() - started) * 1000)

    @staticmethod
    def _percentile(samples: list[float], fraction: float) -> float:
        """Nearest-rank percentile. Exact for the small windows here, and it
        always returns a value that was actually observed."""
        if not samples:
            return 0.0
        ordered = sorted(samples)
        rank = max(1, min(len(ordered), int(round(fraction * len(ordered) + 0.5))))
        return ordered[rank - 1]

    def snapshot(self) -> dict:
        with self._lock:
            latencies = {k: list(v) for k, v in self._latencies.items()}
            counters = dict(self._counters)

        summary = {}
        for label, samples in latencies.items():
            if not samples:
                continue
            summary[label] = {
                "count": len(samples),
                "p50_ms": round(self._percentile(samples, 0.50), 1),
                "p95_ms": round(self._percentile(samples, 0.95), 1),
                "p99_ms": round(self._percentile(samples, 0.99), 1),
                "max_ms": round(max(samples), 1),
            }
        return {"latency": summary, "counters": counters}

    def reset(self) -> None:
        with self._lock:
            self._latencies.clear()
            self._counters.clear()


_metrics = Metrics()


def get_metrics() -> Metrics:
    return _metrics
