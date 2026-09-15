"""API tests, driven through FastAPI's TestClient with fake services.

`dependency_overrides` swaps the real services for fakes, so these exercise
routing, validation, serialization and the error envelope without touching a
network or a provider.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from marketpulse.api.deps import get_market_service, get_news_service
from marketpulse.api.main import create_app
from marketpulse.platform.http import CircuitOpenError
from marketpulse.providers.errors import (
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
    SymbolNotFound,
)
from marketpulse.services.market_service import MarketService
from marketpulse.services.news_service import NewsService
from tests.fakes import (
    FakeCryptoProvider,
    FakeNewsProvider,
    FakePriceProvider,
    make_history,
)


def build_client(
    price: FakePriceProvider | None = None,
    crypto: FakeCryptoProvider | None = None,
    news_us: FakeNewsProvider | None = None,
    news_global: FakeNewsProvider | None = None,
) -> TestClient:
    app = create_app()
    market = MarketService(
        price or FakePriceProvider(histories={"AAPL": make_history("AAPL")}),
        crypto or FakeCryptoProvider(),
        stock_symbols=("AAPL",),
        crypto_ids=("bitcoin",),
    )
    news = NewsService(
        news_us or FakeNewsProvider("NewsAPI"),
        news_global or FakeNewsProvider("MarketAux"),
    )
    app.dependency_overrides[get_market_service] = lambda: market
    app.dependency_overrides[get_news_service] = lambda: news
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return build_client()


# --- meta ------------------------------------------------------------------


def test_health_is_up(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readiness_reports_breaker_and_cache_state(client):
    body = client.get("/health/ready").json()
    assert "cache" in body["checks"]
    assert any(k.startswith("breaker:") for k in body["checks"])


def test_info_advertises_capabilities(client):
    body = client.get("/info").json()
    assert body["name"] == "marketpulse"
    assert isinstance(body["ai_enabled"], bool)


def test_openapi_schema_is_served(client):
    assert client.get("/openapi.json").status_code == 200


def test_every_response_carries_a_request_id(client):
    assert client.get("/health").headers.get("X-Request-ID")


def test_a_sane_caller_supplied_request_id_is_honoured(client):
    r = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"


def test_a_hostile_request_id_is_replaced(client):
    """A caller must not be able to inject arbitrary text into our logs."""
    r = client.get("/health", headers={"X-Request-ID": "x" * 200 + "\ninjected"})
    assert r.headers["X-Request-ID"] != "x" * 200 + "\ninjected"


# --- market ----------------------------------------------------------------


def test_overview_returns_stocks_and_crypto(client):
    body = client.get("/v1/overview").json()
    assert body["stocks"][0]["symbol"] == "AAPL"
    assert body["crypto"][0]["coin_id"] == "bitcoin"
    assert body["degraded"] is False


def test_overview_is_200_but_degraded_when_one_asset_class_fails():
    """A dashboard showing crypto beats one showing nothing."""
    crypto = FakeCryptoProvider(fail_with=ProviderUnavailable("down", provider="cg"))
    r = build_client(crypto=crypto).get("/v1/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert "crypto" in body["failures"]
    assert body["stocks"], "the healthy asset class should still be present"


def test_history_returns_bars(client):
    body = client.get("/v1/history/AAPL").json()
    assert body["symbol"] == "AAPL"
    assert body["count"] == len(body["bars"])
    assert {"t", "o", "h", "l", "c", "v"} <= set(body["bars"][0])


def test_history_downsamples_and_reports_the_original_size():
    price = FakePriceProvider(histories={"AAPL": make_history("AAPL", rows=500)})
    body = build_client(price=price).get("/v1/history/AAPL?max_points=50").json()
    assert body["count"] <= 51  # +1 for the always-retained last bar
    assert body["total"] == 500
    assert body["count"] < body["total"]


def test_downsampling_keeps_the_most_recent_bar():
    """That bar carries the current price; a stride can land just short of it."""
    history = make_history("AAPL", rows=500)
    expected = float(history.frame.iloc[-1]["close"])
    price = FakePriceProvider(histories={"AAPL": history})
    body = build_client(price=price).get("/v1/history/AAPL?max_points=50").json()
    assert body["bars"][-1]["c"] == expected


def test_small_series_are_not_downsampled(client):
    body = client.get("/v1/history/AAPL").json()
    assert body["count"] == body["total"]


# --- validation ------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    [
        "'; DROP TABLE--",
        "../../etc/passwd",
        "A" * 40,
        "<script>",
        "AA PL",
        "",
        "AA^PL",  # caret is legal only as a leading index marker
    ],
)
def test_malformed_symbols_are_rejected_before_reaching_a_provider(client, symbol):
    """The old UI passed raw text input to yfinance and into an LLM prompt."""
    r = client.get(f"/v1/history/{symbol}")
    assert r.status_code in (404, 422)


@pytest.mark.parametrize(
    "symbol",
    ["AAPL", "RELIANCE.NS", "0005.HK", "BRK-B", "^GSPC", "^FTSE", "TCS.NS", "MBG.DE"],
)
def test_legitimate_symbols_are_accepted(symbol):
    price = FakePriceProvider(histories={symbol: make_history(symbol)})
    assert build_client(price=price).get(f"/v1/history/{symbol}").status_code == 200


def test_symbols_are_upper_cased(client):
    assert client.get("/v1/history/aapl").json()["symbol"] == "AAPL"


@pytest.mark.parametrize("period", ["7y", "forever", "1; rm -rf /"])
def test_invalid_period_is_rejected(client, period):
    assert client.get(f"/v1/history/AAPL?period={period}").status_code == 422


def test_max_points_is_bounded(client):
    assert client.get("/v1/history/AAPL?max_points=999999").status_code == 422


# --- error envelope --------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (SymbolNotFound("x", provider="p"), 404, "symbol_not_found"),
        (RateLimited("x", provider="p"), 429, "rate_limited"),
        (ProviderUnavailable("x", provider="p"), 502, "provider_unavailable"),
        (ProviderNotConfigured("x", provider="p"), 503, "provider_not_configured"),
    ],
)
def test_provider_errors_map_to_the_right_status_and_code(error, status, code):
    r = build_client(price=FakePriceProvider(fail_with=error)).get("/v1/history/AAPL")
    assert r.status_code == status
    assert r.json()["error"] == code


def test_every_error_uses_the_same_envelope():
    err = SymbolNotFound("x", provider="p")
    body = build_client(price=FakePriceProvider(fail_with=err)).get("/v1/history/AAPL").json()
    assert set(body) == {"error", "message", "detail", "request_id"}
    assert body["request_id"]


def test_errors_do_not_leak_internals_by_default():
    """`detail` is populated only under DEBUG logging."""
    err = ProviderUnavailable("secret internal context", provider="p")
    body = build_client(price=FakePriceProvider(fail_with=err)).get("/v1/history/AAPL").json()
    assert body["detail"] is None


def test_an_open_circuit_returns_503_with_retry_after():
    err = CircuitOpenError("yfinance", 12.0)
    r = build_client(price=FakePriceProvider(fail_with=err)).get("/v1/history/AAPL")
    assert r.status_code == 503
    assert r.json()["error"] == "provider_circuit_open"
    assert r.headers["Retry-After"] == "12"


def test_an_unexpected_error_is_a_500_with_no_traceback():
    price = FakePriceProvider(fail_with=RuntimeError("boom with internals"))
    r = build_client(price=price).get("/v1/history/AAPL")
    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "internal_error"
    assert "boom with internals" not in str(body["message"])


# --- news ------------------------------------------------------------------


def test_news_for_a_symbol_routes_and_returns_articles():
    price = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    body = build_client(price=price).get("/v1/news/AAPL").json()
    assert body["articles"]
    assert body["source"]


def test_news_search_works_without_a_symbol(client):
    body = client.get("/v1/news?q=bitcoin").json()
    assert body["source"] == "MarketAux", "no exchange means MarketAux first"


def test_news_search_requires_a_query(client):
    assert client.get("/v1/news").status_code == 422


def test_news_failure_surfaces_as_502_not_as_empty_articles():
    """The bug this whole refactor exists to fix."""
    failing = FakeNewsProvider("NewsAPI", fail_with=RateLimited("429", provider="n"))
    failing2 = FakeNewsProvider("MarketAux", fail_with=RateLimited("429", provider="m"))
    r = build_client(news_us=failing, news_global=failing2).get("/v1/news?q=apple")
    assert r.status_code == 502
    assert "articles" not in r.json()


def test_no_news_returns_200_with_an_empty_list():
    """Genuine emptiness is an answer, and must not look like a failure."""
    empty = FakeNewsProvider("NewsAPI", articles=[])
    empty2 = FakeNewsProvider("MarketAux", articles=[])
    r = build_client(news_us=empty, news_global=empty2).get("/v1/news?q=obscureco")
    assert r.status_code == 200
    assert r.json()["articles"] == []


def test_news_without_any_configured_provider_is_503_not_empty():
    """ "No API key" must not render as "no news exists for this company"."""
    us = FakeNewsProvider("NewsAPI", configured=False)
    glob = FakeNewsProvider("MarketAux", configured=False)
    r = build_client(news_us=us, news_global=glob).get("/v1/news?q=apple")
    assert r.status_code == 503
    assert r.json()["error"] == "provider_not_configured"


# --- ai endpoints ----------------------------------------------------------


def _analyst_client(analyst):
    """A TestClient whose analyst dependency is overridden."""
    from marketpulse.api.deps import get_analyst

    tc = build_client()
    tc.app.dependency_overrides[get_analyst] = lambda: analyst
    return tc


def test_analysis_returns_the_structured_result(cache):
    from marketpulse.ai.analyst import NewsAnalyst
    from tests.test_ai import FakeResponse, good_analysis, make_client

    analyst = NewsAnalyst(client=make_client([FakeResponse(parsed=good_analysis())]), cache=cache)
    r = _analyst_client(analyst).get("/v1/analysis/AAPL")
    assert r.status_code == 200
    body = r.json()
    assert body["analysis"]["sentiment"] in {"bullish", "bearish", "neutral", "mixed"}
    assert 0.0 <= body["analysis"]["confidence"] <= 1.0
    assert body["model"]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        ("AINotConfigured", 503, "ai_not_configured"),
        ("AIRateLimited", 429, "ai_rate_limited"),
        ("AIContextTooLong", 413, "ai_context_too_long"),
        ("AIContentFiltered", 422, "ai_content_filtered"),
        ("AIInvalidOutput", 502, "ai_invalid_output"),
        ("AIUnavailable", 502, "ai_unavailable"),
    ],
)
def test_ai_errors_map_to_the_right_status_and_code(error, status, code):
    import marketpulse.ai.errors as ai_errors

    exc_type = getattr(ai_errors, error)

    class Failing:
        model = "m"

        def analyze(self, *a, **k):
            raise exc_type("boom", model="m")

    r = _analyst_client(Failing()).get("/v1/analysis/AAPL")
    assert r.status_code == status
    assert r.json()["error"] == code


def test_ai_errors_use_the_same_envelope_as_everything_else():
    from marketpulse.ai.errors import AIUnavailable

    class Failing:
        model = "m"

        def analyze(self, *a, **k):
            raise AIUnavailable("boom", model="m")

    body = _analyst_client(Failing()).get("/v1/analysis/AAPL").json()
    assert set(body) == {"error", "message", "detail", "request_id"}


def test_analysis_rejects_a_malformed_symbol(cache):
    from marketpulse.ai.analyst import NewsAnalyst
    from tests.test_ai import FakeResponse, good_analysis, make_client

    analyst = NewsAnalyst(client=make_client([FakeResponse(parsed=good_analysis())]), cache=cache)
    assert _analyst_client(analyst).get("/v1/analysis/<script>").status_code in (404, 422)


def test_streaming_endpoint_emits_sse_frames(cache):
    from marketpulse.ai.analyst import NewsAnalyst
    from tests.test_ai import make_client

    analyst = NewsAnalyst(client=make_client(stream_chunks=["Hello ", "world"]), cache=cache)
    r = _analyst_client(analyst).get("/v1/analysis/AAPL/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "data: Hello" in r.text
    assert "event: done" in r.text


def test_a_mid_stream_failure_arrives_as_an_error_event(cache):
    """The status code is already 200 by then, so it has to travel in-band."""
    from marketpulse.ai.analyst import NewsAnalyst
    from tests.test_ai import make_client

    analyst = NewsAnalyst(client=make_client(raises=Exception("429 rate limit")), cache=cache)
    r = _analyst_client(analyst).get("/v1/analysis/AAPL/stream")
    assert r.status_code == 200
    assert "event: error" in r.text
    assert "AIRateLimited" in r.text


def test_insights_streams(cache):
    from marketpulse.ai.analyst import NewsAnalyst
    from tests.test_ai import make_client

    analyst = NewsAnalyst(client=make_client(stream_chunks=["Markets are "]), cache=cache)
    r = _analyst_client(analyst).post("/v1/insights", json={"question": "What is up?"})
    assert r.status_code == 200
    assert "data: Markets are" in r.text


@pytest.mark.parametrize("question", ["", "a", "x" * 2000])
def test_insights_rejects_out_of_range_questions(question, cache):
    from marketpulse.ai.analyst import NewsAnalyst
    from tests.test_ai import make_client

    analyst = NewsAnalyst(client=make_client(stream_chunks=["ok"]), cache=cache)
    r = _analyst_client(analyst).post("/v1/insights", json={"question": question})
    assert r.status_code == 422


# --- performance instrumentation -------------------------------------------


def test_latency_labels_use_the_full_route_template(client):
    """FastAPI's lazy inclusion reports `/overview` for `/v1/overview`; an
    un-prefixed label would collide across API versions."""
    from marketpulse.platform.metrics import get_metrics

    get_metrics().reset()
    client.get("/v1/overview")
    client.get("/v1/history/AAPL")
    labels = set(get_metrics().snapshot()["latency"])
    assert "GET /v1/overview" in labels
    assert "GET /v1/history/{symbol}" in labels


def test_concrete_symbols_share_one_label(client):
    """Otherwise every ticker leaks a new metric series."""
    from marketpulse.platform.metrics import get_metrics

    get_metrics().reset()
    for _ in range(3):
        client.get("/v1/history/AAPL")
    labels = get_metrics().snapshot()["latency"]
    assert labels["GET /v1/history/{symbol}"]["count"] == 3
    assert not any("AAPL" in k for k in labels)


def test_every_response_carries_server_timing(client):
    assert "Server-Timing" in client.get("/v1/overview").headers


def test_metrics_endpoint_exposes_latency_and_cache(client):
    client.get("/v1/overview")
    body = client.get("/metrics").json()
    assert "latency" in body and "cache" in body and "breakers" in body
    assert "gemini" in body["breakers"]


def test_history_defaults_to_a_chart_sized_payload(client):
    """800 points, not 2000: a 1200px plot cannot show more than about two
    points per pixel, so the rest is bytes nobody can see."""
    from marketpulse.api.v1.market import DEFAULT_BARS, MAX_BARS

    assert DEFAULT_BARS < MAX_BARS
    body = client.get("/v1/history/AAPL").json()
    assert body["count"] <= DEFAULT_BARS


# --- symbol search ---------------------------------------------------------


def test_search_returns_matches():
    price = FakePriceProvider(histories={s: make_history(s) for s in ("AAPL", "AMZN")})
    body = build_client(price=price).get("/v1/search?q=A").json()
    assert body["query"] == "A"
    assert {m["symbol"] for m in body["matches"]} == {"AAPL", "AMZN"}


def test_search_returns_200_and_an_empty_list_when_nothing_matches(client):
    """A well-formed query matching nothing is an answer, not a failure."""
    r = client.get("/v1/search?q=zzzznotacompany")
    assert r.status_code == 200
    assert r.json()["matches"] == []


def test_search_requires_a_query(client):
    assert client.get("/v1/search").status_code == 422


def test_search_rejects_an_overlong_query(client):
    assert client.get(f"/v1/search?q={'x' * 200}").status_code == 422


def test_search_respects_the_limit():
    price = FakePriceProvider(histories={f"AA{i}": make_history(f"AA{i}") for i in range(20)})
    body = build_client(price=price).get("/v1/search?q=AA&limit=3").json()
    assert len(body["matches"]) == 3


def test_search_route_is_not_shadowed_by_the_profile_route(client):
    """/v1/search must not be parsed as /v1/profile/{symbol}-style routing.
    Declaring the literal path first is what prevents that."""
    assert client.get("/v1/search?q=AAPL").status_code == 200


def test_search_failure_propagates_as_an_error_not_an_empty_list():
    price = FakePriceProvider(fail_with=ProviderUnavailable("down", provider="yf"))
    r = build_client(price=price).get("/v1/search?q=apple")
    assert r.status_code == 502
    assert "matches" not in r.json()
