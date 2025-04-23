import os
import pandas as pd
import backtrader as bt
from datetime import datetime
from alpaca_trade_api.rest import REST, TimeFrame

# Hardcoded Top 100
top_100_tickers = [
    'AAPL', 'MSFT', 'AMZN', 'GOOG', 'NVDA', 'META', 'TSLA', 'AMD', 'NFLX', 'INTC',
    'BA', 'CRM', 'ORCL', 'PYPL', 'CSCO', 'AVGO', 'TXN', 'QCOM', 'ADBE', 'IBM',
    'V', 'MA', 'JPM', 'BAC', 'WFC', 'C', 'GS', 'MS', 'AXP', 'USB',
    'UNH', 'JNJ', 'PFE', 'LLY', 'MRK', 'TMO', 'ABT', 'CVS', 'BMY', 'GILD',
    'HD', 'LOW', 'COST', 'TGT', 'WMT', 'NKE', 'SBUX', 'KO', 'PEP', 'MCD',
    'XOM', 'CVX', 'COP', 'SLB', 'OXY', 'PSX', 'VLO', 'EOG', 'FANG', 'MPC',
    'F', 'GM', 'TSM', 'NOC', 'LMT', 'RTX', 'DE', 'CAT', 'GE', 'HON',
    'DIS', 'CMCSA', 'T', 'VZ', 'CHTR', 'TMUS', 'SPOT', 'SQ', 'UBER', 'LYFT',
    'PLTR', 'SNOW', 'SHOP', 'ROKU', 'DDOG', 'DOCU', 'NET', 'ZS', 'CRWD', 'PANW',
    'ETSY', 'DKNG', 'ABNB', 'MRNA', 'BNTX', 'RIVN', 'LCID', 'UAL', 'DAL', 'AAL'
]
top_100_tickers = [
    'NVDA', 'META', 'TSLA', 'AMD', 'NFLX','SHOP'
]
# Alpaca setup
API_KEY = os.getenv('ALPACA_API_KEY')
API_SECRET = os.getenv('ALPACA_SECRET_KEY')
BASE_URL = 'https://paper-api.alpaca.markets'
alpaca = REST(API_KEY, API_SECRET, BASE_URL)

# Backtrader-compatible Data Feed
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

# SMA Cross Strategy
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
            if not pos and crossover > 0:
                self.buy(data=d)
            elif pos and crossover < 0:
                self.sell(data=d)

# Run Backtest
def run_backtest():
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(10000)

    for symbol in top_100_tickers:
        try:
            print(f"Fetching {symbol}...")
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

        except Exception as e:
            if "SIP data" in str(e):
                print(f"{symbol} requires SIP access — skipping.")
            else:
                print(f"Error loading {symbol}: {e}")

    cerebro.addstrategy(SmaCross)

    if not cerebro.datas:
        print("❌ No data loaded. Check API access or date range.")
        return

    print('\nStarting Portfolio Value: %.2f' % cerebro.broker.getvalue())
    cerebro.run()
    print('Final Portfolio Value: %.2f' % cerebro.broker.getvalue())
    cerebro.plot()

# Run it
if __name__ == '__main__':
    run_backtest()
