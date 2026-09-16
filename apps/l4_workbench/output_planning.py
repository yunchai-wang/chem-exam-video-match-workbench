"""Teacher-facing deliverables and their workflow dependencies."""

from __future__ import annotations

from typing import Any

from .domain import STAGES, ValidationError


DELIVERABLES: dict[str, dict[str, Any]] = {
    "analysis_report": {
        "label": "试卷与趋势分析报告",
        "description": "形成题型、趋势、视频覆盖和缺口证据。",
        "target_stage": "diagnosis",
        "outline": ["题型与知识结构分布", "考试趋势与证据强度", "视频覆盖与缺口", "异常与待补证据清单"],
    },
    "candidate_pool": {
        "label": "好题候选池",
        "description": "交付高频、好题和生产优先级彼此独立的候选结果。",
        "target_stage": "selection",
        "outline": ["候选题及入选小问", "高频程度与统计口径", "好题理由", "生产优先级与不生产理由"],
    },
    "selected_question_set": {
        "label": "教师确认题集",
        "description": "交付老师通过后的可编辑题集，保留原题图表。",
        "target_stage": "selection",
        "outline": ["通过题目与入选小问", "原题文字、公式和图表", "答案与解析引用", "来源、标签和筛选理由"],
    },
    "generated_questions": {
        "label": "AI 原创题与变式题",
        "description": "基于已确认结构生成原创题或变式题，需单独科学性校验。",
        "target_stage": "mother_question",
        "outline": ["共同底层结构", "原创题或变式题", "答案与完整解析", "科学性和同构性检查"],
    },
    "mother_question": {
        "label": "母题方案",
        "description": "按共同底层结构整合经典母题，不硬拼不同作答边界。",
        "target_stage": "mother_question",
        "outline": ["母题原题与完整图表", "共同底层结构", "可整合与不可整合边界", "覆盖问法与迁移方向"],
    },
    "lesson_plan": {
        "label": "教案",
        "description": "形成可审核、可编辑的课程教学设计。",
        "target_stage": "lesson_plan",
        "outline": ["学习目标与学生卡点", "核心例题与原题图表", "方法建构与作答边界", "变式迁移与反馈点"],
    },
    "transcript": {
        "label": "逐字稿",
        "description": "在教案依赖完成后，只交付可录制逐字稿。",
        "target_stage": "transcript",
        "outline": ["完整口语化讲解", "题图与画面提示", "推理过渡与追问", "答案边界与录制提示"],
    },
    "storyboard": {
        "label": "分镜脚本与素材清单",
        "description": "逐页说明清掉、保留、新增什么及素材需求。",
        "target_stage": "storyboard",
        "outline": ["逐页教学意图", "清除、保留、新增说明", "画面与口播对应", "图片、短视频与素材清单"],
    },
    "ppt": {
        "label": "PPT 生产包",
        "description": "形成交给兼职或后续执行器的 PPT 生产包。",
        "target_stage": "storyboard",
        "outline": ["逐页分镜", "版式与内容逻辑", "素材包", "兼职交接与 PMO 动作契约"],
    },
    "html": {
        "label": "可编辑交互 HTML",
        "description": "形成以教学有效性为先的 HTML 页面生产契约。",
        "target_stage": "storyboard",
        "outline": ["页面结构与教学节奏", "交互目的和反馈", "手写留白或 AI 深度排版", "可编辑 HTML 交付契约"],
    },
}


def normalize_deliverables(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValidationError("deliverables must be a non-empty list")
    normalized: list[str] = []
    for item in value:
        deliverable = str(item)
        if deliverable not in DELIVERABLES:
            raise ValidationError(f"unknown deliverable: {deliverable}")
        if deliverable not in normalized:
            normalized.append(deliverable)
    return normalized


def required_stages_for(deliverables: Any) -> list[str]:
    normalized = normalize_deliverables(deliverables)
    final_index = max(STAGES.index(DELIVERABLES[item]["target_stage"]) for item in normalized)
    return STAGES[: final_index + 1]


def public_catalog() -> list[dict[str, Any]]:
    return [{"id": key, **value} for key, value in DELIVERABLES.items()]

