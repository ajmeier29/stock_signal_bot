import os
import json
import pandas as pd
import backtrader as bt
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta
from telegram import Bot
import asyncio
import pytz

LIVE_MODE = False
ASSET_TYPE = "crypto"
STARTING_CASH = 10000
START = '2025-01-01'
END = '2025-04-21'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TICKER_PATH = os.path.join(BASE_DIR, "tickers.json")
with open(TICKER_PATH) as f:
    TICKERS = json.load(f).get(ASSET_TYPE, [])

API_KEY = os.getenv('ALPACA_API_KEY')
API_SECRET = os.getenv('ALPACA_SECRET_KEY')
BASE_URL = 'https://paper-api.alpaca.markets'
alpaca = REST(API_KEY, API_SECRET, BASE_URL)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)

async def send_telegram_message(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        await telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
    else:
        print("[MOCK] Would send Telegram message:")
        print(message)

def format_time(bt_dt):
    est = pytz.timezone("US/Eastern")
    dt = bt_dt.datetime(0)
    return dt.astimezone(est).strftime('%Y-%m-%d %I:%M %p (EST)')

class PandasData(bt.feeds.PandasData):
    params = dict(datetime=None, open='Open', high='High', low='Low', close='Close', volume='Volume', openinterest=-1)

class CombinedStrategy(bt.Strategy):
    params = dict(fast_ema=9, slow_ema=21, rsi_period=14, rsi_upper=70, rsi_lower=30)

    def __init__(self):
        self.ema_fast = {d: bt.ind.EMA(d.close, period=self.p.fast_ema) for d in self.datas}
        self.ema_slow = {d: bt.ind.EMA(d.close, period=self.p.slow_ema) for d in self.datas}
        self.rsi = {d: bt.ind.RSI(d.close, period=self.p.rsi_period) for d in self.datas}
        self.signal_sent = {}
        self.total_pnl = 0

    def next(self):
        for d in self.datas:
            symbol = d._name
            ema_fast = self.ema_fast[d][0]
            ema_slow = self.ema_slow[d][0]

            if ema_fast > ema_slow and self.signal_sent.get(symbol) != 'LONG':
                # print(f"LONG SIGNAL: {symbol} at ${d.close[0]:.2f}")
                self.buy(data=d, exectype=bt.Order.Market)
                self.signal_sent[symbol] = 'LONG'

            elif ema_fast < ema_slow and self.signal_sent.get(symbol) != 'SHORT':
                # print(f"SHORT SIGNAL: {symbol} at ${d.close[0]:.2f}")
                self.sell(data=d, exectype=bt.Order.Market)
                self.signal_sent[symbol] = 'SHORT'

            elif self.signal_sent.get(symbol) == 'LONG' and ema_fast < ema_slow:
                # print(f"EXIT LONG: {symbol} at ${d.close[0]:.2f}")
                self.close(data=d, exectype=bt.Order.Market)
                self.signal_sent[symbol] = None

            elif self.signal_sent.get(symbol) == 'SHORT' and ema_fast > ema_slow:
                # print(f"EXIT SHORT: {symbol} at ${d.close[0]:.2f}")
                self.close(data=d, exectype=bt.Order.Market)
                self.signal_sent[symbol] = None


def get_data(symbol, start, end):
    try:
        if ASSET_TYPE == "crypto":
            bars = alpaca.get_crypto_bars(symbol, TimeFrame(15, TimeFrameUnit.Minute), start=start, end=end).df
        else:
            bars = alpaca.get_bars(symbol, TimeFrame(15, TimeFrameUnit.Minute), start=start, end=end).df

        bars.index = pd.to_datetime(bars.index)
        bars = bars[['open', 'high', 'low', 'close', 'volume']]
        bars.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        return bars

    except Exception as e:
        print(f"[ERROR] Failed to fetch data for {symbol}: {e}")
        return pd.DataFrame()

def run():
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(STARTING_CASH)
    cerebro.addstrategy(CombinedStrategy)

    start = (datetime.utcnow() - timedelta(days=7)).isoformat() if LIVE_MODE else START
    end = datetime.utcnow().isoformat() if LIVE_MODE else END

    for symbol in TICKERS:
        print(f"[INFO] Fetching data for {symbol} ({ASSET_TYPE})...")
        df = get_data(symbol, start, end)
        if df.empty or len(df) < 50:
            print(f"[WARNING] Skipping {symbol} (insufficient data)")
            continue

        data = PandasData(dataname=df)
        cerebro.adddata(data, name=symbol)

    print(f"Starting Value: {cerebro.broker.getvalue():.2f}")
    cerebro.run()
    print(f"Final Value: {cerebro.broker.getvalue():.2f}")

    if not LIVE_MODE:
        cerebro.plot()

if __name__ == '__main__':
    if LIVE_MODE:
        import time
        while True:
            try:
                run()
                print("Waiting 15 mins...")
                time.sleep(900)
            except Exception as e:
                print(f"Error: {e}. Retrying in 60 sec.")
                time.sleep(60)
    else:
        run()
