import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from api_utils import get_crypto_prices, get_crypto_historical_data, get_stock_data, get_gemini_insights, \
    analyze_news_with_gemini
from news_api_utils import get_top_news
import time
from datetime import datetime, timedelta
import yfinance as yf
import re  # Import the regex module

# --- Streamlit Page Configuration ---
st.set_page_config(layout="wide", page_title="Market Tracker Dashboard", initial_sidebar_state="expanded")

st.title("📈 Real-time Stock & Crypto Market Tracker")

# --- Sidebar for Navigation and Inputs ---
st.sidebar.header("Navigation")

view_mode = st.sidebar.radio("Select Market Type", ("Cryptocurrencies", "Stocks"))

st.sidebar.header("Settings")
update_interval = st.sidebar.slider("Data Update Interval (seconds)", 30, 300, 60, 30)

# --- Session State for Caching Data and UI States ---
if 'stock_data_cache' not in st.session_state:
    st.session_state.stock_data_cache = {}
if 'crypto_prices_cache' not in st.session_state:
    st.session_state.crypto_prices_cache = {}
if 'crypto_history_cache' not in st.session_state:
    st.session_state.crypto_history_cache = {}
if 'news_cache' not in st.session_state:
    st.session_state.news_cache = {}
if 'last_update_time' not in st.session_state:
    st.session_state.last_update_time = None
if 'current_view_mode' not in st.session_state:
    st.session_state.current_view_mode = "Cryptocurrencies"
if 'searched_stock_ticker' not in st.session_state:
    st.session_state.searched_stock_ticker = ""
if 'ticker_info_cache' not in st.session_state:
    st.session_state.ticker_info_cache = {}

if 'show_more_snapshots' not in st.session_state:
    st.session_state.show_more_snapshots = 0

# Reset relevant states when switching market type
if view_mode != st.session_state.current_view_mode:
    st.session_state.selected_asset_detail = None
    st.session_state.searched_stock_ticker = ""
    st.session_state.show_more_snapshots = 0
    st.session_state.current_view_mode = view_mode


# Helper function to get ticker info (like currency, exchange) from yfinance
@st.cache_data(ttl=86400)  # Cache ticker info for 24 hours
def get_yfinance_ticker_info(symbol):
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return info
    except Exception as e:
        print(f"Error fetching ticker info for {symbol}: {e}")
        return {}


@st.cache_data(ttl=update_interval)
def fetch_all_data_cached_and_handled():
    """
    Fetches all necessary data from CoinGecko and yfinance for initial overview.
    Returns (crypto_prices, stock_data_dict, current_fetch_time).
    """
    current_time = datetime.now()

    common_crypto_ids = [
        'bitcoin', 'ethereum', 'ripple', 'cardano', 'solana', 'dogecoin',
        'litecoin', 'polkadot', 'binancecoin', 'tron', 'shiba-inu'  # Added more for "show more"
    ]
    crypto_prices = get_crypto_prices(crypto_ids=common_crypto_ids)
    if not crypto_prices:
        st.warning("Could not fetch cryptocurrency prices. Please check API status or connection.")

    # Expanded list for "show more" snapshots
    common_stock_symbols_for_overview = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'SPY', 'NFLX', 'META',
        'RELIANCE.NS', '0005.HK', 'IBM', 'JPM', 'XOM', 'GS', 'BAC', 'KO', 'PEP',
        'DIS', 'NKE', 'V', 'PG', 'COST', 'TCS.NS', 'HDFCBANK.NS', 'BARC.L', 'SHEL.L', 'DAI.DE', 'TM.TO'
        # Added more global examples
    ]
    stock_data_temp = {}

    st.info("Fetching overview stock data (using yfinance)...")
    for symbol in common_stock_symbols_for_overview:
        stock_df = get_stock_data(symbol, period='1y', interval='1d')
        if not stock_df.empty:
            stock_data_temp[symbol] = stock_df
            info = get_yfinance_ticker_info(symbol)
            st.session_state.ticker_info_cache[symbol] = info
        else:
            st.warning(f"No data found for {symbol} from yfinance overview. Check symbol validity or try again.")
        time.sleep(0.5)

    return crypto_prices, stock_data_temp, current_time


