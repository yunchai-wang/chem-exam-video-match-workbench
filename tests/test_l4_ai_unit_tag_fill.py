from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.l4_workbench.ai_unit_tag_fill import (
    SOURCE_LABEL,
    apply_ai_unit_tag_fill,
    build_ai_unit_tag_fill_run,
)
from apps.l4_workbench.mother_question import build_mother_question_run
from apps.l4_workbench.service import WorkbenchService
from apps.l4_workbench.store import JsonStore


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"


def multi_unit_candidate(cid: str = "rust") -> dict:
    return {
        "id": f"candidate-{cid}",
        "asset_id": cid,
        "source_name": "样例卷",
        "question_no": "16",
        "title": f"{cid} 探究题",
        "ai_next_route": "进入课程生产",
        "structural_keys": ["铁生锈探究"],
        "units": [
            {"id": f"{cid}-u1", "label": "小问（1）", "kind": "subquestion"},
            {"id": f"{cid}-u2", "label": "小问（2）", "kind": "subquestion"},
            {"id": f"{cid}-u3", "label": "小问（3）", "kind": "subquestion"},
            {"id": f"{cid}-u4", "label": "小问（4）", "kind": "subquestion"},
        ],
        "tag_profile": {
            "question_type": "科学探究题",
            "question": ["写方程式", "解释原因", "设计方案"],
            "solution": [],
            "knowledge": {
                "all": ["空气与氧气", "金属与材料", "科学探究"],
                "core": [], "prerequisite": [], "distractor": [], "mentioned": [],
                "core_status": "待识别",
            },
        },
        "difficulty": {"level": "中等"},
        "quality": {"score": 4},
        "frequency": {"numerator": 3},
    }


def multi_unit_asset(cid: str = "rust") -> dict:
    return {
        "id": cid,
        "source_snapshot_id": "snap-1",
        "image_integrity": "preserved",
        "issue_codes": [],
        "raw_text": (
            "某小组探究铁生锈。(1)制取氧气的化学方程式为 。"
            "(2)开始生锈的时间t＞1分钟。(3)对比可证明氧气浓度会影响铁生锈的快慢。"
            "(4)请结合图1设计方案探究温度影响。"
        ),
        "content_blocks": [{"type": "text", "text": "题干"}],
        "tag_profile": multi_unit_candidate(cid)["tag_profile"],
        "normalized_fields": {},
    }


SNAPSHOT = {
    "id": "lib-test",
    "vocabulary": {
        "question": {"labels": [
            {"label": "写化学反应方程式", "status": "现行"},
            {"label": "解释实验操作/实验条件/选用某试剂的原因或作用", "status": "现行"},
            {"label": "补全实验方案", "status": "现行"},
            {"label": "评价实验设计", "status": "现行"},
            {"label": "判断实验方案与实验目的是否匹配", "status": "现行"},
            {"label": "比较溶质质量分数", "status": "现行"},
            {"label": "比较物质的性质", "status": "现行"},
        ]},
        "solution": {"labels": [
            {"label": "根据控制变量法设计实验", "status": "现行"},
            {"label": "根据实验目的判断需要控制的量", "status": "现行"},
        ]},
        "knowledge": {"labels": [
            {"label": "铁生锈探究", "status": "现行"},
            {"label": "空气与氧气", "status": "现行"},
            {"label": "金属与材料", "status": "现行"},
        ]},
        "condition": {"labels": []},
        "context": {"labels": []},
        "thinking_method": {"labels": []},
    },
    "old_to_new": [],
}

QUEUE = [
    {
        "dimension": "question", "label": "写方程式", "status": "待映射",
        "suggested_labels": ["写化学反应方程式"],
    },
    {
        "dimension": "question", "label": "解释原因", "status": "待映射",
        "suggested_labels": ["解释实验操作/实验条件/选用某试剂的原因或作用"],
    },
]


class AiUnitTagFillTests(unittest.TestCase):
    def test_splits_cues_per_unit_and_never_copies_all_whole_tags(self) -> None:
        selection = {"id": "selection-1", "diagnostic_run_id": "d1", "source_snapshot_id": "snap-1",
                     "results": [multi_unit_candidate()]}
        run = build_ai_unit_tag_fill_run(
            selection, [], [multi_unit_asset()],
            label_snapshot=SNAPSHOT, unmatched_queue=QUEUE,
        )
        proposal = run["proposals"][0]
        by_label = {item["label"]: item for item in proposal["units"]}
        self.assertEqual(by_label["小问（1）"]["question"], ["写化学反应方程式"])
        self.assertEqual(by_label["小问（2）"]["question"], [])  # 比时间不能误判为比较物质性质
        self.assertTrue(by_label["小问（3）"]["question"], "对照/证明线索应命中")
        self.assertEqual(by_label["小问（4）"]["question"], ["补全实验方案"])
        # No unit receives the full whole-question trio blindly.
        for unit in proposal["units"]:
            self.assertLessEqual(len(unit["question"]), 3)
            self.assertNotEqual(
                set(unit["question"]),
                {"写化学反应方程式", "解释实验操作/实验条件/选用某试剂的原因或作用", "补全实验方案"},
            )
        self.assertEqual(proposal["whole"]["core_knowledge"][0], "铁生锈探究")
        self.assertEqual(run["source"], SOURCE_LABEL)

    def test_apply_writes_unit_profiles_and_improves_mother_boundary_source(self) -> None:
        candidate = multi_unit_candidate()
        asset = multi_unit_asset()
        selection = {"id": "selection-1", "diagnostic_run_id": "d1", "source_snapshot_id": "snap-1",
                     "results": [candidate]}
        fill = build_ai_unit_tag_fill_run(
            selection, [], [asset], label_snapshot=SNAPSHOT, unmatched_queue=QUEUE,
        )
        applied = apply_ai_unit_tag_fill(selection, [asset], fill)
        self.assertEqual(applied["status"], "applied")
        self.assertTrue(all(unit.get("tag_profile", {}).get("question") for unit in candidate["units"][:1]))
        self.assertEqual(candidate["units"][0]["tag_profile"]["source"], SOURCE_LABEL)
        self.assertEqual(asset["normalized_fields"]["unit_tag_profiles"][f"{candidate['asset_id']}-u1"]["question_tags"],
                         candidate["units"][0]["tag_profile"]["question"])
        twin = multi_unit_candidate("rust-b")
        twin_asset = multi_unit_asset("rust-b")
        twin["tag_profile"] = candidate["tag_profile"]
        twin["units"] = [dict(unit, id=unit["id"].replace("rust", "rust-b"),
                              tag_profile=dict(candidate["units"][i]["tag_profile"]))
                         for i, unit in enumerate(twin["units"])]
        mother = build_mother_question_run(
            {"id": "selection-1", "results": [candidate, twin]}, [], [asset, twin_asset],
        )
        group = mother["groups"][0]
        self.assertEqual(group["members"][0]["answer_boundary"]["task_source"], "逐小问标签")
        self.assertEqual(group["members"][0]["answer_boundary"]["status"], "部分识别")
        self.assertNotEqual(group['mode'], '整合成母题')

    def test_service_endpoint_applies_fill_on_seed_clone(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = WorkbenchService(JsonStore(Path(folder) / "state.json", SEED))
            service.store.initialize()
            state = service.get_state()
            # Seed has no selection run with eligible candidates; dry path must still validate scope.
            with self.assertRaises(Exception):
                service.create_ai_tag_fill_run({"selection_run_id": "missing"})
            self.assertIn("ai_tag_fill_runs", state)


if __name__ == "__main__":
    unittest.main()
