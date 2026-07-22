from __future__ import annotations

from typing import Literal

JobStatus = Literal["PENDING", "RUNNING", "CANCELLING", "DONE", "FAILED", "CANCELLED"]

# A queued job is dropped outright; a running one only gets a request, because the worker
# has to reach its next checkpoint before it can stop.
CANCELLING = "CANCELLING"
CANCELLED = "CANCELLED"


class JobCancelled(Exception):
    """Raised at a checkpoint when the job was asked to stop."""

DOWNLOAD_HISTORY = "download_history"
RUN_BACKTEST = "run_backtest"
RUN_HYPEROPT = "run_hyperopt"
RUN_AI_REPORT = "run_ai_report"
TRAIN_ML_MODEL = "train_ml_model"
RUN_DENSITY_ANALYSIS = "run_density_analysis"
