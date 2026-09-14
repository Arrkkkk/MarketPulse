"""v1 router aggregation.

Health and info sit outside the `/v1` prefix: orchestrators probe `/health`
by convention, and a client fetching `/info` should not have to know the API
version before it can ask what versions exist.
"""

from __future__ import annotations

from fastapi import APIRouter

from marketpulse.api.v1 import health, market, news

api_router = APIRouter()
api_router.include_router(market.router, prefix="/v1")
api_router.include_router(news.router, prefix="/v1")

meta_router = APIRouter()
meta_router.include_router(health.router)
