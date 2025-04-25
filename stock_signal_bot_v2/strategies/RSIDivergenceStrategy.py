class RSIDivergenceStrategy:
    def __init__(self, rsi, datas):
        self.rsi = rsi
        self.datas = datas

    def generate_signal(self, data):
        price_change = data.close[0] - data.close[-1]
        rsi_change = self.rsi[data][0] - self.rsi[data][-1]

        if price_change <= 0 and rsi_change > 0:
            return "LONG"
        elif price_change >= 0 and rsi_change < 0:
            return "SHORT"
        return None