from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.data.models import Base, PaperProfileModel, PaperTradeModel, RuntimeSettingModel
from app.utils.time import utc_now


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bot.sqlite3"
        url = f"sqlite:///{db_path}"
        _seed(url)
        _verify(url)
    print("Persistence verification passed.")


def _seed(url: str) -> None:
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        profile = PaperProfileModel(
            profile_key="aggressive",
            name="Aggressive",
            enabled=True,
            initial_balance=2000,
            current_balance=2012.5,
            equity=2018.0,
            settings_json='{"min_score": 7}',
            net_profit=12.5,
            max_drawdown_pct=0.0,
            peak_equity=2018.0,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(profile)
        session.flush()
        session.add(
            PaperTradeModel(
                account_id=1,
                profile_id=profile.id,
                profile_key="aggressive",
                signal_id=None,
                exchange="bybit",
                symbol="BTCUSDT",
                direction="LONG",
                pattern="test",
                score=8,
                entry_price=100,
                stop_price=99.5,
                take_price=101.5,
                leverage=7,
                position_size_usd=1000,
                remaining_size_usd=1000,
                risk_usd=5,
                opened_at=utc_now(),
                status="OPEN",
            )
        )
        session.add(
            RuntimeSettingModel(
                key="signals.min_score",
                value_json="7",
                updated_at=utc_now(),
            )
        )
        session.commit()
    engine.dispose()


def _verify(url: str) -> None:
    engine = create_engine(url, future=True)
    with Session(engine) as session:
        trade = session.scalar(select(PaperTradeModel).where(PaperTradeModel.status == "OPEN"))
        profile = session.scalar(
            select(PaperProfileModel).where(PaperProfileModel.profile_key == "aggressive")
        )
        setting = session.get(RuntimeSettingModel, "signals.min_score")
    engine.dispose()
    assert trade is not None
    assert profile is not None
    assert setting is not None
    assert profile.current_balance == 2012.5
    assert profile.equity == 2018.0


if __name__ == "__main__":
    main()
