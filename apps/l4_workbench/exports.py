"""Editable Word working copies for candidate pools, frozen sets and mother proposals.

Every original question travels with all of its content blocks (text, tables,
figures). The download is a working copy; the workbench JSON stays the
structured master record, and missing figures are reported instead of hidden.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .docx_writer import DocxBuilder


WORKING_COPY_NOTE = "本文档是工作副本：可编辑、可批注、可交给协作者；结构化主资产和图片来源以工作台记录为准。"


def build_selection_docx(selection: dict[str, Any], reviews: list[dict[str, Any]], assets: list[dict[str, Any]], root: Path) -> tuple[bytes, dict[str, Any]]:
    review_index = {item["candidate_id"]: item for item in reviews if item.get("selection_run_id") == selection["id"]}
    asset_index = {item["id"]: item for item in assets}
    doc = DocxBuilder()
    summary = selection["summary"]
    doc.heading(f"好题候选池工作副本｜{selection['rule_version']}", 1)
    doc.paragraph(f"候选池 {selection['id']} · 生成于 {selection.get('created_at')} · 导出于 {_now()}", style="Caption")
    doc.paragraph(WORKING_COPY_NOTE, style="Caption")
    doc.table([
        ["评估题数", "高频", "好题候选", "高频且好题", "P1 / P2 / P3 / 暂不生产", "教师已介入"],
        [str(summary["evaluated_count"]), str(summary["high_frequency_count"]), str(summary["good_question_count"]),
         str(summary["high_frequency_and_good_count"]),
         f"{summary['p1_count']} / {summary['p2_count']} / {summary['p3_count']} / {summary['not_produce_count']}",
         str(len(review_index))],
    ])
    doc.bullets(selection.get("evidence_limits", []))
    figure_total = 0
    for candidate in selection["results"]:
        review = review_index.get(candidate["id"])
        decision = review["decision"] if review else candidate["ai_next_route"]
        doc.heading(f"{candidate['source_name']} · 第 {candidate['question_no']} 题", 2)
        doc.table([
            ["频次", "好题", "难度", "视频覆盖", "优先级", "当前去向"],
            [f"{candidate['frequency']['level']}（{candidate['frequency']['numerator']}/{candidate['frequency']['denominator']}）",
             candidate["quality"]["recommendation"], candidate["difficulty"]["level"], candidate["coverage"]["status"],
             f"{candidate['production_priority']['recommendation']}（{candidate['production_priority']['status']}）",
             f"{decision}{'（教师' + ('纠正' if review['status'] == 'corrected' else '确认') + '）' if review else '（AI 建议）'}"],
        ])
        roles = review["role_labels"] if review else candidate.get("ai_role_labels", [])
        scenarios = review["usage_scenarios"] if review else candidate.get("ai_usage_scenarios", [])
        doc.paragraph(f"题目角色：{'、'.join(roles) or '—'}｜可用场景：{'、'.join(scenarios) or '—'}", style="Caption")
        selected_units = set(review["selected_unit_ids"]) if review else {unit["id"] for unit in candidate["units"]}
        doc.paragraph("进入后续的题目单元：" + "、".join(unit["label"] for unit in candidate["units"] if unit["id"] in selected_units), style="Caption")
        figure_total += _write_asset_content(doc, asset_index.get(candidate["asset_id"], {}), root)
        doc.paragraph(f"好题理由：{candidate['quality']['reason']}")
        doc.paragraph(f"高频证据：{candidate['frequency']['reason']}")
        doc.paragraph(f"视频覆盖：{candidate['coverage']['reason']}")
        doc.paragraph(f"优先级理由：{candidate['production_priority']['reason']}")
        if review and review.get("reason"):
            doc.paragraph(f"教师说明：{review['reason']}", color="7B5B1E")
    report = _finish(doc, figure_total)
    return doc.to_bytes(), report


def build_question_set_docx(question_set: dict[str, Any], task: dict[str, Any] | None, assets: list[dict[str, Any]], root: Path) -> tuple[bytes, dict[str, Any]]:
    asset_index = {item["id"]: item for item in assets}
    doc = DocxBuilder()
    doc.heading(f"{question_set['name']}｜教师确认题集 {question_set['version']}", 1)
    meta = f"题集 {question_set['id']} · 冻结于 {question_set['frozen_at']} · 校验 {question_set['immutable_checksum'][:16]}… · 导出于 {_now()}"
    doc.paragraph(meta, style="Caption")
    if task:
        doc.paragraph(f"任务：{task['task_type']}｜目标学生：{task.get('target_students') or '—'}｜内容范围：{task.get('content_scope') or '—'}｜状态：{task['status']}", style="Caption")
    doc.paragraph(WORKING_COPY_NOTE, style="Caption")
    figure_total = 0
    for index, item in enumerate(question_set["items"], start=1):
        doc.heading(f"{index}. {item.get('source_name')} · 第 {item.get('question_no')} 题", 2)
        doc.paragraph(f"题目角色：{'、'.join(item.get('role_labels', [])) or '—'}｜可用场景：{'、'.join(item.get('usage_scenarios', [])) or '—'}｜入选单元：{len(item.get('selected_unit_ids', []))} 个", style="Caption")
        asset = asset_index.get(item["asset_id"]) or {"content_blocks": item.get("content_blocks", []), "image_integrity": item.get("image_integrity")}
        figure_total += _write_asset_content(doc, asset, root)
        profile = item.get("tag_profile") or {}
        knowledge = profile.get("knowledge", {})
        tags = [
            ("整题题型", [profile.get("question_type")] if profile.get("question_type") else []),
            ("核心知识", knowledge.get("core", [])), ("问题", profile.get("question", [])), ("解法", profile.get("solution", [])),
        ]
        rows = [[label, "、".join(values)] for label, values in tags if values]
        if rows:
            doc.table([["标签维度", "标签值"], *rows])
    report = _finish(doc, figure_total)
    return doc.to_bytes(), report


def build_mother_question_docx(run: dict[str, Any], reviews: list[dict[str, Any]], assets: list[dict[str, Any]], root: Path) -> tuple[bytes, dict[str, Any]]:
    review_index = {item["group_id"]: item for item in reviews if item.get("mother_question_run_id") == run["id"]}
    asset_index = {item["id"]: item for item in assets}
    summary = run["summary"]
    doc = DocxBuilder()
    doc.heading("经典母题整合审核稿（工作台提案版）", 1)
    doc.paragraph(f"母题路由 {run['id']} · 规则 {run['rule_version']} · 候选池 {run['selection_run_id']} · 导出于 {_now()}", style="Caption")
    doc.paragraph(WORKING_COPY_NOTE + " 本稿只含工作台的分组提案、成员动作与全部原题；完整母题题面、标准答案与评分要点由 Skill 执行后补入。", style="Caption")

    doc.heading("一、整合结论", 2)
    doc.table([
        ["有效题目单元", "分组数", "整合成母题", "递进题组", "保持独立", "异常组", "原题图表保留"],
        [str(summary["eligible_candidate_count"]), str(summary["group_count"]), str(summary["mother_group_count"]),
         str(summary["progressive_group_count"]), str(summary["independent_count"]), str(summary["exception_group_count"]),
         f"{summary['retained_figure_count']}/{summary['source_figure_count']}"],
    ])
    doc.bullets(run.get("evidence_limits", []))
    doc.paragraph(run.get("lesson_plan_gate", ""), color="7B5B1E")

    figure_total = 0
    for index, group in enumerate(run["groups"], start=1):
        review = review_index.get(group["id"])
        mode = review["mode"] if review else group["mode"]
        actions = review["member_actions"] if review else {member["id"]: member["action"] for member in group["members"]}
        anchor = review["anchor_member_id"] if review else group["proposal"].get("anchor_member_id")
        status = ("教师已" + ("纠正" if review["status"] == "corrected" else "确认")) if review else "AI 提案待确认"
        doc.heading(f"二·{index} {group['proposal']['title']}", 2)
        doc.paragraph(f"底层结构：{group['structural_key'] or '无共同底层结构'}｜模式：{mode}｜状态：{status}｜异常：{'、'.join(group['exception_flags']) or '无'}")
        doc.paragraph(group["mode_reason"])
        if review and review.get("reason"):
            doc.paragraph(f"教师说明：{review['reason']}", color="7B5B1E")
        proposal = group["proposal"]
        if group["mode"] == "整合成母题":
            boundary = proposal["shared_boundary"]
            doc.paragraph(f"共同作答边界：{boundary.get('question_type') or '—'} · {'、'.join(boundary.get('tasks', [])) or '—'}")
        doc.paragraph(f"图表呈现：{review['visual_policy'] if review else proposal['visual_policy']}（{proposal['visual_policy_status']}）", style="Caption")
        doc.table([
            ["原题", "单元", "作答边界", "难度", "动作", "理由"],
            *[[f"{member['source_name']} 第 {member['question_no']} 题" + ("（典型原题）" if member["id"] == anchor else ""),
               "、".join(member["unit_labels"]) or "整题",
               f"{member['answer_boundary'].get('question_type') or '题型待识别'} · {'、'.join(member['answer_boundary'].get('tasks', [])) or '任务待识别'}（{member['answer_boundary']['status']}）",
               member["difficulty"], actions.get(member["id"], member["action"]), member["action_reason"]]
              for member in group["members"]],
        ])
        for member in group["members"]:
            doc.heading(f"原题：{member['source_name']} · 第 {member['question_no']} 题", 3)
            figure_total += _write_asset_content(doc, asset_index.get(member["asset_id"], {}), root)
        doc.heading("完整母题题面 / 标准答案与评分要点（待 Skill 补入）", 3)
        doc.paragraph("此处由 chemistry-concept-lesson-framework《经典母题整合与教师审核》流程补入：题干、资料、图表引用、全部小问与选项；逐项答案、依据、必要步骤、可接受表达与成立边界。", style="Caption")
        doc.heading("教师审核区", 3)
        doc.paragraph("审核结论：☐ 通过　☐ 修改后通过　☐ 不通过")
        doc.paragraph("审核理由：\n\n必须修改项：\n\n可选优化项：\n\n确认版本号 / 日期：")
        doc.page_break()
    report = _finish(doc, figure_total)
    return doc.to_bytes(), report


def _write_asset_content(doc: DocxBuilder, asset: dict[str, Any], root: Path) -> int:
    """Write every content block of one original question in source order."""
    blocks = asset.get("content_blocks") or []
    figures = 0
    if not blocks and asset.get("raw_text"):
        doc.paragraph(asset["raw_text"])
    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            doc.paragraph(str(block.get("text") or ""))
        elif kind == "formula":
            doc.paragraph(str(block.get("text") or block.get("latex") or ""))
        elif kind == "table":
            rows = block.get("rows")
            if isinstance(rows, list) and rows and all(isinstance(row, list) for row in rows):
                doc.table([[str(cell) for cell in row] for row in rows], header=False)
            else:
                doc.paragraph(str(block.get("text") or "【表格内容见原题图】"))
        elif kind == "image" and block.get("path"):
            figures += 1
            doc.image(root / str(block["path"]), caption=f"原题图 · {block.get('source_locator') or block['path']}")
        elif kind in {"image", "image_reference"}:
            figures += 1
            doc.paragraph(f"【远程题图待物化：{block.get('url') or block.get('path') or '未知引用'}】", color="C73E43")
    integrity = asset.get("image_integrity")
    if integrity and integrity not in {"preserved", "no_visual_declared"}:
        doc.paragraph(f"图片完整性：{integrity}（请勿把本题当作图表完整的题目使用）", color="C73E43")
    return figures


def _finish(doc: DocxBuilder, figure_total: int) -> dict[str, Any]:
    doc.heading("附：图表完整性报告", 2)
    if doc.missing_images:
        doc.paragraph(f"以下 {len(doc.missing_images)} 张原题图在导出时无法读取，文中已用红色占位标出：", color="C73E43")
        doc.bullets(doc.missing_images)
    else:
        doc.paragraph(f"{figure_total} 张原题图全部随题嵌入，未做拼接、裁剪或改绘。")
    return {"figure_count": figure_total, "missing_figure_count": len(doc.missing_images), "missing_figures": list(doc.missing_images)}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
