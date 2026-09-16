"""Application service for project settings, runs, reviews and learning loops."""

from __future__ import annotations

import json
from datetime import datetime
from functools import wraps
from pathlib import Path
from time import sleep
from typing import Any
from uuid import uuid4

from .domain import INTERVENTION_STRATEGIES, STAGE_LABELS, STAGES, ValidationError
from .diagnosis import build_diagnostic_run, select_gold_sample
from .backtest import create_prediction_freeze, evaluate_prediction_freeze
from .base_adapter import LarkBaseAdapter
from .engine import affected_stages, classify_feedback, recommend_priority, release_decision
from .jobs import StageExecutionError, StageJobRunner
from .manifest_adapter import LocalManifestAdapter
from .pipeline import SourceSnapshotManager
from .standardization import DocumentStandardizer, stable_id
from .store import ConcurrentUpdateError, JsonStore
from .video_evidence import build_coverage_run, build_video_import, load_video_records
from .calibration import build_calibration_review
from .selection import build_selection_review, build_selection_run
from .downstream import create_downstream_task
from .mother_question import build_mother_question_review, build_mother_question_run
from .tag_configuration import build_tag_configuration
from .output_planning import DELIVERABLES, normalize_deliverables, public_catalog, required_stages_for
from .skill_routing import SkillRegistry, normalize_lesson_type
from .stage_executors import build_stage_executors
from .exports import build_mother_question_docx, build_question_set_docx, build_selection_docx


ARTIFACT_OUTPUT_KINDS = {"docx", "markdown", "json", "csv", "pptx", "html", "pdf", "folder", "other"}


