from __future__ import annotations

import json

from sqlalchemy import select

from app.backtesting.data_loader import download_bybit_history
from app.backtesting.engine import BacktestEngine
from app.config import Settings
from app.data.database import Database
from app.data.models import JobModel
from app.jobs.models import DOWNLOAD_HISTORY, RUN_BACKTEST, RUN_HYPEROPT
from app.optimization.optimizer import HyperOptimizer
from app.utils.time import utc_now


class JobWorker:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def run_once(self) -> dict[str, int]:
        processed = 0
        async with self.database.session() as session:
            job = await session.scalar(
                select(JobModel).where(JobModel.status == "PENDING").order_by(JobModel.created_at).limit(1)
            )
            if job is None:
                return {"processed": 0}
            job.status = "RUNNING"
            job.started_at = utc_now()
            params = json.loads(job.params_json)
            try:
                result = await self._run_job(job.job_type, params)
                job.status = "DONE"
                job.result_json = json.dumps(result, ensure_ascii=False, default=str)
            except Exception as exc:  # noqa: BLE001 - store job failure instead of killing worker.
                job.status = "FAILED"
                job.error = str(exc)
            job.finished_at = utc_now()
            processed += 1
        return {"processed": processed}

    async def _run_job(self, job_type: str, params: dict) -> dict:
        if job_type == DOWNLOAD_HISTORY:
            count = await download_bybit_history(
                self.database,
                symbol=str(params["symbol"]),
                timeframe=str(params.get("timeframe", "1m")),
                days=int(params.get("days", 30)),
            )
            return {"candles": count}
        if job_type == RUN_BACKTEST:
            return await BacktestEngine(self.database, self.settings).run(
                strategy_key=str(params["strategy_key"]),
                symbol=str(params["symbol"]),
                timeframe=str(params.get("timeframe", "1m")),
                days=int(params.get("days", 30)),
            )
        if job_type == RUN_HYPEROPT:
            return await HyperOptimizer(self.database, self.settings).run(
                strategy_key=str(params["strategy_key"]),
                symbol=str(params["symbol"]),
                timeframe=str(params.get("timeframe", "1m")),
                days=int(params.get("days", 30)),
            )
        raise ValueError(f"Unsupported job_type: {job_type}")
