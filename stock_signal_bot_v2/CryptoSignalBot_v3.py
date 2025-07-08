import os
import json
import pandas as pd
import backtrader as bt
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
from alpaca_trade_api.stream import Stream
from datetime import datetime, timedelta, timezone
from telegram import Bot
import asyncio
import pytz
import requests
from strategies.EMACrossoverStrategy import EMACrossoverStrategy
from strategies.RSIDivergenceStrategy import RSIDivergenceStrategy
from strategies.RSIConfirmationStrategy import RSIConfirmationStrategy
from strategies.JoeStrategy_V1 import JoeStrategy_V1

ENABLE_SHORTS = True
SEND_TELLY_MSG = True
LIVE_MODE = True
LIVE_MODE_DAYS_BACK = 30
ASSET_TYPE = "crypto"
TRADE_SIZE_PERCENT = 0.3
STARTING_CASH = 5000
BACKTEST_START = '2021-01-01'
BACKTEST_END = '2025-04-24'
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
GROK_API_KEY = os.getenv('GROK_API_KEY')

# Validate environment variables
if not all([API_KEY, API_SECRET]):
    print("[ERROR] Missing ALPACA_API_KEY or ALPACA_SECRET_KEY")
    exit(1)
if SEND_TELLY_MSG and not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    print("[WARNING] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID; Telegram alerts disabled")
    SEND_TELLY_MSG = False
if not GROK_API_KEY:
    print("[WARNING] Missing GROK_API_KEY; sentiment analysis disabled")

message_semaphore = asyncio.Semaphore(1)

