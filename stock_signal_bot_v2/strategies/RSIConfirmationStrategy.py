class RSIConfirmationStrategy:
    def __init__(self, rsi):
        self.rsi = rsi

    def generate_signal(self, data):
        rsi = self.rsi[data][0]
        rsi_prev = self.rsi[data][-1]

        #if rsi > 50 and rsi_prev <= 50:  # RSI crosses above 50
        if rsi > 40 and rsi > rsi_prev:  # RSI above 40 and rising
            return "LONG"
        elif rsi < 50 and rsi_prev >= 50:  # RSI crosses below 50
            return "SHORT"
        return None