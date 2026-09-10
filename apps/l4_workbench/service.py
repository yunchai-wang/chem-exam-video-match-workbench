"""Application service for project settings, runs, reviews and learning loops."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from .domain import INTERVENTION_STRATEGIES, STAGE_LABELS, STAGES, ValidationError
from .engine import affected_stages, classify_feedback, recommend_priority, release_decision
from .store import JsonStore


PROJECT_FIELDS = {
    "name", "subject", "grade", "target_students", "target_region",
    "target_exam_type", "target_year", "content_scope", "planned_artifact",
    "problem_to_solve", "maturity", "intervention_scope",
}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class WorkbenchService:
    def __init__(self, store: JsonStore) -> None:
        self.store = store
        self.store.initialize()

    def get_state(self) -> dict[str, Any]:
        state = self.store.load()
        state["summary"] = self._summary(state)
        return state

    def update_project(self, patch: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        project = state["project"]
        unknown = set(patch) - PROJECT_FIELDS - {"intervention_strategies", "preauthorizations"}
        if unknown:
            raise ValidationError(f"unknown project fields: {', '.join(sorted(unknown))}")
        for key in PROJECT_FIELDS:
            if key in patch:
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

    def start_run(self) -> dict[str, Any]:
        state = self.store.load()
        for question in state["questions"]:
            priority, reason, intervention = recommend_priority(question, state["project"])
            question["production_priority"] = priority
            question["priority_reason"] = reason
            question["intervention"] = intervention
        run = {
            "id": new_id("run"), "mode": "full", "status": "running", "created_at": now(),
            "current_stage": STAGES[0], "stage_states": {stage: "pending" for stage in STAGES},
            "rule_version": state["project"]["rule_version"],
            "selected_question_ids": [q["id"] for q in state["questions"] if q.get("selected_for_candidate")],
        }
        state["runs"].append(run)
        self._event(state, "run.started", "AI 已开始纵向闭环运行", run["id"])
        self._advance(state, run, 0)
        self.store.save(state)
        return run

    def approve_review(self, review_id: str) -> dict[str, Any]:
        state = self.store.load()
        review = self._find(state["reviews"], review_id, "review")
        if review["status"] != "待确认":
            raise ValidationError("review is not waiting for confirmation")
        review["status"] = "已通过"
        review["resolved_at"] = now()
        run = self._find(state["runs"], review["run_id"], "run")
        stage = review["stage"]
        run["stage_states"][stage] = "completed"
        run["status"] = "running"
        self._event(state, "review.approved", f"已通过{STAGE_LABELS[stage]}节点", run["id"])
        self._advance(state, run, STAGES.index(stage) + 1)
        self.store.save(state)
        return run

    def add_artifact_feedback(self, artifact_id: str, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ValidationError("feedback text is required")
        state = self.store.load()
        artifact = self._find(state["artifacts"], artifact_id, "artifact")
        root_stage, rationale = classify_feedback(text)
        rerun_stages = affected_stages(root_stage)
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
        artifact["version"] += 1
        artifact["updated_at"] = now()
        artifact["status"] = "局部重跑完成"
        artifact["revision_notes"].append({
            "version": artifact["version"], "feedback_id": feedback["id"], "rerun_stages": rerun_stages,
            "summary": f"已从{STAGE_LABELS[root_stage]}开始重跑，不重复运行无关上游",
        })
        rerun = {
            "id": new_id("run"), "mode": "targeted_rerun", "status": "completed", "created_at": now(),
            "current_stage": rerun_stages[-1],
            "stage_states": {stage: ("completed" if stage in rerun_stages else "not_affected") for stage in STAGES},
            "rule_version": experiment["id"], "selected_question_ids": artifact["question_ids"],
            "feedback_id": feedback["id"],
        }
        state["runs"].append(rerun)
        self._event(state, "feedback.rerun_completed", artifact["revision_notes"][-1]["summary"], rerun["id"])
        self.store.save(state)
        return {"feedback": feedback, "rule": experiment, "artifact": artifact, "run": rerun}

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
        for index in range(start_index, len(STAGES)):
            stage = STAGES[index]
            run["current_stage"] = stage
            has_exception = self._stage_has_exception(state, stage)
            strategy = state["project"]["intervention_strategies"][stage]
            decision = release_decision(strategy, has_exception)
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
        self._create_artifact(state, run)
        self._event(state, "run.completed", "纵向闭环已生成成品", run["id"])

    def _stage_has_exception(self, state: dict[str, Any], stage: str) -> bool:
        if stage == "standardization":
            return any(q["content_health"]["status"] != "无明显问题" for q in state["questions"])
        if stage == "diagnosis":
            return any(q["coverage"]["status"] == "无法判断" for q in state["questions"])
        if stage == "selection":
            return any(q["quality"]["confidence"] < 0.6 for q in state["questions"])
        return False

    def _exception_item_ids(self, state: dict[str, Any], stage: str) -> list[str]:
        if stage == "standardization":
            return [q["id"] for q in state["questions"] if q["content_health"]["status"] != "无明显问题"]
        if stage == "diagnosis":
            return [q["id"] for q in state["questions"] if q["coverage"]["status"] == "无法判断"]
        if stage == "selection":
            return [q["id"] for q in state["questions"] if q["quality"]["confidence"] < 0.6]
        return []

    def _create_artifact(self, state: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        selected = [q for q in state["questions"] if q["id"] in run["selected_question_ids"]]
        artifact = {
            "id": new_id("artifact"), "run_id": run["id"],
            "title": f"{state['project']['name']}｜成品草案", "kind": state["project"]["planned_artifact"],
            "status": "AI 成品待使用/反馈", "version": 1,
            "question_ids": [q["id"] for q in selected], "created_at": now(), "updated_at": now(),
            "summary": f"围绕 {len(selected)} 道已入选题目生成，保留原题图表要求与逐页分镜交接信息。",
            "outline": ["学习目标与学生卡点", "经典母题与原题图表", "方法建构与作答边界", "变式迁移与反馈点", "逐页分镜、清保增说明与素材清单"],
            "revision_notes": [],
        }
        state["artifacts"].append(artifact)
        return artifact

    def _summary(self, state: dict[str, Any]) -> dict[str, Any]:
        latest_run = state["runs"][-1] if state["runs"] else None
        return {
            "question_count": len(state["questions"]),
            "candidate_count": sum(bool(q.get("selected_for_candidate")) for q in state["questions"]),
            "p1_count": sum(q["production_priority"] == "P1" for q in state["questions"]),
            "exception_count": sum(bool(q.get("exception")) for q in state["questions"]),
            "waiting_review_count": sum(r["status"] == "待确认" for r in state["reviews"]),
            "rule_iteration": len(state["rules"]),
            "latest_run_status": latest_run["status"] if latest_run else "尚未运行",
            "ai_next_action": self._next_action(latest_run),
        }

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
