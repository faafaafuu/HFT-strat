from __future__ import annotations

from pathlib import Path


def active_model_path(models_dir: str = "/app/models") -> Path:
    return Path(models_dir) / "active_model.json"
