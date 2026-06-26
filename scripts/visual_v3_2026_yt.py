#!/usr/bin/env python3
"""Build v3 visual-first promotion artifacts for 2026 chemistry comparisons.

The v3 pass treats Onion screenshots as screenshot-level evidence and favors
"looks similar at a glance" matches over broad same-topic matches.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from html import escape
from pathlib import Path
from typing import Any

import fitz
from PIL import Image

import compare_2026_yt as old_base
import professional_2026_yt as prof
import publish_2026_yt_image_docs as old_image


ROOT = Path("/Users/mbpro/Desktop/AI/chem-exam-analysis")
WORK = ROOT / "outputs" / "2026_yt_visual_v3"
DATA = WORK / "data"
DOCS = WORK / "docs"
FEISHU = WORK / "feishu"
PAYLOADS = WORK / "payloads"
CROPS = WORK / "pdf_question_crops"
LOCAL_EVIDENCE = WORK / "local_evidence"
PAPER_DIR = ROOT / "试卷" / "2026中考卷"
UTUBE_PDF = Path("/Users/mbpro/Desktop/洋葱/PPT和视频/NaOH与CO2反应探究题（U型管）/【定稿5.31】NaOH与CO2反应的探究题(tao第二稿）.pdf")

STAGING_PATH = FEISHU / "staging_doc.json"
IMAGE_TOKEN_PATH = FEISHU / "uploaded_image_tokens.json"
URLS_PATH = FEISHU / "doc_urls_v3.json"


def ensure_dirs() -> None:
    for path in [WORK, DATA, DOCS, FEISHU, PAYLOADS, CROPS, LOCAL_EVIDENCE]:
        path.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path, default: Any = None) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


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
        raise RuntimeError(f"Command returned ok=false: {' '.join(cmd)}\n{json.dumps(payload, ensure_ascii=False)}")
    if payload.get("_notice", {}).get("update"):
        save_json(FEISHU / "lark_cli_update_notice.json", payload["_notice"]["update"])
    return payload


def safe_slug(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")


def normalize(text: str) -> str:
    return old_base.normalize_text(text or "").replace(" ", "")


def split_tags(text: str) -> set[str]:
    return {x for x in re.split(r"[、；,，/ ]+", text or "") if x}


def readable(values: list[str] | set[str]) -> str:
    return "、".join(dict.fromkeys([x for x in values if x]))


def clean_exam_stem(text: str) -> str:
    text = old_base.normalize_text(text or "")
    # Strip trailing section headers accidentally captured by docx extraction.
    text = re.sub(r"\s+[一二三四五六七八九十]+、(?:计算题|实验与探究题|非选择题|选择题).*$", "", text)
    return text.strip()


VISUAL_ANCHORS: list[tuple[str, list[str]]] = [
    ("U型管", ["U型管", "U 形管", "红墨水", "压强", "液面"]),
    ("注射器", ["注射器", "推注", "活塞"]),
    ("集气瓶/锥形瓶", ["集气瓶", "锥形瓶", "烧瓶", "广口瓶"]),
    ("CO2-NaOH反应证明", ["CO2", "二氧化碳", "NaOH", "氢氧化钠"]),
    ("加酸产生气泡", ["稀盐酸", "盐酸", "气泡", "碳酸钠", "碳酸盐"]),
    ("流程图", ["流程图", "工艺流程", "滤液", "滤渣", "废液", "废渣", "制备", "回收", "提纯"]),
    ("实验装置图", ["装置", "实验", "仪器", "导管", "试管", "烧杯", "水槽"]),
    ("表格数据", ["表格", "数据", "记录", "实验次数", "质量/g", "加入"]),
    ("坐标曲线", ["坐标", "曲线", "图像", "pH", "溶解度曲线", "折线"]),
    ("溶解度曲线", ["溶解度曲线", "溶解度", "饱和溶液", "结晶"]),
    ("pH曲线", ["pH", "中和反应", "滴加", "酸碱"]),
    ("金属酸图像", ["金属", "盐酸", "稀硫酸", "氢气", "活动性"]),
    ("推断框图", ["推断", "框图", "转化关系", "A、B", "A～"]),
    ("科普阅读材料", ["科普阅读", "阅读理解", "资料", "短文"]),
    ("制气装置", ["制取氧气", "制取二氧化碳", "发生装置", "收集装置"]),
    ("控制变量对照", ["控制变量", "对照实验", "影响因素", "变量"]),
]


def infer_visual_anchors(text: str) -> list[str]:
    compact = normalize(text)
    anchors = []
    for anchor, keys in VISUAL_ANCHORS:
        if anchor == "CO2-NaOH反应证明":
            has_co2 = "CO2" in text or "二氧化碳" in compact
            has_naoh = "NaOH" in text or "氢氧化钠" in compact
            if has_co2 and has_naoh:
                anchors.append(anchor)
            continue
        if any(normalize(k) in compact for k in keys):
            anchors.append(anchor)
    return anchors


def route_type(text: str, fallback: str = "") -> str:
    compact = normalize(clean_exam_stem(text))
    if any(k in compact for k in ["工艺流程", "流程图", "滤液", "滤渣", "废液", "废渣", "回收", "提纯", "制备"]):
        return "工艺流程题"
    if any(k in compact for k in ["科普阅读", "阅读理解", "资料", "短文"]):
        return "科普阅读题"
    if any(k in compact for k in ["推断", "框图", "转化关系"]) and any(k in compact for k in ["A", "B", "C", "甲", "乙"]):
        return "推断题"
    if any(k in compact for k in ["探究", "猜想", "实验探究", "实验再探究", "反思总结", "控制变量", "对照实验", "数字化", "传感器"]):
        return "科学探究题"
    if any(k in compact for k in ["计算", "质量分数", "根据化学方程式", "样品", "表格", "坐标", "曲线"]) and any(k in compact for k in ["质量", "数据", "g", "完全反应", "分数"]):
        return "计算题"
    if any(k in compact for k in ["装置", "仪器", "制取", "收集", "检验", "过滤", "蒸发"]):
        return "基本实验题"
    return fallback or "基础题"


def is_composite(question: dict) -> bool:
    sigs = split_tags(question.get("signatures", ""))
    anchors = infer_visual_anchors(question.get("stem", ""))
    return len(sigs) >= 2 or (question.get("question_type") in {"综合应用题"} and len(anchors) >= 2)


def load_v2_data() -> tuple[list[dict], list[dict], list[dict]]:
    data_dir = ROOT / "outputs" / "2026_yt_professional" / "data"
    exams = load_json(data_dir / "exam_records.json", [])
    videos = load_json(data_dir / "video_records.json", [])
    transcripts = load_json(data_dir / "transcripts.json", [])
    if not exams or not videos:
        raise RuntimeError("v2 professional data is missing; run scripts/professional_2026_yt.py first.")
    return exams, videos, transcripts


def render_pdf_page(pdf_path: Path, page_index: int, out_path: Path, zoom: float = 1.6, clip: fitz.Rect | None = None) -> Path:
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))
    doc.close()
    return out_path


def question_positions(pdf_path: Path) -> list[dict]:
    doc = fitz.open(pdf_path)
    positions: list[dict] = []
    seen: set[int] = set()
    for page_index, page in enumerate(doc):
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text, *_ = block
            m = re.match(r"\s*(\d{1,2})[．\.\、]", text or "")
            if not m:
                continue
            qnum = int(m.group(1))
            if qnum in seen:
                continue
            if not (1 <= qnum <= 40):
                continue
            seen.add(qnum)
            positions.append(
                {
                    "qnum": qnum,
                    "page_index": page_index,
                    "y0": float(y0),
                    "x0": float(x0),
                    "page_width": float(page.rect.width),
                    "page_height": float(page.rect.height),
                }
            )
    doc.close()
    positions.sort(key=lambda p: (p["page_index"], p["y0"], p["qnum"]))
    return positions


def crop_question_from_pdf(pdf_path: Path, paper: str, qnum: int, positions: list[dict]) -> dict:
    current = next((p for p in positions if p["qnum"] == qnum), None)
    if not current:
        return {"path": "", "page": "", "status": "未定位到题号"}
    later = [p for p in positions if (p["page_index"], p["y0"]) > (current["page_index"], current["y0"])]
    nxt = later[0] if later else None
    doc = fitz.open(pdf_path)
    parts: list[Path] = []
    slug = safe_slug(paper)
    out_dir = CROPS / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    start_page = current["page_index"]
    end_page = nxt["page_index"] if nxt else start_page
    for page_index in range(start_page, end_page + 1):
        page = doc[page_index]
        left = 42
        right = page.rect.width - 42
        if page_index == start_page:
            top = max(35, current["y0"] - 4)
        else:
            top = 36
        if nxt and page_index == nxt["page_index"]:
            bottom = max(top + 80, nxt["y0"] - 8)
        else:
            bottom = page.rect.height - 28
        if bottom <= top + 40:
            continue
        clip = fitz.Rect(left, top, right, bottom)
        part_path = out_dir / f"q{qnum:02d}_p{page_index+1}.png"
        pix = page.get_pixmap(matrix=fitz.Matrix(2.25, 2.25), clip=clip, alpha=False)
        pix.save(str(part_path))
        parts.append(part_path)
    doc.close()
    if not parts:
        return {"path": "", "page": str(current["page_index"] + 1), "status": "裁图为空"}
    if len(parts) == 1:
        final = out_dir / f"q{qnum:02d}.png"
        if final != parts[0]:
            Image.open(parts[0]).convert("RGB").save(final)
    else:
        images = [Image.open(p).convert("RGB") for p in parts]
        width = max(img.width for img in images)
        height = sum(img.height for img in images)
        canvas = Image.new("RGB", (width, height), "white")
        y = 0
        for img in images:
            canvas.paste(img, (0, y))
            y += img.height
        final = out_dir / f"q{qnum:02d}.png"
        canvas.save(final)
    pages = f"{start_page + 1}" if start_page == end_page else f"{start_page + 1}-{end_page + 1}"
    return {"path": str(final), "page": pages, "status": "PDF原卷裁图"}


def build_exam_v3(exams: list[dict]) -> list[dict]:
    pdf_positions: dict[str, list[dict]] = {}
    out = []
    for q in exams:
        paper = q["paper"]
        pdf_path = PAPER_DIR / f"{paper}.pdf"
        if not pdf_path.exists():
            pdf_path = PAPER_DIR / f"{paper}.docx.pdf"
        if pdf_path.exists() and paper not in pdf_positions:
            pdf_positions[paper] = question_positions(pdf_path)
        stem = clean_exam_stem(q["stem"])
        qtype = route_type(stem, q.get("question_type", ""))
        anchors = infer_visual_anchors(stem)
        crop = {"path": "", "page": "", "status": "无PDF"}
        if pdf_path.exists():
            crop = crop_question_from_pdf(pdf_path, paper, q["qnum"], pdf_positions.get(paper, []))
        item = {
            **q,
            "stem": stem,
            "question_type_v3": qtype,
            "visual_anchors": readable(anchors),
            "is_composite": is_composite({**q, "stem": stem, "question_type": qtype}),
            "pdf_path": str(pdf_path) if pdf_path.exists() else "",
            "pdf_page": crop["page"],
            "pdf_crop_path": crop["path"],
            "pdf_crop_token": "",
            "pdf_crop_status": crop["status"],
            "qa_status_v3": "AI初筛-视觉版",
        }
        if "武威市" in paper and q["qnum"] == 17:
            item["question_type_v3"] = "科学探究题"
            item["visual_anchors"] = readable(["U型管", "注射器", "集气瓶/锥形瓶", "CO2-NaOH反应证明", "加酸产生气泡", "控制变量对照"])
            item["qa_status_v3"] = "校准样例-人工规则"
        out.append(item)
    save_json(DATA / "exam_records_v3.json", out)
    return out


def render_u_tube_evidence() -> list[dict]:
    if not UTUBE_PDF.exists():
        return []
    pages = {
        6: "同为 CO2 通入/吸收后无明显现象，需要设计实验证明 NaOH 与 CO2 反应。",
        7: "同为双集气瓶+注射器+U 型管红墨水装置，用压强变化证明 NaOH 与 CO2 反应。",
        23: "同为控制变量对照，用 NaOH 溶液与水对照排除 CO2 溶于水的干扰。",
        28: "同为从反应物消耗角度证明无明显现象反应发生，外观含 U 型管与液面变化。",
        53: "同为总结无明显现象反应的两条证明路径：证明反应物消耗或证明生成物。",
    }
    doc = fitz.open(UTUBE_PDF)
    records = []
    for page_no, note in pages.items():
        if page_no > doc.page_count:
            continue
        page = doc[page_no - 1]
        out_path = LOCAL_EVIDENCE / "NaOH_CO2_U型管" / f"slide_{page_no:02d}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
        pix.save(str(out_path))
        text = page.get_text("text").replace("\n", " ")
        records.append(
            {
                "screenshot_id": f"LOCAL-NAOH-CO2-U-S{page_no:02d}",
                "video_id": "LOCAL-NAOH-CO2-U",
                "video_name": "NaOH与CO2反应的探究题（U型管）",
                "source": "本地PPT/PDF补充",
                "source_row": 0,
                "screenshot_col": f"PDF第{page_no}页",
                "screenshot_token": "",
                "local_path": str(out_path),
                "screenshot_text": text[:800],
                "visual_anchors": readable(infer_visual_anchors(text + " U型管 注射器 红墨水 CO2 NaOH")),
                "adapted_type": "科学探究题",
                "adapted_knowledge": "NaOH与CO2反应探究、控制变量、无明显现象反应证明",
                "quality_status": "v3校准证据",
                "similarity_note": note,
            }
        )
    doc.close()
    return records


def build_video_and_evidence_v3(videos: list[dict]) -> tuple[list[dict], list[dict]]:
    video_v3 = []
    evidence = []
    for v in videos:
        text = " ".join([v.get("video_name", ""), v.get("hierarchy", ""), v.get("content_summary", ""), v.get("signatures", "")])
        name_text = " ".join([v.get("video_name", ""), v.get("hierarchy", "")])
        compact_name = normalize(name_text)
        if any(k in compact_name for k in ["工艺流程", "流程题", "金属回收", "粗盐提纯", "除杂", "制备"]):
            vtype = "工艺流程题"
        elif any(k in compact_name for k in ["计算", "质量分数", "溶解度曲线", "表格与图像", "表格与坐标", "图像坐标", "方程式"]):
            vtype = "计算题"
        elif any(k in compact_name for k in ["推断"]):
            vtype = "推断题"
        elif any(k in compact_name for k in ["科普阅读", "阅读理解"]):
            vtype = "科普阅读题"
        elif any(k in compact_name for k in ["探究", "实验", "控制变量", "NaOH与CO2", "二氧化碳", "测定空气", "制取氧气"]):
            vtype = "科学探究题"
        else:
            vtype = route_type(text, v.get("question_type", ""))
        anchors = infer_visual_anchors(text)
        video_v3.append({**v, "question_type_v3": vtype, "visual_anchors": readable(anchors)})
        token_pairs = []
        for raw in re.split(r"[；;]", v.get("screenshot_tokens", "")):
            if ":" in raw:
                col, token = raw.split(":", 1)
                if token.strip():
                    token_pairs.append((col.strip(), token.strip()))
        refs = v.get("screenshot_refs", "")
        for idx, (col, token) in enumerate(token_pairs, 1):
            cell_text = refs
            e_anchors = infer_visual_anchors(" ".join([text, col, cell_text]))
            evidence.append(
                {
                    "screenshot_id": f"{v['video_id']}-IMG{idx:02d}",
                    "video_id": v["video_id"],
                    "video_name": v.get("video_name", ""),
                    "source": v.get("source", ""),
                    "source_row": v.get("source_row", 0),
                    "screenshot_col": col,
                    "screenshot_token": token,
                    "local_path": "",
                    "screenshot_text": cell_text[:800],
                    "visual_anchors": readable(e_anchors or anchors),
                    "adapted_type": vtype,
                    "adapted_knowledge": v.get("knowledge_tags", ""),
                    "quality_status": "待人工视觉复核",
                    "similarity_note": "",
                }
            )
    u_records = render_u_tube_evidence()
    if u_records:
        video_v3.append(
            {
                "video_id": "LOCAL-NAOH-CO2-U",
                "source": "本地PPT/PDF补充",
                "sheet_id": "",
                "source_row": 0,
                "sheet_url": "",
                "video_name": "NaOH与CO2反应的探究题（U型管）",
                "hierarchy": "本地PPT和视频 / NaOH与CO2反应探究题（U型管）",
                "screenshot_refs": "PDF页拆图",
                "screenshot_tokens": "",
                "transcript_id": "",
                "transcript_file": "",
                "transcript_match_confidence": 1,
                "transcript_match_status": "本地素材直接匹配",
                "content_summary": "围绕 NaOH 与 CO2 无明显现象反应的证明，使用对照实验、U 型管红墨水、压强变化和生成物/反应物消耗证据。",
                "question_type": "科学探究题",
                "question_type_v3": "科学探究题",
                "knowledge_tags": "碳与二氧化碳、酸碱盐、科学探究",
                "problem_tags": "反应证明、控制变量、无明显现象可视化",
                "difficulty": 4,
                "method_skeleton": "CO2与NaOH吸收压强模型",
                "key_constraints": "NaOH与CO2反应探究、U型管压强变化、对照实验",
                "signatures": "NaOH与CO2反应探究、控制变量实验",
                "qa_status": "人工规则补充",
                "visual_anchors": "U型管、注射器、集气瓶/锥形瓶、CO2-NaOH反应证明、控制变量对照",
            }
        )
        evidence.extend(u_records)
    save_json(DATA / "video_records_v3.json", video_v3)
    save_json(DATA / "screenshot_evidence_v3.json", evidence)
    return video_v3, evidence


def route_allowed(qtype: str, vtype: str, composite: bool) -> bool:
    if qtype == vtype:
        return True
    if qtype == "科学探究题":
        return vtype in {"科学探究题", "基本实验题"}
    if qtype == "计算题":
        return vtype == "计算题"
    if qtype == "工艺流程题":
        return vtype == "工艺流程题"
    if qtype in {"推断题", "科普阅读题"}:
        return vtype == qtype
    if composite:
        return vtype in {"综合应用题", qtype}
    return qtype in {"基础题", "综合应用题"} and vtype in {qtype, "综合应用题"}


def score_match(q: dict, v: dict, evs: list[dict]) -> tuple[float, str, str, list[dict]]:
    qtype = q["question_type_v3"]
    vtype = v["question_type_v3"]
    composite = bool(q.get("is_composite"))
    if not route_allowed(qtype, vtype, composite):
        return 0, "题型硬路由不通过", "不同题型宣传版不混放", []

    q_sig = split_tags(q.get("signatures", ""))
    v_sig = split_tags(v.get("signatures", ""))
    q_anchors = split_tags(q.get("visual_anchors", ""))
    q_kn = split_tags(q.get("knowledge_tags", ""))
    v_kn = split_tags(v.get("knowledge_tags", ""))
    selected = []
    for e in evs:
        e_anchors = split_tags(e.get("visual_anchors", ""))
        overlap = len(q_anchors & e_anchors)
        text_bonus = 0
        combined = normalize(e.get("screenshot_text", "") + e.get("video_name", "") + e.get("similarity_note", ""))
        for key in q_anchors:
            if normalize(key) in combined:
                text_bonus += 1
        e["_visual_score"] = overlap * 12 + text_bonus * 3
        if overlap or text_bonus or e.get("quality_status") == "v3校准证据":
            selected.append(e)
    selected.sort(key=lambda x: x.get("_visual_score", 0), reverse=True)

    sig_overlap = q_sig & v_sig
    anchor_overlap = q_anchors & split_tags(v.get("visual_anchors", ""))
    kn_overlap = q_kn & v_kn
    score = 0.0
    if qtype == vtype:
        score += 24
    if sig_overlap:
        score += 36 + min(len(sig_overlap), 2) * 6
    if anchor_overlap:
        score += 22 + min(len(anchor_overlap), 4) * 4
    if kn_overlap:
        score += min(len(kn_overlap), 3) * 4
    if selected:
        score += min(selected[0].get("_visual_score", 0), 28)
    if v["video_id"] == "LOCAL-NAOH-CO2-U" and "武威市" in q["paper"] and q["qnum"] == 17:
        score = 200
        selected = selected[:3]

    reason_bits = []
    if sig_overlap:
        reason_bits.append(f"核心模型：{readable(sig_overlap)}")
    if anchor_overlap:
        reason_bits.append(f"视觉锚点：{readable(anchor_overlap)}")
    if qtype == vtype:
        reason_bits.append(f"题型同为{qtype}")
    reason = "；".join(reason_bits) or "弱相关"
    reject = ""
    if score < 76:
        reject = "宣传视觉版阈值不足：缺少强视觉锚点或核心模型"
    if not selected:
        reject = "未找到可展示的截图级证据"
    return score, reason, reject, selected[: (6 if composite else 3)]


def build_matches_v3(exams: list[dict], videos: list[dict], evidence: list[dict]) -> list[dict]:
    evidence_by_video: dict[str, list[dict]] = {}
    for e in evidence:
        evidence_by_video.setdefault(e["video_id"], []).append(e)
    matches = []
    for q in exams:
        scored = []
        for v in videos:
            score, reason, reject, selected = score_match(q, v, evidence_by_video.get(v["video_id"], []))
            if score <= 0:
                continue
            scored.append((score, reason, reject, selected, v))
        scored.sort(key=lambda x: x[0], reverse=True)
        max_videos = 2 if q.get("is_composite") else 1
        accepted = 0
        for score, reason, reject, selected, v in scored[:12]:
            best_visual_score = max((e.get("_visual_score", 0) for e in selected), default=0)
            visual_gate = best_visual_score >= 24 or v["video_id"] == "LOCAL-NAOH-CO2-U"
            final_show = bool(score >= 110 and selected and visual_gate and accepted < max_videos)
            if final_show:
                accepted += 1
            elif score >= 55 and not reject:
                reject = "同题已保留更强视觉证据"
            matches.append(
                {
                    "match_id": f"{q['question_id']}__{v['video_id']}",
                    "question_id": q["question_id"],
                    "paper": q["paper"],
                    "qnum": q["qnum"],
                    "question_type_v3": q["question_type_v3"],
                    "video_id": v["video_id"],
                    "video_name": v["video_name"],
                    "video_type_v3": v["question_type_v3"],
                    "score": round(score, 1),
                    "route_result": "通过" if route_allowed(q["question_type_v3"], v["question_type_v3"], bool(q.get("is_composite"))) else "不通过",
                    "visual_anchors": readable(split_tags(q.get("visual_anchors", "")) & split_tags(v.get("visual_anchors", ""))),
                    "hit_reason": reason,
                    "reject_reason": "" if final_show else (reject or "同题最终证据已保留更强项"),
                    "screenshot_ids": "；".join(e["screenshot_id"] for e in selected),
                    "screenshot_notes": "；".join(e.get("similarity_note") or note_for_evidence(q, e) for e in selected),
                    "final_show": final_show,
                }
            )
        if not any(m["question_id"] == q["question_id"] and m["final_show"] for m in matches):
            matches.append(
                {
                    "match_id": f"{q['question_id']}__NO_VISUAL_MATCH",
                    "question_id": q["question_id"],
                    "paper": q["paper"],
                    "qnum": q["qnum"],
                    "question_type_v3": q["question_type_v3"],
                    "video_id": "",
                    "video_name": "",
                    "video_type_v3": "",
                    "score": 0,
                    "route_result": "未进入",
                    "visual_anchors": q.get("visual_anchors", ""),
                    "hit_reason": "",
                    "reject_reason": "未达到宣传视觉版展示标准",
                    "screenshot_ids": "",
                    "screenshot_notes": "",
                    "final_show": False,
                }
            )
    save_json(DATA / "matches_v3.json", matches)
    return matches


def note_for_evidence(q: dict, e: dict) -> str:
    q_anchors = split_tags(q.get("visual_anchors", ""))
    e_anchors = split_tags(e.get("visual_anchors", ""))
    overlap = readable(q_anchors & e_anchors)
    if overlap:
        return f"同样呈现{overlap}，截图外观和设问任务可直接对照。"
    return "同一视频内的截图级证据，辅助展示相同模型或方法。"


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
    url = doc.get("url") or doc.get("doc_url") or f"https://guanghe.feishu.cn/docx/{doc_id}"
    return {"doc_id": doc_id, "url": url, "raw": doc}


def ensure_staging_doc() -> dict:
    if STAGING_PATH.exists():
        return load_json(STAGING_PATH)
    doc = create_doc_from_xml("<title>2026中考化学v3视觉版截图素材库</title><p>用于上传 PDF 原卷裁图和本地课程截图。</p>")
    save_json(STAGING_PATH, doc)
    return doc


def upload_local_images(
    exams: list[dict],
    evidence: list[dict],
    question_ids: set[str] | None = None,
    evidence_ids: set[str] | None = None,
) -> tuple[dict[str, str], list[dict], list[dict]]:
    tokens = load_json(IMAGE_TOKEN_PATH, {})
    staging = ensure_staging_doc()
    doc_id = staging["doc_id"]

    def upload(key: str, file_path: str, width: str = "700") -> str:
        if not file_path:
            return ""
        if key in tokens:
            return tokens[key]
        rel_path = str(Path(file_path).relative_to(ROOT))
        payload = run_json(
            [
                "lark-cli",
                "docs",
                "+media-insert",
                "--doc",
                doc_id,
                "--file",
                rel_path,
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
        save_json(IMAGE_TOKEN_PATH, tokens)
        print(f"uploaded {key}", flush=True)
        time.sleep(0.2)
        return tokens[key]

    for q in exams:
        if question_ids is not None and q["question_id"] not in question_ids:
            continue
        key = f"Q::{q['question_id']}"
        q["pdf_crop_token"] = upload(key, q.get("pdf_crop_path", ""), "720")
    for e in evidence:
        if evidence_ids is not None and e["screenshot_id"] not in evidence_ids:
            continue
        if e.get("local_path"):
            e["screenshot_token"] = upload(f"E::{e['screenshot_id']}", e["local_path"], "620")
    save_json(DATA / "exam_records_v3.json", exams)
    save_json(DATA / "screenshot_evidence_v3.json", evidence)
    return tokens, exams, evidence


def img_tag(token: str, width: int = 560, name: str = "") -> str:
    if not token:
        return ""
    name_attr = f' name="{escape(name)}"' if name else ""
    return f'<img src="{escape(token)}" width="{width}"{name_attr}/>'


def html_table(rows: list[list[str]], header: list[str], widths: list[int]) -> str:
    colgroup = "<colgroup>" + "".join(f'<col width="{w}"/>' for w in widths) + "</colgroup>"
    thead = "<thead><tr>" + "".join(f'<th background-color="light-gray">{escape(h)}</th>' for h in header) + "</tr></thead>"
    tbody = "<tbody>" + "".join("<tr>" + "".join(f'<td vertical-align="top">{cell}</td>' for cell in row) + "</tr>" for row in rows) + "</tbody>"
    return f"<table>{colgroup}{thead}{tbody}</table>"


def build_final_docs(exams: list[dict], videos: list[dict], evidence: list[dict], matches: list[dict], base_url: str = "") -> list[dict]:
    video_map = {v["video_id"]: v for v in videos}
    evidence_map = {e["screenshot_id"]: e for e in evidence}
    final_by_q: dict[str, list[dict]] = {}
    for m in matches:
        if m.get("final_show"):
            final_by_q.setdefault(m["question_id"], []).append(m)
    by_paper: dict[str, list[dict]] = {}
    for q in exams:
        by_paper.setdefault(q["paper"], []).append(q)

    manifest = []
    examples = []
    for paper, qs in sorted(by_paper.items()):
        qs.sort(key=lambda x: x["qnum"])
        hit_qs = [q for q in qs if final_by_q.get(q["question_id"])]
        total_score = sum(q.get("score") or 0 for q in qs)
        hit_score = sum(q.get("score") or 0 for q in hit_qs)
        analysis_rows = []
        compare_rows = []
        for q in qs:
            ms = sorted(final_by_q.get(q["question_id"], []), key=lambda x: x["score"], reverse=True)
            onion_lines = []
            for m in ms:
                onion_lines.append(f"{m['video_name']}：{m['hit_reason']}")
            analysis_rows.append(
                [
                    escape(q["question_type_v3"]),
                    escape(str(q["qnum"])),
                    escape(str(q.get("score") or "")),
                    escape(f"{q.get('visual_anchors','')}｜{q.get('signatures','')}"),
                    escape("押中" if ms else "未进入宣传版"),
                    escape("\n".join(onion_lines) if onion_lines else "未达到视觉宣传展示标准").replace("\n", "<br/>"),
                ]
            )
            if not ms:
                continue
            left = f"<p><b>第{q['qnum']}题</b></p>{img_tag(q.get('pdf_crop_token',''), 560, f'{safe_slug(paper)}_q{q['qnum']:02d}.png')}"
            if not q.get("pdf_crop_token"):
                left += f"<p>{escape(q['stem'][:520])}</p>"
            right_parts = []
            for m in ms:
                v = video_map.get(m["video_id"], {})
                right_parts.append(f"<p><b>{escape(v.get('video_name', m['video_name']))}</b></p>")
                right_parts.append(f"<p>{escape(m['hit_reason'])}</p>")
                ids = [sid for sid in m.get("screenshot_ids", "").split("；") if sid]
                notes = [n for n in m.get("screenshot_notes", "").split("；") if n]
                for idx, sid in enumerate(ids):
                    e = evidence_map.get(sid, {})
                    token = e.get("screenshot_token", "")
                    note = notes[idx] if idx < len(notes) else note_for_evidence(q, e)
                    right_parts.append(img_tag(token, 520, f"{sid}.png"))
                    right_parts.append(f"<p>{escape(note)}</p>")
            compare_rows.append([left, "".join(right_parts)])
            examples.append({"paper": paper, "qnum": q["qnum"], "question_type": q["question_type_v3"], "anchors": q.get("visual_anchors", ""), "videos": onion_lines[:2]})

        base_para = f'<p>过程库：<a href="{escape(base_url)}">2026中考化学押题工作台v3（宣传视觉版过程库）</a></p>' if base_url else ""
        xml = "\n".join(
            [
                f"<title>洋葱学园 VS {escape(paper)} 押题对比（v3宣传视觉版）</title>",
                "<h1>基本认识</h1>",
                f"<p>本卷共抽取 {len(qs)} 道题；v3 宣传视觉版只展示题型路由、核心模型和截图外观均较强的押题证据。</p>",
                f"<p><b>最终展示命中：</b>{len(hit_qs)}/{len(qs)}；按分值估算覆盖 {hit_score}/{total_score or '未知'}。</p>",
                "<p>左列为 PDF 原卷裁图，右列为最相似洋葱视频截图；普通题默认展示 1 个视频的 1-3 张截图，综合题可展示多个视频。</p>",
                base_para,
                "<h1>试卷分析表</h1>",
                html_table(analysis_rows, ["题型", "题号", "分值", "视觉锚点/核心模型", "押中判定", "洋葱对应内容"], [92, 55, 55, 230, 95, 370]),
                "<h1>内容对比表</h1>",
                html_table(compare_rows or [["<p>本卷暂无达到 v3 宣传视觉版标准的题目。</p>", ""]], ["中考题目", "洋葱内容"], [560, 560]),
            ]
        )
        path = DOCS / f"{safe_slug(paper)}_v3宣传视觉版.xml"
        path.write_text(xml, encoding="utf-8")
        manifest.append(
            {
                "title": paper,
                "local_xml": str(path),
                "question_count": len(qs),
                "hit_count": len(hit_qs),
                "total_score": total_score,
                "hit_score": hit_score,
            }
        )
    save_json(DATA / "docs_manifest_pre_publish.json", manifest)
    save_json(DATA / "examples_v3.json", examples)
    return manifest


BASE_SCHEMA = {
    "试卷题库v3": [
        {"name": "题目ID", "type": "text"},
        {"name": "试卷", "type": "text"},
        {"name": "题号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "题型硬分类", "type": "text"},
        {"name": "分值", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "PDF页码", "type": "text"},
        {"name": "PDF裁图路径", "type": "text"},
        {"name": "PDF截图Token", "type": "text"},
        {"name": "视觉锚点", "type": "text"},
        {"name": "核心模型签名", "type": "text"},
        {"name": "是否综合题", "type": "checkbox"},
        {"name": "题干摘要", "type": "text"},
        {"name": "截图质检状态", "type": "text"},
    ],
    "视频截图证据库v3": [
        {"name": "截图ID", "type": "text"},
        {"name": "视频ID", "type": "text"},
        {"name": "视频名称", "type": "text"},
        {"name": "来源", "type": "text"},
        {"name": "来源行号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "截图列", "type": "text"},
        {"name": "截图Token", "type": "text"},
        {"name": "本地路径", "type": "text"},
        {"name": "截图文字/时间点", "type": "text"},
        {"name": "视觉锚点", "type": "text"},
        {"name": "适配题型", "type": "text"},
        {"name": "适配知识点", "type": "text"},
        {"name": "截图质检状态", "type": "text"},
    ],
    "视频候选库v3": [
        {"name": "视频ID", "type": "text"},
        {"name": "来源", "type": "text"},
        {"name": "来源行号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "视频名称", "type": "text"},
        {"name": "题型硬分类", "type": "text"},
        {"name": "视觉锚点", "type": "text"},
        {"name": "知识点标签", "type": "text"},
        {"name": "问题标签", "type": "text"},
        {"name": "方法骨架", "type": "text"},
        {"name": "核心模型签名", "type": "text"},
    ],
    "匹配审计库v3": [
        {"name": "匹配ID", "type": "text"},
        {"name": "题目ID", "type": "text"},
        {"name": "试卷", "type": "text"},
        {"name": "题号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "题型", "type": "text"},
        {"name": "视频ID", "type": "text"},
        {"name": "视频名称", "type": "text"},
        {"name": "视频题型", "type": "text"},
        {"name": "视觉分", "type": "number"},
        {"name": "题型路由", "type": "text"},
        {"name": "视觉锚点", "type": "text"},
        {"name": "命中说明", "type": "text"},
        {"name": "截图ID列表", "type": "text"},
        {"name": "拒绝原因", "type": "text"},
        {"name": "进入最终文档", "type": "checkbox"},
    ],
    "规则与标签字典v3": [
        {"name": "规则ID", "type": "text"},
        {"name": "规则名称", "type": "text"},
        {"name": "规则内容", "type": "text"},
    ],
}


def batch_payload(path: Path, fields: list[str], rows: list[list[Any]], batch_size: int = 200) -> list[Path]:
    paths = []
    for i in range(0, len(rows), batch_size):
        p = path.with_name(f"{path.stem}_{i//batch_size+1:03d}.json")
        p.write_text(json.dumps({"fields": fields, "rows": rows[i : i + batch_size]}, ensure_ascii=False), encoding="utf-8")
        paths.append(p)
    return paths


def create_base() -> dict:
    info_path = FEISHU / "base_info_v3.json"
    if info_path.exists():
        return load_json(info_path)
    initial_fields = json.dumps(BASE_SCHEMA["试卷题库v3"], ensure_ascii=False)
    created = run_json(
        [
            "lark-cli",
            "base",
            "+base-create",
            "--as",
            "user",
            "--name",
            "2026中考化学押题工作台v3（宣传视觉版过程库）",
            "--time-zone",
            "Asia/Shanghai",
            "--table-name",
            "试卷题库v3",
            "--fields",
            initial_fields,
            "--format",
            "json",
        ],
        timeout=240,
    )
    text = json.dumps(created["data"], ensure_ascii=False)
    m = re.search(r'"app_token"\s*:\s*"([^"]+)"', text) or re.search(r'"base_token"\s*:\s*"([^"]+)"', text)
    if not m:
        raise RuntimeError(f"Cannot locate base token: {text}")
    base_token = m.group(1)
    table_map = {}
    tables = run_json(["lark-cli", "base", "+table-list", "--as", "user", "--base-token", base_token, "--format", "json"])["data"]
    for t in tables.get("items", []) or tables.get("tables", []):
        name = t.get("name") or t.get("table_name")
        tid = t.get("table_id") or t.get("id")
        if name and tid:
            table_map[name] = tid
    for name, fields in BASE_SCHEMA.items():
        if name in table_map:
            continue
        res = run_json(
            [
                "lark-cli",
                "base",
                "+table-create",
                "--as",
                "user",
                "--base-token",
                base_token,
                "--name",
                name,
                "--fields",
                json.dumps(fields, ensure_ascii=False),
                "--format",
                "json",
            ],
            timeout=240,
        )
        mt = re.search(r'"table_id"\s*:\s*"([^"]+)"', json.dumps(res["data"], ensure_ascii=False))
        if mt:
            table_map[name] = mt.group(1)
    base_url = created["data"].get("url") or created["data"].get("base", {}).get("url") or f"https://guanghe.feishu.cn/base/{base_token}"
    info = {"base_token": base_token, "base_url": base_url, "tables": table_map}
    save_json(info_path, info)
    return info


def rows_for_base(table: str) -> tuple[list[str], list[list[Any]]]:
    if table == "试卷题库v3":
        fields = ["题目ID", "试卷", "题号", "题型硬分类", "分值", "PDF页码", "PDF裁图路径", "PDF截图Token", "视觉锚点", "核心模型签名", "是否综合题", "题干摘要", "截图质检状态"]
        rows = [[r["question_id"], r["paper"], r["qnum"], r["question_type_v3"], r.get("score") or 0, r.get("pdf_page", ""), r.get("pdf_crop_path", ""), r.get("pdf_crop_token", ""), r.get("visual_anchors", ""), r.get("signatures", ""), bool(r.get("is_composite")), r.get("stem", "")[:90000], r.get("pdf_crop_status", "")] for r in load_json(DATA / "exam_records_v3.json", [])]
    elif table == "视频截图证据库v3":
        fields = ["截图ID", "视频ID", "视频名称", "来源", "来源行号", "截图列", "截图Token", "本地路径", "截图文字/时间点", "视觉锚点", "适配题型", "适配知识点", "截图质检状态"]
        rows = [[r["screenshot_id"], r["video_id"], r["video_name"], r["source"], r.get("source_row") or 0, r["screenshot_col"], r.get("screenshot_token", ""), r.get("local_path", ""), r.get("screenshot_text", "")[:90000], r.get("visual_anchors", ""), r.get("adapted_type", ""), r.get("adapted_knowledge", ""), r.get("quality_status", "")] for r in load_json(DATA / "screenshot_evidence_v3.json", [])]
    elif table == "视频候选库v3":
        fields = ["视频ID", "来源", "来源行号", "视频名称", "题型硬分类", "视觉锚点", "知识点标签", "问题标签", "方法骨架", "核心模型签名"]
        rows = [[r["video_id"], r["source"], r.get("source_row") or 0, r["video_name"], r.get("question_type_v3", ""), r.get("visual_anchors", ""), r.get("knowledge_tags", ""), r.get("problem_tags", ""), r.get("method_skeleton", ""), r.get("signatures", "")] for r in load_json(DATA / "video_records_v3.json", [])]
    elif table == "匹配审计库v3":
        fields = ["匹配ID", "题目ID", "试卷", "题号", "题型", "视频ID", "视频名称", "视频题型", "视觉分", "题型路由", "视觉锚点", "命中说明", "截图ID列表", "拒绝原因", "进入最终文档"]
        rows = [[r["match_id"], r["question_id"], r["paper"], r["qnum"], r["question_type_v3"], r["video_id"], r["video_name"], r["video_type_v3"], r["score"], r["route_result"], r["visual_anchors"], r["hit_reason"], r["screenshot_ids"], r["reject_reason"], r["final_show"]] for r in load_json(DATA / "matches_v3.json", [])]
    else:
        fields = ["规则ID", "规则名称", "规则内容"]
        rules = [
            ["V3-01", "题型硬路由", "工艺流程题优先只匹配工艺流程视频；科学探究题优先匹配科学探究/基本实验视频；计算题优先匹配计算视频。综合题允许多个视频拆分证据。"],
            ["V3-02", "视觉相似优先级", "第一优先实验装置/流程图/曲线/表格外观一致；第二优先素材和反应模型一致；第三优先设问任务或解题方法一致。只有知识点相同但截图外观不像，不进入宣传版最终文档。"],
            ["V3-03", "多截图展示", "普通题默认一个视频放1-3张最相似截图；综合题可放2-4个视频，总截图数默认不超过6张。"],
            ["V3-04", "PDF原卷裁图", "中考题目左列使用PDF原卷裁图，确保题图/表格随题展示，避免Word重排跑版。"],
        ]
        rows = rules
    return fields, rows


def populate_base(info: dict) -> None:
    done_path = FEISHU / "base_records_v3.json"
    if done_path.exists():
        return
    status = {}
    for table in BASE_SCHEMA:
        fields, rows = rows_for_base(table)
        paths = batch_payload(PAYLOADS / f"{safe_slug(table)}.json", fields, rows)
        table_status = []
        for path in paths:
            res = run_json(
                [
                    "lark-cli",
                    "base",
                    "+record-batch-create",
                    "--as",
                    "user",
                    "--base-token",
                    info["base_token"],
                    "--table-id",
                    info["tables"].get(table, table),
                    "--json",
                    f"@{path.relative_to(ROOT)}",
                    "--format",
                    "json",
                ],
                timeout=240,
            )
            table_status.append({"payload": str(path), "data": res["data"]})
            time.sleep(0.3)
        status[table] = table_status
    save_json(done_path, status)


def publish_docs(manifest: list[dict], base_url: str) -> dict:
    urls = load_json(URLS_PATH, {})
    for item in manifest:
        title = item["title"]
        if title not in urls:
            doc = create_doc_from_xml(Path(item["local_xml"]).read_text(encoding="utf-8"))
            urls[title] = doc
            save_json(URLS_PATH, urls)
            print(f"created doc {title}: {doc['url']}", flush=True)
    manifest_with_urls = []
    for item in manifest:
        item = dict(item)
        item["url"] = urls[item["title"]]["url"]
        item["doc_id"] = urls[item["title"]]["doc_id"]
        manifest_with_urls.append(item)
    save_json(DATA / "docs_manifest_published.json", manifest_with_urls)

    summary_title = "2026 中考化学押题对比汇总（v3宣传视觉版）"
    if summary_title not in urls:
        rows = []
        for p in manifest_with_urls:
            rows.append([
                f'<a href="{escape(p["url"])}">{escape(p["title"])}</a>',
                escape(f"{p['hit_count']}/{p['question_count']}"),
                escape(f"{p['hit_score']}/{p['total_score'] or '未知'}"),
            ])
        examples = load_json(DATA / "examples_v3.json", [])
        ex_rows = []
        for ex in examples[:30]:
            ex_rows.append([
                escape(ex["paper"]),
                escape(str(ex["qnum"])),
                escape(ex["question_type"]),
                escape(ex["anchors"]),
                escape("；".join(ex["videos"])),
            ])
        summary_xml = "\n".join(
            [
                "<title>2026 中考化学押题对比汇总（v3宣传视觉版）</title>",
                "<h1>总体说明</h1>",
                "<p>v3 宣传视觉版以截图外观相似为第一展示标准，优先保留实验装置、流程图、曲线、表格等“打眼相似”的证据。</p>",
                f'<p>过程库：<a href="{escape(base_url)}">2026中考化学押题工作台v3（宣传视觉版过程库）</a></p>',
                "<h1>分卷文档链接</h1>",
                html_table(rows, ["试卷", "最终展示命中题数", "按分值估算覆盖"], [420, 140, 140]),
                "<h1>典型多截图案例</h1>",
                html_table(ex_rows or [["暂无", "", "", "", ""]], ["试卷", "题号", "题型", "视觉锚点", "洋葱证据"], [240, 60, 100, 220, 460]),
                "<h1>专业结论</h1>",
                "<p>与 v2 相比，本版降低了仅同知识点候选的展示权重，强化题型路由和截图级证据选择。弱相关候选保留在匹配审计库，不进入最终宣传文档。</p>",
            ]
        )
        path = DOCS / "2026中考化学押题对比汇总_v3宣传视觉版.xml"
        path.write_text(summary_xml, encoding="utf-8")
        doc = create_doc_from_xml(summary_xml)
        urls[summary_title] = doc
        save_json(URLS_PATH, urls)
    return urls


def verify_docs(urls: dict) -> dict:
    checks = {}
    for title, item in urls.items():
        try:
            res = run_json(
                [
                    "lark-cli",
                    "docs",
                    "+fetch",
                    "--api-version",
                    "v2",
                    "--doc",
                    item["doc_id"],
                    "--scope",
                    "outline",
                    "--max-depth",
                    "3",
                    "--format",
                    "json",
                ],
                timeout=120,
            )
            content = res["data"]["document"]["content"]
            checks[title] = {"ok": True, "has_table": "<table" in content, "outline_sample": content[:1200]}
        except Exception as exc:
            checks[title] = {"ok": False, "error": str(exc)}
    save_json(FEISHU / "verification_v3.json", checks)
    return checks


def prepare() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    ensure_dirs()
    exams, videos, _transcripts = load_v2_data()
    exam_v3 = build_exam_v3(exams)
    video_v3, evidence = build_video_and_evidence_v3(videos)
    matches = build_matches_v3(exam_v3, video_v3, evidence)
    final_question_ids = {m["question_id"] for m in matches if m.get("final_show")}
    final_evidence_ids = {
        sid
        for m in matches
        if m.get("final_show")
        for sid in m.get("screenshot_ids", "").split("；")
        if sid
    }
    _, exam_v3, evidence = upload_local_images(exam_v3, evidence, final_question_ids, final_evidence_ids)
    matches = build_matches_v3(exam_v3, video_v3, evidence)
    return exam_v3, video_v3, evidence, matches


def publish() -> None:
    exam_v3 = load_json(DATA / "exam_records_v3.json", None)
    video_v3 = load_json(DATA / "video_records_v3.json", None)
    evidence = load_json(DATA / "screenshot_evidence_v3.json", None)
    matches = load_json(DATA / "matches_v3.json", None)
    if not all([exam_v3, video_v3, evidence, matches]):
        exam_v3, video_v3, evidence, matches = prepare()
    base_info = create_base()
    populate_base(base_info)
    manifest = build_final_docs(exam_v3, video_v3, evidence, matches, base_info["base_url"])
    urls = publish_docs(manifest, base_info["base_url"])
    checks = verify_docs(urls)
    save_json(DATA / "publish_summary_v3.json", {"base": base_info, "urls": urls, "checks": checks})
    notice = FEISHU / "lark_cli_update_notice.json"
    if notice.exists():
        subprocess.run(["lark-cli", "update"], cwd=ROOT, text=True, capture_output=True, timeout=300)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    if args.publish:
        publish()
    if not args.prepare and not args.publish:
        prepare()


if __name__ == "__main__":
    main()
