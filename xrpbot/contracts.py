"""Exchange-independent public-data contract; deliberately no authenticated orders."""
from typing import Protocol
from .data import Candle


class MarketData(Protocol):
    symbol: str
    def product(self) -> dict: ...
    def candles(self, start: int, end: int) -> list[Candle]: ...
    def quote(self) -> dict: ...


def product_limits(venue, product):
    """Extract public spot minimums for virtual orders; reject missing filters."""
    if venue == 'coinbase':
        return {'quantity_step':float(product['base_increment']),
                'min_quantity':float(product.get('base_min_size',0)),
                'min_notional':float(product['min_market_funds'])}
    filters = {f['filterType']:f for f in product['filters']}
    lot = filters['LOT_SIZE']
    notional = filters.get('NOTIONAL',filters.get('MIN_NOTIONAL'))
    if not notional:
        raise ValueError('Missing minimum notional filter')
    return {'quantity_step':float(lot['stepSize']), 'min_quantity':float(lot['minQty']),
            'min_notional':float(notional['minNotional'])}
