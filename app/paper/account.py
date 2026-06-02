from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PaperConfig
from app.data.models import PaperAccountModel, PaperEquityCurveModel
from app.utils.time import utc_now


class PaperAccountService:
    def __init__(self, session: AsyncSession, config: PaperConfig) -> None:
        self.session = session
        self.config = config

    async def get_or_create(self) -> PaperAccountModel:
        account = await self.session.scalar(
            select(PaperAccountModel).where(PaperAccountModel.name == "default")
        )
        if account is not None:
            return account
        account = PaperAccountModel(
            name="default",
            initial_balance=self.config.initial_balance,
            balance=self.config.initial_balance,
            equity=self.config.initial_balance,
            net_profit=0.0,
            max_drawdown_pct=0.0,
            peak_equity=self.config.initial_balance,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(account)
        await self.session.flush()
        self.session.add(
            PaperEquityCurveModel(
                account_id=account.id,
                trade_id=None,
                timestamp=utc_now(),
                balance=account.balance,
                equity=account.equity,
                net_profit=account.net_profit,
                drawdown_pct=account.max_drawdown_pct,
            )
        )
        return account

    async def apply_realized_pnl(
        self,
        account: PaperAccountModel,
        pnl_usd: float,
        trade_id: int | None,
    ) -> None:
        account.balance += pnl_usd
        account.equity = account.balance
        account.net_profit = account.balance - account.initial_balance
        account.peak_equity = max(account.peak_equity, account.equity)
        drawdown = 0.0
        if account.peak_equity > 0:
            drawdown = (account.equity - account.peak_equity) / account.peak_equity * 100
        account.max_drawdown_pct = min(account.max_drawdown_pct, drawdown)
        account.updated_at = utc_now()
        self.session.add(
            PaperEquityCurveModel(
                account_id=account.id,
                trade_id=trade_id,
                timestamp=utc_now(),
                balance=account.balance,
                equity=account.equity,
                net_profit=account.net_profit,
                drawdown_pct=drawdown,
            )
        )

