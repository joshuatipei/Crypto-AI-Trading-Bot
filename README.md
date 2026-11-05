# XRP Quant Paper Desk — Version 1 research implementation

Python 3.12+, standard-library runtime, XRP spot only. Starts with **$10,000 virtual capital**. There is **no live order implementation, account connection, leverage, shorting, martingale, or LLM execution path**. No profit claims. Strategy rules and thresholds are unvalidated research baselines.

## Quick start

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m unittest discover -s tests -v
```

Run an authenticated dashboard without a trading/data worker:

```sh
# Supply DASHBOARD_TOKEN securely in your shell/environment settings.
# Use a cryptographically random secret, at least 24 characters; never commit it.
.venv/bin/xrpbot serve --state state/paper.sqlite
```

The dashboard binds loopback port 8080. Username: `paper`; password: your `DASHBOARD_TOKEN`. It starts empty and visibly stale until data arrives. No credentials are stored in source or SQLite. See [deployment](docs/DEPLOYMENT.md) for TLS and iPhone access. Never expose Basic authentication over public plain HTTP.

## Historical ingestion and research

Public adapters: Coinbase Exchange `XRP-USD`, Binance.US `XRPUSDT`. Each verifies public product status. Geographic access, API support, pair availability, and filters must be checked at runtime. No private account API is called. Required egress hosts: `api.exchange.coinbase.com`, `api.binance.us`.

```sh
# Choose UTC minute-aligned START and END epoch seconds, with END in the past.
.venv/bin/xrpbot ingest --exchange coinbase --start "$START" --end "$END" --output data/xrp-1m.csv
.venv/bin/xrpbot backtest --csv data/xrp-1m.csv --strategy trend --output reports/trend.json
.venv/bin/xrpbot backtest --csv data/xrp-1m.csv --walk-forward --stability --output reports/research.json
.venv/bin/xrpbot ml-research --csv data/xrp-1m.csv --output reports/ml.json
```

For all five timeframes, provide at least **202 × 240 consecutive one-minute bars** (roughly 34 days) before expecting signals. For walk-forward, provide more than 74,401 rows; default folds use 60,000 initial training/history rows, one purged row, then 14,400 OOS rows. Warmup is not a performance observation. Acquire multiple years for rallies, crashes and sideways periods; API rate limits may make bulk acquisition slow. V1 does not claim to have obtained those datasets.

CSV schema: `timestamp,open,high,low,close,volume`; timestamps are UTC minute-opening epoch seconds. Prices must be positive, volumes nonnegative, values finite and OHLC envelopes valid. Duplicates, gaps, ordering errors, and incomplete requested coverage fail closed. Ingestion saves exchange/product metadata alongside CSV; backtest reports include SHA-256 of their input. An arbitrary imported CSV is not automatically verified as authentic XRP data.

Backtests use a signal from completed bars, execute at the next bar's open, and model both sides' fees, half-spread, slippage, protective stops, and worse gap opens. Same-bar stop exits are conservative; OHLC cannot resolve queue position. Simulated participation is capped at 1% of bar volume and unfilled remainder is canceled. Results include marked equity, forced final liquidation, net P/L, drawdown, win rate, profit factor, daily Sharpe/Sortino, expectancy, fees, exposure, a cost-adjusted buy-and-hold baseline, and seeded trade-sequence permutations. Undefined ratios are `null`, not invented values. Sharpe uses UTC close-to-close daily returns and 365-day annualization; short samples are unreliable.

**Default 0.6% per-side fees, 10 bps spread and 5 bps slippage are scenario assumptions, not verified historical exchange fees.** Override with `--config path/to/config.json` for backtest or initial `serve` ledger; JSON keys match `Config` in `xrpbot/engine.py`. Example nonsecret override: `{"fee":0.004,"slippage_bps":10,"stop_atr":2.5}`. All omitted keys retain defaults. Historical tier/time-varying fee schedules remain future work. Stability tests explicitly vary fees and ATR multiples. OOS evaluation uses fixed rules; training indices do not imply a fitted strategy or optimization.

## Live-data paper operation

```sh
# Seed with recent continuous XRP history ending near the latest closed minute.
.venv/bin/xrpbot serve --paper-csv data/xrp-1m.csv --exchange coinbase --state state/coinbase.sqlite
```

The worker verifies public product status/precision, refreshes closed candles and quotes, then simulates fills into a transactional SQLite ledger. Each signal timestamp is processed once across restarts. Use a separate ledger/CSV for each venue; USD and USDT are not interchangeable. Default dashboard currency formatting is USD-style; Binance.US balances are virtual USDT, not actual USD.

Pause blocks new entries; protective exits remain active. Emergency stop latches entry shutdown and requests virtual liquidation on the next valid quote. Risk halts cannot be reset from the dashboard: stop the process, inspect the ledger/logs and create a new reviewed simulation ledger. Never edit a running database to bypass risk limits. No fill is invented during stale quotes or an API outage. A local simulated stop cannot guarantee exit during gaps or connectivity loss.

## Architecture

| Module | Responsibility |
|---|---|
| `data.py`, `contracts.py` | Public exchange adapters, validation, completed aggregation, product filters, optional depth imbalance helper |
| `signals.py` | EMA 9/21/50/200, RSI, MACD, ADX, ATR, session VWAP, Bollinger bands, volume, local zones/sweeps, multi-timeframe baseline selection |
| `engine.py` | Virtual market/IOC limit fills, precision/minimums, costs, position sizing, protective stops and risk state, SQLite transactions |
| `research.py`, `ml.py` | Next-open simulation, OOS folds, fee/ATR stability, permutations, purged chronological research-only logistic model |
| `paper.py` | Public-price polling, freshness enforcement, idempotent paper decisions, persisted account |
| `web.py`, `dashboard.html` | Authenticated mobile dashboard and pause/resume/emergency controls |
| `cli.py` | Ingestion, backtesting, optional ML evaluation and service entry points |

Strategies: trend pullbacks, consolidation breakout/retest with volume, ranging mean reversion, aligned momentum, and tentative sweep reversals. Selection uses transparent conditions and deterministic priority, not a weighted confidence score. Conditions are displayed as evidence, not calibrated signal probabilities. Global exhaustion/weak-structure exits and ATR stops apply. RSI uses a rolling 14-change gain/loss ratio; ADX/ATR use Wilder smoothing; indicators are seeded from bounded historical windows. Zones and sweeps are heuristics, not confirmed liquidity or institutional order flow.

Risk defaults: 0.5% equity per entry, 2% aggregate risk cap (one spot position), 2% daily marked loss cutoff, 10% peak-equity drawdown cutoff, 12 entries/day, 15-minute cooldown, roundtrip cost budget 2%, 90-second quote age, 2×5m ATR stop. Sizing includes entry fees, modeled stop costs and available cash. No automatic averaging down. A risk cutoff latches and flattens at the next valid execution opportunity. Actual gap loss can exceed the sizing estimate.

See [actual validation and remaining work](docs/VALIDATION.md). This is an initial reviewable research system, not an institutionally validated autonomous investment product.
