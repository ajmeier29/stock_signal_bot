import backtrader as bt

class EMAFilterStrategy:
    def __init__(self, ema_long):
        """
        Initialize the 200-Period EMA Filter Strategy.
        
        Args:
            ema_long: Backtrader EMA indicator for the long-term period (e.g., 200)
        """
        self.ema_long = ema_long

    def generate_signal(self, data, price):
        """
        Generate a trend signal based on price relative to the 200-period EMA.
        
        Args:
            data: Backtrader data feed
            price: Current price (data.close[0])
            
        Returns:
            str: "LONG" if price > EMA200, "SHORT" if price < EMA200, None otherwise
        """
        ema_long = self.ema_long[data][0]
        
        if price > ema_long:
            return "LONG"
        elif price < ema_long:
            return "SHORT"
        return None