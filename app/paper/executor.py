from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PaperConfig, PaperProfileConfig
from app.data.models import PaperTradeModel, SignalModel
from app.logger import get_logger
from app.paper.account import PaperAccountService
from app.paper.risk import calculate_paper_plan, fee_for_notional
from app.utils.time import utc_now


@dataclass(frozen=True)
class ProfileRiskConfig:
    leverage: float
    risk_per_trade_pct: float
    stop_pct: float
    take_pct: float
    slippage_pct: float


class PaperExecutor:
    def __init__(self, session: AsyncSession, config: PaperConfig) -> None:
        self.session = session
        self.config = config
        self.account_service = PaperAccountService(session, config)
        self.log = get_logger("paper_executor")

    async def open_count(self, profile_key: str = "default") -> int:
        open_count = await self.session.scalar(
            select(func.count(PaperTradeModel.id)).where(
                PaperTradeModel.status == "OPEN",
                PaperTradeModel.profile_key == profile_key,
            )
        )
        return int(open_count or 0)

    async def can_open(self, profile_key: str = "default") -> bool:
        return await self.open_count(profile_key) < self.config.max_open_positions

    async def open_from_signal(
        self,
        signal: SignalModel,
        profile_key: str = "default",
        profile_config: PaperProfileConfig | None = None,
    ) -> PaperTradeModel | None:
        profile_config = profile_config or _legacy_profile_config(self.config)
        skip_reason = await self._skip_reason(signal, profile_key, profile_config)
        if skip_reason is not None:
            self.log.warning(
                "paper.auto_open signal=%s score=%s profile=%s min_score=%s "
                "decision=skipped reason=%s",
                signal.id,
                signal.score,
                profile_key,
                profile_config.min_score,
                skip_reason,
            )
            return None
        profile = await self.account_service.get_or_create_profile(profile_key, profile_config)
        account = await self.account_service.get_or_create_account(
            f"profile:{profile_key}", profile.initial_balance
        )
        risk_config = ProfileRiskConfig(
            leverage=profile_config.leverage,
            risk_per_trade_pct=profile_config.risk_per_trade_pct,
            stop_pct=profile_config.stop_loss_pct,
            take_pct=profile_config.take_profit_pct,
            slippage_pct=self.config.slippage_pct,
        )
        plan = calculate_paper_plan(
            balance=profile.current_balance,
            signal_price=signal.entry_price,
            direction=signal.direction,
            config=risk_config,
        )
        if plan.position_size_usd <= 0:
            self.log.warning(
                "paper.auto_open signal=%s profile=%s decision=skipped reason=invalid_position_size "
                "balance=%s entry_price=%s position_size_usd=%s risk_usd=%s",
                signal.id,
                profile_key,
                profile.current_balance,
                signal.entry_price,
                plan.position_size_usd,
                plan.risk_usd,
            )
            return None
        entry_fee = fee_for_notional(plan.position_size_usd, self.config.taker_fee_pct)
        self.log.info(
            "paper open plan signal_id=%s profile=%s balance=%s direction=%s entry=%s stop=%s take=%s "
            "position_usd=%s risk_usd=%s leverage=%s entry_fee=%s",
            signal.id,
            profile_key,
            profile.current_balance,
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
            profile_id=profile.id,
            profile_key=profile_key,
            signal_id=signal.id,
            exchange=signal.exchange,
            symbol=signal.symbol,
            direction=signal.direction,
            pattern=signal.pattern,
            strategy_key=signal.strategy_key,
            strategy_profile_key=signal.strategy_profile_key,
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
        try:
            await self.session.flush()
        except IntegrityError:
            self.log.warning(
                "paper.auto_open signal=%s profile=%s decision=skipped reason=duplicate_signal_profile",
                signal.id,
                profile_key,
            )
            await self.session.rollback()
            return None
        await self.account_service.apply_profile_realized_pnl(
            account, profile, -entry_fee, trade.id
        )
        self.log.info(
            "paper.auto_open signal=%s score=%s profile=%s min_score=%s decision=opened trade=%s",
            signal.id,
            signal.score,
            profile_key,
            profile_config.min_score,
            trade.id,
        )
        return trade

    async def _skip_reason(
        self,
        signal: SignalModel,
        profile_key: str,
        profile_config: PaperProfileConfig,
    ) -> str | None:
        if not profile_config.enabled:
            return "profile_disabled"
        if signal.score < profile_config.min_score:
            return "score_below_min"
        if (
            profile_config.allowed_patterns
            and signal.pattern not in profile_config.allowed_patterns
        ):
            return "pattern_not_allowed"
        if profile_config.allowed_symbols and signal.symbol not in profile_config.allowed_symbols:
            return "symbol_not_allowed"
        if signal.symbol in profile_config.blocked_symbols:
            return "symbol_blocked"

        open_count = await self.open_count(profile_key)
        if open_count >= profile_config.max_open_positions:
            return "max_positions"

        open_symbol_count = await self.session.scalar(
            select(func.count(PaperTradeModel.id)).where(
                PaperTradeModel.status == "OPEN",
                PaperTradeModel.profile_key == profile_key,
                PaperTradeModel.exchange == signal.exchange,
                PaperTradeModel.symbol == signal.symbol,
            )
        )
        if int(open_symbol_count or 0) >= profile_config.max_positions_per_symbol:
            return "max_positions_per_symbol"

        existing = await self.session.scalar(
            select(PaperTradeModel).where(
                PaperTradeModel.signal_id == signal.id,
                PaperTradeModel.profile_key == profile_key,
            )
        )
        if existing is not None:
            return "duplicate_signal_profile"

        profile = await self.account_service.get_or_create_profile(profile_key, profile_config)
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        realized_today = await self.session.scalar(
            select(func.coalesce(func.sum(PaperTradeModel.pnl_usd), 0.0)).where(
                PaperTradeModel.profile_key == profile_key,
                PaperTradeModel.opened_at >= day_start,
            )
        )
        max_loss = profile.initial_balance * profile_config.max_daily_loss_pct / 100
        if float(realized_today or 0.0) <= -max_loss:
            return "max_daily_loss"
        return None


def _legacy_profile_config(config: PaperConfig) -> PaperProfileConfig:
    return PaperProfileConfig(
        name="Default",
        enabled=True,
        initial_balance=config.initial_balance,
        min_score=config.auto_trade_min_score,
        risk_per_trade_pct=config.risk_per_trade_pct,
        leverage=config.leverage,
        stop_loss_pct=config.stop_pct,
        take_profit_pct=config.take_pct,
        max_open_positions=config.max_open_positions,
        max_positions_per_symbol=1,
        max_daily_loss_pct=100,
        max_holding_minutes=180,
        breakeven_enabled=config.trailing.enabled,
        breakeven_activation_rr=config.trailing.activation_rr,
        trailing_enabled=config.trailing.enabled,
        trailing_activation_rr=config.trailing.activation_rr,
        trailing_distance_pct=config.trailing.distance_pct,
    )
