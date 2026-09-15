"""Security properties, asserted rather than assumed.

The DoD for this phase included "no secret ever reaches a log". That is a
claim, and a claim that is not tested is a hope — so the largest section
here plants sentinel credentials and hunts for them in every place a secret
could plausibly escape: reprs, logs, error bodies, tracebacks, and the
metadata endpoints.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from marketpulse.api.main import create_app
from marketpulse.api.middlewares.ratelimit import (
    AI_TIER,
    DEFAULT_TIER,
    RateLimitMiddleware,
    Tier,
    tier_for,
)
from marketpulse.api.middlewares.security_headers import HEADERS
from marketpulse.config import Settings
from marketpulse.platform.http import RateLimiter
from tests.test_api import build_client

#: Distinctive enough that a substring search cannot produce a false hit.
SENTINEL = "sk-SENTINEL-d41d8cd98f00b204e9800998ecf8427e"

#: A bucket small enough to exhaust in a handful of requests, and refilling
#: slowly enough that the refill cannot rescue the test.
#:
#: The production tiers are NOT usable here. Asserting that 90 requests
#: outrun a bucket refilling at 10/s is really asserting that the loop
#: finishes within three seconds — true on a laptop, false on a CI runner.
#: That version passed locally every time and failed on the first CI run.
TINY = Tier("default", rate_per_second=0.001, burst=3)


def limited_app():
    """An app whose rate limits are small enough to hit deterministically."""
    return create_app(rate_limit_tiers=(TINY, TINY))


@pytest.fixture
def settings_with_secrets(monkeypatch) -> Settings:
    for name in ("GEMINI_API_KEY", "NEWS_API_KEY", "MARKETAUX_API_KEY"):
        monkeypatch.setenv(name, SENTINEL)
    return Settings()


# --- secrets must not escape ----------------------------------------------


def test_repr_of_settings_does_not_contain_the_key(settings_with_secrets):
    assert SENTINEL not in repr(settings_with_secrets)
    assert SENTINEL not in str(settings_with_secrets)


def test_repr_of_an_individual_secret_is_masked(settings_with_secrets):
    assert repr(settings_with_secrets.GEMINI_API_KEY) == "SecretStr('**********')"
    assert SENTINEL not in f"{settings_with_secrets.GEMINI_API_KEY}"


def test_model_dump_does_not_contain_the_key(settings_with_secrets):
    assert SENTINEL not in str(settings_with_secrets.model_dump())


def test_unwrapping_requires_an_explicit_call(settings_with_secrets):
    """The value is reachable — deliberately, and only on purpose."""
    assert settings_with_secrets.GEMINI_API_KEY.get_secret_value() == SENTINEL


def test_a_traceback_through_settings_does_not_carry_the_key(settings_with_secrets):
    import traceback

    try:
        raise RuntimeError(f"context: {settings_with_secrets}")
    except RuntimeError:
        assert SENTINEL not in traceback.format_exc()


def test_no_secret_appears_in_logs_during_a_full_request_cycle(monkeypatch, caplog):
    """The DoD claim, exercised end to end rather than asserted."""
    for name in ("GEMINI_API_KEY", "NEWS_API_KEY", "MARKETAUX_API_KEY"):
        monkeypatch.setenv(name, SENTINEL)

    with caplog.at_level(logging.DEBUG):
        client = build_client()
        for path in ("/health", "/health/ready", "/info", "/metrics", "/v1/overview"):
            client.get(path)
        client.get("/v1/history/NOPE")  # an error path too

    assert SENTINEL not in caplog.text


def test_metadata_endpoints_report_capability_not_credentials(monkeypatch):
    """/info says whether AI is available; it must not say with what."""
    monkeypatch.setenv("GEMINI_API_KEY", SENTINEL)
    body = build_client().get("/info").text
    assert SENTINEL not in body


@pytest.mark.parametrize("path", ["/health", "/health/ready", "/info", "/metrics"])
def test_no_endpoint_echoes_a_configured_secret(monkeypatch, path):
    for name in ("GEMINI_API_KEY", "NEWS_API_KEY", "MARKETAUX_API_KEY"):
        monkeypatch.setenv(name, SENTINEL)
    assert SENTINEL not in build_client().get(path).text


def test_an_error_body_never_carries_a_secret(monkeypatch):
    from marketpulse.providers.errors import ProviderUnavailable
    from tests.fakes import FakePriceProvider

    monkeypatch.setenv("NEWS_API_KEY", SENTINEL)
    failing = FakePriceProvider(
        fail_with=ProviderUnavailable(f"failed with {SENTINEL}", provider="p")
    )
    assert SENTINEL not in build_client(price=failing).get("/v1/history/AAPL").text


def test_env_example_contains_no_real_looking_values():
    """A committed .env.example with a value in it is how keys leak."""
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / ".env.example"
    for line in example.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            _, _, value = line.partition("=")
            assert not value.strip(), f"{line!r} has a value; it should be blank"


def test_dotenv_is_gitignored():
    from pathlib import Path

    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()
    assert ".env" in gitignore.split()


# --- rate limiting ---------------------------------------------------------


def test_ai_endpoints_get_the_strict_tier():
    assert tier_for("/v1/analysis/AAPL") is AI_TIER
    assert tier_for("/v1/analysis/AAPL/stream") is AI_TIER
    assert tier_for("/v1/insights") is AI_TIER


def test_cheap_endpoints_get_the_default_tier():
    assert tier_for("/v1/overview") is DEFAULT_TIER
    assert tier_for("/v1/history/AAPL") is DEFAULT_TIER
    assert tier_for("/v1/search") is DEFAULT_TIER


def test_the_ai_tier_is_much_tighter_than_the_default():
    """AI calls cost money; a cached overview does not."""
    assert AI_TIER.rate_per_second < DEFAULT_TIER.rate_per_second
    assert AI_TIER.burst < DEFAULT_TIER.burst


def test_a_burst_is_allowed_then_requests_are_rejected():
    client = TestClient(limited_app())
    codes = [client.get("/v1/overview").status_code for _ in range(6)]
    assert codes[:3] == [200, 200, 200], "the burst of 3 should have been served"
    assert codes[3:] == [429, 429, 429], "everything past the burst should be rejected"


def test_a_rejection_carries_retry_after_and_the_standard_envelope():
    client = TestClient(limited_app())
    for _ in range(TINY.burst):
        client.get("/v1/overview")
    response = client.get("/v1/overview")

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    body = response.json()
    assert body["error"] == "rate_limited"
    assert set(body) == {"error", "message", "detail", "request_id"}


def test_health_is_never_rate_limited():
    """An orchestrator polling /health must not be throttled into reporting
    the service as down. Uses the tiny tier, so a limit that applied to
    /health would be hit within a few requests."""
    client = TestClient(limited_app())
    codes = {client.get("/health").status_code for _ in range(20)}
    assert codes == {200}


def test_rate_limiting_can_be_disabled_for_tests_and_local_use(monkeypatch):
    monkeypatch.setenv("MARKETPULSE_RATELIMIT", "0")
    client = TestClient(limited_app())
    codes = [client.get("/v1/overview").status_code for _ in range(TINY.burst + 5)]
    assert 429 not in codes


def test_forged_forwarded_headers_are_ignored_without_a_proxy():
    """Otherwise a caller mints a fresh identity per request and the limit
    does nothing at all."""
    app = limited_app()
    assert app.state.trust_proxy_headers is False
    client = TestClient(app)
    codes = [
        client.get("/v1/overview", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
        for i in range(TINY.burst + 3)
    ]
    assert 429 in codes, "a forged X-Forwarded-For bypassed the rate limit"


def test_forwarded_headers_are_honoured_when_a_proxy_is_declared(monkeypatch):
    monkeypatch.setenv("MARKETPULSE_TRUST_PROXY", "1")
    app = create_app()
    assert app.state.trust_proxy_headers is True


def test_try_acquire_never_blocks_and_refills_over_time():
    limiter = RateLimiter(rate_per_second=50.0, burst=2)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False

    import time

    time.sleep(0.05)
    assert limiter.try_acquire() is True


def test_seconds_until_available_is_positive_once_drained():
    limiter = RateLimiter(rate_per_second=1.0, burst=1)
    limiter.try_acquire()
    assert limiter.seconds_until_available() > 0


def test_the_client_table_is_bounded():
    """A spray of forged addresses must not grow memory without limit."""
    from marketpulse.api.middlewares.ratelimit import MAX_TRACKED_CLIENTS

    middleware = RateLimitMiddleware(app=None)
    for i in range(MAX_TRACKED_CLIENTS + 50):
        middleware._bucket(f"10.0.{i // 256}.{i % 256}", DEFAULT_TIER)
    assert len(middleware._buckets) <= MAX_TRACKED_CLIENTS


# --- security headers ------------------------------------------------------


@pytest.mark.parametrize("header", sorted(HEADERS))
def test_security_headers_are_present(header):
    assert build_client().get("/health").headers.get(header) == HEADERS[header]


def test_headers_are_present_on_error_responses_too():
    """An error page is exactly where framing and sniffing protections
    matter, so they must not be skipped on the failure path."""
    response = build_client().get("/v1/history/NOSUCHSYMBOL")
    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_hsts_is_not_asserted_by_the_service():
    """It belongs at the TLS terminator; asserting it here would pin a
    developer's browser to https://localhost."""
    assert "Strict-Transport-Security" not in build_client().get("/health").headers


