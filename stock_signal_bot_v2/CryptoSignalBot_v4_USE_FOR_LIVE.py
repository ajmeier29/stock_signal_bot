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
from strategies.EMACrossoverStrategy import EMACrossoverStrategy,EMACrossoverStrategy_v2
from strategies.RSIDivergenceStrategy import RSIDivergenceStrategy
from strategies.RSIConfirmationStrategy import RSIConfirmationStrategy
from strategies.JoeStrategy_V1 import JoeStrategy_V1

ENABLE_SHORTS = True
SEND_TELLY_MSG = True
LIVE_MODE = True  # Set False for backtest with plotting
LIVE_LOOKBACK_DAYS = 3  # Configurable lookback period for live mode
ASSET_TYPE = "crypto"  # crypto | stocks
TRADE_SIZE_PERCENT = 0.3 
STARTING_CASH = 5000  # Increased to ensure sufficient funds
BACKTEST_START = '2024-01-01'          
BACKTEST_END = '2025-04-24'
TIMEFRAME = TimeFrame(15, TimeFrameUnit.Minute)
INDICATOR_LOOKBACK_MINUTES = 750  # Enough for SMA50 (50 bars = 150 min), plus buffer
ENABLE_EXCEPTION_HANDLING = False  # Set to False to disable exception handling and let script crash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TICKER_PATH = os.path.join(BASE_DIR, "tickers.json")
SIGNAL_HISTORY_PATH = os.path.join(BASE_DIR, "signal_history.json")
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

