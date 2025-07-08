import os
from alpaca_trade_api.rest import REST
alpaca = REST(os.getenv('ALPACA_API_KEY'), os.getenv('ALPACA_SECRET_KEY'), 'https://paper-api.alpaca.markets')
tickers = ['MMM', 'AOS', 'ABT', 'AAPL']  # Sample tickers
bars = alpaca.get_bars(tickers, '5Min', limit=10).df
print(bars)