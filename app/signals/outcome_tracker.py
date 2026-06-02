from __future__ import annotations

import asyncio
from datetime import timedelta

from app.config import OutcomesConfig
from app.data.database import Database
from app.data.repositories import SignalRepository, _aware
from app.logger import get_logger
from app.market.features import MarketFeatureStore
from app.utils.time import utc_now


def calculate_outcome(
    direction: str,
    entry_price: float,
    min_price: float,
    max_price: float,
    price_after: float,
    tp_levels_pct: list[float],
    sl_levels_pct: list[float],
) -> dict[str, object]:
    if direction.upper() == "LONG":
        mfe_pct = (max_price - entry_price) / entry_price * 100
        mae_pct = (entry_price - min_price) / entry_price * 100
    else:
        mfe_pct = (entry_price - min_price) / entry_price * 100
        mae_pct = (max_price - entry_price) / entry_price * 100

    hits: dict[str, bool] = {}
    for level in tp_levels_pct:
        hits[f"tp_{str(level).replace('.', '_')}"] = mfe_pct >= level
    for level in sl_levels_pct:
        hits[f"sl_{str(level).replace('.', '_')}"] = mae_pct >= level

    return {
        "price_after": price_after,
        "mfe_pct": max(0.0, mfe_pct),
        "mae_pct": max(0.0, mae_pct),
        "hits": hits,
    }


class OutcomeTracker:
    def __init__(
        self,
        database: Database,
        feature_store: MarketFeatureStore,
        config: OutcomesConfig,
        interval_seconds: int = 30,
    ) -> None:
        self.database = database
        self.feature_store = feature_store
        self.config = config
        self.interval_seconds = interval_seconds
        self.log = get_logger("outcome_tracker")
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.evaluate_due_outcomes()
            except Exception as exc:  # noqa: BLE001 - tracker must keep running.
                self.log.warning("outcome evaluation failed: %s", exc)
            await asyncio.sleep(self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()

    async def evaluate_due_outcomes(self) -> None:
        now = utc_now()
        async with self.database.session() as session:
            repo = SignalRepository(session)
            signals = await repo.list_signals_missing_outcomes(self.config.horizons_minutes, now=now)
            for signal in signals:
                signal_ts = _aware(signal.timestamp)
                for horizon in self.config.horizons_minutes:
                    end = signal_ts + timedelta(minutes=horizon)
                    if end > now:
                        continue
                    price_after, min_price, max_price = self.feature_store.min_max_price_since(
                        signal.exchange,
                        signal.symbol,
                        signal_ts,
                        end,
                    )
                    if price_after is None or min_price is None or max_price is None:
                        continue
                    outcome = calculate_outcome(
                        direction=signal.direction,
                        entry_price=signal.entry_price,
                        min_price=min_price,
                        max_price=max_price,
                        price_after=price_after,
                        tp_levels_pct=self.config.tp_levels_pct,
                        sl_levels_pct=self.config.sl_levels_pct,
                    )
                    await repo.add_outcome(
                        signal_id=signal.id,
                        horizon_minutes=horizon,
                        price_after=float(outcome["price_after"]),
                        mfe_pct=float(outcome["mfe_pct"]),
                        mae_pct=float(outcome["mae_pct"]),
                        hits=outcome["hits"],  # type: ignore[arg-type]
                    )
