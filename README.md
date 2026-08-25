# Marketpulse

### A Real-time  and Crypto Market Dashboard

This project is a powerful, interactive market tracker built using **Python** and **Streamlit**. It provides users with live data, historical analysis, and AI-driven insights for both stocks and cryptocurrencies, all within a clean and user-friendly web interface.

### Key Features

* **Real-time Data:** Get up-to-the-minute prices for major stocks and cryptocurrencies.
* **Historical Analysis:** View detailed historical price charts and trading volumes for any supported asset.
* **Global Stock Search:** Search for any company from various global stock exchanges using ticker symbols.
* **Intelligent News Aggregation:** Fetches relevant news articles from multiple APIs, prioritizing the most relevant source for the selected company's exchange.
* **AI-Powered Insights:** Utilizes the Google Gemini API to provide a summary and analysis of news articles and answer general market questions.
* **Dynamic UI:** The interface is responsive and allows you to easily switch between crypto and stock views.

### Technologies Used

* **Frontend:** Streamlit
* **Backend:** Python
* **Stock Data:** yfinance
* **Crypto Data:** CoinGecko API
* **News Sources:** NewsAPI.org & MarketAux API
* **AI Model:** Google Gemini API
* **Data Handling:** Pandas & Plotly

### Getting Started

To run this project on your local machine, follow these steps.

#### 1. Clone the repository

```bash
git clone [https://github.com/Rohan-2604/Marketpulse.git](https://github.com/Rohan-2604/Marketpulse.git)
cd Marketpulse

2. Set up the virtual environment
Bash

# For Windows
python -m venv venv
.\venv\Scripts\activate

# For macOS/Linux
python3 -m venv venv
source venv/bin/activate
3. Install dependencies
Install all required Python libraries using the requirements.txt file.

Bash

pip install -r requirements.txt
4. Configure API Keys
This project relies on several external APIs. You must set your API keys as system environment variables.

NEWS_API_KEY: Get a key from NewsAPI.org.

MARKETAUX_API_KEY: Get a key from MarketAux.com.

GEMINI_API_KEY: Get a key from Google AI Studio.

5. Run the application
Once your environment is set up and API keys are configured, run the Streamlit application from your terminal.

Bash

streamlit run app.py
This will launch the web application in your default browser.
