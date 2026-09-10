from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from apps.l4_workbench.domain import ValidationError
from apps.l4_workbench.store import JsonStore


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"


class JsonStoreTests(unittest.TestCase):
    def test_initialize_and_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStore(Path(folder) / "state.json", SEED)
            state = store.initialize()
            state["project"]["name"] = "新的生产项目"
            store.save(state)
            self.assertEqual(store.load()["project"]["name"], "新的生产项目")

    def test_rejects_p0_as_production_priority(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStore(Path(folder) / "state.json", SEED)
            state = store.initialize()
            state["questions"][0]["production_priority"] = "P0"
            with self.assertRaises(ValidationError):
                store.save(state)

    def test_rejects_unknown_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text(SEED.read_text(encoding="utf-8"), encoding="utf-8")
            state = json.loads(path.read_text(encoding="utf-8"))
            state["project"]["intervention_strategies"]["selection"] = "sometimes"
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValidationError):
                JsonStore(path).load()


if __name__ == "__main__":
    unittest.main()
