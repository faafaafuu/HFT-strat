# market-heat-signal-bot

Async signal radar for Bybit linear futures. The bot does not place orders and does not use private trading API keys. It selects liquid futures symbols, monitors price/trades/orderbook/open interest/funding, detects overheated market patterns, sends Telegram alerts, stores signals in SQLite, and evaluates outcomes after fixed horizons.

## What It Does

- Connects to Bybit public linear futures WebSocket.
- Auto-selects liquid symbols by 24h turnover, spread, and 1% orderbook depth.
- Tracks price, public trades, orderbook, spread, depth, funding, and open interest.
- Detects MVP patterns:
  - `oi_pump_price_move`
  - `stop_hunt_sweep`
  - `density_strategy`
  - `channel_4_touch`
- Scores every candidate from 0 to 10.
- Sends only signals above `signals.min_score`.
- Applies per-symbol cooldown to avoid duplicates.
- Saves signals and market snapshots to SQLite.
- Tracks outcomes after `5/15/30/60/180` minutes by default.
- Can run multiple local paper trading portfolios with separate balances, risk rules, fees, slippage, partial TP, trailing stop, and statistics.
- Provides Telegram commands and a persistent lower menu for dashboard, signals, heat, paper, Strategy Lab, and settings.
- Provides a protected FastAPI/Jinja/HTMX dashboard with Strategy Lab, backtest, hyperopt, diagnostics, and performance pages.
- Stores density events, strategy instances, backtest results, jobs, and ML model metadata in SQLite.

Hyperliquid, liquidation feed, CSV export, and real execution are not active in this phase.

## Install Locally

```bash
cd /root/market-heat-signal-bot
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and `config.yaml`, then run:

```bash
python -m app.main
```

## Telegram Bot Token

1. Open Telegram and message `@BotFather`.
2. Run `/newbot`.
3. Choose a name and username.
4. Copy the token into `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:your_real_token
TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_ALLOWED_USER_IDS=your_telegram_user_id
WEB_USERNAME=admin
WEB_PASSWORD=replace_with_a_long_password
WEB_SESSION_SECRET=replace_with_a_long_random_session_secret
WEB_PORT=8080
```

To get `TELEGRAM_CHAT_ID`, send any message to your bot, then call:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates"
```

Use the `chat.id` from the response.

`TELEGRAM_ALLOWED_USER_IDS` is recommended. If it is set, only those Telegram users can run commands and press callback buttons. If it is empty, the bot falls back to `TELEGRAM_CHAT_ID`.

## Docker Run

```bash
cd /root/market-heat-signal-bot
cp .env.example .env
# edit .env
docker compose up -d --build
```

This starts three services:

- `bot` / container `market-heat-signal-bot`: Telegram radar, signal engine, paper trading.
- `web` / container `market-heat-signal-bot-web`: read-only FastAPI dashboard.
- `worker` / container `market-heat-signal-bot-worker`: background job runner for history downloads, backtests, hyperopt, ML training, and density analysis.

Persistent runtime data is stored on the host:

```text
./data/bot.sqlite3
./logs/
./backups/
./models/
```

The container mounts these directories into `/app/data`, `/app/logs`, `/app/backups`, and `/app/models`. `docker compose down`, `docker compose up`, and `docker compose up --build` do not remove this data.

## Development Mode

Dev mode mounts project code into the container and restarts the bot automatically when Python files change. You do not need to rebuild after every code edit unless dependencies changed.

```bash
docker compose -f docker-compose.dev.yml up
```

Stop dev mode:

```bash
docker compose -f docker-compose.dev.yml down
```

Logs:

```bash
docker logs -f market-heat-signal-bot-dev
docker logs -f market-heat-signal-bot-web-dev
```

Makefile shortcuts:

```bash
make dev
make dev-down
make prod
make logs
make logs-web
make logs-worker
make logs-dev
make logs-web-dev
make logs-worker-dev
make restart-dev
make worker-restart
make backup
make verify-persistence
```

`make backup` creates a SQLite backup in `./backups` when `./data/bot.sqlite3` exists.

## Web Dashboard

