#!/usr/bin/env python3
"""Scan paper_tag_overrides for common SOP violations (v5 chemistry defaults).

Usage (from repo root):
  .venv/bin/python .cursor/skills/exam-video-match-audit/scripts/sop_scan.py
  .venv/bin/python .../sop_scan.py --work outputs/2026_yt_visual_v5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "scripts" / "visual_v5_2026_yt.py").exists():
            return parent
    return Path.cwd()


ROOT = repo_root()

# Chemistry v5 defaults — replace when migrating subject/batch
SOL_VIDS = {"XZK-27", "JCTB-145", "JCTB-146", "JCTB-138", "JCTB-139", "JCTB-150"}
YIELD_READ_VIDS = {"XZK-21", "ZND-27"}
FILTER_VIDS = {"JCTB-127", "XZK-26"}
PH_CURVE_VIDS = {"JCTB-158", "XZK-14"}
DUAL_LINE_VIDS = {"XZK-21"}
DUAL_LINE_TASK = re.compile(
    r"对比.*(两条|两.*线|双.*曲线|双压强)|图数据.*证据|证明了.*理由|"
    r"结论与证据|图表对比|双线对比|保护作用.*理由|保鲜效果.*证据"
)
DUAL_LINE_FALSE = re.compile(
    r"溶解度|KNO3|晒盐|结晶|产率.*适宜|适宜.*条件|下列说法"
)
GENERIC_PROCESS = {"XZK-22", "XZK-23", "ZND-29", "ZND-24"}
GENERIC_META = {"XZK-36", "XZK-22", "ZND-29"}
GENERIC_CV_TRIO = {"XZK-36", "XZK-6", "XZK-3"}
O2_SPECIFIC = {"ZND-7", "ZND-8", "ZND-17", "ZND-10"}
CO2_ABSORB_SPECIFIC = {"LOCAL-NAOH-CO2-U", "XZK-39", "XZK-14"}
PH_STEP_SPECIFIC = {"XZK-24", "ZND-24"}
PROJECT_TYPES = re.compile(r"项目|跨学科|任务一|任务二|任务三|Ⅰ、|Ⅱ、")


def short(paper: str) -> str:
    return paper.replace("2026年", "").replace("中考化学试卷", "")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def scan(work: Path) -> list[tuple]:
    data = work / "data"
    exams = {(e["paper"], e["qnum"]): e for e in load_json(data / "exam_records_v5.json")}
    issues: list[tuple] = []

    for fp in sorted((data / "paper_tag_overrides").glob("*_v5.json")):
        cfg = load_json(fp)
        paper = cfg["paper"]
        for qstr, qcfg in cfg.get("questions", {}).items():
            qnum = int(qstr)
            ex = exams.get((paper, qnum), {})
            stem = ex.get("stem", "")
            probs = qcfg.get("problem_tags", ex.get("problem_tags", ""))
            vis = qcfg.get("visual_forms", ex.get("visual_forms", ""))
            audit = qcfg.get("audit_note", "")
            allowed = qcfg.get("allowed_video_ids", [])
            comp = qcfg.get("comparison_video_ids", [])
            text = probs + vis + audit + stem[:400]

            if qcfg.get("comparison_show") and not comp:
                issues.append((short(paper), qnum, "P1", "COMPARISON_SHOW_NO_VIDS", allowed, ""))

            if qcfg.get("promotion") != "allow":
                continue

            if "XZK-9" in allowed and re.search(r"滤液|滤渣|金属回收|流程推断", text):
                if "干扰" not in audit and "成分探究" not in audit:
                    issues.append((short(paper), qnum, "P0", "XZK-9_ON_FILTER_FLOW", allowed, audit[:60]))

            if "ZND-24" in allowed and re.search(r"产率|适宜.*条件|适宜反应", text):
                if "沉淀" not in audit and "除杂" not in probs[:30]:
                    issues.append((short(paper), qnum, "P0", "ZND-24_ON_YIELD", allowed, probs[:50]))

            if "流程图" in vis and "曲线图" in vis:
                has_sol = any(v in SOL_VIDS for v in allowed)
                has_yield = any(v in YIELD_READ_VIDS for v in allowed)
                has_ph = any(v in PH_CURVE_VIDS for v in allowed)
                if re.search(r"溶解度|晒盐|KNO3|质量分数|结晶", text) and not has_sol:
                    issues.append((short(paper), qnum, "P1", "DUAL_NO_SOL_VIDEO", allowed, "流程+溶解度曲线"))
                elif re.search(r"产率|适宜.*条件", text) and not has_yield and not has_ph:
                    issues.append((short(paper), qnum, "P1", "DUAL_NO_YIELD_VIDEO", allowed, "流程+产率曲线"))

            if "XZK-46" in allowed and re.search(r"溶解度|晒盐|KNO3|结晶|母液", text):
                if not any(v in SOL_VIDS for v in allowed):
                    issues.append((short(paper), qnum, "P1", "XZK-46_ON_SOL_COMPOSITE", allowed, ""))

            if re.search(r"溶解度|KNO3|结晶|饱和溶液|母液", text) and "流程" in vis + stem:
                if not any(v in SOL_VIDS for v in allowed):
                    issues.append((short(paper), qnum, "P1", "SOL_PROCESS_NO_SOL_VID", allowed, audit[:60]))

            if re.search(r"滤液.*(成分|含有|一定)|滤渣.*(成分|一定)|金属回收|置换.*滤", stem):
                if not any(v in FILTER_VIDS for v in allowed):
                    if "成分探究" not in probs and "XZK-39" not in allowed:
                        issues.append((short(paper), qnum, "P1", "CLASSIC_FILTER_NO_VID", allowed, stem[stem.find("滤"): stem.find("滤") + 50] if "滤" in stem else ""))

            has_curve = "曲线" in vis or "曲线" in stem or "曲线图" in vis
            if (
                has_curve
                and DUAL_LINE_TASK.search(text)
                and not DUAL_LINE_FALSE.search(text)
                and not any(v in DUAL_LINE_VIDS for v in allowed)
            ):
                issues.append(
                    (
                        short(paper),
                        qnum,
                        "P1",
                        "DUAL_LINE_NO_XZK21",
                        allowed,
                        "双线对比写结论+证据，须 XZK-21(下)",
                    )
                )

            if re.search(r"制氧|MnO2|过氧化氢|CaO2|有效氧|鱼池供氧", text):
                if any(v in GENERIC_META for v in allowed) and not any(v in O2_SPECIFIC for v in allowed):
                    issues.append((short(paper), qnum, "P1", "GENERIC_INSTEAD_OF_O2", allowed, "应挂制氧/催化专课"))

            if re.search(r"CO2.*(吸收|去除|碱)|KOH.*CO2|氨水.*CO2", text):
                if "XZK-36" in allowed and not any(v in CO2_ABSORB_SPECIFIC for v in allowed):
                    issues.append((short(paper), qnum, "P1", "GENERIC_INSTEAD_OF_CO2_ABSORB", allowed, "碱吸收CO2应挂U型管/探究专课"))

            if re.search(r"pH.*(表|产率|颜色)|最适宜.*pH|沉铁|沉碱", text) and "流程" in vis + stem:
                if "XZK-22" in allowed and not any(v in PH_STEP_SPECIFIC for v in allowed):
                    issues.append((short(paper), qnum, "P1", "GENERIC_FLOW_NOT_PH_STEP", allowed, "pH表小问应 XZK-24"))

            if PROJECT_TYPES.search(stem[:500]) and qcfg.get("promotion") == "allow":
                if not qcfg.get("task_video_map") and len(allowed) >= 2 and not qcfg.get("compare_table_video_ids"):
                    issues.append((short(paper), qnum, "P2", "PROJECT_NO_TASK_MAP", allowed, "项目式宜 task_video_map 拆小问"))

            if GENERIC_CV_TRIO.issubset(set(allowed)) and not qcfg.get("task_video_map"):
                issues.append(
                    (
                        short(paper),
                        qnum,
                        "P1",
                        "CV_GENERIC_TRIO_NO_MAP",
                        allowed,
                        "宜按设问拆课 XZK-38/XZK-18，勿 36+6+3 三连",
                    )
                )

            if "ZND-17" in allowed and re.search(r"乙醇|酒曲|健康饮水|水为主题", text):
                if not re.search(r"制氧|CaO2|鱼池|H2O2.*制氧", text) and not qcfg.get("compare_table_video_ids"):
                    issues.append(
                        (
                            short(paper),
                            qnum,
                            "P1",
                            "O2_PROJECT_DECOR",
                            allowed,
                            "非制氧情境勿挂 ZND-17 凑项目链",
                        )
                    )

    seen: set[tuple] = set()
    rows: list[tuple] = []
    for r in sorted(issues):
        key = r[:4]
        if key in seen:
            continue
        seen.add(key)
        rows.append(r)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="SOP scan for paper_tag_overrides")
    parser.add_argument("--work", default=str(ROOT / "outputs" / "2026_yt_visual_v5"), help="v5 work directory")
    args = parser.parse_args()
    work = Path(args.work)
    if not (work / "data" / "exam_records_v5.json").exists():
        print(f"Missing data under {work}; run --prepare first.", file=sys.stderr)
        return 1

    rows = scan(work)
    p0 = sum(1 for r in rows if r[2] == "P0")
    p1 = sum(1 for r in rows if r[2] == "P1")
    print(f"Scanned allow-questions under {work}\n")
    print(f"{'卷':<14} {'题':>3} {'级':>2} {'规则':<28} 白名单")
    print("-" * 90)
    for paper, qnum, pri, rule, allowed, note in rows:
        print(f"{paper:<14} Q{qnum:2} {pri:>2} {rule:<28} {','.join(allowed)}  {note}")
    print(f"\nTotal: {len(rows)} (P0={p0}, P1={p1})")
    return 0 if p0 == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
