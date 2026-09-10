from __future__ import annotations

import unittest

from apps.l4_workbench.backtest import create_prediction_freeze, evaluate_prediction_freeze
from apps.l4_workbench.domain import ValidationError


class BacktestTests(unittest.TestCase):
    def request(self):
        return {
            "training_years": [2023, 2024],
            "validation_years": [2025, 2026],
            "data_cutoff": "2024-12-31",
            "rule_version": "good-question-v1",
            "sample_scope": {"subject": "初中化学", "region": "全国", "exam_type": "中考"},
            "predictions": [
                {"entity_id": "structure-a", "predicted_positive": True, "score": 0.8},
                {"entity_id": "structure-b", "predicted_positive": True, "score": 0.7},
                {"entity_id": "structure-c", "predicted_positive": False, "score": 0.3},
            ],
        }

    def test_future_years_must_be_strictly_later(self) -> None:
        request = self.request()
        request["validation_years"] = [2024]
        with self.assertRaises(ValidationError):
            create_prediction_freeze(request)

    def test_metrics_keep_denominators_and_error_lists(self) -> None:
        freeze = create_prediction_freeze(self.request())
        result = evaluate_prediction_freeze(freeze, {
            "observation_year": 2025,
            "observations": [
                {"entity_id": "structure-a", "actual_positive": True},
                {"entity_id": "structure-b", "actual_positive": False},
                {"entity_id": "structure-c", "actual_positive": True},
                {"entity_id": "structure-d", "actual_positive": True},
            ],
        })
        self.assertEqual(result["precision_numerator"], 1)
        self.assertEqual(result["precision_denominator"], 2)
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["recall_numerator"], 1)
        self.assertEqual(result["recall_denominator"], 3)
        self.assertEqual(result["false_positive_ids"], ["structure-b"])
        self.assertEqual(result["false_negative_ids"], ["structure-c", "structure-d"])

    def test_observation_year_must_be_declared(self) -> None:
        freeze = create_prediction_freeze(self.request())
        with self.assertRaises(ValidationError):
            evaluate_prediction_freeze(freeze, {"observation_year": 2027, "observations": [{"entity_id": "a", "actual_positive": True}]})


if __name__ == "__main__":
    unittest.main()
