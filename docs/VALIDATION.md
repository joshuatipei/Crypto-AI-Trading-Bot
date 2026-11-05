# Validation record — 2026-11-05

## What actually ran

- Python 3.12.14 virtual environment created; editable package installed successfully with `python -m pip install --no-deps -e .`. Runtime has no third-party dependencies. Build isolation obtained setuptools with TLS verification intact.
- `python -m unittest discover -s tests -v`: **35 tests passed, 0 failures, 0 skips**, including actual loopback HTTP requests. Tests use synthetic data, temporary ledgers and mocked public exchange responses where noted.
- `python -m compileall -q xrpbot tests`: passed.
- Installed `xrpbot --help`: passed.
- Installed service CLI started in a subprocess with an ephemeral token and temporary SQLite ledger. An authenticated HTTP state request verified paper mode, $10,000 cash and explicit stale status. Process was stopped afterward. A permanent dashboard is not running.
- Installed backtest CLI ran on **48,500 synthetic flat one-minute candles** covering full multi-timeframe warmup. Actual output: zero trades, zero net P/L, zero fees, undefined win rate/Sharpe/Sortino represented as null. This is a pipeline check and expected cash decision, **not historical XRP performance**. Input/report files are ignored local artifacts under `data/` and `reports/`.
- Installed ML CLI completed on the same synthetic fixture. Research-only output has `trading_enabled: false`. No predictive or economic claim is supported by this fixture.
- Read-only public product requests to both Coinbase Exchange and Binance.US failed with proxy tunnel **403 Forbidden**. Real pair availability, remote response schemas and real candles/quotes could not be verified here. Configured network policy was enforced and lacked the two exchange API domains. Draft additions were saved; draft saving alone does not activate access.

## What the tests establish

Data validation rejects bad envelopes, gaps, duplicates and non-finite values. Aggregation excludes unfinished groups. Synthetic constant-market indicator values and complete five-timeframe evidence are checked. Prefix tests ensure the simulator does not use changed future bars for earlier executions; these are useful regression safeguards, not a mathematical proof against all future leakage.

Execution tests verify next-open fills, fees, conservative gap-stop fills, fee-aware risk sizing, cost/minimum/precision limits, capacity-limited IOC fills, no pyramiding, duplicate identifiers, daily/drawdown latches, trade-frequency limits and pause with protective exits. SQLite rollback/reopen is checked. Fresh quote protective exits remain active when candles are stale; missing quotes cannot produce fictitious fills.

Research tests check disjoint purged OOS boundaries, actual fixed-rule fold execution, report arithmetic, reproducible trade permutations, and that ML scaling/weights do not change when only OOS observations change. HTTP tests verify unauthorized requests fail, the dashboard document is served, control requests require their custom header, and an emergency halt cannot be resumed from the web interface.

## Missing validation and limitations

1. **No real-market backtest or profitability result.** Obtain authentic exchange-specific multi-regime XRP data with provenance, missing-candle handling decisions and actual historical fees. Run separate strategy reports, fixed-rule OOS folds, cost/ATR stability and Monte Carlo analyses. Review the number of real trades and sample length before interpreting ratios.
2. **Public exchange integration unverified remotely.** Activate required network access, then retry pair metadata, candle pagination, quotes, precision/minimums, and outage recovery. Authentication/account/trading permissions are deliberately unused.
3. Fee/spread/slippage inputs are fixed scenarios; time-varying historical fee tiers, dynamic market-impact models, queue reconstruction, persistent resting limit orders and asynchronous exchange order reconciliation are not implemented. V1's orders are synchronous virtual market/IOC fills; partial fills cancel remainder. No real execution exists.
4. Strategy rules, regime thresholds, support/resistance and sweep heuristics are candidates, not validated predictive scores. Breakout/retest and reversal rules need independent historical event audits. Strategy selection priority is fixed, not empirically learned. RSI uses a rolling gain/loss window. Completed four-hour bars impose a long warmup.
5. The ML module is a deliberately small chronological logistic research baseline, not a production AI model. Gradient boosting, nested walk-forward model selection, calibration, robust uncertainty tests and strategy-level net-cost lift remain future work. No model can activate trading.
6. Optional order-book imbalance is a read-only helper and unit-tested with mock depth; it is not polled by the default worker or used in decisions. No spoofing inference is made.
7. Dashboard HTTP/API authentication and controls were tested; visual Safari/iPhone rendering, TLS, systemd/Caddy deployment, push/email alerts, long-duration recovery, backup restore and concurrent-load hardening were not exercised. Do not expose the Python server directly to the Internet. Token rotation and external monitoring are operator responsibilities.
8. State is one growing JSON ledger inside SQLite, adequate for an initial paper prototype; long-lived production needs normalized fills/equity tables, bounded retention and migrations. One worker per ledger is required. GUI uses USD-style formatting; Binance.US simulation actually uses USDT quote units.
9. Daily loss is based on last observed equity at the UTC boundary, with overnight gaps included. Stops and loss cutoffs can overshoot during price gaps and outages. Local paper stops are not exchange-hosted orders. Emergency stop flattens only at the next valid quote.
10. Historical reports include final liquidation and cost-adjusted buy/hold; OOS folds start new virtual accounts and use fixed rules, not fitted parameter selection. No aggregated OOS deployment approval is generated.

## Next development steps

Activate the API-domain draft and provide a dashboard token securely in environment settings. Ingest recent data and verify live-data paper operation. Then acquire archival multi-regime XRP datasets and audited historical cost schedules, benchmark each independent strategy against cash and buy/hold on untouched periods, and reject strategies without stable net-cost evidence. Add integration fixtures from real API responses, production observability/storage, and target-host deployment tests before any sustained unattended simulation.

Any future real-money module requires explicit manual authorization and a separate implementation/review. This repository cannot submit real orders.
