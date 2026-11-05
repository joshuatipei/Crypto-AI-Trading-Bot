"""Live public-data loop; ONLY simulated balances and fills."""
import logging
import time
from dataclasses import asdict, replace
from .data import exchange, validate
from .contracts import product_limits
from .engine import Account, Config, PaperBroker
from .signals import analyze

LOG = logging.getLogger(__name__)


def load_broker(payload):
    config = Config(**payload.get('config', {}))
    account = Account(**payload['account']) if payload.get('account') else None
    return PaperBroker(config, account)


def save_broker(payload, broker):
    payload['config'] = asdict(broker.config)
    payload['account'] = asdict(broker.account)
    price = broker.account.last_price
    eq = broker.account.equity(price)
    payload['equity'] = eq
    payload['daily_pnl'] = eq - broker.account.day_equity
    payload['cumulative_pnl'] = eq - broker.config.capital
    payload['drawdown_pct'] = (1 - eq / broker.account.peak) * 100
    payload['open_risk'] = broker.account.quantity * max(0, broker.account.entry - broker.account.stop)
    sells = [t['pnl'] for t in broker.account.trades if t['side'] == 'sell']
    payload['win_rate'] = sum(p > 0 for p in sells) / len(sells) if sells else None
    payload['open_orders'] = []  # IOC fills only; protective stop remains in account.stop
    payload['mode'] = 'paper'


def tick(store, adapter, candles, now=None, data_error=None):
    now = time.time() if now is None else now
    quote = adapter.quote()
    # A live quote never replays historical signals as historical fills.
    stale = not -5 <= now - (candles[-1].timestamp + 60) <= 120
    blocked = data_error or ('Stale candle stream; entries disabled' if stale else None)
    signal = analyze(candles) if not blocked else {'action':'hold','regime':'unfavorable','reason':blocked,'indicators':{}}
    def update(payload):
        broker = load_broker(payload)
        price = broker.quote_guard(quote, now)
        a = broker.account
        if quote['timestamp'] < a.last_timestamp:
            raise ValueError('Out-of-order exchange quote')
        broker.mark(price, int(quote['timestamp']))
        if a.quantity and (a.halted or quote['bid'] <= a.stop):
            broker.sell(quote['bid'], int(now), 'risk cutoff' if a.halted else 'protective stop', f'quote-exit:{quote["timestamp"]}')
        # Exchange spread is included in bid/ask. Avoid charging modeled half-spread twice.
        reference = quote['ask'] / (1 + broker.config.spread_bps / 20000)
        identifier = f'signal:{(candles[-1].timestamp + 60) // 300 * 300}'
        if payload.get('last_signal') != identifier:
            if signal.get('exit') and a.quantity:
                broker.sell(quote['bid'], int(quote['timestamp']), 'baseline exhaustion/structure exit', identifier)
            elif signal['action'] == 'buy':
                broker.buy(reference, signal['atr'], int(quote['timestamp']), identifier, signal['reason'], capacity=candles[-1].volume * .01)
            payload['last_signal'] = identifier
        save_broker(payload, broker)
        payload.update({'signal': signal, 'quote': quote, 'updated_at': now, 'error': blocked, 'exchange': adapter.symbol})
    store.transact(update)


def run(store, venue, seed, stop_event, interval=15):
    adapter = exchange(venue)
    product = adapter.product()  # No credentials, public pair availability.
    limits = product_limits(venue, product)
    def configure(payload):
        if payload.get('venue', venue) != venue:
            raise ValueError('Use a separate ledger for each exchange/quote currency')
        broker = load_broker(payload)
        broker.config = replace(broker.config, **limits)
        payload['venue'] = venue
        save_broker(payload, broker)
    store.transact(configure)
    candles = validate(seed)
    while not stop_event.is_set():
        try:
            end = int(time.time()) // 60 * 60
            start = candles[-1].timestamp + 60
            data_error = None
            if start < end:
                try:
                    fresh = adapter.candles(start, end)
                    candles = validate((candles + fresh)[-260 * 240:])
                except Exception:
                    LOG.exception('Candle update failed; only protective quote exits allowed')
                    data_error = 'Candle update failed; new entries suspended'
            tick(store, adapter, candles, data_error=data_error)
        except Exception as exc:
            LOG.exception('Paper market-data cycle failed')
            def record(payload):
                payload['error'] = f'{type(exc).__name__}: market-data cycle failed; new entries suspended'
                payload['error_at'] = time.time()
            store.transact(record)
        stop_event.wait(interval)
