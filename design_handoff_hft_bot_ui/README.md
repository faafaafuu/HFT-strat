# Handoff: Market Heat Radar — Telegram Bot & Web Dashboard Visual Redesign

## Overview
This is a visual redesign for the `HFT-strat` (Market Heat Radar) project — a Bybit signal-radar Telegram bot with a companion read-only FastAPI/Jinja2/HTMX web dashboard. The redesign covers six core screens (Dashboard, Signals, Signal Alert, Paper Trading, Strategy Lab, Settings) in **both** surfaces: the Telegram chat UI (message text + inline keyboards) and the web dashboard.

Goal: a laconic, practical, business-clean visual language, reused consistently across the Telegram messages and the web pages, without changing any of the underlying business logic, data fields, or command/callback structure.

## About the Design Files
The bundled file (`HFT Bot Design.dc.html`) is a **design reference built in HTML** — an interactive prototype for browsing all screens (toggle "Telegram-бот" / "Веб-дашборд" at the top, then the screen pills: Dashboard / Signals / Alert / Paper Trading / Strategy Lab / Settings). It is not production code and should not be copied into the app directly.

Your task is to **recreate this visual design in the project's real environments**:
- **Telegram surface**: `app/telegram/formatters.py` (message text/HTML) and `app/telegram/keyboards.py` (`InlineKeyboardMarkup`/`ReplyKeyboardMarkup` layouts). Telegram messages only support a small HTML subset (`<b>`, `<i>`, `<code>`, `<pre>`, `<a>`) — recreate the *hierarchy, grouping, and emphasis* shown in the mock (bold labels, grouped sections, monospace tables) using that subset, not arbitrary CSS.
- **Web surface**: the FastAPI/Jinja2 templates in `app/web/templates/` (`base.html`, `dashboard.html`, `signals.html`, `paper.html`, `strategy_lab.html`, etc.), styled with plain CSS (no existing frontend framework/build step in the repo — keep it dependency-free, e.g. a single stylesheet).

## Fidelity
**High-fidelity.** Colors, type, spacing, and card/table layouts in the mock are final — recreate them precisely within each surface's real constraints (Telegram's limited HTML formatting vs. full CSS on the web).

## Design Tokens

Colors:
- Background (app/page): `#f6f4ef`
- Card / surface: `#ffffff`
- Card border: `#e7e2d9`
- Divider: `#eee9e0`
- Sidebar background (web): `#faf8f4`
- Pill / secondary button background: `#f1efe9`
- Text (primary): `#23211c`
- Text (muted/labels): `#8a8478`
- Text (body/secondary): `#4b473e`
- Accent (brand mark, active tab): `#23211c` (near-black) with white text
- Accent (links/telegram bot avatar): `#2f6fed`
- Positive / long / online: `#1f9254` (bg tint `#e6f4ec`)
- Negative / short: `#d1453b` (bg tint `#faece9`)
- Warning (setup alert border/heading): `#b7791f` (border tint `#f0d9ad`)

Typography:
- UI / headings / labels: **Manrope**, weights 500–800
- Numeric data / tables / monospace fields: **IBM Plex Mono**, weights 400–600
- Base body size 13–13.5px, section headings 15–19px, stat numbers 22px

Shape / spacing:
- Card radius: 14–16px (Telegram message bubbles use `border-top-left-radius: 4px` to read as an incoming chat bubble)
- Buttons/pills radius: 7–9px
- Card padding: 16–22px
- Grid gaps: 6–14px
- Card border: 1px solid `#e7e2d9`; subtle shadow `0 1px 2–3px rgba(30,25,10,0.05)`

## Screens

Each screen below exists in **two** forms: a Telegram message + inline keyboard, and a web dashboard page. Field values in the mock are illustrative sample data — wire up real values from the existing services (`StatusService`, `SignalService`, `PaperService`, `StrategyLabService`, etc.), the fields themselves are unchanged from the current code.

### 1. Dashboard
- **Telegram** (`format_dashboard` / `nav("dashboard")`): bolded title "📊 Market Heat Radar", then a label/value list (Status, Pairs, Signals today/week, WS connections, RAM, Tasks, DB size, Open paper trades, Last heartbeat, Last signal), then Best pattern / Best pair / Uptime / Selected symbols as labeled paragraphs. Keyboard: single row `← Back · 🏠 Home · 🔄 Refresh`.
  - Redesign direction: keep as a two-column `label: value` block (label muted mono, value bold mono) instead of a flat `kv()` list, with a divider before the Best pattern/pair/uptime/symbols section.
- **Web** (`dashboard.html`): 3-column stat-card grid (Pairs Tracked, Signals Today, Signals 7d, Open Paper Trades, RAM/DB, Uptime), a 2-column panel for Best Pattern / Best Pair, and a wrapped chip row for Selected symbols (all shown truncated with a "+N" chip).