async def send_telegram_message(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        async with message_semaphore:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
            await asyncio.sleep(1.1)
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

class WebSocketData(bt.DataBase):
    params = (
        ('symbol', ''),
        ('timeframe', TIMEFRAME),
    )

    def __init__(self):
        super().__init__()
        self.latest_bar = None
        self.bar_buffer = []

    def start(self):
        super().start()
        self._last_bar_time = None

    def _load(self):
        if not self.bar_buffer:
            return False
        bar = self.bar_buffer.pop(0)
        self.lines.datetime[0] = bt.date2num(bar['timestamp'])
        self.lines.open[0] = bar['open']
        self.lines.high[0] = bar['high']
        self.lines.low[0] = bar['low']
        self.lines.close[0] = bar['close']
        self.lines.volume[0] = bar['volume']
        self._last_bar_time = bar['timestamp']
        return True

    def add_bar(self, bar):
        self.bar_buffer.append(bar)

class CombinedStrategy(bt.Strategy):
    params = dict(fast_ema=9, slow_ema=21, rsi_period=14, rsi_upper=70, rsi_lower=30)

    def __init__(self):
        self.last_direction = {}
        self.pending_direction = {}
        self.trade_id_counter = {d._name: 0 for d in self.datas}
        self.active_trade_ids = {}
        self.last_trade_id_for_position = {}
        self.ema_fast = {d: bt.ind.EMA(d.close, period=self.p.fast_ema) for d in self.datas}
        self.ema_slow = {d: bt.ind.EMA(d.close, period=self.p.slow_ema) for d in self.datas}
        self.rsi = {d: bt.ind.RSI(d.close, period=self.p.rsi_period) for d in self.datas}
        self.dma_50 = {d: bt.ind.SMA(d.close, period=50) for d in self.datas}
        self.signal_sent = {}
        self.last_signal_price = {}
        self.last_executed_signal = {}
        self.last_alert_time = {d._name: None for d in self.datas}
        self.strategies = [
            EMACrossoverStrategy(self.ema_fast, self.ema_slow),
            RSIConfirmationStrategy(self.rsi),
        ]

    def get_sentiment_description(self, ticker, direction):
        if not GROK_API_KEY:
            return "Sentiment analysis disabled (missing GROK_API_KEY)"
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
        else:
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
            size = abs(order.executed.size)
            timestamp = format_time(bt.num2date(order.executed.dt))
            direction = "BUY" if order.isbuy() else "SELL"
            print(f"[DEBUG] Order executed: {direction} {symbol}, Trade ID: {trade_id}, "
                  f"Price: ${price:.2f}, Size: {size}, Time: {timestamp}")
            self.last_trade_id_for_position[symbol] = trade_id
            self.active_trade_ids[symbol] = size
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

    def combine_signals(self, signals):
        if not signals or None in signals:
            return None
        unique_signals = set(signals)
        if len(unique_signals) == 1:
            return unique_signals.pop()
        return None

    def process_signal(self, data, signal, trade_size):
        symbol = data._name
        cash_available = self.broker.getcash()
        current_price = data.close[0]
        trade_size = round((cash_available * TRADE_SIZE_PERCENT) / current_price, 8) if current_price != 0 else 0
        if trade_size < 0.00000001:
            print(f"[DEBUG] {symbol}: Trade size too small ({trade_size})")
            return
        current_time = bt.num2date(data.datetime[0])
        last_alert = self.last_alert_time.get(symbol)
        if last_alert and (current_time - last_alert).total_seconds() < 900:
            print(f"[DEBUG] {symbol}: Skipping signal - too soon since last alert")
            return
        if signal == "LONG":
            print(f"[DEBUG] Placing BUY order for {symbol}, size={trade_size}, cash={cash_available:.2f}")
            self.trade_id_counter[symbol] += 1
            self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
            self.buy(data=data, size=trade_size)
            self.last_direction[symbol] = "LONG"
            self.signal_sent[symbol] = "LONG"
            self.last_executed_signal[symbol] = "LONG"
        elif signal == "SHORT" and ENABLE_SHORTS:
            print(f"[DEBUG] Placing SELL order for {symbol}, size={trade_size}, cash={cash_available:.2f}")
            self.trade_id_counter[symbol] += 1
            self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
            self.sell(data=data, size=trade_size)
            self.last_direction[symbol] = "SHORT"
            self.signal_sent[symbol] = "SHORT"
            self.last_executed_signal[symbol] = "SHORT"
        else:
            return
        if SEND_TELLY_MSG:
            estimated_price = data.close[0]
            timestamp = format_time(data.datetime)
            description = self.get_sentiment_description(symbol, signal)
            msg = self.create_entry_signal_message(
                symbol, signal, estimated_price, timestamp,
                self.ema_fast[data][0], self.ema_slow[data][0],
                self.rsi[data][0], description
            )
            send_sync(msg)
            self.last_signal_price[symbol] = estimated_price
            self.last_alert_time[symbol] = current_time
        self.pending_direction[symbol] = None

    def next(self):
        for d in self.datas:
            try:
                symbol = d._name
                position = self.getposition(d).size
                signals = [strategy.generate_signal(d) for strategy in self.strategies]
                current_signal = self.combine_signals(signals)
                print(f"[DEBUG] {symbol}: position={position}, current_signal={current_signal}, "
                      f"signals={signals}, pending_direction={self.pending_direction.get(symbol)}")
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
                if position == 0:
                    signal = self.pending_direction.get(symbol) or current_signal
                    trade_size = round((self.broker.getcash() * TRADE_SIZE_PERCENT) / d.close[0], 8) if d.close[0] != 0 else 0
                    last_signal = self.last_executed_signal.get(symbol)
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
        bars = bars[['open', 'high', 'low', 'close', 'volume']]
        bars.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        return bars
    except Exception as e:
        print(f"[ERROR] Failed to fetch data for {symbol}: {e}")
        return pd.DataFrame()

async def handle_bar(symbol, cerebro, data_feeds):
    async def on_bar(bar):
        bar_data = {
            'timestamp': pd.to_datetime(bar.timestamp),
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume
        }
        data_feeds[symbol].add_bar(bar_data)
        print(f"[DEBUG] Received bar for {symbol}: close={bar.close}, time={bar.timestamp}")
        cerebro.run(runonce=False)
    return on_bar

async def run_websocket(cerebro, data_feeds):
    try:
        print("[DEBUG] Initializing WebSocket stream")
        stream = Stream(API_KEY, API_SECRET, base_url='wss://stream.data.alpaca.markets/v2/crypto')
        stream.on_error = lambda error: print(f"[ERROR] WebSocket stream error: {error}")
        async def authenticate():
            await stream._send({"action": "auth", "key": API_KEY, "secret": API_SECRET})
            print("[DEBUG] Sent WebSocket authentication")
        stream.on_connect = authenticate
        for symbol in TICKERS:
            async def handle_symbol_bar(bar, sym=symbol):
                bar_data = {
                    'timestamp': pd.to_datetime(bar.timestamp),
                    'open': bar.open,
                    'high': bar.high,
                    'low': bar.low,
                    'close': bar.close,
                    'volume': bar.volume
                }
                data_feeds[sym].add_bar(bar_data)
                print(f"[DEBUG] Received bar for {sym}: close={bar.close}, time={bar.timestamp}")
                cerebro.run(runonce=False)
            try:
                if ASSET_TYPE == "crypto":
                    print(f"[DEBUG] Subscribing to crypto bars for {symbol}")
                    stream.subscribe_crypto_bars(handle_symbol_bar, symbol)
                else:
                    print(f"[DEBUG] Subscribing to stock bars for {symbol}")
                    stream.subscribe_bars(handle_symbol_bar, symbol)
            except Exception as e:
                print(f"[ERROR] Failed to subscribe to {symbol}: {e}")
        attempt = 1
        max_attempts = 5
        while attempt <= max_attempts:
            try:
                print(f"[DEBUG] Starting WebSocket connection (attempt {attempt}/{max_attempts})")
                await stream._run_forever()
                break
            except Exception as e:
                print(f"[ERROR] WebSocket error: {e}, retrying in {2 ** attempt} seconds...")
                await asyncio.sleep(2 ** attempt)
                attempt += 1
        if attempt > max_attempts:
            print(f"[ERROR] Failed to connect to WebSocket after {max_attempts} attempts")
            print("[INFO] Falling back to REST polling...")
            await run_rest_polling(cerebro, data_feeds)
    except Exception as e:
        print(f"[ERROR] WebSocket initialization failed: {e}")
        print("[INFO] Falling back to REST polling...")
        await run_rest_polling(cerebro, data_feeds)

async def run_rest_polling(cerebro, data_feeds):
    print("[DEBUG] Starting REST polling")
    while True:
        try:
            end_dt = datetime.now(timezone.utc)
            start_dt = end_dt - timedelta(minutes=15)
            start = start_dt.isoformat().replace('+00:00', 'Z')
            end = end_dt.isoformat().replace('+00:00', 'Z')
            for symbol in TICKERS:
                df = get_data(symbol, start, end)
                if not df.empty:
                    latest_bar = df.iloc[-1]
                    bar_data = {
                        'timestamp': df.index[-1],
                        'open': latest_bar['Open'],
                        'high': latest_bar['High'],
                        'low': latest_bar['Low'],
                        'close': latest_bar['Close'],
                        'volume': latest_bar['Volume']
                    }
                    data_feeds[symbol].add_bar(bar_data)
                    print(f"[DEBUG] Polled bar for {symbol}: close={bar_data['close']}, time={bar_data['timestamp']}")
                    cerebro.run(runonce=False)
            await asyncio.sleep(300)  # Poll every 5 minutes
        except Exception as e:
            print(f"[ERROR] REST polling error: {e}, retrying in 60 seconds...")
            await asyncio.sleep(60)

def run():
    try:
        print("[DEBUG] Starting script execution")
        cerebro = bt.Cerebro()
        cerebro.broker.setcash(STARTING_CASH)
        cerebro.broker.setcommission(commission=0.001)
        print(f"[DEBUG] Initial cash: {cerebro.broker.getcash():.2f}")

        try:
            account = alpaca.get_account()
            print(f"[DEBUG] Alpaca account: cash={account.cash}, buying_power={account.buying_power}")
        except Exception as e:
            print(f"[ERROR] Alpaca API connection failed: {e}")

        cerebro.addstrategy(CombinedStrategy)
        data_feeds = {}

        if LIVE_MODE:
            start_dt = datetime.now(timezone.utc) - timedelta(days=LIVE_MODE_DAYS_BACK)
            end_dt = datetime.now(timezone.utc)
            start = start_dt.isoformat().replace('+00:00', 'Z')
            end = end_dt.isoformat().replace('+00:00', 'Z')
            for symbol in TICKERS:
                print(f"[INFO] Fetching initial data for {symbol} ({ASSET_TYPE})...")
                df = get_data(symbol, start, end)
                if df.empty or len(df) < 50:
                    print(f"[WARNING] Skipping {symbol} (insufficient data)")
                    continue
                data = PandasData(dataname=df)
                cerebro.adddata(data, name=symbol)
                websocket_data = WebSocketData(symbol=symbol)
                data_feeds[symbol] = websocket_data
                cerebro.adddata(websocket_data, name=symbol + "_live")
            print(f"[DEBUG] Starting Value: {cerebro.broker.getvalue():.2f}")
            asyncio.run(run_websocket(cerebro, data_feeds))
        else:
            for symbol in TICKERS:
                print(f"[INFO] Fetching data for {symbol} ({ASSET_TYPE})...")
                df = get_data(symbol, BACKTEST_START, BACKTEST_END)
                if df.empty or len(df) < 50:
                    print(f"[WARNING] Skipping {symbol} (insufficient data)")
                    continue
                data = PandasData(dataname=df)
                cerebro.adddata(data, name=symbol)
            print(f"[DEBUG] Starting Value: {cerebro.broker.getvalue():.2f}")
            cerebro.run()
            print(f"[DEBUG] Final Value: {cerebro.broker.getvalue():.2f}")
            cerebro.plot()
    except Exception as e:
        print(f"[ERROR] Script execution failed: {type(e).__name__}: {e}")
        raise

if __name__ == '__main__':
    print("[DEBUG] Script launched")
    run()