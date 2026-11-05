import argparse
import json
import logging
import threading
from pathlib import Path
from .data import exchange, read_csv, write_csv
from .engine import Store, Config
from dataclasses import asdict
from .paper import run
from .research import backtest, monte_carlo, stability, walk_forward
from .web import server


def main():
    parser = argparse.ArgumentParser(description='XRP spot PAPER ONLY; no live execution')
    sub = parser.add_subparsers(dest='command', required=True)
    ingest = sub.add_parser('ingest')
    ingest.add_argument('--exchange', choices=['coinbase', 'binanceus'], default='coinbase')
    ingest.add_argument('--start', type=int, required=True, help='UTC minute-aligned epoch seconds')
    ingest.add_argument('--end', type=int, required=True)
    ingest.add_argument('--output', required=True)
    test = sub.add_parser('backtest')
    test.add_argument('--csv', required=True)
    test.add_argument('--output', required=True)
    test.add_argument('--config', help='JSON Config overrides; fees are scenario assumptions')
    test.add_argument('--strategy', choices=['auto', 'trend', 'breakout', 'mean_reversion', 'momentum', 'reversal'], default='auto')
    test.add_argument('--walk-forward', action='store_true')
    test.add_argument('--stability', action='store_true')
    ml = sub.add_parser('ml-research')
    ml.add_argument('--csv', required=True)
    ml.add_argument('--output', required=True)
    web = sub.add_parser('serve')
    web.add_argument('--state', default='state/paper.sqlite')
    web.add_argument('--config', help='Initial ledger Config JSON; cannot change an existing ledger')
    web.add_argument('--host', default='127.0.0.1')
    web.add_argument('--port', type=int, default=8080)
    web.add_argument('--paper-csv', help='Validated XRP one-minute seed history; enables public-data loop')
    web.add_argument('--exchange', choices=['coinbase', 'binanceus'], default='coinbase')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command == 'ingest':
        if args.start % 60 or args.end % 60 or args.end <= args.start:
            parser.error('Require aligned start < end')
        adapter = exchange(args.exchange)
        product = adapter.product()
        candles = adapter.candles(args.start, args.end)
        if candles[0].timestamp != args.start or candles[-1].timestamp != args.end - 60:
            raise ValueError('Exchange returned incomplete requested history')
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        write_csv(args.output, candles)
        Path(args.output + '.meta.json').write_text(json.dumps({'exchange': args.exchange, 'symbol': adapter.symbol, 'product': product, 'start': args.start, 'end': args.end, 'candles': len(candles)}, indent=2))
        print(f'Saved {len(candles)} validated closed candles')
    elif args.command == 'ml-research':
        from .ml import evaluate
        report = evaluate(read_csv(args.csv))
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False))
        print('Saved research-only model evaluation; trading remains disabled')
    elif args.command == 'backtest':
        candles = read_csv(args.csv)
        cfg = Config(**json.loads(Path(args.config).read_text())) if args.config else Config()
        report = backtest(candles, config=cfg, strategy=args.strategy)
        import hashlib
        report['input_sha256'] = hashlib.sha256(Path(args.csv).read_bytes()).hexdigest()
        report['monte_carlo'] = monte_carlo(report['trades'])
        if args.walk_forward:
            report['walk_forward'] = walk_forward(candles, strategy=args.strategy)
        if args.stability:
            report['stability'] = stability(candles, args.strategy)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False))
        print(json.dumps(report['metrics'], indent=2))
    else:
        Path(args.state).parent.mkdir(parents=True, exist_ok=True)
        store = Store(args.state)
        if args.config:
            cfg = Config(**json.loads(Path(args.config).read_text()))
            def initialize(p):
                if p.get('config') and p['config'] != asdict(cfg):
                    raise ValueError('Existing ledger config differs; use a new state path after review')
                p['config'] = asdict(cfg)
            store.transact(initialize)
        from .paper import load_broker, save_broker
        store.transact(lambda p: save_broker(p, load_broker(p)))
        httpd = server(store, args.host, args.port)
        stop = threading.Event()
        if args.paper_csv:
            seed = read_csv(args.paper_csv)
            if len(seed) < 201 * 240:
                parser.error('Paper seed requires at least 48,240 consecutive one-minute candles')
            def worker():
                try:
                    run(store, args.exchange, seed, stop)
                except Exception:
                    logging.exception('Paper worker could not initialize')
                    store.transact(lambda p: p.update(error='Paper worker startup failed; see local logs'))
            threading.Thread(target=worker, daemon=True).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            httpd.server_close()


if __name__ == '__main__':
    main()
