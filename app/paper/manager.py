from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import PaperConfig, PaperProfileConfig
from app.data.database import Database
from app.data.models import PaperDailyStatsModel, PaperProfileModel, PaperTradeModel
from app.logger import get_logger
from app.paper.account import PaperAccountService, profile_config_from_model
from app.paper.executor import PaperExecutor
from app.paper.risk import apply_exit_slippage, fee_for_notional, pnl_for_exit
from app.utils.time import utc_now


class PaperTradeNotifier:
    async def send_paper_opened(self, trade: PaperTradeModel, balance: float) -> None: ...

    async def send_paper_closed(
        self, trade: PaperTradeModel, balance: float, winrate: float
    ) -> None: ...


class PaperTradeManager:
    def __init__(
        self,
        database: Database,
        config: PaperConfig,
        notifier: PaperTradeNotifier | None = None,
    ) -> None:
        self.database = database
        self.config = config
        self.notifier = notifier
        self.log = get_logger("paper_manager")
        self.latest_prices: dict[tuple[str, str], float] = {}
        self._open_symbols: set[tuple[str, str]] = set()
        self._price_update_lock = asyncio.Lock()

    async def ensure_account(self) -> None:
        async with self.database.session() as session:
            service = PaperAccountService(session, self.config)
            await service.get_or_create()
            for profile_key, profile_config in self.config.profiles.items():
                profile = await service.get_or_create_profile(profile_key, profile_config)
                self.config.profiles[profile_key] = profile_config_from_model(
                    profile, profile_config
                )
            rows = (
                await session.execute(
                    select(PaperTradeModel.exchange, PaperTradeModel.symbol).where(
                        PaperTradeModel.status == "OPEN"
                    )
                )
            ).all()
            self._open_symbols = {(str(exchange), str(symbol)) for exchange, symbol in rows}

    async def open_from_signal(
        self,
        signal,
        profile_key: str | None = None,
        profile_config: PaperProfileConfig | None = None,
    ) -> PaperTradeModel | None:
        profile_key = profile_key or self.config.default_profile
        profile_config = profile_config or self.config.profiles.get(profile_key)
        if profile_config is None:
            self.log.warning(
                "paper open request rejected signal_id=%s profile=%s reason=profile_missing",
                signal.id,
                profile_key,
            )
            return None
        self.log.info(
            "paper open request signal_id=%s exchange=%s symbol=%s direction=%s score=%s "
            "auto_min_score=%s max_open_positions=%s",
            signal.id,
            signal.exchange,
            signal.symbol,
            signal.direction,
            signal.score,
            profile_config.min_score,
            profile_config.max_open_positions,
        )
        async with self.database.session() as session:
            executor = PaperExecutor(session, self.config)
            trade = await executor.open_from_signal(signal, profile_key, profile_config)
            if trade is None:
                self.log.warning(
                    "paper open request rejected signal_id=%s exchange=%s symbol=%s profile=%s",
                    signal.id,
                    signal.exchange,
                    signal.symbol,
                    profile_key,
                )
                return None
            profile = await executor.account_service.get_or_create_profile(
                profile_key, profile_config
            )
            trade_id = trade.id
            balance = profile.current_balance
            self.log.info(
                "paper open committed trade_id=%s signal_id=%s profile=%s balance=%s",
                trade_id,
                signal.id,
                profile_key,
                balance,
            )
            self._open_symbols.add((signal.exchange, signal.symbol))
        async with self.database.session() as session:
            trade = await session.get(PaperTradeModel, trade_id)
            if trade and self.notifier:
                await self.notifier.send_paper_opened(trade, balance)
            return trade

    async def open_for_signal(self, signal) -> list[PaperTradeModel]:
        opened: list[PaperTradeModel] = []
        if not self.config.enabled:
            self.log.info(
                "paper.auto_open signal=%s decision=skipped reason=paper_disabled", signal.id
            )
            return opened
        for profile_key, profile_config in self.config.profiles.items():
            trade = await self.open_from_signal(signal, profile_key, profile_config)
            if trade is not None:
                opened.append(trade)
        return opened

    async def on_price(
        self, exchange: str, symbol: str, price: float, timestamp: datetime | None = None
    ) -> None:
        timestamp = timestamp or utc_now()
        key = (exchange, symbol)
        self.latest_prices[key] = price
        if key not in self._open_symbols:
            return
        async with self._price_update_lock:
            await self._on_price_locked(exchange, symbol, price, timestamp, key)

    async def _on_price_locked(
        self,
        exchange: str,
        symbol: str,
        price: float,
        timestamp: datetime,
        key: tuple[str, str],
    ) -> None:
        async with self.database.session() as session:
            trades = list(
                (
                    await session.scalars(
                        select(PaperTradeModel).where(
                            PaperTradeModel.exchange == exchange,
                            PaperTradeModel.symbol == symbol,
                            PaperTradeModel.status == "OPEN",
                        )
                    )
                ).all()
            )
            if not trades:
                self._open_symbols.discard(key)
                return
            account_service = PaperAccountService(session, self.config)
            closed_payloads: list[tuple[PaperTradeModel, float, float]] = []
            touched_accounts = {}
            for trade in trades:
                profile_config = self._profile_config_for_trade(trade)
                profile = await _profile_for_trade(session, trade)
                profile_account = await account_service.get_or_create_account(
                    f"profile:{trade.profile_key}", profile.initial_balance
                )
                touched_accounts[profile_account.id] = profile_account
                await self._update_trade(
                    account_service,
                    profile_account,
                    profile,
                    profile_config,
                    trade,
                    price,
                    timestamp,
                )
                if trade.status != "OPEN":
                    await _upsert_daily_stats(session, profile_account)
                    winrate = await _winrate(session, profile_key=trade.profile_key)
                    closed_payloads.append((trade, profile.current_balance, winrate))
            legacy_account = await account_service.get_or_create()
            await self._mark_to_market(session, legacy_account)
            for account in touched_accounts.values():
                await _upsert_daily_stats(session, account)
            if not any(trade.status == "OPEN" for trade in trades):
                self._open_symbols.discard(key)
        for trade, balance, winrate in closed_payloads:
            if self.notifier:
                await self.notifier.send_paper_closed(trade, balance, winrate)

    async def persist_state(self) -> None:
        async with self.database.session() as session:
            account_service = PaperAccountService(session, self.config)
            legacy_account = await account_service.get_or_create()
            await self._mark_to_market(session, legacy_account)

    async def close_manual(self, trade_id: int, price: float) -> PaperTradeModel | None:
        return await self._close_by_id(trade_id, price, "CLOSED_MANUAL")

    async def expire_trade(self, trade_id: int, price: float) -> PaperTradeModel | None:
        return await self._close_by_id(trade_id, price, "EXPIRED")

    async def _close_by_id(
        self, trade_id: int, price: float, status: str
    ) -> PaperTradeModel | None:
        async with self.database.session() as session:
            trade = await session.get(PaperTradeModel, trade_id)
            if trade is None or trade.status != "OPEN":
                return None
            account_service = PaperAccountService(session, self.config)
            profile = await _profile_for_trade(session, trade)
            account = await account_service.get_or_create_account(
                f"profile:{trade.profile_key}", profile.initial_balance
            )
            await self._close_trade(
                account_service, account, profile, trade, price, status, utc_now()
            )
            await _upsert_daily_stats(session, account)
            balance = profile.current_balance
            winrate = await _winrate(session, profile_key=trade.profile_key)
        if self.notifier:
            await self.notifier.send_paper_closed(trade, balance, winrate)
        return trade

    async def _update_trade(
        self,
        account_service: PaperAccountService,
        account,
        profile: PaperProfileModel,
        profile_config: PaperProfileConfig,
        trade: PaperTradeModel,
        price: float,
        timestamp: datetime,
    ) -> None:
        if trade.direction == "LONG":
            trade.high_watermark = max(trade.high_watermark or price, price)
        else:
            trade.low_watermark = min(trade.low_watermark or price, price)

        await self._maybe_partial_close(account_service, account, profile, trade, price)
        self._maybe_update_breakeven_stop(trade, profile_config, price)
        self._maybe_update_trailing_stop(trade, profile_config, price)

        opened_at = trade.opened_at
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        if timestamp - opened_at >= timedelta(minutes=profile_config.max_holding_minutes):
            await self._close_trade(
                account_service, account, profile, trade, price, "EXPIRED", timestamp
            )
            return

        if trade.direction == "LONG":
            if price >= trade.take_price:
                await self._close_trade(
                    account_service,
                    account,
                    profile,
                    trade,
                    trade.take_price,
                    "CLOSED_TP",
                    timestamp,
                )
            elif price <= trade.stop_price:
                await self._close_trade(
                    account_service,
                    account,
                    profile,
                    trade,
                    trade.stop_price,
                    "CLOSED_SL",
                    timestamp,
                )
        else:
            if price <= trade.take_price:
                await self._close_trade(
                    account_service,
                    account,
                    profile,
                    trade,
                    trade.take_price,
                    "CLOSED_TP",
                    timestamp,
                )
            elif price >= trade.stop_price:
                await self._close_trade(
                    account_service,
                    account,
                    profile,
                    trade,
                    trade.stop_price,
                    "CLOSED_SL",
                    timestamp,
                )

    async def _maybe_partial_close(
        self,
        account_service: PaperAccountService,
        account,
        profile: PaperProfileModel,
        trade: PaperTradeModel,
        price: float,
    ) -> None:
        cfg = self.config.partial_tp
        if not cfg.enabled or trade.partial_closed:
            return
        rr = _trade_rr(trade, price)
        if rr < cfg.first_target_rr:
            return
        close_size = trade.remaining_size_usd * cfg.first_tp_pct / 100
        if close_size <= 0:
            return
        exit_price = apply_exit_slippage(price, trade.direction, self.config.slippage_pct)
        pnl = pnl_for_exit(trade.direction, trade.entry_price, exit_price, close_size)
        fee = fee_for_notional(close_size, self.config.taker_fee_pct)
        realized = pnl - fee
        trade.partial_closed = True
        trade.partial_exit_price = exit_price
        trade.partial_pnl_usd += realized
        trade.pnl_usd += realized
        trade.fees_usd += fee
        trade.remaining_size_usd -= close_size
        trade.realized_rr = trade.pnl_usd / trade.risk_usd if trade.risk_usd else 0.0
        await account_service.apply_profile_realized_pnl(account, profile, realized, trade.id)

    def _maybe_update_breakeven_stop(
        self,
        trade: PaperTradeModel,
        profile_config: PaperProfileConfig,
        price: float,
    ) -> None:
        if not profile_config.breakeven_enabled:
            return
        rr = _trade_rr(trade, price)
        if rr < profile_config.breakeven_activation_rr:
            return
        if trade.direction == "LONG":
            trade.stop_price = max(trade.stop_price, trade.entry_price)
        else:
            trade.stop_price = min(trade.stop_price, trade.entry_price)

    def _maybe_update_trailing_stop(
        self,
        trade: PaperTradeModel,
        profile_config: PaperProfileConfig,
        price: float,
    ) -> None:
        if not profile_config.trailing_enabled:
            return
        rr = _trade_rr(trade, price)
        if rr < profile_config.trailing_activation_rr:
            return
        trade.trailing_activated = True
        distance = profile_config.trailing_distance_pct / 100
        if trade.direction == "LONG":
            candidate = max(trade.entry_price, (trade.high_watermark or price) * (1 - distance))
            trade.stop_price = max(trade.stop_price, candidate)
        else:
            candidate = min(trade.entry_price, (trade.low_watermark or price) * (1 + distance))
            trade.stop_price = min(trade.stop_price, candidate)

    async def _close_trade(
        self,
        account_service: PaperAccountService,
        account,
        profile: PaperProfileModel,
        trade: PaperTradeModel,
        trigger_price: float,
        status: str,
        timestamp: datetime,
    ) -> None:
        if trade.remaining_size_usd <= 0:
            return
        exit_price = apply_exit_slippage(trigger_price, trade.direction, self.config.slippage_pct)
        pnl = pnl_for_exit(trade.direction, trade.entry_price, exit_price, trade.remaining_size_usd)
        fee = fee_for_notional(trade.remaining_size_usd, self.config.taker_fee_pct)
        realized = pnl - fee
        trade.pnl_usd += realized
        trade.fees_usd += fee
        trade.pnl_pct = (
            trade.pnl_usd / trade.position_size_usd * 100 if trade.position_size_usd else 0.0
        )
        trade.realized_rr = trade.pnl_usd / trade.risk_usd if trade.risk_usd else 0.0
        trade.remaining_size_usd = 0.0
        trade.exit_price = exit_price
        trade.closed_at = timestamp
        trade.status = status
        await account_service.apply_profile_realized_pnl(account, profile, realized, trade.id)

    async def _mark_to_market(self, session, account) -> None:
        open_trades = list(
            (
                await session.scalars(
                    select(PaperTradeModel).where(PaperTradeModel.status == "OPEN")
                )
            ).all()
        )
        unrealized = 0.0
        for trade in open_trades:
            price = self.latest_prices.get((trade.exchange, trade.symbol))
            if price is None:
                continue
            unrealized += pnl_for_exit(
                trade.direction, trade.entry_price, price, trade.remaining_size_usd
            )
        account.equity = account.balance + unrealized
        account.peak_equity = max(account.peak_equity, account.equity)
        drawdown = 0.0
        if account.peak_equity > 0:
            drawdown = (account.equity - account.peak_equity) / account.peak_equity * 100
        account.max_drawdown_pct = min(account.max_drawdown_pct, drawdown)
        account.updated_at = utc_now()

        profiles = list((await session.scalars(select(PaperProfileModel))).all())
        for profile in profiles:
            profile_unrealized = 0.0
            for trade in open_trades:
                if trade.profile_key != profile.profile_key:
                    continue
                price = self.latest_prices.get((trade.exchange, trade.symbol))
                if price is None:
                    continue
                profile_unrealized += pnl_for_exit(
                    trade.direction, trade.entry_price, price, trade.remaining_size_usd
                )
            profile.equity = profile.current_balance + profile_unrealized
            profile.peak_equity = max(profile.peak_equity, profile.equity)
            profile_drawdown = 0.0
            if profile.peak_equity > 0:
                profile_drawdown = (
                    (profile.equity - profile.peak_equity) / profile.peak_equity * 100
                )
            profile.max_drawdown_pct = min(profile.max_drawdown_pct, profile_drawdown)
            profile.updated_at = utc_now()

    def _profile_config_for_trade(self, trade: PaperTradeModel) -> PaperProfileConfig:
        return self.config.profiles.get(
            trade.profile_key,
            PaperProfileConfig(
                name=trade.profile_key,
                enabled=True,
                initial_balance=self.config.initial_balance,
                min_score=self.config.auto_trade_min_score,
                risk_per_trade_pct=self.config.risk_per_trade_pct,
                leverage=self.config.leverage,
                stop_loss_pct=self.config.stop_pct,
                take_profit_pct=self.config.take_pct,
                max_open_positions=self.config.max_open_positions,
                max_positions_per_symbol=1,
                max_daily_loss_pct=100,
                max_holding_minutes=180,
            ),
        )


