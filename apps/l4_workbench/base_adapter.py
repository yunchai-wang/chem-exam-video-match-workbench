"""Read-only Feishu Base adapter backed by lark-cli."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .domain import ValidationError


FIELD_ALIASES = {
    "source_id": ["题目ID", "试题ID", "question_id"],
    "source_paper": ["试卷", "试卷名称", "paper"],
    "question_text": ["题目文本", "题干", "题目", "试题文本", "stem"],
    "question_image": ["题目截图", "题图", "原题图", "试题截图", "pdf_crop_path", "image_path"],
    "question_no": ["题号", "试题号", "qnum", "question_no"],
    "year": ["年份", "年度"],
    "province": ["省", "省份"],
    "city": ["市", "城市", "地区"],
    "exam_type": ["试卷类型", "考试类型"],
    "knowledge_tags": ["知识点标签-智能", "知识点标签", "知识点", "knowledge_tags"],
    "core_knowledge_tags": ["核心知识点标签", "关键知识点", "核心知识", "core_knowledge_tags"],
    "distractor_knowledge_tags": ["干扰项知识点标签", "错误选项知识点", "distractor_knowledge_tags"],
    "mentioned_knowledge_tags": ["仅提及知识点", "提及知识点", "mentioned_knowledge_tags"],
    "chapter": ["章", "章节"],
    "section": ["大节", "节"],
    "subsection": ["人教教材小节", "小节"],
    "difficulty": ["难度", "教研难度", "difficulty"],
    "question_type": ["一级题型", "题目类型", "primary_type", "question_type", "raw_qtype"],
    "score": ["分值", "score"],
    "visual_forms": ["视觉形态", "visual_forms"],
    "background_tags": ["背景素材", "background_tags"],
    "task_tags": ["设问任务", "task_tags"],
    "question_tags": ["问题标签", "question_tags"],
    "solution_tags": ["解法标签", "solution_tags"],
    "condition_tags": ["条件标签", "condition_tags"],
    "context_tags": ["情景标签", "情境标签", "context_tags"],
    "thinking_method_tags": ["思想方法标签", "thinking_method_tags"],
    "unit_tag_profiles": ["小问标签JSON", "逐小问标签", "unit_tag_profiles"],
    "method_models": ["解法模型", "method_models"],
    "source_page": ["PDF页码", "pdf_page"],
    "historical_ai_quality": ["好题", "AI好题"],
    "historical_teacher_quality": ["教研判定好题", "教研好题"],
    "historical_production_choice": ["做视频入选", "生产入选"],
    "library_has_video": ["是否已做视频", "是否有视频"],
    "new_material": ["新素材"],
    "new_form": ["新形式"],
    "new_questioning": ["新设问"],
    "ability_type": ["能力分层题型", "题型"],
}


class LarkBaseAdapter:
    """Resolve, preview and import Base records without any write command."""

    def __init__(self, runner: Callable[[list[str]], dict[str, Any]] | None = None, executable: str = "lark-cli") -> None:
        self.runner = runner
        self.executable = executable

    def preview(self, request: dict[str, Any]) -> dict[str, Any]:
        url = str(request.get("url", "")).strip()
        if not url.startswith("https://"):
            raise ValidationError("Base preview requires an https URL")
        limit = int(request.get("limit", 5))
        if limit < 1 or limit > 200:
            raise ValidationError("Base preview limit must be between 1 and 200")
        coordinates = self._resolve(url)
        base_token = str(request.get("base_token") or coordinates["base_token"])
        table_id = str(request.get("table_id") or coordinates.get("table_id") or "")
        view_id = str(request.get("view_id") or coordinates.get("view_id") or "") or None
        if not table_id:
            raise ValidationError("Base URL does not identify a table; select a table before preview")

        field_payload = self._command("base", "+field-list", "--base-token", base_token, "--table-id", table_id, "--limit", "200")
        fields = field_payload.get("data", {}).get("fields", [])
        if not isinstance(fields, list):
            raise ValidationError("Base field response is invalid")
        field_names = [str(item.get("name")) for item in fields if item.get("name")]
        visible_fields = list(field_names)
        view_filter = None
        view_name = None
        if view_id:
            try:
                view_payload = self._command("base", "+view-get", "--base-token", base_token, "--table-id", table_id, "--view-id", view_id)
                view_name = view_payload.get("data", {}).get("view", {}).get("name")
                filter_payload = self._command("base", "+view-get-filter", "--base-token", base_token, "--table-id", table_id, "--view-id", view_id)
                view_filter = filter_payload.get("data", {}).get("filter")
                visible_payload = self._command("base", "+view-get-visible-fields", "--base-token", base_token, "--table-id", table_id, "--view-id", view_id)
                visible_fields = visible_payload.get("data", {}).get("visible_fields") or visible_fields
            except ValidationError:
                view_filter = None

        record_args = ["base", "+record-list", "--base-token", base_token, "--table-id", table_id, "--limit", str(limit)]
        if view_id:
            record_args.extend(["--view-id", view_id])
        for field_name in visible_fields:
            record_args.extend(["--field-id", str(field_name)])
        record_payload = self._command(*record_args)
        data = record_payload.get("data", {})
        matrix = data.get("data", [])
        returned_fields = data.get("fields", [])
        record_ids = data.get("record_id_list", [])
        records = []
        for index, row in enumerate(matrix):
            records.append({
                "record_id": record_ids[index] if index < len(record_ids) else f"row-{index + 1}",
                "fields": {name: row[position] if position < len(row) else None for position, name in enumerate(returned_fields)},
            })
        return {
            "adapter": "lark-cli-readonly-v1", "url": url, "base_token": base_token, "table_id": table_id,
            "table_name": request.get("table_name"), "view_id": view_id, "view_name": view_name,
            "view_filter": view_filter, "visible_fields": visible_fields, "fields": fields,
            "records": records, "record_count": len(records), "has_more": bool(data.get("has_more")),
            "query_context": data.get("query_context", {}), "suggested_mapping": suggest_mapping(field_names),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _resolve(self, url: str) -> dict[str, Any]:
        query = parse_qs(urlparse(url).query)
        payload = self._command("base", "+url-resolve", "--url", url)
        data = payload.get("data", {})
        base_token = data.get("base_token")
        if not base_token:
            raise ValidationError("Unable to resolve Base token")
        return {
            "base_token": base_token,
            "table_id": (query.get("table") or [data.get("block_id")])[0],
            "view_id": (query.get("view") or [None])[0],
        }

    def _command(self, *args: str) -> dict[str, Any]:
        command = [self.executable, *args, "--as", "user", "--format", "json"]
        if self.runner:
            result = self.runner(command)
        else:
            if not shutil.which(self.executable):
                raise ValidationError("lark-cli is unavailable; install or configure it before Base preview")
            try:
                completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=90)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ValidationError(f"Base read failed: {error}") from error
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "unknown error").strip()[-800:]
                raise ValidationError(f"Base read failed: {detail}")
            try:
                result = json.loads(completed.stdout)
            except json.JSONDecodeError as error:
                raise ValidationError("lark-cli returned invalid JSON") from error
        if not result.get("ok", False):
            raise ValidationError(f"Base read failed: {result.get('error') or 'unknown error'}")
        return result


def suggest_mapping(field_names: list[str]) -> dict[str, str]:
    normalized = {name.strip().lower(): name for name in field_names}
    mapping: dict[str, str] = {}
    for canonical, aliases in FIELD_ALIASES.items():
        match = next((normalized[alias.lower()] for alias in aliases if alias.lower() in normalized), None)
        if match:
            mapping[canonical] = match
    return mapping
