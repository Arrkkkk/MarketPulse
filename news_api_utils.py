import os
import requests
from datetime import datetime, timedelta

# Access API keys from system environment variables
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
MARKETAUX_API_KEY = os.environ.get("MARKETAUX_API_KEY")


def _fetch_news_from_newsapi(query, language='en', page_size=5):
    """
    Fetches news articles related to a query using NewsAPI.org.
    """
    if not NEWS_API_KEY:
        print("ERROR: NEWS_API_KEY not found. Please set it as a system environment variable.")
        return [], "NewsAPI.org (API Key Missing)"

    from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "language": language,
        "sortBy": "relevancy",
        "from": from_date,
        "pageSize": page_size,
        "apiKey": NEWS_API_KEY
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        articles = data.get('articles', [])
        print(f"NewsAPI.org fetched {len(articles)} articles for '{query}'")
        return articles, "NewsAPI.org"
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error from NewsAPI.org for '{query}': {http_err} - Response: {response.text}")
        return [], f"NewsAPI.org (HTTP Error: {response.status_code})"
    except Exception as e:
        print(f"Error fetching news from NewsAPI.org for '{query}': {e}")
        return [], "NewsAPI.org (General Error)"


def _fetch_news_from_marketaux(query_text, ticker_symbol=None, page_size=5):
    """
    Fetches news articles from MarketAux API, prioritizing ticker_symbol if available.
    query_text: General search query (fallback or for broader context).
    ticker_symbol: The specific stock ticker symbol (e.g., 'AAPL', 'RELIANCE.NS').
    page_size: Number of articles to return (max usually 5 for free tier).
    """
    if not MARKETAUX_API_KEY:
        print("ERROR: MARKETAUX_API_KEY not found. Please set it as a system environment variable.")
        return [], "MarketAux API (API Key Missing)"

    url = "https://api.marketaux.com/v1/news/all"
    params = {
        "api_token": MARKETAUX_API_KEY,
        "limit": page_size,
        "sort": "published_at",
        "direction": "desc",
    }

    # PRIORITY: Use 'symbols' parameter if ticker_symbol is provided
    if ticker_symbol:
        params["symbols"] = ticker_symbol.upper()  # MarketAux uses 'symbols' parameter for tickers
        print(f"MarketAux: Searching by precise symbol '{ticker_symbol}'")
    else:
        # Fallback to general 'search' if no symbol provided (e.g., for crypto or if symbol info is missing)
        params["search"] = query_text
        print(f"MarketAux: Searching by general query '{query_text}' (no symbol provided)")

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        articles = data.get('data', [])

        formatted_articles = []
        for article in articles:
            source_name = article.get('source', 'MarketAux')
            if isinstance(source_name, dict):
                source_name = source_name.get('name', 'MarketAux')

            formatted_articles.append({
                'title': article.get('title'),
                'description': article.get('description'),
                'url': article.get('url'),
                'publishedAt': article.get('published_at'),
                'source': {'name': source_name}
            })
        print(
            f"MarketAux fetched {len(formatted_articles)} articles for '{query_text}' (symbol: {ticker_symbol or 'N/A'})")
        return formatted_articles, "MarketAux API"
    except requests.exceptions.HTTPError as http_err:
        print(
            f"HTTP error from MarketAux API for '{query_text}' (symbol: {ticker_symbol or 'N/A'}): {http_err} - Response: {response.text}")
        return [], f"MarketAux API (HTTP Error: {response.status_code})"
    except Exception as e:
        print(f"Error fetching news from MarketAux API for '{query_text}' (symbol: {ticker_symbol or 'N/A'}): {e}")
        return [], "MarketAux API (General Error)"


