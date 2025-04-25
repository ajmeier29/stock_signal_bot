import backtrader as bt
# Aggressive breakout strategy
class RelaxedBreakout(bt.Strategy):
    params = dict(
        breakout_period=10,  # shorter breakout period
        atr_period=14,
        atr_mult=1.5,
        risk_pct=0.03,
        profit_target=0.02,  # 2% profit target
    )

    def __init__(self):
        self.highest = bt.ind.Highest(self.data.high(-1), period=self.p.breakout_period)
        self.atr = bt.ind.ATR(period=self.p.atr_period)

    def next(self):
        if self.position.size == 0:
            if self.data.close[0] > self.highest[0]:
                size = int((self.broker.getvalue() * self.p.risk_pct) / (self.atr[0] * self.p.atr_mult))
                size = max(1, size)  # ensures at least 1 share is bought
                if size * self.data.close[0] < self.broker.getcash():
                    self.buy(size=size)
                    self.stop_loss = self.data.close[0] - (self.atr[0] * self.p.atr_mult)
                    self.profit_price = self.data.close[0] * (1 + self.p.profit_target)
        else:
            # Trailing stop logic
            self.stop_loss = max(self.stop_loss, self.data.close[0] - self.atr[0] * self.p.atr_mult)

            # Profit taking
            if self.data.close[0] >= self.profit_price:
                self.close()
            
            # Stop-loss hit
            elif self.data.close[0] <= self.stop_loss:
                self.close()



class SimpleGuaranteedTrade(bt.Strategy):
    def __init__(self):
        # Maintain a dict to hold EMA indicators per data feed
        self.ema_fast = {}
        self.ema_slow = {}

        for d in self.datas:
            self.ema_fast[d._name] = bt.ind.EMA(d.close, period=5)
            self.ema_slow[d._name] = bt.ind.EMA(d.close, period=20)

    def next(self):
        for d in self.datas:
            symbol = d._name
            pos = self.getposition(d).size
            price = d.close[0]

            if not pos:
                if self.ema_fast[symbol][0] > self.ema_slow[symbol][0]:
                    size = int(self.broker.getcash() / len(self.datas) / price)
                    if size > 0:
                        self.buy(data=d, size=size)
                        print(f"Entered long {symbol} at {price:.2f}")
            else:
                if self.ema_fast[symbol][0] < self.ema_slow[symbol][0]:
                    self.close(data=d)
                    print(f"Closed position {symbol} at {price:.2f}")
