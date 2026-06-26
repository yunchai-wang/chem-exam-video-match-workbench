#!/usr/bin/env python3
"""Create a v3.1 patch for Yunnan Q12-14 topic-group science reading."""

from __future__ import annotations

import json
import re
import subprocess
import time
from html import escape
from pathlib import Path

import fitz
from PIL import Image

import visual_v3_2026_yt as v3


ROOT = Path("/Users/mbpro/Desktop/AI/chem-exam-analysis")
WORK = ROOT / "outputs" / "2026_yt_visual_v31_yunnan_patch"
DATA = WORK / "data"
DOCS = WORK / "docs"
FEISHU = WORK / "feishu"
PAPER = "2026年云南省中考化学试卷"
GROUP_ID = "2026年云南省中考化学试卷-Q12-14"


def ensure_dirs() -> None:
    for p in [WORK, DATA, DOCS, FEISHU]:
        p.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_json(cmd: list[str], input_text: str | None = None, timeout: int = 240) -> dict:
    proc = subprocess.run(cmd, cwd=ROOT, input=input_text, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(cmd)
            + f"\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    payload = json.loads(proc.stdout)
    if not payload.get("ok"):
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload


def create_doc_from_xml(xml: str) -> dict:
    doc = run_json(
        [
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
        ],
        input_text=xml,
        timeout=300,
    )["data"]["document"]
    doc_id = doc["document_id"]
    return {"doc_id": doc_id, "url": doc.get("url") or f"https://guanghe.feishu.cn/docx/{doc_id}", "raw": doc}


def crop_group() -> Path:
    pdf = ROOT / "试卷" / "2026中考卷" / f"{PAPER}.pdf"
    out_dir = WORK / "group_crops"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    clips = [
        (3, fitz.Rect(42, 70, doc[3].rect.width - 42, doc[3].rect.height - 25)),
        (4, fitz.Rect(42, 70, doc[4].rect.width - 42, 252)),
    ]
    parts = []
    for i, (page_index, rect) in enumerate(clips, 1):
        pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(2.15, 2.15), clip=rect, alpha=False)
        part = out_dir / f"yunnan_q12_14_part{i}.png"
        pix.save(str(part))
        parts.append(Image.open(part).convert("RGB"))
    doc.close()
    width = max(im.width for im in parts)
    height = sum(im.height for im in parts)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for im in parts:
        canvas.paste(im, (0, y))
        y += im.height
    final = out_dir / "yunnan_q12_14_group.png"
    canvas.save(final)
    return final


def ensure_staging_doc() -> dict:
    staging_path = v3.FEISHU / "staging_doc.json"
    if staging_path.exists():
        return load_json(staging_path)
    return v3.ensure_staging_doc()


def upload_image(key: str, file_path: Path, width: str = "720") -> str:
    token_path = FEISHU / "image_tokens_v31.json"
    tokens = json.loads(token_path.read_text(encoding="utf-8")) if token_path.exists() else {}
    if key in tokens:
        return tokens[key]
    staging = ensure_staging_doc()
    payload = run_json(
        [
            "lark-cli",
            "docs",
            "+media-insert",
            "--doc",
            staging["doc_id"],
            "--file",
            str(file_path.relative_to(ROOT)),
            "--width",
            width,
            "--align",
            "center",
            "--format",
            "json",
        ],
        timeout=240,
    )
    tokens[key] = payload["data"]["file_token"]
    save_json(token_path, tokens)
    time.sleep(0.2)
    return tokens[key]


def img(token: str, width: int = 540, name: str = "") -> str:
    name_attr = f' name="{escape(name)}"' if name else ""
    return f'<img src="{escape(token)}" width="{width}"{name_attr}/>' if token else ""


