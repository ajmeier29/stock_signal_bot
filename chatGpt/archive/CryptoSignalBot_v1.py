import os
import json
import pandas as pd
import backtrader as bt
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta, timezone
from telegram import Bot
import asyncio
import pytz

LIVE_MODE = False  # Set False for backtest with plotting
ASSET_TYPE = "crypto"
STARTING_CASH = 10000
START = '2025-04-21'
END = '2025-04-24'
ALERT_TIME = 500  # Only send alerts if the signal occurred within the last X minutes, where X is this value

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

# Async message sender
async def send_telegram_message(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
    else:
        print("[MOCK] Would send Telegram message:")
        print(message)

def send_sync(message):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_telegram_message(message))
    except RuntimeError:
        asyncio.run(send_telegram_message(message))

def format_time(bt_dt):
    est = pytz.timezone("US/Eastern")
    dt = bt_dt.datetime(0)
    return dt.astimezone(est).strftime('%Y-%m-%d %I:%M %p (EST)')

class PandasData(bt.feeds.PandasData):
    params = dict(datetime=None, open='Open', high='High', low='Low', close='Close', volume='Volume', openinterest=-1)

class CombinedStrategy(bt.Strategy):
    params = dict(fast_ema=9, slow_ema=21)

    def __init__(self):
        self.ema_fast = {d: bt.ind.EMA(d.close, period=self.p.fast_ema) for d in self.datas}
        self.ema_slow = {d: bt.ind.EMA(d.close, period=self.p.slow_ema) for d in self.datas}
        self.signal_sent = {}  # {symbol: (signal_type, timestamp)}

    def already_sent(self, symbol, signal_type, timestamp):
        last = self.signal_sent.get(symbol)
        return last and last[0] == signal_type and last[1] == timestamp

    def notify_order(self, order):
        if order.status in [order.Completed] and order.executed.dt:
            order_time = order.executed.dt.datetime()
            now_utc = datetime.now(timezone.utc)
            if (order_time.date() == now_utc.date() and
                order_time.hour == now_utc.hour and
                (now_utc.minute - order_time.minute) % 60 < 5):
                action = 'BUY' if order.isbuy() else 'SELL'
                symbol = order.data._name
                price = order.executed.price
                timestamp = format_time(order.data.datetime)
                msg = f"*{action} EXECUTED*: `{symbol}` at *${price:.2f}* — _{timestamp}_"
                send_sync(msg)

    def next(self):
        now_utc = datetime.now(timezone.utc)
        for d in self.datas:
            symbol = d._name
            bar_time = d.datetime.datetime(0).replace(tzinfo=timezone.utc)
            if bar_time.date() != now_utc.date() or bar_time.hour != now_utc.hour or (now_utc.minute - bar_time.minute) % 60 >= ALERT_TIME:
                continue  # Skip past bars

            ema_fast = self.ema_fast[d][0]
            ema_slow = self.ema_slow[d][0]
            price = d.close[0]
            timestamp = d.datetime.datetime(0)

            if ema_fast > ema_slow and not self.already_sent(symbol, 'LONG', timestamp):
                self.buy(data=d)
                self.signal_sent[symbol] = ('LONG', timestamp)
                send_sync(f"🚀 *BUY SIGNAL TRIGGERED!*\nAsset: `{symbol}`\nTime: _{format_time(d.datetime)}_\n")

            elif ema_fast < ema_slow and not self.already_sent(symbol, 'SHORT', timestamp):
                self.sell(data=d)
                self.signal_sent[symbol] = ('SHORT', timestamp)
                send_sync(f"📉 *SHORT SIGNAL TRIGGERED!*\nAsset: `{symbol}`\nTime: _{format_time(d.datetime)}_\n")

            elif self.signal_sent.get(symbol, (None,))[0] == 'LONG' and ema_fast < ema_slow:
                self.close(data=d)
                self.signal_sent[symbol] = ('NONE', timestamp)
                send_sync(f"✅ *EXIT LONG*\nAsset: `{symbol}`\nTime: _{format_time(d.datetime)}_\n")

            elif self.signal_sent.get(symbol, (None,))[0] == 'SHORT' and ema_fast > ema_slow:
                self.close(data=d)
                self.signal_sent[symbol] = ('NONE', timestamp)
                send_sync(f"✅ *EXIT SHORT*\nAsset: `{symbol}`\nTime: _{format_time(d.datetime)}_\n")

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

    if LIVE_MODE:
        start_dt = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0)
        end_dt = datetime.now(timezone.utc).replace(microsecond=0)
        start = start_dt.isoformat().replace('+00:00', 'Z')
        end = end_dt.isoformat().replace('+00:00', 'Z')
    else:
        start = START
        end = END

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