st.session_state.crypto_prices_cache, st.session_state.stock_data_cache, st.session_state.last_update_time = fetch_all_data_cached_and_handled()

st.success(f"Dashboard data last updated at {st.session_state.last_update_time.strftime('%Y-%m-%d %H:%M:%S')}")
st.info(
    f"Next automatic data update in approx. {int(update_interval - (datetime.now() - st.session_state.last_update_time).total_seconds())} seconds.")

# --- Market Snapshots Section ---
st.markdown("---")
st.subheader("Market Snapshots")

initial_snapshots_count = 4
additional_snapshots_per_click = 4
total_snapshots_to_show = initial_snapshots_count + (
            st.session_state.show_more_snapshots * additional_snapshots_per_click)

# Display Crypto Snapshots
st.markdown("#### Cryptocurrencies")
crypto_snapshot_symbols = list(st.session_state.crypto_prices_cache.keys())
crypto_symbols_to_display = crypto_snapshot_symbols[:min(total_snapshots_to_show, len(crypto_snapshot_symbols))]

cols_crypto = st.columns(min(len(crypto_symbols_to_display), 4))
for i, crypto_id in enumerate(crypto_symbols_to_display):
    with cols_crypto[i % 4]:
        data = st.session_state.crypto_prices_cache.get(crypto_id, {})
        price = data.get('usd', 'N/A')
        change_24h = data.get('usd_24h_change', 'N/A')
        st.metric(label=f"{crypto_id.replace('-', ' ').title()} (USD)",
                  value=f"${price:,.2f}" if isinstance(price, (int, float)) else price,
                  delta=f"{change_24h:+.2f}%" if isinstance(change_24h, (int, float)) else "N/A")

# --- Thin line separator ---
st.markdown("<hr style='border: 1px solid #333; margin: 1em 0;'>", unsafe_allow_html=True)

# Display Stock Snapshots
st.markdown("#### Stocks")
stock_snapshot_symbols = list(st.session_state.stock_data_cache.keys())
stock_symbols_to_display = stock_snapshot_symbols[:min(total_snapshots_to_show, len(stock_snapshot_symbols))]

cols_stock = st.columns(min(len(stock_symbols_to_display), 4))
for i, symbol in enumerate(stock_symbols_to_display):
    with cols_stock[i % 4]:
        data = st.session_state.stock_data_cache.get(symbol, pd.DataFrame())
        close_price = data.get('close', pd.Series(0)).iloc[-1] if not data.empty else 'N/A'
        prev_close = data.get('close', pd.Series(0)).iloc[-2] if len(data) >= 2 else None
        delta = (close_price - prev_close) if isinstance(close_price, (int, float)) and isinstance(prev_close, (
        int, float)) else None

        info = st.session_state.ticker_info_cache.get(symbol, {})
        currency = info.get('currency', 'USD')
        currency_display = currency if currency and isinstance(currency, str) and currency.strip() else 'N/A'
        if currency_display == 'GBp': currency_display = 'GBP'

        st.metric(label=f"{symbol} ({currency_display})",
                  value=f"{close_price:,.2f} {currency_display}" if isinstance(close_price,
                                                                               (int, float)) else close_price,
                  delta=f"{delta:+.2f}" if isinstance(delta, (int, float)) else "N/A")

# "Show More/Less" Buttons for Snapshots
col_snap_buttons1, col_snap_buttons2 = st.columns([1, 1])
with col_snap_buttons1:
    if total_snapshots_to_show < max(len(st.session_state.crypto_prices_cache), len(st.session_state.stock_data_cache)):
        if st.button("Show More Snapshots ▶️", key="show_more_button"):
            st.session_state.show_more_snapshots += 1
            st.rerun()
    else:
        st.info("All available overview snapshots are displayed.")

with col_snap_buttons2:
    if st.session_state.show_more_snapshots > 0:
        if st.button("Show Less Snapshots ◀️", key="show_less_button"):
            st.session_state.show_more_snapshots -= 1
            st.experimental_rerun()

