import os
import pandas as pd
import backtrader as bt
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta
from telegram import Bot
import asyncio
import pytz

# === CONFIGURATION ===
LIVE_MODE = False  # Set True for live signals, False for backtesting
TICKERS = ['AAPL', 'MSFT', 'AMZN', 'GOOG']
STARTING_CASH = 10000

# Alpaca setup
API_KEY = os.getenv('ALPACA_API_KEY')
API_SECRET = os.getenv('ALPACA_SECRET_KEY')
BASE_URL = 'https://paper-api.alpaca.markets'
alpaca = REST(API_KEY, API_SECRET, BASE_URL)

# Telegram setup
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)

async def send_telegram_message(message):
    await telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')

# Helper to format time nicely
def format_time(bt_dt):
    est = pytz.timezone("US/Eastern")
    dt = bt_dt.datetime(0)
    return dt.astimezone(est).strftime('%Y-%m-%d %I:%M %p (EST)')

# === Data feed wrapper ===
class PandasData(bt.feeds.PandasData):
    params = dict(datetime=None, open='Open', high='High', low='Low', close='Close', volume='Volume', openinterest=-1)

# === Strategy definition ===
class CombinedStrategy(bt.Strategy):
    params = dict(fast_ema=9, slow_ema=21, rsi_period=14, rsi_upper=70, rsi_lower=30)

    def __init__(self):
        self.ema_fast = {d: bt.ind.EMA(d.close, period=self.p.fast_ema) for d in self.datas}
        self.ema_slow = {d: bt.ind.EMA(d.close, period=self.p.slow_ema) for d in self.datas}
        self.rsi = {d: bt.ind.RSI(d.close, period=self.p.rsi_period) for d in self.datas}
        self.signal_sent = {}

    def next(self):
        for d in self.datas:
            symbol = d._name
            ema_fast = self.ema_fast[d][0]
            ema_slow = self.ema_slow[d][0]
            rsi = self.rsi[d][0]

            try:
                price_change = ((d.close[0] - d.close[-4]) / d.close[-4]) * 100
                vol_avg = sum([d.volume[-i] for i in range(1, 6)]) / 5
                vol_change = ((d.volume[0] - vol_avg) / vol_avg) * 100
            except Exception:
                price_change = 0
                vol_change = 0

            time_str = format_time(self.datas[0].datetime)

            # EMA Crossover (Trend Following)
            if ema_fast > ema_slow and self.signal_sent.get(symbol) != 'LONG':
                msg = (
                    f"\U0001F4C8 *[BUY SIGNAL - TREND BREAKOUT]*\n\n"
                    f"*Ticker:* {symbol}\n"
                    f"*Price:* ${d.close[0]:.2f}\n"
                    f"*Time:* {time_str}\n"
                    f"*Strategy:* EMA({self.p.fast_ema}) crossed ABOVE EMA({self.p.slow_ema})\n\n"
                    f"\u2705 *Momentum:* +{price_change:.2f}% past hour\n"
                    f"\U0001F4CA *Volume Spike:* {vol_change:.1f}% above avg"
                )
                if LIVE_MODE:
                    asyncio.run(send_telegram_message(msg))
                self.buy(data=d)
                self.signal_sent[symbol] = 'LONG'

            elif ema_fast < ema_slow and self.signal_sent.get(symbol) != 'SHORT':
                msg = (
                    f"\U0001F4C9 *[SELL SIGNAL - TREND REVERSAL]*\n\n"
                    f"*Ticker:* {symbol}\n"
                    f"*Price:* ${d.close[0]:.2f}\n"
                    f"*Time:* {time_str}\n"
                    f"*Strategy:* EMA({self.p.fast_ema}) crossed BELOW EMA({self.p.slow_ema})\n\n"
                    f"\u274C *Momentum:* {price_change:.2f}% past hour\n"
                    f"\U0001F4CA *Volume Drop:* {vol_change:.1f}% vs avg"
                )
                if LIVE_MODE:
                    asyncio.run(send_telegram_message(msg))
                self.sell(data=d)
                self.signal_sent[symbol] = 'SHORT'

            # RSI Mean-Reversion
            if rsi < self.p.rsi_lower and self.signal_sent.get(symbol) != 'RSI_BUY':
                msg = (
                    f"\U0001F7E2 *[BUY SIGNAL - OVERSOLD]*\n\n"
                    f"*Ticker:* {symbol}\n"
                    f"*Price:* ${d.close[0]:.2f}\n"
                    f"*Time:* {time_str}\n"
                    f"*Strategy:* RSI = {rsi:.2f} (Oversold)\n\n"
                    f"\u2B07 Price drop: {price_change:.2f}%\n"
                    f"\U0001F4CA Volume: {vol_change:.1f}% vs avg"
                )
                if LIVE_MODE:
                    asyncio.run(send_telegram_message(msg))
                self.buy(data=d)
                self.signal_sent[symbol] = 'RSI_BUY'

            elif rsi > self.p.rsi_upper and self.signal_sent.get(symbol) != 'RSI_SELL':
                msg = (
                    f"\U0001F534 *[SELL SIGNAL - OVERBOUGHT]*\n\n"
                    f"*Ticker:* {symbol}\n"
                    f"*Price:* ${d.close[0]:.2f}\n"
                    f"*Time:* {time_str}\n"
                    f"*Strategy:* RSI = {rsi:.2f} (Overbought)\n\n"
                    f"\u2B06 Price up: {price_change:.2f}%\n"
                    f"\U0001F4CA Volume: {vol_change:.1f}% vs avg"
                )
                if LIVE_MODE:
                    asyncio.run(send_telegram_message(msg))
                self.sell(data=d)
                self.signal_sent[symbol] = 'RSI_SELL'

# Fetch Data Function
def get_data(symbol, start, end):
    bars = alpaca.get_bars(symbol, TimeFrame(15, TimeFrameUnit.Minute), start=start, end=end).df
    bars.index = pd.to_datetime(bars.index)
    bars = bars[['open', 'high', 'low', 'close', 'volume']]
    bars.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
    return bars

# Run bot/backtest
def run():
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(STARTING_CASH)
    cerebro.addstrategy(CombinedStrategy)

    if LIVE_MODE:
        start = (datetime.utcnow() - timedelta(days=7)).isoformat()
        end = datetime.utcnow().isoformat()
    else:
        start = '2024-01-01'
        end = '2025-04-21'

    for symbol in TICKERS:
        df = get_data(symbol, start, end)
        if len(df) < 50:
            print(f"Skipping {symbol} (insufficient data)")
            continue

        data = PandasData(dataname=df)
        cerebro.adddata(data, name=symbol)

    print(f'Starting Value: {cerebro.broker.getvalue():.2f}')
    cerebro.run()
    print(f'Final Value: {cerebro.broker.getvalue():.2f}')

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
