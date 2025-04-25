import os
import json
import pandas as pd
import backtrader as bt
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta, timezone
from telegram import Bot
import asyncio
import pytz
import requests

LIVE_MODE = True  # Set False for backtest with plotting
ASSET_TYPE = "crypto"
STARTING_CASH = 50000  # Increased to ensure sufficient funds
START = '2025-04-01'
END = '2025-04-21'
TIMEFRAME = TimeFrame(15, TimeFrameUnit.Minute)

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
GROK_API_KEY = os.getenv('GROK_API_KEY')  # Set this in your environment

# Async message sender
message_semaphore = asyncio.Semaphore(1)

async def send_telegram_message(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        async with message_semaphore:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
            await asyncio.sleep(1.1)  # Add delay to stay under 1 msg/sec
    else:
        print("[MOCK] Would send Telegram message:")
        print(message)

def send_sync(message):
    if not LIVE_MODE:
        print(f"[DEBUG] Mock send: {message}")
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_telegram_message(message))
    except RuntimeError:
        try:
            asyncio.run(send_telegram_message(message))
        except Exception as e:
            print(f"[ERROR] Failed to send Telegram message: {e}")

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
        self.last_direction = {}
        self.pending_direction = {}  # symbol → "LONG" or "SHORT"
        self.trade_id_counter = {d._name: 0 for d in self.datas}
        self.active_trade_ids = {}  # Used for next entry
        self.last_trade_id_for_position = {}  # ID for currently active position
        self.ema_fast = {d: bt.ind.EMA(d.close, period=self.p.fast_ema) for d in self.datas}
        self.ema_slow = {d: bt.ind.EMA(d.close, period=self.p.slow_ema) for d in self.datas}
        self.rsi = {d: bt.ind.RSI(d.close, period=self.p.rsi_period) for d in self.datas}
        self.signal_sent = {}  # {symbol: signal_type}
        self.last_signal_price = {}  # {symbol: price of last signal}
        self.last_executed_signal = {}  # {symbol: last executed signal type}

    def get_sentiment_description(self, ticker, direction):
        try:
            url = "https://api.x.ai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {GROK_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "grok-3",
                "messages": [
                    {
                        "role": "system",
                        "content": "System: For the given ticker (e.g., BTC/USD) and signal (\"long\" or \"short\"), return a ~25-word description of current market sentiment, including key sentiment drivers (e.g., social media, ETF inflows). Search for bullish drivers if \"long\" or bearish if \"short\". Exclude price, trading value, market cap, obvious fundamentals (e.g., Bitcoin is a decentralized cryptocurrency), and avoid stating \"sentiment is bullish\" or \"sentiment is bearish\". Perform a quick search for sentiment. No additional content."
                    },
                    {
                        "role": "user",
                        "content": f"Ticker: {ticker}, Signal: {direction}"
                    }
                ],
                "max_tokens": 50,
                "temperature": 0.7
            }
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            market_sentiment = response.json()["choices"][0]["message"]["content"]
            print(f"[DEBUG] Grok API response for {ticker}: {market_sentiment}")
            return market_sentiment
        except Exception as e:
            print(f"[ERROR] Failed to fetch sentiment for {ticker}: {e}")
            return "Unable to fetch market sentiment at this time."

    def create_entry_signal_message(self, symbol, signal, price, timestamp, ema_fast, ema_slow, rsi, market_sentiment):
        if signal == "LONG":
            emoji = "🚀"
            signal_type = "LONG Signal Triggered"
            reason = "Fast EMA crossed above Slow EMA, indicating bullish momentum"
        else:  # SHORT
            emoji = "📉"
            signal_type = "SHORT Signal Triggered"
            reason = "Fast EMA crossed below Slow EMA, indicating bearish momentum"
        
        rsi_status = "Neutral"
        if rsi > 70:
            rsi_status = "Overbought"
        elif rsi < 30:
            rsi_status = "Oversold"
        
        msg = (
            f"{emoji} *{signal_type}!* `{symbol}`\n"
            f"🕒 *Timestamp*: {timestamp}\n"
            f"💰 *Entry Price*: ${price:.2f}\n"
            f"📊 *Reason*: {reason}\n"
            f"🔢 *Indicators*:\n"
            f"  • Fast EMA: ${ema_fast:.2f}\n"
            f"  • Slow EMA: ${ema_slow:.2f}\n"
            f"  • RSI: {rsi:.1f} ({rsi_status})\n"
            f"📝 *Market Sentiment*: {market_sentiment}\n"
        )
        return msg

    def notify_order(self, order):
        if order.status == order.Completed:
            symbol = order.data._name
            price = order.executed.price
            size = order.executed.size
            timestamp = format_time(bt.num2date(order.executed.dt))
            trade_id = self.active_trade_ids.get(symbol, '?')
            direction = "BUY" if order.isbuy() else "SELL"
            print(f"[DEBUG] Order executed: {direction} {symbol}, Trade ID: {trade_id}, "
                  f"Price: ${price:.2f}, Size: {size}, Time: {timestamp}")
            self.last_trade_id_for_position[symbol] = trade_id
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            print(f"[ERROR] Order failed: symbol={order.data._name}, Trade ID: {trade_id}, "
                  f"status={order.getstatusname()}, size={order.size}, alive={order.alive()}")

    def notify_trade(self, trade):
        if trade.isclosed:
            symbol = trade.data._name
            trade_id = self.last_trade_id_for_position.get(symbol, '?')
            direction = self.last_direction.get(symbol, '?')
            pnl = trade.pnl
            entry_price = trade.price
            position_size = abs(trade.size)

            print(f"[DEBUG] Trade closed: symbol={symbol}, direction={direction}, trade_id={trade_id}, "
                  f"entry_price={entry_price:.2f}, pnl={pnl:.2f}, position_size={position_size}")

            if position_size == 0:
                print(f"[ERROR] Zero position size for {symbol}, Trade ID: {trade_id}")
                return

            self.last_trade_id_for_position[symbol] = None
            self.active_trade_ids[symbol] = None
            # Do NOT reset self.signal_sent here to prevent duplicates

    def next(self):
        for d in self.datas:
            try:
                symbol = d._name
                ema_fast = self.ema_fast[d][0]  # Current fast EMA
                ema_slow = self.ema_slow[d][0]  # Current slow EMA
                ema_fast_prev = self.ema_fast[d][-1]  # Previous bar's fast EMA
                ema_slow_prev = self.ema_slow[d][-1]  # Previous bar's slow EMA
                rsi = self.rsi[d][0]
                position = self.getposition(d).size

                # Determine signal intent from EMA crossover
                current_signal = None
                # LONG: Fast EMA crosses above Slow EMA
                if ema_fast > ema_slow and ema_fast_prev <= ema_slow_prev:
                    current_signal = "LONG"
                # SHORT: Fast EMA crosses below Slow EMA
                elif ema_fast < ema_slow and ema_fast_prev >= ema_slow_prev:
                    current_signal = "SHORT"

                # print(f"[DEBUG] {symbol}: position={position}, current_signal={current_signal}, "
                #     f"last_direction={self.last_direction.get(symbol, '?')}, "
                #     f"ema_fast={ema_fast:.2f}, ema_slow={ema_slow:.2f}, "
                #     f"ema_fast_prev={ema_fast_prev:.2f}, ema_slow_prev={ema_slow_prev:.2f}")

                # Exit if open position is opposite of new signal
                if position > 0 and current_signal == "SHORT":
                    print(f"[DEBUG] Closing LONG position for {symbol}")
                    self.close(data=d)
                    self.pending_direction[symbol] = "SHORT"
                    continue

                elif position < 0 and current_signal == "LONG":
                    print(f"[DEBUG] Closing SHORT position for {symbol}")
                    self.close(data=d)
                    self.pending_direction[symbol] = "LONG"
                    continue

                # Entry logic only if flat
                if position == 0:
                    signal = self.pending_direction.get(symbol) or current_signal
                    trade_size = 0.1

                    # Only trigger a new signal if it differs from the last executed signal
                    last_signal = self.last_executed_signal.get(symbol)
                    if signal and signal != last_signal:
                        if signal == "LONG" and self.signal_sent.get(symbol) != "LONG":
                            self.last_direction[symbol] = "LONG"
                            print(f"[DEBUG] Placing BUY order for {symbol}, size={trade_size}, cash={self.broker.getcash():.2f}")
                            self.trade_id_counter[symbol] += 1
                            self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
                            self.buy(data=d, size=trade_size)
                            self.signal_sent[symbol] = "LONG"
                            self.last_executed_signal[symbol] = "LONG"
                            self.pending_direction[symbol] = None

                            estimated_price = d.close[0]
                            timestamp = format_time(d.datetime)
                            description = self.get_sentiment_description(symbol, "LONG")
                            msg = self.create_entry_signal_message(symbol, "LONG", estimated_price, timestamp, ema_fast, ema_slow, rsi, description)
                            send_sync(msg)
                            self.last_signal_price[symbol] = estimated_price

                        elif signal == "SHORT" and self.signal_sent.get(symbol) != "SHORT":
                            self.last_direction[symbol] = "SHORT"
                            print(f"[DEBUG] Placing SELL order for {symbol}, size={trade_size}, cash={self.broker.getcash():.2f}")
                            self.trade_id_counter[symbol] += 1
                            self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
                            self.sell(data=d, size=trade_size)
                            self.signal_sent[symbol] = "SHORT"
                            self.last_executed_signal[symbol] = "SHORT"
                            self.pending_direction[symbol] = None

                            estimated_price = d.close[0]
                            timestamp = format_time(d.datetime)
                            description = self.get_sentiment_description(symbol, "SHORT")
                            msg = self.create_entry_signal_message(symbol, "SHORT", estimated_price, timestamp, ema_fast, ema_slow, rsi, description)
                            send_sync(msg)
                            self.last_signal_price[symbol] = estimated_price

            except Exception as e:
                print(f"[ERROR] {symbol} — {type(e).__name__}: {e}")


def get_data(symbol, start, end):
    try:
        if ASSET_TYPE == "crypto":
            bars = alpaca.get_crypto_bars(symbol, TIMEFRAME, start=start, end=end).df
        else:
            bars = alpaca.get_bars(symbol, TIMEFRAME, start=start, end=end).df
        print(f"[DEBUG] Fetched {len(bars)} bars for {symbol}, sample close: {bars['close'].iloc[-1] if not bars.empty else 'N/A'}")
        if bars.empty:
            print(f"[ERROR] Empty data for {symbol}")
            return pd.DataFrame()
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
    cerebro.broker.setcommission(commission=0.001)  # 0.1% commission
    print(f"[DEBUG] Initial cash: {cerebro.broker.getcash():.2f}")

    # Verify Alpaca account
    try:
        account = alpaca.get_account()
        print(f"[DEBUG] Alpaca account: cash={account.cash}, buying_power={account.buying_power}")
    except Exception as e:
        print(f"[ERROR] Alpaca API connection failed: {e}")

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
        print(f"[DEBUG] Cash before trade for {symbol}: {cerebro.broker.getcash():.2f}")

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