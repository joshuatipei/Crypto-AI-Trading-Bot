"""Deterministic spot simulator. All orders are virtual, persisted atomically."""
import json
import math
import sqlite3
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_DOWN


@dataclass(frozen=True)
class Config:
    capital: float = 10000
    risk_per_trade: float = .005
    aggregate_risk: float = .02
    daily_loss: float = .02
    max_drawdown: float = .10
    fee: float = .006  # conservative configurable scenario, NOT historical fee claim
    spread_bps: float = 10
    slippage_bps: float = 5
    max_cost_fraction: float = .02
    max_trades_day: int = 12
    cooldown_seconds: int = 900
    max_quote_age: int = 90
    stop_atr: float = 2
    quantity_step: float = .000001
    min_quantity: float = 1
    min_notional: float = 5

    def __post_init__(self):
        if any(not math.isfinite(v) for v in asdict(self).values()):
            raise ValueError('Non-finite config')
        if self.capital <= 0 or self.stop_atr <= 0 or self.quantity_step <= 0 or self.min_quantity < 0 or self.min_notional < 0:
            raise ValueError('Invalid capital/precision/stop')
        for k in ('risk_per_trade', 'aggregate_risk', 'daily_loss', 'max_drawdown', 'max_cost_fraction'):
            if not 0 < getattr(self, k) < 1:
                raise ValueError(f'Invalid {k}')
        if not 0 <= self.fee < 1:
            raise ValueError('Invalid fee')
        if self.risk_per_trade > self.aggregate_risk or min(self.spread_bps, self.slippage_bps, self.cooldown_seconds) < 0 or self.max_quote_age <= 0 or self.max_trades_day < 1:
            raise ValueError('Invalid risk or execution limits')


@dataclass
class Account:
    cash: float = 10000
    quantity: float = 0
    entry: float = 0
    entry_fee: float = 0
    stop: float = 0
    peak: float = 10000
    day: int = -1
    day_equity: float = 10000
    daily_entries: int = 0
    last_entry: int = -10**12
    paused: bool = False
    halted: bool = False
    last_price: float = 0
    last_timestamp: int = 0
    seen: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def equity(self, price):
        return self.cash + self.quantity * price


