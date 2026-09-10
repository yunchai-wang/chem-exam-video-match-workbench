"""Pure decision rules for priority, release and feedback attribution."""

from __future__ import annotations

from typing import Any

from .domain import STAGE_LABELS, STAGES


def recommend_priority(question: dict[str, Any], project: dict[str, Any]) -> tuple[str, str, str]:
    """Return priority, reason and minimum sufficient intervention."""
    health = question.get("content_health", {}).get("status")
    if health in {"疑似问题", "已确认需纠错"}:
        return "暂不生产", "内容健康检查未通过，先进入独立纠错队列", "纠错后再评估"

    coverage = question.get("coverage", {}).get("status")
    if coverage == "充分覆盖":
        return "暂不生产", "已有视频对必要题目单元和得分点充分覆盖", "仅题库保留"

    quality = question.get("quality", {})
    if quality.get("ai_conclusion") == "否" and quality.get("teacher_conclusion") != "是":
        return "暂不生产", "题目质量不足，不能仅因存在需求直接进入生产", "改造为母题后再评估"

    frequency = question.get("frequency", {})
    trend = question.get("trend", {})
    learner = question.get("learner_value", {})
    difficulty = question.get("difficulty", {}).get("level")
    target = project.get("target_students", "中等及以上")
    high_frequency = frequency.get("level") == "高频"
    rising = trend.get("label") in {"升温", "稳定高频"}
    stable_barrier = bool(learner.get("stable_barrier"))
    high_migration = question.get("migration_value") == "高"
    high_score = float(question.get("score", 0)) >= 6

    if difficulty in {"基础", "简单"} and target in {"中等", "中等及以上"}:
        if high_frequency and (stable_barrier or learner.get("key_prerequisite")):
            return "P2", "题目基础，但属于高频易错或关键前置台阶", "短讲/补丁"
        return "P3", "对目标学生学习增益较低，保留题源但不进入当前课程生产", "仅题库保留"

    if coverage == "组合支撑但缺综合迁移":
        if high_frequency and high_score and stable_barrier:
            return "P1", "综合结构高频、分值高且目标学生存在稳定迁移卡点", "完整新课"
        return "P2", "已有局部知识支撑，但缺少同一综合任务中的迁移台阶", "综合真题串讲"

    if coverage in {"未覆盖", "部分覆盖", "无法判断"} and (rising or high_frequency) and stable_barrier and high_migration:
        return "P1", "考试价值、真实学生卡点和迁移价值同时较高，且存在实质覆盖缺口", "完整新课"

    if coverage in {"未覆盖", "部分覆盖"} and (high_frequency or rising or stable_barrier):
        return "P2", "存在可验证的学习价值或考试价值，适合轻量补强", "短讲/补丁"

    return "P3", "有题源价值，但当前任务下缺少足够的优先生产证据", "仅题库保留"


def release_decision(strategy: str, has_exception: bool) -> str:
    if strategy == "blocked":
        return "blocked"
    if strategy == "confirm":
        return "wait"
    if strategy == "exceptions" and has_exception:
        return "wait"
    return "continue"


FEEDBACK_ROUTES = [
    ("standardization", ("原题", "图表", "题干", "识别", "数据漏", "图片")),
    ("selection", ("选题", "好题", "优先级", "不值得", "难度", "高频")),
    ("mother_question", ("母题", "拼题", "合并", "拆分", "底层结构")),
    ("lesson_plan", ("教案", "教学目标", "例题顺序", "教学环节", "讲什么")),
    ("transcript", ("逐字稿", "口语", "讲解", "过渡", "表述")),
    ("storyboard", ("分镜", "PPT", "页面", "素材", "HTML", "交互", "排版")),
]


def classify_feedback(text: str) -> tuple[str, str]:
    for stage, keywords in FEEDBACK_ROUTES:
        matched = [keyword for keyword in keywords if keyword.lower() in text.lower()]
        if matched:
            return stage, f"反馈中出现与{STAGE_LABELS[stage]}相关的信号：{'、'.join(matched)}"
    return "storyboard", "未命中明确上游信号，先归入成品表达层并保留人工改写入口"


def affected_stages(root_stage: str) -> list[str]:
    if root_stage not in STAGES:
        raise ValueError(f"unknown stage: {root_stage}")
    return STAGES[STAGES.index(root_stage) :]
