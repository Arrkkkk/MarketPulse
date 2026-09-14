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

import httpx
import pandas as pd

from marketpulse.schema.api import (
    HistoryResponse,
    NewsResponse,
    OverviewResponse,
    ProfileResponse,
    ReadinessResponse,
    ServiceInfo,
)

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30.0


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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
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
