# strategies/fvg_pro.py

import backtrader as bt

class FvgProLongOnly(bt.Strategy):
    params = dict(
        risk_pct=0.01,             # risk 1% of capital per trade
        atr_mult_initial=1.5,      # initial stop = 1.5 * ATR
        atr_mult_trail=2.5,        # trail stop = 2.5 * ATR after break-even
        partial_tp_pct=0.02,       # take 50% at +2%
        max_bars_in_trade=5,       # tighten stop after 5 bars
    )

    def __init__(self):
        self.smas = {d: bt.ind.SMA(d, period=20) for d in self.datas}
        self.atrs = {d: bt.ind.ATR(d, period=14) for d in self.datas}
        self.entry_price = {}
        self.high_water = {}
        self.break_even_triggered = {}
        self.entry_bar = {}
        self.order_refs = {}

    def next(self):
        for d in self.datas:
            pos = self.getposition(d)
            price = d.close[0]
            atr = self.atrs[d][0]
            sma = self.smas[d][0]

            if len(d) < 3 or atr == 0:
                continue

            # === ENTRY LOGIC ===
            bullish_fvg = d.low[0] > d.high[-2]
            bullish = d.close[0] > d.open[0] and (d.close[0] - d.open[0]) > 0.5 * (d.high[0] - d.low[0])

            if not pos:
                cash = self.broker.getvalue()
                risk_amount = cash * self.p.risk_pct
                size = int(risk_amount / (atr * self.p.atr_mult_initial))

                if bullish_fvg and bullish and price > sma:
                    self.buy(data=d, size=size)
                    self.entry_price[d] = price
                    self.high_water[d] = price
                    self.break_even_triggered[d] = False
                    self.entry_bar[d] = len(d)
                    print(f"📅 LONG {d._name} @ {price:.2f}")

            # === POSITION MANAGEMENT ===
            elif pos:
                entry = self.entry_price[d]
                bars_held = len(d) - self.entry_bar[d]
                pnl = (price - entry) * pos.size

                if pos.size > 0:
                    self.high_water[d] = max(self.high_water[d], price)

                    # Partial take profit
                    if not self.break_even_triggered[d] and price >= entry * (1 + self.p.partial_tp_pct):
                        self.sell(data=d, size=pos.size // 2)
                        print(f"📈 TP Partial LONG {d._name} @ {price:.2f} | PnL: {pnl:.2f}")
                        self.break_even_triggered[d] = True

                    if self.break_even_triggered[d]:
                        trail_stop = self.high_water[d] - self.p.atr_mult_trail * atr
                        if bars_held >= self.p.max_bars_in_trade:
                            trail_stop = max(trail_stop, entry)  # tighten
                    else:
                        trail_stop = entry - self.p.atr_mult_initial * atr

                    if price <= trail_stop:
                        self.close(data=d)
                        print(f"🚪 EXIT LONG {d._name} @ {price:.2f} | PnL: {pnl:.2f}")
