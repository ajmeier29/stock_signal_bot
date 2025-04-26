class NewRuleStrategy:
    def __init__(self, data, lookback=20):
        self.data = data
        self.lookback = lookback

    def generate_signal(self, data):
        # Placeholder: Price breakout rule
        high = max(self.data.close.get(size=self.lookback))
        low = min(self.data.close.get(size=self.lookback))
        current_price = self.data.close[0]
        prev_price = self.data.close[-1]

        # LONG: Price breaks above 20-period high
        if current_price > high and prev_price <= high:
            return "LONG"
        # SHORT: Price breaks below 20-period low
        elif current_price < low and prev_price >= low:
            return "SHORT"
        return None