import os
import pandas as pd
import backtrader as bt
from datetime import datetime, timedelta
from alpaca_trade_api.rest import REST, TimeFrame

# ========== STEP 1: Volume Filter ==========
def get_high_volume_sp500_tickers(min_avg_volume=1_000_000):
    API_KEY = os.getenv('ALPACA_API_KEY')
    API_SECRET = os.getenv('ALPACA_SECRET_KEY')
    BASE_URL = 'https://paper-api.alpaca.markets'
    alpaca = REST(API_KEY, API_SECRET, BASE_URL)

    sp500_url = 'https://datahub.io/core/s-and-p-500-companies/r/constituents.csv'
    sp500_df = pd.read_csv(sp500_url)
    tickers = sp500_df['Symbol'].tolist()

    liquid_tickers = []
    cutoff_date = (datetime.today() - timedelta(days=10)).strftime('%Y-%m-%d')
    today_str = datetime.today().strftime('%Y-%m-%d')

    print(f"\nFiltering {len(tickers)} S&P 500 tickers by volume...\n")

    for i, symbol in enumerate(tickers, 1):
        try:
            bars = alpaca.get_bars(
                symbol,
                TimeFrame.Day,
                start=cutoff_date,
                end=today_str
            ).df

            if not bars.empty and len(bars) >= 5:
                avg_volume = bars['volume'].tail(5).mean()
                if avg_volume >= min_avg_volume:
                    liquid_tickers.append(symbol)
                print(f"{i:3d}/{len(tickers)} {symbol:<6} ✅ Avg Vol: {avg_volume:,.0f}")
            else:
                print(f"{i:3d}/{len(tickers)} {symbol:<6} ❌ Not enough data")
        except Exception as e:
            print(f"{i:3d}/{len(tickers)} {symbol:<6} ⚠️ Skipped: {e}")
            continue

    print(f"\n✅ Filtered down to {len(liquid_tickers)} tickers with volume > {min_avg_volume:,}\n")
    return liquid_tickers


# ========== STEP 2: Backtrader Setup ==========

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


# ========== STEP 3: Main Backtest Runner ==========

def run_backtest():
    API_KEY = os.getenv('ALPACA_API_KEY')
    API_SECRET = os.getenv('ALPACA_SECRET_KEY')
    BASE_URL = 'https://paper-api.alpaca.markets'
    alpaca = REST(API_KEY, API_SECRET, BASE_URL)

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(10000)

    symbols = get_high_volume_sp500_tickers(min_avg_volume=1_000_000)

    for symbol in symbols:
        print(f"\nLoading data for {symbol}...")
        try:
            bars = alpaca.get_bars(
                symbol,
                TimeFrame.Day,
                start="2025-01-01",
                end="2025-04-21"
            ).df

            if len(bars) < 50:
                print(f"Skipping {symbol} (not enough data for SMA50)")
                continue

            bars.index = pd.to_datetime(bars.index)
            df = bars[['open', 'high', 'low', 'close', 'volume']].copy()
            df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            data = PandasData(dataname=df)
            cerebro.adddata(data, name=symbol)
        except Exception as e:
            print(f"Error loading {symbol}: {e}")
            continue

    if not cerebro.datas:
        print("❌ No valid data sources loaded. Exiting.")
        return

    cerebro.addstrategy(SmaCross)

    print('\nStarting Portfolio Value: %.2f' % cerebro.broker.getvalue())
    cerebro.run()
    print('Final Portfolio Value: %.2f' % cerebro.broker.getvalue())
    cerebro.plot()


# ========== Run it ==========

if __name__ == '__main__':
    run_backtest()
