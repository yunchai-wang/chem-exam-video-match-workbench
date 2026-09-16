from __future__ import annotations

import json
import tempfile
import threading
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

    def test_migrates_v1_state_without_changing_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = json.loads(SEED.read_text(encoding="utf-8"))
            state["schema_version"] = 1
            for key in ["metadata", "source_snapshots", "jobs", "prediction_freezes", "backtest_results"]:
                state.pop(key, None)
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            loaded = JsonStore(path).load()
            self.assertEqual(loaded["schema_version"], 12)
            self.assertEqual(loaded["project"]["default_deliverables"], ["ppt"])
            self.assertEqual(loaded["metadata"]["state_revision"], 0)
            self.assertEqual(loaded["question_assets"], [])
            self.assertEqual(loaded["diagnostic_runs"], [])
            self.assertEqual(loaded["gold_sample_sets"], [])
            self.assertEqual(loaded["video_assets"], [])
            self.assertEqual(loaded["coverage_runs"], [])
            self.assertEqual(loaded["calibration_reviews"], [])
            self.assertEqual(loaded["selection_runs"], [])
            self.assertEqual(loaded["selection_reviews"], [])
            self.assertEqual(loaded["question_sets"], [])
            self.assertEqual(loaded["downstream_tasks"], [])
            self.assertEqual(len(loaded["label_library_snapshots"]), 1)
            self.assertEqual(loaded["tag_configurations"], [])
            self.assertIsNone(loaded["project"]["active_tag_configuration_id"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)

    def test_save_creates_recoverable_backup(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStore(Path(folder) / "state.json", SEED)
            state = store.initialize()
            state["project"]["name"] = "修改后"
            store.save(state)
            backups = store.available_backups()
            self.assertEqual(len(backups), 1)
            restored = store.restore_backup(backups[0])
            self.assertEqual(restored["project"]["name"], "2026 初中化学视频迭代演示项目")
            self.assertGreater(restored["metadata"]["state_revision"], state["metadata"]["state_revision"])

    def test_schema_8_assets_and_existing_runs_receive_tag_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = json.loads(SEED.read_text(encoding="utf-8"))
            state["schema_version"] = 8
            state.pop("label_library_snapshots", None)
            state["question_assets"] = [{
                "id": "q1", "source_fields": {"knowledge_tags": "单质的概念", "task_tags": "判断物质类别"},
            }]
            state["diagnostic_runs"] = [{"id": "d1", "results": [{"asset_id": "q1"}]}]
            state["selection_runs"] = [{"id": "s1", "results": [{"asset_id": "q1"}]}]
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            loaded = JsonStore(path).load()
            self.assertEqual(loaded["question_assets"][0]["tag_profile"]["knowledge"]["all"], ["单质的概念"])
            self.assertEqual(loaded["diagnostic_runs"][0]["results"][0]["tag_profile"]["question"], ["判断物质类别"])
            self.assertEqual(loaded["selection_runs"][0]["results"][0]["label_library_snapshot_id"], "junior-chem-label-contract-2026-09-15-v2")

    def test_schema_9_adds_new_contract_without_rewriting_historical_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = json.loads(SEED.read_text(encoding="utf-8"))
            state["schema_version"] = 9
            state["label_library_snapshots"] = [{"id": "junior-chem-label-library-2026-01-28"}]
            state["question_assets"] = [{
                "id": "q1",
                "tag_profile": {
                    "library_snapshot_id": "junior-chem-label-library-2026-01-28",
                    "knowledge": {"all": ["氧气的性质"], "core": ["氧气的性质"], "distractor": [], "mentioned": []},
                },
            }]
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            loaded = JsonStore(path).load()
            self.assertEqual(loaded["schema_version"], 12)
            self.assertEqual(loaded["project"]["default_deliverables"], ["ppt"])
            self.assertEqual(len(loaded["label_library_snapshots"]), 2)
            profile = loaded["question_assets"][0]["tag_profile"]
            self.assertEqual(profile["library_snapshot_id"], "junior-chem-label-library-2026-01-28")
            self.assertEqual(profile["knowledge"]["prerequisite"], [])
            self.assertEqual(profile["contract_version"], "2026-09-15.v2")

    def test_concurrent_service_updates_do_not_lose_fields(self) -> None:
        from apps.l4_workbench.service import WorkbenchService

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            first = WorkbenchService(JsonStore(path, SEED))
            second = WorkbenchService(JsonStore(path, SEED))
            barrier = threading.Barrier(2)

            def update(service, patch):
                barrier.wait()
                service.update_project(patch)

            threads = [
                threading.Thread(target=update, args=(first, {"target_region": "南京"})),
                threading.Thread(target=update, args=(second, {"target_year": "2028"})),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            project = first.get_state()["project"]
            self.assertEqual(project["target_region"], "南京")
            self.assertEqual(project["target_year"], "2028")


if __name__ == "__main__":
    unittest.main()
