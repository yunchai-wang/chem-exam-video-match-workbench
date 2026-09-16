"""Stage executors that freeze Skill packets for the production chain.

母题 → 教案 → 逐字稿 → 分镜 each produce a packet: the confirmed inputs (with
every original figure), the Skill to follow, the gates that apply and the
outputs expected back. Nothing here calls a model; an agent executes the
packet and the workbench records that honestly as ``skill_packet``.
When the project has no real confirmed candidates yet, executors fall back to
the explicit dry-run contract so the demo path keeps working.
"""

from __future__ import annotations

from typing import Any, Callable

from .domain import STAGE_LABELS
from .skill_routing import LESSON_TYPE_LABELS, SkillRegistry, normalize_lesson_type


FIGURE_BLOCK_TYPES = {"image", "table", "image_reference"}
INCOMPLETE_IMAGE_STATES = {"missing", "partial", "remote_reference_unmaterialized"}
PACKET_STATUS = "Skill 执行包已冻结，等待 Agent 按 SKILL.md 执行；不是正式生产成品"


class StageGateError(RuntimeError):
    """A design gate blocks this stage until a teacher acts."""


def build_stage_executors(registry: SkillRegistry) -> dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]]:
    return {
        "mother_question": lambda state, run: mother_question_executor(state, run, registry),
        "lesson_plan": lambda state, run: lesson_plan_executor(state, run, registry),
        "transcript": lambda state, run: transcript_executor(state, run, registry),
        "storyboard": lambda state, run: storyboard_executor(state, run, registry),
    }


def dry_run_output(state: dict[str, Any], run: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "execution_mode": "dry_run",
        "production_ready": False,
        "result_type": "contract_preview",
        "message": f"执行契约已验证；{reason}",
        "question_count": len(run.get("selected_question_ids", [])),
        "state_revision": state["metadata"]["state_revision"],
    }


def mother_question_executor(state: dict[str, Any], run: dict[str, Any], registry: SkillRegistry) -> dict[str, Any]:
    context = _mother_context(state)
    if context is None:
        return dry_run_output(state, run, "当前项目还没有真实候选池与母题提案，母题阶段保持契约预演。")
    mother_run, reviews = context["mother_run"], context["reviews"]
    assets = {item["id"]: item for item in state["question_assets"]}
    confirmed, unconfirmed = [], []
    for group in mother_run["groups"]:
        review = reviews.get(group["id"])
        (confirmed if review else unconfirmed).append(_frozen_group(group, review, assets))
    figures = sum(len(member["figures"]) for group in confirmed for member in group["members"])
    incomplete = [
        f"{member['source_name']} 第 {member['question_no']} 题"
        for group in confirmed for member in group["members"] if member["image_incomplete"]
    ]
    lesson_type = _lesson_type(state)
    skills = registry.route("mother_question", lesson_type)
    gates = [
        "母题提案未经教师确认（或批量确认非异常组）不能进入教案；本执行包只包含已确认的组。",
        "Skill 只补完整题面、答案核验与来源映射，不重新分组，也不改变工作台记录的成员动作。",
        "每道原题的全部图表随组保留，系统与 Skill 都不得自动拼接、改绘或裁剪原题图。",
    ]
    if incomplete:
        gates.append(f"原题图不完整的成员需先补图或标注“教案生成受阻”：{'；'.join(incomplete)}。")
    packet = {
        "execution_mode": "skill_packet", "production_ready": False, "result_type": "skill_packet",
        "stage": "mother_question", "status": PACKET_STATUS,
        "lesson_type": lesson_type, "lesson_type_label": LESSON_TYPE_LABELS[lesson_type],
        "skills": skills,
        "inputs": {
            "selection_run_id": mother_run["selection_run_id"],
            "mother_question_run_id": mother_run["id"],
            "mother_question_rule_version": mother_run["rule_version"],
            "confirmed_groups": confirmed,
            "confirmed_group_count": len(confirmed),
            "unconfirmed_group_count": len(unconfirmed),
            "unconfirmed_group_ids": [group["group_id"] for group in unconfirmed],
            "member_count": sum(len(group["members"]) for group in confirmed),
            "figure_count": figures,
            "incomplete_figure_members": incomplete,
        },
        "figure_retention": {"source_count": figures, "retained_count": figures, "policy": "全部原题图表随题保留"},
        "gates": gates,
        "expected_outputs": _expected_outputs(skills),
        "lesson_plan_ready": bool(confirmed),
        "state_revision": state["metadata"]["state_revision"],
    }
    packet["agent_prompt"] = _agent_prompt(run, packet, f"{len(confirmed)} 组已确认母题/题组、{packet['inputs']['member_count']} 道原题、{figures} 张原题图")
    packet["message"] = (
        f"已冻结 {len(confirmed)} 组已确认母题/题组供 Skill 执行；{len(unconfirmed)} 组未确认不进入教案。"
        if confirmed else "母题提案存在，但尚无教师确认的分组；教案阶段将按门禁受阻。"
    )
    return packet


