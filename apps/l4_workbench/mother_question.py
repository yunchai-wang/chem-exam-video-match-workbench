"""Route confirmed question units into mother-question proposals.

Grouping is driven by explicit shared underlying structure plus an answer
boundary signature. Same knowledge point alone never forms a group, and the
proposal always keeps every original question with all of its figures.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from .domain import ValidationError


RULE_VERSION = "mother-question-v0.1"
GROUP_MODES = ("整合成母题", "递进题组", "保持独立")
MEMBER_ACTIONS = {"保留", "合并", "改写", "舍弃"}
VISUAL_POLICIES = {"并列原图", "典型原图＋并列附图", "经核对的可编辑重建图"}
REVIEW_DECISIONS = {"按 AI 提案确认", *GROUP_MODES}
ELIGIBLE_ROUTES = {"进入课程生产", "进入母题改造"}
FIGURE_BLOCK_TYPES = {"image", "table", "image_reference"}
INCOMPLETE_IMAGE_STATES = {"missing", "partial", "remote_reference_unmaterialized"}
DIFFICULTY_ORDER = {"基础": 0, "中等": 1, "较难": 2, "未知": 3}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def build_mother_question_run(
    selection_run: dict[str, Any],
    selection_reviews: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    diagnostic_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Propose mother-question groups from units whose effective route is production."""
    reviews = {
        item["candidate_id"]: item for item in selection_reviews
        if item.get("selection_run_id") == selection_run["id"]
    }
    asset_index = {item["id"]: item for item in assets}
    diagnosis_index = {
        item["asset_id"]: item for item in (diagnostic_run or {}).get("results", [])
    }

    members: list[dict[str, Any]] = []
    for candidate in selection_run["results"]:
        review = reviews.get(candidate["id"], {})
        route = review.get("decision", candidate["ai_next_route"])
        if route not in ELIGIBLE_ROUTES:
            continue
        selected_units = review.get("selected_unit_ids", [unit["id"] for unit in candidate["units"]])
        if not selected_units:
            continue
        asset = asset_index.get(candidate["asset_id"], {})
        keys = candidate.get("structural_keys")
        if keys is None:
            keys = diagnosis_index.get(candidate["asset_id"], {}).get("structural_keys", [])
        members.append(_member(candidate, asset, route, selected_units, list(keys or [])))

    groups = _build_groups(selection_run["id"], members)
    review_fingerprint = [
        {"candidate_id": key, "decision": value.get("decision"), "units": value.get("selected_unit_ids")}
        for key, value in sorted(reviews.items())
    ]
    checksum = hashlib.sha256(json.dumps({
        "selection": selection_run["id"], "rule": RULE_VERSION, "reviews": review_fingerprint,
        "members": members,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    source_figures = sum(item["figure_retention"]["source_count"] for item in groups)
    retained_figures = sum(item["figure_retention"]["retained_count"] for item in groups)
    summary = {
        "eligible_candidate_count": len(members),
        "group_count": len(groups),
        "mother_group_count": sum(item["mode"] == "整合成母题" for item in groups),
        "progressive_group_count": sum(item["mode"] == "递进题组" for item in groups),
        "independent_count": sum(item["mode"] == "保持独立" for item in groups),
        "exception_group_count": sum(item["exception"] for item in groups),
        "member_action_counts": {
            action: sum(member["action"] == action for group in groups for member in group["members"])
            for action in sorted(MEMBER_ACTIONS)
        },
        "source_figure_count": source_figures,
        "retained_figure_count": retained_figures,
        "figures_fully_retained": source_figures == retained_figures,
    }
    return {
        "id": f"mother-{checksum[:14]}",
        "selection_run_id": selection_run["id"],
        "diagnostic_run_id": selection_run.get("diagnostic_run_id"),
        "source_snapshot_id": selection_run.get("source_snapshot_id"),
        "rule_version": RULE_VERSION,
        "status": "proposed_pending_optional_confirmation",
        "lesson_plan_gate": "按项目介入策略放行；自动模式使用非异常提案，异常对象隔离。",
        "evidence_limits": [
            "只按显式共同底层结构分组；同知识点但考查逻辑或作答边界不同的题不会硬拼成母题。",
            "作答边界由整题题型与小问任务标签推断；缺少任务标签时只提议递进题组，不合并成母题。",
            "系统不会自动拼接或改绘原题图；多图母题默认并列全部原图，典型原图由教师选择。",
        ],
        "summary": summary,
        "groups": groups,
        "created_at": now(),
    }


def build_mother_question_review(run: dict[str, Any], request: dict[str, Any], *, mode: str = "single") -> dict[str, Any]:
    group_id = str(request.get("group_id") or "")
    group = next((item for item in run["groups"] if item["id"] == group_id), None)
    if group is None:
        raise ValidationError("group is not part of this mother-question run")
    decision = str(request.get("decision") or "按 AI 提案确认")
    if decision not in REVIEW_DECISIONS:
        raise ValidationError(f"invalid mother-question decision: {decision}")
    effective_mode = group["mode"] if decision == "按 AI 提案确认" else decision

    member_ids = [member["id"] for member in group["members"]]
    ai_actions = {member["id"]: member["action"] for member in group["members"]}
    overrides = request.get("member_actions") or {}
    if not isinstance(overrides, dict) or not set(overrides) <= set(member_ids):
        raise ValidationError("member actions must reference members of the group")
    unknown_actions = {str(value) for value in overrides.values()} - MEMBER_ACTIONS
    if unknown_actions:
        raise ValidationError(f"invalid member action: {', '.join(sorted(unknown_actions))}")
    actions = {**ai_actions, **{key: str(value) for key, value in overrides.items()}}
    if all(action == "舍弃" for action in actions.values()):
        raise ValidationError("a group cannot discard every original question")
    if effective_mode == "整合成母题" and sum(action != "舍弃" for action in actions.values()) < 2:
        raise ValidationError("a mother question needs at least two retained original questions")

    anchor = request.get("anchor_member_id", group["proposal"].get("anchor_member_id"))
    if anchor is not None:
        anchor = str(anchor)
        if anchor not in member_ids:
            raise ValidationError("anchor must be a member of the group")
        if actions[anchor] == "舍弃":
            raise ValidationError("the anchor question cannot be discarded")
    visual_policy = str(request.get("visual_policy") or group["proposal"]["visual_policy"])
    if visual_policy not in VISUAL_POLICIES:
        raise ValidationError(f"invalid visual policy: {visual_policy}")

    reason = str(request.get("reason") or "").strip()
    grouping_changed = effective_mode != group["mode"] or actions != ai_actions or anchor != group["proposal"].get("anchor_member_id")
    visual_adjusted = visual_policy != group["proposal"]["visual_policy"]
    if grouping_changed and not reason:
        raise ValidationError("changing the AI grouping, member actions or anchor requires a reason")
    if not reason:
        reason = (
            "教师选择了母题图表呈现方式；该偏好只作用于当前项目，不改写分组规则。"
            if visual_adjusted else
            ("教师确认 AI 母题提案及全部原题去向。" if mode == "single" else "批量确认：接受非异常组的 AI 母题提案。")
        )
    identity = json.dumps({"run": run["id"], "group": group_id}, ensure_ascii=False, sort_keys=True)
    return {
        "id": f"mother-review-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:14]}",
        "mother_question_run_id": run["id"], "group_id": group_id,
        "status": "corrected" if grouping_changed else "accepted", "review_mode": mode,
        "ai_mode": group["mode"], "mode": effective_mode,
        "member_actions": actions, "anchor_member_id": anchor, "visual_policy": visual_policy,
        "visual_adjusted": visual_adjusted, "reason": reason,
        "figure_retention_confirmed": True,
        "lesson_plan_gate": "已确认，可进入教案" if effective_mode != "保持独立" else "已确认为独立题，按单题进入教案",
        "rule_proposals": [{
            "family": "mother_question_grouping_rule", "status": "隔离实验候选", "evidence": reason,
        }] if grouping_changed else [],
        "preference_signals": [{
            "family": "mother_question_visual_preference", "scope": "当前生产项目", "evidence": reason,
        }] if visual_adjusted else [],
        "ai_flow_blocked": False, "updated_at": now(),
    }


def _member(candidate: dict[str, Any], asset: dict[str, Any], route: str, selected_units: list[str], keys: list[str]) -> dict[str, Any]:
    units = [unit for unit in candidate.get("units", []) if unit["id"] in set(selected_units)]
    figures = [
        {
            "type": block.get("type"), "path": block.get("path"), "url": block.get("url"),
            "caption": block.get("caption") or block.get("alt"), "asset_id": candidate["asset_id"],
        }
        for block in asset.get("content_blocks", []) if block.get("type") in FIGURE_BLOCK_TYPES
    ]
    image_integrity = asset.get("image_integrity", "unknown")
    return {
        "id": f"member-{candidate['id']}",
        "candidate_id": candidate["id"], "asset_id": candidate["asset_id"],
        "source_name": candidate.get("source_name"), "question_no": candidate.get("question_no"),
        "title": candidate.get("title") or "题目资产",
        "route": route, "unit_ids": [unit["id"] for unit in units],
        "unit_labels": [unit["label"] for unit in units],
        "structural_keys": keys, "primary_structural_key": keys[0] if keys else None,
        "answer_boundary": _answer_boundary(candidate, units),
        "core_knowledge": list((candidate.get("tag_profile") or {}).get("knowledge", {}).get("core", [])),
        "context_tags": list((candidate.get("tag_profile") or {}).get("context", [])),
        "difficulty": candidate.get("difficulty", {}).get("level", "未知"),
        "quality_score": candidate.get("quality", {}).get("score", 0),
        "frequency_numerator": candidate.get("frequency", {}).get("numerator", 0),
        "figures": figures, "image_integrity": image_integrity,
        "image_incomplete": image_integrity in INCOMPLETE_IMAGE_STATES,
        "duplicate_group_id": asset.get("duplicate_group_id"),
        "issue_codes": list(asset.get("issue_codes", [])),
        "action": "保留", "action_reason": "",
    }


def _answer_boundary(candidate: dict[str, Any], units: list[dict[str, Any]]) -> dict[str, Any]:
    profile = candidate.get("tag_profile") or {}
    unit_tasks: list[str] = []
    for unit in units:
        unit_tasks.extend((unit.get("tag_profile") or {}).get("question", []) or [])
    tasks = sorted(dict.fromkeys(unit_tasks or profile.get("question", []) or []))
    question_type = profile.get("question_type")
    unit_kinds = sorted({unit.get("kind", "whole_question") for unit in units})
    if tasks and question_type:
        status = "已识别"
    elif tasks or question_type:
        status = "部分识别"
    else:
        status = "待识别"
    if any("待校准" in str((unit.get("tag_profile") or {}).get("status", "")) for unit in units):
        status = "部分识别"
    return {
        "question_type": question_type, "tasks": tasks, "unit_kinds": unit_kinds,
        "task_source": "逐小问标签" if unit_tasks else ("整题任务标签" if tasks else "无"),
        "status": status,
        "signature": json.dumps({"type": question_type, "tasks": tasks, "kinds": unit_kinds}, ensure_ascii=False, sort_keys=True),
    }


def _build_groups(selection_id: str, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    keyless: list[dict[str, Any]] = []
    for member in members:
        (by_key[member["primary_structural_key"]] if member["primary_structural_key"] else keyless).append(member)

    groups = [_group(selection_id, key, items) for key, items in sorted(by_key.items())]
    for member in keyless:
        groups.append(_group(selection_id, None, [member]))
    groups.sort(key=lambda item: (GROUP_MODES.index(item["mode"]), -len(item["members"]), item["structural_key"] or "～"))
    return groups


def _group(selection_id: str, key: str | None, items: list[dict[str, Any]]) -> dict[str, Any]:
    members = sorted(items, key=lambda item: (DIFFICULTY_ORDER.get(item["difficulty"], 3), str(item["source_name"]), str(item["question_no"])))
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for member in members:
        clusters[member["answer_boundary"]["signature"]].append(member)
    boundary_statuses = {member["answer_boundary"]["status"] for member in members}
    flags: list[str] = []

    if key is None:
        mode, reason = "保持独立", "缺少可验证的共同底层结构；不会仅凭知识点名称把题目归组。"
        flags.append("缺底层结构")
    elif len(members) == 1:
        mode, reason = "保持独立", f"底层结构“{key}”在当前有效候选中只有这一道题。"
    elif len(clusters) == 1 and boundary_statuses == {"已识别"}:
        boundary = members[0]["answer_boundary"]
        mode = "整合成母题"
        reason = (
            f"{len(members)} 道题共享底层结构“{key}”，整题题型均为{boundary['question_type']}，"
            f"设问任务一致（{'、'.join(boundary['tasks'])}），只是情境或表述不同。"
        )
    elif boundary_statuses != {"已识别"}:
        unresolved = sum(member["answer_boundary"]["status"] != "已识别" for member in members)
        mode = "递进题组"
        reason = (
            f"{len(members)} 道题共享底层结构“{key}”，但其中 {unresolved} 道的整题题型或设问任务尚未识别，"
            "无法判断作答边界是否一致；暂按递进题组保留全部原题，不合并成母题，也不武断拆开。"
        )
        flags.append("作答边界待识别")
    elif _clusters_related(list(clusters.values())):
        mode = "递进题组"
        reason = f"{len(members)} 道题共享底层结构“{key}”，但设问任务或整题题型不同（{len(clusters)} 种作答边界），围绕同一结构组成递进题组。"
    else:
        mode = "保持独立"
        reason = f"虽共享底层结构“{key}”，但设问任务、题型与核心知识均不重合，各题保持独立。"

    anchor_id = _assign_actions(members, mode)
    figures = [figure for member in members for figure in member["figures"]]
    incomplete = [member["id"] for member in members if member["image_incomplete"]]
    if incomplete:
        flags.append("原题图不完整")
    if any(member["issue_codes"] for member in members):
        flags.append("题目资产存在异常")

    group_id = hashlib.sha256(json.dumps({
        "selection": selection_id, "key": key, "members": sorted(member["id"] for member in members),
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:14]
    return {
        "id": f"group-{group_id}", "structural_key": key, "mode": mode, "mode_reason": reason,
        "member_count": len(members), "members": members,
        "boundary_clusters": [
            {"signature": signature, "member_ids": [member["id"] for member in cluster],
             "question_type": cluster[0]["answer_boundary"]["question_type"], "tasks": cluster[0]["answer_boundary"]["tasks"]}
            for signature, cluster in clusters.items()
        ],
        "proposal": _proposal(key, mode, members, anchor_id),
        "figure_retention": {
            "source_count": len(figures), "retained_count": len(figures),
            "figures": figures,
            "policy": "全部原题图表随对应原题保留；系统不自动拼接、改绘或裁剪。",
            "incomplete_member_ids": incomplete,
        },
        "exception": bool(flags), "exception_flags": flags,
        "status": "AI 提案待确认",
    }


def _clusters_related(clusters: list[list[dict[str, Any]]]) -> bool:
    task_sets = [set(cluster[0]["answer_boundary"]["tasks"]) for cluster in clusters]
    knowledge_sets = [set(item for member in cluster for item in member["core_knowledge"]) for cluster in clusters]
    for index in range(len(clusters)):
        for other in range(index + 1, len(clusters)):
            if task_sets[index] & task_sets[other] or knowledge_sets[index] & knowledge_sets[other]:
                return True
    return False


def _assign_actions(members: list[dict[str, Any]], mode: str) -> str | None:
    seen_duplicates: dict[str, str] = {}
    for member in members:
        duplicate = member["duplicate_group_id"]
        if duplicate and duplicate in seen_duplicates:
            member["action"] = "舍弃"
            member["action_reason"] = f"与 {seen_duplicates[duplicate]} 为同一题目资产的重复记录；来源仍保留。"
        elif member["route"] == "进入母题改造":
            member["action"] = "改写"
            member["action_reason"] = "原题质量不足但底层结构可用；需改写后再进入母题，不直接使用原题面。"
            if duplicate:
                seen_duplicates.setdefault(duplicate, f"{member['source_name']} 第 {member['question_no']} 题")
        else:
            member["action"] = "保留"
            member["action_reason"] = ""
            if duplicate:
                seen_duplicates.setdefault(duplicate, f"{member['source_name']} 第 {member['question_no']} 题")

    usable = [member for member in members if member["action"] == "保留"]
    if mode != "整合成母题":
        for member in usable:
            member["action_reason"] = (
                "作为递进题组中的独立台阶保留，题面与全部图表不变。" if mode == "递进题组"
                else "保持独立使用，题面与全部图表不变。"
            )
        return None
    if not usable:
        return None
    anchor = max(usable, key=lambda item: (
        not item["image_incomplete"], item["quality_score"], item["frequency_numerator"], -DIFFICULTY_ORDER.get(item["difficulty"], 3),
    ))
    anchor["action_reason"] = "作为母题的典型原题保留完整题面与图表；教师可改选其他原题为典型。"
    for member in usable:
        if member is not anchor:
            member["action"] = "合并"
            member["action_reason"] = "底层结构与作答边界一致，仅情境或表述不同；作为母题的并列情境合并，原题图表全部保留。"
    return anchor["id"]


def _proposal(key: str | None, mode: str, members: list[dict[str, Any]], anchor_id: str | None) -> dict[str, Any]:
    if mode != "整合成母题":
        return {
            "title": f"{key or '独立题'}{'递进题组' if mode == '递进题组' else ''}（{len(members)} 道原题）",
            "anchor_member_id": None, "visual_policy": "并列原图",
            "visual_policy_status": "各题使用自身原图，无需选择典型原图",
            "steps": [
                {"member_id": member["id"], "difficulty": member["difficulty"], "tasks": member["answer_boundary"]["tasks"]}
                for member in members
            ] if mode == "递进题组" else [],
            "next_stage_gate": "确认后按题组或单题进入教案；教案必须随题保留原图。",
        }
    anchor = next((member for member in members if member["id"] == anchor_id), members[0])
    boundary = anchor["answer_boundary"]
    return {
        "title": f"{key}母题（{len(members)} 道原题）",
        "anchor_member_id": anchor_id,
        "shared_structure": key,
        "shared_boundary": {"question_type": boundary["question_type"], "tasks": boundary["tasks"]},
        "context_variants": [
            {"member_id": member["id"], "source_name": member["source_name"], "question_no": member["question_no"],
             "context_tags": member["context_tags"], "action": member["action"]}
            for member in members
        ],
        "visual_policy": "并列原图",
        "visual_policy_status": "待教师选择：并列全部原图、典型原图＋并列附图，或经核对的可编辑重建图；系统不自动拼接。",
        "next_stage_gate": "母题未经确认不能生成教案；教案必须携带全部原题图表。",
    }