async def _profile_for_trade(session, trade: PaperTradeModel) -> PaperProfileModel:
    if trade.profile_id is not None:
        profile = await session.get(PaperProfileModel, trade.profile_id)
        if profile is not None:
            return profile
    profile = await session.scalar(
        select(PaperProfileModel).where(PaperProfileModel.profile_key == trade.profile_key)
    )
    if profile is None:
        profile = PaperProfileModel(
            profile_key=trade.profile_key,
            name=trade.profile_key,
            enabled=True,
            initial_balance=0.0,
            current_balance=0.0,
            equity=0.0,
            settings_json="{}",
            net_profit=0.0,
            max_drawdown_pct=0.0,
            peak_equity=0.0,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(profile)
        await session.flush()
    return profile


def _trade_rr(trade: PaperTradeModel, price: float) -> float:
    if trade.entry_price <= 0 or trade.position_size_usd <= 0:
        return 0.0
    risk_fraction = trade.risk_usd / trade.position_size_usd
    if risk_fraction <= 0:
        return 0.0
    if trade.direction == "LONG":
        favorable_fraction = price / trade.entry_price - 1
    else:
        favorable_fraction = trade.entry_price / price - 1 if price > 0 else 0.0
    return favorable_fraction / risk_fraction


async def _winrate(session, profile_key: str | None = None) -> float:
    filters = [PaperTradeModel.status != "OPEN"]
    if profile_key is not None:
        filters.append(PaperTradeModel.profile_key == profile_key)
    closed = list((await session.scalars(select(PaperTradeModel).where(*filters))).all())
    if not closed:
        return 0.0
    wins = sum(1 for trade in closed if trade.pnl_usd > 0)
    return wins / len(closed) * 100


async def _upsert_daily_stats(session, account) -> None:
    today = utc_now().date()
    day_start = datetime.combine(today, datetime.min.time())
    trades = list(
        (
            await session.scalars(
                select(PaperTradeModel).where(
                    PaperTradeModel.account_id == account.id,
                    PaperTradeModel.status != "OPEN",
                    PaperTradeModel.closed_at >= day_start,
                )
            )
        ).all()
    )
    wins = sum(1 for trade in trades if trade.pnl_usd > 0)
    losses = sum(1 for trade in trades if trade.pnl_usd < 0)
    winrate = wins / len(trades) * 100 if trades else 0.0
    stmt = sqlite_insert(PaperDailyStatsModel).values(
        account_id=account.id,
        date=today,
        balance=account.balance,
        net_profit=account.net_profit,
        trades=len(trades),
        wins=wins,
        losses=losses,
        winrate_pct=winrate,
        max_drawdown_pct=account.max_drawdown_pct,
        updated_at=utc_now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_id", "date"],
        set_={
            "balance": account.balance,
            "net_profit": account.net_profit,
            "trades": len(trades),
            "wins": wins,
            "losses": losses,
            "winrate_pct": winrate,
            "max_drawdown_pct": account.max_drawdown_pct,
            "updated_at": utc_now(),
        },
    )
    await session.execute(stmt)
