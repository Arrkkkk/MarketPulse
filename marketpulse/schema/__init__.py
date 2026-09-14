"""Domain models shared by providers, services, the API and its client.

One definition of what a quote or an article *is*, so the contract between
layers is a artifact you can read rather than a dict shape you infer.
"""

from marketpulse.schema.market import (
    CompanyProfile,
    CryptoQuote,
    PriceHistory,
    Quote,
)
from marketpulse.schema.news import NewsArticle, NewsResult

__all__ = [
    "CompanyProfile",
    "CryptoQuote",
    "NewsArticle",
    "NewsResult",
    "PriceHistory",
    "Quote",
]
