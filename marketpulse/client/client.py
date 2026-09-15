"""Typed HTTP client for the MarketPulse service.

The UI imports this and nothing else from the backend half. That is the
boundary: swap Streamlit for Next.js, or point this at a deployed service,
and nothing on either side has to change.

Errors arrive as one exception type carrying the service's machine-readable
code, so a caller can branch on `code` instead of matching on strings:

    try:
        history = client.get_history("AAPL")
    except MarketPulseClientError as exc:
        if exc.code == "symbol_not_found":
            ...

Adapted from JoshuaC215/agent-service-toolkit `src/client/client.py`.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pandas as pd

from marketpulse.ai.schemas import AnalysisResult
from marketpulse.schema.api import (
    HistoryResponse,
    NewsResponse,
    OverviewResponse,
    ProfileResponse,
    ReadinessResponse,
    SearchResponse,
    ServiceInfo,
)

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30.0


def _cloud_run_id_token(audience: str) -> str | None:
    """A Google-signed ID token scoped to `audience`, or None.

    Exists for exactly one deployment shape: the API deployed as a private
    Cloud Run service (`--no-allow-unauthenticated`), reachable only by a
    caller presenting a token Cloud Run's own front end verifies before a
    request ever reaches the FastAPI process — the Cloud Run equivalent of
    what Fly's private network and Compose's internal-only networking give
    for free. See docs/deploying.md's Cloud Run section.

    Every other shape gets None: local dev, Docker Compose, Fly, or a Cloud
    Run API deployed with --allow-unauthenticated. The metadata-server call
    this makes can only succeed on Google's own infrastructure, so off it
    this fails fast — exactly as harmless as an absent API key elsewhere in
    this project: a feature quietly not activated, not an error. Imported
    lazily rather than at module level so the one process that never talks
    to Cloud Run (a local Streamlit session, most of the time) never pays
    to import a Google Cloud library it will not use.
    """
    if not audience.startswith("https://"):
        return None
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        request = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(request, audience)
    except Exception:  # noqa: BLE001 — any failure here means "not applicable"
        return None


class MarketPulseClientError(Exception):
    """A request failed, or the service reported an error.

    `code` is the service's stable error code when it answered
    ("symbol_not_found", "rate_limited", ...) and None when it could not be
    reached at all — which is itself the distinction a caller needs.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after = retry_after
        super().__init__(message)

    @property
    def is_transient(self) -> bool:
        """Whether retrying could plausibly help."""
        return self.code in {
            "rate_limited",
            "provider_unavailable",
            "provider_circuit_open",
            None,
        }


