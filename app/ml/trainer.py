from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from app.data.database import Database
from app.data.repositories import MLModelRepository
from app.ml.dataset_builder import FEATURES, build_signal_dataset


class MLTrainer:
    def __init__(self, database: Database, models_dir: str = "/app/models") -> None:
        self.database = database
        self.models_dir = Path(models_dir)

    async def train(self, *, model_type: str = "heuristic_gbdt_proxy") -> dict[str, Any]:
        async with self.database.session() as session:
            dataset = await build_signal_dataset(session)
        if len(dataset) < 300:
            return {"status": "skipped", "reason": "not_enough_data", "samples": len(dataset)}
        split = int(len(dataset) * 0.7)
        train_rows = dataset[:split]
        test_rows = dataset[split:]
        weights = _fit_feature_weights(train_rows)
        metrics = _evaluate(test_rows, weights)
        is_active = metrics["precision"] >= 0.52 and metrics["accuracy"] >= 0.5
        self.models_dir.mkdir(parents=True, exist_ok=True)
        model_path = self.models_dir / "active_model.json"
        payload = {
            "model_type": model_type,
            "features": FEATURES,
            "weights": weights,
            "metrics": metrics,
            "samples": len(dataset),
            "is_active": is_active,
        }
        model_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        async with self.database.session() as session:
            await MLModelRepository(session).add_run(
                model_type=model_type,
                features=FEATURES,
                metrics=metrics | {"samples": len(dataset)},
                model_path=str(model_path),
                is_active=is_active,
            )
        return payload


def _fit_feature_weights(rows: list[dict[str, Any]]) -> dict[str, float]:
    positives = [row["features"] for row in rows if row["target"]["hit_tp_before_sl"]]
    negatives = [row["features"] for row in rows if not row["target"]["hit_tp_before_sl"]]
    weights = {}
    for feature in FEATURES:
        pos_avg = mean([float(row.get(feature, 0.0) or 0.0) for row in positives]) if positives else 0.0
        neg_avg = mean([float(row.get(feature, 0.0) or 0.0) for row in negatives]) if negatives else 0.0
        scale = max(abs(pos_avg), abs(neg_avg), 1.0)
        weights[feature] = max(-1.0, min(1.0, (pos_avg - neg_avg) / scale))
    return weights


def _evaluate(rows: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, float]:
    if not rows:
        return {"accuracy": 0.0, "precision": 0.0}
    predictions = []
    for row in rows:
        score = _score(row["features"], weights)
        predictions.append((score >= 0.55, bool(row["target"]["hit_tp_before_sl"])))
    correct = sum(1 for predicted, actual in predictions if predicted == actual)
    predicted_positive = sum(1 for predicted, _ in predictions if predicted)
    true_positive = sum(1 for predicted, actual in predictions if predicted and actual)
    return {
        "accuracy": correct / len(predictions),
        "precision": true_positive / predicted_positive if predicted_positive else 0.0,
    }


def _score(features: dict[str, Any], weights: dict[str, float]) -> float:
    raw = 0.5
    for feature, weight in weights.items():
        value = float(features.get(feature, 0.0) or 0.0)
        raw += max(-0.05, min(0.05, value / 10 * weight))
    return max(0.0, min(1.0, raw))
