"""Deterministic AI unit-tag fill for mother-question answer boundaries.

This does not call a model. It proposes per-subquestion question/solution tags
and core-knowledge scopes from unit text cues, the live vocabulary, and the
unmatched-label queue. Whole-question tags are never copied onto every unit.
Every write is marked ``AI 补标·待校准``.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from typing import Any

from .domain import ValidationError
from .mother_question import ELIGIBLE_ROUTES
from .tagging import QUESTION_TYPES, TAGGING_CONTRACT_VERSION, normalize_tag_profile


FILL_VERSION = "ai-unit-tag-fill-v0.1"
SOURCE_LABEL = "AI 补标·待校准"
ACTIVE_STATUSES = {"现行", "已修改", "新增"}
UNIT_MARK = re.compile(r"[（(]([1-9]|[一二三四五六七八九十]+)[）)]")
DIGIT_LABELS = {
    "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
}

# Cue → preferred library terminal labels (first resolvable wins).
QUESTION_CUES: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"化学方程式|写.*方程式|方程式为"), ("写化学反应方程式", "判断化学方程式的正误")),
    (re.compile(r"设计.*方案|请结合.*设计|设计方案|设计实验"), ("补全实验方案", "评价实验设计", "求实验设计的作用")),
    (re.compile(r"为什么|原因|理由|解释"), ("解释实验操作/实验条件/选用某试剂的原因或作用", "解释实验现象", "解释实验结果")),
    (re.compile(r"对比.*证明|可证明|对照"), ("判断实验方案与实验目的是否匹配", "补全实验方案")),
    (re.compile(r"合理|不合理"), ("判断实验方案与实验目的是否匹配", "判断实验操作的正误")),
    (re.compile(r"溶质质量分数|质量分数"), ("比较溶质质量分数", "比较溶质的质量")),
    (re.compile(r"填[“\"]\s*[＞＜＝]|[＞＜＝]\s*\d|时间t\s*[＞＜＝]"), ("比较物质的性质", "比较金属的活动性顺序")),
    (re.compile(r"计算|多少克|求.*质量"), ("比较溶质的质量", "比较析出固体的质量")),
    (re.compile(r"写出.*化学式|化学式为|化合价"), ("求符合要求的化学式", "判断有关化学式的含义", "比较元素的化合价")),
    (re.compile(r"推断|物质是|可能是"), ("判断实验方案与实验目的是否匹配",)),
    (re.compile(r"读图|如图所示|根据图|图像信息"), ("解释实验结果", "解释实验现象")),
]
SOLUTION_CUES: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"控制变量|对比.*证明|对照"), ("根据控制变量法设计实验", "根据实验目的判断需要控制的量", "根据对比/对照/控制变量法得实验设计的目的")),
    (re.compile(r"化学方程式.*计算|利用.*方程式"), ("利用化学方程式计算反应物/生成物的质量",)),
    (re.compile(r"图像|曲线|读图|如图"), ("根据统计图得相关信息", "根据图表得对应反应物的质量")),
]
TYPE_CUES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"探究|实验分析|交流讨论|实验拓展"), "科学探究题"),
    (re.compile(r"工艺流程|流程图|生产流程"), "工艺流程题"),
    (re.compile(r"计算|质量分数|溶解度表"), "计算题"),
    (re.compile(r"推断题|推断下列|物质推断"), "推断题"),
    (re.compile(r"阅读|科普|材料"), "科普阅读题"),
    (re.compile(r"实验装置|基本实验|实验室制"), "基本实验题"),
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def build_ai_unit_tag_fill_run(
    selection_run: dict[str, Any],
    selection_reviews: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    *,
    label_snapshot: dict[str, Any] | None = None,
    unmatched_queue: list[dict[str, Any]] | None = None,
    diagnostic_run: dict[str, Any] | None = None,
    scope: str = "eligible",
) -> dict[str, Any]:
    """Propose unit/core tags for candidates; does not mutate inputs."""
    if scope not in {"eligible", "all"}:
        raise ValidationError("scope must be eligible or all")
    reviews = {
        item["candidate_id"]: item for item in selection_reviews
        if item.get("selection_run_id") == selection_run["id"]
    }
    assets_by_id = {item["id"]: item for item in assets}
    diagnosis_by_asset = {
        item["asset_id"]: item for item in (diagnostic_run or {}).get("results", [])
    }
    resolver = LabelResolver(label_snapshot, unmatched_queue or [])

    proposals: list[dict[str, Any]] = []
    for candidate in selection_run["results"]:
        review = reviews.get(candidate["id"], {})
        route = review.get("decision", candidate.get("ai_next_route"))
        if scope == "eligible" and route not in ELIGIBLE_ROUTES:
            continue
        asset = assets_by_id.get(candidate["asset_id"], {})
        structural_keys = candidate.get("structural_keys")
        if structural_keys is None:
            structural_keys = diagnosis_by_asset.get(candidate["asset_id"], {}).get("structural_keys", [])
        proposals.append(_propose_candidate(candidate, asset, list(structural_keys or []), resolver))

    filled_units = sum(
        1 for item in proposals for unit in item["units"]
        if unit["question"] or unit["solution"]
    )
    summary = {
        "scope": scope,
        "candidate_count": len(proposals),
        "candidates_with_unit_fills": sum(1 for item in proposals if any(unit["question"] for unit in item["units"])),
        "unit_fill_count": filled_units,
        "core_knowledge_fill_count": sum(1 for item in proposals if item["whole"]["core_knowledge"]),
        "question_type_fill_count": sum(1 for item in proposals if item["whole"]["question_type_filled"]),
        "unresolved_unit_count": sum(1 for item in proposals for unit in item["units"] if unit["status"] == "待逐小问识别"),
        "library_snapshot_id": (label_snapshot or {}).get("id"),
    }
    checksum = hashlib.sha256(json.dumps({
        "selection": selection_run["id"], "version": FILL_VERSION, "scope": scope,
        "candidates": [item["candidate_id"] for item in proposals],
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "id": f"ai-tag-fill-{checksum[:14]}",
        "selection_run_id": selection_run["id"],
        "rule_version": FILL_VERSION,
        "source": SOURCE_LABEL,
        "status": "proposed",
        "summary": summary,
        "proposals": proposals,
        "evidence_limits": [
            "逐小问标签只按该小问文本线索生成，不会把整题任务标签自动下沉到每一个小问。",
            "核心知识与全部知识分离；干扰项/仅提及知识不会被标成核心。",
            "产出一律标注为 AI 补标·待校准；教师抽检前不得当作金标准。",
            "优先使用现行词表与待映射队列中的显式/建议映射；库外标签不会被悄悄删除。",
        ],
        "created_at": now(),
    }


def apply_ai_unit_tag_fill(
    selection_run: dict[str, Any],
    assets: list[dict[str, Any]],
    fill_run: dict[str, Any],
) -> dict[str, Any]:
    """Write proposals into the selection candidates and matching question assets."""
    proposals = {item["candidate_id"]: item for item in fill_run["proposals"]}
    assets_by_id = {item["id"]: item for item in assets}
    applied = 0
    for candidate in selection_run["results"]:
        proposal = proposals.get(candidate["id"])
        if not proposal:
            continue
        _apply_to_candidate(candidate, proposal)
        asset = assets_by_id.get(candidate["asset_id"])
        if asset is not None:
            _apply_to_asset(asset, proposal)
        applied += 1
    fill_run = dict(fill_run)
    fill_run["status"] = "applied"
    fill_run["applied_at"] = now()
    fill_run["applied_candidate_count"] = applied
    return fill_run


class LabelResolver:
    def __init__(self, snapshot: dict[str, Any] | None, queue: list[dict[str, Any]]) -> None:
        self.snapshot_id = (snapshot or {}).get("id")
        self.active: dict[str, set[str]] = {}
        self.old_to_new: dict[tuple[str, str], str] = {}
        if snapshot and snapshot.get("vocabulary"):
            for dim, meta in snapshot["vocabulary"].items():
                self.active[dim] = {
                    item["label"] for item in meta.get("labels", [])
                    if item.get("status") in ACTIVE_STATUSES
                }
            for item in snapshot.get("old_to_new", []):
                self.old_to_new[(item["dimension"], item["old"])] = item["new"]
        self.mapped = {
            (item["dimension"], item["label"]): item["mapped_to"]
            for item in queue
            if item.get("status") == "已映射" and item.get("mapped_to")
        }
        self.suggested = {
            (item["dimension"], item["label"]): list(item.get("suggested_labels") or [])
            for item in queue
            if item.get("status") in {None, "待映射", "pending"} and item.get("suggested_labels")
        }

    def resolve(self, dimension: str, label: str) -> tuple[str | None, str]:
        label = str(label or "").strip()
        if not label:
            return None, "空"
        if (dimension, label) in self.mapped:
            return self.mapped[(dimension, label)], "教师映射"
        if (dimension, label) in self.old_to_new:
            return self.old_to_new[(dimension, label)], "词表旧→新"
        active = self.active.get(dimension)
        if active is not None and label in active:
            return label, "现行词表"
        suggestions = self.suggested.get((dimension, label)) or []
        if len(suggestions) == 1 and (active is None or suggestions[0] in active):
            return suggestions[0], "队列单建议"
        if active is None:
            return label, "无词表·保留原标签"
        # Prefer a longer active label that contains the project tag (coarse→fine).
        # Never collapse a long project tag into a short library fragment.
        contained = sorted(
            (item for item in active if label in item and len(item) >= max(len(label), 4)),
            key=lambda item: (len(item), item),
        )
        if contained:
            return contained[0], "字符重合·粗到细"
        return None, "未映射"


def _propose_candidate(
    candidate: dict[str, Any],
    asset: dict[str, Any],
    structural_keys: list[str],
    resolver: LabelResolver,
) -> dict[str, Any]:
    profile = normalize_tag_profile(deepcopy(candidate.get("tag_profile") or asset.get("tag_profile") or {}))
    raw_text = str(asset.get("raw_text") or candidate.get("title") or "")
    units = list(candidate.get("units") or [])
    segments = _unit_segments(raw_text, units)

    remapped_questions = _remap_list(resolver, "question", profile.get("question") or [])
    remapped_solutions = _remap_list(resolver, "solution", profile.get("solution") or [])
    remapped_all_knowledge = _remap_list(resolver, "knowledge", (profile.get("knowledge") or {}).get("all") or [])

    unit_proposals = []
    for unit in units:
        text = segments.get(unit["id"], "")
        kind = unit.get("kind", "whole_question")
        question_tags, question_trace = _pick_question_tags(text, kind, remapped_questions, units, resolver)
        solution_tags, solution_trace = _pick_solution_tags(text, remapped_solutions, resolver)
        status = "AI 已补标·待校准" if question_tags else "待逐小问识别"
        unit_proposals.append({
            "unit_id": unit["id"], "label": unit.get("label"), "kind": kind,
            "text_excerpt": text[:160],
            "question": question_tags[:3], "solution": solution_tags[:2],
            "question_trace": question_trace, "solution_trace": solution_trace,
            "status": status, "source": SOURCE_LABEL,
        })

    question_type, type_filled, type_trace = _pick_question_type(profile, raw_text)
    core, core_trace = _pick_core_knowledge(
        structural_keys, remapped_all_knowledge, raw_text, resolver,
        existing_core=(profile.get("knowledge") or {}).get("core") or [],
    )
    return {
        "candidate_id": candidate["id"],
        "asset_id": candidate["asset_id"],
        "structural_keys": structural_keys,
        "whole": {
            "question_type": question_type,
            "question_type_filled": type_filled,
            "question_type_trace": type_trace,
            "question_tags_remapped": remapped_questions,
            "solution_tags_remapped": remapped_solutions,
            "knowledge_all_remapped": remapped_all_knowledge,
            "core_knowledge": core,
            "core_trace": core_trace,
            "source": SOURCE_LABEL,
        },
        "units": unit_proposals,
        "source": SOURCE_LABEL,
    }


def _pick_question_tags(
    text: str,
    kind: str,
    remapped_whole: list[str],
    units: list[dict[str, Any]],
    resolver: LabelResolver,
) -> tuple[list[str], list[dict[str, str]]]:
    trace: list[dict[str, str]] = []
    picked: list[str] = []
    if text:
        for pattern, labels in QUESTION_CUES:
            if not pattern.search(text):
                continue
            resolved, how, chosen = _resolve_first(resolver, "question", labels)
            if not resolved:
                continue
            if resolved not in picked:
                picked.append(resolved)
                trace.append({"label": resolved, "via": f"线索→{how}", "cue": pattern.pattern, "preferred": chosen})
            if len(picked) >= 3:
                break
    # Single whole-question unit may inherit remapped whole tags (capped), never multi-unit copy.
    if not picked and kind == "whole_question" and len(units) == 1 and remapped_whole:
        for label in remapped_whole[:3]:
            picked.append(label)
            trace.append({"label": label, "via": "整题唯一单元·继承映射后整题任务", "cue": ""})
    # Multi-unit: only attach a remapped whole tag if its wording also appears in this unit text.
    if text and len(units) > 1 and remapped_whole:
        for label in remapped_whole:
            if label in picked:
                continue
            stem = re.sub(r"[（(].*$", "", label)
            key = stem[:4] if len(stem) >= 2 else stem
            if key and key in text:
                picked.append(label)
                trace.append({"label": label, "via": "整题任务·仅在本小问文本命中时下放", "cue": key})
            if len(picked) >= 3:
                break
    return picked[:3], trace


def _pick_solution_tags(
    text: str,
    remapped_whole: list[str],
    resolver: LabelResolver,
) -> tuple[list[str], list[dict[str, str]]]:
    trace: list[dict[str, str]] = []
    picked: list[str] = []
    if not text:
        return picked, trace
    for pattern, labels in SOLUTION_CUES:
        if not pattern.search(text):
            continue
        resolved, how, chosen = _resolve_first(resolver, "solution", labels)
        if resolved and resolved not in picked:
            picked.append(resolved)
            trace.append({"label": resolved, "via": f"线索→{how}", "cue": pattern.pattern, "preferred": chosen})
        if len(picked) >= 2:
            break
    if not picked and remapped_whole:
        for label in remapped_whole[:1]:
            stem = label[:4]
            if stem and stem in text:
                picked.append(label)
                trace.append({"label": label, "via": "整题解法·本小问命中", "cue": stem})
    return picked[:2], trace


def _resolve_first(resolver: LabelResolver, dimension: str, labels: tuple[str, ...] | list[str]) -> tuple[str | None, str, str]:
    for label in labels:
        resolved, how = resolver.resolve(dimension, label)
        if resolved:
            return resolved, how, label
    return None, "未映射", labels[0] if labels else ""


def _pick_question_type(profile: dict[str, Any], raw_text: str) -> tuple[str | None, bool, str]:
    current = profile.get("question_type")
    if current in QUESTION_TYPES:
        return current, False, "已有有效题型"
    for pattern, label in TYPE_CUES:
        if pattern.search(raw_text):
            return label, True, f"线索 {pattern.pattern}"
    candidates = profile.get("question_type_candidates") or []
    for item in candidates:
        if item in QUESTION_TYPES:
            return item, True, "从候选题型回填"
    return None, False, "未能识别"


def _pick_core_knowledge(
    structural_keys: list[str],
    remapped_all: list[str],
    raw_text: str,
    resolver: LabelResolver,
    *,
    existing_core: list[str],
) -> tuple[list[str], list[dict[str, str]]]:
    trace: list[dict[str, str]] = []
    core: list[str] = []
    for label in existing_core:
        resolved, how = resolver.resolve("knowledge", label)
        if resolved and resolved not in core:
            core.append(resolved)
            trace.append({"label": resolved, "via": f"已有核心·{how}"})
    for key in structural_keys:
        resolved, how = resolver.resolve("knowledge", key)
        if resolved and resolved not in core:
            core.append(resolved)
            trace.append({"label": resolved, "via": f"底层结构·{how}"})
        elif resolver.active.get("knowledge"):
            overlaps = sorted(
                (item for item in resolver.active["knowledge"] if key and (key in item or item in key) and len(item) >= 4),
                key=lambda item: (-len(item), item),
            )
            if overlaps and overlaps[0] not in core:
                core.append(overlaps[0])
                trace.append({"label": overlaps[0], "via": "底层结构·词表包含"})
        if len(core) >= 3:
            return core[:3], trace
    stem = raw_text[:240]
    for label in remapped_all:
        if label in core:
            continue
        # Require a meaningful token (≥2 chars) that is not a generic fragment.
        token = label if len(label) <= 8 else label[:8]
        if len(token) >= 2 and token in stem:
            core.append(label)
            trace.append({"label": label, "via": "全部知识·题干命中"})
        if len(core) >= 3:
            break
    # If still empty but remapped_all has 1–2 items, promote carefully — never dump a long all-list.
    if not core and 1 <= len(remapped_all) <= 2:
        for label in remapped_all:
            core.append(label)
            trace.append({"label": label, "via": "全部知识过短·暂作核心候选"})
    return core[:3], trace


def _remap_list(resolver: LabelResolver, dimension: str, values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        resolved, _how = resolver.resolve(dimension, value)
        if resolved and resolved not in result:
            result.append(resolved)
    return result


def _unit_segments(raw_text: str, units: list[dict[str, Any]]) -> dict[str, str]:
    if not units:
        return {}
    if len(units) == 1 and units[0].get("kind") == "whole_question":
        return {units[0]["id"]: raw_text}
    marks = list(UNIT_MARK.finditer(raw_text))
    by_number: dict[str, str] = {}
    for index, match in enumerate(marks):
        number = DIGIT_LABELS.get(match.group(1), match.group(1))
        end = marks[index + 1].start() if index + 1 < len(marks) else len(raw_text)
        by_number[number] = raw_text[match.start():end].strip()
    segments: dict[str, str] = {}
    for unit in units:
        label = str(unit.get("label") or "")
        number_match = re.search(r"([1-9]|[一二三四五六七八九十]+)", label)
        number = DIGIT_LABELS.get(number_match.group(1), number_match.group(1)) if number_match else ""
        segments[unit["id"]] = by_number.get(number, "")
    return segments


def _apply_to_candidate(candidate: dict[str, Any], proposal: dict[str, Any]) -> None:
    profile = normalize_tag_profile(deepcopy(candidate.get("tag_profile") or {}))
    whole = proposal["whole"]
    if whole["question_type"]:
        profile["question_type"] = whole["question_type"]
        profile["question_type_candidates"] = [whole["question_type"]]
        profile["question_type_status"] = "有效"
    if whole["question_tags_remapped"]:
        profile["question"] = whole["question_tags_remapped"]
    if whole["solution_tags_remapped"]:
        profile["solution"] = whole["solution_tags_remapped"]
    knowledge = profile.setdefault("knowledge", {})
    if whole["knowledge_all_remapped"]:
        knowledge["all"] = list(dict.fromkeys([*(knowledge.get("all") or []), *whole["knowledge_all_remapped"]]))
    if whole["core_knowledge"]:
        knowledge["core"] = whole["core_knowledge"]
        knowledge["core_status"] = "AI 已补标·待校准"
    profile["fill_source"] = SOURCE_LABEL
    profile["fill_version"] = FILL_VERSION
    if proposal.get("structural_keys") and not candidate.get("structural_keys"):
        candidate["structural_keys"] = proposal["structural_keys"]
    candidate["tag_profile"] = profile

    unit_index = {item["unit_id"]: item for item in proposal["units"]}
    updated_units = []
    for unit in candidate.get("units") or []:
        item = dict(unit)
        fill = unit_index.get(unit["id"])
        if fill:
            item["tag_profile"] = {
                "library_snapshot_id": profile.get("library_snapshot_id"),
                "contract_version": TAGGING_CONTRACT_VERSION,
                "classification_model": "整题唯一题型＋逐小问任务多标签",
                "status": fill["status"],
                "source": SOURCE_LABEL,
                "fill_version": FILL_VERSION,
                "knowledge": {
                    "all": [], "core": [], "prerequisite": [], "distractor": [], "mentioned": [],
                    "core_status": "待识别",
                },
                "condition": [], "question": fill["question"], "solution": fill["solution"],
                "context": [], "thinking_method": [],
                "trace": {"question": fill["question_trace"], "solution": fill["solution_trace"]},
            }
        updated_units.append(item)
    candidate["units"] = updated_units


def _apply_to_asset(asset: dict[str, Any], proposal: dict[str, Any]) -> None:
    profile = normalize_tag_profile(deepcopy(asset.get("tag_profile") or {}))
    whole = proposal["whole"]
    if whole["question_type"]:
        profile["question_type"] = whole["question_type"]
        profile["question_type_candidates"] = [whole["question_type"]]
        profile["question_type_status"] = "有效"
    if whole["question_tags_remapped"]:
        profile["question"] = whole["question_tags_remapped"]
    if whole["solution_tags_remapped"]:
        profile["solution"] = whole["solution_tags_remapped"]
    knowledge = profile.setdefault("knowledge", {})
    if whole["knowledge_all_remapped"]:
        knowledge["all"] = list(dict.fromkeys([*(knowledge.get("all") or []), *whole["knowledge_all_remapped"]]))
    if whole["core_knowledge"]:
        knowledge["core"] = whole["core_knowledge"]
        knowledge["core_status"] = "AI 已补标·待校准"
    profile["fill_source"] = SOURCE_LABEL
    profile["fill_version"] = FILL_VERSION
    asset["tag_profile"] = profile

    unit_map = {
        item["unit_id"]: {
            "question_tags": item["question"],
            "solution_tags": item["solution"],
            "source": SOURCE_LABEL,
        }
        for item in proposal["units"] if item["question"] or item["solution"]
    }
    fields = dict(asset.get("normalized_fields") or {})
    existing = fields.get("unit_tag_profiles")
    if isinstance(existing, str):
        try:
            existing = json.loads(existing)
        except json.JSONDecodeError:
            existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing.update(unit_map)
    fields["unit_tag_profiles"] = existing
    if whole["core_knowledge"]:
        fields["core_knowledge_tags"] = whole["core_knowledge"]
    if whole["question_type"]:
        fields["question_type"] = whole["question_type"]
    asset["normalized_fields"] = fields
