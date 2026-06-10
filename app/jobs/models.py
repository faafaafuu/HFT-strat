from __future__ import annotations

from typing import Literal

JobStatus = Literal["PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED"]

DOWNLOAD_HISTORY = "download_history"
RUN_BACKTEST = "run_backtest"
RUN_HYPEROPT = "run_hyperopt"
RUN_AI_REPORT = "run_ai_report"
TRAIN_ML_MODEL = "train_ml_model"
RUN_DENSITY_ANALYSIS = "run_density_analysis"