def table(rows: list[list[str]], headers: list[str], widths: list[int]) -> str:
    colgroup = "<colgroup>" + "".join(f'<col width="{w}"/>' for w in widths) + "</colgroup>"
    thead = "<thead><tr>" + "".join(f'<th background-color="light-gray">{escape(h)}</th>' for h in headers) + "</tr></thead>"
    tbody = "<tbody>" + "".join("<tr>" + "".join(f'<td vertical-align="top">{c}</td>' for c in row) + "</tr>" for row in rows) + "</tbody>"
    return f"<table>{colgroup}{thead}{tbody}</table>"


def patch_data(group_token: str, group_path: Path) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    exams = load_json(v3.DATA / "exam_records_v3.json")
    videos = load_json(v3.DATA / "video_records_v3.json")
    evidence = load_json(v3.DATA / "screenshot_evidence_v3.json")
    matches = load_json(v3.DATA / "matches_v3.json")

    group_anchors = "科普阅读材料、流程图、坐标曲线、表格数据、图文阅读题组"
    for q in exams:
        if q["paper"] == PAPER and q["qnum"] in {12, 13, 14}:
            q["question_type_v3"] = "科普阅读题"
            q["visual_anchors"] = group_anchors
            q["signatures"] = v3.readable(list(v3.split_tags(q.get("signatures", "")) | {"科普阅读"}))
            q["pdf_crop_path"] = str(group_path)
            q["pdf_crop_token"] = group_token
            q["pdf_crop_status"] = "v3.1题组公共材料裁图"
            q["topic_group_id"] = GROUP_ID
            q["qa_status_v3"] = "v3.1人工校准-科普阅读题组"

    for item in videos:
        if item["video_id"] in {"XZK-21", "ZND-27"}:
            item["question_type_v3"] = "科普阅读题"
            item["visual_anchors"] = group_anchors
            item["signatures"] = v3.readable(list(v3.split_tags(item.get("signatures", "")) | {"科普阅读"}))

    for item in evidence:
        if item["video_id"] in {"XZK-21", "ZND-27"}:
            item["visual_anchors"] = group_anchors
            item["adapted_type"] = "科普阅读题"
            item["quality_status"] = "v3.1科普阅读题组校准证据"
            if item["video_id"] == "XZK-21":
                item["similarity_note"] = "同为科普阅读题组：长材料配流程/曲线/柱状图，要求从图文信息中提取变量关系。"
            else:
                item["similarity_note"] = "同为科普短文+多图表数据解读题，外观为阅读材料配柱状图/折线图。"

    existing = [m for m in matches if not (m["paper"] == PAPER and m["qnum"] in {12, 13, 14})]
    ev_by_video = {}
    for item in evidence:
        ev_by_video.setdefault(item["video_id"], []).append(item)
    video_by_id = {v["video_id"]: v for v in videos}
    patched = []
    for qnum in [12, 13, 14]:
        q = next(q for q in exams if q["paper"] == PAPER and q["qnum"] == qnum)
        for vid, limit in [("XZK-21", 3), ("ZND-27", 3)]:
            v = video_by_id[vid]
            evs = ev_by_video[vid][:limit]
            patched.append(
                {
                    "match_id": f"{q['question_id']}__{vid}__V31",
                    "question_id": q["question_id"],
                    "paper": q["paper"],
                    "qnum": q["qnum"],
                    "question_type_v3": "科普阅读题",
                    "video_id": vid,
                    "video_name": v["video_name"],
                    "video_type_v3": "科普阅读题",
                    "score": 160 if vid == "XZK-21" else 145,
                    "route_result": "通过",
                    "visual_anchors": group_anchors,
                    "hit_reason": "题组同为科普阅读/图文信息题，材料+流程/图表+数据判断的外观和任务形式一致。",
                    "reject_reason": "",
                    "screenshot_ids": "；".join(e["screenshot_id"] for e in evs),
                    "screenshot_notes": "；".join(e["similarity_note"] for e in evs),
                    "final_show": True,
                }
            )
    matches = existing + patched
    save_json(DATA / "exam_records_v31.json", exams)
    save_json(DATA / "video_records_v31.json", videos)
    save_json(DATA / "screenshot_evidence_v31.json", evidence)
    save_json(DATA / "matches_v31.json", matches)
    return exams, videos, evidence, matches


