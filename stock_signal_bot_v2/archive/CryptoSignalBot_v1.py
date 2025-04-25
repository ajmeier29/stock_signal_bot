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
        self.last_direction = {}
        self.pending_direction = {}  # symbol → "LONG" or "SHORT"
        self.trade_id_counter = {d._name: 0 for d in self.datas}
        self.active_trade_ids = {}  # what will be used for next entry
        self.last_trade_id_for_position = {}  # the ID for the *currently active* position
        self.ema_fast = {d: bt.ind.EMA(d.close, period=self.p.fast_ema) for d in self.datas}
        self.ema_slow = {d: bt.ind.EMA(d.close, period=self.p.slow_ema) for d in self.datas}
        self.rsi = {d: bt.ind.RSI(d.close, period=self.p.rsi_period) for d in self.datas}
        self.signal_sent = {}  # {symbol: signal_type}

    def notify_order(self, order):
        if order.status == order.Completed:
            symbol = order.data._name
            price = order.executed.price
            timestamp = format_time(bt.num2date(order.executed.dt))
            trade_id = self.active_trade_ids.get(symbol, '?')

            # Check if it's an ENTRY order
            if order.isbuy():
                self.last_trade_id_for_position[symbol] = trade_id
                # send_sync(
                #     f"🎯 *ORDER EXECUTED {direction}* `{symbol}` | Trade ID: `{trade_id}`\n"
                #     f"• Entry Price: *${price:.2f}*\n"
                #     f"• Time: _{timestamp}_"
                # )

            elif order.issell():
                self.last_trade_id_for_position[symbol] = trade_id
                # send_sync(
                #     f"🎯 *ORDER EXECUTED {direction}* `{symbol}` | Trade ID: `{trade_id}`\n"
                #     f"• Entry Price: *${price:.2f}*\n"
                #     f"• Time: _{timestamp}_"
                # )


    # def notify_trade(self, trade):
    #     if trade.isclosed:
    #         symbol = trade.data._name
    #         trade_id = self.last_trade_id_for_position.get(symbol, '?')
    #         direction = self.last_direction.get(symbol, '?')

    #         pnl = trade.pnl
    #         entry_price = trade.price
    #         exit_price = entry_price + pnl if direction == "LONG" else entry_price - pnl
    #         pnl_percent = (pnl / entry_price) * 100 if entry_price != 0 else 0

    #         entry_time = format_time(bt.num2date(trade.dtopen))
    #         exit_time = format_time(bt.num2date(trade.dtclose))

    #         msg = (
    #             f"✅ *EXIT {direction}* `{symbol}` | Trade ID: `{trade_id}`\n"
    #             f"• Entry: _{entry_time}_ — *${entry_price:.2f}*\n"
    #             f"• Exit:  _{exit_time}_ — *${exit_price:.2f}*\n"
    #             f"• Profit: *${pnl:.2f}* (*{pnl_percent:.2f}%*)"
    #         )
    #         send_sync(msg)

    #         # Reset tracking
    #         self.last_trade_id_for_position[symbol] = None
    #         self.active_trade_ids[symbol] = None
    #         self.signal_sent[symbol] = None

    def notify_trade(self, trade):
        if trade.isclosed:
            symbol = trade.data._name
            trade_id = self.last_trade_id_for_position.get(symbol, '?')
            direction = self.last_direction.get(symbol, '?')

            # Get trade details
            pnl = trade.pnl  # Total profit/loss
            entry_price = trade.price  # Average entry price
            position_size = abs(trade.size)  # Absolute position size

            # Calculate exit price
            if position_size > 0:  # Avoid division by zero
                if direction == "LONG":
                    exit_price = entry_price + (pnl / position_size)  # LONG: exit = entry + (pnl / size)
                else:  # SHORT
                    exit_price = entry_price - (pnl / position_size)  # SHORT: exit = entry - (pnl / size)
            else:
                exit_price = entry_price  # Fallback
                print(f"[WARNING] Zero position size for {symbol}, Trade ID: {trade_id}")

            # Calculate percentage return
            total_entry_value = entry_price * position_size
            pnl_percent = (pnl / total_entry_value) * 100 if total_entry_value != 0 else 0

            # Format times
            entry_time = format_time(bt.num2date(trade.dtopen))
            exit_time = format_time(bt.num2date(trade.dtclose))

            # Create message
            msg = (
                f"✅ *EXIT {direction}* `{symbol}` | Trade ID: `{trade_id}`\n"
                f"• Entry: _{entry_time}_ — *${entry_price:.2f}*\n"
                f"• Exit:  _{exit_time}_ — *${exit_price:.2f}*\n"
                f"• Profit: *${pnl:.2f}* (*{pnl_percent:.2f}%*)"
            )

            # Debug and send
            print(f"[DEBUG] Trade closed: {msg}")
            try:
                send_sync(msg)
            except Exception as e:
                print(f"[ERROR] Failed to send Telegram message for {symbol}, Trade ID: {trade_id}: {e}")

            # Reset tracking
            self.last_trade_id_for_position[symbol] = None
            self.active_trade_ids[symbol] = None
            self.signal_sent[symbol] = None

    def next(self):
        for d in self.datas:
            try:
                symbol = d._name
                ema_fast = self.ema_fast[d][0]
                ema_slow = self.ema_slow[d][0]
                position = self.getposition(d).size

                # === Determine signal intent from EMA crossover ===
                current_signal = None
                if ema_fast > ema_slow:
                    current_signal = "LONG"
                elif ema_fast < ema_slow:
                    current_signal = "SHORT"

                # === Exit if open position is opposite of new signal ===
                if position > 0 and current_signal == "SHORT":
                    self.close(data=d)
                    self.signal_sent[symbol] = None
                    self.pending_direction[symbol] = "SHORT"
                    continue

                elif position < 0 and current_signal == "LONG":
                    self.close(data=d)
                    self.signal_sent[symbol] = None
                    self.pending_direction[symbol] = "LONG"
                    continue

                # === Entry logic only if flat ===
                if position == 0:
                    signal = self.pending_direction.get(symbol) or current_signal

                    if signal == "LONG" and self.signal_sent.get(symbol) != "LONG":
                        self.last_direction[symbol] = "LONG"
                        self.trade_id_counter[symbol] += 1
                        self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
                        self.buy(data=d)
                        self.signal_sent[symbol] = "LONG"
                        self.pending_direction[symbol] = None

                        estimated_price = d.close[0]
                        send_sync(
                            f"🚀 *BUY SIGNAL TRIGGERED!* `{symbol}` | Trade ID: `{self.active_trade_ids[symbol]}`\n"
                            f"• Estimated Entry: *${estimated_price:.2f}*\n"
                            f"• Time: _{format_time(d.datetime)}_"
                        )

                    elif signal == "SHORT" and self.signal_sent.get(symbol) != "SHORT":
                        self.last_direction[symbol] = "SHORT"
                        self.trade_id_counter[symbol] += 1
                        self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
                        self.sell(data=d)
                        self.signal_sent[symbol] = "SHORT"
                        self.pending_direction[symbol] = None

                        estimated_price = d.close[0]
                        send_sync(
                            f"📉 *SHORT SIGNAL TRIGGERED!* `{symbol}` | Trade ID: `{self.active_trade_ids[symbol]}`\n"
                            f"• Estimated Entry: *${estimated_price:.2f}*\n"
                            f"• Time: _{format_time(d.datetime)}_"
                        )

            except Exception as e:
                print(f"[ERROR] {symbol} — {type(e).__name__}: {e}")





    # def next(self):
    #     for d in self.datas:
    #         symbol = d._name
    #         ema_fast = self.ema_fast[d][0]
    #         ema_slow = self.ema_slow[d][0]
    #         price = d.close[0]
    #         timestamp = d.datetime.datetime(0)
    #         trade_id = self.active_trade_ids.get(symbol, '?')

    #         # === EXIT Logic First ===
    #         if self.signal_sent.get(symbol) == 'LONG' and ema_fast < ema_slow:
    #             self.close(data=d)
    #             self.signal_sent[symbol] = None
    #             send_sync(f"✅ *EXIT LONG* `{symbol}` | Trade ID: `{trade_id}` — _{format_time(d.datetime)}_")

    #         elif self.signal_sent.get(symbol) == 'SHORT' and ema_fast > ema_slow:
    #             self.close(data=d)
    #             self.signal_sent[symbol] = None
    #             send_sync(f"✅ *EXIT SHORT* `{symbol}` | Trade ID: `{trade_id}` — _{format_time(d.datetime)}_")

    #         # === ENTRY Logic ===
    #         if ema_fast > ema_slow and self.signal_sent.get(symbol) != 'LONG':
    #             self.trade_id_counter[symbol] += 1
    #             self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
    #             self.buy(data=d)
    #             self.signal_sent[symbol] = 'LONG'
    #             send_sync(f"🚀 *BUY SIGNAL TRIGGERED!* `{symbol}` | Trade ID: `{self.active_trade_ids[symbol]}` — _{format_time(d.datetime)}_")

    #         elif ema_fast < ema_slow and self.signal_sent.get(symbol) != 'SHORT':
    #             self.trade_id_counter[symbol] += 1
    #             self.active_trade_ids[symbol] = self.trade_id_counter[symbol]
    #             self.sell(data=d)
    #             self.signal_sent[symbol] = 'SHORT'
    #             send_sync(f"📉 *SHORT SIGNAL TRIGGERED!* `{symbol}` | Trade ID: `{self.active_trade_ids[symbol]}` — _{format_time(d.datetime)}_")

    # def next(self):
    #     for d in self.datas:
    #         symbol = d._name
    #         ema_fast = self.ema_fast[d][0]
    #         ema_slow = self.ema_slow[d][0]
    #         price = d.close[0]
    #         timestamp = d.datetime.datetime(0)

    #         # ✅ Exit logic first
    #         if self.signal_sent.get(symbol) == 'LONG' and ema_fast < ema_slow:
    #             self.close(data=d)
    #             self.signal_sent[symbol] = None
    #             send_sync(f"✅ *EXIT LONG* Asset: `{symbol}` Time: _{format_time(d.datetime)}_")

    #         elif self.signal_sent.get(symbol) == 'SHORT' and ema_fast > ema_slow:
    #             self.close(data=d)
    #             self.signal_sent[symbol] = None
    #             send_sync(f"✅ *EXIT SHORT* Asset: `{symbol}` Time: _{format_time(d.datetime)}_")

    #         # 🚀 Entry logic after exit logic
    #         if ema_fast > ema_slow and self.signal_sent.get(symbol) != 'LONG':
    #             self.buy(data=d)
    #             self.signal_sent[symbol] = 'LONG'
    #             send_sync(f"🚀 *BUY SIGNAL TRIGGERED!* Asset: `{symbol}` Time: _{format_time(d.datetime)}_")

    #         elif ema_fast < ema_slow and self.signal_sent.get(symbol) != 'SHORT':
    #             self.sell(data=d)
    #             self.signal_sent[symbol] = 'SHORT'
    #             send_sync(f"📉 *SHORT SIGNAL TRIGGERED!* Asset: `{symbol}` Time: _{format_time(d.datetime)}_")


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
            # run()
            # print("Waiting 15 mins...")
            # time.sleep(900)
            try:
                run()
                print("Waiting 15 mins...")
                time.sleep(900)
            except Exception as e:
                print(f"Error: {e}. Retrying in 60 sec.")
                time.sleep(60)
    else:
        run()