The web dashboard is a separate FastAPI service using Jinja2 and HTMX. It reads
SQLite through application services only and does not call Bybit REST/WebSocket.

Open:

```text
http://SERVER_IP:8080/
```

Authentication uses a normal login page and a signed session cookie:

```env
WEB_USERNAME=admin
WEB_PASSWORD=replace_with_a_long_password
WEB_SESSION_SECRET=replace_with_a_long_random_session_secret
WEB_PORT=8080
```

Routes:

- `GET /login`
- `POST /login`
- `POST /logout`

If the dashboard is exposed through the included nginx TLS proxy, the default HTTPS endpoint is:

```text
https://SERVER_IP:9443/
```

Pages:

- `/` dashboard
- `/signals`
- `/paper`
- `/trades`
- `/analytics`
- `/analytics/why-losing`
- `/performance`
- `/strategy-lab`
- `/strategy-lab/strategies`
- `/strategy-lab/instances`
- `/strategy-lab/backtests`
- `/strategy-lab/hyperopt`
- `/strategy-lab/compare`
- `/strategy-lab/density`

API:

```text
GET /api/status
GET /api/signals
GET /api/paper/profiles
GET /api/paper/trades/open
GET /api/paper/trades/closed
GET /api/analytics/summary
GET /api/performance
GET /api/strategy-lab/strategies
GET /api/strategy-lab/instances
GET /api/strategy-lab/density/events
```

Analytics responses are cached briefly to avoid expensive recomputation on every
browser refresh. The web service is read-only in this phase; live trading and
private exchange APIs are not implemented.

## Strategy Lab

Strategy Lab separates strategy logic from paper profiles. One strategy can run
as multiple independent instances with different thresholds and paper profiles:

```yaml
strategy_instances:
  density_conservative:
    strategy_key: density_strategy
    enabled: true
    min_score: 8
    paper_profile: conservative
    config:
      min_density_usd: 1000000
      max_distance_pct: 0.25
      min_lifetime_sec: 20
      require_absorption: true
      require_trend_alignment: true

  density_aggressive:
    strategy_key: density_strategy
    enabled: true
    min_score: 7
    paper_profile: aggressive
    config:
      min_density_usd: 500000
      max_distance_pct: 0.4
      min_lifetime_sec: 8
      require_absorption: false
      require_trend_alignment: false
```

Runtime enable/disable changes from web are stored in SQLite settings storage,
not written back into `config.yaml`.

Open the lab:

```text
/strategy-lab
```

Useful pages:

- `/strategy-lab/instances` — edit instance thresholds and density parameters.
- `/strategy-lab/backtests` — queue historical backtests.
- `/strategy-lab/hyperopt` — queue parameter search.
- `/strategy-lab/compare` — compare paper profiles and recent backtests.
- `/strategy-lab/density` — inspect density events and density summaries.
- `/analytics/why-losing` — diagnose weak strategy instances, profiles, symbols, hours, score buckets, and exit statuses.

Backtest and hyperopt forms can target a specific `strategy_instance_id`. When
an instance is selected, the job uses that instance's strategy key and config as
base parameters.

Queued jobs are stored in SQLite and are processed by the `worker` service in
Docker. For a one-shot local run without Docker, process one pending job with:

```bash
make job-worker
```

Current strategy registry includes:

- `oi_pump_price_move`
- `stop_hunt_sweep`
- `micro_stop_hunt_reclaim`
- `oi_momentum_scalper`
- `failed_breakout_fade`
- `trend_pullback_scalper`
- `density_strategy`
- `channel_4_touch`

## 4-Touch Channel Strategy

`channel_4_touch` trades consolidation channels drawn from three pivots. Two
pivots land on one boundary (points 1 and 3), one on the opposite boundary
(point 2). The second touch of the anchor line is what proves the line is real
rather than accidental, so the first touch of the *opposite* boundary after
point 3 - the fourth touch overall - is the entry.

- Long when the fourth touch is on the lower boundary, short when it is on the
  upper one.
- A touch only counts as a wick. A candle that *closes* past a boundary by more
  than `breakout_buffer_pct` breaks the channel, and a broken channel never
  produces another signal.