def parse_formatted_time(formatted_time):
    """Parse the formatted time string back to a datetime object in UTC."""
    est = pytz.timezone("US/Eastern")
    dt = datetime.strptime(formatted_time, '%Y-%m-%d %I:%M %p (EST)')
    dt = est.localize(dt)
    return dt.astimezone(timezone.utc)

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
        self.dma_50 = {d: bt.ind.SMA(d.close, period=50) for d in self.datas}  # 50-day DMA

        self.signal_sent = {}  # {symbol: signal_type}
        self.last_signal_price = {}  # {symbol: price of last signal}
        self.last_executed_signal = {}  # {symbol: last executed signal type}
        self.signal_history = self.load_signal_history()  # Load persistent signal history

        # Strategy registry
        self.strategies = [
            #EMACrossoverStrategy(self.ema_fast, self.ema_slow),   # -- Combine this with RSIConfirmationStrategy for best strat
            EMACrossoverStrategy_v2(self.ema_fast, self.ema_slow),  # -- Combine this with RSIConfirmationStrategy for best strat
            RSIConfirmationStrategy(self.rsi),                    # -- Combine this with RSIConfirmationStrategy for best strat
            #JoeStrategy_V1(self.datas, self.dma_50, alpaca),
        ]

    def load_signal_history(self):
        """Load signal history from file."""
        try:
            with open(SIGNAL_HISTORY_PATH, 'r') as f:
                data = json.load(f)
                # Convert list of [signal, timestamp, price] to set of tuples
                return {symbol: set((s, t, p) for s, t, p in signals) for symbol, signals in data.items()}
        except (FileNotFoundError, json.JSONDecodeError):
            # Create an empty file if it doesn't exist
            data = {}
            with open(SIGNAL_HISTORY_PATH, 'w') as f:
                json.dump(data, f)
            return {}

    def save_signal_history(self):
        """Save signal history to file, overwriting existing file."""
        # Convert sets to lists of [signal, timestamp, price] for JSON serialization
        data = {symbol: [[s, t, p] for s, t, p in signals] for symbol, signals in self.signal_history.items()}
        with open(SIGNAL_HISTORY_PATH, 'w') as f:
            json.dump(data, f)

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
            reason = "Combined strategy signal, indicating bullish momentum"
        else:  # SHORT
            emoji = "📉"
            signal_type = "SHORT Signal Triggered"
            reason = "Combined strategy signal, indicating bearish momentum"
        
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
        symbol = order.data._name
        trade_id = self.active_trade_ids.get(symbol, '?')
        if order.status == order.Completed:
            price = order.executed.price
            size = abs(order.executed.size)  # Use absolute size
            timestamp = format_time(bt.num2date(order.executed.dt))
            direction = "BUY" if order.isbuy() else "SELL"
            print(f"[DEBUG] Order executed: {direction} {symbol}, Trade ID: {trade_id}, "
                  f"Price: ${price:.2f}, Size: {size}, Time: {timestamp}")
            self.last_trade_id_for_position[symbol] = trade_id
            self.active_trade_ids[symbol] = size  # Store executed size
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
            position_size = abs(trade.size) if trade.size != 0 else self.active_trade_ids.get(symbol, 0)
            print(f"[DEBUG] Trade closed: symbol={symbol}, direction={direction}, trade_id={trade_id}, "
                  f"entry_price={entry_price:.2f}, pnl={pnl:.2f}, position_size={position_size}, raw_trade_size={trade.size}")
            if position_size == 0:
                print(f"[ERROR] Zero position size for {symbol}, Trade ID: {trade_id}")
                return
            self.last_trade_id_for_position[symbol] = None
            self.active_trade_ids[symbol] = None
            # Do NOT reset self.signal_sent here to prevent duplicates

    def combine_signals(self, signals):
        """Combine signals: require all strategies to produce the same non-None signal."""
        if not signals or None in signals:
            return None
        unique_signals = set(signals)
        if len(unique_signals) == 1:
            return unique_signals.pop()
        return None

    def get_timeframe_seconds(self, timeframe):
        """Convert TimeFrame to seconds."""
        amount = timeframe.amount
        unit = timeframe.unit
        if unit == TimeFrameUnit.Minute:
            return amount * 60
        elif unit == TimeFrameUnit.Hour:
            return amount * 3600
        elif unit == TimeFrameUnit.Day:
            return amount * 86400
        else:
            raise ValueError(f"Unsupported timeframe unit: {unit}")

    def clean_signal_history(self, symbol, max_age):
        """Remove signal history entries older than max_age seconds."""
        if symbol not in self.signal_history:
            return
        current_dt = datetime.now(timezone.utc)
        to_remove = []
        for signal, timestamp, price in self.signal_history[symbol]:
            signal_dt = parse_formatted_time(timestamp)
            time_diff = (current_dt - signal_dt).total_seconds()
            if time_diff > max_age:
                to_remove.append((signal, timestamp, price))
        for signal_key in to_remove:
            self.signal_history[symbol].discard(signal_key)
        self.save_signal_history()

    def process_signal(self, data, signal, trade_size):
        """Process a trading signal: execute trade, send alert, update tracking."""
        symbol = data._name
        cash_available = self.broker.getcash()
        current_price = data.close[0]
        trade_size = round((cash_available * TRADE_SIZE_PERCENT) / current_price, 8) if current_price != 0 else 0
        current_timestamp = format_time(datetime.now(timezone.utc))
        bar_timestamp = format_time(data.datetime)
        
        # Check if signal is recent (within TIMEFRAME * 4)
        current_dt = datetime.now(timezone.utc)
        timeframe_seconds = self.get_timeframe_seconds(TIMEFRAME)
        max_age = timeframe_seconds * 4  # TIMEFRAME * 4
        signal_dt = bt.num2date(data.datetime[0]).astimezone(timezone.utc)
        time_diff = (current_dt - signal_dt).total_seconds()
        
        print(f"[DEBUG] Processing signal for {symbol}: {signal}, bar_time={bar_timestamp}, current_time={current_timestamp}, price={current_price:.2f}, age={time_diff:.0f}s, max_age={max_age}s")
        
        if time_diff > max_age:
            print(f"[DEBUG] Skipping old signal for {symbol}: {signal} at {bar_timestamp} (age: {time_diff:.0f}s, max: {max_age}s)")
            return

        # Clean old signal history
        self.clean_signal_history(symbol, max_age)

        # Check for duplicate signals
        if symbol not in self.signal_history:
            self.signal_history[symbol] = set()
        signal_key = (signal, bar_timestamp, current_price)  # Include price in signal key
        if signal_key in self.signal_history[symbol]:
            print(f"[DEBUG] Skipping duplicate signal for {symbol}: {signal} at {bar_timestamp}, price={current_price:.2f}")
            return

        if signal == "LONG":
            print(f"[DEBUG] Placing BUY order for {symbol}, size={trade_size}, cash={self.broker.getcash():.2f}")
            self.trade_id_counter[symbol] += 1
            self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
            self.buy(data=data, size=trade_size)
            self.last_direction[symbol] = "LONG"
            self.signal_sent[symbol] = "LONG"
            self.last_executed_signal[symbol] = "LONG"
        elif signal == "SHORT" and ENABLE_SHORTS or (signal == "SHORT" and self.last_direction[symbol] == "LONG"):
            print(f"[DEBUG] Placing SELL order for {symbol}, size={trade_size}, cash={self.broker.getcash():.2f}")
            self.trade_id_counter[symbol] += 1
            self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
            self.sell(data=data, size=trade_size)
            self.last_direction[symbol] = "SHORT"
            self.signal_sent[symbol] = "SHORT"
            self.last_executed_signal[symbol] = "SHORT"

        # Record signal in history
        self.signal_history[symbol].add(signal_key)
        self.save_signal_history()

        # Send Telegram alert
        if SEND_TELLY_MSG:
            estimated_price = data.close[0]
            description = self.get_sentiment_description(symbol, signal)
            msg = self.create_entry_signal_message(
                symbol, signal, estimated_price, current_timestamp,
                self.ema_fast[data][0], self.ema_slow[data][0],
                self.rsi[data][0], description
            )
            send_sync(msg)
            self.last_signal_price[symbol] = estimated_price
            self.pending_direction[symbol] = None

    def next(self):
        for d in self.datas:
            try:
                symbol = d._name
                position = self.getposition(d).size

                # Check if enough bars are available
                if len(d) < 50:  # Minimum for SMA50
                    print(f"[DEBUG] Skipping {symbol}: insufficient bars ({len(d)} < 50)")
                    continue

                # Check if bar is recent
                current_dt = datetime.now(timezone.utc)
                bar_dt = bt.num2date(d.datetime[0]).astimezone(timezone.utc)
                time_diff = (current_dt - bar_dt).total_seconds()
                max_age = self.get_timeframe_seconds(TIMEFRAME) * 20  # TIMEFRAME * 4
                # if time_diff > max_age:
                #     print(f"[DEBUG] Skipping old bar for {symbol}: {format_time(d.datetime)} (age: {time_diff:.0f}s, max: {max_age}s)")
                #     continue

                # Generate signals from all strategies
                signals = [strategy.generate_signal(d) for strategy in self.strategies]
                current_signal = self.combine_signals(signals)

                # Exit if open position is opposite of new signal
                if position > 0 and current_signal == "SHORT" and self.strategies[0].generate_signal(d) == "SHORT":
                    print(f"[DEBUG] Closing LONG position for {symbol}")
                    self.close(data=d)
                    self.pending_direction[symbol] = "SHORT"
                    continue
                elif position < 0 and current_signal == "LONG" and self.strategies[0].generate_signal(d) == "LONG":
                    print(f"[DEBUG] Closing SHORT position for {symbol}")
                    self.close(data=d)
                    self.pending_direction[symbol] = "LONG"
                    continue

                # Entry logic only if flat
                if position == 0:
                    signal = self.pending_direction.get(symbol) or current_signal
                    trade_size = round((self.broker.getcash() * TRADE_SIZE_PERCENT) / d.close[0], 8) if d.close[0] != 0 else 0
                    last_signal = self.last_executed_signal.get(symbol)

                    # Only process new, unique signals, and skip SHORT if disabled
                    if signal and signal != last_signal and self.signal_sent.get(symbol) != signal:
                        self.process_signal(d, signal, trade_size)

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
        # Filter bars to only include those within INDICATOR_LOOKBACK_MINUTES
        current_time = pd.Timestamp.now(timezone.utc)
        min_time = current_time - pd.Timedelta(minutes=INDICATOR_LOOKBACK_MINUTES)
        bars = bars[(bars.index <= current_time) & (bars.index >= min_time)]
        bars = bars[['open', 'high', 'low', 'close', 'volume']]
        bars.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        print(f"[DEBUG] Filtered to {len(bars)} bars within {INDICATOR_LOOKBACK_MINUTES} minutes")
        return bars
    except Exception as e:
        print(f"[ERROR] Failed to fetch data for {symbol}: {e}")
        return pd.DataFrame()