st.markdown("---")

# --- Display Logic based on view_mode ---
if view_mode == "Cryptocurrencies":
    st.header("Cryptocurrency Market Data")

    if st.session_state.crypto_prices_cache:
        st.subheader("Current Crypto Prices Overview")
        crypto_data_list = []
        for crypto_id, data in st.session_state.crypto_prices_cache.items():
            price = data.get('usd', 'N/A')
            change_24h = data.get('usd_24h_change', 'N/A')
            market_cap = data.get('usd_market_cap', 'N/A')
            vol_24h = data.get('usd_24h_vol', 'N/A')
            last_updated_timestamp = data.get('last_updated_at', 0)
            last_updated = datetime.fromtimestamp(last_updated_timestamp).strftime(
                '%Y-%m-%d %H:%M:%S') if last_updated_timestamp else 'N/A'

            crypto_data_list.append({
                "Crypto": crypto_id.replace('-', ' ').title(),
                "Price (USD)": price,
                "24h Change (%)": f"{change_24h:+.2f}" if isinstance(change_24h, (int, float)) else change_24h,
                "Market Cap (USD)": f"{market_cap:,.0f}" if isinstance(market_cap, (int, float)) else market_cap,
                "24h Volume (USD)": f"{vol_24h:,.0f}" if isinstance(vol_24h, (int, float)) else vol_24h,
                "Last Updated (UTC)": last_updated
            })
        crypto_df = pd.DataFrame(crypto_data_list)
        st.dataframe(crypto_df, use_container_width=True, hide_index=True)
    else:
        st.info("No cryptocurrency data available to display in overview.")

    st.markdown("---")
    st.subheader("Detailed Cryptocurrency Analysis")
    all_crypto_ids_sorted = sorted([c.replace('-', ' ').title() for c in st.session_state.crypto_prices_cache.keys()])
    selected_crypto_name = st.selectbox("Select Cryptocurrency for Detailed View", all_crypto_ids_sorted,
                                        key="select_crypto_detail")

    if selected_crypto_name:
        selected_crypto_id = selected_crypto_name.lower().replace(' ', '-')
        days_to_display = st.select_slider("Select historical period", options=['1', '7', '30', '90', '365', 'max'],
                                           value='30', key="crypto_days_slider")


        @st.cache_data(ttl=3600)
        def get_cached_crypto_history(crypto_id, days):
            return get_crypto_historical_data(crypto_id, days=days)


        history_df = get_cached_crypto_history(selected_crypto_id, days_to_display)

        if not history_df.empty:
            st.markdown(f"### Price Chart for {selected_crypto_name}")
            fig_line = go.Figure(
                data=[go.Scatter(x=history_df.index, y=history_df['price'], mode='lines', name='Price')])
            fig_line.update_layout(title=f"{selected_crypto_name} Price Over Time (USD)", xaxis_title="Date",
                                   yaxis_title="Price (USD)")
            st.plotly_chart(fig_line, use_container_width=True)

            st.markdown(f"### Recent {selected_crypto_name} News & AI Analysis")


            @st.cache_data(ttl=3600)
            def get_cached_news_crypto(query):
                return get_top_news(query, exchange_short_name=None, ticker_symbol=None, page_size=5)


            news_articles, news_source = get_cached_news_crypto(f"{selected_crypto_name} cryptocurrency")
            st.info(
                f"News Source: {news_source}. Note: News relevance may vary due to API coverage and ambiguity of search terms.")

            if news_articles:
                for i, article in enumerate(news_articles):
                    st.markdown(f"**[{article.get('title', 'No Title')}]({article.get('url')})**")
                    st.write(
                        f"Source: {article.get('source', {}).get('name', 'N/A')} | Published: {pd.to_datetime(article.get('publishedAt')).strftime('%Y-%m-%d %H:%M') if article.get('publishedAt') else 'N/A'}")
                    st.write(article.get('description', 'No description available.'))
                    st.markdown("---")

                with st.expander(f"Get AI Analysis for {selected_crypto_name} News"):
                    if st.button(f"Analyze News with Gemini for {selected_crypto_name}",
                                 key=f"gemini_crypto_news_{selected_crypto_id}"):
                        with st.spinner("Asking Gemini to analyze news..."):
                            gemini_analysis = analyze_news_with_gemini(news_articles, selected_crypto_name)
                            st.write(gemini_analysis)
            else:
                st.info(
                    f"No recent news found for {selected_crypto_name}. News coverage may be limited from available APIs.")
        else:
            st.info(f"No historical data for {selected_crypto_name} available or fetching failed.")
    else:
        st.info("Please select a cryptocurrency from the dropdown to see detailed analysis.")