- Once built the channel is a constant: the boundaries never widen or narrow.
- The stop clears the touch wick and the boundary, floored at `stop_pct` and
  rejected above `max_stop_pct`. The target is `take_pct` or the opposite
  boundary, whichever is closer. Setups below `min_rr` are skipped.

Unlike the other strategies this one reads OHLC structure rather than aggregate
features, so it needs `FeatureSnapshot.candles`. Only the backtest engine fills
that today - the live feature store keeps ticks, not candles - so the strategy
is registered but disabled for live signals, per the backtest-first plan.

### Timeframe Matters More Than Any Other Parameter

The default 1-1.5% stop and 3-5% target only fit channels several percent wide.
On BTCUSDT 1m the median channel is about 0.2% wide, so every fourth touch is
correctly rejected on risk/reward and the backtest reports zero trades. Run this
strategy on H1 and above, or sweep `stop_pct`/`take_pct` down along with the
timeframe:

```bash
python tools/download_history.py --symbol BTCUSDT --timeframe 1h --days 720
python tools/run_backtest.py --strategy channel_4_touch --symbol BTCUSDT --timeframe 1h --days 720
```

Timeframe is not part of the hyperopt grid because it changes which candles get
loaded. Pass several and the optimizer sweeps each in turn, pooling the results:
`timeframe=15m,1h,4h` in the Strategy Lab form or in the job params.

### Position Management

`simulate_exit` supports a trailing stop, a move to breakeven, and a partial take
profit, with the same semantics paper trading uses. All three are **off by
default** so a plain run reproduces the plain stop/take result. Turn them on per
run:

```bash
python tools/run_backtest.py --strategy channel_4_touch --symbol BTCUSDT \
  --timeframe 4h --days 720 \
  -p trailing_enabled=true -p trailing_activation_rr=1 -p trailing_distance_pct=2
```

`-p KEY=VALUE` passes any strategy or exit-rule parameter and repeats.

For this strategy management *hurts*: on 4h over 720 days BTC drops from +20.6%
to +8.7% with breakeven and +10.9% with trailing. The share of trades reaching
target falls from 30% to 16-18% - at a ~30% winrate the result rides on the few
trades that reach target, and trailing cuts exactly those while leaving the
losers full size. Measure before enabling it anywhere.

## Density Strategy

`density_strategy` trades only from real orderbook-derived density events. It
tracks large bid/ask levels, lifetime, distance to price, refills, pulls, eaten
levels, and absorption-like events without keeping unbounded orderbook history
in RAM.

Supported scenarios:

- Density bounce
- Eaten density breakout
- Spoof pull reversal
- Absorption at density

Stored tables:

- `density_events`
- `density_levels`

Density backtests require saved L2/density history. If only candles are present,
the backtest returns `insufficient_density_history` instead of fabricating
density signals from OHLC data.

## Trend And ML Filters

The trend filter adds global, daily, and local trend context using price
structure, VWAP/EMA slope as supporting context, impulse, volatility regime, and
range/trend classification. It writes `trend_alignment_score` into signal
context and can adjust the final score conservatively.

The ML layer is offline and advisory only. It can train a simple tabular quality
model from stored signals, outcomes, paper/backtest results, density features,
trend context, and recent performance. It never sends orders and never makes an
entry decision by itself. If an active model is valid, it can only adjust signal
score within a bounded range.

Model artifacts are stored under:

```text
./models/
```

Metadata is stored in:

```text
ml_model_runs
```

## Configure Symbols

Auto-select is enabled by default:

```yaml
symbols:
  auto_select: true
  max_symbols: 30
  min_24h_volume_usd: 20000000
  max_spread_pct: 0.05
  min_orderbook_depth_usd_1pct: 500000
```

For manual symbols:

```yaml
symbols:
  auto_select: false
  manual_list:
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT
```

## Bybit Performance

Bybit public market data is split across multiple WebSocket connections to reduce
ping timeouts under high orderbook/trade throughput. These settings can be tuned
in `config.yaml` without changing strategy thresholds:

