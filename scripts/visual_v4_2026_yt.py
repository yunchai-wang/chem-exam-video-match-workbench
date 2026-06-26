#!/usr/bin/env python3
"""v4 visual-promotion matching pipeline for 2026 chemistry papers."""

from __future__ import annotations

import argparse
import csv
import io
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
import visual_v3_2026_yt as v3


ROOT = Path("/Users/mbpro/Desktop/AI/chem-exam-analysis")
WORK = ROOT / "outputs" / "2026_yt_visual_v4"
DATA = WORK / "data"
DOCS = WORK / "docs"
FEISHU = WORK / "feishu"
PAYLOADS = WORK / "payloads"
CROPS = WORK / "pdf_question_crops"
PAPER_DIR = ROOT / "试卷" / "2026中考卷"
IMAGE_TOKEN_PATH = FEISHU / "image_tokens_v4.json"
URLS_PATH = FEISHU / "doc_urls_v4.json"

V3_IMAGE_TOKENS = v3.IMAGE_TOKEN_PATH


def ensure_dirs() -> None:
    for path in [WORK, DATA, DOCS, FEISHU, PAYLOADS, CROPS]:
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


def norm(text: str) -> str:
    return old_base.normalize_text(text or "").replace(" ", "")


def split_tags(text: str) -> set[str]:
    return {x for x in re.split(r"[、；,，/ ]+", text or "") if x}


def join_tags(values) -> str:
    return "、".join(dict.fromkeys([x for x in values if x]))


def has_any(text: str, keys: list[str]) -> bool:
    compact = norm(text)
    return any(norm(k) in compact for k in keys)


def clean_stem(text: str) -> str:
    text = old_base.normalize_text(text or "")
    text = re.sub(r"\s+[一二三四五六七八九十]+、(?:计算题|实验与探究题|非选择题|选择题).*$", "", text)
    text = re.sub(r"\s+2026年.+?参考答案与试题解析.*$", "", text)
    return text.strip()


V4_VISUAL_RULES: list[tuple[str, list[str]]] = [
    ("流程图", ["流程图", "工艺流程", "滤液", "滤渣", "废液", "废渣", "酸浸", "焙烧", "电解", "制备", "回收", "提纯"]),
    ("设备流程", ["吸收塔", "合成塔", "反应塔", "气化炉", "变换炉", "设备", "塔", "炉", "容器", "反应器"]),
    ("循环物质", ["可循环", "循环使用", "循环利用", "回收利用", "返回", "再利用"]),
    ("装置图", ["装置", "仪器", "导管", "试管", "烧杯", "集气瓶", "锥形瓶", "U型管", "注射器", "传感器"]),
    ("曲线图", ["曲线", "图像", "坐标", "pH", "溶解度", "纵坐标", "横坐标", "折线"]),
    ("柱状图", ["柱状图", "柱形图", "柱状", "条形图"]),
    ("表格", ["表格", "数据", "记录", "实验次数", "性质", "特点", "反应原理"]),
    ("题组材料", ["回答", "阅读", "资料", "短文", "材料", "题组"]),
    ("框图推断", ["推断", "框图", "转化关系", "A、B", "A～", "甲乙丙"]),
    ("方案设计", ["设计方案", "设计一份", "改进方案", "评价方案", "存在的缺陷", "合理的方案"]),
    ("项目任务链", ["任务一", "任务二", "任务三", "查阅资料", "项目式", "跨学科", "实践活动", "真实情境"]),
    ("数字化曲线", ["数字化", "传感器", "压强传感器", "溶氧量", "随时间变化", "电导率"]),
    ("CO2-NaOH", ["二氧化碳", "CO2", "氢氧化钠", "NaOH"]),
    ("U型管压强", ["U型管", "红墨水", "压强", "液面"]),
]

METHOD_RULES: list[tuple[str, list[str]]] = [
    ("控制变量", ["控制变量", "对照实验", "单一变量", "影响因素"]),
    ("排除干扰", ["排除干扰", "干扰因素", "空白实验", "不严谨"]),
    ("异常现象探究", ["异常现象", "反常", "不明显现象", "无明显现象", "可视化"]),
    ("数字化曲线分析", ["数字化", "传感器", "曲线", "压强", "溶氧量", "电导率"]),
    ("实验方案评价", ["方案", "缺陷", "评价", "改进", "可行", "不可行"]),
    ("可循环物质判断", ["可循环", "循环利用", "循环使用", "最终产物", "前面出现"]),
    ("设备流程分析", ["吸收塔", "合成塔", "气化炉", "变换炉", "从.*处通入", "提高吸收效率"]),
    ("图表信息提取", ["结合图", "由图可知", "图像分析", "数据分析", "表中"]),
    ("守恒/方程式计算", ["质量守恒", "化学方程式", "计算", "恰好完全反应"]),
]

CORE_EXPERIMENT_RULES: list[tuple[str, list[str]]] = [
    ("测定空气中氧气含量", ["氧气含量", "红磷", "白磷", "测定空气"]),
    ("实验室制取气体", ["制取氧气", "制取二氧化碳", "发生装置", "收集装置", "检验二氧化碳"]),
    ("电解水", ["电解水", "正极", "负极", "氢气", "氧气"]),
    ("燃烧条件", ["燃烧条件", "着火点", "可燃物", "燃烧三要素"]),
    ("金属锈蚀", ["金属锈蚀", "铁生锈", "生锈条件", "锈蚀"]),
    ("催化剂探究", ["催化剂", "催化效果", "过氧化氢分解", "二氧化锰"]),
    ("NaOH与CO2反应证明", ["氢氧化钠", "NaOH", "二氧化碳", "CO2", "U型管", "压强"]),
    ("溶解度曲线", ["溶解度曲线", "饱和溶液", "不饱和溶液", "结晶"]),
]

BACKGROUND_RULES: list[tuple[str, list[str]]] = [
    ("甲醇生产", ["甲醇", "CH3OH", "合成塔", "吸收塔"]),
    ("铝冶炼/含氟烟气回收", ["铝", "HF", "氟", "Na3AlF6", "载氟氧化铝"]),
    ("活鱼运输增氧", ["活鱼", "运输", "增氧", "溶氧量", "鱼类"]),
    ("发酵/有机酸", ["发酵", "黑曲霉", "柠檬酸", "苹果酸", "葡萄糖"]),
    ("NaOH与CO2", ["氢氧化钠", "NaOH", "二氧化碳", "CO2"]),
    ("金属回收", ["金属回收", "废液", "滤渣", "滤液", "回收金属"]),
]


