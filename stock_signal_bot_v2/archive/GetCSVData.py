import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
from datetime import datetime
import os
# Set your Alpaca credentials
API_KEY = os.getenv('ALPACA_API_KEY')
API_SECRET = os.getenv('ALPACA_SECRET_KEY')
BASE_URL = 'https://paper-api.alpaca.markets'


# Setup
alpaca = REST(API_KEY, API_SECRET, BASE_URL)

# Config
symbol = 'NVDA'
start = "2025-01-01"
end = "2025-04-21"
timeframe = TimeFrame(5, TimeFrameUnit.Minute)  # or TimeFrame.Day

# Get data
bars = alpaca.get_bars(symbol, timeframe, start=start, end=end).df

# Format it
bars = bars.copy()
bars.index.name = 'datetime'
bars = bars.reset_index()

# Select only the columns we care about
df = bars[['datetime', 'open', 'high', 'low', 'close', 'volume']]

# Save
csv_path = f"{symbol}_data.csv"
df.to_csv(csv_path, index=False)
print(f"✅ Exported to: {csv_path}")