def lesson_plan_executor(state: dict[str, Any], run: dict[str, Any], registry: SkillRegistry) -> dict[str, Any]:
    upstream = _stage_output(state, run, "mother_question")
    if upstream is None or upstream.get("execution_mode") != "skill_packet":
        return dry_run_output(state, run, "上游母题阶段为契约预演，教案阶段保持契约预演。")
    if not upstream.get("lesson_plan_ready"):
        raise StageGateError(
            "母题提案尚无教师确认的分组；按设计门禁，母题未经确认不能生成教案。"
            "请在“题目资产与候选池 → 母题路由”中确认或批量确认非异常组后重跑。"
        )
    lesson_type = upstream["lesson_type"]
    skills = registry.route("lesson_plan", lesson_type)
    project = state["project"]
    groups = upstream["inputs"]["confirmed_groups"]
    packet = {
        "execution_mode": "skill_packet", "production_ready": False, "result_type": "skill_packet",
        "stage": "lesson_plan", "status": PACKET_STATUS,
        "lesson_type": lesson_type, "lesson_type_label": LESSON_TYPE_LABELS[lesson_type],
        "skills": skills,
        "inputs": {
            "upstream_stage": "mother_question",
            "mother_question_run_id": upstream["inputs"]["mother_question_run_id"],
            "confirmed_groups": groups,
            "confirmed_group_count": len(groups),
            "figure_count": upstream["inputs"]["figure_count"],
            "incomplete_figure_members": upstream["inputs"]["incomplete_figure_members"],
            "project": {
                "name": project.get("name"), "subject": project.get("subject"), "grade": project.get("grade"),
                "target_students": project.get("target_students"), "target_region": project.get("target_region"),
                "target_exam_type": project.get("target_exam_type"), "content_scope": project.get("content_scope"),
                "planned_artifact": project.get("planned_artifact"),
            },
        },
        "figure_retention": upstream["figure_retention"],
        "gates": [
            "教案只读取已确认的母题/题组；若执行中发现已确认母题存在条件冲突或科学性问题，重新打开母题审核，不得擅自改题后继续。",
            "所有原题图片、表格、装置图、坐标图和流程图必须随对应题目完整出现；原题图缺失、模糊或错配时标为“教案生成受阻”。",
            "教学目标、关键理解、方法与例题分开写；每道例题只设一个主功能，逐选项/逐小问审视后再决定保留、改写、后置或删除。",
            "教案产出是待教师审核的初稿；逐字稿阶段只读取教师确认后的教案。",
        ],
        "expected_outputs": _expected_outputs(skills),
        "state_revision": state["metadata"]["state_revision"],
    }
    packet["agent_prompt"] = _agent_prompt(run, packet, f"{len(groups)} 组已确认母题/题组、{upstream['inputs']['figure_count']} 张原题图")
    packet["message"] = f"已冻结 {len(groups)} 组已确认母题/题组的教案执行包；教案初稿由 Skill 生成后仍需教师审核。"
    return packet


