import backtrader as bt

class ADXFilterStrategy:
    def __init__(self, adx, plus_di, minus_di, adx_threshold=25):
        """
        Initialize the ADX Filter Strategy.
        
        Args:
            adx: Backtrader ADX indicator
            plus_di: Backtrader +DI indicator
            minus_di: Backtrader -DI indicator
            adx_threshold: Minimum ADX value for a trending market (default: 25)
        """
        self.adx = adx
        self.plus_di = plus_di
        self.minus_di = minus_di
        self.adx_threshold = adx_threshold

    def generate_signal(self, data):
        """
        Generate a trend signal based on ADX and DI indicators.
        
        Args:
            data: Backtrader data feed
            
        Returns:
            str: "LONG" if ADX > threshold and +DI > -DI, "SHORT" if ADX > threshold and -DI > +DI, None otherwise
        """
        adx = self.adx[data][0]
        plus_di = self.plus_di[data][0]
        minus_di = self.minus_di[data][0]
        
        if adx > self.adx_threshold:
            if plus_di > minus_di:
                return "LONG"
            elif minus_di > plus_di:
                return "SHORT"
        return None