def get_timeframe_seconds(timeframe):
    """Convert TimeFrame to seconds."""
    amount = timeframe.amount
    unit = timeframe.unit
    if unit == TimeFrameUnit.Minute:
        return amount * 60
    elif unit == TimeFrameUnit.Hour:
        return amount * 3600
    elif unit == TimeFrameUnit.Day:
        return amount * 86400
    else:
        raise ValueError(f"Unsupported timeframe unit: {unit}")

def get_timeframe_string(timeframe):
    """Convert TimeFrame to human-readable string."""
    amount = timeframe.amount
    unit = timeframe.unit
    if unit == TimeFrameUnit.Minute:
        return f"{amount} minute{'s' if amount != 1 else ''}"
    elif unit == TimeFrameUnit.Hour:
        return f"{amount} hour{'s' if amount != 1 else ''}"
    elif unit == TimeFrameUnit.Day:
        return f"{amount} day{'s' if amount != 1 else ''}"
    else:
        return str(timeframe)

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
        end_dt = datetime.now(timezone.utc).replace(microsecond=0)
        start_dt = end_dt - timedelta(minutes=INDICATOR_LOOKBACK_MINUTES)
        start = start_dt.isoformat().replace('+00:00', 'Z')
        end = end_dt.isoformat().replace('+00:00', 'Z')
    else:
        start = BACKTEST_START
        end = BACKTEST_END

    for symbol in TICKERS:
        print(f"[INFO] Fetching data for {symbol} ({ASSET_TYPE})...")
        df = get_data(symbol, start, end)
        if df.empty or len(df) < 50:  # Minimum for SMA50
            print(f"[WARNING] Skipping {symbol} (insufficient data: {len(df)} bars)")
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
        wait_seconds = get_timeframe_seconds(TIMEFRAME)
        wait_string = get_timeframe_string(TIMEFRAME)
        while True:
            if ENABLE_EXCEPTION_HANDLING:
                try:
                    run()
                    print(f"Waiting {wait_string}...")
                    time.sleep(wait_seconds)
                except Exception as e:
                    print(f"Error: {e}. Retrying in 60 sec.")
                    time.sleep(60)
            else:
                run()  # Run without exception handling; will crash on error
                print(f"Waiting {wait_string}...")
                time.sleep(wait_seconds)
    else:
        run()