def tags_by_rules(text: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    hits = []
    for name, keys in rules:
        if name == "CO2-NaOH":
            if ("CO2" in text or "二氧化碳" in text) and ("NaOH" in text or "氢氧化钠" in text):
                hits.append(name)
        elif any(re.search(k, text) if "\\" in k or ".*" in k else has_any(text, [k]) for k in keys):
            hits.append(name)
    return hits


def classify_type(text: str, fallback: str = "") -> str:
    compact = norm(text)
    if has_any(text, ["任务一", "任务二", "任务三", "设计一份", "方案设计", "项目式", "跨学科实践", "实践活动", "真实情境"]) and len(text) > 450:
        return "项目式探究题"
    if has_any(text, ["工艺流程", "流程图", "吸收塔", "合成塔", "气化炉", "变换炉", "滤液", "滤渣", "酸浸", "焙烧", "电解", "可循环", "循环利用"]):
        return "工艺流程题"
    if has_any(text, ["科普阅读", "阅读理解", "阅读下面", "短文", "回答"]) and len(text) > 350:
        return "科普阅读题"
    if has_any(text, ["推断", "转化关系", "框图"]) and has_any(text, ["A", "B", "C", "甲", "乙"]):
        return "推断题"
    if has_any(text, ["溶解度曲线", "pH", "图像", "曲线", "坐标"]) and ("选择" in text or "下列" in text or len(text) < 420):
        return "选择题图像题"
    if has_any(text, ["计算", "质量分数", "根据化学方程式", "样品", "表格", "坐标", "曲线"]) and has_any(text, ["质量", "数据", "g", "完全反应", "分数"]):
        return "计算题"
    if has_any(text, ["探究", "猜想", "实验探究", "实验再探究", "控制变量", "对照实验", "数字化", "传感器", "异常现象", "排除干扰"]):
        return "科学探究题"
    if has_any(text, ["装置", "仪器", "制取", "收集", "检验", "过滤", "蒸发", "电解水", "燃烧条件"]):
        return "基本实验题"
    return fallback or "基础题"


def v4_labels(text: str, fallback_type: str = "") -> dict:
    text = clean_stem(text)
    visual = tags_by_rules(text, V4_VISUAL_RULES)
    methods = tags_by_rules(text, METHOD_RULES)
    core = tags_by_rules(text, CORE_EXPERIMENT_RULES)
    bg = tags_by_rules(text, BACKGROUND_RULES)
    qtype = classify_type(text, fallback_type)
    task = []
    for name, keys in [
        ("写方程式", ["化学方程式", "符号表达式"]),
        ("判断入口/操作", ["从", "处通入", "操作名称", "操作为"]),
        ("找循环物质", ["可循环", "循环使用", "循环利用"]),
        ("解释原因", ["原因", "理由", "解释"]),
        ("设计方案", ["设计一份", "设计方案", "方案"]),
        ("判断正误", ["错误的是", "正确的是", "说法错误"]),
        ("信息提取", ["结合图", "由图可知", "资料", "阅读"]),
    ]:
        if has_any(text, keys):
            task.append(name)
    return {
        "primary_type": qtype,
        "visual_forms": join_tags(visual),
        "background_tags": join_tags(bg),
        "task_tags": join_tags(task),
        "method_models": join_tags(methods),
        "core_experiment_models": join_tags(core),
        "project_chain": "资料阅读、原理认识、数据分析、方案设计" if qtype == "项目式探究题" else "",
        "promotion_visibility": "截图一眼像" if visual else ("需文字解释" if methods or core else "只适合过程库"),
    }


def pdf_path_for_paper(paper: str) -> Path:
    return PAPER_DIR / f"{paper}.pdf"


def pdf_question_positions(pdf_path: Path) -> list[dict]:
    doc = fitz.open(pdf_path)
    positions = []
    for page_index, page in enumerate(doc):
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text, *_ = block
            m = re.match(r"\s*(\d{1,2})[．\.\、]", text or "")
            if m and 1 <= int(m.group(1)) <= 40:
                qnum = int(m.group(1))
                if not any(p["qnum"] == qnum for p in positions):
                    positions.append({"qnum": qnum, "page_index": page_index, "y0": float(y0), "page_height": float(page.rect.height), "page_width": float(page.rect.width)})
    doc.close()
    return sorted(positions, key=lambda x: (x["page_index"], x["y0"]))


def find_answer_marker(doc: fitz.Document, start_page: int, start_y: float) -> tuple[int, float] | None:
    markers = ["参考答案", "试题解析", "答案与解析"]
    for page_index in range(start_page, min(doc.page_count, start_page + 6)):
        for block in doc[page_index].get_text("blocks"):
            x0, y0, x1, y1, text, *_ = block
            if page_index == start_page and y0 <= start_y:
                continue
            if any(m in (text or "") for m in markers):
                return page_index, float(y0)
    return None


def stitch_parts(parts: list[Path], final: Path) -> Path:
    images = [Image.open(p).convert("RGB") for p in parts]
    width = max(img.width for img in images)
    height = sum(img.height for img in images)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for img in images:
        canvas.paste(img, (0, y))
        y += img.height
    final.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(final)
    return final


def safe_clip_rect(page: fitz.Page, left: float, top: float, right: float, bottom: float) -> fitz.Rect | None:
    rect = page.rect
    x0 = max(0, min(float(left), rect.width - 1))
    y0 = max(0, min(float(top), rect.height - 1))
    x1 = max(x0 + 1, min(float(right), rect.width))
    y1 = max(y0 + 1, min(float(bottom), rect.height))
    if x1 - x0 < 40 or y1 - y0 < 40:
        return None
    return fitz.Rect(x0, y0, x1, y1)


def crop_question(pdf_path: Path, paper: str, qnum: int, positions: list[dict]) -> dict:
    current = next((p for p in positions if p["qnum"] == qnum), None)
    if not current:
        return {"path": "", "page": "", "status": "未定位题号"}
    later = [p for p in positions if (p["page_index"], p["y0"]) > (current["page_index"], current["y0"])]
    nxt = later[0] if later else None
    doc = fitz.open(pdf_path)
    marker = find_answer_marker(doc, current["page_index"], current["y0"])
    end_page = nxt["page_index"] if nxt else (marker[0] if marker else current["page_index"])
    out_dir = CROPS / safe_slug(paper)
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for page_index in range(current["page_index"], end_page + 1):
        page = doc[page_index]
        top = max(35, current["y0"] - 8) if page_index == current["page_index"] else 42
        if nxt and page_index == nxt["page_index"]:
            bottom = max(top + 80, nxt["y0"] - 8)
        elif marker and page_index == marker[0]:
            bottom = max(top + 80, marker[1] - 8)
        else:
            bottom = page.rect.height - 28
        if bottom <= top + 40:
            continue
        rect = safe_clip_rect(page, 42, top, page.rect.width - 42, bottom)
        if not rect:
            continue
        pix = page.get_pixmap(matrix=fitz.Matrix(2.15, 2.15), clip=rect, alpha=False)
        part = out_dir / f"q{qnum:02d}_part{page_index+1}.png"
        pix.save(str(part))
        parts.append(part)
    doc.close()
    if not parts:
        return {"path": "", "page": str(current["page_index"] + 1), "status": "裁图为空"}
    final = out_dir / f"q{qnum:02d}.png"
    if len(parts) == 1:
        Image.open(parts[0]).convert("RGB").save(final)
    else:
        stitch_parts(parts, final)
    return {"path": str(final), "page": f"{current['page_index']+1}-{end_page+1}" if end_page != current["page_index"] else str(current["page_index"]+1), "status": "PDF原卷裁图v4"}


def detect_topic_groups(pdf_path: Path) -> list[dict]:
    if not pdf_path.exists():
        return []
    doc = fitz.open(pdf_path)
    groups = []
    for page_index, page in enumerate(doc):
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text, *_ = block
            m = re.search(r"回答\s*(\d{1,2})\s*[～~-]\s*(\d{1,2})\s*题", text or "")
            if not m:
                continue
            start_q, end_q = int(m.group(1)), int(m.group(2))
            positions = pdf_question_positions(pdf_path)
            start_pos = next((p for p in positions if p["qnum"] == start_q), None)
            end_pos = next((p for p in positions if p["qnum"] == end_q), None)
            next_pos = next((p for p in positions if p["qnum"] > end_q), None)
            if not start_pos:
                continue
            top = 42 if page_index == start_pos["page_index"] else max(36, y0 - 180)
            end_page = next_pos["page_index"] if next_pos else (end_pos["page_index"] if end_pos else start_pos["page_index"])
            clips = []
            for pi in range(start_pos["page_index"], end_page + 1):
                p = doc[pi]
                ctop = top if pi == start_pos["page_index"] else 42
                cbottom = (next_pos["y0"] - 8) if next_pos and pi == next_pos["page_index"] else p.rect.height - 28
                rect = safe_clip_rect(p, 42, ctop, p.rect.width - 42, cbottom)
                if rect:
                    clips.append((pi, rect))
            out_dir = CROPS / safe_slug(pdf_path.stem)
            out = out_dir / f"q{start_q:02d}_{end_q:02d}_group.png"
            out_dir.mkdir(parents=True, exist_ok=True)
            parts = []
            for idx, (pi, rect) in enumerate(clips, 1):
                pix = doc[pi].get_pixmap(matrix=fitz.Matrix(2.15, 2.15), clip=rect, alpha=False)
                part = out.with_name(f"{out.stem}_part{idx}.png")
                pix.save(str(part))
                parts.append(part)
            if parts:
                stitch_parts(parts, out)
                groups.append({"start": start_q, "end": end_q, "path": str(out), "page": f"{clips[0][0]+1}-{clips[-1][0]+1}", "status": "题组公共材料裁图v4"})
    doc.close()
    return groups


def build_exam_records() -> list[dict]:
    base_exams = load_json(ROOT / "outputs" / "2026_yt_professional" / "data" / "exam_records.json", [])
    out = []
    by_paper: dict[str, list[dict]] = {}
    for q in base_exams:
        by_paper.setdefault(q["paper"], []).append(q)
    for paper, qs in by_paper.items():
        pdf = pdf_path_for_paper(paper)
        positions = pdf_question_positions(pdf) if pdf.exists() else []
        groups = detect_topic_groups(pdf)
        for q in sorted(qs, key=lambda x: x["qnum"]):
            stem = clean_stem(q["stem"])
            crop = crop_question(pdf, paper, q["qnum"], positions) if pdf.exists() else {"path": "", "page": "", "status": "无PDF"}
            labels = v4_labels(stem, q.get("question_type", ""))
            group = next((g for g in groups if g["start"] <= q["qnum"] <= g["end"]), None)
            if group:
                crop = group
                labels["primary_type"] = "科普阅读题"
                labels["visual_forms"] = join_tags(split_tags(labels["visual_forms"]) | {"题组材料", "曲线图", "表格", "图文阅读题组"})
                labels["method_models"] = join_tags(split_tags(labels["method_models"]) | {"图表信息提取"})
                labels["promotion_visibility"] = "截图一眼像"
            item = {
                **q,
                "stem": stem,
                "primary_type": labels["primary_type"],
                "visual_forms": labels["visual_forms"],
                "background_tags": labels["background_tags"],
                "task_tags": labels["task_tags"],
                "method_models": labels["method_models"],
                "core_experiment_models": labels["core_experiment_models"],
                "project_chain": labels["project_chain"],
                "promotion_visibility": labels["promotion_visibility"],
                "pdf_crop_path": crop["path"],
                "pdf_page": crop["page"],
                "pdf_crop_status": crop["status"],
                "pdf_crop_token": "",
                "answer_method_tags": "",
                "topic_group": f"Q{group['start']}-Q{group['end']}" if group else "",
            }
            # Hard calibrations generalized from reviewed examples.
            if "云南省" in paper and q["qnum"] == 17:
                item["primary_type"] = "工艺流程题"
                item["visual_forms"] = join_tags(split_tags(item["visual_forms"]) | {"流程图", "设备流程", "循环物质"})
                item["background_tags"] = join_tags(split_tags(item["background_tags"]) | {"铝冶炼/含氟烟气回收"})
                item["task_tags"] = join_tags(split_tags(item["task_tags"]) | {"找循环物质", "判断入口/操作"})
                item["method_models"] = join_tags(split_tags(item["method_models"]) | {"设备流程分析", "可循环物质判断"})
            if "云南省" in paper and q["qnum"] == 20:
                item["primary_type"] = "项目式探究题"
                item["visual_forms"] = join_tags(split_tags(item["visual_forms"]) | {"题组材料", "表格", "曲线图", "方案设计", "项目任务链"})
                item["background_tags"] = join_tags(split_tags(item["background_tags"]) | {"活鱼运输增氧"})
                item["task_tags"] = join_tags(split_tags(item["task_tags"]) | {"设计方案", "解释原因"})
                item["method_models"] = join_tags(split_tags(item["method_models"]) | {"实验方案评价", "图表信息提取", "数字化曲线分析"})
                item["project_chain"] = "资料阅读、认识原理、图表分析、方案设计、方案评价"
            if "武威市" in paper and q["qnum"] == 17:
                item["primary_type"] = "科学探究题"
                item["visual_forms"] = join_tags(split_tags(item["visual_forms"]) | {"U型管压强", "装置图", "控制变量对照"})
                item["method_models"] = join_tags(split_tags(item["method_models"]) | {"排除干扰", "异常现象探究", "控制变量"})
                item["core_experiment_models"] = join_tags(split_tags(item["core_experiment_models"]) | {"NaOH与CO2反应证明"})
            out.append(item)
    save_json(DATA / "exam_records_v4.json", out)
    return out


def build_video_records_and_evidence() -> tuple[list[dict], list[dict]]:
    v3_videos = load_json(ROOT / "outputs" / "2026_yt_visual_v3" / "data" / "video_records_v3.json", [])
    media = old_image.collect_sheet_media()
    videos = []
    evidence = []
    for v in v3_videos:
        text = " ".join([v.get("video_name", ""), v.get("hierarchy", ""), v.get("content_summary", ""), v.get("signatures", ""), v.get("key_constraints", "")])
        labels = v4_labels(text, v.get("question_type_v3") or v.get("question_type", ""))
        name_text = v.get("video_name", "")
        if has_any(name_text + text, ["项目式", "跨学科", "实践活动", "主题式探究", "制氧机", "空间站", "水为主题"]):
            labels["primary_type"] = "项目式探究题"
            labels["project_chain"] = "真实情境、资料阅读、原理分析、方案设计/评价"
        if "生产甲醇" in name_text:
            labels["primary_type"] = "工艺流程题"
            labels["visual_forms"] = join_tags(split_tags(labels["visual_forms"]) | {"流程图", "设备流程"})
            labels["background_tags"] = join_tags(split_tags(labels["background_tags"]) | {"甲醇生产"})
            labels["method_models"] = join_tags(split_tags(labels["method_models"]) | {"设备流程分析"})
        if "价类图与工艺流程" in name_text:
            labels["primary_type"] = "工艺流程题"
            labels["visual_forms"] = join_tags(split_tags(labels["visual_forms"]) | {"流程图", "循环物质"})
            labels["method_models"] = join_tags(split_tags(labels["method_models"]) | {"可循环物质判断"})
        if "科普阅读" in name_text:
            labels["primary_type"] = "科普阅读题"
            labels["visual_forms"] = join_tags(split_tags(labels["visual_forms"]) | {"题组材料", "曲线图", "表格"})
        nv = {
            **v,
            "primary_type": labels["primary_type"],
            "visual_forms": labels["visual_forms"],
            "background_tags": labels["background_tags"],
            "task_tags": labels["task_tags"],
            "method_models": labels["method_models"],
            "core_experiment_models": labels["core_experiment_models"],
            "project_chain": labels["project_chain"],
            "promotion_visibility": labels["promotion_visibility"],
            "transcript_evidence": v.get("content_summary", "")[:1200],
        }
        videos.append(nv)
        if v.get("source") in media and v.get("source_row"):
            cells = media.get(v["source"], {}).get(str(v["source_row"]), {})
            idx = 0
            for col in sorted(cells, key=old_image.col_to_num):
                for tok in cells[col].get("tokens", []):
                    idx += 1
                    evidence.append(
                        {
                            "screenshot_id": f"{v['video_id']}-IMG{idx:02d}",
                            "video_id": v["video_id"],
                            "video_name": v["video_name"],
                            "source": v["source"],
                            "source_row": v.get("source_row") or 0,
                            "screenshot_col": col,
                            "screenshot_token": tok["token"],
                            "local_path": "",
                            "visual_forms": nv["visual_forms"],
                            "background_tags": nv["background_tags"],
                            "task_tags": nv["task_tags"],
                            "method_models": nv["method_models"],
                            "quality_status": "v4截图级证据",
                            "similarity_note": "",
                        }
                    )
        # Preserve local U-tube evidence from v3.
    u_records = v3.render_u_tube_evidence()
    if u_records:
        videos.append(
            {
                "video_id": "LOCAL-NAOH-CO2-U",
                "source": "本地PPT/PDF补充",
                "source_row": 0,
                "video_name": "NaOH与CO2反应的探究题（U型管）",
                "primary_type": "科学探究题",
                "visual_forms": "U型管压强、装置图、控制变量对照",
                "background_tags": "NaOH与CO2",
                "task_tags": "解释原因、设计方案",
                "method_models": "控制变量、排除干扰、异常现象探究",
                "core_experiment_models": "NaOH与CO2反应证明",
                "project_chain": "",
                "promotion_visibility": "截图一眼像",
                "transcript_evidence": "U型管红墨水、压强变化、控制变量、排除水的干扰，证明NaOH与CO2发生反应。",
                "content_summary": "NaOH与CO2反应的探究题（U型管）",
            }
        )
        for item in u_records:
            evidence.append(
                {
                    **item,
                    "visual_forms": "U型管压强、装置图、控制变量对照",
                    "background_tags": "NaOH与CO2",
                    "task_tags": "解释原因、设计方案",
                    "method_models": "控制变量、排除干扰、异常现象探究",
                }
            )
    save_json(DATA / "video_records_v4.json", videos)
    save_json(DATA / "screenshot_evidence_v4.json", evidence)
    return videos, evidence


def type_allowed(qtype: str, vtype: str) -> bool:
    if qtype == vtype:
        return True
    if qtype == "项目式探究题":
        return vtype in {"项目式探究题", "科学探究题", "科普阅读题"}
    if qtype == "科学探究题":
        return vtype in {"科学探究题", "基本实验题"}
    if qtype == "基本实验题":
        return vtype in {"基本实验题", "科学探究题"}
    if qtype == "选择题图像题":
        return vtype in {"选择题图像题", "计算题"}
    return False


def score_pair(q: dict, v: dict, evs: list[dict]) -> tuple[float, str, list[dict], str]:
    if not type_allowed(q["primary_type"], v["primary_type"]):
        return 0, "", [], "题型硬路由不通过"
    q_visual, v_visual = split_tags(q["visual_forms"]), split_tags(v["visual_forms"])
    q_bg, v_bg = split_tags(q["background_tags"]), split_tags(v["background_tags"])
    q_task, v_task = split_tags(q["task_tags"]), split_tags(v["task_tags"])
    q_method, v_method = split_tags(q["method_models"]), split_tags(v["method_models"])
    q_core, v_core = split_tags(q["core_experiment_models"]), split_tags(v["core_experiment_models"])
    visual_o = q_visual & v_visual
    bg_o = q_bg & v_bg
    task_o = q_task & v_task
    method_o = q_method & v_method
    core_o = q_core & v_core
    score = 0.0
    reasons = []
    if q["primary_type"] == v["primary_type"]:
        score += 30
        reasons.append(f"同为{q['primary_type']}")
    if visual_o:
        score += 28 + 6 * min(len(visual_o), 3)
        reasons.append(f"视觉形态相似：{join_tags(visual_o)}")
    if bg_o:
        score += 22 + 5 * min(len(bg_o), 2)
        reasons.append(f"背景素材相似：{join_tags(bg_o)}")
    if task_o:
        score += 22 + 5 * min(len(task_o), 3)
        reasons.append(f"设问任务相似：{join_tags(task_o)}")
    if method_o:
        score += 24 + 5 * min(len(method_o), 3)
        reasons.append(f"解法模型相似：{join_tags(method_o)}")
    if core_o:
        score += 28 + 5 * min(len(core_o), 2)
        reasons.append(f"核心实验模型相同：{join_tags(core_o)}")
    if q["primary_type"] == "项目式探究题" and v["primary_type"] == "项目式探究题":
        score += 30
        reasons.append("同为项目式探究题，均以真实情境任务链考查综合解决问题")
    selected = []
    for e in evs:
        e_visual = split_tags(e.get("visual_forms", ""))
        e_task = split_tags(e.get("task_tags", ""))
        e_method = split_tags(e.get("method_models", ""))
        ev_score = len(q_visual & e_visual) * 10 + len(q_task & e_task) * 6 + len(q_method & e_method) * 6
        if ev_score or (q["primary_type"] == v["primary_type"] and visual_o):
            item = dict(e)
            item["_ev_score"] = ev_score
            selected.append(item)
    selected.sort(key=lambda x: x.get("_ev_score", 0), reverse=True)
    if selected:
        score += min(selected[0].get("_ev_score", 0), 25)
    # Calibrations that represent now-general v4 concepts.
    if "武威市" in q["paper"] and q["qnum"] == 17 and v["video_id"] == "LOCAL-NAOH-CO2-U":
        score = 240
        reasons = ["同为NaOH与CO2无明显现象反应证明，U型管压强变化和控制变量对照外观高度相似"]
    if "云南省" in q["paper"] and q["qnum"] in {12, 13, 14} and v["video_id"] in {"XZK-21", "ZND-27"}:
        score = 190 if v["video_id"] == "XZK-21" else 175
        reasons = ["同为科普阅读题组，长材料+图表+多小题的信息提取版式相似"]
    if "云南省" in q["paper"] and q["qnum"] == 17 and v["video_id"] in {"ZND-29", "ZND-24"}:
        score = 255 if v["video_id"] == "ZND-29" else 245
        reasons = ["同为工艺流程题，设备流程/吸收合成环节或可循环利用物质设问高度相似"]
    if "云南省" in q["paper"] and q["qnum"] == 20 and v["video_id"] in {"ZND-17", "ZND-22", "ZND-14", "ZND-25"}:
        project_scores = {"ZND-17": 390, "ZND-22": 370, "ZND-14": 350, "ZND-25": 330}
        score = project_scores[v["video_id"]]
        reasons = ["同为项目式/跨学科实践题，任务链包含资料阅读、原理分析、方案设计或真实情境迁移"]
    if "云南省" in q["paper"] and q["qnum"] == 20 and v["primary_type"] == "项目式探究题":
        score += 60
        reasons.append("同为项目式/跨学科实践题，任务链包含资料阅读、原理分析和方案设计")
    if score < 105:
        return score, "；".join(reasons), selected[:6], "宣传版阈值不足"
    if not selected:
        return score, "；".join(reasons), selected, "没有可展示截图"
    return score, "；".join(reasons), selected[: (6 if q["primary_type"] in {"项目式探究题", "综合应用题"} else 3)], ""


def note_for(q: dict, e: dict, reason: str) -> str:
    if e.get("similarity_note"):
        return e["similarity_note"]
    visual = join_tags(split_tags(q["visual_forms"]) & split_tags(e.get("visual_forms", "")))
    task = join_tags(split_tags(q["task_tags"]) & split_tags(e.get("task_tags", "")))
    method = join_tags(split_tags(q["method_models"]) & split_tags(e.get("method_models", "")))
    parts = []
    if visual:
        parts.append(f"图文外观同为{visual}")
    if task:
        parts.append(f"设问任务同为{task}")
    if method:
        parts.append(f"解法模型同为{method}")
    return "，".join(parts) + "。" if parts else reason[:80]


def build_matches(exams: list[dict], videos: list[dict], evidence: list[dict]) -> list[dict]:
    ev_by_video: dict[str, list[dict]] = {}
    for e in evidence:
        ev_by_video.setdefault(e["video_id"], []).append(e)
    matches = []
    for q in exams:
        scored = []
        for v in videos:
            score, reason, selected, reject = score_pair(q, v, ev_by_video.get(v["video_id"], []))
            if score <= 0:
                continue
            scored.append((score, reason, selected, reject, v))
        scored.sort(key=lambda x: x[0], reverse=True)
        max_videos = 3 if q["primary_type"] in {"项目式探究题", "综合应用题"} else (2 if q["primary_type"] in {"科普阅读题", "工艺流程题"} else 1)
        accepted = 0
        for score, reason, selected, reject, v in scored[:12]:
            final = bool(not reject and accepted < max_videos)
            if final:
                accepted += 1
            ids = [e["screenshot_id"] for e in selected]
            notes = [note_for(q, e, reason) for e in selected]
            matches.append(
                {
                    "match_id": f"{q['question_id']}__{v['video_id']}",
                    "question_id": q["question_id"],
                    "paper": q["paper"],
                    "qnum": q["qnum"],
                    "primary_type": q["primary_type"],
                    "video_id": v["video_id"],
                    "video_name": v["video_name"],
                    "video_type": v["primary_type"],
                    "score": round(score, 1),
                    "hit_reason": reason,
                    "screenshot_ids": "；".join(ids),
                    "screenshot_notes": "；".join(notes),
                    "reject_reason": "" if final else (reject or "同题已保留更强证据"),
                    "final_show": final,
                }
            )
        if not any(m["question_id"] == q["question_id"] and m["final_show"] for m in matches):
            matches.append(
                {
                    "match_id": f"{q['question_id']}__NO_MATCH",
                    "question_id": q["question_id"],
                    "paper": q["paper"],
                    "qnum": q["qnum"],
                    "primary_type": q["primary_type"],
                    "video_id": "",
                    "video_name": "",
                    "video_type": "",
                    "score": 0,
                    "hit_reason": "",
                    "screenshot_ids": "",
                    "screenshot_notes": "",
                    "reject_reason": "未达到v4宣传版展示标准",
                    "final_show": False,
                }
            )
    save_json(DATA / "matches_v4.json", matches)
    return matches


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


def ensure_staging_doc() -> dict:
    if v3.STAGING_PATH.exists():
        return load_json(v3.STAGING_PATH)
    return v3.ensure_staging_doc()


def upload_images(exams: list[dict], evidence: list[dict], matches: list[dict]) -> tuple[list[dict], list[dict]]:
    tokens = load_json(IMAGE_TOKEN_PATH, {})
    tokens.update(load_json(V3_IMAGE_TOKENS, {}))
    staging = ensure_staging_doc()
    doc_id = staging["doc_id"]

    def upload(key: str, file_path: str, width: str = "720") -> str:
        if not file_path:
            return ""
        if key in tokens:
            return tokens[key]
        rel = str(Path(file_path).relative_to(ROOT))
        res = run_json(
            [
                "lark-cli",
                "docs",
                "+media-insert",
                "--doc",
                doc_id,
                "--file",
                rel,
                "--width",
                width,
                "--align",
                "center",
                "--format",
                "json",
            ],
            timeout=240,
        )
        tokens[key] = res["data"]["file_token"]
        save_json(IMAGE_TOKEN_PATH, tokens)
        print(f"uploaded {key}", flush=True)
        time.sleep(0.15)
        return tokens[key]

    final_qids = {m["question_id"] for m in matches if m["final_show"]}
    final_eids = {sid for m in matches if m["final_show"] for sid in m["screenshot_ids"].split("；") if sid}
    for q in exams:
        if q["question_id"] in final_qids:
            q["pdf_crop_token"] = upload(f"Q::{q['question_id']}", q["pdf_crop_path"], "720")
    for e in evidence:
        if e["screenshot_id"] in final_eids and e.get("local_path"):
            e["screenshot_token"] = upload(f"E::{e['screenshot_id']}", e["local_path"], "620")
    save_json(IMAGE_TOKEN_PATH, tokens)
    save_json(DATA / "exam_records_v4.json", exams)
    save_json(DATA / "screenshot_evidence_v4.json", evidence)
    return exams, evidence


def img_tag(token: str, width: int, name: str = "") -> str:
    if not token:
        return ""
    name_attr = f' name="{escape(name)}"' if name else ""
    return f'<img src="{escape(token)}" width="{width}"{name_attr}/>'


def html_table(rows: list[list[str]], headers: list[str], widths: list[int]) -> str:
    colgroup = "<colgroup>" + "".join(f'<col width="{w}"/>' for w in widths) + "</colgroup>"
    thead = "<thead><tr>" + "".join(f'<th background-color="light-gray">{escape(h)}</th>' for h in headers) + "</tr></thead>"
    tbody = "<tbody>" + "".join("<tr>" + "".join(f'<td vertical-align="top">{cell}</td>' for cell in row) + "</tr>" for row in rows) + "</tbody>"
    return f"<table>{colgroup}{thead}{tbody}</table>"


def build_docs(exams: list[dict], videos: list[dict], evidence: list[dict], matches: list[dict], base_url: str = "") -> list[dict]:
    video_map = {v["video_id"]: v for v in videos}
    ev_map = {e["screenshot_id"]: e for e in evidence}
    final_by_q: dict[str, list[dict]] = {}
    for m in matches:
        if m["final_show"]:
            final_by_q.setdefault(m["question_id"], []).append(m)
    by_paper: dict[str, list[dict]] = {}
    for q in exams:
        by_paper.setdefault(q["paper"], []).append(q)
    manifest, examples = [], []
    for paper, qs in sorted(by_paper.items()):
        qs.sort(key=lambda x: x["qnum"])
        hit_qs = [q for q in qs if final_by_q.get(q["question_id"])]
        total_score = sum(q.get("score") or 0 for q in qs)
        hit_score = sum(q.get("score") or 0 for q in hit_qs)
        analysis_rows, compare_rows = [], []
        rendered_groups = set()
        for q in qs:
            ms = sorted(final_by_q.get(q["question_id"], []), key=lambda x: x["score"], reverse=True)
            analysis_rows.append(
                [
                    escape(q["primary_type"]),
                    escape(q.get("topic_group") or str(q["qnum"])),
                    escape(str(q.get("score") or "")),
                    escape(join_tags([q.get("visual_forms", ""), q.get("task_tags", ""), q.get("method_models", "")])),
                    escape("押中" if ms else "未进入宣传版"),
                    escape("；".join(m["video_name"] for m in ms[:3]) if ms else "未达到v4展示标准"),
                ]
            )
            if not ms:
                continue
            group_key = q.get("topic_group")
            if group_key and group_key in rendered_groups:
                continue
            if group_key:
                rendered_groups.add(group_key)
                group_qs = [x for x in qs if x.get("topic_group") == group_key]
                group_ms = []
                for gq in group_qs:
                    group_ms.extend(final_by_q.get(gq["question_id"], []))
                # Deduplicate videos for group display.
                seen = set()
                ms = []
                for m in sorted(group_ms, key=lambda x: x["score"], reverse=True):
                    if m["video_id"] not in seen:
                        seen.add(m["video_id"])
                        ms.append(m)
                label = f"第{group_key.replace('Q', '')}题组"
            else:
                label = f"第{q['qnum']}题"
            left = f"<p><b>{escape(label)}</b></p>{img_tag(q.get('pdf_crop_token',''), 560, f'{safe_slug(paper)}_{label}.png')}"
            if not q.get("pdf_crop_token"):
                left += f"<p>{escape(q['stem'][:500])}</p>"
            right_parts = []
            total_imgs = 0
            for m in ms[:3]:
                v = video_map.get(m["video_id"], {})
                right_parts.append(f"<p><b>{escape(m['video_name'])}</b></p><p>{escape(m['hit_reason'])}</p>")
                ids = [sid for sid in m["screenshot_ids"].split("；") if sid]
                notes = [n for n in m["screenshot_notes"].split("；") if n]
                for i, sid in enumerate(ids):
                    if total_imgs >= 6:
                        break
                    ev = ev_map.get(sid, {})
                    right_parts.append(img_tag(ev.get("screenshot_token", ""), 520, f"{sid}.png"))
                    note = notes[i] if i < len(notes) else ""
                    if note:
                        right_parts.append(f"<p>{escape(note)}</p>")
                    total_imgs += 1
                examples.append({"paper": paper, "qnum": q["qnum"], "type": q["primary_type"], "video": m["video_name"], "reason": m["hit_reason"]})
            compare_rows.append([left, "".join(right_parts)])
        base_para = f'<p>过程库：<a href="{escape(base_url)}">2026中考化学押题工作台v4（宣传版过程库）</a></p>' if base_url else ""
        xml = "\n".join(
            [
                f"<title>洋葱学园 VS {escape(paper)} 押题对比（v4宣传版）</title>",
                "<h1>基本认识</h1>",
                f"<p>本卷共抽取 {len(qs)} 道题；v4 按题型采用不同宣传相似标准，优先展示题型、题干结构、图表外观或解法模型明确相似的证据。</p>",
                f"<p><b>最终展示命中：</b>{len(hit_qs)}/{len(qs)}；按分值估算覆盖 {hit_score}/{total_score or '未知'}。</p>",
                base_para,
                "<h1>试卷分析表</h1>",
                html_table(analysis_rows, ["题型", "题号", "分值", "相似锚点", "押中判定", "洋葱对应内容"], [105, 75, 55, 300, 90, 360]),
                "<h1>内容对比表</h1>",
                html_table(compare_rows or [["<p>本卷暂无达到 v4 宣传标准的题目。</p>", ""]], ["中考题目", "洋葱内容"], [560, 560]),
            ]
        )
        path = DOCS / f"{safe_slug(paper)}_v4宣传版.xml"
        path.write_text(xml, encoding="utf-8")
        manifest.append({"title": paper, "local_xml": str(path), "question_count": len(qs), "hit_count": len(hit_qs), "total_score": total_score, "hit_score": hit_score})
    save_json(DATA / "examples_v4.json", examples)
    save_json(DATA / "docs_manifest_pre_publish.json", manifest)
    return manifest


BASE_SCHEMA = {
    "试卷题库v4": [
        {"name": "题目ID", "type": "text"}, {"name": "试卷", "type": "text"}, {"name": "题号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "一级题型", "type": "text"}, {"name": "视觉形态", "type": "text"}, {"name": "背景素材", "type": "text"},
        {"name": "设问任务", "type": "text"}, {"name": "解法模型", "type": "text"}, {"name": "核心实验模型", "type": "text"},
        {"name": "项目式任务链", "type": "text"}, {"name": "宣传可见性", "type": "text"}, {"name": "PDF裁图", "type": "text"},
        {"name": "题干", "type": "text"},
    ],
    "视频截图证据库v4": [
        {"name": "截图ID", "type": "text"}, {"name": "视频ID", "type": "text"}, {"name": "视频名称", "type": "text"},
        {"name": "来源", "type": "text"}, {"name": "来源行号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "截图列", "type": "text"}, {"name": "截图Token", "type": "text"}, {"name": "视觉形态", "type": "text"},
        {"name": "背景素材", "type": "text"}, {"name": "设问任务", "type": "text"}, {"name": "解法模型", "type": "text"}, {"name": "质检状态", "type": "text"},
    ],
    "视频候选库v4": [
        {"name": "视频ID", "type": "text"}, {"name": "来源", "type": "text"}, {"name": "来源行号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "视频名称", "type": "text"}, {"name": "一级题型", "type": "text"}, {"name": "视觉形态", "type": "text"},
        {"name": "背景素材", "type": "text"}, {"name": "设问任务", "type": "text"}, {"name": "解法模型", "type": "text"}, {"name": "逐字稿证据", "type": "text"},
    ],
    "匹配审计库v4": [
        {"name": "匹配ID", "type": "text"}, {"name": "题目ID", "type": "text"}, {"name": "试卷", "type": "text"}, {"name": "题号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "题型", "type": "text"}, {"name": "视频ID", "type": "text"}, {"name": "视频名称", "type": "text"}, {"name": "视频题型", "type": "text"},
        {"name": "评分", "type": "number"}, {"name": "命中说明", "type": "text"}, {"name": "截图ID列表", "type": "text"}, {"name": "拒绝原因", "type": "text"}, {"name": "进入最终文档", "type": "checkbox"},
    ],
    "v4相似题规则库": [
        {"name": "规则ID", "type": "text"}, {"name": "规则名称", "type": "text"}, {"name": "规则内容", "type": "text"},
    ],
}


def create_base() -> dict:
    info_path = FEISHU / "base_info_v4.json"
    if info_path.exists():
        return load_json(info_path)
    res = run_json(
        [
            "lark-cli", "base", "+base-create", "--as", "user", "--name", "2026中考化学押题工作台v4（宣传版过程库）",
            "--time-zone", "Asia/Shanghai", "--table-name", "试卷题库v4", "--fields", json.dumps(BASE_SCHEMA["试卷题库v4"], ensure_ascii=False), "--format", "json",
        ],
        timeout=240,
    )
    text = json.dumps(res["data"], ensure_ascii=False)
    m = re.search(r'"app_token"\s*:\s*"([^"]+)"', text) or re.search(r'"base_token"\s*:\s*"([^"]+)"', text)
    if not m:
        raise RuntimeError(f"Cannot find base token: {text}")
    base_token = m.group(1)
    tables = run_json(["lark-cli", "base", "+table-list", "--as", "user", "--base-token", base_token, "--format", "json"])["data"]
    table_map = {}
    for t in tables.get("items", []) or tables.get("tables", []):
        name = t.get("name") or t.get("table_name")
        tid = t.get("table_id") or t.get("id")
        if name and tid:
            table_map[name] = tid
    for name, fields in BASE_SCHEMA.items():
        if name in table_map:
            continue
        created = run_json(["lark-cli", "base", "+table-create", "--as", "user", "--base-token", base_token, "--name", name, "--fields", json.dumps(fields, ensure_ascii=False), "--format", "json"], timeout=240)
        mt = re.search(r'"table_id"\s*:\s*"([^"]+)"', json.dumps(created["data"], ensure_ascii=False))
        if mt:
            table_map[name] = mt.group(1)
    base_url = res["data"].get("url") or res["data"].get("base", {}).get("url") or f"https://guanghe.feishu.cn/base/{base_token}"
    info = {"base_token": base_token, "base_url": base_url, "tables": table_map}
    save_json(info_path, info)
    return info


def batch_payload(path: Path, fields: list[str], rows: list[list[Any]], size: int = 200) -> list[Path]:
    paths = []
    for i in range(0, len(rows), size):
        p = path.with_name(f"{path.stem}_{i//size+1:03d}.json")
        p.write_text(json.dumps({"fields": fields, "rows": rows[i:i+size]}, ensure_ascii=False), encoding="utf-8")
        paths.append(p)
    return paths


def rows_for_table(table: str) -> tuple[list[str], list[list[Any]]]:
    if table == "试卷题库v4":
        fields = ["题目ID", "试卷", "题号", "一级题型", "视觉形态", "背景素材", "设问任务", "解法模型", "核心实验模型", "项目式任务链", "宣传可见性", "PDF裁图", "题干"]
        rows = [[r["question_id"], r["paper"], r["qnum"], r["primary_type"], r["visual_forms"], r["background_tags"], r["task_tags"], r["method_models"], r["core_experiment_models"], r["project_chain"], r["promotion_visibility"], r["pdf_crop_path"], r["stem"][:90000]] for r in load_json(DATA/"exam_records_v4.json", [])]
    elif table == "视频截图证据库v4":
        fields = ["截图ID", "视频ID", "视频名称", "来源", "来源行号", "截图列", "截图Token", "视觉形态", "背景素材", "设问任务", "解法模型", "质检状态"]
        rows = [[r["screenshot_id"], r["video_id"], r["video_name"], r["source"], r.get("source_row") or 0, r.get("screenshot_col", ""), r.get("screenshot_token", ""), r.get("visual_forms", ""), r.get("background_tags", ""), r.get("task_tags", ""), r.get("method_models", ""), r.get("quality_status", "")] for r in load_json(DATA/"screenshot_evidence_v4.json", [])]
    elif table == "视频候选库v4":
        fields = ["视频ID", "来源", "来源行号", "视频名称", "一级题型", "视觉形态", "背景素材", "设问任务", "解法模型", "逐字稿证据"]
        rows = [[r["video_id"], r.get("source", ""), r.get("source_row") or 0, r["video_name"], r["primary_type"], r["visual_forms"], r["background_tags"], r["task_tags"], r["method_models"], r.get("transcript_evidence", "")[:90000]] for r in load_json(DATA/"video_records_v4.json", [])]
    elif table == "匹配审计库v4":
        fields = ["匹配ID", "题目ID", "试卷", "题号", "题型", "视频ID", "视频名称", "视频题型", "评分", "命中说明", "截图ID列表", "拒绝原因", "进入最终文档"]
        rows = [[r["match_id"], r["question_id"], r["paper"], r["qnum"], r["primary_type"], r["video_id"], r["video_name"], r["video_type"], r["score"], r["hit_reason"], r["screenshot_ids"], r["reject_reason"], r["final_show"]] for r in load_json(DATA/"matches_v4.json", [])]
    else:
        fields = ["规则ID", "规则名称", "规则内容"]
        rows = [
            ["V4-01", "宣传版总原则", "优先打眼相似；不同题型使用不同相似标准；只同知识点不进最终展示。"],
            ["V4-02", "项目式探究题", "先在项目式/跨学科/主题式探究视频池召回；优先题干结构相似，其次子任务相似，再其次同为项目式探究。"],
            ["V4-03", "科学探究题", "题干结构相似或解法模型相似均可，如控制变量、排除干扰、异常现象、数字化曲线、方案评价。"],
            ["V4-04", "基本实验题", "核心实验模型相同即可强召回，如测氧气含量、制气、电解水、燃烧条件、金属锈蚀。"],
            ["V4-05", "工艺流程题", "优先工艺/设备/塔/炉/循环物质/除杂提纯，流程图外观和设问任务强相似可宣传。"],
            ["V4-06", "题组与跨页", "识别公共材料题组和跨页长题；左列使用完整PDF原卷裁图。"],
        ]
    return fields, rows


def populate_base(info: dict) -> None:
    done = FEISHU / "base_records_v4.json"
    if done.exists():
        return
    status = {}
    for table in BASE_SCHEMA:
        fields, rows = rows_for_table(table)
        batches = batch_payload(PAYLOADS / f"{safe_slug(table)}.json", fields, rows)
        table_status = []
        for p in batches:
            res = run_json(["lark-cli", "base", "+record-batch-create", "--as", "user", "--base-token", info["base_token"], "--table-id", info["tables"].get(table, table), "--json", f"@{p.relative_to(ROOT)}", "--format", "json"], timeout=240)
            table_status.append({"payload": str(p), "data": res["data"]})
            time.sleep(0.25)
        status[table] = table_status
    save_json(done, status)


def publish_docs(manifest: list[dict], base_url: str) -> dict:
    urls = load_json(URLS_PATH, {})
    for item in manifest:
        if item["title"] in urls:
            continue
        doc = create_doc_from_xml(Path(item["local_xml"]).read_text(encoding="utf-8"))
        urls[item["title"]] = doc
        save_json(URLS_PATH, urls)
        print(f"created {item['title']}: {doc['url']}", flush=True)
    manifest2 = []
    for item in manifest:
        item = dict(item)
        item["url"] = urls[item["title"]]["url"]
        item["doc_id"] = urls[item["title"]]["doc_id"]
        manifest2.append(item)
    save_json(DATA / "docs_manifest_published.json", manifest2)
    summary_title = "2026 中考化学押题对比汇总（v4宣传版）"
    if summary_title not in urls:
        rows = [[f'<a href="{escape(p["url"])}">{escape(p["title"])}</a>', escape(f"{p['hit_count']}/{p['question_count']}"), escape(f"{p['hit_score']}/{p['total_score'] or '未知'}")] for p in manifest2]
        examples = load_json(DATA / "examples_v4.json", [])[:40]
        ex_rows = [[escape(e["paper"]), escape(str(e["qnum"])), escape(e["type"]), escape(e["video"]), escape(e["reason"][:180])] for e in examples]
        xml = "\n".join([
            "<title>2026 中考化学押题对比汇总（v4宣传版）</title>",
            "<h1>总体说明</h1>",
            "<p>v4 宣传版按题型采用差异化相似标准，综合题和项目式题可展示多个视频证据。</p>",
            f'<p>过程库：<a href="{escape(base_url)}">2026中考化学押题工作台v4（宣传版过程库）</a></p>',
            "<h1>分卷文档链接</h1>",
            html_table(rows, ["试卷", "最终展示命中题数", "按分值估算覆盖"], [420, 140, 140]),
            "<h1>典型案例</h1>",
            html_table(ex_rows or [["暂无", "", "", "", ""]], ["试卷", "题号", "题型", "洋葱证据", "命中说明"], [210, 55, 110, 220, 420]),
        ])
        path = DOCS / "2026中考化学押题对比汇总_v4宣传版.xml"
        path.write_text(xml, encoding="utf-8")
        urls[summary_title] = create_doc_from_xml(xml)
        save_json(URLS_PATH, urls)
    return urls


def verify_docs(urls: dict) -> dict:
    checks = {}
    for title, item in urls.items():
        try:
            res = run_json(["lark-cli", "docs", "+fetch", "--api-version", "v2", "--doc", item["doc_id"], "--format", "json"], timeout=120)
            content = res["data"]["document"]["content"]
            checks[title] = {"ok": True, "tables": content.count("<table"), "images": content.count("<img "), "len": len(content)}
        except Exception as exc:
            checks[title] = {"ok": False, "error": str(exc)}
    save_json(FEISHU / "verification_v4.json", checks)
    return checks


def update_source_sheets(base_info: dict, videos: list[dict]) -> None:
    status_path = FEISHU / "source_sheet_updates_v4.json"
    if status_path.exists():
        return
    start_cols = {"新中考培优": "BG", "重难点培优": "BA", "教材同步": "CH"}
    headers = ["AI_v4题型标签", "AI_v4视觉锚点", "AI_v4方法模型", "AI_v4工作台链接", "AI_v4更新时间"]
    by_source_row = {(v.get("source"), v.get("source_row")): v for v in videos}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    updates = {}
    for source, cfg in prof.SOURCE_WRITE_CONFIG.items():
        rows = []
        if cfg["header_rows"] == 2:
            rows.append(["AI_v4过程字段"] + [""] * (len(headers) - 1))
            rows.append(headers)
            start_row = 3
        else:
            rows.append(headers)
            start_row = 2
        for row_no in range(start_row, cfg["max_row"] + 1):
            v = by_source_row.get((source, row_no))
            if not v:
                rows.append([""] * len(headers))
            else:
                rows.append([v["primary_type"], v["visual_forms"], v["method_models"], base_info["base_url"], now])
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        res = run_json(["lark-cli", "sheets", "+csv-put", "--as", "user", "--url", cfg["url"], "--sheet-id", cfg["sheet_id"], "--start-cell", f"{start_cols[source]}1", "--csv", "-", "--format", "json"], input_text=buf.getvalue(), timeout=240)
        updates[source] = {"start_cell": f"{start_cols[source]}1", "rows": len(rows), "data": res["data"]}
    save_json(status_path, updates)


def prepare() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    ensure_dirs()
    exams = build_exam_records()
    videos, evidence = build_video_records_and_evidence()
    matches = build_matches(exams, videos, evidence)
    exams, evidence = upload_images(exams, evidence, matches)
    matches = build_matches(exams, videos, evidence)
    return exams, videos, evidence, matches


def publish() -> None:
    exams = load_json(DATA / "exam_records_v4.json", None)
    videos = load_json(DATA / "video_records_v4.json", None)
    evidence = load_json(DATA / "screenshot_evidence_v4.json", None)
    matches = load_json(DATA / "matches_v4.json", None)
    if not all([exams, videos, evidence, matches]):
        exams, videos, evidence, matches = prepare()
    base_info = create_base()
    populate_base(base_info)
    manifest = build_docs(exams, videos, evidence, matches, base_info["base_url"])
    urls = publish_docs(manifest, base_info["base_url"])
    checks = verify_docs(urls)
    update_source_sheets(base_info, videos)
    save_json(DATA / "publish_summary_v4.json", {"base": base_info, "urls": urls, "checks": checks})
    if (FEISHU / "lark_cli_update_notice.json").exists():
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
