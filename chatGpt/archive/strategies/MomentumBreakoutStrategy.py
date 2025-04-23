
import backtrader as bt

# Momentum breakout strategy
class MomentumBreakout(bt.Strategy):
    params = dict(
        period=20,
        atr_period=14,
        atr_mult=2.0
    )

    def __init__(self):
        self.highest = bt.ind.Highest(self.data.high(-1), period=self.p.period)
        self.atr = bt.ind.ATR(period=self.p.atr_period)
        self.order = None

    def next(self):
        if self.order:
            return

        if not self.position:
            if self.data.close[0] > self.highest[0]:
                size = int(self.broker.cash / self.data.close[0])
                self.order = self.buy(size=size)
                self.entry_price = self.data.close[0]
                self.stop_price = self.entry_price - self.atr[0] * self.p.atr_mult

        else:
            new_stop = self.data.close[0] - self.atr[0] * self.p.atr_mult
            self.stop_price = max(self.stop_price, new_stop)

            if self.data.close[0] <= self.stop_price:
                self.order = self.close()

    def notify_order(self, order):
        if order.status in [order.Completed, order.Canceled, order.Margin]:
            self.order = None
