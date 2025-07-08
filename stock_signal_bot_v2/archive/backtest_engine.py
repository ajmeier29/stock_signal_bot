import os
import pandas as pd
import backtrader as bt
from datetime import datetime
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit

# 👇 Import the strategy
from strategies.sma_crossover import SmaCrossover
from strategies.fvg_strategy import FvgProLongAndShort
from strategies.fvg_strategy_LongOnly import FvgProLongOnly
from strategies.MomentumBreakoutStrategy import MomentumBreakout
from strategies.aggressiveStrat import SimpleGuaranteedTrade

stratUsing = SimpleGuaranteedTrade

# ====== CONFIG ======
TICKERS = ['AAPL', 'MSFT', 'AMZN', 'GOOG']
#TICKERS = ['NVDA']
STARTING_CASH = 10000

# ------ 1D Timeframe backtest
# TIMEFRAME = TimeFrame.Day # 1D timeframe
# START_DATE = "2025-01-01"
# END_DATE = "2025-04-21"
# ------ 1D Timeframe backtest

# ------ 5 Min Timeframe backtest
TIMEFRAME = TimeFrame(5, TimeFrameUnit.Minute) # 5min timeframe
START_DATE = "2024-01-01"
END_DATE = "2025-04-21"
# ------ 5 Min Timeframe backtest


# ----- THIS MAKES A DECENT PERCENT USING FvgProLongAndShort

# TIMEFRAME = TimeFrame(1, TimeFrameUnit.Hour) # 5min timeframe
# START_DATE = "2025-01-01"
# END_DATE = "2025-04-21"

# ----------

# ====== CONFIG ======


# ====== Alpaca Setup ======
API_KEY = os.getenv('ALPACA_API_KEY')
API_SECRET = os.getenv('ALPACA_SECRET_KEY')
BASE_URL = 'https://paper-api.alpaca.markets'
alpaca = REST(API_KEY, API_SECRET, BASE_URL)

# ====== Backtrader Feed Wrapper ======
class PandasData(bt.feeds.PandasData):
    params = (
        ('datetime', None),
        ('open', 'Open'),
        ('high', 'High'),
        ('low', 'Low'),
        ('close', 'Close'),
        ('volume', 'Volume'),
        ('openinterest', -1),
    )

# ====== Backtest Runner ======
def run_backtest(strategy_class):
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(STARTING_CASH)

    for symbol in TICKERS:
        try:
            print(f"Loading {symbol}...")
            bars = alpaca.get_bars(
                symbol,
                TIMEFRAME,
                start=START_DATE,
                end=END_DATE
            ).df

            if len(bars) < 50:
                print(f"Skipping {symbol} (not enough data)")
                continue

            bars.index = pd.to_datetime(bars.index)
            df = bars[['open', 'high', 'low', 'close', 'volume']].copy()
            df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            data = PandasData(dataname=df)
            cerebro.adddata(data, name=symbol)

        except Exception as e:
            if "SIP data" in str(e):
                print(f"{symbol} requires SIP access — skipping.")
            else:
                print(f"Error loading {symbol}: {e}")

    if not cerebro.datas:
        print("❌ No data loaded.")
        return

    cerebro.addstrategy(strategy_class)

    print('\n📊 Starting Portfolio Value: %.2f' % cerebro.broker.getvalue())
    cerebro.run()
    print('📈 Final Portfolio Value: %.2f' % cerebro.broker.getvalue())
    cerebro.plot()

# ====== Run It ======
if __name__ == '__main__':
    run_backtest(stratUsing)
