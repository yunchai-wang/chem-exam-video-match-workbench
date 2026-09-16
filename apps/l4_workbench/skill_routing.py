"""Route production stages to locally installed Skills.

The workbench does not call a language model itself. For the stages that need
one (mother question, lesson plan, transcript, storyboard) it freezes a
"skill packet": which SKILL.md to follow, which references to read first,
which inputs are frozen (with every original figure), which gates apply and
what the Skill is expected to hand back. An agent then executes the packet.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


LESSON_TYPES = {"problem", "concept"}
LESSON_TYPE_LABELS = {"problem": "解题课", "concept": "概念课"}
DEFAULT_SKILL_ROOTS = (
    "~/.codex/skills",
    "~/.cursor/skills",
    "~/.claude/skills",
    "~/.claude/skills/skills",
    "~/.agents/skills",
)

# Stage -> lesson type -> ordered skill roles. `fit` records how well the
# Skill matches this project so the packet never overstates it.
STAGE_SKILL_ROUTES: dict[str, dict[str, list[dict[str, Any]]]] = {
    "mother_question": {
        "problem": [
            {
                "role": "primary", "skill": "chemistry-concept-lesson-framework", "fit": "partial",
                "entry": "references/经典母题整合与教师审核.md",
                "read_first": ["references/经典母题整合与教师审核.md"],
                "expected_outputs": ["《经典母题整合审核稿》（整合结论、完整母题题面、标准答案与评分要点、来源与改写映射、学科质量自检、教师审核区）"],
                "adaptation_notes": [
                    "该 Skill 的母题整合路由面向初中化学多题整合，与本项目学段一致；本项目是解题课，只借用其分组、母题标准与教师审核门禁，不生成概念课教案。",
                    "工作台已按共同底层结构与作答边界给出分组提案和成员动作；Skill 负责补完整题面、答案核验与来源映射，不重新分组。",
                ],
            },
            {
                "role": "standard", "skill": "onion-chemistry-course-design-review", "fit": "reference_only",
                "entry": "references/review-lesson-production.md",
                "read_first": ["references/review-lesson-production.md"],
                "expected_outputs": [],
                "adaptation_notes": ["原技能面向高中一轮 20～25 分钟复习课；只引用其“零、经典母题整合与教师确认门禁”的审核稿结构，不套用时长与轮次要求。"],
            },
        ],
        "concept": [
            {
                "role": "primary", "skill": "chemistry-concept-lesson-framework", "fit": "full",
                "entry": "references/经典母题整合与教师审核.md",
                "read_first": ["references/经典母题整合与教师审核.md", "references/认知跃迁与例题功能检查.md"],
                "expected_outputs": ["《经典母题整合审核稿》"],
                "adaptation_notes": [],
            },
        ],
    },
    "lesson_plan": {
        "problem": [
            {
                "role": "primary", "skill": "onion-chemistry-course-design-review", "fit": "partial",
                "entry": "SKILL.md",
                "read_first": ["references/course-standard.md", "references/review-lesson-production.md"],
                "expected_outputs": ["解题课教案初稿（.docx）：教学目标、关键理解、学生卡点、知识—目标—题目矩阵、核心例题与原题图、方法链、验证任务"],
                "adaptation_notes": [
                    "原技能面向高中一轮 20～25 分钟复习课；本项目为初中中考解题微课，时长、轮次与“核心例题/综合训练”容量以项目设置为准，只借用课程设计门禁、逐项审题矩阵与教案结构。",
                    "教案中每道原题必须插入工作台冻结的原题图，不重建为可编辑题面；缺图成员已在执行包中标记。",
                ],
            },
        ],
        "concept": [
            {
                "role": "primary", "skill": "chemistry-concept-lesson-framework", "fit": "full",
                "entry": "SKILL.md",
                "read_first": ["references/认知跃迁与例题功能检查.md", "references/教案参考.md"],
                "expected_outputs": ["概念课讲解框架（.docx，8 大部分）"],
                "adaptation_notes": [],
            },
        ],
    },
    "transcript": {
        "problem": [
            {
                "role": "primary", "skill": "chemistry-problem-script", "fit": "full",
                "entry": "SKILL.md",
                "read_first": ["references/解题课初稿成熟度SOP.md", "references/洋葱口语文风规范.md", "references/解题课审题模型.md"],
                "expected_outputs": ["解题课逐字稿初稿、洋葱味道点评、修改稿、初步润色稿、各取所长终稿（均为 .docx）"],
                "adaptation_notes": [],
            },
            {
                "role": "review", "skill": "onion-flavor-script-review", "fit": "full",
                "entry": "SKILL.md",
                "read_first": ["references/洋葱味道框架.md"],
                "expected_outputs": ["洋葱味道审稿报告；初稿—定稿对比"],
                "adaptation_notes": [],
            },
        ],
        "concept": [
            {
                "role": "primary", "skill": "chemistry-concept-script", "fit": "full",
                "entry": "SKILL.md",
                "read_first": ["references/概念课认知跃迁自检.md", "references/洋葱味道框架.md"],
                "expected_outputs": ["概念课逐字稿初稿、点评、修改稿、润色稿（均为 .docx）"],
                "adaptation_notes": [],
            },
            {
                "role": "review", "skill": "onion-flavor-script-review", "fit": "full",
                "entry": "SKILL.md",
                "read_first": ["references/洋葱味道框架.md"],
                "expected_outputs": ["洋葱味道审稿报告"],
                "adaptation_notes": [],
            },
        ],
    },
    "storyboard": {
        "problem": [
            {
                "role": "primary", "skill": "onion-problem-storyboard", "fit": "full",
                "entry": "SKILL.md",
                "read_first": ["references/problem-visual-logic.md", "references/storyboard-contract.md"],
                "expected_outputs": ["storyboard.md、storyboard.json、asset_manifest.csv；PPT 模式另交 ppt_handoff.md 与 assets/；HTML 模式另交可离线运行页面"],
                "validator": "scripts/validate_storyboard.py",
                "adaptation_notes": [],
            },
        ],
        "concept": [
            {
                "role": "primary", "skill": "onion-concept-storyboard", "fit": "full",
                "entry": "SKILL.md",
                "read_first": ["references/concept-visual-logic.md", "references/storyboard-contract.md"],
                "expected_outputs": ["storyboard.md、storyboard.json、asset_manifest.csv；PPT/HTML 模式附加交付物"],
                "validator": "scripts/validate_storyboard.py",
                "adaptation_notes": [],
            },
        ],
    },
}

# Skills that live next to the routed ones but are deliberately not wired.
UNROUTED_SKILLS = {
    "onion-chemistry-candidate-assessment": "评估的是高中化学求职候选人，不是题目候选；与生产链无关。",
    "course-data-analyst": "依赖“视频数据助手”MCP 读取学生观看/练习数据；应接入选题阶段的学生价值实证，而不是母题→分镜链。",
}

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def normalize_lesson_type(value: Any) -> str:
    text = str(value or "problem")
    if text not in LESSON_TYPES:
        from .domain import ValidationError
        raise ValidationError(f"invalid lesson type: {text}")
    return text


class SkillRegistry:
    """Resolve Skill names against local skill roots without executing anything."""

    def __init__(self, roots: list[str | Path] | None = None) -> None:
        env_roots = [item for item in os.environ.get("L4_SKILL_ROOTS", "").split(os.pathsep) if item]
        candidates = roots if roots is not None else (env_roots or list(DEFAULT_SKILL_ROOTS))
        self.roots = [Path(item).expanduser() for item in candidates]

    def resolve(self, name: str) -> dict[str, Any]:
        for root in self.roots:
            skill_dir = root / name
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                text = skill_md.read_text(encoding="utf-8", errors="replace")
                meta = _frontmatter(text)
                references = sorted(str(item.relative_to(skill_dir)) for item in (skill_dir / "references").glob("*") if item.is_file()) if (skill_dir / "references").is_dir() else []
                scripts = sorted(str(item.relative_to(skill_dir)) for item in (skill_dir / "scripts").glob("*.py")) if (skill_dir / "scripts").is_dir() else []
                return {
                    "name": name, "available": True, "path": str(skill_dir), "skill_md": str(skill_md),
                    "root": str(root), "description": meta.get("description", "")[:400],
                    "references": references, "scripts": scripts,
                }
        return {
            "name": name, "available": False, "path": None, "skill_md": None, "root": None,
            "description": "", "references": [], "scripts": [],
            "missing_reason": f"在 {', '.join(str(root) for root in self.roots)} 下未找到 {name}/SKILL.md",
        }

    def route(self, stage: str, lesson_type: str) -> list[dict[str, Any]]:
        lesson_type = normalize_lesson_type(lesson_type)
        routes = STAGE_SKILL_ROUTES.get(stage, {}).get(lesson_type, [])
        resolved = []
        for route in routes:
            skill = self.resolve(route["skill"])
            entry_path = Path(skill["path"]) / route["entry"] if skill["available"] else None
            validator = route.get("validator")
            validator_path = Path(skill["path"]) / validator if skill["available"] and validator else None
            resolved.append({
                **route,
                "lesson_type": lesson_type,
                "lesson_type_label": LESSON_TYPE_LABELS[lesson_type],
                "resolved": skill,
                "entry_path": str(entry_path) if entry_path and entry_path.is_file() else None,
                "entry_missing": bool(skill["available"] and entry_path and not entry_path.is_file()),
                "validator_path": str(validator_path) if validator_path and validator_path.is_file() else None,
                "read_first_paths": [
                    str(Path(skill["path"]) / item) for item in route.get("read_first", [])
                    if skill["available"] and (Path(skill["path"]) / item).is_file()
                ],
            })
        return resolved

    def catalog(self, lesson_type: str) -> dict[str, Any]:
        """Stage-by-stage view for the settings page; never triggers execution."""
        stages = {
            stage: [
                {
                    "role": item["role"], "skill": item["skill"], "fit": item["fit"],
                    "available": item["resolved"]["available"], "path": item["resolved"]["path"],
                    "description": item["resolved"]["description"], "adaptation_notes": item["adaptation_notes"],
                }
                for item in self.route(stage, lesson_type)
            ]
            for stage in STAGE_SKILL_ROUTES
        }
        return {
            "lesson_type": normalize_lesson_type(lesson_type),
            "roots": [str(root) for root in self.roots],
            "stages": stages,
            "unrouted": [{"skill": name, "reason": reason, "available": self.resolve(name)["available"]} for name, reason in UNROUTED_SKILLS.items()],
        }


def _frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values
