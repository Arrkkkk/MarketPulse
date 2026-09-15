"""Client tests, run against the real service over an ASGI transport.

These are the only tests that exercise both halves of the split together:
the client's serialization, its error mapping, and its SSE parsing against
the service that actually produces those responses. A socket is not
involved, so they run at unit-test speed.

The gap this closes was real — the client sat at 34% coverage while being
the single contract between the UI and the backend.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from marketpulse.ai.analyst import NewsAnalyst
from marketpulse.api.deps import get_analyst, get_market_service, get_news_service
from marketpulse.api.main import create_app
from marketpulse.client import MarketPulseClient, MarketPulseClientError, bars_to_frame
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
from tests.test_ai import FakeResponse, good_analysis, make_client


def build(
    price=None, crypto=None, news_us=None, news_global=None, analyst=None, cache=None
) -> MarketPulseClient:
    app = create_app()
    app.dependency_overrides[get_market_service] = lambda: MarketService(
        price or FakePriceProvider(histories={"AAPL": make_history("AAPL", rows=30)}),
        crypto or FakeCryptoProvider(),
        stock_symbols=("AAPL",),
        crypto_ids=("bitcoin",),
    )
    app.dependency_overrides[get_news_service] = lambda: NewsService(
        news_us or FakeNewsProvider("NewsAPI"),
        news_global or FakeNewsProvider("MarketAux"),
    )
    if analyst is not None:
        app.dependency_overrides[get_analyst] = lambda: analyst
    return MarketPulseClient(base_url="http://testserver", http_client=TestClient(app))


@pytest.fixture
def client() -> MarketPulseClient:
    return build()


# --- meta ------------------------------------------------------------------


def test_health_returns_true_when_the_service_is_up(client):
    assert client.health() is True


def test_health_returns_false_rather_than_raising_when_unreachable():
    """Used to render a status badge; it must not be able to break the page."""
    unreachable = MarketPulseClient(base_url="http://127.0.0.1:9")
    assert unreachable.health() is False


def test_info_round_trips(client):
    info = client.info()
    assert info.name == "marketpulse"
    assert isinstance(info.default_crypto_ids, list)


def test_readiness_round_trips(client):
    assert client.readiness().status in {"ready", "degraded"}


def test_metrics_round_trips(client):
    assert "cache" in client.metrics()


# --- market ----------------------------------------------------------------


def test_overview_deserializes_into_typed_quotes(client):
    overview = client.get_overview()
    assert overview.stocks[0].symbol == "AAPL"
    assert overview.crypto[0].coin_id == "bitcoin"


def test_history_round_trips_and_rebuilds_a_frame(client):
    response = client.get_history("AAPL")
    frame = bars_to_frame(response)
    assert len(frame) == response.count
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index.is_monotonic_increasing


def test_bars_survive_the_json_round_trip_exactly(client):
    """The wire format renames OHLCV to single letters; a mistake in that
    mapping would silently transpose price fields."""
    source = make_history("AAPL", rows=5).frame
    price = FakePriceProvider(histories={"AAPL": make_history("AAPL", rows=5)})
    frame = bars_to_frame(build(price=price).get_history("AAPL"))
    for column in ("open", "high", "low", "close"):
        assert list(frame[column]) == list(source[column]), f"{column} was transposed"


def test_bars_to_frame_handles_an_empty_response(client):
    from marketpulse.schema.api import HistoryResponse
    from marketpulse.schema.market import utcnow

    empty = HistoryResponse(symbol="X", interval="1d", as_of=utcnow(), count=0, total=0, bars=[])
    assert bars_to_frame(empty).empty


def test_bars_to_frame_handles_a_dst_transition_in_the_bar_timestamps():
    """Found from a real Render deploy: a year of daily AAPL bars crashed
    the client with `ValueError: Tz-aware datetime.datetime cannot be
    converted to datetime64 unless utc=True`.

    Root cause: yfinance localizes daily bars to the exchange timezone, so
    bars before and after a DST transition carry different fixed UTC
    offsets (EST vs EDT) once each one becomes its own aware `datetime` on
    the wire. `pd.DatetimeIndex` refuses to infer one index tz from a list
    mixing those offsets unless told to normalize to UTC.
    """
    from datetime import timedelta, timezone

    from marketpulse.schema.api import HistoryResponse, OHLCVBar
    from marketpulse.schema.market import utcnow

    est = timezone(timedelta(hours=-5))
    edt = timezone(timedelta(hours=-4))
    bars = [
        OHLCVBar(t=datetime(2026, 3, 1, 9, 30, tzinfo=est), o=1, h=1, l=1, c=1, v=1),
        OHLCVBar(t=datetime(2026, 3, 9, 9, 30, tzinfo=edt), o=2, h=2, l=2, c=2, v=1),
    ]
    response = HistoryResponse(
        symbol="AAPL", interval="1d", as_of=utcnow(), count=len(bars), total=len(bars), bars=bars
    )

    frame = bars_to_frame(response)

    assert len(frame) == 2
    assert frame.index.is_monotonic_increasing


def test_profile_round_trips():
    from marketpulse.schema.market import CompanyProfile

    price = FakePriceProvider(
        histories={"AAPL": make_history("AAPL")},
        profiles={"AAPL": CompanyProfile(symbol="AAPL", long_name="Apple", currency="USD")},
    )
    assert build(price=price).get_profile("AAPL").long_name == "Apple"


def test_crypto_history_round_trips(client):
    assert client.get_crypto_history("bitcoin", days="30").count > 0


def test_search_round_trips(client):
    assert client.search("AAPL").matches[0].symbol == "AAPL"


# --- news ------------------------------------------------------------------


def test_news_round_trips(client):
    assert client.get_news("AAPL").articles


def test_search_news_round_trips(client):
    assert client.search_news("bitcoin").source


# --- error mapping ---------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (SymbolNotFound("x", provider="p"), "symbol_not_found"),
        (RateLimited("x", provider="p"), "rate_limited"),
        (ProviderUnavailable("x", provider="p"), "provider_unavailable"),
        (ProviderNotConfigured("x", provider="p"), "provider_not_configured"),
    ],
)
def test_service_errors_surface_as_a_typed_client_error(error, code):
    client = build(price=FakePriceProvider(fail_with=error))
    with pytest.raises(MarketPulseClientError) as caught:
        client.get_history("AAPL")
    assert caught.value.code == code
    assert caught.value.request_id


def test_an_unreachable_service_reports_no_code():
    """code=None is the signal that the service never answered at all, as
    opposed to answering with an error — the UI renders those differently."""
    client = MarketPulseClient(base_url="http://127.0.0.1:9")
    with pytest.raises(MarketPulseClientError) as caught:
        client.get_overview()
    assert caught.value.code is None
    assert caught.value.is_transient


@pytest.mark.parametrize(
    ("code", "transient"),
    [
        ("rate_limited", True),
        ("provider_unavailable", True),
        ("provider_circuit_open", True),
        ("symbol_not_found", False),
        ("provider_not_configured", False),
    ],
)
def test_is_transient_classifies_whether_a_retry_could_help(code, transient):
    assert MarketPulseClientError("x", code=code).is_transient is transient


def test_retry_after_is_parsed_from_the_header():
    from marketpulse.platform.http import CircuitOpenError

    client = build(price=FakePriceProvider(fail_with=CircuitOpenError("yfinance", 30.0)))
    with pytest.raises(MarketPulseClientError) as caught:
        client.get_history("AAPL")
    assert caught.value.retry_after == 30


def test_none_valued_params_are_dropped_rather_than_sent_as_none(client):
    """max_points=None must not become `?max_points=None`, which 422s."""
    assert client.get_history("AAPL", max_points=None).count > 0


# --- streaming -------------------------------------------------------------


def test_stream_analysis_yields_text(cache):
    analyst = NewsAnalyst(client=make_client(stream_chunks=["Hello ", "world"]), cache=cache)
    chunks = list(build(analyst=analyst).stream_analysis("AAPL"))
    assert "".join(chunks).strip() == "Hello world"


def test_stream_insights_yields_text(cache):
    analyst = NewsAnalyst(client=make_client(stream_chunks=["Markets ", "moved"]), cache=cache)
    assert "Markets" in "".join(build(analyst=analyst).stream_insights("what happened?"))


def test_an_in_band_error_event_becomes_an_exception(cache):
    """A failure after the first byte cannot change the status code, so the
    service sends `event: error`. The client must not hand that to the UI as
    if it were analysis text."""
    analyst = NewsAnalyst(client=make_client(raises=Exception("429 rate limit")), cache=cache)
    with pytest.raises(MarketPulseClientError) as caught:
        list(build(analyst=analyst).stream_analysis("AAPL"))
    assert caught.value.code == "ai_stream_error"
    assert "AIRateLimited" in str(caught.value)


def test_the_done_event_terminates_the_stream_cleanly(cache):
    analyst = NewsAnalyst(client=make_client(stream_chunks=["a", "b", "c"]), cache=cache)
    chunks = list(build(analyst=analyst).stream_analysis("AAPL"))
    assert "" not in [c for c in chunks if c.startswith("event")]
    assert "".join(chunks).strip() == "abc"


def test_get_analysis_round_trips(cache):
    analyst = NewsAnalyst(client=make_client([FakeResponse(parsed=good_analysis())]), cache=cache)
    result = build(analyst=analyst).get_analysis("AAPL")
    assert result.analysis.sentiment.value in {"bullish", "bearish", "neutral", "mixed"}


# --- lifecycle -------------------------------------------------------------


def test_client_closes_cleanly(client):
    client.close()


def test_client_works_as_a_context_manager():
    app = create_app()
    with MarketPulseClient(base_url="http://testserver", http_client=TestClient(app)) as client:
        assert client.health() is True


# --- base URL scheme normalisation ------------------------------------------
#
# Render's Blueprint wiring hands the UI a bare "host:port" for the private
# API service — see docs/deploying.md and render.yaml. No render.yaml
# `fromService` property includes a scheme, so the client adds one itself.


def test_a_bare_host_and_port_is_given_an_http_scheme():
    client = MarketPulseClient(base_url="marketpulse-api-ab12:8000")
    try:
        assert client.base_url == "http://marketpulse-api-ab12:8000"
    finally:
        client.close()


def test_an_existing_scheme_is_left_alone():
    client = MarketPulseClient(base_url="https://api.example.com")
    try:
        assert client.base_url == "https://api.example.com"
    finally:
        client.close()


# --- Cloud Run identity token ----------------------------------------------
#
# See docs/deploying.md: a private Cloud Run API service is reachable only
# by a caller presenting a Google-signed ID token. These assert the two
# things that matter — it never fires off Google's infrastructure, and a
# real token becomes a real Authorization header — without a metadata
# server, which does not exist in a test environment.


def test_cloud_run_auth_is_skipped_for_a_plain_http_url():
    """Local dev, Docker Compose and Fly all use http:// base URLs — no
    metadata-server call should even be attempted for them."""
    from marketpulse.client.client import _cloud_run_id_token

    assert _cloud_run_id_token("http://localhost:8000") is None


def test_cloud_run_auth_fails_closed_off_google_infrastructure():
    """An https:// URL that is not actually a Cloud Run deployment (or any
    environment without metadata-server credentials, like this one) must
    not raise — the whole point is that this degrades to "no auth header"
    silently, the same way an absent API key does elsewhere in this
    project."""
    from marketpulse.client.client import _cloud_run_id_token

    assert _cloud_run_id_token("https://api-example.a.run.app") is None


def test_cloud_run_auth_header_is_attached_when_a_token_is_available(monkeypatch):
    """The one thing this module cannot exercise for real (there is no
    metadata server here): given a token, the client sends it."""
    import marketpulse.client.client as client_module

    monkeypatch.setattr(client_module, "_cloud_run_id_token", lambda audience: "fake-token")
    client = MarketPulseClient(base_url="https://api-example.a.run.app")
    try:
        assert client._http.headers["authorization"] == "Bearer fake-token"
    finally:
        client.close()