class MarketPulseClient:
    """Synchronous client. Streamlit reruns are synchronous; so is this."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        """`http_client` exists so tests can hand in Starlette's TestClient,
        which is itself an httpx.Client wired straight to the ASGI app. That
        exercises the real client against the real service — serialization,
        status mapping, SSE parsing — without a socket, and it is the only
        way the two halves of the split get tested together.

        (httpx.ASGITransport is not usable here: it is async-only, so a
        synchronous httpx.Client cannot close it.)

        A scheme is added to `base_url` if one is missing. render.yaml
        currently runs one combined free-tier service (see
        docs/deploying.md), so nothing needs this today — it's here for a
        two-service Render deploy on a paid plan, where the Blueprint's
        `fromService` / `property: hostport` hands the UI a bare
        "host:port" for the private API service; no `fromService` property
        includes a scheme. Every other deployment shape already supplies a
        full URL (Fly's `http://x.internal:8000`, Cloud Run's
        `https://...run.app`, plain `http://localhost:8000` locally), so
        this is a no-op for them.
        """
        if "://" not in base_url:
            base_url = f"http://{base_url}"
        self.base_url = base_url.rstrip("/")
        self._owns_http = http_client is None
        if http_client is None:
            token = _cloud_run_id_token(self.base_url)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            http_client = httpx.Client(base_url=self.base_url, timeout=timeout, headers=headers)
        self._http = http_client

    def close(self) -> None:
        # An injected client belongs to whoever created it.
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> MarketPulseClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- transport --------------------------------------------------------

    def _get(self, path: str, **params) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise MarketPulseClientError(
                f"could not reach the MarketPulse service at {self.base_url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise self._to_error(response)
        return response.json()

    @staticmethod
    def _to_error(response: httpx.Response) -> MarketPulseClientError:
        request_id = response.headers.get("X-Request-ID")
        retry_after = response.headers.get("Retry-After")
        try:
            body = response.json()
            code = body.get("error")
            message = body.get("message") or response.text
        except Exception:  # noqa: BLE001 — a non-JSON error page
            code, message = None, response.text[:200]
        return MarketPulseClientError(
            message,
            code=code,
            status_code=response.status_code,
            request_id=request_id,
            retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
        )

    # -- meta -------------------------------------------------------------

    def info(self) -> ServiceInfo:
        return ServiceInfo.model_validate(self._get("/info"))

    def health(self) -> bool:
        try:
            return self._get("/health").get("status") == "ok"
        except MarketPulseClientError:
            return False

    def readiness(self) -> ReadinessResponse:
        return ReadinessResponse.model_validate(self._get("/health/ready"))

    def metrics(self) -> dict:
        return self._get("/metrics")

    # -- market -----------------------------------------------------------

    def get_overview(self) -> OverviewResponse:
        return OverviewResponse.model_validate(self._get("/v1/overview"))

    def get_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
        max_points: int | None = None,
    ) -> HistoryResponse:
        return HistoryResponse.model_validate(
            self._get(
                f"/v1/history/{symbol}",
                period=period,
                interval=interval,
                max_points=max_points,
            )
        )

    def search(self, query: str, limit: int = 8) -> SearchResponse:
        return SearchResponse.model_validate(self._get("/v1/search", q=query, limit=limit))

    def get_profile(self, symbol: str) -> ProfileResponse:
        return ProfileResponse.model_validate(self._get(f"/v1/profile/{symbol}"))

    def get_crypto_history(
        self, coin_id: str, days: str = "30", max_points: int | None = None
    ) -> HistoryResponse:
        return HistoryResponse.model_validate(
            self._get(f"/v1/crypto/history/{coin_id}", days=days, max_points=max_points)
        )

    # -- news -------------------------------------------------------------

    def get_news(self, symbol: str, limit: int = 5) -> NewsResponse:
        return NewsResponse.model_validate(self._get(f"/v1/news/{symbol}", limit=limit))

    def search_news(self, query: str, limit: int = 5) -> NewsResponse:
        return NewsResponse.model_validate(self._get("/v1/news", q=query, limit=limit))

    # -- ai ---------------------------------------------------------------

    def get_analysis(self, symbol: str, limit: int = 5, refresh: bool = False) -> AnalysisResult:
        return AnalysisResult.model_validate(
            self._get(f"/v1/analysis/{symbol}", limit=limit, refresh=refresh)
        )

    def stream_analysis(self, symbol: str, limit: int = 5) -> Iterator[str]:
        """Yield summary text as the model produces it."""
        yield from self._stream("GET", f"/v1/analysis/{symbol}/stream", params={"limit": limit})

    def stream_insights(self, question: str) -> Iterator[str]:
        yield from self._stream("POST", "/v1/insights", json={"question": question})

    def _stream(self, method: str, path: str, **kwargs) -> Iterator[str]:
        """Consume an SSE endpoint, yielding the text of each data event.

        An error arriving mid-stream cannot change the status code, so the
        service sends it as an `event: error` frame; this turns that back
        into an exception the caller can handle like any other.
        """
        try:
            with self._http.stream(method, path, timeout=120.0, **kwargs) as response:
                if response.status_code >= 400:
                    response.read()
                    raise self._to_error(response)

                event = "message"
                for line in response.iter_lines():
                    if not line:
                        event = "message"
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].lstrip()
                        if event == "error":
                            raise MarketPulseClientError(data, code="ai_stream_error")
                        if event == "done":
                            return
                        yield data
        except httpx.HTTPError as exc:
            raise MarketPulseClientError(f"stream failed: {exc}") from exc


def bars_to_frame(response: HistoryResponse) -> pd.DataFrame:
    """Rebuild a plotting-ready DataFrame from a HistoryResponse.

    Lives with the client because it is presentation glue: the wire format is
    a list of bars, and every charting caller wants the same frame back.
    """
    if not response.bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(
        {
            "open": [b.o for b in response.bars],
            "high": [b.h for b in response.bars],
            "low": [b.low for b in response.bars],
            "close": [b.c for b in response.bars],
            "volume": [b.v for b in response.bars],
        },
        index=pd.DatetimeIndex([b.t for b in response.bars], name="date"),
    )
    return frame.sort_index()
