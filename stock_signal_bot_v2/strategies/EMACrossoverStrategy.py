class EMACrossoverStrategy:
    def __init__(self, ema_fast, ema_slow):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def generate_signal(self, data):
        ema_fast = self.ema_fast[data][0]
        ema_slow = self.ema_slow[data][0]
        ema_fast_prev = self.ema_fast[data][-1]
        ema_slow_prev = self.ema_slow[data][-1]

        if ema_fast > ema_slow and ema_fast_prev <= ema_slow_prev:
            return "LONG"
        elif ema_fast < ema_slow and ema_fast_prev >= ema_slow_prev:
            return "SHORT"
        return None