### 2. Signals
- **Telegram** (`format_recent_signals` / `signals_menu`): one block per signal — bold symbol, `DIRECTION • Score X/10`, muted timestamp — separated by blank lines, footer "Page N". Keyboard: one row per signal (`SYMBOL • DIRECTION • score/10`, callback opens detail), then a Prev/Next pager row (only shown when applicable), then Back/Home/Refresh.
- **Web** (`signals.html`): a table with columns SYMBOL / DIRECTION / SCORE / TIME; direction colored green (LONG) / red (SHORT); mono font for score and time.

### 3. Signal Alert
- **Telegram** (`format_signal_message` + `signal_alert_menu`): header is "📈 Potential Setup" normally, or "⚠️ Potential Setup" (amber, bordered card) when `signal.score >= settings.signals.strong_score`. Body: Pair / Direction / Score as a 2-col grid, Reasons as a bullet list, then Entry ref / Invalidation as a mono 2-col grid. Keyboard: exchange chart link row (Bybit/Hyperliquid + TradingView, real URLs, not callbacks), then Entered (green) / Ignore (red) row, then Details row.
- **Web**: no equivalent page exists (alerts are a Telegram-only concept — there's no `alert` route in `app/web/templates`). Do not build one unless product asks for it; the mock shows an explanatory empty state here for completeness.

### 4. Paper Trading
- **Telegram** (`format_paper_profiles` / `paper_profiles_menu`): one block per profile — bold name, then Balance / PnL (colored green/red) / Open / Trades / Status inline. Keyboard: one row per profile name, then Compare/Create Profile row, then Back/Home/Refresh. (Deeper profile screens — `paper_profile_menu`, `paper_profile_settings_menu`, trades lists — exist in the code; carry the same visual language into those if in scope.)
- **Web** (`paper.html`): 2-column card grid, one card per profile, each with a 2-col label/value block (Balance, PnL colored, Winrate, Open/Trades).

### 5. Strategy Lab
- **Telegram** (`_format_strategy_lab` default view / `strategy_lab_menu`): bold "🧪 Strategy Lab" title, "Strategies" bullet list with `key • ON/OFF` state, "Recent Jobs" bullet list (`id job_type • status`), footer note. Keyboard: 2-column grid — Active Strategies/Density Strategy, Backtests/Hyperopt, ML Status/Trend Status, Results/Density Events — then Back/Home/Refresh.
- **Web** (`strategy_lab.html`, plus its many sub-pages): 2-column layout — left "Strategies" list with ON/OFF state pills (green tint / neutral tint), right "Recent Jobs" list with status text (mono, amber for "running").

### 6. Settings
- **Telegram** (`format_settings` / `settings_menu`): bold "⚙️ Settings" title, then a mono label/value grid (Bot State colored green when Active, Min Score, Cooldown, Auto Select, Max Symbols, Notifications). Keyboard: 2-column +/- and on/off rows (Min Score −/+, Cooldown −5m/+5m, Auto Select/Notifications toggles, Max Symbols −5/+5), then Back/Home/Refresh.
- **Web**: a bordered list of setting rows, each `label` left / control right — text badge for Bot State, stepper (−/value/+) for Min Score, plain mono value for read-only fields (Cooldown, Max Symbols), and a real toggle switch component (green when on) for Auto-select and Notifications.

## Interactions & Behavior
- All navigation in Telegram is via `InlineKeyboardMarkup` callback data — no new callback routes are introduced by this redesign, only visual formatting of existing ones.
- The web dashboard is read-only (per README: "does not call Bybit REST/WebSocket"); it should keep its existing HTMX polling/refresh behavior — only the markup/CSS changes.
- Toggle switches on the web Settings page should reflect real boolean state from `Settings`/`runtime_settings` and post to the existing `/api` or form endpoints already wired in `routes.py`/`api.py` — no new endpoints needed.
- Sidebar/tab active states: exact match between whichever screen is open and its nav item highlighted (dark pill on Telegram-mode tab bar / dark row on web sidebar).

## State Management
No new client state beyond what already exists:
- Telegram: no state beyond Telegram's own message/callback lifecycle already implemented in `TelegramCommands`.
- Web: whatever the current Jinja/HTMX pages already track (page navigation, HTMX partial refresh targets). No SPA/client framework is introduced.

## Assets
No external images/icons. Emoji (📊 📈 🧪 ⚙️ ✓) are used exactly as in the current `formatters.py`/`keyboards.py` — do not introduce new emoji or icon sets.

## Files
- `HFT Bot Design.dc.html` — interactive HTML prototype of all 6 screens × 2 surfaces (open in a browser; use the top toggle + screen pills to browse states).