def yunnan_doc(exams: list[dict], videos: list[dict], evidence: list[dict], matches: list[dict]) -> str:
    video_by_id = {v["video_id"]: v for v in videos}
    ev_by_id = {e["screenshot_id"]: e for e in evidence}
    qs = [q for q in exams if q["paper"] == PAPER]
    qs.sort(key=lambda q: q["qnum"])
    final = {}
    for m in matches:
        if m["paper"] == PAPER and m.get("final_show"):
            final.setdefault(m["question_id"], []).append(m)

    analysis_rows = []
    for q in qs:
        ms = final.get(q["question_id"], [])
        analysis_rows.append(
            [
                escape(q["question_type_v3"]),
                escape("12-14题组" if q["qnum"] in {12, 13, 14} else str(q["qnum"])),
                escape(str(q.get("score") or "")),
                escape(q.get("visual_anchors", "")),
                escape("押中" if ms else "未进入宣传版"),
                escape("；".join(m["video_name"] for m in ms[:2]) if ms else "未达到视觉宣传展示标准"),
            ]
        )

    compare_rows = []
    group_q = next(q for q in qs if q["qnum"] == 12)
    left = f"<p><b>第12-14题组</b></p>{img(group_q['pdf_crop_token'], 560, 'yunnan_q12_14_group.png')}"
    right_parts = []
    for m in final[group_q["question_id"]]:
        right_parts.append(f"<p><b>{escape(m['video_name'])}</b></p>")
        right_parts.append(f"<p>{escape(m['hit_reason'])}</p>")
        ids = [x for x in m["screenshot_ids"].split("；") if x]
        notes = [x for x in m["screenshot_notes"].split("；") if x]
        for i, sid in enumerate(ids):
            ev = ev_by_id[sid]
            right_parts.append(img(ev.get("screenshot_token", ""), 520, f"{sid}.png"))
            right_parts.append(f"<p>{escape(notes[i] if i < len(notes) else ev.get('similarity_note', ''))}</p>")
    compare_rows.append([left, "".join(right_parts)])

    for q in qs:
        if q["qnum"] in {12, 13, 14}:
            continue
        ms = final.get(q["question_id"], [])
        if not ms:
            continue
        left = f"<p><b>第{q['qnum']}题</b></p>{img(q.get('pdf_crop_token',''), 560, f'yunnan_q{q['qnum']:02d}.png')}"
        right_parts = []
        for m in ms:
            right_parts.append(f"<p><b>{escape(m['video_name'])}</b></p><p>{escape(m['hit_reason'])}</p>")
            for sid in [x for x in m["screenshot_ids"].split("；") if x]:
                ev = ev_by_id[sid]
                right_parts.append(img(ev.get("screenshot_token", ""), 520, f"{sid}.png"))
        compare_rows.append([left, "".join(right_parts)])

    return "\n".join(
        [
            "<title>洋葱学园 VS 2026年云南省中考化学试卷 押题对比（v3.1科普题组修正版）</title>",
            "<h1>基本认识</h1>",
            "<p>v3.1 补充识别“阅读材料+多题共用图表”的科普阅读题组。第12-14题作为一个整体题组展示，左列使用公共材料整页裁图。</p>",
            "<h1>试卷分析表</h1>",
            table(analysis_rows, ["题型", "题号", "分值", "视觉锚点/核心模型", "押中判定", "洋葱对应内容"], [92, 75, 55, 250, 95, 350]),
            "<h1>内容对比表</h1>",
            table(compare_rows, ["中考题目", "洋葱内容"], [560, 560]),
        ]
    )


