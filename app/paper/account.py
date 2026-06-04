from __future__ import annotations

import json

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PaperConfig, PaperProfileConfig
from app.data.models import PaperAccountModel, PaperEquityCurveModel, PaperProfileModel
from app.utils.time import utc_now


class PaperAccountService:
    def __init__(self, session: AsyncSession, config: PaperConfig) -> None:
        self.session = session
        self.config = config

    async def get_or_create(self) -> PaperAccountModel:
        return await self.get_or_create_account("default", self.config.initial_balance)

    async def get_or_create_account(
        self,
        name: str,
        initial_balance: float,
    ) -> PaperAccountModel:
        account = await self.session.scalar(
            select(PaperAccountModel).where(PaperAccountModel.name == name)
        )
        if account is not None:
            return account
        account = PaperAccountModel(
            name=name,
            initial_balance=initial_balance,
            balance=initial_balance,
            equity=initial_balance,
            net_profit=0.0,
            max_drawdown_pct=0.0,
            peak_equity=initial_balance,
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

    async def get_or_create_profile(
        self,
        profile_key: str,
        profile_config: PaperProfileConfig,
    ) -> PaperProfileModel:
        profile = await self.session.scalar(
            select(PaperProfileModel).where(PaperProfileModel.profile_key == profile_key)
        )
        if profile is not None:
            stored = profile_config_from_model(profile, profile_config)
            profile.name = stored.name
            profile.enabled = stored.enabled
            profile.updated_at = utc_now()
            return profile
        settings_json = json.dumps(profile_config.model_dump(), ensure_ascii=False, sort_keys=True)
        profile = PaperProfileModel(
            profile_key=profile_key,
            name=profile_config.name,
            enabled=profile_config.enabled,
            initial_balance=profile_config.initial_balance,
            current_balance=profile_config.initial_balance,
            equity=profile_config.initial_balance,
            settings_json=settings_json,
            net_profit=0.0,
            max_drawdown_pct=0.0,
            peak_equity=profile_config.initial_balance,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(profile)
        await self.session.flush()
        account = await self.get_or_create_account(
            f"profile:{profile_key}", profile_config.initial_balance
        )
        self.session.add(
            PaperEquityCurveModel(
                account_id=account.id,
                profile_id=profile.id,
                profile_key=profile.profile_key,
                trade_id=None,
                timestamp=utc_now(),
                balance=profile.current_balance,
                equity=profile.equity,
                net_profit=profile.net_profit,
                drawdown_pct=profile.max_drawdown_pct,
            )
        )
        return profile

    async def save_profile_config(
        self,
        profile_key: str,
        profile_config: PaperProfileConfig,
    ) -> PaperProfileModel:
        profile = await self.get_or_create_profile(profile_key, profile_config)
        profile.name = profile_config.name
        profile.enabled = profile_config.enabled
        profile.settings_json = json.dumps(
            profile_config.model_dump(), ensure_ascii=False, sort_keys=True
        )
        profile.updated_at = utc_now()
        return profile

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

    async def apply_profile_realized_pnl(
        self,
        account: PaperAccountModel,
        profile: PaperProfileModel,
        pnl_usd: float,
        trade_id: int | None,
    ) -> None:
        profile.current_balance += pnl_usd
        profile.equity = profile.current_balance
        profile.net_profit = profile.current_balance - profile.initial_balance
        profile.peak_equity = max(profile.peak_equity, profile.equity)
        drawdown = 0.0
        if profile.peak_equity > 0:
            drawdown = (profile.equity - profile.peak_equity) / profile.peak_equity * 100
        profile.max_drawdown_pct = min(profile.max_drawdown_pct, drawdown)
        profile.updated_at = utc_now()

        account.balance = profile.current_balance
        account.equity = profile.equity
        account.net_profit = profile.net_profit
        account.peak_equity = profile.peak_equity
        account.max_drawdown_pct = profile.max_drawdown_pct
        account.updated_at = utc_now()

        self.session.add(
            PaperEquityCurveModel(
                account_id=account.id,
                profile_id=profile.id,
                profile_key=profile.profile_key,
                trade_id=trade_id,
                timestamp=utc_now(),
                balance=profile.current_balance,
                equity=profile.equity,
                net_profit=profile.net_profit,
                drawdown_pct=drawdown,
            )
        )


def profile_config_from_model(
    profile: PaperProfileModel,
    fallback: PaperProfileConfig,
) -> PaperProfileConfig:
    if not profile.settings_json:
        return fallback
    try:
        data = json.loads(profile.settings_json)
    except json.JSONDecodeError:
        return fallback
    if not isinstance(data, dict):
        return fallback
    merged = fallback.model_dump()
    merged.update(data)
    try:
        return PaperProfileConfig.model_validate(merged)
    except ValidationError:
        return fallback
