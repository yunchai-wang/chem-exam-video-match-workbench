#!/usr/bin/env python3
"""Publish generated 2026 comparison XML files to Feishu docs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import compare_2026_yt as build


WORK = Path("/Users/mbpro/Desktop/AI/chem-exam-analysis/outputs/2026_yt_compare")
DOCS = WORK / "docs"
FEISHU = WORK / "feishu"
URLS_PATH = FEISHU / "doc_urls.json"


def load_urls() -> dict:
    if URLS_PATH.exists():
        return json.loads(URLS_PATH.read_text(encoding="utf-8"))
    return {}


def save_urls(urls: dict) -> None:
    FEISHU.mkdir(parents=True, exist_ok=True)
    URLS_PATH.write_text(json.dumps(urls, ensure_ascii=False, indent=2), encoding="utf-8")


def create_doc(title: str, xml_path: Path) -> dict:
    content = xml_path.read_text(encoding="utf-8")
    cmd = [
        "lark-cli",
        "docs",
        "+create",
        "--api-version",
        "v2",
        "--as",
        "user",
        "--parent-position",
        "my_library",
        "--content",
        "-",
        "--format",
        "json",
    ]
    proc = subprocess.run(cmd, input=content, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to create {title}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response while creating {title}: {proc.stdout}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Create failed for {title}: {json.dumps(payload, ensure_ascii=False)}")
    return payload["data"]["document"]


def rebuild_summary_with_urls(urls: dict) -> Path:
    papers = json.loads((WORK / "papers_meta.json").read_text(encoding="utf-8"))
    results = json.loads((WORK / "match_results.json").read_text(encoding="utf-8"))
    for paper in papers:
        if paper["title"] in urls:
            paper["url"] = urls[paper["title"]]["url"]

    examples = []
    for paper_title, result in results.items():
        for q in result["questions"]:
            qnum = str(q["number"])
            matches = result["matches"].get(qnum, [])
            if matches and matches[0]["score"] >= 18:
                examples.append({"paper": paper_title, "qnum": q["number"], "topics": q["topics"], "matches": matches})

    summary_xml = build.build_summary_doc(papers, examples)
    path = DOCS / "2026中考化学押题对比汇总.xml"
    path.write_text(summary_xml, encoding="utf-8")
    return path


def main() -> None:
    urls = load_urls()
    papers = json.loads((WORK / "papers_meta.json").read_text(encoding="utf-8"))

    for paper in papers:
        title = paper["title"]
        if title in urls and urls[title].get("url"):
            print(f"skip {title}: {urls[title]['url']}", flush=True)
            continue
        doc = create_doc(title, Path(paper["local_xml"]))
        urls[title] = {"url": doc["url"], "document_id": doc["document_id"]}
        save_urls(urls)
        print(f"created {title}: {doc['url']}", flush=True)

    summary_title = "2026 中考化学押题对比汇总"
    summary_path = rebuild_summary_with_urls(urls)
    if summary_title not in urls:
        doc = create_doc(summary_title, summary_path)
        urls[summary_title] = {"url": doc["url"], "document_id": doc["document_id"]}
        save_urls(urls)
        print(f"created {summary_title}: {doc['url']}", flush=True)
    else:
        print(f"skip {summary_title}: {urls[summary_title]['url']}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
