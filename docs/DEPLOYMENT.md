# Continuous paper operation

A small Linux VM with Python 3.12+, persistent storage and outbound HTTPS can run this single-process application. No GPU, database service, paid LLM, or exchange credentials are needed. CPU/RAM needs scale with candle history and ledger size; benchmark before choosing a plan. No hosting-price claim is made.

1. Install into `/opt/xrp-paper` as an unprivileged service user and create `.venv` with the README commands. Create writable `/var/lib/xrp-paper`, owned by that user, mode 0700.
2. Obtain validated recent history for the chosen venue and place it at `/var/lib/xrp-paper/xrp-1m.csv`. Fetch enough for all timeframes; replace the example service's venue if appropriate.
3. Generate `DASHBOARD_TOKEN` securely and put its environment assignment in `/etc/xrp-paper/dashboard.env`, outside the checkout, mode 0600 readable only by the intended service/root. Never paste it into logs, issue descriptions or Git. The user chooses and manages this secret.
4. Review/install `deployment/xrp-paper.service` with correct paths. Use systemd to enable/restart it and inspect journal output. It starts loopback HTTP; it does not bind a public interface.
5. Install a TLS reverse proxy such as Caddy with your owned DNS name and the example Caddyfile. Only allow public ports 80/443 and your administrative access. Keep 8080 private. Access the domain from Safari and authenticate as `paper`. Do not rely on an onboarding localhost preview.
6. Verify the authenticated state response, a fresh heartbeat, the intended venue and balances, then exercise pause/resume and emergency stop with a disposable simulation ledger. Check that a restart preserves cash, fills, processed signals and halts. Monitor stale/error state and logs externally.

Back up SQLite using SQLite's backup API or while the service is stopped; protect backups as account history. One worker owns each ledger. A single JSON state document stores trade history, so rotate/archive reviewed ledgers for long runs. In-process logs and dashboard events are the V1 alerts; external email/push alert delivery and a watchdog are not implemented.

TLS proxy, systemd, DNS, public mobile access, restore drills, browser rendering, long-duration operation and real exchange connectivity must be validated on the target host. These deployment examples were reviewed but not installed in this cloud workspace. Funded execution would require a separate implementation, risk/security review and explicit manual authorization; this release cannot submit it.