class PaperBroker:
    def __init__(self, config=None, account=None):
        self.config = config or Config()
        self.account = account or Account(cash=self.config.capital, peak=self.config.capital, day_equity=self.config.capital)

    def event(self, ts, message):
        self.account.events.append({'timestamp': ts, 'message': message})
        self.account.events = self.account.events[-200:]

    def mark(self, price, ts):
        a, c = self.account, self.config
        if not math.isfinite(price) or price <= 0 or ts < a.last_timestamp:
            raise ValueError('Invalid or out-of-order mark')
        eq = a.equity(price)
        if a.day != ts // 86400:
            # Boundary baseline uses previous mark, preserving overnight gap losses.
            a.day_equity = a.equity(a.last_price) if a.last_price else eq
            a.day, a.daily_entries = ts // 86400, 0
        a.peak = max(a.peak, eq)
        if eq <= a.day_equity * (1 - c.daily_loss) or eq <= a.peak * (1 - c.max_drawdown):
            if not a.halted:
                self.event(ts, 'Risk cutoff: entries latched off; flattening virtual position')
            a.halted = True
        a.last_price, a.last_timestamp = price, ts
        return eq

    def sell(self, reference, ts, reason, identifier):
        a, c = self.account, self.config
        if identifier in a.seen or not a.quantity:
            return False
        fill = reference * (1 - (c.spread_bps / 2 + c.slippage_bps) / 10000)
        gross = a.quantity * fill
        fee = gross * c.fee
        pnl = gross - fee - a.quantity * a.entry - a.entry_fee
        a.cash += gross - fee
        a.trades.append({'timestamp': ts, 'side': 'sell', 'quantity': a.quantity, 'price': fill,
                         'fee': fee, 'slippage': reference - fill, 'pnl': pnl, 'reason': reason, 'id': identifier})
        a.quantity = a.entry = a.entry_fee = a.stop = 0
        a.seen.append(identifier)
        return True

    def buy(self, reference, atr, ts, identifier, reason, limit=None, low=None, capacity=None):
        a, c = self.account, self.config
        eq = self.mark(reference, ts)
        if identifier in a.seen or a.quantity or a.paused or a.halted or a.daily_entries >= c.max_trades_day or ts - a.last_entry < c.cooldown_seconds:
            return False
        if not math.isfinite(atr) or atr <= 0:
            raise ValueError('ATR must be positive')
        costs = 2 * c.fee + (c.spread_bps + 2 * c.slippage_bps) / 10000
        if costs > c.max_cost_fraction:
            return False
        fill = reference * (1 + (c.spread_bps / 2 + c.slippage_bps) / 10000)
        if limit is not None:
            if not math.isfinite(limit) or limit <= 0:
                raise ValueError('Invalid limit')
            if low is None or low > limit:
                return False  # IOC simulator: cancel unfilled remainder
            fill = min(fill, limit)
        stop = fill - c.stop_atr * atr
        if stop <= 0:
            return False
        # Include entry/exit costs and modeled stop slippage in capital-at-risk.
        risk_unit = fill * (1 + c.fee) - stop * (1 - (c.spread_bps / 2 + c.slippage_bps) / 10000) * (1 - c.fee)
        qty = min(eq * min(c.risk_per_trade, c.aggregate_risk) / risk_unit, a.cash / (fill * (1 + c.fee)))
        if capacity is not None:
            if not math.isfinite(capacity) or capacity < 0:
                raise ValueError('Invalid liquidity capacity')
            qty = min(qty, capacity)
        step = Decimal(str(c.quantity_step))
        qty = float((Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_DOWN) * step)
        if qty < c.min_quantity or qty * fill < c.min_notional:
            return False
        fee = qty * fill * c.fee
        a.cash -= qty * fill + fee
        a.quantity, a.entry, a.entry_fee, a.stop = qty, fill, fee, stop
        a.last_entry, a.daily_entries = ts, a.daily_entries + 1
        a.seen.append(identifier)
        a.trades.append({'timestamp': ts, 'side': 'buy', 'quantity': qty, 'price': fill,
                         'fee': fee, 'slippage': fill - reference, 'reason': reason, 'id': identifier,
                         'fill_status': 'filled_or_capacity_limited_IOC'})
        return True

    def on_bar(self, bar):
        """Existing stop is processed before new signals. Gap fills at worse open."""
        a = self.account
        self.mark(bar.open, bar.timestamp)
        if a.quantity and (a.halted or bar.low <= a.stop):
            self.sell(bar.open if a.halted else min(bar.open, a.stop), bar.timestamp,
                      'risk cutoff' if a.halted else 'protective stop', f'exit:{bar.timestamp}')
        self.mark(bar.close, bar.timestamp + 60)
        if a.halted and a.quantity:
            self.sell(bar.close, bar.timestamp + 60, 'risk cutoff', f'cutoff:{bar.timestamp}')

    def quote_guard(self, quote, now):
        bid, ask, ts = quote['bid'], quote['ask'], quote['timestamp']
        if not all(math.isfinite(x) for x in (bid, ask, ts)) or bid <= 0 or ask < bid or not -5 <= now - ts <= self.config.max_quote_age:
            raise ValueError('Invalid, future, or stale quote')
        if (ask - bid) / bid + 2 * self.config.fee + 2 * self.config.slippage_bps / 10000 > self.config.max_cost_fraction:
            raise ValueError('Spread exceeds cost budget')
        return (bid + ask) / 2


class Store:
    """Single SQLite transaction covers idempotency IDs, fills, cash and risk state."""
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')

    def transact(self, callback):
        with sqlite3.connect(self.path, timeout=15) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM state WHERE id=1').fetchone()
            payload = json.loads(row[0]) if row else {}
            result = callback(payload)
            db.execute('INSERT OR REPLACE INTO state VALUES(1, ?)', (json.dumps(payload, allow_nan=False),))
            return result
