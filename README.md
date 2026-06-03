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
- Can run local paper trading simulation with virtual balance, fees, slippage, partial TP, trailing stop, and portfolio statistics.
- Provides Telegram commands including `/stats`, `/signals`, `/top_pairs`, `/top_patterns`, `/pause`, `/resume`.

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

In production Docker mode, SQLite data is stored in the named Docker volume `market_heat_signal_bot_market_storage`. Dev mode uses the local `./storage` directory for easier inspection.

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
```

Makefile shortcuts:

```bash
make dev
make dev-down
make prod
make logs
make logs-dev
make restart-dev
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

## Read Signals

Signal fields:

- `direction` is an idea direction, not an order.
- `score` is confidence from configured heuristics.
- `reasons` explain which conditions fired.
- `invalid level` and `first target` are rough review levels, not trading instructions.
- Always verify context manually: BTC direction, news, nearby orderbook density, and volatility.

## Paper Trading

Paper trading is fully local. The bot never sends real orders and does not need exchange API keys.

Enable it:

```yaml
app:
  mode: paper_trading

paper:
  initial_balance: 2000
  leverage: 5
  risk_per_trade_pct: 0.5
  max_open_positions: 3
  auto_trade_min_score: 8
  stop_pct: 0.5
  take_pct: 1.5
  taker_fee_pct: 0.055
  slippage_pct: 0.01
```

In `signal_only`, signals are stored and alerted but no virtual trades are opened. In `paper_trading`, signals with `score >= paper.auto_trade_min_score` open virtual trades automatically.

Trade lifecycle:

1. Signal is generated and saved.
2. If paper mode is active and score is high enough, a virtual trade opens.
3. Entry includes configured slippage.
4. Position size is calculated from account risk and stop distance.
5. Ticks from public market data check TP, SL, partial TP, and trailing stop.
6. On close, fees and slippage are applied, balance is updated, and equity curve is written.

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

paper_trades
  id, account_id, signal_id, exchange, symbol, direction, pattern, score,
  entry_price, stop_price, take_price, leverage, position_size_usd,
  remaining_size_usd, risk_usd, opened_at, closed_at, status,
  exit_price, pnl_usd, fees_usd, pnl_pct, realized_rr,
  partial_closed, partial_exit_price, partial_pnl_usd,
  trailing_activated, high_watermark, low_watermark

paper_equity_curve
  id, account_id, trade_id, timestamp, balance, equity, net_profit, drawdown_pct

paper_daily_stats
  id, account_id, date, balance, net_profit, trades, wins,
  losses, winrate_pct, max_drawdown_pct, updated_at
```

## Telegram Commands

The Telegram interface uses an inline menu, so daily use does not require typing commands. `/start` opens:

- `📊 Dashboard`
- `📈 Signals`
- `📉 Statistics`
- `📊 Heat Scanner`
- `⚙️ Settings`

Sections use `← Back`, `🏠 Home`, and `🔄 Refresh`. Long signal lists are paginated, and settings such as min score, cooldown, auto symbol selection, max symbols, and notifications can be changed from buttons. Changes are saved to `config.yaml`.

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

`/status` shows uptime, last heartbeat, active websocket connections, selected symbols, and last signal time. `/pause` keeps collecting market snapshots but stops creating new signals. `/resume` enables signal creation again.

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

Current tests cover scoring, outcomes, and paper risk calculations.

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
