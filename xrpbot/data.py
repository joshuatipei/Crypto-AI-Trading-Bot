"""Validated closed OHLCV candles and read-only public exchange adapters."""
import csv
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode


@dataclass(frozen=True)
class Candle:
    timestamp: int  # UTC opening time, epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


def validate(candles, interval=60):
    if not candles:
        raise ValueError("No candles")
    previous = None
    for c in candles:
        if not all(math.isfinite(v) for v in asdict(c).values()):
            raise ValueError("Non-finite candle")
        if c.timestamp < 0 or c.timestamp % interval:
            raise ValueError("Candle timestamp is not interval aligned")
        if min(c.open, c.high, c.low, c.close) <= 0 or c.volume < 0:
            raise ValueError("Invalid price or volume")
        if c.low > min(c.open, c.close) or c.high < max(c.open, c.close) or c.high < c.low:
            raise ValueError("Invalid OHLC envelope")
        if previous is not None and c.timestamp - previous != interval:
            raise ValueError("Missing, duplicate, or unordered candles")
        previous = c.timestamp
    return candles


def read_csv(path, interval=60):
    with open(path, newline="") as f:
        candles = [Candle(int(r['timestamp']), *(float(r[k]) for k in ('open', 'high', 'low', 'close', 'volume'))) for r in csv.DictReader(f)]
    return validate(candles, interval)


def write_csv(path, candles):
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(Candle.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(c) for c in candles)


def aggregate(candles, seconds):
    """Only emit complete UTC-aligned groups; never expose unfinished higher bars."""
    groups = {}
    for c in candles:
        groups.setdefault(c.timestamp // seconds * seconds, []).append(c)
    result = []
    for ts, group in sorted(groups.items()):
        if len(group) != seconds // 60 or group[0].timestamp != ts or group[-1].timestamp != ts + seconds - 60:
            continue
        result.append(Candle(ts, group[0].open, max(c.high for c in group), min(c.low for c in group), group[-1].close, sum(c.volume for c in group)))
    return result


def public_json(url):
    # Inherited proxy and TLS trust are retained. Bounded retries only for transient errors.
    from urllib.error import HTTPError, URLError
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'xrp-paper-research/0.1'}), timeout=15) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError) as exc:
            if attempt == 2 or '403 Forbidden' in str(exc):
                raise
        time.sleep(2 ** attempt)


class Coinbase:
    """Public Exchange API only, XRP-USD. Verify product availability before use."""
    base = 'https://api.exchange.coinbase.com'
    symbol = 'XRP-USD'

    def product(self):
        p = public_json(f'{self.base}/products/{self.symbol}')
        if p.get('id') != self.symbol or p.get('status') != 'online' or p.get('trading_disabled', False):
            raise ValueError('XRP-USD is unavailable')
        return p

    def candles(self, start, end):
        result = {}
        for begin in range(start, end, 60 * 299):
            finish = min(end, begin + 60 * 299)
            iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).isoformat()
            query = urlencode({'granularity': 60, 'start': iso(begin), 'end': iso(finish)})
            rows = public_json(f'{self.base}/products/{self.symbol}/candles?{query}')
            for ts, low, high, op, close, vol in rows:
                if start <= ts < end and ts + 60 <= time.time():
                    result[ts] = Candle(ts, float(op), float(high), float(low), float(close), float(vol))
        return validate([result[t] for t in sorted(result)])

    def quote(self):
        q = public_json(f'{self.base}/products/{self.symbol}/ticker')
        return {'bid': float(q['bid']), 'ask': float(q['ask']),
                'timestamp': datetime.fromisoformat(q['time'].replace('Z', '+00:00')).timestamp()}


class BinanceUS:
    base = 'https://api.binance.us'
    symbol = 'XRPUSDT'

    def product(self):
        data = public_json(f'{self.base}/api/v3/exchangeInfo?symbol={self.symbol}')
        products = data.get('symbols', [])
        if len(products) != 1 or products[0]['status'] != 'TRADING' or not products[0].get('isSpotTradingAllowed'):
            raise ValueError('XRPUSDT spot unavailable')
        return products[0]

    def candles(self, start, end):
        result = []
        cursor = start
        while cursor < end:
            query = urlencode({'symbol': self.symbol, 'interval': '1m', 'startTime': cursor * 1000,
                               'endTime': end * 1000 - 1, 'limit': 1000})
            rows = public_json(f'{self.base}/api/v3/klines?{query}')
            if not rows:
                break
            for row in rows:
                ts = int(row[0]) // 1000
                if start <= ts < end and ts + 60 <= time.time():
                    result.append(Candle(ts, *(float(row[i]) for i in (1, 2, 3, 4, 5))))
            following = int(rows[-1][0]) // 1000 + 60
            if following <= cursor:
                raise ValueError('Non-advancing exchange pagination')
            cursor = following
        return validate(result)

    def quote(self):
        q = public_json(f'{self.base}/api/v3/ticker/bookTicker?symbol={self.symbol}')
        clock = public_json(f'{self.base}/api/v3/time')
        return {'bid': float(q['bidPrice']), 'ask': float(q['askPrice']), 'timestamp': clock['serverTime'] / 1000}


def exchange(name):
    return {'coinbase': Coinbase, 'binanceus': BinanceUS}[name]()


def orderbook(adapter):
    """Top-level displayed quantity imbalance; descriptive, not a predictive score."""
    if isinstance(adapter, Coinbase):
        book = public_json(f'{adapter.base}/products/{adapter.symbol}/book?level=1')
    elif isinstance(adapter, BinanceUS):
        book = public_json(f'{adapter.base}/api/v3/depth?symbol={adapter.symbol}&limit=5')
    else:
        raise ValueError('Unsupported adapter')
    bid = sum(float(row[1]) for row in book['bids'])
    ask = sum(float(row[1]) for row in book['asks'])
    if min(bid,ask)<0 or not math.isfinite(bid+ask):
        raise ValueError('Invalid orderbook')
    return {'imbalance':(bid-ask)/(bid+ask) if bid+ask else None,
            'received_at':time.time(),'note':'Displayed depth only; no spoofing or predictive inference'}
