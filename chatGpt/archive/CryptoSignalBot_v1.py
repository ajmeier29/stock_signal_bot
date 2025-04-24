import os
import json
import pandas as pd
import backtrader as bt
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta, timezone
from telegram import Bot
import asyncio
import pytz

LIVE_MODE = True  # Set False for backtest with plotting
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

# Async message sender
message_semaphore = asyncio.Semaphore(1)

async def send_telegram_message(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        async with message_semaphore:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
            await asyncio.sleep(1.1)  # add a slight delay to stay under 1 msg/sec
    else:
        print("[MOCK] Would send Telegram message:")
        print(message)

def send_sync(message):
    if not LIVE_MODE:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_telegram_message(message))
    except RuntimeError:
        asyncio.run(send_telegram_message(message))

def format_time(bt_dt):
    est = pytz.timezone("US/Eastern")
    if isinstance(bt_dt, datetime):
        dt = bt_dt
    elif hasattr(bt_dt, 'datetime'):
        dt = bt_dt.datetime(0)
    elif hasattr(bt_dt, '__getitem__'):
        dt = bt.num2date(bt_dt[0])
    else:
        dt = bt_dt
    return dt.astimezone(est).strftime('%Y-%m-%d %I:%M %p (EST)')

class PandasData(bt.feeds.PandasData):
    params = dict(datetime=None, open='Open', high='High', low='Low', close='Close', volume='Volume', openinterest=-1)

class CombinedStrategy(bt.Strategy):
    params = dict(fast_ema=9, slow_ema=21, rsi_period=14, rsi_upper=70, rsi_lower=30)

    def __init__(self):
        self.ema_fast = {d: bt.ind.EMA(d.close, period=self.p.fast_ema) for d in self.datas}
        self.ema_slow = {d: bt.ind.EMA(d.close, period=self.p.slow_ema) for d in self.datas}
        self.rsi = {d: bt.ind.RSI(d.close, period=self.p.rsi_period) for d in self.datas}
        self.signal_sent = {}  # {symbol: signal_type}

    def notify_trade(self, trade):
        if trade.isclosed:
            symbol = trade.data._name
            pnl = trade.pnl
            msg = f"📈 *TRADE CLOSED* `{symbol}` Profit: *${pnl:.2f}*"
            send_sync(msg)


    def notify_order(self, order):
        if order.status in [order.Completed]:
            action = 'BUY' if order.isbuy() else 'SELL'
            symbol = order.data._name
            price = order.executed.price
            timestamp = format_time(order.data.datetime)
            msg = f"*{action} EXECUTED*: `{symbol}` at *${price:.2f}* — _{timestamp}_"
            send_sync(msg)

    def next(self):
        for d in self.datas:
            symbol = d._name
            ema_fast = self.ema_fast[d][0]
            ema_slow = self.ema_slow[d][0]
            price = d.close[0]
            timestamp = d.datetime.datetime(0)

            # ✅ Exit logic first
            if self.signal_sent.get(symbol) == 'LONG' and ema_fast < ema_slow:
                self.close(data=d)
                self.signal_sent[symbol] = None
                send_sync(f"✅ *EXIT LONG* Asset: `{symbol}` Time: _{format_time(d.datetime)}_")

            elif self.signal_sent.get(symbol) == 'SHORT' and ema_fast > ema_slow:
                self.close(data=d)
                self.signal_sent[symbol] = None
                send_sync(f"✅ *EXIT SHORT* Asset: `{symbol}` Time: _{format_time(d.datetime)}_")

            # 🚀 Entry logic after exit logic
            if ema_fast > ema_slow and self.signal_sent.get(symbol) != 'LONG':
                self.buy(data=d)
                self.signal_sent[symbol] = 'LONG'
                send_sync(f"🚀 *BUY SIGNAL TRIGGERED!* Asset: `{symbol}` Time: _{format_time(d.datetime)}_")

            elif ema_fast < ema_slow and self.signal_sent.get(symbol) != 'SHORT':
                self.sell(data=d)
                self.signal_sent[symbol] = 'SHORT'
                send_sync(f"📉 *SHORT SIGNAL TRIGGERED!* Asset: `{symbol}` Time: _{format_time(d.datetime)}_")


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
