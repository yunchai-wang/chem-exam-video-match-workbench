from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from apps.l4_workbench.domain import ValidationError
from apps.l4_workbench.label_library_sync import audit_tag_profiles, build_label_library_snapshot, dimension_tables
from apps.l4_workbench.service import WorkbenchService
from apps.l4_workbench.store import JsonStore


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"

# Synthetic rows shaped like the live tables; no real vocabulary is stored in the repo.
FAKE_ROWS = {
    "knowledge": [
        {"record_id": "k1", "层级1": "科学探究与化学实验", "层级2": "实验探究", "层级3（旧标签）": "试剂的取用", "层级4（新标签）": "固体的取用", "末级标签": ["固体的取用"], "删除/改": [], "调整记录": []},
        {"record_id": "k2", "层级1": "身边的化学物质", "层级2": "溶液", "层级3（旧标签）": "", "层级4（新标签）": "溶解度曲线", "末级标签": ["溶解度曲线"], "删除/改": ["已改"], "调整记录": ["25年5月内容有调整"]},
        {"record_id": "k3", "层级1": "身边的化学物质", "层级2": "溶液", "层级3（旧标签）": "", "层级4（新标签）": "饱和溶液", "末级标签": ["饱和溶液"], "删除/改": ["删除"], "调整记录": []},
        {"record_id": "k4", "层级1": "身边的化学物质", "层级2": "溶液", "层级3（旧标签）": "", "层级4（新标签）": "", "末级标签": [], "删除/改": [], "调整记录": []},
    ],
    "solution": [
        {"record_id": "s1", "解法标签1级": "根据控制变量法进行判断", "解法标签2级": "根据控制变量法判断需要控制的量", "末级标签": ["根据控制变量法判断需要控制的量"], "删除": ["不删除"], "父记录": [], "知识板块1级": ["科学探究与化学实验"], "知识板块2级": ["实验探究"]},
        {"record_id": "s2", "解法标签1级": "利用化学方程式计算", "解法标签2级": "利用化学方程式计算反应物/生成物的质量", "末级标签": ["利用化学方程式计算反应物/生成物的质量"], "删除": ["删除"], "父记录": [{"id": "s1"}]},
    ],
    "condition": [
        {"record_id": "c1", "条件标签1级": "溶解度", "条件标签2级-定稿": "给出3种物质的溶解度曲线", "条件标签2级": "", "末级标签": ["给出3种物质的溶解度曲线"], "删除/改": []},
        {"record_id": "c2", "条件标签1级": "溶解度", "条件标签2级-定稿": "给出3种物质的溶解度曲线", "条件标签2级": "", "末级标签": ["给出3种物质的溶解度曲线"], "删除/改": ["新增"]},
    ],
    "question": [
        {"record_id": "q1", "问题标签1级": "写化学方程式", "问题标签2级": "写化学反应方程式", "末级标签": ["写化学反应方程式"], "删除/改": []},
        {"record_id": "q2", "问题标签1级": "求科学家", "问题标签2级": "求科学家的成就", "末级标签": ["求科学家的成就"], "删除/改": ["删除"]},
        {"record_id": "q3", "问题标签1级": "判断", "问题标签2级": "判断入口", "末级标签": ["判断入口"], "删除/改": ["待定"]},
    ],
    "context": [{"record_id": "x1", "情景标签1级": "生活", "情景标签2级": "厨房", "末级标签": ["厨房"]}],
    "thinking_method": [{"record_id": "t1", "思想方法标签": "控制变量法", "末级标签": ["控制变量法"]}],
}


def write_fake_raw(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for dimension, table_id in dimension_tables().items():
        (raw_dir / f"{table_id}.records.ndjson").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in FAKE_ROWS[dimension]) + "\n", encoding="utf-8",
        )
        (raw_dir / f"{table_id}.fields.json").write_text(json.dumps({"ok": True, "data": {"fields": [{"name": "末级标签", "type": "select"}]}}), encoding="utf-8")
    (raw_dir / "fetch-report.json").write_text(json.dumps({
        "base": {"name": "初中化学六类标签验证", "revision": 36}, "fetched_at": "2026-09-16T16:52:00",
        "tables": {dimension: {"table_id": table_id, "rev": 99} for dimension, table_id in dimension_tables().items()},
    }), encoding="utf-8")


class LabelLibrarySnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.raw = Path(self.temp.name) / "raw"
        write_fake_raw(self.raw)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_statuses_old_to_new_and_duplicates_are_explicit(self) -> None:
        snapshot = build_label_library_snapshot(self.raw, values_path=Path(self.temp.name) / "values.json")
        knowledge = snapshot["vocabulary"]["knowledge"]
        self.assertEqual(knowledge["total"], 4)
        self.assertEqual(knowledge["labelled"], 3)
        self.assertEqual(knowledge["status_counts"], {"已修改": 1, "已删除": 1, "现行": 1})
        self.assertEqual(knowledge["active_count"], 2)
        self.assertEqual(snapshot["old_to_new"], [{"dimension": "knowledge", "old": "试剂的取用", "new": "固体的取用", "record_id": "k1", "status": "现行"}])
        self.assertEqual(snapshot["vocabulary"]["condition"]["duplicate_active_labels"], ["给出3种物质的溶解度曲线"])
        self.assertEqual(snapshot["vocabulary"]["question"]["status_counts"]["待定"], 1)
        self.assertEqual(snapshot["status"], "synced_local_readonly")
        self.assertEqual(snapshot["source"]["tables"]["knowledge"]["revision"], 99)
        self.assertEqual(snapshot["prompt_versions"]["question"], "2.6")
        self.assertTrue((Path(self.temp.name) / "values.json").is_file())

    def test_missing_table_refuses_to_build_a_partial_vocabulary(self) -> None:
        (self.raw / f"{dimension_tables()['question']}.records.ndjson").unlink()
        with self.assertRaises(ValidationError):
            build_label_library_snapshot(self.raw)

    def test_audit_queues_unknown_and_deprecated_tags_with_suggestions(self) -> None:
        snapshot = build_label_library_snapshot(self.raw)
        assets = [
            {"id": "a1", "tag_profile": {"knowledge": {"all": ["固体的取用", "试剂的取用", "酸碱盐"]}, "question": ["写方程式", "求科学家的成就"], "solution": [], "condition": [], "context": ["厨房"], "thinking_method": []}},
            {"id": "a2", "tag_profile": {"knowledge": {"all": ["酸碱盐"]}, "question": ["写方程式"], "solution": [], "condition": [], "context": [], "thinking_method": []}},
        ]
        audit, queue = audit_tag_profiles(assets, snapshot, [])
        self.assertEqual(audit["counts"]["knowledge"], {"matched": 1, "deprecated": 1, "unknown": 2})
        self.assertEqual(audit["counts"]["question"], {"matched": 0, "deprecated": 1, "unknown": 2})
        self.assertEqual(audit["counts"]["context"]["matched"], 1)
        by_label = {(item["dimension"], item["label"]): item for item in queue}
        old = by_label[("knowledge", "试剂的取用")]
        self.assertEqual(old["bucket"], "deprecated")
        self.assertEqual(old["suggested_labels"], ["固体的取用"])
        self.assertEqual(old["suggestion_source"], "显式旧→新映射")
        self.assertEqual(by_label[("question", "写方程式")]["occurrence_count"], 2)
        self.assertIn("写化学反应方程式", by_label[("question", "写方程式")]["suggested_labels"])
        self.assertEqual(by_label[("question", "求科学家的成就")]["bucket"], "deprecated")
        self.assertEqual(queue[0]["occurrence_count"], 2)

    def test_audit_preserves_previous_manual_mappings(self) -> None:
        snapshot = build_label_library_snapshot(self.raw)
        assets = [{"id": "a1", "tag_profile": {"knowledge": {"all": ["酸碱盐"]}, "question": [], "solution": [], "condition": [], "context": [], "thinking_method": []}}]
        _, first = audit_tag_profiles(assets, snapshot, [])
        first[0].update({"status": "已映射", "mapped_to": "溶解度曲线"})
        _, second = audit_tag_profiles(assets, snapshot, first)
        self.assertEqual(second[0]["id"], first[0]["id"])
        self.assertEqual(second[0]["status"], "已映射")
        self.assertEqual(second[0]["mapped_to"], "溶解度曲线")


class LabelLibraryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.raw = Path(self.temp.name) / "raw"
        write_fake_raw(self.raw)
        self.service = WorkbenchService(JsonStore(Path(self.temp.name) / "state.json", SEED))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_sync_freezes_snapshot_and_queue_and_mapping_validates_against_active_labels(self) -> None:
        with self.service.store.transaction() as state:
            state["question_assets"].append({"id": "a1", "source_snapshot_id": "s", "tag_profile": {"knowledge": {"all": ["酸碱盐"]}, "question": ["写方程式"], "solution": [], "condition": [], "context": [], "thinking_method": []}})
        result = self.service.sync_label_library({"raw_dir": str(self.raw)})
        self.assertFalse(result["fetched"])
        self.assertEqual(result["queue_size"], 2)
        state = self.service.get_state()
        latest = state["label_library_snapshots"][-1]
        self.assertEqual(latest["status"], "synced_local_readonly")
        self.assertIn("audit", latest)
        self.assertTrue(state["summary"]["label_library_live"])
        self.assertEqual(state["summary"]["unmatched_label_count"], 2)

        entry = next(item for item in state["unmatched_label_queue"] if item["label"] == "写方程式")
        with self.assertRaises(ValidationError):
            self.service.map_unmatched_label({"id": entry["id"], "mapped_to": "求科学家的成就"})  # deleted label
        mapped = self.service.map_unmatched_label({"id": entry["id"], "mapped_to": "写化学反应方程式", "reason": "同义"})
        self.assertEqual(mapped["status"], "已映射")
        self.assertEqual(self.service.get_state()["summary"]["unmatched_label_count"], 1)

        again = self.service.sync_label_library({"raw_dir": str(self.raw)})
        self.assertEqual(len(self.service.get_state()["label_library_snapshots"]), len(state["label_library_snapshots"]))
        remapped = next(item for item in self.service.get_state()["unmatched_label_queue"] if item["label"] == "写方程式")
        self.assertEqual(remapped["mapped_to"], "写化学反应方程式")
        self.assertEqual(again["snapshot"]["id"], latest["id"])


if __name__ == "__main__":
    unittest.main()
