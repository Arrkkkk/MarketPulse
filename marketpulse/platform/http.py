"""Outbound-call resilience: retry, circuit breaking, rate limiting.

The only place in MarketPulse that decides how to behave when an upstream
provider is slow, throttling, or down. Providers describe *what* to fetch;
this module decides *how hard to try*.

Synchronous on purpose. Every upstream client MarketPulse uses (yfinance,
pycoingecko, requests) is blocking, so an async layer would only add an event
loop for calls that cannot yield. FastAPI (Phase 3) runs `def` endpoints in a
threadpool, so this composes there without change.

Adapted from wshobson/maverick-mcp `maverick/platform/http.py`, which is
async; the state machine and the single-probe rule are the parts worth taking.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from marketpulse.platform.metrics import get_metrics
from marketpulse.platform.telemetry import get_logger

T = TypeVar("T")

logger = get_logger("platform.http")

#: HTTP statuses worth retrying. 4xx other than 429 are the caller's fault and
#: retrying them just burns quota.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Indirection so the test suite can neutralise backoff without also
#: neutralising the rate limiter, which needs to sleep for real to be
#: meaningfully tested. Patch this, not `time.sleep`.
_RETRY_SLEEP: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to retry, and how long to wait between attempts."""

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with full jitter, for a 0-indexed attempt."""
        raw = min(self.base_delay * (2**attempt), self.max_delay)
        return raw * (1.0 + random.uniform(-self.jitter, self.jitter))


@dataclass(frozen=True)
class BreakerPolicy:
    failure_threshold: int = 5
    recovery_seconds: float = 30.0


class CircuitOpenError(Exception):
    """A call was rejected without being attempted: the breaker is open."""

    def __init__(self, name: str, seconds_until_half_open: float) -> None:
        self.name = name
        self.seconds_until_half_open = seconds_until_half_open
        super().__init__(
            f"circuit breaker {name!r} is open; retry in {seconds_until_half_open:.1f}s"
        )


class CircuitBreaker:
    """Per-service breaker: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.

    Consecutive failures trip the breaker at `failure_threshold`. Once the
    recovery window elapses exactly one caller is admitted as a probe; its
    success closes the breaker and its failure reopens it with a fresh window.
    Every other caller during that window fails fast rather than queueing
    behind the probe — the point of the breaker is to stop sending load to a
    service that is already struggling, and a queue would defeat that.
    """

    def __init__(self, name: str, policy: BreakerPolicy | None = None) -> None:
        self.name = name
        self._policy = policy or BreakerPolicy()
        self._state = "closed"
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state

    def reset(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._opened_at = None

    def _seconds_until_half_open(self) -> float:
        if self._opened_at is None:
            return 0.0
        return max(0.0, self._policy.recovery_seconds - (time.monotonic() - self._opened_at))

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Run `fn` under the breaker. Raises CircuitOpenError when open."""
        is_probe = False
        with self._lock:
            if self._state == "open":
                remaining = self._seconds_until_half_open()
                if remaining > 0:
                    raise CircuitOpenError(self.name, remaining)
                self._state = "half_open"
                is_probe = True
            elif self._state == "half_open":
                # A probe is already in flight. Fail fast.
                raise CircuitOpenError(self.name, self._seconds_until_half_open())

        try:
            result = fn(*args, **kwargs)
        except Exception:
            with self._lock:
                if is_probe:
                    self._state = "open"
                    self._opened_at = time.monotonic()
                    logger.warning("breaker %s: probe failed, reopening", self.name)
                else:
                    self._failures += 1
                    if self._failures >= self._policy.failure_threshold:
                        self._state = "open"
                        self._opened_at = time.monotonic()
                        logger.warning(
                            "breaker %s: opened after %d consecutive failures",
                            self.name,
                            self._failures,
                        )
            raise
        else:
            with self._lock:
                if self._state != "closed":
                    logger.info("breaker %s: closed", self.name)
                self._failures = 0
                self._state = "closed"
                self._opened_at = None
            return result