PROJECT_FIELDS = {
    "name", "subject", "grade", "target_students", "target_region",
    "target_exam_type", "target_year", "content_scope", "planned_artifact",
    "problem_to_solve", "maturity", "intervention_scope", "default_deliverables",
    "lesson_type",
}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def retry_concurrent_updates(function):
    """Retry a pure local state mutation when another request committed first."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        last_error = None
        for attempt in range(4):
            try:
                return function(*args, **kwargs)
            except ConcurrentUpdateError as error:
                last_error = error
                sleep(0.005 * (attempt + 1))
        raise last_error
    return wrapped


class WorkbenchService:
    def __init__(
        self,
        store: JsonStore,
        base_adapter: LarkBaseAdapter | None = None,
        manifest_adapter: LocalManifestAdapter | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.store = store
        self.store.initialize()
        self.snapshots = SourceSnapshotManager(self.store.path.parent / "source_snapshots")
        self.standardizer = DocumentStandardizer(self.store.path.parent)
        self.base_adapter = base_adapter or LarkBaseAdapter()
        self.manifest_adapter = manifest_adapter or LocalManifestAdapter()
        self.skills = skill_registry or SkillRegistry()
        self.jobs = StageJobRunner(build_stage_executors(self.skills))

    def get_state(self) -> dict[str, Any]:
        state = self.store.load()
        state["summary"] = self._summary(state)
        state["output_catalog"] = public_catalog()
        state["skill_catalog"] = self.skills.catalog(state["project"].get("lesson_type", "problem"))
        return state

    @retry_concurrent_updates
    def update_project(self, patch: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        project = state["project"]
        unknown = set(patch) - PROJECT_FIELDS - {"intervention_strategies", "preauthorizations"}
        if unknown:
            raise ValidationError(f"unknown project fields: {', '.join(sorted(unknown))}")
        for key in PROJECT_FIELDS:
            if key in patch:
                if key == "default_deliverables":
                    project[key] = normalize_deliverables(patch[key])
                elif key == "lesson_type":
                    project[key] = normalize_lesson_type(patch[key])
                else:
                    project[key] = patch[key]
        if "intervention_strategies" in patch:
            strategies = patch["intervention_strategies"]
            if not isinstance(strategies, dict):
                raise ValidationError("intervention_strategies must be an object")
            for stage, strategy in strategies.items():
                if stage not in STAGES or strategy not in INTERVENTION_STRATEGIES:
                    raise ValidationError(f"invalid strategy: {stage}={strategy}")
                project["intervention_strategies"][stage] = strategy
        if "preauthorizations" in patch:
            permissions = patch["preauthorizations"]
            if not isinstance(permissions, dict):
                raise ValidationError("preauthorizations must be an object")
            for key, value in permissions.items():
                if key not in project["preauthorizations"] or not isinstance(value, bool):
                    raise ValidationError(f"invalid preauthorization: {key}")
                project["preauthorizations"][key] = value
        self._event(state, "project.updated", "生产项目设置已更新")
        self.store.save(state)
        return self.get_state()

    def create_source_snapshot(self, request: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.snapshots.capture(request)
        with self.store.transaction() as state:
            state["source_snapshots"].append(snapshot)
            self._event(state, "source.snapshot_created", f"已冻结来源快照：{snapshot['source_label']}")
        return snapshot

    @retry_concurrent_updates
    def create_tag_configuration(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        payload = dict(request)
        payload.setdefault("subject", state["project"].get("subject"))
        config = build_tag_configuration(payload)
        state["tag_configurations"] = [item for item in state["tag_configurations"] if item["id"] != config["id"]]
        state["tag_configurations"].append(config)
        state["project"]["active_tag_configuration_id"] = config["id"]
        self._event(state, "tag_configuration.activated", f"已启用标签配置：{config['name']}（{config['onboarding_mode_label']}）")
        self.store.save(state)
        return config

    def standardize_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        state = self.store.load()
        snapshot = self._find(state["source_snapshots"], snapshot_id, "source snapshot")
        existing = next((item for item in state["standardization_runs"] if item["source_snapshot_id"] == snapshot_id and item["source_checksum"] == snapshot["immutable_checksum"]), None)
        if existing:
            return self._standardization_payload(state, existing)
        result = self.standardizer.standardize(snapshot)
        with self.store.transaction() as current:
            existing = next((item for item in current["standardization_runs"] if item["id"] == result["run"]["id"]), None)
            if existing:
                return self._standardization_payload(current, existing)
            self._merge_standardization(current, result)
            self._event(current, "source.standardized", f"已形成 {result['run']['question_asset_count']} 个标准题目资产")
        return result

    def preview_base(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.base_adapter.preview(request)

    def import_base(self, request: dict[str, Any]) -> dict[str, Any]:
        preview = self.base_adapter.preview(request)
        mapping = request.get("mapping") or preview["suggested_mapping"]
        if not isinstance(mapping, dict):
            raise ValidationError("mapping must be an object")
        known_fields = {field["name"] for field in preview["fields"]}
        unknown = {str(value) for value in mapping.values() if value and value not in known_fields}
        if unknown:
            raise ValidationError(f"mapping references unknown Base fields: {', '.join(sorted(unknown))}")
        if not mapping.get("question_text"):
            raise ValidationError("mapping.question_text is required")
        snapshot_request = {
            "source_type": "feishu_base", "source_label": request.get("source_label") or "Feishu Base 题目",
            "url": request.get("url"), "data_cutoff": request.get("data_cutoff"), "limit": request.get("limit", 20),
        }
        snapshot = self.snapshots.capture_remote_export(snapshot_request, preview)
        result = self.standardizer.standardize_base_records(snapshot, preview, mapping)
        mapping_record = {
            "id": stable_id("mapping", preview["base_token"], preview["table_id"], preview.get("view_id"), json.dumps(mapping, ensure_ascii=False, sort_keys=True)),
            "source_type": "feishu_base", "base_token": preview["base_token"], "table_id": preview["table_id"],
            "view_id": preview.get("view_id"), "view_filter": preview.get("view_filter"), "mapping": mapping,
            "source_fields": preview["fields"], "created_at": now(),
        }
        with self.store.transaction() as state:
            state["source_snapshots"].append(snapshot)
            state["field_mappings"] = [item for item in state["field_mappings"] if item["id"] != mapping_record["id"]]
            state["field_mappings"].append(mapping_record)
            self._merge_standardization(state, result)
            suffix = "（当前仅为截断样本）" if snapshot.get("has_more") else ""
            self._event(state, "base.imported", f"已从 Base 冻结并标准化 {len(result['question_assets'])} 条记录{suffix}")
        return {"snapshot": snapshot, "mapping": mapping_record, **result}

    def preview_manifest(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.manifest_adapter.preview(request)

    def import_manifest(self, request: dict[str, Any]) -> dict[str, Any]:
        preview = self.manifest_adapter.preview(request)
        mapping = request.get("mapping") or preview["suggested_mapping"]
        if not isinstance(mapping, dict):
            raise ValidationError("mapping must be an object")
        known_fields = {field["name"] for field in preview["fields"]}
        unknown = {str(value) for value in mapping.values() if value and value not in known_fields}
        if unknown:
            raise ValidationError(f"mapping references unknown manifest fields: {', '.join(sorted(unknown))}")
        if not mapping.get("question_text"):
            raise ValidationError("mapping.question_text is required")

        manifest_path = Path(preview["path"])
        records = self.manifest_adapter.load_records(manifest_path)
        image_paths = self.manifest_adapter.image_paths(records, mapping.get("question_image"), manifest_path.parent)
        snapshot = self.snapshots.capture({
            "source_type": "local_manifest",
            "source_label": request.get("source_label") or manifest_path.stem,
            "data_cutoff": request.get("data_cutoff"),
            "manifest_name": manifest_path.name,
            "paths": [str(manifest_path), *(str(path) for path in image_paths)],
        })
        result = self.standardizer.standardize_manifest_records(snapshot, manifest_path, records, mapping)
        mapping_record = {
            "id": stable_id("mapping", snapshot["immutable_checksum"], json.dumps(mapping, ensure_ascii=False, sort_keys=True)),
            "source_type": "local_manifest", "manifest_name": manifest_path.name,
            "mapping": mapping, "source_fields": preview["fields"], "image_report": preview["image_report"], "created_at": now(),
        }
        with self.store.transaction() as state:
            state["source_snapshots"].append(snapshot)
            state["field_mappings"].append(mapping_record)
            self._merge_standardization(state, result)
            self._event(
                state,
                "manifest.imported",
                f"已从本地结构化清单冻结 {len(records)} 条记录和 {len(image_paths)} 张唯一题图",
            )
        return {"snapshot": snapshot, "mapping": mapping_record, "image_report": preview["image_report"], **result}

    def asset_path(self, relative_path: str) -> Path:
        target = (self.store.path.parent / relative_path).resolve()
        root = self.store.path.parent.resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValidationError("unknown local asset")
        return target

    def diagnose_snapshot(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        snapshot_id = str(request.get("source_snapshot_id") or "")
        snapshot = self._find(state["source_snapshots"], snapshot_id, "source snapshot")
        assets = [item for item in state["question_assets"] if item.get("source_snapshot_id") == snapshot_id]
        result = build_diagnostic_run(snapshot, assets)
        existing = next((item for item in state["diagnostic_runs"] if item["id"] == result["id"]), None)
        if existing:
            return existing
        with self.store.transaction() as current:
            current["diagnostic_runs"].append(result)
            self._event(current, "diagnosis.completed", f"已对 {len(assets)} 个真实题目资产完成首轮可解释诊断")
        return result

    def create_gold_sample(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        diagnostic_run_id = str(request.get("diagnostic_run_id") or "")
        diagnostic_run = self._find(state["diagnostic_runs"], diagnostic_run_id, "diagnostic run")
        assets = [
            item for item in state["question_assets"]
            if item.get("source_snapshot_id") == diagnostic_run["source_snapshot_id"]
        ]
        sample = select_gold_sample(diagnostic_run, assets, int(request.get("size", 40)))
        existing = next((item for item in state["gold_sample_sets"] if item["id"] == sample["id"]), None)
        if existing:
            return existing
        with self.store.transaction() as current:
            current["gold_sample_sets"].append(sample)
            self._event(current, "gold_sample.created", f"已生成 {sample['actual_size']} 题待校准金样本")
        return sample

    def import_video_manifest(self, request: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(request.get("path") or "")).expanduser().resolve()
        records = load_video_records(path)
        snapshot = self.snapshots.capture({
            "source_type": "video_manifest", "source_label": request.get("source_label") or path.stem,
            "data_cutoff": request.get("data_cutoff"), "paths": [str(path)],
        })
        result = build_video_import(snapshot, records)
        with self.store.transaction() as state:
            state["source_snapshots"].append(snapshot)
            state["video_imports"].append({key: value for key, value in result.items() if key != "video_assets"})
            # The workbench has one active evidence library. Historical coverage
            # runs keep their embedded candidates, while the active library is
            # replaced atomically so re-imports cannot inflate asset counts.
            state["video_assets"] = result["video_assets"]
            self._event(
                state,
                "video_manifest.imported",
                f"已冻结 {result['listing_count']} 条课库目录记录，形成 {result['video_asset_count']} 个去重视频实体",
            )
        return {"snapshot": snapshot, **result}

    def diagnose_coverage(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        diagnostic_run = self._find(state["diagnostic_runs"], str(request.get("diagnostic_run_id") or ""), "diagnostic run")
        gold_sample = self._find(state["gold_sample_sets"], str(request.get("gold_sample_id") or ""), "gold sample")
        video_import = self._find(state["video_imports"], str(request.get("video_import_id") or ""), "video import")
        videos = [item for item in state["video_assets"] if item.get("source_snapshot_id") == video_import["source_snapshot_id"]]
        result = build_coverage_run(diagnostic_run, gold_sample, video_import, videos)
        existing = next((item for item in state["coverage_runs"] if item["id"] == result["id"]), None)
        if existing:
            return existing
        assets = [item for item in state["question_assets"] if item.get("source_snapshot_id") == diagnostic_run["source_snapshot_id"]]
        with self.store.transaction() as current:
            current["coverage_runs"].append(result)
            selection = build_selection_run(diagnostic_run, gold_sample, result, assets, current["calibration_reviews"])
            current["selection_runs"].append(selection)
            self._event(current, "coverage.completed", f"已对 {result['result_count']} 道金样本题运行保守视频覆盖候选")
            self._event(current, "selection.completed", f"已自动形成 {selection['result_count']} 道真实题目的生产候选判断")
        return result

    def create_selection_run(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        diagnostic_run, gold_sample, coverage_run = self._calibration_context(state, request)
        assets = [item for item in state["question_assets"] if item.get("source_snapshot_id") == diagnostic_run["source_snapshot_id"]]
        result = build_selection_run(diagnostic_run, gold_sample, coverage_run, assets, state["calibration_reviews"])
        existing = next((item for item in state["selection_runs"] if item["id"] == result["id"]), None)
        if existing:
            return existing
        with self.store.transaction() as current:
            current["selection_runs"].append(result)
            self._event(current, "selection.completed", f"已形成 {result['result_count']} 道真实题目的生产候选判断")
        return result

    def save_calibration(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        diagnostic_run, gold_sample, coverage_run = self._calibration_context(state, request)
        asset = self._find(state["question_assets"], str(request.get("asset_id") or ""), "question asset")
        review = build_calibration_review(diagnostic_run, gold_sample, coverage_run, asset, request)
        with self.store.transaction() as current:
            current["calibration_reviews"] = [item for item in current["calibration_reviews"] if item["id"] != review["id"]]
            current["calibration_reviews"].append(review)
            action = "纠正" if review["corrected_fields"] else "确认"
            self._event(current, "calibration.saved", f"已{action}金样本：{review['source_name']} 第 {review['question_no']} 题")
            self._append_refreshed_selection(current, diagnostic_run, gold_sample, coverage_run)
        return review

    def batch_pass_calibrations(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        diagnostic_run, gold_sample, coverage_run = self._calibration_context(state, request)
        assets = {item["id"]: item for item in state["question_assets"]}
        requested_ids = set(request.get("asset_ids") or [item["asset_id"] for item in gold_sample["items"]])
        sample_ids = {item["asset_id"] for item in gold_sample["items"]}
        if not requested_ids <= sample_ids:
            raise ValidationError("batch pass contains assets outside the gold sample")
        passed = []
        skipped = []
        for asset_id in requested_ids:
            asset = assets.get(asset_id)
            if asset is None:
                raise ValidationError("question asset not found")
            if asset.get("issue_codes"):
                skipped.append(asset_id)
                continue
            passed.append(build_calibration_review(
                diagnostic_run, gold_sample, coverage_run, asset, {}, mode="batch_pass",
            ))
        with self.store.transaction() as current:
            passed_ids = {item["id"] for item in passed}
            current["calibration_reviews"] = [item for item in current["calibration_reviews"] if item["id"] not in passed_ids] + passed
            self._event(current, "calibration.batch_passed", f"已批量通过 {len(passed)} 题，保留 {len(skipped)} 个异常对象待复核")
            self._append_refreshed_selection(current, diagnostic_run, gold_sample, coverage_run)
        return {"passed_count": len(passed), "skipped_count": len(skipped), "skipped_asset_ids": skipped, "reviews": passed}

    def save_selection_review(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        selection_run = self._find(state["selection_runs"], str(request.get("selection_run_id") or ""), "selection run")
        review = build_selection_review(selection_run, request)
        with self.store.transaction() as current:
            current["selection_reviews"] = [item for item in current["selection_reviews"] if item["id"] != review["id"]]
            current["selection_reviews"].append(review)
            action = "纠正" if review["status"] == "corrected" else "确认"
            self._event(current, "selection.reviewed", f"已{action}候选题生产去向：{review['asset_id']}")
        return review

    def batch_pass_selections(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        selection_run = self._find(state["selection_runs"], str(request.get("selection_run_id") or ""), "selection run")
        existing = {item["candidate_id"]: item for item in state["selection_reviews"] if item["selection_run_id"] == selection_run["id"]}
        passed, skipped = [], []
        for candidate in selection_run["results"]:
            if candidate["exception"] or existing.get(candidate["id"], {}).get("status") == "corrected":
                skipped.append(candidate["id"])
                continue
            passed.append(build_selection_review(selection_run, {"candidate_id": candidate["id"]}, mode="batch_pass"))
        with self.store.transaction() as current:
            passed_ids = {item["id"] for item in passed}
            current["selection_reviews"] = [item for item in current["selection_reviews"] if item["id"] not in passed_ids] + passed
            self._event(current, "selection.batch_passed", f"已批量通过 {len(passed)} 个非异常候选，保留 {len(skipped)} 个对象")
        return {"passed_count": len(passed), "skipped_count": len(skipped), "reviews": passed}

    def create_downstream_task(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        selection_run = self._find(state["selection_runs"], str(request.get("selection_run_id") or ""), "selection run")
        result = create_downstream_task(
            selection_run, state["selection_reviews"], state["question_assets"], state["project"], request,
        )
        with self.store.transaction() as current:
            question_set = result["question_set"]
            task = result["task"]
            current["question_sets"] = [item for item in current["question_sets"] if item["id"] != question_set["id"]]
            current["question_sets"].append(question_set)
            current["downstream_tasks"] = [item for item in current["downstream_tasks"] if item["id"] != task["id"]]
            current["downstream_tasks"].append(task)
            self._event(current, "downstream.task_created", f"已冻结 {question_set['item_count']} 道题并创建{task['task_type']}任务契约")
        return result

    def create_mother_question_run(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        selection_run = self._find(state["selection_runs"], str(request.get("selection_run_id") or ""), "selection run")
        diagnostic_run = next((item for item in state["diagnostic_runs"] if item["id"] == selection_run.get("diagnostic_run_id")), None)
        assets = [item for item in state["question_assets"] if item.get("source_snapshot_id") == selection_run.get("source_snapshot_id")]
        result = build_mother_question_run(selection_run, state["selection_reviews"], assets, diagnostic_run)
        existing = next((item for item in state["mother_question_runs"] if item["id"] == result["id"]), None)
        if existing:
            return existing
        with self.store.transaction() as current:
            if any(item["id"] == result["id"] for item in current["mother_question_runs"]):
                return result
            current["mother_question_runs"].append(result)
            summary = result["summary"]
            self._event(
                current, "mother_question.proposed",
                f"已从 {summary['eligible_candidate_count']} 道有效候选形成 {summary['mother_group_count']} 组母题提案、"
                f"{summary['progressive_group_count']} 组递进题组；原题图表 {summary['retained_figure_count']}/{summary['source_figure_count']} 全部保留",
            )
        return result

    def save_mother_question_review(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        run = self._find(state["mother_question_runs"], str(request.get("mother_question_run_id") or ""), "mother question run")
        review = build_mother_question_review(run, request)
        with self.store.transaction() as current:
            current["mother_question_reviews"] = [item for item in current["mother_question_reviews"] if item["id"] != review["id"]]
            current["mother_question_reviews"].append(review)
            action = "纠正" if review["status"] == "corrected" else "确认"
            self._event(current, "mother_question.reviewed", f"已{action}母题分组：{review['group_id']}（{review['mode']}）")
        return review

    def batch_confirm_mother_question_groups(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        run = self._find(state["mother_question_runs"], str(request.get("mother_question_run_id") or ""), "mother question run")
        existing = {item["group_id"]: item for item in state["mother_question_reviews"] if item["mother_question_run_id"] == run["id"]}
        passed, skipped = [], []
        for group in run["groups"]:
            if group["exception"] or existing.get(group["id"], {}).get("status") == "corrected":
                skipped.append(group["id"])
                continue
            passed.append(build_mother_question_review(run, {"group_id": group["id"]}, mode="batch_confirm"))
        with self.store.transaction() as current:
            passed_ids = {item["id"] for item in passed}
            current["mother_question_reviews"] = [item for item in current["mother_question_reviews"] if item["id"] not in passed_ids] + passed
            self._event(current, "mother_question.batch_confirmed", f"已批量确认 {len(passed)} 组非异常母题提案，保留 {len(skipped)} 组待处理")
        return {"passed_count": len(passed), "skipped_count": len(skipped), "skipped_group_ids": skipped, "reviews": passed}

    # -- editable working copies ---------------------------------------------
    def export_selection_docx(self, selection_run_id: str) -> tuple[bytes, str, dict[str, Any]]:
        state = self.store.load()
        selection = self._find(state["selection_runs"], selection_run_id, "selection run")
        payload, report = build_selection_docx(selection, state["selection_reviews"], state["question_assets"], self.store.path.parent)
        return payload, f"候选池-{selection['id']}.docx", report

    def export_question_set_docx(self, question_set_id: str) -> tuple[bytes, str, dict[str, Any]]:
        state = self.store.load()
        question_set = self._find(state["question_sets"], question_set_id, "question set")
        task = next((item for item in state["downstream_tasks"] if item.get("question_set_id") == question_set["id"]), None)
        payload, report = build_question_set_docx(question_set, task, state["question_assets"], self.store.path.parent)
        return payload, f"{question_set['name']}-{question_set['version']}.docx", report

    def export_mother_question_docx(self, run_id: str) -> tuple[bytes, str, dict[str, Any]]:
        state = self.store.load()
        run = self._find(state["mother_question_runs"], run_id, "mother question run")
        payload, report = build_mother_question_docx(run, state["mother_question_reviews"], state["question_assets"], self.store.path.parent)
        return payload, f"经典母题整合审核稿-{run['id']}.docx", report

    # -- skill output write-back and teacher confirmation ---------------------
    @retry_concurrent_updates
    def register_artifact_outputs(self, artifact_id: str, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        artifact = self._find(state["artifacts"], artifact_id, "artifact")
        outputs = request.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise ValidationError("outputs must be a non-empty list")
        recorded = []
        for item in outputs:
            if not isinstance(item, dict) or not str(item.get("path") or "").strip():
                raise ValidationError("each output requires a path")
            kind = str(item.get("kind") or "other")
            if kind not in ARTIFACT_OUTPUT_KINDS:
                raise ValidationError(f"invalid output kind: {kind}")
            path = Path(str(item["path"]).strip()).expanduser()
            recorded.append({
                "id": new_id("output"), "path": str(path), "kind": kind,
                "exists_on_register": path.exists(),
                "produced_by": str(item.get("produced_by") or request.get("produced_by") or "agent"),
                "skill": str(item.get("skill") or request.get("skill") or ""),
                "note": str(item.get("note") or "").strip(),
                "artifact_version": artifact["version"], "registered_at": now(),
            })
        artifact.setdefault("outputs", []).extend(recorded)
        artifact["confirmation"] = None
        artifact["status"] = "Skill 产出已回填，待教师确认（非正式成品）"
        artifact["updated_at"] = now()
        missing = sum(not item["exists_on_register"] for item in recorded)
        suffix = f"，其中 {missing} 个路径当前不可读" if missing else ""
        self._event(state, "artifact.outputs_registered", f"已为“{artifact['kind']}”回填 {len(recorded)} 个 Skill 产出{suffix}")
        self.store.save(state)
        return artifact

    @retry_concurrent_updates
    def confirm_artifact(self, artifact_id: str, request: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        artifact = self._find(state["artifacts"], artifact_id, "artifact")
        current_outputs = [item for item in artifact.get("outputs", []) if item["artifact_version"] == artifact["version"]]
        if not current_outputs:
            raise ValidationError("当前版本还没有回填任何 Skill 产出，不能确认执行契约或执行包本身")
        artifact["confirmation"] = {
            "version": artifact["version"], "confirmed_at": now(), "confirmed_by": "teacher",
            "reason": str(request.get("reason") or "").strip(),
            "primary_output_id": str(request.get("primary_output_id") or current_outputs[0]["id"]),
        }
        if artifact["confirmation"]["primary_output_id"] not in {item["id"] for item in current_outputs}:
            raise ValidationError("primary_output_id must reference an output of the current version")
        artifact["status"] = f"教师已确认 V{artifact['version']}"
        artifact["updated_at"] = now()
        self._event(state, "artifact.confirmed", f"教师已确认“{artifact['kind']}” V{artifact['version']}；下游阶段可读取该版本")
        self.store.save(state)
        return artifact

    def freeze_predictions(self, request: dict[str, Any]) -> dict[str, Any]:
        freeze = create_prediction_freeze(request)
        with self.store.transaction() as state:
            state["prediction_freezes"].append(freeze)
            self._event(state, "backtest.predictions_frozen", f"已冻结预测：{freeze['id']}")
        return freeze

    def evaluate_predictions(self, freeze_id: str, request: dict[str, Any]) -> dict[str, Any]:
        with self.store.transaction() as state:
            freeze = self._find(state["prediction_freezes"], freeze_id, "prediction freeze")
            result = evaluate_prediction_freeze(freeze, request)
            state["backtest_results"].append(result)
            self._event(state, "backtest.completed", f"已完成真实后验回测：{freeze_id}")
        return result

    @retry_concurrent_updates
    def update_question(self, question_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        question = self._find(state["questions"], question_id, "question")
        allowed = {
            "selected_for_candidate",
            "teacher_quality_conclusion",
            "teacher_coverage_conclusion",
            "teacher_feedback_reason",
            "selected_unit_ids",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValidationError(f"unknown question fields: {', '.join(sorted(unknown))}")
        if "selected_for_candidate" in patch:
            question["selected_for_candidate"] = bool(patch["selected_for_candidate"])
        if "teacher_quality_conclusion" in patch:
            question["quality"]["teacher_conclusion"] = str(patch["teacher_quality_conclusion"])
        if "teacher_coverage_conclusion" in patch:
            question["coverage"]["teacher_conclusion"] = str(patch["teacher_coverage_conclusion"])
        if "teacher_feedback_reason" in patch:
            question["teacher_feedback_reason"] = str(patch["teacher_feedback_reason"]).strip()
        if "selected_unit_ids" in patch:
            selected = set(patch["selected_unit_ids"])
            for unit in question.get("units", []):
                unit["selected"] = unit["id"] in selected
        priority, reason, intervention = recommend_priority(question, state["project"])
        question["production_priority"] = priority
        question["priority_reason"] = reason
        question["intervention"] = intervention
        self._event(state, "question.updated", f"已更新题目：{question['title']}")
        self.store.save(state)
        return question

    @retry_concurrent_updates
    def start_run(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        state = self.store.load()
        request = request or {}
        deliverables = normalize_deliverables(
            request.get("deliverables", state["project"]["default_deliverables"])
        )
        required_stages = required_stages_for(deliverables)
        for question in state["questions"]:
            priority, reason, intervention = recommend_priority(question, state["project"])
            question["production_priority"] = priority
            question["priority_reason"] = reason
            question["intervention"] = intervention
        run = {
            "id": new_id("run"), "mode": "full", "status": "running", "created_at": now(),
            "current_stage": required_stages[0],
            "stage_states": {stage: ("pending" if stage in required_stages else "not_requested") for stage in STAGES},
            "requested_deliverables": deliverables,
            "required_stages": required_stages,
            "skipped_stages": [stage for stage in STAGES if stage not in required_stages],
            "delivery_scope": "once",
            "rule_version": state["project"]["rule_version"],
            "source_snapshot_ids": [item["id"] for item in state["source_snapshots"]],
            "selected_question_ids": [q["id"] for q in state["questions"] if q.get("selected_for_candidate")],
        }
        state["runs"].append(run)
        labels = "、".join(DELIVERABLES[item]["label"] for item in deliverables)
        self._event(state, "run.started", f"AI 已按本次交付目标开始运行：{labels}", run["id"])
        self._advance(state, run, 0)
        self.store.save(state)
        return run

    @retry_concurrent_updates
    def approve_review(self, review_id: str) -> dict[str, Any]:
        state = self.store.load()
        review = self._find(state["reviews"], review_id, "review")
        if review["status"] != "待确认":
            raise ValidationError("review is not waiting for confirmation")
        review["status"] = "已通过"
        review["resolved_at"] = now()
        run = self._find(state["runs"], review["run_id"], "run")
        stage = review["stage"]
        if run["stage_states"].get(stage) == "blocked":
            try:
                self.jobs.execute(state, run, stage)
            except StageExecutionError as error:
                run["stage_states"][stage] = "failed"
                run["status"] = "failed"
                run["error"] = str(error)
                self._event(state, "run.failed", f"{STAGE_LABELS[stage]}执行失败：{error}", run["id"])
                self.store.save(state)
                return run
        run["stage_states"][stage] = "completed"
        run["status"] = "running"
        self._event(state, "review.approved", f"已通过{STAGE_LABELS[stage]}节点", run["id"])
        self._advance(state, run, STAGES.index(stage) + 1)
        self.store.save(state)
        return run

    @retry_concurrent_updates
    def add_artifact_feedback(self, artifact_id: str, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ValidationError("feedback text is required")
        state = self.store.load()
        artifact = self._find(state["artifacts"], artifact_id, "artifact")
        root_stage, rationale = classify_feedback(text)
        source_run = self._find(state["runs"], artifact["run_id"], "run")
        source_required_stages = source_run.get("required_stages", STAGES)
        rerun_stages = [stage for stage in affected_stages(root_stage) if stage in source_required_stages]
        if not rerun_stages:
            target_stage = artifact.get("target_stage", source_required_stages[-1])
            rerun_stages = [target_stage]
        feedback = {
            "id": new_id("feedback"), "artifact_id": artifact_id, "text": text.strip(), "created_at": now(),
            "root_stage": root_stage, "root_stage_label": STAGE_LABELS[root_stage],
            "rationale": rationale, "rerun_stages": rerun_stages,
        }
        state["feedback"].append(feedback)
        latest_rule = state["rules"][-1]
        baseline = latest_rule.get("experiment_precision") or latest_rule.get("baseline_precision")
        experiment = {
            "id": new_id("project-rule-exp"), "name": f"成品反馈归因实验：{STAGE_LABELS[root_stage]}",
            "scope": "当前生产项目", "status": "隔离实验版（待真实保留集回测）",
            "baseline_precision": round(float(baseline), 3) if baseline is not None else None,
            "experiment_precision": None,
            "backtest_status": "等待冻结的后验年份或保留集数据",
            "change_summary": f"根据反馈生成{STAGE_LABELS[root_stage]}规则候选；真实 A/B 结果不得用演示数据代替",
            "evidence_feedback_id": feedback["id"], "created_at": now(),
        }
        state["rules"].append(experiment)
        rerun = {
            "id": new_id("run"), "mode": "targeted_rerun", "status": "running", "created_at": now(),
            "current_stage": rerun_stages[0],
            "stage_states": {stage: ("pending" if stage in rerun_stages else "not_affected") for stage in STAGES},
            "requested_deliverables": [artifact.get("deliverable_id", "ppt")],
            "required_stages": source_required_stages,
            "skipped_stages": [stage for stage in STAGES if stage not in source_required_stages],
            "rule_version": experiment["id"], "selected_question_ids": artifact["question_ids"],
            "source_snapshot_ids": source_run.get("source_snapshot_ids", []), "feedback_id": feedback["id"],
        }
        state["runs"].append(rerun)
        for stage in rerun_stages:
            rerun["current_stage"] = stage
            try:
                self.jobs.execute(state, rerun, stage)
            except StageExecutionError as error:
                rerun["stage_states"][stage] = "failed"
                rerun["status"] = "failed"
                rerun["error"] = str(error)
                artifact["status"] = "局部重跑失败"
                self._event(state, "feedback.rerun_failed", f"{STAGE_LABELS[stage]}重跑失败：{error}", rerun["id"])
                self.store.save(state)
                return {"feedback": feedback, "rule": experiment, "artifact": artifact, "run": rerun}
            rerun["stage_states"][stage] = "completed"
        rerun["status"] = "completed"
        artifact["version"] += 1
        artifact["updated_at"] = now()
        artifact["confirmation"] = None
        artifact["status"] = "执行契约重跑预览（非正式生产成品）"
        artifact["revision_notes"].append({
            "version": artifact["version"], "feedback_id": feedback["id"], "rerun_stages": rerun_stages,
            "summary": f"已从{STAGE_LABELS[root_stage]}开始执行重跑契约；实际执行模式见任务记录",
        })
        self._event(state, "feedback.rerun_completed", artifact["revision_notes"][-1]["summary"], rerun["id"])
        self.store.save(state)
        return {"feedback": feedback, "rule": experiment, "artifact": artifact, "run": rerun}

    @retry_concurrent_updates
    def request_publication(self, rule_id: str) -> dict[str, Any]:
        state = self.store.load()
        rule = self._find(state["rules"], rule_id, "rule")
        publication = {
            "id": new_id("publication"), "rule_id": rule_id, "rule_name": rule["name"],
            "target_scope": "跨项目公共规则/公共 Skill", "status": "待人工批准", "created_at": now(),
            "reason": "公共发布可能影响所有老师，系统禁止自动批准",
        }
        state["publications"].append(publication)
        self._event(state, "rule.publication_requested", "已创建公共规则人工审批请求")
        self.store.save(state)
        return publication

    def _advance(self, state: dict[str, Any], run: dict[str, Any], start_index: int) -> None:
        required_stages = run.get("required_stages", STAGES)
        for index in range(start_index, len(STAGES)):
            stage = STAGES[index]
            if stage not in required_stages:
                run["stage_states"][stage] = "not_requested"
                continue
            run["current_stage"] = stage
            has_exception = self._stage_has_exception(state, stage)
            strategy = state["project"]["intervention_strategies"][stage]
            decision = release_decision(strategy, has_exception)
            if decision != "blocked":
                try:
                    self.jobs.execute(state, run, stage)
                except StageExecutionError as error:
                    run["stage_states"][stage] = "failed"
                    run["status"] = "failed"
                    run["error"] = str(error)
                    self._event(state, "run.failed", f"{STAGE_LABELS[stage]}执行失败：{error}", run["id"])
                    return
            if decision in {"wait", "blocked"}:
                run["stage_states"][stage] = "waiting" if decision == "wait" else "blocked"
                run["status"] = "waiting" if decision == "wait" else "blocked"
                review = {
                    "id": new_id("review"), "run_id": run["id"], "stage": stage,
                    "stage_label": STAGE_LABELS[stage], "status": "待确认",
                    "reason": "节点配置为每次确认" if strategy == "confirm" else (
                        "发现异常，仅异常对象进入复核" if strategy == "exceptions" else "节点禁止自动执行"
                    ),
                    "item_ids": self._exception_item_ids(state, stage) if has_exception else run["selected_question_ids"],
                    "created_at": now(),
                }
                state["reviews"].append(review)
                self._event(state, "run.waiting", f"运行等待：{review['reason']}", run["id"])
                return
            run["stage_states"][stage] = "completed"
            self._event(state, "stage.completed", f"AI 已完成{STAGE_LABELS[stage]}", run["id"])
        run["status"] = "completed"
        run["current_stage"] = required_stages[-1]
        self._create_artifacts(state, run)
        self._event(state, "run.completed", "本次所选交付目标的执行契约已完成；真实生产执行器待接入", run["id"])

    def _stage_has_exception(self, state: dict[str, Any], stage: str) -> bool:
        if stage == "standardization":
            return any(q["content_health"]["status"] != "无明显问题" for q in state["questions"])
        if stage == "diagnosis":
            return any(q["coverage"]["status"] == "无法判断" for q in state["questions"])
        if stage == "selection":
            return any(q["quality"]["confidence"] < 0.6 for q in state["questions"])
        if stage == "mother_question":
            return bool(self._unconfirmed_exception_groups(state))
        return False

    def _exception_item_ids(self, state: dict[str, Any], stage: str) -> list[str]:
        if stage == "standardization":
            return [q["id"] for q in state["questions"] if q["content_health"]["status"] != "无明显问题"]
        if stage == "diagnosis":
            return [q["id"] for q in state["questions"] if q["coverage"]["status"] == "无法判断"]
        if stage == "selection":
            return [q["id"] for q in state["questions"] if q["quality"]["confidence"] < 0.6]
        if stage == "mother_question":
            return self._unconfirmed_exception_groups(state)
        return []

    @staticmethod
    def _unconfirmed_exception_groups(state: dict[str, Any]) -> list[str]:
        selection = state["selection_runs"][-1] if state["selection_runs"] else None
        if not selection:
            return []
        run = next((item for item in reversed(state["mother_question_runs"]) if item["selection_run_id"] == selection["id"]), None)
        if not run:
            return []
        reviewed = {item["group_id"] for item in state["mother_question_reviews"] if item["mother_question_run_id"] == run["id"]}
        return [group["id"] for group in run["groups"] if group["exception"] and group["id"] not in reviewed]

    def _create_artifacts(self, state: dict[str, Any], run: dict[str, Any]) -> list[dict[str, Any]]:
        selected = [q for q in state["questions"] if q["id"] in run["selected_question_ids"]]
        packets = {
            job["stage"]: job for job in state["jobs"]
            if job.get("run_id") == run["id"] and job.get("status") == "completed" and job.get("execution_mode") == "skill_packet"
        }
        artifacts = []
        for deliverable_id in run.get("requested_deliverables", ["ppt"]):
            definition = DELIVERABLES[deliverable_id]
            packet_job = packets.get(definition["target_stage"])
            artifact = {
                "id": new_id("artifact"), "run_id": run["id"], "deliverable_id": deliverable_id,
                "target_stage": definition["target_stage"],
                "title": f"{state['project']['name']}｜{definition['label']}", "kind": definition["label"],
                "status": "执行契约预览（非正式生产成品）", "version": 1,
                "question_ids": [q["id"] for q in selected], "created_at": now(), "updated_at": now(),
                "summary": f"围绕 {len(selected)} 道已入选题目形成“{definition['label']}”结构预览；依赖阶段不额外生成成品，真实 AI/Skill 执行器尚未接入。",
                "outline": list(definition["outline"]),
                "revision_notes": [],
                "skill_packet_job_id": None,
                "outputs": [],
                "confirmation": None,
            }
            if packet_job:
                output = packet_job["output"]
                inputs = output.get("inputs", {})
                primary = next((item for item in output.get("skills", []) if item["role"] == "primary"), None)
                artifact.update({
                    "status": "Skill 执行包已就绪，等待 Agent 执行（非正式成品）",
                    "skill_packet_job_id": packet_job["id"],
                    "summary": (
                        f"已按“{output.get('lesson_type_label')}”路由到 Skill {primary['skill'] if primary else '—'}"
                        f"（{'本机已安装' if primary and primary['resolved']['available'] else '本机未安装'}）；"
                        f"输入为 {inputs.get('confirmed_group_count', 0)} 组已确认母题/题组、{inputs.get('figure_count', 0)} 张原题图。"
                        "执行包不是成品，Agent 执行后需回填产出路径。"
                    ),
                })
            state["artifacts"].append(artifact)
            artifacts.append(artifact)
        return artifacts

    def _summary(self, state: dict[str, Any]) -> dict[str, Any]:
        latest_run = state["runs"][-1] if state["runs"] else None
        latest_selection = state["selection_runs"][-1] if state["selection_runs"] else None
        selection_results = latest_selection["results"] if latest_selection else []
        selection_reviews = {
            item["candidate_id"]: item for item in state["selection_reviews"]
            if latest_selection and item.get("selection_run_id") == latest_selection["id"]
        }
        selected_candidate_count = sum(
            selection_reviews.get(item["id"], {}).get("decision", item["ai_next_route"]) == "进入课程生产"
            for item in selection_results
        )
        return {
            "question_count": len(state["questions"]),
            "candidate_count": selected_candidate_count if latest_selection else sum(bool(q.get("selected_for_candidate")) for q in state["questions"]),
            "p1_count": sum(item["production_priority"]["recommendation"] == "P1" for item in selection_results) if latest_selection else sum(q["production_priority"] == "P1" for q in state["questions"]),
            "exception_count": sum(bool(q.get("exception")) for q in state["questions"]),
            "waiting_review_count": sum(r["status"] == "待确认" for r in state["reviews"]),
            "rule_iteration": len(state["rules"]),
            "source_snapshot_count": len(state["source_snapshots"]),
            "job_count": len(state["jobs"]),
            "failed_job_count": sum(job["status"] == "failed" for job in state["jobs"]),
            "backtest_count": len(state["backtest_results"]),
            "standardized_asset_count": len(state["question_assets"]),
            "standardization_issue_count": sum(item.get("issue_count", 0) for item in state["standardization_runs"]),
            "diagnostic_run_count": len(state["diagnostic_runs"]),
            "gold_sample_count": len(state["gold_sample_sets"]),
            "video_asset_count": len(state["video_assets"]),
            "coverage_run_count": len(state["coverage_runs"]),
            "calibration_review_count": len(state["calibration_reviews"]),
            "selection_run_count": len(state["selection_runs"]),
            "selection_review_count": len(state["selection_reviews"]),
            "question_set_count": len(state["question_sets"]),
            "downstream_task_count": len(state["downstream_tasks"]),
            "mother_question_run_count": len(state["mother_question_runs"]),
            "mother_question_review_count": len(state["mother_question_reviews"]),
            "tag_configuration_count": len(state["tag_configurations"]),
            "state_revision": state["metadata"]["state_revision"],
            "latest_run_status": latest_run["status"] if latest_run else "尚未运行",
            "ai_next_action": self._next_action(latest_run),
        }

    def _calibration_context(self, state: dict[str, Any], request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        diagnostic_run = self._find(state["diagnostic_runs"], str(request.get("diagnostic_run_id") or ""), "diagnostic run")
        gold_sample = self._find(state["gold_sample_sets"], str(request.get("gold_sample_id") or ""), "gold sample")
        coverage_run = self._find(state["coverage_runs"], str(request.get("coverage_run_id") or ""), "coverage run")
        if gold_sample["diagnostic_run_id"] != diagnostic_run["id"] or coverage_run["diagnostic_run_id"] != diagnostic_run["id"] or coverage_run["gold_sample_id"] != gold_sample["id"]:
            raise ValidationError("calibration context ids do not belong to the same run")
        return diagnostic_run, gold_sample, coverage_run

    @staticmethod
    def _append_refreshed_selection(state: dict[str, Any], diagnostic_run: dict[str, Any], gold_sample: dict[str, Any], coverage_run: dict[str, Any]) -> None:
        assets = [item for item in state["question_assets"] if item.get("source_snapshot_id") == diagnostic_run["source_snapshot_id"]]
        result = build_selection_run(diagnostic_run, gold_sample, coverage_run, assets, state["calibration_reviews"])
        if not any(item["id"] == result["id"] for item in state["selection_runs"]):
            state["selection_runs"].append(result)
            WorkbenchService._event(state, "selection.refreshed", "教师校准已归因，候选池按隔离规则刷新；AI 主流程未被阻塞")

    def _next_action(self, run: dict[str, Any] | None) -> str:
        if not run:
            return "按当前项目策略启动资料标准化与诊断"
        if run["status"] == "waiting":
            return f"等待教师处理{STAGE_LABELS[run['current_stage']]}审核；其他可运行分支不受影响"
        if run["status"] == "blocked":
            return f"{STAGE_LABELS[run['current_stage']]}禁止自动执行，等待修改项目授权"
        if run["status"] == "completed":
            return "监测成品反馈与后验数据，主动生成下一轮规则实验"
        return f"继续运行{STAGE_LABELS[run['current_stage']]}"

    @staticmethod
    def _merge_standardization(state: dict[str, Any], result: dict[str, Any]) -> None:
        if not any(item["id"] == result["run"]["id"] for item in state["standardization_runs"]):
            state["standardization_runs"].append(result["run"])
        document_ids = {item["id"] for item in result["documents"]}
        asset_ids = {item["id"] for item in result["question_assets"]}
        state["documents"] = [item for item in state["documents"] if item["id"] not in document_ids] + result["documents"]
        state["question_assets"] = [item for item in state["question_assets"] if item["id"] not in asset_ids] + result["question_assets"]
        groups: dict[str, list[dict[str, Any]]] = {}
        for asset in state["question_assets"]:
            groups.setdefault(asset["fingerprint"], []).append(asset)
        for fingerprint, assets in groups.items():
            duplicate_id = f"duplicate-{fingerprint[:12]}" if len(assets) > 1 else None
            for asset in assets:
                asset["duplicate_group_id"] = duplicate_id
                asset["duplicate_count"] = len(assets)

    @staticmethod
    def _standardization_payload(state: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        document_ids = set(run["document_ids"])
        asset_ids = set(run["question_asset_ids"])
        return {
            "run": run,
            "documents": [item for item in state["documents"] if item["id"] in document_ids],
            "question_assets": [item for item in state["question_assets"] if item["id"] in asset_ids],
        }

    @staticmethod
    def _find(items: list[dict[str, Any]], item_id: str, label: str) -> dict[str, Any]:
        for item in items:
            if item.get("id") == item_id:
                return item
        raise ValidationError(f"unknown {label}: {item_id}")

    @staticmethod
    def _event(state: dict[str, Any], kind: str, message: str, run_id: str | None = None) -> None:
        event = {"id": new_id("event"), "kind": kind, "message": message, "created_at": now()}
        if run_id:
            event["run_id"] = run_id
        state["events"].append(event)
