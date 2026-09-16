from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.l4_workbench.domain import ValidationError
from apps.l4_workbench.skill_routing import STAGE_SKILL_ROUTES, SkillRegistry, normalize_lesson_type


def install_fake_skill(root: Path, name: str, *, references: tuple[str, ...] = (), scripts: tuple[str, ...] = ()) -> None:
    skill_dir = root / name
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: fake {name}\n---\n# {name}\n", encoding="utf-8")
    for item in references:
        (skill_dir / "references" / item).write_text("ref", encoding="utf-8")
    if scripts:
        (skill_dir / "scripts").mkdir(exist_ok=True)
        for item in scripts:
            (skill_dir / "scripts" / item).write_text("print('ok')\n", encoding="utf-8")


class SkillRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        install_fake_skill(self.root, "chemistry-problem-script", references=("解题课初稿成熟度SOP.md", "洋葱口语文风规范.md"))
        install_fake_skill(self.root, "onion-problem-storyboard", references=("problem-visual-logic.md", "storyboard-contract.md"), scripts=("validate_storyboard.py",))
        self.registry = SkillRegistry([self.root])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resolves_installed_skill_with_frontmatter_and_files(self) -> None:
        skill = self.registry.resolve("chemistry-problem-script")
        self.assertTrue(skill["available"])
        self.assertEqual(skill["description"], "fake chemistry-problem-script")
        self.assertIn("references/解题课初稿成熟度SOP.md", skill["references"])

    def test_missing_skill_is_reported_not_faked(self) -> None:
        skill = self.registry.resolve("onion-flavor-script-review")
        self.assertFalse(skill["available"])
        self.assertIn("未找到", skill["missing_reason"])

    def test_transcript_route_marks_available_primary_and_missing_reviewer(self) -> None:
        routes = self.registry.route("transcript", "problem")
        primary = next(item for item in routes if item["role"] == "primary")
        review = next(item for item in routes if item["role"] == "review")
        self.assertEqual(primary["skill"], "chemistry-problem-script")
        self.assertTrue(primary["resolved"]["available"])
        self.assertEqual(len(primary["read_first_paths"]), 2)
        self.assertFalse(review["resolved"]["available"])

    def test_storyboard_route_exposes_validator_path(self) -> None:
        routes = self.registry.route("storyboard", "problem")
        self.assertTrue(routes[0]["validator_path"].endswith("validate_storyboard.py"))

    def test_every_routed_stage_has_a_primary_for_both_lesson_types(self) -> None:
        for stage, by_type in STAGE_SKILL_ROUTES.items():
            for lesson_type in ("problem", "concept"):
                roles = [item["role"] for item in by_type[lesson_type]]
                self.assertIn("primary", roles, f"{stage}/{lesson_type}")

    def test_catalog_lists_unrouted_skills_with_reasons(self) -> None:
        catalog = self.registry.catalog("problem")
        names = {item["skill"] for item in catalog["unrouted"]}
        self.assertIn("onion-chemistry-candidate-assessment", names)
        self.assertIn("course-data-analyst", names)
        self.assertEqual(set(catalog["stages"]), {"mother_question", "lesson_plan", "transcript", "storyboard"})

    def test_lesson_type_is_validated(self) -> None:
        self.assertEqual(normalize_lesson_type(None), "problem")
        with self.assertRaises(ValidationError):
            normalize_lesson_type("lecture")


if __name__ == "__main__":
    unittest.main()