class RateLimiter:
    """Token bucket. `acquire()` blocks until a token is available.

    Sized from each provider's published quota so MarketPulse throttles itself
    before the upstream does it for us — the free tiers here are small enough
    that a single enthusiastic user can exhaust a day's allowance.
    """

    def __init__(self, rate_per_second: float, burst: float | None = None) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._rate = rate_per_second
        self._capacity = burst if burst is not None else max(1.0, rate_per_second)
        self._tokens = self._capacity
        self._updated_at = time.monotonic()
        self._lock = threading.Lock()

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Take a token if one is available; never block.

        The inbound counterpart to `acquire`. For an outbound call, waiting
        is correct — we want the request to happen, just later. For an
        inbound one it is not: holding a connection open to slow a caller
        down consumes a worker and is indistinguishable from the service
        being slow. Reject with 429 instead.
        """
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self._capacity, self._tokens + (now - self._updated_at) * self._rate)
            self._updated_at = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def seconds_until_available(self, tokens: float = 1.0) -> float:
        """How long until `tokens` would be available, for Retry-After."""
        with self._lock:
            deficit = max(0.0, tokens - self._tokens)
            return deficit / self._rate

    def acquire(self, tokens: float = 1.0) -> None:
        """Block until a token is available. For outbound calls only."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated_at) * self._rate
                )
                self._updated_at = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self._rate
            time.sleep(wait)


# --- process-global registries -------------------------------------------

_breakers: dict[str, CircuitBreaker] = {}
_limiters: dict[str, RateLimiter] = {}
_registry_lock = threading.Lock()


def get_breaker(name: str, policy: BreakerPolicy | None = None) -> CircuitBreaker:
    """The process-wide breaker for `name`, created on first use."""
    with _registry_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(name, policy)
        return _breakers[name]


def get_limiter(name: str, rate_per_second: float, burst: float | None = None) -> RateLimiter:
    """The process-wide rate limiter for `name`, created on first use."""
    with _registry_lock:
        if name not in _limiters:
            _limiters[name] = RateLimiter(rate_per_second, burst)
        return _limiters[name]


def reset_registries() -> None:
    """Clear both registries. For tests."""
    with _registry_lock:
        _breakers.clear()
        _limiters.clear()


def call_with_retry(
    fn: Callable[..., T],
    *args,
    policy: RetryPolicy | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    give_up_on: tuple[type[BaseException], ...] = (),
    sleep: Callable[[float], None] | None = None,
    **kwargs,
) -> T:
    """Call `fn`, retrying transient failures with exponential backoff.

    `give_up_on` wins over `retry_on`: a symbol that does not exist will not
    start existing because we asked four more times.

    `sleep` is injectable so tests do not have to spend real seconds.
    """
    policy = policy or RetryPolicy()
    sleep_fn = sleep if sleep is not None else _RETRY_SLEEP
    last: BaseException | None = None
    for attempt in range(policy.attempts):
        try:
            return fn(*args, **kwargs)
        except give_up_on:
            raise
        except retry_on as exc:
            last = exc
            if attempt == policy.attempts - 1:
                break
            delay = policy.delay_for(attempt)
            logger.info(
                "retrying after %s (attempt %d/%d, sleeping %.2fs)",
                type(exc).__name__,
                attempt + 1,
                policy.attempts,
                delay,
            )
            sleep_fn(delay)
    assert last is not None  # only reachable after at least one failure
    raise last


def resilient(
    name: str,
    fn: Callable[..., T],
    *args,
    retry: RetryPolicy | None = None,
    limiter: RateLimiter | None = None,
    give_up_on: tuple[type[BaseException], ...] = (),
    **kwargs,
) -> T:
    """Rate limit, then retry, then breaker — the standard outbound wrapper.

    Ordering matters. The breaker is outermost so that a tripped breaker costs
    nothing at all, and retries sit inside it so one logical call counts as a
    single success or failure rather than three.
    """
    breaker = get_breaker(name)
    metrics = get_metrics()

    def _attempt() -> T:
        if limiter is not None:
            limiter.acquire()
        return call_with_retry(fn, *args, policy=retry, give_up_on=give_up_on, **kwargs)

    # Recorded here because every outbound call in the app funnels through
    # this function — one instrumentation point rather than one per provider
    # method, which is how coverage gaps appear.
    try:
        result = breaker.call(_attempt)
    except Exception as exc:
        metrics.record_provider_call(name, ok=False, error=type(exc).__name__)
        raise
    metrics.record_provider_call(name, ok=True)
    return result
