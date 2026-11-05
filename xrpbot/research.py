"""Cost-aware next-bar backtests, reporting and chronological research helpers."""
import math
import random
from dataclasses import asdict
from statistics import mean, pstdev
from .engine import Config, PaperBroker
from .signals import analyze


def metrics(equities, trades, initial, held_seconds=0):
    values = [initial] + [v for _, v in equities]
    peak, drawdown = initial, 0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    sells = [t['pnl'] for t in trades if t['side'] == 'sell']
    wins, losses = sum(p for p in sells if p > 0), -sum(p for p in sells if p < 0)
    # UTC daily close returns; annualized using 365 days, not irregular trade events.
    daily = {}
    for ts, value in equities:
        daily[ts // 86400] = value
    d = list(daily.values())
    returns = [b / a - 1 for a, b in zip(d, d[1:])]
    avg = mean(returns) if returns else 0
    std = pstdev(returns) if len(returns) >= 2 else 0
    downside = math.sqrt(mean(min(r, 0) ** 2 for r in returns)) if returns else 0
    duration = equities[-1][0] - equities[0][0] + 60 if equities else 0
    exposure = held_seconds / duration if duration else 0
    net = values[-1] - initial
    return {'net_profit': net, 'return_pct': net / initial * 100, 'max_drawdown_pct': drawdown * 100,
            'win_rate': sum(p > 0 for p in sells) / len(sells) if sells else None,
            'profit_factor': wins / losses if losses else None,
            'sharpe': avg / std * math.sqrt(365) if std else None,
            'sortino': avg / downside * math.sqrt(365) if downside else None,
            'expectancy': mean(sells) if sells else None, 'closed_trades': len(sells),
            'fees_paid': sum(t['fee'] for t in trades), 'exposure_fraction': exposure,
            'exposure_adjusted_return_pct': net / initial * 100 / exposure if exposure else None,
            'daily_return_observations': len(returns)}


def backtest(candles, config=None, strategy='auto', signal_fn=None):
    from .data import validate
    validate(candles)
    cfg = config or Config()
    broker, curve, held = PaperBroker(cfg), [], 0
    pending = None
    for idx, bar in enumerate(candles):
        # Signal uses data ending at previous close; execution begins next open.
        if pending and pending.get('exit') and broker.account.quantity:
            broker.sell(bar.open, bar.timestamp, 'baseline exhaustion/structure exit', f'signal-exit:{bar.timestamp}')
        elif pending and pending['action'] == 'buy':
            broker.buy(bar.open, pending['atr'], bar.timestamp, f'entry:{bar.timestamp}', pending['reason'], capacity=bar.volume * .01)
        if broker.account.quantity:
            held += 60
        broker.on_bar(bar)
        curve.append((bar.timestamp + 60, broker.account.equity(bar.close)))
        pending = None
        if signal_fn:
            pending = signal_fn(candles[:idx + 1])
        elif (bar.timestamp + 60) % 300 == 0 and idx >= 201 * 240:
            pending = analyze(candles[max(0, idx - 260 * 240 + 1):idx + 1], strategy)
    if broker.account.quantity:
        broker.sell(candles[-1].close, candles[-1].timestamp + 60, 'end of sample liquidation', 'final')
        curve[-1] = (curve[-1][0], broker.account.cash)
    # Buy/hold enters at first open, exits at final close, with same modeled costs.
    cost = (cfg.spread_bps / 2 + cfg.slippage_bps) / 10000
    hold_qty = cfg.capital / (candles[0].open * (1 + cost) * (1 + cfg.fee))
    hold_final = hold_qty * candles[-1].close * (1 - cost) * (1 - cfg.fee)
    return {'metrics': metrics(curve, broker.account.trades, cfg.capital, held),
            'buy_hold_return_pct': (hold_final / cfg.capital - 1) * 100,
            'trades': broker.account.trades, 'equity': curve, 'assumptions': asdict(cfg),
            'source': 'caller supplied OHLCV; verify provenance separately',
            'strategy': strategy, 'validated_for_live': False,
            'warnings': ['Rule thresholds are research assumptions, not optimized or validated.',
                         'Constant fee scenario; historical tier schedules not supplied.',
                         'OHLCV cannot reconstruct queue priority or intrabar market depth.'] +
                        (['Insufficient multi-timeframe warmup; no production-rule evaluation.'] if len(candles) < 202 * 240 and signal_fn is None else [])}


def chronological_folds(length, train, test, purge=1):
    if min(train, test, purge) < 1:
        raise ValueError('Positive train/test/purge required')
    cursor = train + purge
    while cursor + test <= length:
        yield (0, cursor - purge), (cursor, cursor + test)
        cursor += test


def monte_carlo(trades, initial=10000, simulations=1000, seed=7):
    """Permutation drawdown sensitivity; terminal P/L is invariant, not a forecast."""
    if simulations < 1 or initial <= 0:
        raise ValueError('Positive simulations and initial equity required')
    pnl = [t['pnl'] for t in trades if t['side'] == 'sell']
    rng, drawdowns = random.Random(seed), []
    for _ in range(simulations):
        order = rng.sample(pnl, len(pnl))
        equity = peak = initial
        dd = 0
        for p in order:
            equity += p
            peak = max(peak, equity)
            dd = max(dd, 1 - equity / peak)
        drawdowns.append(dd)
    return {'simulations': simulations, 'seed': seed, 'trades': len(pnl),
            'drawdown_p95_pct': sorted(drawdowns)[int(.95 * (simulations - 1))] * 100 if pnl else None}


def stability(candles, strategy='auto'):
    return [{'stop_atr': atr, 'fee': fee,
             'metrics': backtest(candles, Config(stop_atr=atr, fee=fee), strategy)['metrics']}
            for atr in (1.5, 2, 2.5) for fee in (.004, .006, .008)]


def walk_forward(candles, train=60000, test=14400, strategy='auto'):
    """Fixed-rule OOS evaluation, no parameter fitting or test-set optimization."""
    results = []
    for train_span, test_span in chronological_folds(len(candles), train, test):
        start, end = test_span
        # Warmup history can inform a signal but is never traded in this fold.
        def signals(prefix):
            absolute = start + len(prefix) - 1
            if (prefix[-1].timestamp + 60) % 300:
                return {'action': 'hold'}
            return analyze(candles[max(0, absolute - 260 * 240 + 1):absolute + 1], strategy)
        report = backtest(candles[start:end], strategy=strategy, signal_fn=signals)
        results.append({'train_indices': train_span, 'test_indices': test_span, 'metrics': report['metrics']})
    return results
