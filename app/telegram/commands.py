from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC
from typing import Any

from telegram import CallbackQuery, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.config import PaperProfileConfig, save_settings
from app.data.repositories import SignalRepository
from app.market.features import FeatureSnapshot
from app.paper.account import PaperAccountService
from app.paper.statistics import (
    paper_profile_summary,
    paper_profile_trades,
    paper_profiles_summary,
    paper_summary,
)
from app.signals.patterns import detect_patterns
from app.signals.scoring import score_signal
from app.telegram.formatters import (
    HeatRow,
    format_config,
    format_dashboard,
    format_ignored,
    format_marked_entered,
    format_paper_compare,
    format_paper_portfolio,
    format_paper_profile,
    format_paper_profile_settings,
    format_paper_profiles,
    format_paper_trades,
    format_recent_signals,
    format_scanner,
    format_scanner_pair,
    format_settings,
    format_signal_detail,
    format_stats,
    since_today,
    since_week,
)
from app.telegram.keyboards import (
    main_menu,
    main_reply_keyboard,
    nav,
    paper_compare_menu,
    paper_profile_menu,
    paper_profile_settings_menu,
    paper_profiles_menu,
    paper_trades_menu,
    scanner_menu,
    scanner_pair_menu,
    settings_menu,
    signal_alert_detail_menu,
    signal_alert_menu,
    signal_detail_menu,
    signals_menu,
)
from app.utils.time import utc_now

SIGNALS_PAGE_SIZE = 6
STALE_CALLBACK_MARKERS = (
    "Query is too old",
    "query id is invalid",
    "response timeout expired",
)


