# market-heat-signal-bot

Async signal radar for Bybit linear futures. The bot does not place orders and does not use private trading API keys. It selects liquid futures symbols, monitors price/trades/orderbook/open interest/funding, detects overheated market patterns, sends Telegram alerts, stores signals in SQLite, and evaluates outcomes after fixed horizons.

## What It Does

- Connects to Bybit public linear futures WebSocket.
- Auto-selects liquid symbols by 24h turnover, spread, and 1% orderbook depth.
- Tracks price, public trades, orderbook, spread, depth, funding, and open interest.
- Detects MVP patterns:
  - `oi_pump_price_move`
  - `stop_hunt_sweep`
- Scores every candidate from 0 to 10.
- Sends only signals above `signals.min_score`.
- Applies per-symbol cooldown to avoid duplicates.
- Saves signals and market snapshots to SQLite.
- Tracks outcomes after `5/15/30/60/180` minutes by default.
- Can run multiple local paper trading portfolios with separate balances, risk rules, fees, slippage, partial TP, trailing stop, and statistics.
- Provides Telegram commands and a persistent lower menu for dashboard, signals, heat, paper, and settings.

Hyperliquid, liquidation feed, density events, dashboard, CSV export, and manual entry tracking are planned v2/v3 items.

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

This starts two services:

- `bot` / container `market-heat-signal-bot`: Telegram radar, signal engine, paper trading.
- `web` / container `market-heat-signal-bot-web`: read-only FastAPI dashboard.

Persistent runtime data is stored on the host:

```text
./data/bot.sqlite3
./logs/
./backups/
```

The container mounts these directories into `/app/data`, `/app/logs`, and `/app/backups`. `docker compose down`, `docker compose up`, and `docker compose up --build` do not remove this data.

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
make logs-dev
make logs-web-dev
make restart-dev
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

Authentication is required via Basic Auth:

```env
WEB_USERNAME=admin
WEB_PASSWORD=replace_with_a_long_password
WEB_PORT=8080
```

Pages:

- `/` dashboard
- `/signals`
- `/paper`
- `/trades`
- `/analytics`
- `/performance`

API:

```text
GET /api/status
GET /api/signals
GET /api/paper/profiles
GET /api/paper/trades/open
GET /api/paper/trades/closed
GET /api/analytics/summary
GET /api/performance
```

Analytics responses are cached briefly to avoid expensive recomputation on every
browser refresh. The web service is read-only in this phase; live trading and
private exchange APIs are not implemented.

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