def process_doc(group_path: Path, yunnan_url: str) -> str:
    rows = [
        ["漏洞1", "按单题题号裁图", "第12题只保留选项，漏掉上方公共材料和图1-图3。", "新增题组公共材料裁图。"],
        ["漏洞2", "按单题文本打题型", "12题被标基础题，13题被标基本实验题，14题被普通CO2误触发。", "识别“回答12～14题”，统一标为科普阅读题组。"],
        ["漏洞3", "科普阅读截图锚点过窄", "重难点科普阅读截图只保留“科普阅读材料”，没有柱状图/折线图视觉锚点。", "给科普阅读截图补充图文阅读、坐标曲线、表格数据锚点。"],
    ]
    return "\n".join(
        [
            "<title>v3.1规则补丁：云南12-14科普阅读题组审计</title>",
            "<h1>补丁结论</h1>",
            f'<p>已新生成云南卷修正版交付文档：<a href="{escape(yunnan_url)}">云南卷 v3.1科普题组修正版</a></p>',
            "<p>本补丁不覆盖旧 v3 文档，作为规则修正和备份版本保留。</p>",
            "<h1>问题诊断</h1>",
            table(rows, ["问题", "原规则", "表现", "v3.1修正"], [90, 190, 360, 360]),
            "<h1>题组裁图</h1>",
            f"<p>本地题组裁图路径：{escape(str(group_path))}</p>",
            "<h1>新增规则</h1>",
            "<p>遇到“回答12～14题/回答若干题”等提示时，先建立题组，再将公共材料、流程图、曲线图、表格图统一作为题组视觉锚点传给组内所有题。</p>",
            "<p>科普阅读宣传匹配可优先看“长材料+图表/曲线+信息提取设问”的版式相似，不要求素材主题完全相同。</p>",
            "<p>CO2 相关锚点细分：只有同时出现 CO2/二氧化碳 与 NaOH/氢氧化钠，才进入 NaOH-CO2 反应证明模型。</p>",
        ]
    )


def summary_doc(yunnan_url: str, process_url: str) -> str:
    return "\n".join(
        [
            "<title>2026 中考化学押题对比汇总（v3.1补丁）</title>",
            "<h1>本次补丁</h1>",
            "<p>补充云南第12-14题科普阅读题组匹配，新增题组公共材料裁图和科普阅读图文题组规则。</p>",
            table(
                [
                    [f'<a href="{escape(yunnan_url)}">云南卷 v3.1科普题组修正版</a>', "第12-14题组新增命中", "新中考培优 科普阅读理解（下）；重难点培优 科普阅读理解题"],
                    [f'<a href="{escape(process_url)}">v3.1规则补丁过程文档</a>', "规则审计", "题组裁图、科普阅读锚点、CO2误触发修正"],
                ],
                ["文档", "更新内容", "说明"],
                [380, 180, 430],
            ),
        ]
    )


def main() -> None:
    ensure_dirs()
    group_path = crop_group()
    group_token = upload_image("QGROUP::YUNNAN_Q12_14", group_path)
    exams, videos, evidence, matches = patch_data(group_token, group_path)
    y_xml = yunnan_doc(exams, videos, evidence, matches)
    y_path = DOCS / "云南卷_v3.1科普题组修正版.xml"
    y_path.write_text(y_xml, encoding="utf-8")
    y_doc = create_doc_from_xml(y_xml)
    p_xml = process_doc(group_path, y_doc["url"])
    p_path = DOCS / "v3.1规则补丁_云南12-14科普阅读题组审计.xml"
    p_path.write_text(p_xml, encoding="utf-8")
    p_doc = create_doc_from_xml(p_xml)
    s_xml = summary_doc(y_doc["url"], p_doc["url"])
    s_path = DOCS / "2026中考化学押题对比汇总_v3.1补丁.xml"
    s_path.write_text(s_xml, encoding="utf-8")
    s_doc = create_doc_from_xml(s_xml)
    urls = {"云南卷v3.1": y_doc, "过程补丁": p_doc, "汇总补丁": s_doc}
    save_json(FEISHU / "doc_urls_v31_patch.json", urls)
    print(json.dumps(urls, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