class TelegramCommands:
    def __init__(self, service: Any) -> None:
        self.service = service
        self._callback_semaphore = asyncio.Semaphore(4)
        self._view_cache: dict[str, tuple[float, str, InlineKeyboardMarkup]] = {}

    async def start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        if update.message is not None:
            await update.message.reply_text(
                "Menu enabled.",
                reply_markup=main_reply_keyboard(),
            )
        await self._send_or_edit(update, "Select a section.", main_menu(), title=True)

    async def help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self._send_or_edit(update, "Select a section.", main_menu(), title=True)

    async def menu_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        text = (update.message.text if update.message else "").strip()
        if "Dashboard" in text:
            await self.show_dashboard(update)
        elif "Signals" in text:
            await self.show_signals(update, page=0)
        elif "Heat" in text:
            await self.show_scanner(update)
        elif "Paper" in text:
            await self.show_paper(update)
        elif "Settings" in text:
            await self.show_settings(update)
        else:
            await self._send_or_edit(update, "Select a section.", main_menu(), title=True)

    async def status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self.show_dashboard(update)

    async def signals(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self.show_signals(update, page=0)

    async def stats(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self.show_stats(update)

    async def stats_today(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self.show_stats(update, since=since_today().replace(tzinfo=UTC))

    async def stats_week(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self.show_stats(update, since=since_week().replace(tzinfo=UTC))

    async def top_pairs(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self.show_stats(update)

    async def top_patterns(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self.show_stats(update)

    async def paper(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self.show_paper(update)

    async def config(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await self._send_or_edit(update, format_config(self.service.settings), nav("config"))

    async def pause(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self.service.paused = True
        await self.show_settings(update)

    async def resume(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self.service.paused = False
        await self.show_settings(update)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.data is None:
            return
        answered = await self._answer_callback(query)
        if not answered:
            return
        if not await self._authorize(update, answer_callback=False):
            return
        data = query.data
        self._schedule_callback_task(update, context, data)

    async def _process_callback(self, update: Update, data: str) -> None:
        async with self._callback_semaphore:
            await self._dispatch_callback(update, data)

    async def _dispatch_callback(self, update: Update, data: str) -> None:
        if data == "home":
            await self._send_or_edit(update, "Select a section.", main_menu(), title=True)
        elif data == "dashboard":
            await self.show_dashboard(update)
        elif data.startswith("signals:"):
            await self.show_signals(update, page=_int_part(data, 1, 0))
        elif data.startswith("signal:"):
            parts = data.split(":")
            signal_id = int(parts[1])
            page = int(parts[2]) if len(parts) > 2 else 0
            await self.show_signal_detail(update, signal_id, page)
        elif data.startswith("signal_details:"):
            await self.show_signal_alert_detail(update, int(data.split(":", 1)[1]))
        elif data.startswith("signal_enter:"):
            await self.mark_signal_entered(update, int(data.split(":", 1)[1]))
        elif data.startswith("signal_ignore:"):
            await self.ignore_signal(update, int(data.split(":", 1)[1]))
        elif data == "stats":
            await self.show_stats(update)
        elif data == "config":
            await self._send_or_edit(update, format_config(self.service.settings), nav("config"))
        elif data == "scanner":
            await self.show_scanner(update)
        elif data.startswith("scanner_pair:"):
            await self.show_scanner_pair(update, data.split(":", 1)[1])
        elif data == "paper":
            await self.show_paper(update)
        elif data.startswith("pp:"):
            await self.show_paper_profile(update, data.split(":", 1)[1])
        elif data == "pcmp":
            await self.show_paper_compare(update)
        elif data == "pnew":
            await self.create_paper_profile(update)
        elif data.startswith("ps:"):
            await self.show_paper_profile_settings(update, data.split(":", 1)[1])
        elif data.startswith("pt:"):
            await self.toggle_paper_profile_setting(update, data)
        elif data.startswith("pset:"):
            await self.adjust_paper_profile_setting(update, data)
        elif data.startswith("pr:"):
            await self.reset_paper_profile(update, data.split(":", 1)[1])
        elif data.startswith("pot:"):
            await self.show_paper_trades(update, data.split(":", 1)[1], status="OPEN")
        elif data.startswith("pct:"):
            await self.show_paper_trades(update, data.split(":", 1)[1], status="CLOSED")
        elif data == "settings":
            await self.show_settings(update)
        elif data.startswith("settings:"):
            saved = await self.apply_setting(data)
            warning = None
            if not saved:
                warning = "Config file is read-only. Runtime value changed, but it was not saved."
            await self.show_settings(update, warning=warning)

    def _schedule_callback_task(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        data: str,
    ) -> None:
        coroutine = self._process_callback(update, data)
        name = f"telegram_callback:{data[:40]}"
        if context.application is not None:
            context.application.create_task(coroutine, update=update, name=name)
            return
        task = asyncio.create_task(coroutine, name=name)
        task.add_done_callback(lambda done: self._log_callback_task_result(done, data))

    def _log_callback_task_result(self, task: asyncio.Task[None], data: str) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                self.service.log.warning("Telegram callback task failed data=%s: %s", data, exc)

    async def show_dashboard(self, update: Update) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            today_count = await repo.count_since(since_today().replace(tzinfo=UTC))
            week_count = await repo.count_since(since_week().replace(tzinfo=UTC))
            summary = await repo.summary()
            last_signal_time = await repo.last_signal_time()
        text = format_dashboard(
            online=self.service.is_online,
            pairs_count=len(self.service.symbols),
            signals_today=today_count,
            signals_week=week_count,
            best_pattern=summary.get("best_pattern"),
            best_pair=summary.get("best_pair"),
            uptime=utc_now() - self.service.started_at,
            last_heartbeat=self.service.last_heartbeat,
            active_websocket_connections=self.service.active_websocket_connections,
            selected_symbols=self.service.symbols,
            last_signal_time=last_signal_time,
        )
        await self._send_or_edit(update, text, nav("dashboard"))

    async def show_signals(self, update: Update, page: int) -> None:
        page = max(0, page)
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signals = await repo.list_recent(
                limit=SIGNALS_PAGE_SIZE, offset=page * SIGNALS_PAGE_SIZE
            )
        await self._send_or_edit(
            update,
            format_recent_signals(signals, page, SIGNALS_PAGE_SIZE),
            signals_menu(signals, page, SIGNALS_PAGE_SIZE),
        )

    async def show_signal_detail(self, update: Update, signal_id: int, page: int) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signal = await repo.get_signal_with_outcomes(signal_id)
        if signal is None:
            await self._send_or_edit(
                update, "Signal not found.", signal_detail_menu(page, signal_id)
            )
            return
        await self._send_or_edit(
            update, format_signal_detail(signal), signal_detail_menu(page, signal_id)
        )

    async def show_signal_alert_detail(self, update: Update, signal_id: int) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signal = await repo.get_signal_with_outcomes(signal_id)
        if signal is None:
            await self._send_or_edit(update, "Signal not found.", nav("home"))
            return
        await self._send_or_edit(
            update, format_signal_detail(signal), signal_alert_detail_menu(signal)
        )

    async def mark_signal_entered(self, update: Update, signal_id: int) -> None:
        now = utc_now()
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signal = await repo.get_signal(signal_id)
            if signal is None:
                await self._send_or_edit(update, "Signal not found.", nav("home"))
                return
            price = self._manual_entry_price(signal.exchange, signal.symbol, signal.entry_price)
            signal = await repo.mark_entered_manual(signal_id, price, now)
        if signal is None:
            await self._send_or_edit(update, "Signal not found.", nav("home"))
            return
        await self._send_or_edit(update, format_marked_entered(signal), signal_alert_menu(signal))

    async def ignore_signal(self, update: Update, signal_id: int) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            signal = await repo.ignore_signal(signal_id)
        if signal is None:
            await self._send_or_edit(update, "Signal not found.", nav("home"))
            return
        await self._send_or_edit(update, format_ignored(signal), signal_alert_menu(signal))

    async def show_stats(self, update: Update, since=None) -> None:
        async with self.service.database.session() as session:
            repo = SignalRepository(session)
            summary = await repo.summary(since=since)
        await self._send_or_edit(update, format_stats(summary), nav("stats"))

    async def show_scanner(self, update: Update) -> None:
        rows = self._heat_rows()
        await self._send_or_edit(update, format_scanner(rows), scanner_menu(rows))

    async def show_scanner_pair(self, update: Update, symbol: str) -> None:
        rows = {row.symbol: row for row in self._heat_rows(limit=50)}
        row = rows.get(symbol)
        if row is None:
            row = HeatRow(
                symbol=symbol,
                score=0,
                price_change_5m_pct=None,
                oi_change_15m_pct=None,
                volume_spike_ratio=0,
                spread_pct=None,
                met=[],
                missing=["No active data"],
            )
        await self._send_or_edit(update, format_scanner_pair(row), scanner_pair_menu(symbol))

    async def show_settings(self, update: Update, warning: str | None = None) -> None:
        await self._send_or_edit(
            update,
            format_settings(self.service.settings, self.service.paused, warning=warning),
            settings_menu(),
        )

    async def show_paper(self, update: Update) -> None:
        cache_key = "paper:profiles"
        cached = self._cached(cache_key)
        if cached is not None:
            text, markup = cached
            await self._send_or_edit(update, text, markup)
            return
        await self._send_or_edit(update, "Loading...", nav("paper"))
        async with self.service.database.session() as session:
            profiles = await paper_profiles_summary(session)
            if not profiles:
                summary = await paper_summary(session)
                text = format_paper_portfolio(summary)
                markup = nav("paper")
            else:
                text = format_paper_profiles(profiles)
                markup = paper_profiles_menu([str(item["profile_key"]) for item in profiles])
        self._store_cache(cache_key, text, markup)
        await self._send_or_edit(update, text, markup)

    async def show_paper_profile(self, update: Update, profile_key: str) -> None:
        cache_key = f"paper:profile:{profile_key}"
        cached = self._cached(cache_key)
        if cached is not None:
            text, markup = cached
            await self._send_or_edit(update, text, markup)
            return
        await self._send_or_edit(update, "Loading...", nav("paper", back="paper"))
        async with self.service.database.session() as session:
            summary = await paper_profile_summary(session, profile_key)
        text = format_paper_profile(summary)
        markup = paper_profile_menu(profile_key, bool(summary.get("enabled")))
        self._store_cache(cache_key, text, markup)
        await self._send_or_edit(update, text, markup)

    async def show_paper_compare(self, update: Update) -> None:
        cache_key = "paper:compare"
        cached = self._cached(cache_key)
        if cached is not None:
            text, markup = cached
            await self._send_or_edit(update, text, markup)
            return
        await self._send_or_edit(update, "Loading...", paper_compare_menu())
        async with self.service.database.session() as session:
            profiles = await paper_profiles_summary(session)
        text = format_paper_compare(profiles)
        markup = paper_compare_menu()
        self._store_cache(cache_key, text, markup)
        await self._send_or_edit(update, text, markup)

    async def show_paper_profile_settings(self, update: Update, profile_key: str) -> None:
        async with self.service.database.session() as session:
            summary = await paper_profile_summary(session, profile_key)
        await self._send_or_edit(
            update,
            format_paper_profile_settings(summary),
            paper_profile_settings_menu(profile_key),
        )

    async def show_paper_trades(self, update: Update, profile_key: str, status: str) -> None:
        async with self.service.database.session() as session:
            trades = await paper_profile_trades(session, profile_key, status=status, limit=10)
        title = "🧪 Open Trades" if status == "OPEN" else "🧪 Closed Trades"
        menu_status = "ot" if status == "OPEN" else "ct"
        await self._send_or_edit(
            update,
            format_paper_trades(trades, title),
            paper_trades_menu(profile_key, menu_status),
        )

    async def toggle_paper_profile_setting(self, update: Update, data: str) -> None:
        parts = data.split(":")
        if len(parts) != 3:
            await self.show_paper(update)
            return
        profile_key, field = parts[1], parts[2]
        profile_config = self.service.settings.paper.profiles.get(profile_key)
        if profile_config is None:
            await self._send_or_edit(update, "Profile not found.", nav("paper", back="paper"))
            return
        values = profile_config.model_dump()
        if field == "enabled":
            values["enabled"] = not bool(values["enabled"])
        elif field == "trail":
            values["trailing_enabled"] = not bool(values["trailing_enabled"])
        elif field == "be":
            values["breakeven_enabled"] = not bool(values["breakeven_enabled"])
        else:
            await self.show_paper_profile(update, profile_key)
            return
        await self._save_paper_profile_config(profile_key, values)
        await self.show_paper_profile_settings(update, profile_key)

    async def adjust_paper_profile_setting(self, update: Update, data: str) -> None:
        parts = data.split(":")
        if len(parts) != 4:
            await self.show_paper(update)
            return
        profile_key, field, delta_raw = parts[1], parts[2], parts[3]
        profile_config = self.service.settings.paper.profiles.get(profile_key)
        if profile_config is None:
            await self._send_or_edit(update, "Profile not found.", nav("paper", back="paper"))
            return
        values = profile_config.model_dump()
        delta = float(delta_raw)
        mapping = {
            "score": ("min_score", 1, 10, int),
            "risk": ("risk_per_trade_pct", 0.0, 10.0, float),
            "lev": ("leverage", 1.0, 50.0, float),
            "sl": ("stop_loss_pct", 0.1, 10.0, float),
            "tp": ("take_profit_pct", 0.1, 20.0, float),
            "maxpos": ("max_open_positions", 1, 50, int),
            "dl": ("max_daily_loss_pct", 0.1, 100.0, float),
        }
        target = mapping.get(field)
        if target is None:
            await self.show_paper_profile_settings(update, profile_key)
            return
        key, low, high, caster = target
        values[key] = caster(min(high, max(low, float(values[key]) + delta)))
        await self._save_paper_profile_config(profile_key, values)
        await self.show_paper_profile_settings(update, profile_key)

    async def reset_paper_profile(self, update: Update, profile_key: str) -> None:
        async with self.service.database.session() as session:
            from sqlalchemy import select

            from app.data.models import PaperAccountModel, PaperProfileModel, PaperTradeModel

            open_trade = await session.scalar(
                select(PaperTradeModel).where(
                    PaperTradeModel.profile_key == profile_key,
                    PaperTradeModel.status == "OPEN",
                )
            )
            if open_trade is not None:
                await self._send_or_edit(
                    update,
                    "Cannot reset profile while it has open trades.",
                    paper_profile_menu(profile_key, True),
                )
                return
            profile = await session.scalar(
                select(PaperProfileModel).where(PaperProfileModel.profile_key == profile_key)
            )
            if profile is None:
                await self._send_or_edit(update, "Profile not found.", nav("paper", back="paper"))
                return
            profile.current_balance = profile.initial_balance
            profile.equity = profile.initial_balance
            profile.net_profit = 0.0
            profile.peak_equity = profile.initial_balance
            profile.max_drawdown_pct = 0.0
            account = await session.scalar(
                select(PaperAccountModel).where(PaperAccountModel.name == f"profile:{profile_key}")
            )
            if account is not None:
                account.balance = profile.initial_balance
                account.equity = profile.initial_balance
                account.net_profit = 0.0
                account.peak_equity = profile.initial_balance
                account.max_drawdown_pct = 0.0
        self._invalidate_paper_cache(profile_key)
        await self.show_paper_profile(update, profile_key)

    async def create_paper_profile(self, update: Update) -> None:
        base_key = self.service.settings.paper.default_profile
        base_config = self.service.settings.paper.profiles.get(base_key)
        if base_config is None:
            base_config = next(iter(self.service.settings.paper.profiles.values()))
        existing = set(self.service.settings.paper.profiles)
        idx = 1
        while f"custom_{idx}" in existing:
            idx += 1
        profile_key = f"custom_{idx}"
        values = base_config.model_dump()
        values["name"] = f"Custom {idx}"
        values["enabled"] = False
        profile_config = PaperProfileConfig.model_validate(values)
        self.service.settings.paper.profiles[profile_key] = profile_config
        async with self.service.database.session() as session:
            service = PaperAccountService(session, self.service.settings.paper)
            await service.save_profile_config(profile_key, profile_config)
        self._invalidate_paper_cache()
        await self.show_paper_profile(update, profile_key)

    async def apply_setting(self, data: str) -> bool:
        parts = data.split(":")
        if len(parts) < 3:
            return True
        action = parts[1]
        key = parts[2]
        if action == "toggle":
            if key == "auto_select":
                self.service.settings.symbols.auto_select = (
                    not self.service.settings.symbols.auto_select
                )
            elif key == "notifications":
                self.service.settings.telegram.notifications_enabled = (
                    not self.service.settings.telegram.notifications_enabled
                )
        elif action == "set" and len(parts) == 4:
            delta = int(parts[3])
            if key == "min_score":
                self.service.settings.signals.min_score = min(
                    10,
                    max(1, self.service.settings.signals.min_score + delta),
                )
            elif key == "cooldown":
                self.service.settings.signals.cooldown_minutes_per_symbol = max(
                    0,
                    self.service.settings.signals.cooldown_minutes_per_symbol + delta,
                )
            elif key == "max_symbols":
                self.service.settings.symbols.max_symbols = min(
                    200,
                    max(1, self.service.settings.symbols.max_symbols + delta),
                )
        saved = save_settings(self.service.settings, self.service.config_path)
        if not saved:
            self.service.log.warning(
                "config save failed path=%s reason=read_only_or_unwritable",
                self.service.config_path,
            )
        return saved

    async def _save_paper_profile_config(
        self,
        profile_key: str,
        values: dict[str, object],
    ) -> None:
        profile_config = PaperProfileConfig.model_validate(values)
        self.service.settings.paper.profiles[profile_key] = profile_config
        async with self.service.database.session() as session:
            service = PaperAccountService(session, self.service.settings.paper)
            await service.save_profile_config(profile_key, profile_config)
        self._invalidate_paper_cache(profile_key)

    def _cached(self, key: str) -> tuple[str, InlineKeyboardMarkup] | None:
        cached = self._view_cache.get(key)
        if cached is None:
            return None
        expires_at, text, markup = cached
        if expires_at <= time.monotonic():
            self._view_cache.pop(key, None)
            return None
        return text, markup

    def _store_cache(self, key: str, text: str, markup: InlineKeyboardMarkup) -> None:
        self._view_cache[key] = (time.monotonic() + 8.0, text, markup)

    def _invalidate_paper_cache(self, profile_key: str | None = None) -> None:
        prefixes = ["paper:profiles", "paper:compare"]
        if profile_key is not None:
            prefixes.extend([f"paper:profile:{profile_key}"])
        for key in list(self._view_cache):
            if key in prefixes:
                self._view_cache.pop(key, None)

    def _heat_rows(self, limit: int = 10) -> list[HeatRow]:
        if self.service.feature_store is None:
            return []
        rows: list[HeatRow] = []
        for symbol in self.service.symbols:
            snapshot = self.service.feature_store.snapshot(
                "bybit",
                symbol,
                sweep_lookback_minutes=self.service.settings.thresholds.sweep_lookback_minutes,
                sweep_return_minutes=self.service.settings.thresholds.sweep_return_minutes,
            )
            if snapshot is None:
                continue
            rows.append(self._heat_row(snapshot))
        rows.sort(key=lambda item: item.score, reverse=True)
        return rows[:limit]

    def _heat_row(self, snapshot: FeatureSnapshot) -> HeatRow:
        thresholds = self.service.settings.thresholds
        met: list[str] = []
        missing: list[str] = []
        score = 0.0

        price_change = snapshot.price_change_5m_pct
        if price_change is not None and abs(price_change) >= thresholds.price_change_5m_pct:
            met.append("Price Move")
            score += min(2.0, abs(price_change) / thresholds.price_change_5m_pct * 1.5)
        else:
            missing.append("Price Move")

        oi_change = snapshot.oi_change_15m_pct
        if oi_change is not None and oi_change >= thresholds.oi_change_15m_pct:
            met.append("OI Growth")
            score += min(2.0, oi_change / thresholds.oi_change_15m_pct * 1.5)
        else:
            missing.append("OI Growth")

        if snapshot.volume_spike_ratio >= thresholds.volume_spike_multiplier:
            met.append("Volume Spike")
            score += min(
                2.0, snapshot.volume_spike_ratio / thresholds.volume_spike_multiplier * 1.5
            )
        else:
            missing.append("Volume Spike")

        if (
            snapshot.spread_pct is not None
            and snapshot.spread_pct <= self.service.settings.symbols.max_spread_pct
        ):
            met.append("Spread OK")
            score += 1.5
        else:
            missing.append("Spread OK")

        if snapshot.returned_after_low_sweep or snapshot.returned_after_high_sweep:
            met.append("Sweep Return")
            score += 2.5
        else:
            missing.append("Sweep Return")

        for candidate in detect_patterns(snapshot, thresholds):
            score = max(score, float(score_signal(candidate, thresholds)))

        return HeatRow(
            symbol=snapshot.symbol,
            score=min(10.0, score),
            price_change_5m_pct=price_change,
            oi_change_15m_pct=oi_change,
            volume_spike_ratio=snapshot.volume_spike_ratio,
            spread_pct=snapshot.spread_pct,
            met=met,
            missing=missing,
        )

    def _manual_entry_price(self, exchange: str, symbol: str, fallback: float) -> float:
        if self.service.feature_store is None:
            return fallback
        return self.service.feature_store.latest_price(exchange, symbol) or fallback

    async def _send_or_edit(
        self,
        update: Update,
        text: str,
        reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup,
        title: bool = False,
    ) -> None:
        if title:
            text = "<b>📊 Market Heat Radar</b>\n\n" + text
        if update.callback_query and update.callback_query.message:
            try:
                await update.callback_query.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except BadRequest as exc:
                if "Message is not modified" not in str(exc) and not _is_stale_callback(exc):
                    raise
                if _is_stale_callback(exc):
                    self.service.log.warning("stale Telegram edit ignored: %s", exc)
            return
        if update.message:
            await update.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

    async def _authorize(self, update: Update, answer_callback: bool = False) -> bool:
        if self.service.is_authorized_update(update):
            return True
        user = update.effective_user
        chat = update.effective_chat
        self.service.log.warning(
            "unauthorized Telegram update user_id=%s chat_id=%s",
            user.id if user else None,
            chat.id if chat else None,
        )
        if answer_callback and update.callback_query is not None:
            await self._answer_callback(update.callback_query, "Unauthorized", show_alert=True)
        elif update.message is not None:
            await update.message.reply_text("Unauthorized.")
        return False

    async def _answer_callback(
        self,
        query: CallbackQuery,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        try:
            await query.answer(text=text, show_alert=show_alert)
        except BadRequest as exc:
            if not _is_stale_callback(exc):
                raise
            self.service.log.warning("stale Telegram callback ignored: %s", exc)
            return False
        return True


def _int_part(data: str, index: int, default: int) -> int:
    try:
        return int(data.split(":")[index])
    except (IndexError, ValueError):
        return default


def _is_stale_callback(exc: BadRequest) -> bool:
    message = str(exc)
    return any(marker in message for marker in STALE_CALLBACK_MARKERS)
