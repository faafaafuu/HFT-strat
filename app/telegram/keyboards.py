from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from app.data.models import SignalModel
from app.telegram.charts import exchange_chart_url, tradingview_chart_url
from app.telegram.formatters import HeatRow


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Dashboard", callback_data="dashboard"),
                InlineKeyboardButton("📈 Signals", callback_data="signals:0"),
            ],
            [
                InlineKeyboardButton("📉 Statistics", callback_data="stats"),
                InlineKeyboardButton("📊 Heat Scanner", callback_data="scanner"),
            ],
            [
                InlineKeyboardButton("📊 Paper Portfolio", callback_data="paper"),
                InlineKeyboardButton("🧪 Strategy Lab", callback_data="strategy_lab"),
            ],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
        ]
    )


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["📊 Dashboard", "📈 Signals"],
            ["📉 Heat", "🧪 Paper"],
            ["🧪 Strategy Lab", "⚙️ Settings"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def nav(section: str, back: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("← Back", callback_data=back),
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("🔄 Refresh", callback_data=section),
            ]
        ]
    )


def paper_profiles_menu(profile_keys: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(key.title(), callback_data=f"pp:{key}")] for key in profile_keys]
    rows.append(
        [
            InlineKeyboardButton("Compare", callback_data="pcmp"),
            InlineKeyboardButton("Create Profile", callback_data="pnew"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("← Back", callback_data="home"),
            InlineKeyboardButton("🏠 Home", callback_data="home"),
            InlineKeyboardButton("🔄 Refresh", callback_data="paper"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def paper_profile_menu(profile_key: str, enabled: bool) -> InlineKeyboardMarkup:
    toggle = "Disable" if enabled else "Enable"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Open Trades", callback_data=f"pot:{profile_key}"),
                InlineKeyboardButton("Closed Trades", callback_data=f"pct:{profile_key}"),
            ],
            [
                InlineKeyboardButton("Stats", callback_data=f"pp:{profile_key}"),
                InlineKeyboardButton("Settings", callback_data=f"ps:{profile_key}"),
            ],
            [
                InlineKeyboardButton(toggle, callback_data=f"pt:{profile_key}:enabled"),
                InlineKeyboardButton("Reset Balance", callback_data=f"pr:{profile_key}"),
            ],
            [
                InlineKeyboardButton("← Back", callback_data="paper"),
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"pp:{profile_key}"),
            ],
        ]
    )


def paper_profile_settings_menu(profile_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Min Score -", callback_data=f"pset:{profile_key}:score:-1"),
                InlineKeyboardButton("Min Score +", callback_data=f"pset:{profile_key}:score:1"),
            ],
            [
                InlineKeyboardButton("Risk -", callback_data=f"pset:{profile_key}:risk:-0.1"),
                InlineKeyboardButton("Risk +", callback_data=f"pset:{profile_key}:risk:0.1"),
            ],
            [
                InlineKeyboardButton("Lev -", callback_data=f"pset:{profile_key}:lev:-1"),
                InlineKeyboardButton("Lev +", callback_data=f"pset:{profile_key}:lev:1"),
            ],
            [
                InlineKeyboardButton("SL -", callback_data=f"pset:{profile_key}:sl:-0.1"),
                InlineKeyboardButton("SL +", callback_data=f"pset:{profile_key}:sl:0.1"),
            ],
            [
                InlineKeyboardButton("TP -", callback_data=f"pset:{profile_key}:tp:-0.1"),
                InlineKeyboardButton("TP +", callback_data=f"pset:{profile_key}:tp:0.1"),
            ],
            [
                InlineKeyboardButton("Max Pos -", callback_data=f"pset:{profile_key}:maxpos:-1"),
                InlineKeyboardButton("Max Pos +", callback_data=f"pset:{profile_key}:maxpos:1"),
            ],
            [
                InlineKeyboardButton("Daily Loss -", callback_data=f"pset:{profile_key}:dl:-0.5"),
                InlineKeyboardButton("Daily Loss +", callback_data=f"pset:{profile_key}:dl:0.5"),
            ],
            [
                InlineKeyboardButton("Trailing On/Off", callback_data=f"pt:{profile_key}:trail"),
                InlineKeyboardButton("Breakeven On/Off", callback_data=f"pt:{profile_key}:be"),
            ],
            [
                InlineKeyboardButton("← Back", callback_data=f"pp:{profile_key}"),
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"ps:{profile_key}"),
            ],
        ]
    )


def paper_compare_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("← Back", callback_data="paper"),
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("🔄 Refresh", callback_data="pcmp"),
            ]
        ]
    )


def paper_trades_menu(profile_key: str, status: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("← Back", callback_data=f"pp:{profile_key}"),
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"p{status}:{profile_key}"),
            ]
        ]
    )


