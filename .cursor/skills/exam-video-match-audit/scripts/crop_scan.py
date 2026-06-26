#!/usr/bin/env python3
"""Scan exam PDF crops for cross-page / truncated question images.

Usage (from repo root):
  .venv/bin/python .cursor/skills/exam-video-match-audit/scripts/crop_scan.py
  .venv/bin/python .../crop_scan.py --work outputs/2026_yt_visual_v5
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

FIG_REF = re.compile(r"如图\s*[0-9一二三四五六七八九十]+|图\s*[12]\s|图[12][、，]")
SUBQ_REF = re.compile(r"[（(]\s*[2-9]\s*[）)]|任务[二三四五六七八九十]")
ANSWER_REF = re.compile(r"参考答案|试题解析|答案与解析")


def short(paper: str) -> str:
    return paper.replace("2026年", "").replace("中考化学试卷", "")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def scan(work: Path) -> list[tuple]:
    data = work / "data"
    exams = load_json(data / "exam_records_v5.json")
    issues: list[tuple] = []

    for q in exams:
        stem = q.get("stem", "")
        page_span = str(q.get("pdf_page", "") or "")
        crop_path = q.get("pdf_crop_path", "")
        status = q.get("pdf_crop_status", "")

        if not crop_path or status == "无PDF":
            continue

        single_page = "-" not in page_span and page_span.isdigit()
        multi_hint = bool(FIG_REF.search(stem) or SUBQ_REF.search(stem))

        if single_page and multi_hint and len(stem) > 280:
            issues.append(
                (
                    short(q["paper"]),
                    q["qnum"],
                    "P1",
                    "CROSS_PAGE_MAYBE",
                    page_span,
                    "题干含如图/后续小问/任务续页，裁图页码却为单页",
                )
            )

        if page_span and "-" in page_span:
            start_p, end_p = (int(x) for x in page_span.split("-", 1))
            if end_p - start_p >= 2:
                issues.append(
                    (
                        short(q["paper"]),
                        q["qnum"],
                        "P2",
                        "CROSS_PAGE_WIDE",
                        page_span,
                        "跨≥3 PDF 页，复核是否裁进答案区",
                    )
                )

        path = Path(crop_path)
        if path.exists() and path.suffix.lower() == ".png":
            try:
                from PIL import Image

                text_sample = ""
                im = Image.open(path)
                # 仅对异常超高裁图做提示（可能含答案条）
                if im.height > im.width * 3.2 and "-" in page_span:
                    end_p = int(page_span.split("-")[-1])
                    start_p = int(page_span.split("-")[0])
                    if end_p - start_p >= 2:
                        issues.append(
                            (
                                short(q["paper"]),
                                q["qnum"],
                                "P1",
                                "CROP_TALL_CHECK",
                                page_span,
                                f"裁图 {im.width}x{im.height}，检查末页是否含答案条",
                            )
                        )
            except Exception:
                pass

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
    parser = argparse.ArgumentParser(description="Scan PDF question crops for cross-page issues")
    parser.add_argument("--work", default=str(ROOT / "outputs" / "2026_yt_visual_v5"))
    args = parser.parse_args()
    work = Path(args.work)
    if not (work / "data" / "exam_records_v5.json").exists():
        print(f"Missing data under {work}; run --prepare first.", file=sys.stderr)
        return 1

    rows = scan(work)
    p1 = sum(1 for r in rows if r[2] == "P1")
    p2 = sum(1 for r in rows if r[2] == "P2")
    print(f"Scanned pdf crops under {work}\n")
    print(f"{'卷':<14} {'题':>3} {'级':>2} {'规则':<22} 页码  说明")
    print("-" * 90)
    for paper, qnum, pri, rule, pages, note in rows:
        print(f"{paper:<14} Q{qnum:2} {pri:>2} {rule:<22} {pages:<5} {note}")
    print(f"\nTotal: {len(rows)} (P1={p1}, P2={p2})")
    return 0 if p1 == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
