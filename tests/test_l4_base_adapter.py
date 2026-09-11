from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.l4_workbench.base_adapter import LarkBaseAdapter
from apps.l4_workbench.service import WorkbenchService
from apps.l4_workbench.store import JsonStore


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"
BASE_URL = "https://example.feishu.cn/base/base123?table=tbl123&view=vew123"


def fake_runner(command: list[str]):
    action = command[2]
    if action == "+url-resolve":
        return {"ok": True, "data": {"base_token": "base123", "block_id": "tbl123"}}
    if action == "+field-list":
        return {"ok": True, "data": {"fields": [
            {"id": "f1", "name": "题目文本", "type": "text"},
            {"id": "f2", "name": "题目截图", "type": "lookup"},
            {"id": "f3", "name": "好题", "type": "select"},
            {"id": "f4", "name": "教研判定好题", "type": "select"},
        ]}}
    if action == "+view-get":
        return {"ok": True, "data": {"view": {"id": "vew123", "name": "好题视图"}}}
    if action == "+view-get-filter":
        return {"ok": True, "data": {"filter": {"logic": "and", "conditions": [["教研判定好题", "==", ["是"]]]}}}
    if action == "+view-get-visible-fields":
        return {"ok": True, "data": {"visible_fields": ["题目文本", "题目截图", "好题", "教研判定好题"]}}
    if action == "+record-list":
        return {"ok": True, "data": {
            "fields": ["题目文本", "题目截图", "好题", "教研判定好题"],
            "data": [["1. 根据[图片：装置]回答", "q1.png", ["是"], ["不符合"]]],
            "record_id_list": ["rec1"], "has_more": False, "query_context": {"record_scope": "view_filtered_records"},
        }}
    raise AssertionError(command)


class BaseAdapterTests(unittest.TestCase):
    def test_preview_keeps_view_filter_and_separate_historical_fields(self) -> None:
        preview = LarkBaseAdapter(runner=fake_runner).preview({"url": BASE_URL, "limit": 5})
        self.assertEqual(preview["view_filter"]["conditions"][0][0], "教研判定好题")
        self.assertEqual(preview["suggested_mapping"]["historical_ai_quality"], "好题")
        self.assertEqual(preview["suggested_mapping"]["historical_teacher_quality"], "教研判定好题")

    def test_import_freezes_raw_records_and_marks_remote_image_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = WorkbenchService(JsonStore(Path(folder) / "state.json", SEED), LarkBaseAdapter(runner=fake_runner))
            result = service.import_base({"url": BASE_URL, "limit": 5, "source_label": "已有真题 Base"})
            state = service.get_state()
            self.assertEqual(result["snapshot"]["status"], "frozen")
            self.assertEqual(state["question_assets"][0]["source_record_id"], "rec1")
            self.assertEqual(state["question_assets"][0]["image_integrity"], "remote_reference_unmaterialized")
            self.assertEqual(state["question_assets"][0]["normalized_fields"]["historical_ai_quality"], ["是"])
            self.assertEqual(state["question_assets"][0]["normalized_fields"]["historical_teacher_quality"], ["不符合"])


if __name__ == "__main__":
    unittest.main()
