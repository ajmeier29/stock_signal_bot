from datetime import datetime, timedelta, timezone
import yfinance as yf

class JoeStrategy_V1:
    def __init__(self, data, dma_50, alpaca, lookback=30, volume_threshold=1.25, min_price=20, volume_avg_period=30, earnings_days=14):
        self.data = data  # Dictionary of data feeds
        self.dma_50 = dma_50  # 50-day DMA indicator
        self.alpaca = alpaca  # Alpaca API client
        self.lookback = lookback  # Lookback for HH/HL and support/resistance
        self.volume_threshold = volume_threshold  # 125% of avg volume
        self.min_price = min_price  # Min stock price $20
        self.volume_avg_period = volume_avg_period  # 30 days for avg volume
        self.earnings_days = earnings_days  # Min 14 days before earnings

    def generate_signal(self, data):
        symbol = data._name
        try:
            # Watchlist Criteria
            # 1. Uptrend with 2 HH/HL
            closes = data.close.get(size=self.lookback)
            highs = data.high.get(size=self.lookback)
            lows = data.low.get(size=self.lookback)
            if len(closes) < self.lookback:
                return None
            hh_hl = all(highs[i] > highs[i-1] and lows[i] > lows[i-1] for i in range(-1, -3, -1))  # Last 2 bars
            if not hh_hl:
                return None

            # 2. 30-day avg volume > 500K
            volumes = data.volume.get(size=self.volume_avg_period * 96)  # ~30 days (96 bars/day)
            if len(volumes) < self.volume_avg_period * 96:
                return None
            avg_volume = sum(volumes) / len(volumes)
            if avg_volume * 390 / 15 < 500000:  # Scale to daily volume (390 min / 15-min bars)
                return None

            # 3. Above uptrending 50 DMA for 1 day
            current_close = data.close[0]
            dma_50 = self.dma_50[data][0]
            dma_50_prev = self.dma_50[data][-1]
            if current_close <= dma_50 or dma_50 <= dma_50_prev:
                return None

            # 4. Stock price >= $20
            if current_close < self.min_price:
                return None

            # Setup Conditions
            # 1. >=14 days before earnings (commented out)
            # ticker = yf.Ticker(symbol)
            # earnings_dates = ticker.calendar
            # if earnings_dates is not None and 'Earnings Date' in earnings_dates:
            #     try:
            #         next_earnings = earnings_dates['Earnings Date'][0]
            #         # Convert datetime.date to datetime.datetime if necessary
            #         if isinstance(next_earnings, datetime):
            #             next_earnings_dt = next_earnings
            #         elif isinstance(next_earnings, str):
            #             next_earnings_dt = datetime.fromisoformat(next_earnings.replace('Z', '+00:00'))
            #         else:  # Assume datetime.date
            #             next_earnings_dt = datetime.combine(next_earnings, datetime.min.time(), tzinfo=timezone.utc)
            #         days_to_earnings = (next_earnings_dt - datetime.now(timezone.utc)).days
            #         print(f"[DEBUG] {symbol}: Next earnings on {next_earnings_dt}, {days_to_earnings} days away")
            #         if days_to_earnings < self.earnings_days:
            #             return None
            #     except (KeyError, TypeError, ValueError) as e:
            #         print(f"[WARNING] {symbol}: Failed to parse earnings data - {e}, proceeding without check")
            # else:
            #     print(f"[WARNING] {symbol}: No earnings data available, proceeding without check")

            # 2. Uptrending with pullback to support
            recent_lows = min(lows[-5:])  # Support as lowest low in last 5 bars
            if not (recent_lows * 0.99 <= current_close <= recent_lows * 1.01):  # Within 1% of support
                return None

            # 3. Sideways to uptrending at resistance
            recent_high = max(highs[-5:])  # Resistance as highest high in last 5 bars
            if current_close < recent_high * 0.99:  # Not near resistance
                return None

            # 4. Bullish price pattern (simplified: bullish engulfing)
            if not (data.close[0] > data.open[0] and data.close[0] > data.open[-1] and data.open[0] < data.close[-1]):
                return None

            # 5. Trending above 50 DMA (already checked)
            # Entry Rules
            # 1. CAHOLD: Close above high of low day
            low_day_high = highs[lows.index(min(lows[-5:]))]
            if current_close <= low_day_high:
                return None

            # 2. No buy on down day
            if current_close <= data.close[-1]:
                return None

            # 3. Break above resistance with 125% avg volume
            current_volume = data.volume[0]
            if current_volume * 390 / 15 < avg_volume * self.volume_threshold * 390 / 15:
                # Check next day condition: stays above resistance or up
                if data.close[-1] <= recent_high or data.close[0] <= data.close[-1]:
                    return None

            # 4. >=14 days before earnings (commented out)
            # 5. No bad news
            news = self.alpaca.get_news(symbol, limit=5)
            for article in news:
                # Simplified: Check for negative keywords in title/description
                if any(word in (article.headline + article.summary).lower() for word in ["downgrade", "loss", "decline", "negative"]):
                    return None

            return "LONG"

        except Exception as e:
            print(f"[ERROR] {symbol} - JoeStrategy_V1: {type(e).__name__}: {e}")
            return None

    def check_exit(self, data, position, entry_price):
        """Check exit conditions for an open position."""
        symbol = data._name
        try:
            current_close = data.close[0]
            # 1. 5% below support
            recent_lows = min(data.low.get(size=5))  # Support as lowest low in last 5 bars
            if current_close < recent_lows * 0.95:
                return True

            # 2. Trend changes (close below 50 DMA or no new HH/HL)
            if current_close < self.dma_50[data][0]:
                return True
            highs = data.high.get(size=3)
            lows = data.low.get(size=3)
            if len(highs) >= 3 and not (highs[-1] > highs[-2] and lows[-1] > lows[-2]):
                return True

            # 3. Trailing stop: 15% below price if >15% above entry
            if entry_price and current_close >= entry_price * 1.15:
                if current_close < max(data.close.get(size=5)) * 0.85:  # 15% below recent high
                    return True

            return False

        except Exception as e:
            print(f"[ERROR] {symbol} - JoeStrategy_V1 Exit: {type(e).__name__}: {e}")
            return False