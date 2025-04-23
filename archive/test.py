import os
import pandas as pd
import backtrader as bt
from alpaca_trade_api.rest import REST
import yfinance as yf
import telegram
import asyncio
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
maxTickers = 50

if not all([ALPACA_API_KEY, ALPACA_SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    raise ValueError("Missing environment variables for Alpaca or Telegram")

# Initialize Alpaca REST client
alpaca = REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, base_url='https://paper-api.alpaca.markets')

# Initialize Telegram bot
bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

def get_high_volume_tickers(min_volume=500000, max_tickers=maxTickers):
    """Fetch 500+ US stock tickers with average daily volume >= min_volume."""
    logger.info("Fetching high-volume tickers...")
    
    # Get S&P 500 and NASDAQ tickers
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
    """Custom PandasData feed for Alpaca data."""
    params = (
        ('datetime', 'timestamp'),
        ('open', 'open'),
        ('high', 'high'),
        ('low', 'low'),
        ('close', 'close'),
        ('volume', 'volume'),
        ('openinterest', None),
    )

class SMARSIStrategy(bt.Strategy):
    params = (
        ('sma_short', 10),
        ('sma_long', 50),
        ('rsi_period', 14),
        ('rsi_low', 30),
        ('rsi_high', 70),
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
                self.signals.append({
                    'ticker': ticker,
                    'signal': 'Buy',
                    'price': price,
                    'time': timestamp,
                    'rsi': rsi,
                    'sma_short': sma_short,
                    'sma_long': sma_long
                })
            # Sell: SMA 10 crosses below SMA 50 and RSI > 70
            elif (sma_short < sma_long and sma_short_prev >= sma_long_prev and
                  rsi > self.p.rsi_high and self.getposition(data)):
                self.signals.append({
                    'ticker': ticker,
                    'signal': 'Sell',
                    'price': price,
                    'time': timestamp,
                    'rsi': rsi,
                    'sma_short': sma_short,
                    'sma_long': sma_long
                })
    
    async def send_signals(self):
        for signal in self.signals:
            try:
                message = (
                    f"📊 *{signal['ticker']} Signal* 📉\n"
                    f"Signal: {signal['signal']}\n"
                    f"Price: ${signal['price']:.2f}\n"
                    f"RSI: {signal['rsi']:.2f}\n"
                    f"SMA 10: {signal['sma_short']:.2f}\n"
                    f"SMA 50: {signal['sma_long']:.2f}\n"
                    f"Time: {signal['time']}\n"
                    f"#StockSignals"
                )
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=message,
                    parse_mode='Markdown'
                )
                logger.info(f"Sent signal for {signal['ticker']}: {signal['signal']}")
            except Exception as e:
                logger.error(f"Error sending signal for {signal['ticker']}: {e}")
        self.signals.clear()

def fetch_alpaca_data(tickers, timeframe='5Min', limit=100):
    """Fetch 5-minute OHLCV data from Alpaca for multiple tickers."""
    try:
        bars = alpaca.get_bars(symbol=tickers, timeframe=timeframe, limit=limit).df
        return bars
    except Exception as e:
        logger.error(f"Error fetching Alpaca data: {e}")
        return pd.DataFrame()

def main():
    # Get high-volume tickers
    tickers = get_high_volume_tickers()
    
    # Initialize Backtrader
    cerebro = bt.Cerebro()
    
    # Fetch initial data
    bars = fetch_alpaca_data(tickers)
    if bars.empty:
        logger.error("No data fetched from Alpaca")
        return
    
    # Add data feeds
    for ticker in tickers:
        try:
            df = bars[bars['symbol'] == ticker][['open', 'high', 'low', 'close', 'volume']].reset_index()
            df = df.rename(columns={'index': 'timestamp'})
            if df.empty:
                logger.warning(f"No data for {ticker}")
                continue
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            data = AlpacaPandasData(
                dataname=df,
                timeframe=bt.TimeFrame.Minutes,
                compression=5,
                name=ticker
            )
            cerebro.adddata(data)
            logger.info(f"Added data feed for {ticker}")
        except Exception as e:
            logger.warning(f"Error adding data for {ticker}: {e}")
    
    # Add strategy
    cerebro.addstrategy(SMARSIStrategy)
    
    # Simulate live data updates
    logger.info("Starting Backtrader with simulated live updates...")
    while True:
        try:
            cerebro.run(runonce=False)  # Run incrementally
            strategy = cerebro.runningstrats[0]
            asyncio.run(strategy.send_signals())
            
            # Fetch new data every 5 minutes
            time.sleep(300)
            bars = fetch_alpaca_data(tickers, limit=1)
            if not bars.empty:
                for ticker in tickers:
                    df_new = bars[bars['symbol'] == ticker][['open', 'high', 'low', 'close', 'volume']].reset_index()
                    df_new = df_new.rename(columns={'index': 'timestamp'})
                    if not df_new.empty:
                        df_new['timestamp'] = pd.to_datetime(df_new['timestamp'])
                        # Update existing data feed (simplified)
                        logger.info(f"Updated data for {ticker}")
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")