```yaml
exchanges:
  bybit:
    ws_topics_per_connection: 20
    orderbook_depth_limit: 100
    orderbook_process_interval_ms: 250
```

Lower `orderbook_process_interval_ms` reacts faster to book changes but costs more
CPU. Higher values reduce CPU while keeping trades/tickers live.

## Read Signals

Signal fields:

- `direction` is an idea direction, not an order.
- `score` is confidence from configured heuristics.
- `reasons` explain which conditions fired.
- `invalid level` and `first target` are rough review levels, not trading instructions.
- Always verify context manually: BTC direction, news, nearby orderbook density, and volatility.

## Paper Trading

Paper trading is fully local. The bot never sends real orders and does not need exchange API keys.
One Telegram bot can run several independent paper profiles on the same signal stream.

Enable it:

```yaml
app:
  mode: paper_trading

paper:
  enabled: true
  default_profile: aggressive
  profiles:
    conservative:
      name: Conservative
      enabled: true
      initial_balance: 2000
      min_score: 8
      risk_per_trade_pct: 0.3
      leverage: 3
      stop_loss_pct: 0.4
      take_profit_pct: 1.2
    aggressive:
      name: Aggressive
      enabled: true
      initial_balance: 2000
      min_score: 7
      risk_per_trade_pct: 0.7
      leverage: 7
      stop_loss_pct: 0.5
      take_profit_pct: 1.5
  taker_fee_pct: 0.055
  slippage_pct: 0.01
```

In `signal_only`, signals are stored and alerted but no virtual trades are opened. In `paper_trading`, every enabled profile evaluates each signal independently. A signal with score `8` can open both Conservative and Aggressive trades, while a score `7` opens only profiles whose `min_score` allows it.

Trade lifecycle:

1. Signal is generated and saved.
2. If paper mode is active, each enabled profile checks its own min score, symbol/pattern filters, open-position limits, and daily loss limit.
3. Matching profiles open isolated virtual trades with profile-specific SL, TP, leverage, and risk.
4. Entry includes configured slippage.
5. Position size is calculated from profile balance, risk, and stop distance.
6. Ticks from public market data check TP, SL, timeout, partial TP, breakeven, and trailing stop.
7. On close, fees and slippage are applied, only that profile balance is updated, and profile equity curve is written.

Position example:

```text
Balance      $2000
Risk         0.5% = $10
Stop         0.5%
Risk size    $10 / 0.005 = $2000 notional
Leverage cap $2000 * 5 = $10000 notional
Position     min($2000, $10000) = $2000
```

Balance update example:

```text
Start balance     $2000.00
Trade 1 TP        +$29.40
Trade 2 SL        -$11.10
Trade 3 TP        +$28.90
New balance       $2047.20
Net profit        +$47.20
```

Paper database schema:

```text
paper_accounts
  id, name, initial_balance, balance, equity, net_profit,
  max_drawdown_pct, peak_equity, created_at, updated_at

paper_profiles
  id, profile_key, name, enabled, initial_balance, current_balance,
  equity, settings_json, net_profit, max_drawdown_pct, peak_equity,
  created_at, updated_at

paper_trades
  id, account_id, profile_id, profile_key, signal_id, exchange, symbol, direction, pattern, score,
  entry_price, stop_price, take_price, leverage, position_size_usd,
  remaining_size_usd, risk_usd, opened_at, closed_at, status,
  exit_price, pnl_usd, fees_usd, pnl_pct, realized_rr,
  partial_closed, partial_exit_price, partial_pnl_usd,
  trailing_activated, high_watermark, low_watermark

paper_equity_curve
  id, account_id, profile_id, profile_key, trade_id, timestamp,
  balance, equity, net_profit, drawdown_pct

paper_daily_stats
  id, account_id, date, balance, net_profit, trades, wins,
  losses, winrate_pct, max_drawdown_pct, updated_at

runtime_settings
  key, value_json, updated_at

strategy_analysis
  id, created_at, period_start, period_end, profile_key, pattern,
  symbol, total_trades, winrate, profit_factor, expectancy,
  avg_mfe, avg_mae, conclusion_json
```