# --- input hardening (regression guards) -----------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "../../../../etc/passwd",
        "'; DROP TABLE users;--",
        "<script>alert(1)</script>",
        "%2e%2e%2f",
        "A" * 500,
        "AAPL AAPL",
        "AAPL;ls",
    ],
)
def test_hostile_symbols_are_rejected_at_the_edge(payload):
    response = build_client().get(f"/v1/history/{payload}")
    assert response.status_code in (404, 422), f"{payload!r} reached the provider"


@pytest.mark.parametrize("payload", ["\x00nul", "AAPL\nX", "AAPL\r\nHost: evil", "AAPL\tX"])
def test_control_characters_are_rejected_by_the_validator(payload):
    """Asserted against the validator rather than over HTTP: httpx refuses to
    construct a URL containing a control character, so the request never
    exists to be tested. That is defence in depth — the transport rejects it
    too — but it means the edge check has to be exercised directly."""
    from marketpulse.schema.api import is_valid_symbol

    assert not is_valid_symbol(payload)


def test_an_oversized_insights_question_is_rejected():
    body = {"question": "x" * 50_000}
    assert build_client().post("/v1/insights", json=body).status_code == 422


def test_cors_is_an_allowlist_not_a_wildcard():
    from marketpulse.api import main

    source = open(main.__file__).read()
    assert 'allow_origins=["*"]' not in source
    assert "localhost:8501" in source


