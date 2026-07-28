import os
from pycoingecko import CoinGeckoAPI
# REMOVED: from alpha_vantage.timeseries import TimeSeries # THIS LINE WAS THE PROBLEM
import google.generativeai as genai
import pandas as pd
import time
from datetime import datetime, timedelta

# ADDED (or ensure it's there):
import yfinance as yf  # Import yfinance for stock data functions

# --- API Key Configuration ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Configure Gemini API
gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    except Exception as e:
        print(f"Error configuring Gemini API: {e}. Check GEMINI_API_KEY.")
        gemini_model = None
else:
    print("WARNING: GEMINI_API_KEY not found. Gemini insights will be unavailable.")

# --- CoinGecko API ---
cg = CoinGeckoAPI()


def get_crypto_prices(crypto_ids, vs_currencies='usd'):
    """
    Fetches current prices for cryptocurrencies from CoinGecko.
    crypto_ids: Comma-separated string or list of crypto IDs (e.g., 'bitcoin,ethereum').
    vs_currencies: Comma-separated string or list of currencies (e.g., 'usd,eur').
    """
    try:
        if isinstance(crypto_ids, list):
            crypto_ids = ",".join(crypto_ids)
        if isinstance(vs_currencies, list):
            vs_currencies = ",".join(vs_currencies)

        prices = cg.get_price(ids=crypto_ids, vs_currencies=vs_currencies,
                              include_market_cap='true', include_24hr_vol='true',
                              include_24hr_change='true', include_last_updated_at='true')
        return prices
    except Exception as e:
        print(f"Error fetching crypto prices from CoinGecko: {e}")
        return {}


def get_crypto_historical_data(crypto_id, vs_currency='usd', days='30'):
    """
    Fetches historical data for a cryptocurrency from CoinGecko.
    crypto_id: ID of the cryptocurrency (e.g., 'bitcoin').
    vs_currency: Currency to compare against (e.g., 'usd').
    days: Number of days of historical data ('1', '7', '30', '365', 'max').
    """
    try:
        data = cg.get_coin_market_chart_by_id(id=crypto_id, vs_currency=vs_currency, days=days)
        prices = data.get('prices', [])
        df = pd.DataFrame(prices, columns=['timestamp', 'price'])
        df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('date', inplace=True)
        df.drop('timestamp', axis=1, inplace=True)
        return df
    except Exception as e:
        print(f"Error fetching crypto historical data from CoinGecko for {crypto_id}: {e}")
        return pd.DataFrame()


# --- yfinance for Stock Data ---
# This function relies on 'import yfinance as yf' at the top
def get_stock_data(symbol, period='1y', interval='1d'):
    """
    Fetches historical stock data from yfinance.
    symbol: Stock ticker symbol (e.g., 'AAPL').
    period: Data period (e.g., '1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max').
    interval: Data interval (e.g., '1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h', '1d', '5d', '1wk', '1mo', '3mo').
              Note: Not all periods support all intervals (e.g., 1m interval only for 7 days max).
    """
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period=period, interval=interval)
        if data.empty:
            print(f"No data found for {symbol} with period={period}, interval={interval}.")
            return pd.DataFrame()

        data.columns = [col.lower() for col in data.columns]
        if 'adj close' in data.columns:
            data['close'] = data['adj close']
        data = data[['open', 'high', 'low', 'close', 'volume']]

        data.index = pd.to_datetime(data.index)
        data.sort_index(inplace=True)
        return data
    except Exception as e:
        print(f"Error fetching stock data for {symbol} from yfinance: {e}")
        return pd.DataFrame()


# --- Gemini API for Insights and News Analysis ---
def get_gemini_insights(prompt_text):
    """
    Gets AI insights from the Gemini model based on a prompt.
    prompt_text: The text prompt for Gemini to generate insights.
    """
    if not gemini_model:
        return "Gemini API is not configured. Please ensure GEMINI_API_KEY is set correctly and the model initialized."
    try:
        response = gemini_model.generate_content(prompt_text)
        return response.text
    except Exception as e:
        return f"Error getting Gemini insights: {e}"


def analyze_news_with_gemini(news_articles, asset_name):
    """
    Uses Gemini to analyze a list of news articles for an asset.
    news_articles: A list of dictionaries, each containing 'title' and 'description'.
    asset_name: The name of the stock or crypto for context (e.g., 'Apple', 'Bitcoin').
    """
    if not gemini_model:
        return "Gemini API is not configured. News analysis will be unavailable."

    if not news_articles:
        return "No news articles provided for analysis."

    combined_news_text = f"Here are some recent news articles related to {asset_name}. Provide a concise summary of the key themes and overall sentiment (positive, negative, neutral, mixed) expressed in these articles regarding {asset_name}. Focus on how these news might impact the market for {asset_name}. Keep the summary to a maximum of 200 words.\n\n"
    for i, article in enumerate(news_articles[:5]):
        title = article.get('title', 'N/A')
        description = article.get('description', 'No description available.')
        combined_news_text += f"Article {i + 1}:\nTitle: {title}\nDescription: {description}\n---\n"

    return get_gemini_insights(combined_news_text)


if __name__ == "__main__":
    print("--- Testing API Utils ---")

    print("\n--- Crypto Prices (CoinGecko) ---")
    crypto_data = get_crypto_prices(crypto_ids=['bitcoin', 'ethereum'])
    print(crypto_data)

    print("\n--- Bitcoin Historical Data (CoinGecko) ---")
    btc_history = get_crypto_historical_data('bitcoin', days='7')
    print(btc_history.head())

    print("\n--- Stock Data (yfinance) ---")
    stock_data = get_stock_data('GOOGL', period='1mo')
    print(stock_data.head())

    stock_data_max = get_stock_data('IBM', period='max')
    print(stock_data_max.tail())

    if GEMINI_API_KEY and gemini_model:
        print("\n--- Gemini Insights ---")
        insights_prompt = "What are the current trends in the cryptocurrency market? (Summarize in 50 words)"
        insights = get_gemini_insights(insights_prompt)
        print(insights)

        print("\n--- Gemini News Analysis ---")
        sample_news = [
            {"title": "Bitcoin Surges on Institutional Adoption News",
             "description": "Major financial institutions are increasingly investing in Bitcoin, driving up its price."},
            {"title": "Regulatory Scrutiny Looms Over Crypto Market",
             "description": "Governments worldwide are considering stricter regulations for digital assets, causing some uncertainty."}
        ]
        news_analysis = analyze_news_with_gemini(sample_news, "Bitcoin")
        print(news_analysis)
    else:
        print("Skipping Gemini tests: GEMINI_API_KEY not set or Gemini model not initialized.")