def strategy_lab_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Strategies", callback_data="sl:strategies"),
                InlineKeyboardButton("Backtests", callback_data="sl:backtests"),
            ],
            [
                InlineKeyboardButton("Results", callback_data="sl:results"),
                InlineKeyboardButton("Data", callback_data="sl:data"),
            ],
            [
                InlineKeyboardButton("Compare", callback_data="sl:compare"),
                InlineKeyboardButton("Diagnostics", callback_data="sl:diagnostics"),
            ],
            [
                InlineKeyboardButton("← Back", callback_data="home"),
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("🔄 Refresh", callback_data="strategy_lab"),
            ],
        ]
    )


def signals_menu(signals: list[SignalModel], page: int, page_size: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{signal.symbol} • {signal.direction} • {signal.score}/10",
                callback_data=f"signal:{signal.id}:{page}",
            )
        ]
        for signal in signals
    ]
    pager = []
    if page > 0:
        pager.append(InlineKeyboardButton("← Prev", callback_data=f"signals:{page - 1}"))
    if len(signals) == page_size:
        pager.append(InlineKeyboardButton("Next →", callback_data=f"signals:{page + 1}"))
    if pager:
        rows.append(pager)
    rows.append(
        [
            InlineKeyboardButton("← Back", callback_data="home"),
            InlineKeyboardButton("🏠 Home", callback_data="home"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"signals:{page}"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def signal_detail_menu(page: int, signal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("← Back", callback_data=f"signals:{page}"),
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"signal:{signal_id}:{page}"),
            ]
        ]
    )


def signal_alert_detail_menu(signal: SignalModel) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    charts = []
    exchange_url = exchange_chart_url(signal.exchange, signal.symbol)
    if exchange_url is not None:
        label, url = exchange_url
        charts.append(InlineKeyboardButton(label, url=url))
    tv_url = tradingview_chart_url(signal.exchange, signal.symbol)
    if tv_url is not None:
        charts.append(InlineKeyboardButton("TradingView", url=tv_url))
    if charts:
        rows.append(charts)
    rows.append(
        [
            InlineKeyboardButton("Entered", callback_data=f"signal_enter:{signal.id}"),
            InlineKeyboardButton("Ignore", callback_data=f"signal_ignore:{signal.id}"),
        ]
    )
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def signal_alert_menu(signal: SignalModel) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    charts = []
    exchange_url = exchange_chart_url(signal.exchange, signal.symbol)
    if exchange_url is not None:
        label, url = exchange_url
        charts.append(InlineKeyboardButton(label, url=url))
    tv_url = tradingview_chart_url(signal.exchange, signal.symbol)
    if tv_url is not None:
        charts.append(InlineKeyboardButton("TradingView", url=tv_url))
    if charts:
        rows.append(charts)
    rows.append(
        [
            InlineKeyboardButton("Entered", callback_data=f"signal_enter:{signal.id}"),
            InlineKeyboardButton("Ignore", callback_data=f"signal_ignore:{signal.id}"),
        ]
    )
    rows.append([InlineKeyboardButton("Details", callback_data=f"signal_details:{signal.id}")])
    return InlineKeyboardMarkup(rows)


def scanner_menu(rows: list[HeatRow]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                f"{idx}. {row.symbol} • {row.score:.1f}", callback_data=f"scanner_pair:{row.symbol}"
            )
        ]
        for idx, row in enumerate(rows, start=1)
    ]
    buttons.append(
        [
            InlineKeyboardButton("← Back", callback_data="home"),
            InlineKeyboardButton("🏠 Home", callback_data="home"),
            InlineKeyboardButton("🔄 Refresh", callback_data="scanner"),
        ]
    )
    return InlineKeyboardMarkup(buttons)


def scanner_pair_menu(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("← Back", callback_data="scanner"),
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"scanner_pair:{symbol}"),
            ]
        ]
    )


def settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Min Score -", callback_data="settings:set:min_score:-1"),
                InlineKeyboardButton("Min Score +", callback_data="settings:set:min_score:1"),
            ],
            [
                InlineKeyboardButton("Cooldown -5m", callback_data="settings:set:cooldown:-5"),
                InlineKeyboardButton("Cooldown +5m", callback_data="settings:set:cooldown:5"),
            ],
            [
                InlineKeyboardButton(
                    "Auto Select On/Off", callback_data="settings:toggle:auto_select"
                ),
                InlineKeyboardButton(
                    "Notifications On/Off", callback_data="settings:toggle:notifications"
                ),
            ],
            [
                InlineKeyboardButton("Max Symbols -5", callback_data="settings:set:max_symbols:-5"),
                InlineKeyboardButton("Max Symbols +5", callback_data="settings:set:max_symbols:5"),
            ],
            [
                InlineKeyboardButton("← Back", callback_data="home"),
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("🔄 Refresh", callback_data="settings"),
            ],
        ]
    )