# --- middleware ordering ---------------------------------------------------

#: Outermost to innermost. Starlette's add_middleware prepends, so this list
#: is the reverse of the registration order — which is exactly the mistake
#: that produced 429s with no request id and rejected requests counted as
#: served latency.
EXPECTED_STACK = [
    "CORSMiddleware",  # headers must reach error responses too
    "ErrorHandlerMiddleware",  # outside everything it protects
    "SecurityHeadersMiddleware",
    "RequestIdMiddleware",  # outside RateLimit, so a 429 gets an id
    "RateLimitMiddleware",  # outside Timing, so rejects are not timed
    "TimingMiddleware",
    "GZipMiddleware",  # innermost, so timing includes compression
]


def test_middleware_stack_is_in_the_intended_order():
    stack = [m.cls.__name__ for m in create_app().user_middleware]
    assert stack == EXPECTED_STACK


def test_a_rate_limited_response_carries_a_request_id():
    """It did not, because RateLimit was registered outside RequestId."""
    client = TestClient(limited_app())
    for _ in range(TINY.burst):
        client.get("/v1/overview")
    response = client.get("/v1/overview")

    assert response.status_code == 429
    assert response.json()["request_id"], "429 body has no request id"
    assert response.headers.get("X-Request-ID")


def test_a_rate_limited_response_still_carries_security_headers():
    client = TestClient(limited_app())
    for _ in range(TINY.burst):
        client.get("/v1/overview")
    response = client.get("/v1/overview")

    assert response.status_code == 429
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_rejected_requests_are_not_counted_as_served_latency():
    """Timing sits inside RateLimit, so a 429 never reaches it."""
    from marketpulse.platform.metrics import get_metrics

    get_metrics().reset()
    client = TestClient(limited_app())
    codes = [client.get("/v1/overview").status_code for _ in range(TINY.burst + 3)]
    assert 429 in codes

    served = get_metrics().snapshot()["latency"].get("GET /v1/overview", {})
    assert served.get("count", 0) == codes.count(200)


def test_the_ai_tier_applies_to_ai_paths_on_a_configured_app():
    """The tier split must survive tier injection, not just exist in the
    module-level defaults."""
    strict = Tier("ai", rate_per_second=0.001, burst=1)
    generous = Tier("default", rate_per_second=1000.0, burst=1000)
    app = create_app(rate_limit_tiers=(generous, strict))
    middleware = next(m for m in app.user_middleware if m.cls is RateLimitMiddleware)
    instance = RateLimitMiddleware(app=None, **middleware.kwargs)
    assert instance.tier("/v1/analysis/AAPL") is strict
    assert instance.tier("/v1/insights") is strict
    assert instance.tier("/v1/overview") is generous