elif view_mode == "Stocks":
    st.header("Stock Market Data")

    if st.session_state.stock_data_cache:
        st.subheader("Current Stock Prices Overview (Last Close)")
        stock_summary_data = []
        all_display_symbols = sorted(list(st.session_state.stock_data_cache.keys()))
        for symbol in all_display_symbols:
            data = st.session_state.stock_data_cache.get(symbol)
            if data is not None and not data.empty:
                last_row = data.iloc[-1]
                prev_close = data.iloc[-2]['close'] if len(data) >= 2 else last_row['open']
                change_percent = ((last_row['close'] - prev_close) / prev_close) * 100 if prev_close != 0 else 0

                stock_summary_data.append({
                    "Symbol": symbol,
                    "Open": f"{last_row['open']:.2f}",
                    "High": f"{last_row['high']:.2f}",
                    "Low": f"{last_row['low']:.2f}",
                    "Close": f"{last_row['close']:.2f}",
                    "Change (%)": f"{change_percent:+.2f}",
                    "Volume": f"{int(last_row['volume']):,}",
                    "Last Updated": data.index[-1].strftime('%Y-%m-%d')
                })
        stock_df = pd.DataFrame(stock_summary_data)
        st.dataframe(stock_df, use_container_width=True, hide_index=True)
    else:
        st.info("No stock data available to display in overview. Initial fetch may still be in progress.")

    st.markdown("---")
    st.subheader("Search Any Stock by Ticker Symbol")

    search_ticker_input_help = """
    Enter Stock Ticker (e.g., AAPL for Apple US, RELIANCE.NS for Reliance India, 0005.HK for HSBC Hong Kong).

    **Global Exchange Suffixes:** (Use these suffixes after the base ticker for non-US exchanges)
    """
    st.markdown(search_ticker_input_help)

    # Global Suffixes Table
    global_suffixes = {
        "Argentina (BYMA)": ".BA", "Australia (ASX)": ".AX", "Australia (CBOE)": ".CX", "Austria (VSE)": ".VI",
        "Belgium (Euronext)": ".BR", "Brazil (BOVESPA)": ".SA",
        "Canada (CSE)": ".CN", "Canada (NEO)": ".NE", "Canada (TSX)": ".TO", "Canada (TSXV)": ".V",
        "Chile (SSE)": ".SN", "China (Shanghai)": ".SS", "China (Shenzhen)": ".SZ", "Czech Rep. (PSE)": ".PR",
        "Denmark (OMX)": ".CO", "Egypt (EGX)": ".CA", "Estonia (OMX)": ".TL", "Finland (OMX)": ".HE",
        "France (Euronext)": ".PA",
        "Germany (Berlin)": ".BE", "Germany (Bremen)": ".BM", "Germany (Dusseldorf)": ".DU",
        # Berlin, Bremen, Dusseldorf Bourse
        "Germany (Frankfurt)": ".F", "Germany (Hamburg)": ".HM", "Germany (Hanover)": ".HA",
        # Frankfurt, Hamburg, Hanover Bourse
        "Germany (Munich)": ".MU", "Germany (Stuttgart)": ".SG", "Germany (XETRA)": ".DE",  # Munich, Stuttgart Bourse
        "Greece (ATHEX)": ".AT", "Hong Kong (HKEX)": ".HK", "Hungary (BSE)": ".BD", "Iceland (OMX)": ".IC",
        "India (BSE)": ".BO",
        "India (NSE)": ".NS", "Indonesia (IDX)": ".JK", "Ireland (Euronext)": ".IR", "Israel (TASE)": ".TA",
        "Italy (EuroTLX)": ".TI", "Italy (Borsa Italiana)": ".MI", "Japan (TSE)": ".T", "Latvia (OMX)": ".RG",
        "Lithuania (OMX)": ".VS", "Malaysia (MYX)": ".KL", "Mexico (BMV)": ".MX", "Netherlands (Euronext)": ".AS",
        "New Zealand (NZX)": ".NZ", "Norway (OSE)": ".OL", "Portugal (Euronext)": ".LS", "Qatar (QSE)": ".QA",
        "Russia (MOEX)": ".ME", "Saudi Arabia (Tadawul)": ".SAU", "Singapore (SGX)": ".SI", "South Africa (JSE)": ".JO",
        "South Korea (KSE)": ".KS", "South Korea (KOSDAQ)": ".KQ", "Spain (BME)": ".MC", "Sweden (OMX)": ".ST",
        "Switzerland (SIX)": ".SW", "Taiwan (OTC)": ".TWO", "Taiwan (TWSE)": ".TW", "Thailand (SET)": ".BK",
        "Turkey (BIST)": ".IS", "UK (LSE)": ".L", "UK (LSE Secondary)": ".IL",
        "USA (NASDAQ/NYSE)": "(No suffix for primary listings)", "Venezuela (BVC)": ".CR"
    }

    suffix_df = pd.DataFrame(list(global_suffixes.items()), columns=['Country/Market', 'Suffix'])
    st.dataframe(suffix_df, hide_index=True, use_container_width=True)

    search_ticker = st.text_input("Enter Stock Ticker:", value=st.session_state.searched_stock_ticker,
                                  help="e.g. AAPL, GOOGL, RELIANCE.NS, 0005.HK").strip().upper()

    if search_ticker != st.session_state.searched_stock_ticker:
        st.session_state.searched_stock_ticker = search_ticker

    display_ticker = search_ticker if search_ticker else None

    if not display_ticker:
        potential_stock_symbols_for_dropdown = sorted(list(st.session_state.stock_data_cache.keys()))
        selected_stock_symbol_from_dropdown = st.selectbox("Or Select from Most Viewed Stocks",
                                                           potential_stock_symbols_for_dropdown,
                                                           key="select_stock_detail")
        display_ticker = selected_stock_symbol_from_dropdown
    else:
        st.write(f"Displaying data for searched ticker: **{display_ticker}**")

    if display_ticker:
        stock_data = st.session_state.stock_data_cache.get(display_ticker)
        ticker_info = st.session_state.ticker_info_cache.get(display_ticker)

        if stock_data is None or stock_data.empty or ticker_info is None or st.button(
                f"Refresh Data for {display_ticker}", key="refresh_searched_stock"):
            with st.spinner(f"Fetching detailed data for {display_ticker} from yfinance..."):
                stock_data = get_stock_data(display_ticker, period='max', interval='1d')
                if not stock_data.empty:
                    st.session_state.stock_data_cache[display_ticker] = stock_data
                    info = get_yfinance_ticker_info(display_ticker)
                    st.session_state.ticker_info_cache[display_ticker] = info
                    ticker_info = info
                else:
                    st.error(
                        f"Could not fetch detailed data for {display_ticker}. Please check the ticker symbol and its exchange suffix (e.g., RELIANCE.NS for India, 0005.HK for Hong Kong) or try again later.")
                    stock_data = pd.DataFrame()
                    ticker_info = {}

        # --- NEW UI ELEMENT: Company Name and Summary ---
        if ticker_info and ticker_info.get('longName'):
            # Combine longName and info icon for popover
            col_name, col_popover = st.columns([0.8, 0.2])
            with col_name:
                st.markdown(f"### {ticker_info['longName']} ({display_ticker})")
            with col_popover:
                # Popover for detailed company description
                # Note: Popovers are for Streamlit >= 1.28.0
                with st.popover("ℹ️ Company Info"):
                    st.write(f"**Sector:** {ticker_info.get('sector', 'N/A')}")
                    st.write(f"**Industry:** {ticker_info.get('industry', 'N/A')}")

                    full_summary_text = ticker_info.get('longBusinessSummary') or ticker_info.get('description')
                    if full_summary_text:
                        key_segments = [
                            "Oil to Chemicals", "Oil and Gas", "Retail", "Digital Services",
                            "Material and Composites", "Renewables", "Financial Services",
                            "hydrocarbon exploration and production", "petroleum products",
                            "petrochemicals", "textile", "retail", "digital", "material and composites",
                            "renewables", "financial services businesses",
                            "yarns, fabrics, apparel, and auto furnishings",
                            "crude oil and natural gas",
                            "digital television, gaming, broadband, and telecommunication services",
                            "non-banking financial and insurance broking services", "news and entertainment platforms",
                            "highway hospitality and fleet management services"
                        ]

                        formatted_summary = full_summary_text
                        for segment in key_segments:
                            formatted_summary = re.sub(r'\b(' + re.escape(segment) + r')\b', r'**\1**',
                                                       formatted_summary, flags=re.IGNORECASE)

                        paragraphs = re.split(r'\.\s*(?=[A-Z])|\n\s*\n', formatted_summary)
                        st.markdown("##### Business Summary:")
                        for p in paragraphs:
                            if p.strip():
                                st.write(p.strip())
                    else:
                        st.info("No detailed company description available from yfinance.")
            st.markdown("---")  # Separator after company info and popover

        # Determine currency code
        currency = ticker_info.get('currency')
        if currency == 'GBp':
            currency = 'GBP'

        currency_display = currency if currency and isinstance(currency, str) and currency.strip() else 'N/A'

        if not stock_data.empty:
            st.markdown(f"### Current Price and Key Metrics for {display_ticker}")
            st.markdown("*(Data below reflects last End-of-Day or delayed intraday prices from Yahoo Finance)*")

            last_row = stock_data.iloc[-1]
            prev_close = stock_data.iloc[-2]['close'] if len(stock_data) >= 2 else last_row['open']

            daily_change = last_row['close'] - prev_close
            daily_change_percent = (daily_change / prev_close) * 100 if prev_close != 0 else 0

            # More Real-time Details (from EOD data or Ticker Info)
            col_metrics1, col_metrics2, col_metrics3 = st.columns(3)
            with col_metrics1:
                st.metric(label=f"Current Close Price ({stock_data.index[-1].strftime('%Y-%m-%d')})",
                          value=f"{last_row['close']:.2f} {currency_display}",
                          delta=f"{daily_change:+.2f} ({daily_change_percent:+.2f}%)")
                st.metric(label=f"Today's Open", value=f"{last_row['open']:.2f} {currency_display}")
            with col_metrics2:
                st.metric(label=f"Today's High", value=f"{last_row['high']:.2f} {currency_display}")
                st.metric(label=f"Today's Low", value=f"{last_row['low']:.2f} {currency_display}")
            with col_metrics3:
                st.metric(label="Volume", value=f"{int(last_row['volume']):,}")
                st.metric(label="Previous Close", value=f"{prev_close:.2f} {currency_display}" if isinstance(prev_close,
                                                                                                             (int,
                                                                                                              float)) else "N/A")

            # Additional key metrics from ticker.info
            col_advanced_metrics1, col_advanced_metrics2, col_advanced_metrics3 = st.columns(3)
            with col_advanced_metrics1:
                st.metric(label="Market Cap",
                          value=f"{ticker_info.get('marketCap', 'N/A'):,}" if ticker_info.get('marketCap') else "N/A")
            with col_advanced_metrics2:
                st.metric(label="52-week High",
                          value=f"{ticker_info.get('fiftyTwoWeekHigh', 'N/A'):.2f} {currency_display}" if isinstance(
                              ticker_info.get('fiftyTwoWeekHigh'), (int, float)) else "N/A")
                st.metric(label="52-week Low",
                          value=f"{ticker_info.get('fiftyTwoWeekLow', 'N/A'):.2f} {currency_display}" if isinstance(
                              ticker_info.get('fiftyTwoWeekLow'), (int, float)) else "N/A")
            with col_advanced_metrics3:
                st.metric(label="Trailing P/E", value=f"{ticker_info.get('trailingPE', 'N/A'):.2f}" if isinstance(
                    ticker_info.get('trailingPE'), (int, float)) else "N/A")
                st.metric(label="Dividend Yield",
                          value=f"{ticker_info.get('dividendYield', 'N/A') * 100:.2f}%" if isinstance(
                              ticker_info.get('dividendYield'), (int, float)) else "N/A")

            st.markdown(f"### Candlestick Chart for {display_ticker}")
            fig = go.Figure(data=[go.Candlestick(x=stock_data.index,
                                                 open=stock_data['open'],
                                                 high=stock_data['high'],
                                                 low=stock_data['low'],
                                                 close=stock_data['close'])])
            fig.update_layout(xaxis_rangeslider_visible=False, title=f"{display_ticker} Candlestick Chart",
                              xaxis_title="Date", yaxis_title=f"Price ({currency_display})")
            st.plotly_chart(fig, use_container_width=True)

            st.markdown(f"### Trading Volume for {display_ticker}")
            fig_vol = go.Figure(data=[go.Bar(x=stock_data.index, y=stock_data['volume'])])
            fig_vol.update_layout(title=f"{display_ticker} Trading Volume", xaxis_title="Date", yaxis_title="Volume")
            st.plotly_chart(fig_vol, use_container_width=True)

            st.markdown(f"### Recent {display_ticker} News & AI Analysis")


            @st.cache_data(ttl=3600)
            def get_cached_news_stock(query, exchange_short_name, ticker_symbol):
                return get_top_news(query, exchange_short_name=exchange_short_name, ticker_symbol=ticker_symbol,
                                    page_size=5)


            news_query_string = ticker_info.get('longName', f"{display_ticker} stock")

            news_articles, news_source = get_cached_news_stock(
                news_query_string,
                exchange_short_name=ticker_info.get('exchangeShortName'),
                ticker_symbol=display_ticker
            )
            st.info(
                f"News Source: {news_source}. Note: News relevance may vary due to API coverage and ambiguity of search terms.")

            if news_articles:
                for i, article in enumerate(news_articles):
                    st.markdown(f"**[{article.get('title', 'No Title')}]({article.get('url')})**")
                    st.write(
                        f"Source: {article.get('source', {}).get('name', 'N/A')} | Published: {pd.to_datetime(article.get('publishedAt')).strftime('%Y-%m-%d %H:%M') if article.get('publishedAt') else 'N/A'}")
                    st.write(article.get('description', 'No description available.'))
                    st.markdown("---")

                with st.expander(f"Get AI Analysis for {display_ticker} News"):
                    if st.button(f"Analyze News with Gemini for {display_ticker}",
                                 key=f"gemini_stock_news_{display_ticker}"):
                        with st.spinner("Asking Gemini to analyze news..."):
                            gemini_analysis = analyze_news_with_gemini(news_articles, display_ticker)
                            st.write(gemini_analysis)
            else:
                st.info(f"No recent news found for {display_ticker}. News coverage may be limited from available APIs.")
        else:
            st.info(f"No detailed data for {display_ticker} available or fetching failed. Check the ticker symbol.")
    else:
        st.info("Enter a stock ticker above or select from 'Most Viewed Stocks' to see detailed analysis.")

st.markdown("---")
st.header("General AI Market Insights")
st.write("Leverage the Gemini API to get insights on broader market trends.")

general_insights_prompt = st.text_area("Enter your question or prompt for Gemini AI:",
                                       value="What are the current major trends impacting the global stock and cryptocurrency markets? Provide a concise summary.",
                                       height=100, key="general_ai_prompt")

if st.button("Get General AI Insights", key="get_general_insights_btn"):
    if general_insights_prompt:
        with st.spinner("Generating general market insights with Gemini AI..."):
            general_insights = get_gemini_insights(general_insights_prompt)
            st.subheader("Gemini AI General Market Analysis:")
            st.write(general_insights)
    else:
        st.warning("Please enter a prompt for general AI insights.")

time.sleep(update_interval)
st.rerun()