## Storage And Retention

Runtime storage is configured in `config.yaml`:

```yaml
database:
  url: sqlite+aiosqlite:////app/data/bot.sqlite3

storage:
  persist_market_snapshots: true
  market_snapshot_interval_sec: 60
  keep_raw_ticks_minutes: 30
  keep_orderbook_events_days: 30
  keep_market_snapshots_days: 90
```

In memory, the bot keeps bounded rolling buffers only. Raw trades, prices, and OI use `deque(maxlen=...)` plus time retention. Full orderbook history is not kept in RAM; only the latest aggregated orderbook metrics are retained. Longer-horizon outcomes fall back to persisted `market_snapshots`.

SQLite backups are created at startup, before migrations, after startup, every 24 hours, and on graceful shutdown. Backup names use:

```text
bot_YYYY-MM-DD_HH-MM.sqlite3
```

## Telegram Commands

The Telegram interface uses a persistent lower menu plus inline section buttons, so daily use does not require typing commands. `/start` installs the lower menu:

- `📊 Dashboard`
- `📈 Signals`
- `📉 Heat`
- `🧪 Paper`
- `🧪 Strategy Lab`
- `⚙️ Settings`

Sections use `← Back`, `🏠 Home`, and `🔄 Refresh`. Long signal lists are paginated, and settings such as min score, cooldown, auto symbol selection, max symbols, and notifications can be changed from buttons. Runtime changes are saved to SQLite in `runtime_settings`, not written to `config.yaml`.

The `🧪 Paper` section shows all paper profiles, per-profile cards, open/closed trades, profile settings, and profile comparison:

```text
Profile | Balance | PnL | WR | PF | DD | Trades
```

Paper profile settings changed from Telegram are saved in SQLite/runtime storage, not written to `config.yaml`. This avoids write errors in read-only Docker mounts.

Signal alerts include chart and action buttons:

- `Bybit` or `Hyperliquid`
- `TradingView`
- `Entered`
- `Ignore`
- `Details`

`Entered` stores `manual_entry_price`, `manual_entered_at`, and changes the signal status to `ENTERED_MANUAL`. `Ignore` changes the signal status to `IGNORED`; the signal remains in general statistics but is excluded from manual-entry analysis.

```text
/start
/status
/signals
/stats
/stats_today
/stats_week
/top_pairs
/top_patterns
/paper
/config
/pause
/resume
/help
```

`/status` shows uptime, last heartbeat, active websocket connections, selected symbols, last signal time, RAM usage, active asyncio tasks, DB size, and open paper trades. `/pause` keeps collecting market snapshots but stops creating new signals. `/resume` enables signal creation again.

## Web Dashboard

The web dashboard runs as a separate FastAPI/Jinja/HTMX service and reads only SQLite/services. It does not call Bybit or any private trading API.

Start production services:

```bash
docker compose up -d --build
```

Open:

```text
http://SERVER_IP:8080
```

HTTPS is available through the nginx reverse proxy. On this server, port 443 is already used by another Caddy service, so the dashboard proxy is exposed on port 9443. With the default IP-only setup, the project uses a self-signed certificate, so browsers will show a certificate warning.

Generate a self-signed certificate for the server IP:

```bash
make tls-cert TLS_HOST=84.247.166.53
docker compose up -d nginx
```

Open:

```text
https://SERVER_IP:9443/
```

Check HTTPS health:

```bash
make https-health
curl -k https://SERVER_IP:9443/health
```

For a trusted browser certificate without warnings, point a domain to the server and replace `certs/dashboard.crt` / `certs/dashboard.key` with a real certificate for that domain, or use a Caddy/Traefik/Certbot setup.

Health endpoints are public and do not require Basic Auth:

```bash
curl http://SERVER_IP:8080/health
curl http://SERVER_IP:8080/api/health
```

Expected response:

```json
{"status":"ok"}
```

Useful commands:

```bash
make web-logs
make web-restart
make web-health
```

If `WEB_USERNAME` and `WEB_PASSWORD` are set in `.env`, browser login is required for dashboard pages and protected API endpoints.

