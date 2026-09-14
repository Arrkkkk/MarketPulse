"""Resilience layer: breaker state machine, retry, token bucket."""

from __future__ import annotations

import time

import pytest

from marketpulse.platform.http import (
    BreakerPolicy,
    CircuitBreaker,
    CircuitOpenError,
    RateLimiter,
    RetryPolicy,
    call_with_retry,
    get_breaker,
    resilient,
)


class Boom(Exception):
    pass


def _fail():
    raise Boom("upstream is unhappy")


# --- circuit breaker -------------------------------------------------------


def test_breaker_stays_closed_below_threshold():
    breaker = CircuitBreaker("t", BreakerPolicy(failure_threshold=3))
    for _ in range(2):
        with pytest.raises(Boom):
            breaker.call(_fail)
    assert breaker.state == "closed"


def test_breaker_opens_at_threshold_and_then_fails_fast():
    breaker = CircuitBreaker("t", BreakerPolicy(failure_threshold=3, recovery_seconds=60))
    for _ in range(3):
        with pytest.raises(Boom):
            breaker.call(_fail)
    assert breaker.state == "open"

    # The next call must not reach the upstream at all.
    called = {"n": 0}

    def _spy():
        called["n"] += 1

    with pytest.raises(CircuitOpenError):
        breaker.call(_spy)
    assert called["n"] == 0


def test_a_success_resets_the_failure_count():
    breaker = CircuitBreaker("t", BreakerPolicy(failure_threshold=3))
    for _ in range(2):
        with pytest.raises(Boom):
            breaker.call(_fail)
    breaker.call(lambda: "ok")
    for _ in range(2):
        with pytest.raises(Boom):
            breaker.call(_fail)
    assert breaker.state == "closed", "the success should have cleared the earlier failures"


def test_breaker_half_opens_after_recovery_and_closes_on_a_good_probe():
    breaker = CircuitBreaker("t", BreakerPolicy(failure_threshold=1, recovery_seconds=0.05))
    with pytest.raises(Boom):
        breaker.call(_fail)
    assert breaker.state == "open"

    time.sleep(0.06)
    assert breaker.call(lambda: "recovered") == "recovered"
    assert breaker.state == "closed"


def test_a_failing_probe_reopens_the_breaker():
    breaker = CircuitBreaker("t", BreakerPolicy(failure_threshold=1, recovery_seconds=0.05))
    with pytest.raises(Boom):
        breaker.call(_fail)
    time.sleep(0.06)
    with pytest.raises(Boom):
        breaker.call(_fail)  # the probe
    assert breaker.state == "open"

    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "should not run")


def test_only_one_caller_becomes_the_probe():
    """Everyone else fails fast rather than queueing behind it.

    Queueing would send the recovering service exactly the burst of load the
    breaker exists to prevent.
    """
    breaker = CircuitBreaker("t", BreakerPolicy(failure_threshold=1, recovery_seconds=0.01))
    with pytest.raises(Boom):
        breaker.call(_fail)
    time.sleep(0.02)

    started = []

    def _slow():
        started.append(1)
        time.sleep(0.05)
        return "ok"

    import threading

    results: list[object] = []

    def _run():
        try:
            results.append(breaker.call(_slow))
        except CircuitOpenError as exc:
            results.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(started) == 1, "more than one probe was admitted"
    assert sum(isinstance(r, CircuitOpenError) for r in results) == 3


def test_get_breaker_returns_the_same_instance_per_name():
    assert get_breaker("svc") is get_breaker("svc")
    assert get_breaker("svc") is not get_breaker("other")


# --- retry -----------------------------------------------------------------


def test_retry_succeeds_after_transient_failures():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Boom("not yet")
        return "ok"

    assert call_with_retry(flaky, policy=RetryPolicy(attempts=3), sleep=lambda _: None) == "ok"
    assert attempts["n"] == 3


def test_retry_reraises_the_last_error_after_exhausting_attempts():
    attempts = {"n": 0}

    def always_fails():
        attempts["n"] += 1
        raise Boom("still down")

    with pytest.raises(Boom):
        call_with_retry(always_fails, policy=RetryPolicy(attempts=4), sleep=lambda _: None)
    assert attempts["n"] == 4


def test_give_up_on_wins_over_retry_on():
    attempts = {"n": 0}

    class Permanent(Exception):
        pass

    def permanent():
        attempts["n"] += 1
        raise Permanent()

    with pytest.raises(Permanent):
        call_with_retry(
            permanent,
            policy=RetryPolicy(attempts=5),
            give_up_on=(Permanent,),
            sleep=lambda _: None,
        )
    assert attempts["n"] == 1


def test_backoff_grows_and_is_bounded():
    policy = RetryPolicy(attempts=6, base_delay=1.0, max_delay=4.0, jitter=0.0)
    delays = [policy.delay_for(i) for i in range(5)]
    assert delays == [1.0, 2.0, 4.0, 4.0, 4.0]


# --- rate limiter ----------------------------------------------------------


def test_limiter_allows_the_initial_burst_immediately():
    limiter = RateLimiter(rate_per_second=1.0, burst=3)
    start = time.monotonic()
    for _ in range(3):
        limiter.acquire()
    assert time.monotonic() - start < 0.05


def test_limiter_blocks_once_the_bucket_is_drained():
    limiter = RateLimiter(rate_per_second=20.0, burst=1)
    limiter.acquire()
    start = time.monotonic()
    limiter.acquire()
    assert time.monotonic() - start >= 0.03


def test_limiter_rejects_a_nonpositive_rate():
    with pytest.raises(ValueError):
        RateLimiter(rate_per_second=0)


# --- composition -----------------------------------------------------------


def test_resilient_retries_then_trips_the_breaker():
    """One logical call is one breaker outcome, not one per retry."""
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise Boom()

    policy = RetryPolicy(attempts=2, base_delay=0.0, jitter=0.0)
    for _ in range(5):
        with pytest.raises(Boom):
            resilient("svc-compose", always_fails, retry=policy)

    assert calls["n"] == 10, "5 logical calls x 2 attempts"
    assert get_breaker("svc-compose").state == "open"

    with pytest.raises(CircuitOpenError):
        resilient("svc-compose", always_fails, retry=policy)
    assert calls["n"] == 10, "an open breaker must not reach the upstream"
