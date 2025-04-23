import backtrader as bt
import pandas as pd
from datetime import datetime
from alpaca_trade_api.rest import REST, TimeFrame
import os

# Alpaca API setup
API_KEY = os.getenv('ALPACA_API_KEY')
API_SECRET = os.getenv('ALPACA_SECRET_KEY')
BASE_URL = 'https://paper-api.alpaca.markets'

alpaca = REST(API_KEY, API_SECRET, BASE_URL)
symbols = ['AAPL', 'MSFT', 'GOOGL']  # Add your list of tickers here

# Backtrader-compatible data feed class
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

# Strategy with separate logic for each data feed
class SmaCross(bt.Strategy):
    def __init__(self):
        self.crossovers = {}
        for d in self.datas:
            sma10 = bt.ind.SMA(d, period=10)
            sma50 = bt.ind.SMA(d, period=50)
            self.crossovers[d] = bt.ind.CrossOver(sma10, sma50)

    def next(self):
        for d in self.datas:
            pos = self.getposition(d)
            crossover = self.crossovers[d]
            if not pos:
                if crossover > 0:
                    self.buy(data=d)
            elif crossover < 0:
                self.sell(data=d)

# Setup Cerebro
cerebro = bt.Cerebro()
cerebro.broker.setcash(10000)

# Add data for each symbol
for symbol in symbols:
    print(f"\nFetching data for {symbol}...")
    bars = alpaca.get_bars(
        symbol,
        TimeFrame.Day,
        start="2025-01-01",
        end="2025-04-21"
    ).df

    if len(bars) < 50:
        print(f"Skipping {symbol} (not enough data)")
        continue

    bars.index = pd.to_datetime(bars.index)
    df = bars[['open', 'high', 'low', 'close', 'volume']].copy()
    df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
    data = PandasData(dataname=df)
    cerebro.adddata(data, name=symbol)

# Add strategy
cerebro.addstrategy(SmaCross)

# Run
print('Starting Portfolio Value: %.2f' % cerebro.broker.getvalue())
cerebro.run()
print('Final Portfolio Value: %.2f' % cerebro.broker.getvalue())
cerebro.plot()
