"""AI analysis endpoints."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from marketpulse.ai.errors import AIError
from marketpulse.ai.schemas import AnalysisResult
from marketpulse.api.deps import AnalystDep, MarketServiceDep, NewsServiceDep
from marketpulse.schema.api import InsightsRequest, Symbol

router = APIRouter(tags=["ai"])


def _articles_for(symbol: str, news, market, limit: int):
    """The same article set the news panel shows, so the analysis matches it."""
    profile = market.get_profile(symbol)
    query = profile.long_name if profile and profile.long_name else symbol
    exchange = profile.exchange if profile else None
    return news.get_news(query, exchange=exchange, symbol=symbol, limit=limit).articles, query


@router.get(
    "/analysis/{symbol}",
    response_model=AnalysisResult,
    summary="Structured sentiment analysis of recent news",
)
def get_analysis(
    symbol: Symbol,
    analyst: AnalystDep,
    news: NewsServiceDep,
    market: MarketServiceDep,
    limit: int = Query(5, ge=1, le=10),
    refresh: bool = Query(False, description="Bypass the cached analysis"),
) -> AnalysisResult:
    """Sentiment, themes and risks as validated structured data.

    Cached for six hours keyed on the article set, so a page refresh costs
    nothing and a genuinely new story invalidates it.
    """
    articles, query = _articles_for(symbol, news, market, limit)
    return analyst.analyze(query, articles, refresh=refresh)


@router.get(
    "/analysis/{symbol}/stream",
    summary="Stream a prose summary of recent news (SSE)",
    response_class=StreamingResponse,
)
def stream_analysis(
    symbol: Symbol,
    analyst: AnalystDep,
    news: NewsServiceDep,
    market: MarketServiceDep,
    limit: int = Query(5, ge=1, le=10),
) -> StreamingResponse:
    """Server-sent events carrying the summary as it is generated.

    The structured endpoint above blocks until the model finishes; this one
    puts text on screen in about a second. The UI uses both.
    """
    articles, query = _articles_for(symbol, news, market, limit)

    def events() -> Iterator[str]:
        try:
            for chunk in analyst.stream_summary(query, articles):
                # SSE data lines cannot contain raw newlines.
                for line in chunk.split("\n"):
                    yield f"data: {line}\n"
                yield "\n"
        except AIError as exc:
            # The response has already started, so the status code is fixed
            # at 200. Errors have to travel in-band as a typed event.
            yield f"event: error\ndata: {type(exc).__name__}: {exc}\n\n"
        else:
            yield "event: done\ndata: \n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Stops nginx buffering the stream into one lump, which would
            # defeat the entire point.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/insights", summary="Answer a free-text market question")
def insights(body: InsightsRequest, analyst: AnalystDep) -> StreamingResponse:
    """The general market question box, streamed.

    POST rather than GET: the prompt is a body, not an identifier, and it
    should not end up in access logs or browser history.
    """
    def events() -> Iterator[str]:
        try:
            for chunk in analyst.stream_insights(body.question):
                for line in chunk.split("\n"):
                    yield f"data: {line}\n"
                yield "\n"
        except AIError as exc:
            yield f"event: error\ndata: {type(exc).__name__}: {exc}\n\n"
        else:
            yield "event: done\ndata: \n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