def transcript_executor(state: dict[str, Any], run: dict[str, Any], registry: SkillRegistry) -> dict[str, Any]:
    upstream = _stage_output(state, run, "lesson_plan")
    if upstream is None or upstream.get("execution_mode") != "skill_packet":
        return dry_run_output(state, run, "上游教案阶段为契约预演，逐字稿阶段保持契约预演。")
    lesson_type = upstream["lesson_type"]
    skills = registry.route("transcript", lesson_type)
    packet = {
        "execution_mode": "skill_packet", "production_ready": False, "result_type": "skill_packet",
        "stage": "transcript", "status": PACKET_STATUS,
        "lesson_type": lesson_type, "lesson_type_label": LESSON_TYPE_LABELS[lesson_type],
        "skills": skills,
        "inputs": {
            "upstream_stage": "lesson_plan",
            "lesson_plan_packet_run_id": run["id"],
            "confirmed_group_count": upstream["inputs"]["confirmed_group_count"],
            "figure_count": upstream["inputs"]["figure_count"],
            "project": upstream["inputs"]["project"],
            "lesson_plan_document": None,
        },
        "figure_retention": upstream["figure_retention"],
        "gates": [
            "逐字稿只读取教师确认的教案，不擅自改变教学目标和题目边界；工作台尚未记录教案确认状态，Agent 执行前必须向教师核对并把已确认教案路径填入 inputs.lesson_plan_document。",
            "按 Skill 的多步流程（初稿→洋葱味道点评→修改→润色→终稿）执行，每步输出可编辑 .docx。",
            "信息时序复核：学生此刻已看到并理解的信息才能被调用；画面切换、高亮与台词同步。",
        ],
        "expected_outputs": _expected_outputs(skills),
        "state_revision": state["metadata"]["state_revision"],
    }
    packet["agent_prompt"] = _agent_prompt(run, packet, f"{upstream['inputs']['confirmed_group_count']} 组已确认母题/题组对应的已确认教案")
    packet["message"] = "已冻结逐字稿执行包；需要教师确认的教案作为唯一主输入。"
    return packet


def storyboard_executor(state: dict[str, Any], run: dict[str, Any], registry: SkillRegistry) -> dict[str, Any]:
    upstream = _stage_output(state, run, "transcript")
    if upstream is None or upstream.get("execution_mode") != "skill_packet":
        return dry_run_output(state, run, "上游逐字稿阶段为契约预演，分镜阶段保持契约预演。")
    lesson_type = upstream["lesson_type"]
    skills = registry.route("storyboard", lesson_type)
    deliverables = run.get("requested_deliverables", [])
    mode = "html" if "html" in deliverables else "ppt"
    validator = next((item["validator_path"] for item in skills if item.get("validator_path")), None)
    packet = {
        "execution_mode": "skill_packet", "production_ready": False, "result_type": "skill_packet",
        "stage": "storyboard", "status": PACKET_STATUS,
        "lesson_type": lesson_type, "lesson_type_label": LESSON_TYPE_LABELS[lesson_type],
        "skills": skills,
        "inputs": {
            "upstream_stage": "transcript",
            "mode": mode,
            "requested_deliverables": deliverables,
            "confirmed_group_count": upstream["inputs"]["confirmed_group_count"],
            "figure_count": upstream["inputs"]["figure_count"],
            "project": upstream["inputs"]["project"],
            "final_transcript_document": None,
        },
        "figure_retention": upstream["figure_retention"],
        "gates": [
            "以明确标为“定稿/终稿”的逐字稿和原题为主输入；未定稿不得以视觉制作绕过教研审核，Agent 执行前须把定稿路径填入 inputs.final_transcript_document。",
            "不重绘会造成科学失真的原题图；高风险科学图优先保留原图或重建为经核对的可编辑矢量图。",
            f"storyboard.json 生成后运行校验脚本：{validator or '（校验脚本未在本机找到）'}。",
        ],
        "expected_outputs": _expected_outputs(skills),
        "validator_path": validator,
        "state_revision": state["metadata"]["state_revision"],
    }
    packet["agent_prompt"] = _agent_prompt(run, packet, f"定稿逐字稿（{mode.upper()} 模式）")
    packet["message"] = f"已冻结分镜执行包（{mode.upper()} 模式）；定稿逐字稿作为唯一主输入。"
    return packet


def _mother_context(state: dict[str, Any]) -> dict[str, Any] | None:
    selection = state.get("selection_runs", [])[-1:] or [None]
    selection = selection[0]
    if not selection:
        return None
    mother_run = next((item for item in reversed(state.get("mother_question_runs", [])) if item["selection_run_id"] == selection["id"]), None)
    if not mother_run:
        return None
    reviews = {item["group_id"]: item for item in state.get("mother_question_reviews", []) if item["mother_question_run_id"] == mother_run["id"]}
    return {"selection": selection, "mother_run": mother_run, "reviews": reviews}


