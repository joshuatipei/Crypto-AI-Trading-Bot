"""Causal descriptive indicators and independent, unvalidated baseline rules."""
import math
from statistics import mean, pstdev
from .data import aggregate


def ema(values, period):
    value = values[0]
    alpha = 2 / (period + 1)
    for x in values[1:]:
        value += alpha * (x - value)
    return value


def indicators(candles):
    if len(candles) < 201:
        raise ValueError('At least 201 closed candles required')
    close = [c.close for c in candles]
    changes = [b - a for a, b in zip(close, close[1:])]
    gain = mean(max(x, 0) for x in changes[-14:])
    loss = mean(max(-x, 0) for x in changes[-14:])
    rsi = 100 - 100 / (1 + gain / loss) if loss else (100 if gain else 50)
    tr, plus, minus = [], [], []
    for prev, c in zip(candles, candles[1:]):
        tr.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
        up, down = c.high - prev.high, prev.low - c.low
        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)
    # Wilder smoothing, seeded with a 14-period average.
    def wilder(values):
        out = [mean(values[:14])]
        for value in values[14:]:
            out.append((out[-1] * 13 + value) / 14)
        return out
    atrs, pos, neg = wilder(tr), wilder(plus), wilder(minus)
    dx = [100 * abs(p - n) / (p + n) if p + n else 0 for p, n in zip(pos, neg)]
    adx = wilder(dx)[-1]
    day = candles[-1].timestamp // 86400
    session = [c for c in candles if c.timestamp // 86400 == day]
    volume = sum(c.volume for c in session)
    vwap = sum((c.high + c.low + c.close) / 3 * c.volume for c in session) / volume if volume else close[-1]
    middle, sd = mean(close[-20:]), pstdev(close[-20:])
    macds = [ema(close[:i], 12) - ema(close[:i], 26) for i in range(max(26, len(close) - 40), len(close) + 1)]
    prev20 = candles[-21:-1]
    return {'price': close[-1], **{f'ema{n}': ema(close, n) for n in (9, 21, 50, 200)},
            'rsi': rsi, 'macd': macds[-1], 'macd_signal': ema(macds, 9), 'adx': adx,
            'atr': atrs[-1], 'vwap': vwap, 'bb_lower': middle - 2 * sd, 'bb_upper': middle + 2 * sd,
            'zscore': (close[-1] - middle) / sd if sd else 0,
            'volume_ratio': candles[-1].volume / mean(c.volume for c in prev20) if mean(c.volume for c in prev20) else 0,
            'support': min(c.low for c in prev20), 'resistance': max(c.high for c in prev20),
            'liquidity_sweep': candles[-1].low < min(c.low for c in prev20) and close[-1] > min(c.low for c in prev20)}


def analyze(candles, strategy='auto'):
    frames = {str(n): aggregate(candles, n * 60) for n in (1, 5, 15, 60, 240)}
    if any(len(bars) < 201 for bars in frames.values()):
        return {'action': 'hold', 'regime': 'unfavorable', 'reason': 'Warmup: 201 closed candles per timeframe required', 'indicators': {}}
    values = {tf: indicators(bars[-260:]) for tf, bars in frames.items()}
    i, h, four = values['5'], values['60'], values['240']
    regime = ('volatile' if i['atr'] / i['price'] > .025 else
              'trending' if h['adx'] >= 25 else 'ranging' if h['adx'] < 20 else 'unfavorable')
    up = h['price'] > h['ema50'] > h['ema200'] and four['price'] > four['ema200']
    confirm = values['15']['ema9'] > values['15']['ema21']
    five = frames['5'][-260:]
    previous = five[-2]
    consolidation = five[-14:-2]
    ceiling = max(c.high for c in consolidation)
    floor = min(c.low for c in consolidation)
    breakout_retest = ((ceiling - floor) / ceiling < .025 and previous.close > ceiling
                       and five[-1].low <= ceiling * 1.003 and five[-1].close > ceiling)
    rules = {
        'trend': regime == 'trending' and up and confirm and i['ema21'] <= i['price'] <= i['ema9'] * 1.003 and 40 < i['rsi'] < 65,
        'breakout': up and confirm and breakout_retest and max(i['volume_ratio'], previous.volume / (mean(c.volume for c in consolidation) or 1)) > 1.5,
        'mean_reversion': regime == 'ranging' and i['zscore'] < -2 and i['rsi'] < 30 and values['15']['rsi'] < 40,
        'momentum': up and confirm and i['macd'] > i['macd_signal'] and 55 < i['rsi'] < 75 and i['volume_ratio'] > 1.5,
        'reversal': regime == 'ranging' and i['liquidity_sweep'] and i['rsi'] < 40 and values['1']['macd'] > values['1']['macd_signal'],
    }
    if strategy not in ('auto', *rules):
        raise ValueError('Unknown strategy')
    chosen = next((k for k, v in rules.items() if v and (strategy == 'auto' or strategy == k)), None)
    if regime in ('volatile', 'unfavorable'):
        chosen = None
    return {'action': 'buy' if chosen else 'hold', 'strategy': chosen, 'regime': regime,
            'exit': i['rsi'] > 75 or (i['price'] < i['ema21'] and i['macd'] < i['macd_signal'] and h['price'] < h['ema50']),
            'reason': f'Baseline {chosen} conditions met' if chosen else 'No eligible multi-timeframe setup',
            'indicators': values, 'atr': i['atr'], 'strength': 'conditions met' if chosen else 'no setup'}
