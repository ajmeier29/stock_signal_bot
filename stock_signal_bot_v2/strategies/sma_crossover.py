# strategies/sma_crossover.py

import backtrader as bt

class SmaCrossover(bt.Strategy):
    """Simple SMA10/50 crossover strategy"""
    def __init__(self):
        self.crossovers = {
            d: bt.ind.CrossOver(bt.ind.SMA(d, period=10), bt.ind.SMA(d, period=50))
            for d in self.datas
        }

    def next(self):
        for d in self.datas:
            pos = self.getposition(d)
            cross = self.crossovers[d]
            if not pos and cross > 0:
                self.buy(data=d)
            elif pos and cross < 0:
                self.sell(data=d)
