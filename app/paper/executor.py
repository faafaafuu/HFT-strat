from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PaperConfig
from app.data.models import PaperTradeModel, SignalModel
from app.logger import get_logger
from app.paper.account import PaperAccountService
from app.paper.risk import calculate_paper_plan, fee_for_notional
from app.utils.time import utc_now


class PaperExecutor:
    def __init__(self, session: AsyncSession, config: PaperConfig) -> None:
        self.session = session
        self.config = config
        self.account_service = PaperAccountService(session, config)
        self.log = get_logger("paper_executor")

    async def open_count(self) -> int:
        open_count = await self.session.scalar(
            select(func.count(PaperTradeModel.id)).where(PaperTradeModel.status == "OPEN")
        )
        return int(open_count or 0)

    async def can_open(self) -> bool:
        return await self.open_count() < self.config.max_open_positions

    async def open_from_signal(self, signal: SignalModel) -> PaperTradeModel | None:
        open_count = await self.open_count()
        if open_count >= self.config.max_open_positions:
            self.log.warning(
                "paper open skipped signal_id=%s reason=max_open_positions open=%s max=%s",
                signal.id,
                open_count,
                self.config.max_open_positions,
            )
            return None
        existing = await self.session.scalar(
            select(PaperTradeModel).where(PaperTradeModel.signal_id == signal.id)
        )
        if existing is not None:
            self.log.warning(
                "paper open skipped signal_id=%s reason=duplicate_trade trade_id=%s",
                signal.id,
                existing.id,
            )
            return None
        account = await self.account_service.get_or_create()
        plan = calculate_paper_plan(
            balance=account.balance,
            signal_price=signal.entry_price,
            direction=signal.direction,
            config=self.config,
        )
        if plan.position_size_usd <= 0:
            self.log.warning(
                "paper open skipped signal_id=%s reason=invalid_position_size "
                "balance=%s entry_price=%s position_size_usd=%s risk_usd=%s",
                signal.id,
                account.balance,
                signal.entry_price,
                plan.position_size_usd,
                plan.risk_usd,
            )
            return None
        entry_fee = fee_for_notional(plan.position_size_usd, self.config.taker_fee_pct)
        self.log.info(
            "paper open plan signal_id=%s balance=%s direction=%s entry=%s stop=%s take=%s "
            "position_usd=%s risk_usd=%s leverage=%s entry_fee=%s",
            signal.id,
            account.balance,
            signal.direction,
            plan.entry_price,
            plan.stop_price,
            plan.take_price,
            plan.position_size_usd,
            plan.risk_usd,
            plan.leverage,
            entry_fee,
        )
        trade = PaperTradeModel(
            account_id=account.id,
            signal_id=signal.id,
            exchange=signal.exchange,
            symbol=signal.symbol,
            direction=signal.direction,
            pattern=signal.pattern,
            score=signal.score,
            entry_price=plan.entry_price,
            stop_price=plan.stop_price,
            take_price=plan.take_price,
            leverage=plan.leverage,
            position_size_usd=plan.position_size_usd,
            remaining_size_usd=plan.position_size_usd,
            risk_usd=plan.risk_usd,
            opened_at=utc_now(),
            status="OPEN",
            pnl_usd=-entry_fee,
            fees_usd=entry_fee,
            realized_rr=-entry_fee / plan.risk_usd if plan.risk_usd else 0.0,
            high_watermark=plan.entry_price,
            low_watermark=plan.entry_price,
        )
        self.session.add(trade)
        await self.session.flush()
        await self.account_service.apply_realized_pnl(account, -entry_fee, trade.id)
        return trade