def _frozen_group(group: dict[str, Any], review: dict[str, Any] | None, assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    actions = review["member_actions"] if review else {member["id"]: member["action"] for member in group["members"]}
    anchor = review["anchor_member_id"] if review else group["proposal"].get("anchor_member_id")
    members = []
    for member in group["members"]:
        asset = assets.get(member["asset_id"], {})
        blocks = asset.get("content_blocks", [])
        members.append({
            "member_id": member["id"], "candidate_id": member["candidate_id"], "asset_id": member["asset_id"],
            "source_name": member["source_name"], "question_no": member["question_no"], "title": member["title"],
            "unit_ids": member["unit_ids"], "unit_labels": member["unit_labels"],
            "action": actions.get(member["id"], member["action"]), "is_anchor": member["id"] == anchor,
            "answer_boundary": member["answer_boundary"], "difficulty": member["difficulty"],
            "raw_text": asset.get("raw_text") or "",
            "content_blocks": blocks,
            "figures": [block for block in blocks if block.get("type") in FIGURE_BLOCK_TYPES],
            "image_integrity": asset.get("image_integrity", "unknown"),
            "image_incomplete": asset.get("image_integrity", "unknown") in INCOMPLETE_IMAGE_STATES,
            "tag_profile": asset.get("tag_profile", {}),
        })
    return {
        "group_id": group["id"], "structural_key": group["structural_key"],
        "ai_mode": group["mode"], "mode": review["mode"] if review else group["mode"],
        "title": group["proposal"]["title"], "mode_reason": group["mode_reason"],
        "confirmed": bool(review), "review_status": review["status"] if review else None,
        "visual_policy": review["visual_policy"] if review else group["proposal"]["visual_policy"],
        "anchor_member_id": anchor, "exception_flags": group["exception_flags"],
        "members": members,
    }


def _stage_output(state: dict[str, Any], run: dict[str, Any], stage: str) -> dict[str, Any] | None:
    for job in reversed(state.get("jobs", [])):
        if job.get("run_id") == run["id"] and job.get("stage") == stage and job.get("status") == "completed":
            return job.get("output")
    return None


def _lesson_type(state: dict[str, Any]) -> str:
    return normalize_lesson_type(state["project"].get("lesson_type", "problem"))


def _expected_outputs(skills: list[dict[str, Any]]) -> list[str]:
    outputs: list[str] = []
    for item in skills:
        outputs.extend(f"[{item['skill']}] {value}" for value in item.get("expected_outputs", []))
    return outputs


def _agent_prompt(run: dict[str, Any], packet: dict[str, Any], input_summary: str) -> str:
    primary = next((item for item in packet["skills"] if item["role"] == "primary"), None)
    others = [item for item in packet["skills"] if item["role"] != "primary"]
    stage_label = STAGE_LABELS[packet["stage"]]
    if primary is None:
        return f"该阶段（{stage_label}）没有配置 Skill 路由。"
    resolved = primary["resolved"]
    location = resolved["skill_md"] if resolved["available"] else f"（本机未安装 {primary['skill']}：{resolved.get('missing_reason')}）"
    read_first = "、".join(primary.get("read_first_paths") or primary.get("read_first", [])) or "无"
    extra = "".join(
        f"\n参考/复审 Skill：{item['skill']}（{item['fit']}）→ {item['resolved']['skill_md'] or '本机未安装'}" for item in others
    )
    notes = "".join(f"\n适配说明：{note}" for note in primary.get("adaptation_notes", []))
    gates = "".join(f"\n- {gate}" for gate in packet["gates"])
    return (
        f"请读取 {location} 并按其流程执行「{stage_label}」（{packet['lesson_type_label']}）。\n"
        f"先读：{read_first}。{extra}{notes}\n"
        f"输入：L4 工作台运行 {run['id']} 的「{stage_label}」执行包（{input_summary}），"
        f"原题文字、图片路径与标签见执行包 inputs；图片可通过工作台 /api/assets/<path> 读取。\n"
        f"门禁：{gates}\n"
        f"产出：{'；'.join(packet['expected_outputs']) or '按 SKILL.md'}。\n"
        "完成后把产出文件路径回填给工作台成品记录；不要把本执行包当作正式成品。"
    )
