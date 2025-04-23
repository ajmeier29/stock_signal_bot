import os
import pandas as pd
import backtrader as bt
import yfinance as yf
import logging
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables (only needed for consistency, not used in yfinance)
load_dotenv()
maxTickers = 25

def get_high_volume_tickers(min_volume=500000, max_tickers=maxTickers):
    """Fetch US stock tickers with average daily volume >= min_volume."""
    logger.info("Fetching high-volume tickers...")
    
    # Get S&P 500 tickers
    sp500 = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')[0]['Symbol'].tolist()
    tickers = sp500
    
    # Add more NASDAQ tickers if needed
    if len(tickers) < max_tickers:
        nasdaq_stocks = pd.read_csv('https://old.nasdaq.com/screening/companies-by-name.aspx?letter=0&exchange=nasdaq&render=download')
        tickers.extend(nasdaq_stocks['Symbol'].tolist())
        tickers = list(dict.fromkeys(tickers))  # Remove duplicates
    
    # Filter by volume
    high_volume_tickers = []
    for ticker in tickers[:max_tickers * 2]:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period='10d', interval='1d')
            if hist.empty or len(hist) < 10:
                continue
            avg_volume = hist['Volume'].mean()
            if avg_volume >= min_volume:
                high_volume_tickers.append(ticker)
                logger.info(f"Added {ticker}: Avg Volume {avg_volume:.0f}")
            if len(high_volume_tickers) >= max_tickers:
                break
        except Exception as e:
            logger.warning(f"Error fetching {ticker}: {e}")
        time.sleep(0.1)  # Avoid yfinance rate limits
    
    logger.info(f"Found {len(high_volume_tickers)} high-volume tickers")
    return high_volume_tickers[:max_tickers]

class AlpacaPandasData(bt.feeds.PandasData):
    """Custom PandasData feed for yfinance data (compatible with Alpaca format)."""
    params = {
        'datetime': 'timestamp',
        'open': 'open',
        'high': 'high',
        'low': 'low',
        'close': 'close',
        'volume': 'volume',
        'openinterest': -1  # No openinterest column
    }

class SMARSIStrategy(bt.Strategy):
    params = (
        ('sma_short', 10),
        ('sma_long', 50),
        ('rsi_period', 14),
        ('rsi_low', 30),
        ('rsi_high', 70),
        ('size', 100),  # Number of shares to trade
    )
    
    def __init__(self):
        self.signals = []
        self.indicators = {}
        for data in self.datas:
            self.indicators[data._name] = {
                'sma_short': bt.indicators.SMA(data.close, period=self.p.sma_short),
                'sma_long': bt.indicators.SMA(data.close, period=self.p.sma_long),
                'rsi': bt.indicators.RSI(data.close, period=self.p.rsi_period)
            }
    
    def next(self):
        for data in self.datas:
            ticker = data._name
            sma_short = self.indicators[ticker]['sma_short'][0]
            sma_long = self.indicators[ticker]['sma_long'][0]
            sma_short_prev = self.indicators[ticker]['sma_short'][-1]
            sma_long_prev = self.indicators[ticker]['sma_long'][-1]
            rsi = self.indicators[ticker]['rsi'][0]
            price = data.close[0]
            timestamp = data.datetime.datetime()
            
            # Buy: SMA 10 crosses above SMA 50 and RSI < 30
            if (sma_short > sma_long and sma_short_prev <= sma_long_prev and
                rsi < self.p.rsi_low and not self.getposition(data)):
                self.buy(data=data, size=self.p.size)
                self.signals.append({
                    'ticker': ticker,
                    'signal': 'Buy',
                    'price': price,
                    'time': timestamp,
                    'rsi': rsi,
                    'sma_short': sma_short,
                    'sma_long': sma_long
                })
                logger.info(f"Buy signal for {ticker} at {price:.2f}, RSI: {rsi:.2f}")
            # Sell: SMA 10 crosses below SMA 50 and RSI > 70
            elif (sma_short < sma_long and sma_short_prev >= sma_long_prev and
                  rsi > self.p.rsi_high and self.getposition(data)):
                self.sell(data=data, size=self.p.size)
                self.signals.append({
                    'ticker': ticker,
                    'signal': 'Sell',
                    'price': price,
                    'time': timestamp,
                    'rsi': rsi,
                    'sma_short': sma_short,
                    'sma_long': sma_long
                })
                logger.info(f"Sell signal for {ticker} at {price:.2f}, RSI: {rsi:.2f}")

def fetch_yfinance_historical_data(tickers, timeframe='5m', start_date='2025-03-01', end_date='2025-04-21'):
    """Fetch historical 5-minute OHLCV data from yfinance for multiple tickers."""
    logger.info(f"Fetching historical data for {len(tickers)} tickers from {start_date} to {end_date}...")
    all_bars = []
    for ticker in tickers:
        try:
            df = yf.download(ticker, start=start_date, end=end_date, interval=timeframe)
            if not df.empty:
                # Flatten MultiIndex columns if present
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [col[0].lower() for col in df.columns]
                else:
                    df.columns = df.columns.str.lower()
                # Reset index to make Datetime a column and rename to timestamp
                df = df.reset_index().rename(columns