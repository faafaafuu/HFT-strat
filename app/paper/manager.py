from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import PaperConfig
from app.data.database import Database
from app.data.models import PaperDailyStatsModel, PaperTradeModel
from app.logger import get_logger
from app.paper.account import PaperAccountService
from app.paper.executor import PaperExecutor
from app.paper.risk import apply_exit_slippage, fee_for_notional, pnl_for_exit, rr_for_price
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

    async def ensure_account(self) -> None:
        async with self.database.session() as session:
            await PaperAccountService(session, self.config).get_or_create()

    async def open_from_signal(self, signal) -> PaperTradeModel | None:
        async with self.database.session() as session:
            executor = PaperExecutor(session, self.config)
            trade = await executor.open_from_signal(signal)
            if trade is None:
                return None
            account = await executor.account_service.get_or_create()
            trade_id = trade.id
            balance = account.balance
        async with self.database.session() as session:
            trade = await session.get(PaperTradeModel, trade_id)
            if trade and self.notifier:
                await self.notifier.send_paper_opened(trade, balance)
            return trade

    async def on_price(
        self, exchange: str, symbol: str, price: float, timestamp: datetime | None = None
    ) -> None:
        timestamp = timestamp or utc_now()
        self.latest_prices[(exchange, symbol)] = price
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
                return
            account_service = PaperAccountService(session, self.config)
            account = await account_service.get_or_create()
            closed: list[PaperTradeModel] = []
            for trade in trades:
                await self._update_trade(session, account_service, account, trade, price, timestamp)
                if trade.status != "OPEN":
                    closed.append(trade)
            if closed:
                await _upsert_daily_stats(session, account)
            await self._mark_to_market(session, account)
            balance = account.balance
            winrate = await _winrate(session)
        for trade in closed:
            if self.notifier:
                await self.notifier.send_paper_closed(trade, balance, winrate)

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
            account = await account_service.get_or_create()
            await self._close_trade(account_service, account, trade, price, status, utc_now())
            await _upsert_daily_stats(session, account)
            balance = account.balance
            winrate = await _winrate(session)
        if self.notifier:
            await self.notifier.send_paper_closed(trade, balance, winrate)
        return trade

    async def _update_trade(
        self,
        session,
        account_service: PaperAccountService,
        account,
        trade: PaperTradeModel,
        price: float,
        timestamp: datetime,
    ) -> None:
        if trade.direction == "LONG":
            trade.high_watermark = max(trade.high_watermark or price, price)
        else:
            trade.low_watermark = min(trade.low_watermark or price, price)

        await self._maybe_partial_close(account_service, account, trade, price)
        self._maybe_update_trailing_stop(trade, price)

        if trade.direction == "LONG":
            if price >= trade.take_price:
                await self._close_trade(
                    account_service, account, trade, trade.take_price, "CLOSED_TP", timestamp
                )
            elif price <= trade.stop_price:
                await self._close_trade(
                    account_service, account, trade, trade.stop_price, "CLOSED_SL", timestamp
                )
        else:
            if price <= trade.take_price:
                await self._close_trade(
                    account_service, account, trade, trade.take_price, "CLOSED_TP", timestamp
                )
            elif price >= trade.stop_price:
                await self._close_trade(
                    account_service, account, trade, trade.stop_price, "CLOSED_SL", timestamp
                )

    async def _maybe_partial_close(
        self,
        account_service: PaperAccountService,
        account,
        trade: PaperTradeModel,
        price: float,
    ) -> None:
        cfg = self.config.partial_tp
        if not cfg.enabled or trade.partial_closed:
            return
        rr = rr_for_price(trade.direction, trade.entry_price, trade.stop_price, price)
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
        await account_service.apply_realized_pnl(account, realized, trade.id)

    def _maybe_update_trailing_stop(self, trade: PaperTradeModel, price: float) -> None:
        cfg = self.config.trailing
        if not cfg.enabled:
            return
        rr = rr_for_price(trade.direction, trade.entry_price, trade.stop_price, price)
        if rr < cfg.activation_rr:
            return
        trade.trailing_activated = True
        distance = cfg.distance_pct / 100
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
        await account_service.apply_realized_pnl(account, realized, trade.id)

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


async def _winrate(session) -> float:
    closed = list(
        (
            await session.scalars(select(PaperTradeModel).where(PaperTradeModel.status != "OPEN"))
        ).all()
    )
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