def get_top_news(query, exchange_short_name=None, ticker_symbol=None, page_size=5):
    """
    Unified function to fetch news, prioritizing based on exchange and using ticker for MarketAux.
    Prioritizes MarketAux for non-US exchanges, NewsAPI.org for US exchanges, with fallbacks.
    query: The general search query (e.g., 'Apple Inc.', 'Reliance Industries').
    exchange_short_name: Short name of the stock exchange (e.g., 'NASDAQ', 'NSE', 'LSE').
                         Obtained from yfinance ticker info. Can be None for crypto/general.
    ticker_symbol: The specific stock ticker symbol (e.g., 'AAPL', 'RELIANCE.NS').
    page_size: Number of articles to return.
    """
    articles = []
    news_source = "Unknown"

    us_exchanges = ['NASDAQ', 'NYSE', 'NYSE ARCA', 'NYSEAMERICAN', 'OTC']

    if exchange_short_name and exchange_short_name.upper() in us_exchanges:
        # Prioritize NewsAPI.org for identified US stocks
        print(f"Prioritizing NewsAPI.org for '{query}' (Exchange: {exchange_short_name})...")
        articles, news_source = _fetch_news_from_newsapi(query, page_size=page_size)

        if not articles and MARKETAUX_API_KEY:  # Fallback to MarketAux if NewsAPI fails and MarketAux key exists
            print(f"NewsAPI.org found no news for '{query}'. Falling back to MarketAux API.")
            # For fallback, pass the ticker symbol to MarketAux
            articles, news_source = _fetch_news_from_marketaux(query, ticker_symbol=ticker_symbol, page_size=page_size)
            if news_source == "MarketAux API": news_source += " (Fallback)"
    else:  # Non-US exchange, or no exchange info (e.g., crypto, general query)
        # Prioritize MarketAux for non-US stocks or crypto
        print(f"Prioritizing MarketAux API for '{query}' (Exchange: {exchange_short_name or 'N/A'})...")
        # Pass the ticker_symbol to MarketAux for non-US stocks
        articles, news_source = _fetch_news_from_marketaux(query, ticker_symbol=ticker_symbol, page_size=page_size)

        if not articles and NEWS_API_KEY:  # Fallback to NewsAPI.org if MarketAux fails and NewsAPI key exists
            print(f"MarketAux API found no news for '{query}'. Falling back to NewsAPI.org.")
            articles, news_source = _fetch_news_from_newsapi(query, page_size=page_size)
            if news_source == "NewsAPI.org": news_source += " (Fallback)"

    if not articles:
        if not NEWS_API_KEY and not MARKETAUX_API_KEY:
            news_source = "No News APIs Configured"
        elif not NEWS_API_KEY:
            news_source = f"{news_source} (NewsAPI.org Key Missing)"
        elif not MARKETAUX_API_KEY:
            news_source = f"{news_source} (MarketAux API Key Missing)"
        else:
            news_source += " (No articles found from either API)"

    return articles, news_source


if __name__ == "__main__":
    print("--- Testing News API Utils ---")

    # Test a US stock (Apple, NASDAQ) - should prioritize NewsAPI, then MarketAux by symbol
    print("\n--- Apple Stock News (NASDAQ) ---")
    apple_news, source = get_top_news(query="Apple Inc.", exchange_short_name="NASDAQ", ticker_symbol="AAPL",
                                      page_size=3)
    print(f"Source used: {source}")
    if apple_news:
        for article in apple_news:
            print(f"Title: {article.get('title')}")
            print(f"Source: {article.get('source', {}).get('name')}")
            print(f"URL: {article.get('url')}")
            print("-" * 20)
    else:
        print("No news found for Apple Inc.")

    # Test an Indian stock (Reliance, NSE) - should prioritize MarketAux by symbol, then NewsAPI by query
    print("\n--- Reliance Industries News (NSE) ---")
    reliance_news, source = get_top_news(query="Reliance Industries Limited", exchange_short_name="NSE",
                                         ticker_symbol="RELIANCE.NS", page_size=3)
    print(f"Source used: {source}")
    if reliance_news:
        for article in reliance_news:
            print(f"Title: {article.get('title')}")
            print(f"Source: {article.get('source', {}).get('name')}")
            print(f"URL: {article.get('url')}")
            print("-" * 20)
    else:
        print("No news found for Reliance Industries Limited.")

    # Test a crypto (no exchange info, no ticker symbol) - should prioritize MarketAux by query, then NewsAPI by query
    print("\n--- Bitcoin Crypto News (No Exchange Info) ---")
    bitcoin_news, source = get_top_news(query="Bitcoin cryptocurrency", exchange_short_name=None, ticker_symbol=None,
                                        page_size=2)
    print(f"Source used: {source}")
    if bitcoin_news:
        for article in bitcoin_news:
            print(f"Title: {article.get('title')}")
            print(f"Source: {article.get('source', {}).get('name')}")
            print(f"URL: {article.get('url')}")
            print("-" * 20)
    else:
        print("No news found for Bitcoin cryptocurrency.")