## Strategy Lab

Strategy Lab adds a strategy registry and offline research workflow. Current registered strategies:

- `oi_pump_price_move`
- `stop_hunt_sweep`
- `micro_stop_hunt_reclaim`
- `oi_momentum_scalper`
- `failed_breakout_fade`
- `trend_pullback_scalper`

Strategy profiles are configured under `strategy_profiles` and map strategies to paper profiles for analysis:

```yaml
strategy_profiles:
  profiles:
    scalping_safe:
      enabled: true
      strategies: [stop_hunt_sweep, micro_stop_hunt_reclaim, failed_breakout_fade]
      min_score: 8
      symbols: auto
      paper_profile: conservative
```

Web page:

```text
/strategy-lab
```

Diagnostics page:

```text
/analytics/diagnostics
```

Download Bybit historical candles:

```bash
make download-history SYMBOL=BTCUSDT TIMEFRAME=1m DAYS=30
```

Run backtest:

```bash
make backtest STRATEGY=micro_stop_hunt_reclaim SYMBOL=BTCUSDT DAYS=30
```

Run one queued Web job:

```bash
make job-worker
```

Backtest, hyperopt, history download, ML training, and density analysis run as
background jobs so Telegram callbacks and market-data loops are not blocked.
Backtest/hyperopt uses stored candles from SQLite, applies fees and slippage,
checks TP/SL/timeout on future candles only, and stores results in:

- `historical_candles`
- `backtest_runs`
- `backtest_trades`
- `backtest_equity_curve`
- `jobs`

Job types:

- `download_history`
- `run_backtest`
- `run_hyperopt`
- `train_ml_model`
- `run_density_analysis`

Live trading, private trading endpoints, and real orders are not implemented in this phase.

## Statistics

Outcomes are calculated for configured horizons:

```yaml
outcomes:
  horizons_minutes: [5, 15, 30, 60, 180]
  tp_levels_pct: [0.5, 1.0, 1.5]
  sl_levels_pct: [0.3, 0.5, 0.7]
```

For LONG:

- MFE = `(max_price - entry_price) / entry_price`
- MAE = `(entry_price - min_price) / entry_price`

For SHORT:

- MFE = `(entry_price - min_price) / entry_price`
- MAE = `(max_price - entry_price) / entry_price`

## Tests

```bash
cd /root/market-heat-signal-bot
. .venv/bin/activate
pytest
```

Current tests cover scoring, outcomes, Telegram callback/auth behavior, paper risk calculations, and multi-profile paper routing.

Manual persistence check:

```bash
make verify-persistence
```

## Tooling

```bash
make lint
make typecheck
make test
make security
make audit
make check
```

`make audit` uses `pip-audit` and needs network access to vulnerability databases.

## Architecture Graphs

The project includes Graphify plus a local AST review generator. Graphify writes its raw code graph to `graphify-out/graph.json`; the local generator writes review-ready files to `project_analysis/`.

Update all graphs and reports:

```bash
make graph
```

Run the graph build and print the architecture review:

```bash
make graph-review
```

Generated files:

```text
project_analysis/
  architecture_graph.dot
  architecture_graph.svg
  imports_graph.dot
  imports_graph.svg
  dependency_graph.dot
  dependency_graph.svg
  graphify_graph.json
  findings.md
  architecture_review.md
```

Open the SVG files in a browser. If Graphviz is not installed, the `.dot` files are still generated and can be opened with Graphviz-compatible tools. The review looks for cyclic imports, high fan-in/fan-out modules, large classes/functions, unused-file candidates, unused-function candidates, and package-level dependency pressure.

## Limitations

- MVP supports Bybit only.
- No trading or private API actions are implemented.
- Liquidation and density patterns are not active in MVP.
- OI change needs historical polling time after startup.
- Public API/WebSocket outages can delay or miss events.
- Signal quality must be validated statistically before using it in a process.

## Risk Notice

This project is a market research and alerting tool. It is not financial advice, does not guarantee profit, and does not replace risk management. Crypto futures are high-risk instruments; use the output as data for manual review only.
