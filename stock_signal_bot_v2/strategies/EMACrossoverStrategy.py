class EMACrossoverStrategy_v2:
    def __init__(self, ema_fast, ema_slow, min_gap_percent=0.1):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.min_gap_percent = min_gap_percent  # Minimum % gap between EMAs for a valid crossover

    def generate_signal(self, data):
        ema_fast = self.ema_fast[data][0]
        ema_slow = self.ema_slow[data][0]
        ema_fast_prev = self.ema_fast[data][-1]
        ema_slow_prev = self.ema_slow[data][-1]

        # Calculate percentage gap between EMAs
        gap_percent = abs((ema_fast - ema_slow) / ema_slow) * 100 if ema_slow != 0 else 0

        # Check if gap meets minimum threshold
        if gap_percent < self.min_gap_percent:
            return None  # Gap too small, no signal

        # Check for crossover
        if ema_fast > ema_slow and ema_fast_prev <= ema_slow_prev:
            return "LONG"
        elif ema_fast < ema_slow and ema_fast_prev >= ema_slow_prev:
            return "SHORT"
        return None


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