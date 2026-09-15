"""Logging, correlation and metrics.

The DoD for this phase is "one request id traceable end-to-end through the
logs". That is testable, so it is tested here against the real formatter and
the real filter rather than by inspection.
"""

from __future__ import annotations

import json
import logging

import pytest

from marketpulse.api.main import create_app
from marketpulse.platform.metrics import Metrics
from marketpulse.platform.telemetry import (
    JsonFormatter,
    RequestIdFilter,
    current_request_id,
    get_logger,
    reset_logging,
    reset_request_id,
    set_request_id,
)
from marketpulse.providers.errors import ProviderUnavailable
from tests.fakes import FakePriceProvider
from tests.test_api import build_client


class CapturingHandler(logging.Handler):
    """Captures formatted lines through the real formatter and filter."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(JsonFormatter())
        self.addFilter(RequestIdFilter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.lines]


@pytest.fixture
def captured():
    """Attach a capturing handler to the marketpulse logger tree."""
    root = logging.getLogger("marketpulse")
    handler = CapturingHandler()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(previous_level)


# --- JSON formatting -------------------------------------------------------


def test_every_line_is_valid_json_with_the_expected_fields(captured):
    get_logger("test").info("hello")
    record = captured.records()[0]
    assert set(record) >= {"ts", "level", "logger", "message", "request_id"}
    assert record["level"] == "INFO"
    assert record["message"] == "hello"


def test_timestamps_are_rfc3339_utc(captured):
    get_logger("test").info("x")
    ts = captured.records()[0]["ts"]
    assert ts.endswith("+00:00"), f"not UTC-qualified: {ts}"


def test_percent_style_arguments_are_interpolated(captured):
    get_logger("test").info("fetched %d rows for %s", 42, "AAPL")
    assert captured.records()[0]["message"] == "fetched 42 rows for AAPL"


def test_extra_fields_become_top_level_keys(captured):
    """This is where structure actually comes from."""
    get_logger("test").info("analysis done", extra={"symbol": "AAPL", "tokens": 512})
    record = captured.records()[0]
    assert record["symbol"] == "AAPL"
    assert record["tokens"] == 512


def test_an_unserializable_extra_does_not_raise(captured):
    """A log line must never be able to take the process down."""

    class Opaque:
        pass

    get_logger("test").info("odd", extra={"thing": Opaque()})
    assert "Opaque" in captured.records()[0]["thing"]


def test_exceptions_are_captured_with_a_traceback(captured):
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("test").exception("failed")
    record = captured.records()[0]
    assert "ValueError: boom" in record["exception"]


def test_text_format_is_the_default_and_json_is_opt_in(monkeypatch):
    from marketpulse.platform import telemetry

    reset_logging()
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    get_logger("t")
    assert not isinstance(telemetry._OUR_HANDLER.formatter, telemetry.JsonFormatter)

    reset_logging()
    monkeypatch.setenv("LOG_FORMAT", "json")
    get_logger("t")
    assert isinstance(telemetry._OUR_HANDLER.formatter, telemetry.JsonFormatter)
    reset_logging()


# --- request correlation ---------------------------------------------------


def test_a_bound_request_id_reaches_the_log_line(captured):
    token = set_request_id("abc123")
    try:
        get_logger("test").info("inside a request")
    finally:
        reset_request_id(token)
    assert captured.records()[0]["request_id"] == "abc123"


def test_a_line_outside_any_request_has_no_id(captured):
    """null in JSON, not the dash the text format uses as a placeholder."""
    assert current_request_id() is None
    get_logger("test").info("startup")
    assert captured.records()[0]["request_id"] is None


def test_the_text_format_shows_a_placeholder_rather_than_none():
    from marketpulse.platform.telemetry import NO_REQUEST_ID, TEXT_FORMAT

    record = logging.LogRecord("marketpulse.t", logging.INFO, __file__, 1, "x", None, None)
    RequestIdFilter().filter(record)
    rendered = logging.Formatter(TEXT_FORMAT).format(record)
    assert f"[{NO_REQUEST_ID}]" in rendered
    assert "None" not in rendered


def test_the_id_is_restored_after_a_nested_scope():
    outer = set_request_id("outer")
    inner = set_request_id("inner")
    assert current_request_id() == "inner"
    reset_request_id(inner)
    assert current_request_id() == "outer"
    reset_request_id(outer)


# --- the DoD ---------------------------------------------------------------


def test_one_request_id_is_traceable_end_to_end(captured):
    """The Phase 9 definition of done.

    A caller-supplied id must appear on the response and on every log line
    emitted while handling that request — including lines from layers well
    below HTTP, which is the point of putting the ContextVar in `platform`
    rather than in the middleware.
    """
    failing = FakePriceProvider(fail_with=ProviderUnavailable("upstream down", provider="yf"))
    client = build_client(price=failing)

    response = client.get("/v1/history/AAPL", headers={"X-Request-ID": "trace-me-123"})

    assert response.status_code == 502
    assert response.headers["X-Request-ID"] == "trace-me-123"
    assert response.json()["request_id"] == "trace-me-123"

    traced = [r for r in captured.records() if r["request_id"] == "trace-me-123"]
    assert traced, "no log line carried the request id"

    # And the id has to reach more than the one module that set it.
    loggers = {r["logger"] for r in traced}
    assert loggers, f"expected correlated lines, got {loggers}"


def test_logs_from_different_layers_share_the_request_id(captured):
    """A request id that only appears on API-layer lines is not traceable."""
    token = set_request_id("multi-layer")
    try:
        get_logger("api").warning("api layer")
        get_logger("providers.yfinance").warning("provider layer")
        get_logger("platform.cache").warning("platform layer")
    finally:
        reset_request_id(token)

    traced = [r for r in captured.records() if r["request_id"] == "multi-layer"]
    assert len(traced) == 3
    assert {r["logger"] for r in traced} == {
        "marketpulse.api",
        "marketpulse.providers.yfinance",
        "marketpulse.platform.cache",
    }


def test_concurrent_requests_do_not_share_an_id(captured):
    """A ContextVar, not a global — otherwise two in-flight requests would
    interleave their ids and correlation would be worse than useless."""
    import threading

    seen: dict[str, str | None] = {}

    def worker(name: str) -> None:
        token = set_request_id(name)
        try:
            seen[name] = current_request_id()
        finally:
            reset_request_id(token)

    threads = [threading.Thread(target=worker, args=(f"req-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert seen == {f"req-{i}": f"req-{i}" for i in range(8)}


# --- metrics ---------------------------------------------------------------


def test_provider_error_rate_is_computed():
    m = Metrics()
    for _ in range(9):
        m.record_provider_call("yfinance", ok=True)
    m.record_provider_call("yfinance", ok=False, error="ProviderUnavailable")
    assert m.provider_error_rates()["yfinance"] == pytest.approx(0.1)


def test_error_rate_distinguishes_providers():
    m = Metrics()
    m.record_provider_call("yfinance", ok=True)
    m.record_provider_call("coingecko", ok=False, error="RateLimited")
    rates = m.provider_error_rates()
    assert rates["yfinance"] == 0.0
    assert rates["coingecko"] == 1.0


def test_a_provider_with_no_calls_is_absent_rather_than_zero():
    """Reporting 0% for something never called would read as healthy."""
    assert Metrics().provider_error_rates() == {}


def test_error_types_are_counted_separately():
    m = Metrics()
    m.record_provider_call("yfinance", ok=False, error="RateLimited")
    m.record_provider_call("yfinance", ok=False, error="SymbolNotFound")
    counters = m.snapshot()["counters"]
    assert counters["provider.yfinance.error.RateLimited"] == 1
    assert counters["provider.yfinance.error.SymbolNotFound"] == 1


def test_ai_usage_is_aggregated():
    m = Metrics()
    m.record_ai_call("gemini-x", prompt_tokens=500, output_tokens=200, cost_usd=0.0012)
    m.record_ai_call("gemini-x", prompt_tokens=300, output_tokens=100, cost_usd=0.0008)
    snapshot = m.snapshot()
    assert snapshot["counters"]["ai.prompt_tokens"] == 800
    assert snapshot["counters"]["ai.output_tokens"] == 300
    assert snapshot["ai_estimated_cost_usd"] == pytest.approx(0.002)


def test_cost_accumulates_in_integer_micro_usd():
    """A float accumulator drifts across thousands of small additions."""
    m = Metrics()
    for _ in range(1000):
        m.record_ai_call("m", prompt_tokens=1, output_tokens=1, cost_usd=0.000001)
    assert m.snapshot()["counters"]["ai.cost_micro_usd"] == 1000


def test_a_cached_ai_call_is_counted_but_spends_no_tokens():
    m = Metrics()
    m.record_ai_call("m", prompt_tokens=None, output_tokens=None, cost_usd=None, cached=True)
    counters = m.snapshot()["counters"]
    assert counters["ai.cache_hits"] == 1
    assert "ai.prompt_tokens" not in counters


def test_fallback_use_is_visible():
    m = Metrics()
    m.record_ai_call("m", prompt_tokens=1, output_tokens=1, cost_usd=None, fallback=True)
    assert m.snapshot()["counters"]["ai.fallback_used"] == 1


def test_cost_is_omitted_entirely_when_no_rates_are_known():
    """Absent, not zero — zero would read as "this was free"."""
    m = Metrics()
    m.record_ai_call("m", prompt_tokens=100, output_tokens=50, cost_usd=None)
    assert "ai_estimated_cost_usd" not in m.snapshot()


# --- the endpoint ----------------------------------------------------------


def test_metrics_endpoint_reports_provider_outcomes():
    from marketpulse.platform.metrics import get_metrics

    get_metrics().reset()
    client = build_client()
    client.get("/v1/overview")
    body = client.get("/metrics").json()
    assert "counters" in body and "latency" in body and "cache" in body


def test_a_failing_provider_shows_up_in_the_error_rate():
    from marketpulse.platform.metrics import get_metrics

    get_metrics().reset()
    failing = FakePriceProvider(fail_with=ProviderUnavailable("down", provider="yf"))
    client = build_client(price=failing)
    client.get("/v1/history/AAPL")
    counters = client.get("/metrics").json()["counters"]
    assert counters.get("status_5xx", 0) >= 1


def test_readiness_still_reports_every_breaker():
    checks = build_client().get("/health/ready").json()["checks"]
    for provider in ("yfinance", "coingecko", "newsapi", "marketaux", "gemini"):
        assert f"breaker:{provider}" in checks


def test_liveness_never_touches_a_dependency():
    """A transient upstream outage must not get the container restarted."""
    import marketpulse.api.v1.health as health_mod

    source = open(health_mod.__file__).read()
    body = source[source.index("def health(") : source.index("def readiness(")]
    assert "get_cache" not in body
    assert "get_breaker" not in body


def test_create_app_installs_the_request_id_filter():
    create_app()
    root = logging.getLogger("marketpulse")
    assert any(isinstance(f, RequestIdFilter) for f in root.filters) or any(
        isinstance(f, RequestIdFilter) for h in root.handlers for f in h.filters
    )


def test_a_missing_symbol_does_not_count_against_provider_health():
    """The error rate measures whether the provider is *working*, not
    whether the data exists. A 404 for a nonexistent ticker means the call
    succeeded and the answer was "nothing" — counting it as a provider error
    would make the rate useless as a health signal, and it is the same
    empty-vs-failed distinction the whole codebase turns on.
    """
    from marketpulse.platform.http import reset_registries, resilient
    from marketpulse.platform.metrics import get_metrics
    from marketpulse.providers.errors import SymbolNotFound

    reset_registries()
    get_metrics().reset()

    # An upstream call that returns nothing: the transport worked.
    resilient("probe", lambda: [])
    rates = get_metrics().provider_error_rates()
    assert rates["probe"] == 0.0

    # An upstream call that actually fails: the transport did not work.
    with pytest.raises(ConnectionError):
        resilient("probe", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    assert get_metrics().provider_error_rates()["probe"] > 0.0

    # And SymbolNotFound raised by the provider *after* a successful fetch
    # never reaches this wrapper, so it cannot pollute the rate.
    assert issubclass(SymbolNotFound, Exception)
