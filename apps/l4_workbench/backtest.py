"""Leakage-safe prediction freezes and deterministic backtest metrics."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from .domain import ValidationError


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def create_prediction_freeze(request: dict[str, Any]) -> dict[str, Any]:
    training_years = _years(request.get("training_years"), "training_years")
    validation_years = _years(request.get("validation_years"), "validation_years")
    if max(training_years) >= min(validation_years):
        raise ValidationError("validation years must be strictly later than all training years")
    data_cutoff = str(request.get("data_cutoff", "")).strip()
    rule_version = str(request.get("rule_version", "")).strip()
    if not data_cutoff or not rule_version:
        raise ValidationError("data_cutoff and rule_version are required")
    predictions = request.get("predictions")
    if not isinstance(predictions, list) or not predictions:
        raise ValidationError("predictions must be a non-empty list")
    normalized = []
    seen = set()
    for item in predictions:
        entity_id = str(item.get("entity_id", "")).strip()
        if not entity_id or entity_id in seen or not isinstance(item.get("predicted_positive"), bool):
            raise ValidationError("each prediction needs a unique entity_id and boolean predicted_positive")
        seen.add(entity_id)
        normalized.append({
            "entity_id": entity_id,
            "predicted_positive": item["predicted_positive"],
            "score": item.get("score"),
            "reason": item.get("reason", ""),
        })
    immutable_payload = {
        "training_years": training_years,
        "validation_years": validation_years,
        "data_cutoff": data_cutoff,
        "rule_version": rule_version,
        "sample_scope": request.get("sample_scope", {}),
        "predictions": normalized,
    }
    checksum = hashlib.sha256(json.dumps(immutable_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "id": f"freeze-{uuid4().hex[:12]}",
        **immutable_payload,
        "immutable_checksum": checksum,
        "frozen_at": now(),
        "status": "frozen",
    }


def evaluate_prediction_freeze(freeze: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    observation_year = int(request.get("observation_year", 0))
    if observation_year not in freeze["validation_years"]:
        raise ValidationError("observation_year is not declared in this freeze")
    observations = request.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValidationError("observations must be a non-empty list")
    actual: dict[str, bool] = {}
    for item in observations:
        entity_id = str(item.get("entity_id", "")).strip()
        value = item.get("actual_positive")
        if not entity_id or entity_id in actual or not isinstance(value, bool):
            raise ValidationError("each observation needs a unique entity_id and boolean actual_positive")
        actual[entity_id] = value
    predicted = {item["entity_id"]: item["predicted_positive"] for item in freeze["predictions"]}
    verified_ids = sorted(actual)
    tp = [item for item in verified_ids if predicted.get(item, False) and actual[item]]
    fp = [item for item in verified_ids if predicted.get(item, False) and not actual[item]]
    fn = [item for item in verified_ids if not predicted.get(item, False) and actual[item]]
    tn = [item for item in verified_ids if not predicted.get(item, False) and not actual[item]]
    precision_denominator = len(tp) + len(fp)
    recall_denominator = len(tp) + len(fn)
    return {
        "id": f"backtest-{uuid4().hex[:12]}",
        "freeze_id": freeze["id"],
        "freeze_checksum": freeze["immutable_checksum"],
        "observation_year": observation_year,
        "evaluated_at": now(),
        "sample_count": len(verified_ids),
        "tp": len(tp), "fp": len(fp), "fn": len(fn), "tn": len(tn),
        "precision_numerator": len(tp),
        "precision_denominator": precision_denominator,
        "precision": round(len(tp) / precision_denominator, 4) if precision_denominator else None,
        "recall_numerator": len(tp),
        "recall_denominator": recall_denominator,
        "recall": round(len(tp) / recall_denominator, 4) if recall_denominator else None,
        "true_positive_ids": tp,
        "false_positive_ids": fp,
        "false_negative_ids": fn,
        "true_negative_ids": tn,
        "unverified_prediction_ids": sorted(set(predicted) - set(actual)),
    }


def _years(value: Any, label: str) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValidationError(f"{label} must be a non-empty list")
    years = sorted({int(item) for item in value})
    if any(year < 2000 or year > 2100 for year in years):
        raise ValidationError(f"{label} contains an invalid year")
    return years
