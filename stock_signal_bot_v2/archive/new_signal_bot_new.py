import os
import json
import backtrader as bt
from telegram import Bot
from datetime import datetime
import asyncio
import pytz

# === CONFIGURATION ===
MODE = "backtest"         # Options: "live", "backtest", "mock"
ASSET_TYPE = "crypto"     # Options: "stocks", "crypto"
STARTING_CASH = 10000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TICKER_PATH = os.path.join(BASE_DIR, "tickers.json")


# Load tickers from external file
with open(TICKER_PATH) as f:
    TICKERS = json.load(f).get(ASSET_TYPE, [])

# Telegram setup
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)

async def send_telegram_message(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        await telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
    else:
        print("[MOCK] Would send Telegram message:")
        print(message)

# === Modular Message Builder ===
def build_signal_message(ticker, price, strategy_name, direction, rsi=None, momentum=None, volume_spike=None):
    now = datetime.now(pytz.timezone("US/Eastern")).strftime('%Y-%m-%d %I:%M %p (EST)')
    chart_emoji = "📈" if direction == "BUY" else "📉"

    msg = (
        f"{'🟢' if direction == 'BUY' else '🔴'} *[{direction} SIGNAL]* {chart_emoji}\n\n"
        f"*Ticker:* `{ticker}`\n"
        f"*Current Price:* `${price:.2f}`\n"
        f"*Strategy:* _{strategy_name}_\n"
        f"*Timestamp:* {now}\n\n"
    )

    if rsi is not None:
        msg += f"*RSI:* `{rsi:.2f}` {'(Oversold)' if rsi < 30 else '(Overbought)' if rsi > 70 else ''}\n"
    if momentum is not None:
        msg += f"*Momentum:* `{momentum:+.2f}%` over past hour\n"
    if volume_spike is not None:
        msg += f"*Volume Deviation:* `{volume_spike:+.1f}%` from 5-bar avg\n"

    msg += (
        f"\n🔎 *Analysis:* This signal is generated based on confluence of key indicators suggesting "
        f"a high-probability {'entry' if direction == 'BUY' else 'exit'} point.\n"
        f"📈 Stay sharp. Timing and execution matter.\n"
        f"\n⚠️ *Disclaimer:* This is not financial advice. You are solely responsible for your trades. "
        f"Always do your own research before making investment decisions."
    )

    return msg

# === Main Entry Point ===
def run():
    if MODE == "mock":
        print("=== MOCK MODE ===")

        signals = [
            build_signal_message("AAPL", 199.42, "EMA(9/21) + RSI(14)", "BUY", rsi=28.3, momentum=3.72, volume_spike=21.4),
            build_signal_message("TSLA", 745.80, "RSI(14) Overbought Rejection", "SELL", rsi=78.6, momentum=5.10, volume_spike=-12.3),
            build_signal_message("NVDA", 684.10, "Momentum + Volume Spike", "BUY", rsi=62.5, momentum=6.89, volume_spike=35.7)
        ]

        async def send_all():
            for msg in signals:
                await send_telegram_message(msg)

        asyncio.run(send_all())
        return

    # Placeholder for live/backtest logic
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(STARTING_CASH)
    print(f"[{MODE.upper()}] Mode active — strategy execution not yet implemented.")

if __name__ == '__main__':
    run()
