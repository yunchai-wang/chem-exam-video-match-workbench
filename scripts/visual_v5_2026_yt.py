#!/usr/bin/env python3
"""v5 professional visual-promotion matching pipeline for 2026 chemistry papers."""

from __future__ import annotations

import argparse
import csv
import difflib
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
WORK = ROOT / "outputs" / "2026_yt_visual_v5"
DATA = WORK / "data"
DOCS = WORK / "docs"
FEISHU = WORK / "feishu"
PAYLOADS = WORK / "payloads"
CROPS = ROOT / "outputs" / "2026_yt_visual_v5" / "pdf_question_crops"  # reuse v5 crops
PAPER_DIR = ROOT / "试卷" / "2026中考卷"
IMAGE_TOKEN_PATH = FEISHU / "image_tokens_v5.json"
URLS_PATH = FEISHU / "doc_urls_v5.json"

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
    ("流程图", ["流程图", "工艺流程", "滤液", "滤渣", "废液", "废渣", "酸浸", "焙烧", "制备", "回收", "提纯"]),
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

TASK_MODEL_RULES: list[tuple[str, list[str]]] = [
    ("共存判断", ["大量共存", "能否共存", "不能共存", "可共存", "能共存", "离子共存", "物质共存"]),
    ("除杂判断", ["除杂", "除去杂质", "不引入新杂质", "提纯", "净化"]),
    ("鉴别判断", ["鉴别", "区分", "检验", "鉴定", "分别加入"]),
    ("转化判断", ["一步转化", "转化", "能否反应", "能否实现", "物质间反应"]),
    ("排序判断", ["排序", "由大到小", "由小到大", "活动性", "先后顺序", "反应先后"]),
    ("图像匹配", ["图像", "曲线", "坐标", "纵坐标", "横坐标", "变化趋势"]),
    ("方案评价", ["设计方案", "评价方案", "方案可行", "方案不可行", "缺陷", "改进方案"]),
    ("循环物质判断", ["可循环", "循环利用", "循环使用", "返回", "再利用"]),
    ("入口/出口/步骤作用判断", ["处通入", "入口", "出口", "步骤作用", "提高吸收效率"]),
]

CHOICE_ALLOWED_TASKS = {"共存判断", "图像匹配", "循环物质判断", "入口/出口/步骤作用判断"}

CORE_EXPERIMENT_RULES: list[tuple[str, list[str]]] = [
    ("测定空气中氧气含量", ["氧气含量", "红磷", "白磷", "测定空气"]),
    ("实验室制取气体", ["制取氧气", "制取二氧化碳", "发生装置", "收集装置", "检验二氧化碳"]),
    ("电解水", ["电解水"]),
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


def co2_naoh_context(text: str) -> bool:
    return has_any(text, ["二氧化碳", "CO2"]) and has_any(text, ["氢氧化钠", "NaOH"])


def electrolysis_water_context(text: str) -> bool:
    if has_any(text, ["锂电池", "锂离子电池", "固态锂电池", "负极材料", "电解质", "比容量", "能量密度"]):
        return False
    return has_any(text, ["电解水", "电解 水", "水的电解", "电解  水"]) or (
        has_any(text, ["电解", "通电"]) and has_any(text, ["水", "H2O", "电解水"])
    )


def is_simple_mcq_stem(stem: str) -> bool:
    compact = norm(stem)
    return bool(re.search(r"[（(]\s*[ABCDＡＢＣＤ]", stem or "")) and len(compact) < 320 and "任务一" not in compact


def load_paper_tag_overrides() -> dict[str, dict]:
    root = DATA / "paper_tag_overrides"
    out = {}
    if not root.exists():
        return out
    for path in root.glob("*_v5.json"):
        cfg = load_json(path, {})
        paper = cfg.get("paper")
        if paper:
            out[paper] = cfg
    return out


PAPER_TAG_OVERRIDES = {}


def apply_paper_tag_override(item: dict, cfg: dict) -> None:
    for key in (
        "primary_type",
        "knowledge_tags",
        "problem_tags",
        "visual_forms",
        "background_tags",
        "task_tags",
        "task_models",
        "method_models",
        "core_experiment_models",
        "project_chain",
        "comparison_show",
        "comparison_video_ids",
        "compare_table_show",
        "compare_table_video_ids",
        "task_video_map",
        "video_similarity_notes",
        "feishu_manual_right",
    ):
        if key in cfg and cfg[key] is not None:
            item[key] = cfg[key]
    item["promotion_policy"] = cfg.get("promotion", "auto")
    item["allowed_video_ids"] = cfg.get("allowed_video_ids", [])
    item["audit_note"] = cfg.get("audit_note", "")
    if cfg.get("promotion") == "exclude":
        item["promotion_visibility"] = "人工审计排除"
    elif cfg.get("promotion") == "allow":
        item["promotion_visibility"] = "人工审计白名单"


GENERIC_META_VIDEO_IDS = {"XZK-36", "XZK-22", "ZND-29"}


def video_display_order(q: dict) -> list[str]:
    order: list[str] = []
    for block in q.get("task_video_map") or []:
        for vid in block.get("video_ids") or []:
            if vid not in order:
                order.append(vid)
    for vid in q.get("allowed_video_ids") or []:
        if vid not in order:
            order.append(vid)
    for vid in q.get("comparison_video_ids") or []:
        if vid not in order:
            order.append(vid)
    return order


def sort_matches_for_question(q: dict, ms: list[dict]) -> list[dict]:
    order = video_display_order(q)
    generic_last = {vid: idx for idx, vid in enumerate(order) if vid in GENERIC_META_VIDEO_IDS}

    def sort_key(m: dict) -> tuple:
        vid = m.get("video_id", "")
        if vid in order:
            base = order.index(vid)
        else:
            base = 900 + (generic_last.get(vid, 50))
        return (base, -m.get("isomorph_rank", 0), -m.get("score", 0))

    return sorted(ms, key=sort_key)


def tags_by_rules(text: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    hits = []
    for name, keys in rules:
        if name in {"CO2-NaOH", "NaOH与CO2", "NaOH与CO2反应证明"}:
            if co2_naoh_context(text):
                hits.append(name)
        elif name == "电解水":
            if electrolysis_water_context(text):
                hits.append(name)
        elif any(re.search(k, text) if "\\" in k or ".*" in k else has_any(text, [k]) for k in keys):
            hits.append(name)
    return hits


def has_real_visual_prompt(text: str) -> bool:
    return has_any(text, ["如图", "图1", "图2", "图3", "图示", "流程图", "装置", "曲线", "坐标", "表格", "表中", "由图", "结合图", "数据如表"])


def process_context(text: str) -> bool:
    return has_any(
        text,
        [
            "工艺流程", "流程图", "滤液", "滤渣", "废液", "废渣", "酸浸", "碱浸", "焙烧", "灼烧",
            "制备", "提纯", "吸收塔", "合成塔", "反应塔", "气化炉", "变换炉", "设备1", "设备2", "反应器",
            "母液", "产物", "副产物", "返回", "循环使用的物质", "循环利用的物质",
        ],
    )


def life_common_context(text: str) -> bool:
    return has_any(
        text,
        [
            "绿色发展", "垃圾分类", "可回收物", "生态环境", "人与自然", "工业污水", "露天焚烧", "农药化肥",
            "保护水资源", "生活垃圾", "一次性塑料", "环保", "低碳", "节能", "食品", "营养", "安全标识",
        ],
    )


def false_trigger_keywords(text: str) -> str:
    keys = []
    for key in ["循环利用", "可回收物", "绿色发展", "正确的是", "错误的是", "符合这一理念"]:
        if has_any(text, [key]):
            keys.append(key)
    return join_tags(keys)


def is_basic_common_choice(item: dict) -> bool:
    stem = item.get("stem", "")
    whitelist = split_tags(item.get("task_models", "")) & CHOICE_ALLOWED_TASKS
    return bool(
        "选择" in str(item.get("raw_qtype", ""))
        and (item.get("score") or 0) <= 3
        and not item.get("has_visual")
        and not item.get("topic_group")
        and len(stem) < 320
        and not has_real_visual_prompt(stem)
        and (life_common_context(stem) or item.get("difficulty", 0) <= 3)
        and not whitelist
    )


def is_choice_question(item: dict) -> bool:
    return "选择" in str(item.get("raw_qtype", "")) and (item.get("score") or 0) <= 4


def choice_allowed_reason(item: dict) -> str:
    if not is_choice_question(item):
        return "非选择题"
    visual = split_tags(item.get("visual_forms", ""))
    task_models = split_tags(item.get("task_models", ""))
    methods = split_tags(item.get("method_models", ""))
    stem = item.get("stem", "")
    if item.get("topic_group") and item.get("primary_type") == "科普阅读题":
        return "科普材料题组"
    if item.get("primary_type") == "科普阅读题" and has_any(stem, ["阅读下列材料", "阅读下面", "阅读材料", "科普短文", "短文"]):
        return "科普材料题组"
    if "共存判断" in task_models:
        return "离子/物质共存选择题"
    if process_context(stem) and (visual & {"流程图", "设备流程"}):
        return "含流程图选择题"
    if visual & {"曲线图", "柱状图", "数字化曲线"} or has_any(stem, ["坐标图", "坐标曲线", "变化曲线", "纵坐标", "横坐标", "随时间变化"]):
        return "坐标图像选择题"
    has_explicit_table = has_any(stem, ["表格", "表中", "下表", "如下表", "数据如表", "记录表", "表所示"])
    if has_explicit_table and "表格" in visual and ("守恒/方程式计算" in methods or has_any(stem, ["计算", "质量守恒", "质量分数", "完全反应"])):
        return "表格计算选择题"
    return ""


def visual_evidence_source(item: dict) -> str:
    if item.get("has_visual") or item.get("topic_group"):
        return "PDF题图"
    if has_real_visual_prompt(item.get("stem", "")):
        return "题干明确图表词"
    return "无"


def classify_type(text: str, fallback: str = "") -> str:
    compact = norm(text)
    if has_any(text, ["任务一", "任务二", "任务三", "设计一份", "方案设计", "项目式", "跨学科实践", "实践活动", "真实情境"]) and len(text) > 450:
        return "项目式探究题"
    if has_any(text, ["阅读下列材料", "绿色储粮", "气调储粮", "储粮大国", "仓储技术"]) and not has_any(
        text,
        ["工艺流程", "流程图", "滤液", "滤渣", "吸收塔", "合成塔", "循环使用的物质", "循环利用的物质"],
    ):
        return "科普阅读题"
    if process_context(text):
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
    task_models = tags_by_rules(text, TASK_MODEL_RULES)
    core = tags_by_rules(text, CORE_EXPERIMENT_RULES)
    bg = tags_by_rules(text, BACKGROUND_RULES)
    qtype = classify_type(text, fallback_type)
    if not process_context(text):
        visual = [v for v in visual if v not in {"流程图", "设备流程", "循环物质"}]
        methods = [m for m in methods if m != "可循环物质判断"]
        task_models = [t for t in task_models if t != "循环物质判断"]
    task = []
    for name, keys in [
        ("写方程式", ["化学方程式", "符号表达式"]),
        ("判断入口/操作", ["从", "处通入", "操作名称", "操作为"]),
        ("找循环物质", ["可循环", "循环使用", "循环利用"]),
        ("解释原因", ["原因", "理由", "解释"]),
        ("设计方案", ["设计一份", "设计方案", "方案"]),
        ("信息提取", ["结合图", "由图可知", "资料", "阅读"]),
    ]:
        if has_any(text, keys):
            task.append(name)
    if not process_context(text):
        task = [t for t in task if t != "找循环物质"]
    return {
        "primary_type": qtype,
        "visual_forms": join_tags(visual),
        "background_tags": join_tags(bg),
        "task_tags": join_tags(task),
        "task_models": join_tags(task_models),
        "method_models": join_tags(methods),
        "core_experiment_models": join_tags(core),
        "project_chain": "资料阅读、原理认识、数据分析、方案设计" if qtype == "项目式探究题" else "",
        "promotion_visibility": "截图一眼像" if visual else ("需文字解释" if methods or core else "只适合过程库"),
    }


def pdf_path_for_paper(paper: str) -> Path:
    return PAPER_DIR / f"{paper}.pdf"


def extract_qnum_from_block(text: str) -> int | None:
    t = text or ""
    m = re.match(r"\s*(\d{1,2})[．\.\、]", t)
    if m:
        qnum = int(m.group(1))
        return qnum if 1 <= qnum <= 40 else None
    m = re.search(r"[（(]?多选[）)]?\s*(\d{1,2})[．\.\、]", t)
    if m:
        qnum = int(m.group(1))
        return qnum if 1 <= qnum <= 40 else None
    return None


def pdf_question_positions(pdf_path: Path) -> list[dict]:
    doc = fitz.open(pdf_path)
    positions = []
    for page_index, page in enumerate(doc):
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text, *_ = block
            qnum = extract_qnum_from_block(text or "")
            if qnum is None:
                continue
            if not any(p["qnum"] == qnum for p in positions):
                positions.append({"qnum": qnum, "page_index": page_index, "y0": float(y0), "page_height": float(page.rect.height), "page_width": float(page.rect.width)})
    doc.close()
    return sorted(positions, key=lambda x: (x["page_index"], x["y0"]))


ANSWER_SECTION_MARKERS = ["参考答案", "试题解析", "答案与解析"]


def find_answer_marker(doc: fitz.Document, start_page: int, start_y: float) -> tuple[int, float] | None:
    for page_index in range(start_page, min(doc.page_count, start_page + 8)):
        for block in doc[page_index].get_text("blocks"):
            x0, y0, x1, y1, text, *_ = block
            if page_index == start_page and y0 <= start_y:
                continue
            if any(m in (text or "") for m in ANSWER_SECTION_MARKERS):
                return page_index, float(y0)
    return None


def find_section_break_y(page: fitz.Page, min_y: float) -> float | None:
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text, *_ = block
        if y0 < min_y + 40:
            continue
        t = (text or "").strip()
        if re.match(r"^[一二三四五六七八九十]+[、\.．]", t):
            return float(y0)
    return None


def find_page_footer_y(page: fitz.Page, min_y: float = 0) -> float | None:
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text, *_ = block
        if y0 < min_y:
            continue
        if re.search(r"第\s*\d+\s*页\s*[（(]\s*共\s*\d+\s*页", text or ""):
            return float(y0)
    return None


def page_has_answer_header(page: fitz.Page, max_y: float = 160) -> bool:
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text, *_ = block
        if y0 > max_y:
            continue
        if any(m in (text or "") for m in ANSWER_SECTION_MARKERS):
            return True
    return False


def page_continues_question(page: fitz.Page) -> bool:
    text = page.get_text()
    if not text.strip():
        return bool(page.get_images())
    patterns = [
        r"图\s*[0-9一二三四五六七八九十]+",
        r"[（(]\s*[1-9]\d?\s*[）)]",
        r"任务[一二三四五六七八九十]",
        r"【拓展探究】",
        r"【资料】",
        r"【反思",
    ]
    if any(re.search(p, text) for p in patterns):
        return True
    return len(page.get_images()) >= 2


def resolve_question_end_page(
    doc: fitz.Document,
    current: dict,
    nxt: dict | None,
    marker: tuple[int, float] | None,
) -> int:
    start = current["page_index"]
    if nxt:
        section_y = find_section_break_y(doc[start], current["y0"] + 40)
        if section_y and nxt["page_index"] > start:
            return start
        return nxt["page_index"]
    if marker:
        mp, my = marker
        # 参考答案在页首时，上一页才是最后一页题面（避免裁进答案区）
        if my < 160:
            return max(start, mp - 1)
        return mp
    last = start
    for pi in range(start + 1, min(doc.page_count, start + 8)):
        if page_has_answer_header(doc[pi]):
            break
        if page_continues_question(doc[pi]):
            last = pi
        else:
            break
    return last


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
    end_page = resolve_question_end_page(doc, current, nxt, marker)
    out_dir = CROPS / safe_slug(paper)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob(f"q{qnum:02d}_part*.png"):
        stale.unlink(missing_ok=True)
    parts = []
    for page_index in range(current["page_index"], end_page + 1):
        page = doc[page_index]
        top = max(35, current["y0"] - 8) if page_index == current["page_index"] else 42
        footer_y = find_page_footer_y(page, top)
        if nxt and page_index == nxt["page_index"]:
            bottom = max(top + 80, nxt["y0"] - 8)
        elif marker and page_index == marker[0] and marker[1] >= 160:
            bottom = max(top + 80, marker[1] - 8)
        elif footer_y:
            bottom = max(top + 80, footer_y - 6)
        else:
            bottom = page.rect.height - 28
        if page_index == current["page_index"]:
            section_y = find_section_break_y(page, top + 40)
            if section_y:
                bottom = min(bottom, section_y - 8)
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
    return {"path": str(final), "page": f"{current['page_index']+1}-{end_page+1}" if end_page != current["page_index"] else str(current["page_index"]+1), "status": "PDF原卷裁图"}


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
                groups.append({"start": start_q, "end": end_q, "path": str(out), "page": f"{clips[0][0]+1}-{clips[-1][0]+1}", "status": "题组公共材料裁图"})
    doc.close()
    return groups


def apply_screenshot_local_overrides(evidence: list[dict]) -> list[dict]:
    overrides = load_json(DATA / "screenshot_local_overrides_v5.json", {})
    for item in evidence:
        rel = overrides.get(item.get("screenshot_id", ""))
        if not rel:
            continue
        path = Path(rel)
        if not path.is_absolute():
            path = ROOT / path
        item["local_path"] = str(path)
        item["source"] = "本地截图覆盖"
        item["screenshot_col"] = "override"
    return evidence


def split_merged_choice_stems(qs: list[dict]) -> list[dict]:
    """Split merged stems when professional OCR glued Q10+(多选)Q11 into one record."""
    out: list[dict] = []
    seen = {q["qnum"] for q in qs}
    for q in sorted(qs, key=lambda x: x["qnum"]):
        stem = q.get("stem", "")
        m = re.search(r"[（(]?多选[）)]?\s*(\d{1,2})[．\.\、]", stem)
        if q.get("qnum") == 10 and m and int(m.group(1)) == 11 and 11 not in seen:
            split_at = m.start()
            q10 = {**q, "stem": clean_stem(stem[:split_at])}
            q11_stem = stem[split_at:]
            tail = re.search(r"\s*1[2-9][．\.\、]", q11_stem)
            if tail:
                q11_stem = q11_stem[: tail.start()].strip()
            q11 = {
                **q,
                "question_id": q["question_id"].replace("-Q10", "-Q11"),
                "qnum": 11,
                "stem": clean_stem(q11_stem),
                "question_type": "选择题图像题",
                "raw_qtype": "选择题",
            }
            out.append(q10)
            out.append(q11)
            seen.add(11)
            continue
        out.append(q)
    return sorted(out, key=lambda x: x["qnum"])


def build_exam_records() -> list[dict]:
    global PAPER_TAG_OVERRIDES
    PAPER_TAG_OVERRIDES = load_paper_tag_overrides()
    base_exams = load_json(ROOT / "outputs" / "2026_yt_professional" / "data" / "exam_records.json", [])
    out = []
    by_paper: dict[str, list[dict]] = {}
    for q in base_exams:
        by_paper.setdefault(q["paper"], []).append(q)
    for paper, qs in by_paper.items():
        qs = split_merged_choice_stems(qs)
        pdf = pdf_path_for_paper(paper)
        positions = pdf_question_positions(pdf) if pdf.exists() else []
        groups = detect_topic_groups(pdf)
        paper_override = PAPER_TAG_OVERRIDES.get(paper, {})
        for q in sorted(qs, key=lambda x: x["qnum"]):
            stem = clean_stem(q["stem"])
            crop = crop_question(pdf, paper, q["qnum"], positions) if pdf.exists() else {"path": "", "page": "", "status": "无PDF"}
            labels = v4_labels(stem, q.get("question_type", ""))
            group = next((g for g in groups if g["start"] <= q["qnum"] <= g["end"]), None)
            if group:
                crop = group
                if is_simple_mcq_stem(stem):
                    labels = v4_labels(stem, q.get("question_type", ""))
                    if labels["primary_type"] in {"科普阅读题", "项目式探究题"}:
                        labels["primary_type"] = "基础题"
                    labels["visual_forms"] = join_tags(split_tags(labels["visual_forms"]) - {"曲线图", "表格", "图文阅读题组", "题组材料", "流程图"})
                    labels["method_models"] = join_tags(split_tags(labels["method_models"]) - {"图表信息提取"})
                    labels["promotion_visibility"] = "拼盘基础小题"
                else:
                    labels["primary_type"] = "科普阅读题"
                    labels["visual_forms"] = join_tags(split_tags(labels["visual_forms"]) | {"题组材料", "曲线图", "表格", "图文阅读题组"})
                    labels["method_models"] = join_tags(split_tags(labels["method_models"]) | {"图表信息提取"})
                    labels["promotion_visibility"] = "截图一眼像"
            item = {
                **q,
                "knowledge_tags": q.get("knowledge_tags", ""),
                "problem_tags": q.get("problem_tags", ""),
                "difficulty": q.get("difficulty", 0),
                "stem": stem,
                "primary_type": labels["primary_type"],
                "visual_forms": labels["visual_forms"],
                "background_tags": labels["background_tags"],
                "task_tags": labels["task_tags"],
                "task_models": labels["task_models"],
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
                "basic_common_choice_filter": "否",
                "visual_evidence_source": "",
                "false_trigger_keywords": "",
                "choice_entry_filter": "否",
                "choice_entry_reason": "",
                "promotion_policy": "auto",
                "allowed_video_ids": [],
                "audit_note": "",
            }
            # Hard calibrations generalized from reviewed examples.
            if "云南省" in paper and q["qnum"] == 15:
                item["primary_type"] = "科普阅读题"
                item["visual_forms"] = join_tags(split_tags(item["visual_forms"]) | {"题组材料", "表格"})
                item["background_tags"] = join_tags(split_tags(item["background_tags"]) - {"NaOH与CO2", "甲醇生产"})
                item["core_experiment_models"] = join_tags(split_tags(item["core_experiment_models"]) - {"NaOH与CO2反应证明"})
                item["promotion_visibility"] = "需文字解释"
            if "云南省" in paper and q["qnum"] == 16:
                item["primary_type"] = "科普阅读题"
                item["visual_forms"] = join_tags((split_tags(item["visual_forms"]) | {"题组材料", "表格"}) - {"设备流程", "循环物质", "流程图"})
                item["background_tags"] = join_tags(split_tags(item["background_tags"]) - {"NaOH与CO2"})
                item["core_experiment_models"] = join_tags(
                    split_tags(item["core_experiment_models"]) - {"NaOH与CO2反应证明", "电解水", "测定空气中氧气含量"}
                )
                item["task_models"] = join_tags(split_tags(item["task_models"]) - {"循环物质判断", "入口/出口/步骤作用判断"})
                item["method_models"] = join_tags(split_tags(item["method_models"]) - {"可循环物质判断", "设备流程分析"})
                item["task_tags"] = join_tags(split_tags(item["task_tags"]) - {"找循环物质", "判断入口/操作"})
                item["promotion_visibility"] = "需文字解释"
            if "云南省" in paper and q["qnum"] == 17:
                item["primary_type"] = "工艺流程题"
                item["visual_forms"] = join_tags(split_tags(item["visual_forms"]) | {"流程图", "设备流程", "循环物质"})
                item["background_tags"] = join_tags(split_tags(item["background_tags"]) | {"铝冶炼/含氟烟气回收"})
                item["task_tags"] = join_tags(split_tags(item["task_tags"]) | {"找循环物质", "判断入口/操作"})
                item["method_models"] = join_tags(split_tags(item["method_models"]) | {"设备流程分析", "可循环物质判断"})
                item["task_models"] = join_tags(split_tags(item["task_models"]) | {"循环物质判断", "入口/出口/步骤作用判断"})
            if "云南省" in paper and q["qnum"] == 20:
                item["primary_type"] = "项目式探究题"
                item["visual_forms"] = join_tags(split_tags(item["visual_forms"]) | {"题组材料", "表格", "曲线图", "方案设计", "项目任务链"})
                item["background_tags"] = join_tags(split_tags(item["background_tags"]) | {"活鱼运输增氧"})
                item["task_tags"] = join_tags(split_tags(item["task_tags"]) | {"设计方案", "解释原因"})
                item["method_models"] = join_tags(split_tags(item["method_models"]) | {"实验方案评价", "图表信息提取", "数字化曲线分析"})
                item["task_models"] = join_tags(split_tags(item["task_models"]) | {"方案评价", "图像匹配"})
                item["project_chain"] = "资料阅读、认识原理、图表分析、方案设计、方案评价"
            if "武威市" in paper and q["qnum"] == 17:
                item["primary_type"] = "科学探究题"
                item["visual_forms"] = join_tags(split_tags(item["visual_forms"]) | {"U型管压强", "装置图", "控制变量对照"})
                item["method_models"] = join_tags(split_tags(item["method_models"]) | {"排除干扰", "异常现象探究", "控制变量"})
                item["core_experiment_models"] = join_tags(split_tags(item["core_experiment_models"]) | {"NaOH与CO2反应证明"})
            if item["primary_type"] == "工艺流程题" and not process_context(item["stem"]) and "选择" in str(item.get("raw_qtype", "")):
                item["primary_type"] = "基础题"
                item["visual_forms"] = join_tags(split_tags(item["visual_forms"]) - {"流程图", "设备流程", "循环物质"})
                item["task_tags"] = join_tags(split_tags(item["task_tags"]) - {"找循环物质"})
                item["task_models"] = join_tags(split_tags(item["task_models"]) - {"循环物质判断"})
                item["method_models"] = join_tags(split_tags(item["method_models"]) - {"可循环物质判断"})
            item["visual_evidence_source"] = visual_evidence_source(item)
            item["false_trigger_keywords"] = false_trigger_keywords(item["stem"])
            choice_reason = choice_allowed_reason(item)
            item["choice_entry_reason"] = choice_reason or "非白名单选择题"
            if is_choice_question(item) and not choice_reason:
                item["choice_entry_filter"] = "是"
                item["promotion_visibility"] = "选择题入口过滤"
            if is_basic_common_choice(item):
                item["primary_type"] = "基础常识选择题"
                item["promotion_visibility"] = "基础常识题过滤"
                item["basic_common_choice_filter"] = "是"
            q_override = paper_override.get("questions", {}).get(str(q["qnum"]))
            if q_override:
                apply_paper_tag_override(item, q_override)
            out.append(item)
    save_json(DATA / "exam_records_v5.json", out)
    return out


def transcript_name_key(text: str) -> str:
    text = Path(text or "").stem
    text = re.sub(r"【[^】]*(脚本|录音|定稿|终版|逐字稿|解题)[^】]*】", "", text)
    text = re.sub(r"[\[\]（）()【】《》_+ 　·•\-—]+", "", text)
    text = re.sub(r"(脚本|录音稿|逐字稿|定稿|终版|最终稿|不含批注|含题干|解题课|录屏课|新版|新修|副本|复制|v\d+|V\d+)", "", text)
    return norm(text)


def direction_penalty(a: str, b: str) -> float:
    directions = ["上", "中", "下", "一", "二", "三", "1", "2", "3"]
    for d1, d2 in [("上", "下"), ("下", "上"), ("上", "中"), ("中", "上"), ("中", "下"), ("下", "中"), ("1", "2"), ("2", "1"), ("1", "3"), ("3", "1"), ("2", "3"), ("3", "2")]:
        if d1 in a and d2 in b:
            return 0.12
    return 0.0


def transcript_name_score(video_name: str, file_name: str) -> float:
    vkey = transcript_name_key(video_name)
    fkey = transcript_name_key(file_name)
    if not vkey or not fkey:
        return 0.0
    ratio = difflib.SequenceMatcher(None, vkey, fkey).ratio()
    vt, ft = prof.tokenize_cn(vkey), prof.tokenize_cn(fkey)
    overlap = len(vt & ft) / max(1, len(vt | ft))
    contains = 0.18 if (vkey in fkey or fkey in vkey) else 0
    score = ratio * 0.58 + overlap * 0.34 + contains - direction_penalty(vkey, fkey)
    return round(max(0.0, min(score, 1.0)), 3)


def transcript_body_score(video: dict, transcript: dict) -> float:
    context = " ".join([video.get("video_name", ""), video.get("hierarchy", ""), video.get("content_summary", ""), video.get("signatures", "")])
    tokens = [t for t in prof.tokenize_cn(context) if len(t) >= 2]
    text = norm(transcript.get("text", "")[:9000])
    if not tokens or not text:
        return 0.0
    hits = sum(1 for t in tokens if t in text)
    key_hits = sum(1 for t in prof.tokenize_cn(video.get("video_name", "")) if t in text)
    return round(min(1.0, hits / max(6, len(tokens)) * 0.72 + min(key_hits, 5) * 0.05), 3)


def load_transcripts_for_v43() -> list[dict]:
    path = ROOT / "outputs" / "2026_yt_professional" / "data" / "transcripts.json"
    transcripts = load_json(path, [])
    if transcripts:
        return transcripts
    built = prof.build_transcripts()
    return [prof.asdict(x) for x in built]


def match_transcript_v43(video: dict, transcripts: list[dict]) -> tuple[dict | None, dict]:
    best_name = None
    ranked = []
    for tr in transcripts:
        if not tr.get("text"):
            continue
        nscore = transcript_name_score(video.get("video_name", ""), tr.get("file_name", ""))
        ranked.append((nscore, tr))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if ranked:
        best_name = ranked[0]
    body_score = 0.0
    adopted = None
    status = "未匹配"
    reason = "文件名和正文均不足以确认"
    if best_name and best_name[0] >= 0.58:
        adopted = best_name[1]
        status = "强匹配-文件名"
        reason = "视频名称与逐字稿文件名相似度高，优先采用文件名匹配"
        body_score = transcript_body_score(video, adopted)
    elif best_name:
        candidates = ranked[:8]
        rescored = []
        for nscore, tr in candidates:
            bscore = transcript_body_score(video, tr)
            rescored.append((nscore * 0.7 + bscore * 0.3, nscore, bscore, tr))
        rescored.sort(key=lambda x: x[0], reverse=True)
        total, nscore, body_score, tr = rescored[0]
        if nscore >= 0.44 and body_score >= 0.24:
            adopted = tr
            status = "强匹配-文件名+正文"
            reason = "文件名不能单独确认，但正文与视频名称/截图文字共同支持"
        elif nscore >= 0.36 or body_score >= 0.32:
            adopted = None
            status = "弱匹配待人工复核"
            reason = "候选存在一定相似，但按v5规则不直接采用为最终标签"
            best_name = (nscore, tr)
        else:
            best_name = (nscore, tr)
    audit = {
        "video_id": video.get("video_id", ""),
        "video_name": video.get("video_name", ""),
        "source": video.get("source", ""),
        "source_row": video.get("source_row") or 0,
        "candidate_transcript_id": (adopted or (best_name[1] if best_name else {})).get("transcript_id", "") if (adopted or best_name) else "",
        "candidate_file": (adopted or (best_name[1] if best_name else {})).get("file_name", "") if (adopted or best_name) else "",
        "file_name_score": round(best_name[0], 3) if best_name else 0,
        "body_score": body_score,
        "adopt_status": status,
        "reject_reason": "" if adopted else reason,
        "match_reason": reason,
    }
    return adopted, audit


def transcript_evidence_text(video: dict, transcript: dict | None) -> str:
    base = video.get("content_summary", "")
    if not transcript:
        return base[:1200]
    summary = transcript.get("summary", "")
    text = old_base.normalize_text(transcript.get("text", ""))[:1600]
    return old_base.normalize_text("；".join([summary, text, base]))[:2200]


def build_video_records_and_evidence() -> tuple[list[dict], list[dict]]:
    v3_videos = load_json(ROOT / "outputs" / "2026_yt_visual_v3" / "data" / "video_records_v3.json", [])
    transcripts = load_transcripts_for_v43()
    media = old_image.collect_sheet_media()
    videos = []
    evidence = []
    transcript_audits = []
    for v in v3_videos:
        matched_tr, tr_audit = match_transcript_v43(v, transcripts)
        transcript_audits.append(tr_audit)
        transcript_text = matched_tr.get("text", "") if matched_tr else ""
        text = " ".join([v.get("video_name", ""), v.get("hierarchy", ""), v.get("content_summary", ""), v.get("signatures", ""), v.get("key_constraints", ""), transcript_text[:5000]])
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
        if "离子共存" in name_text or "共存" in name_text:
            labels["task_models"] = join_tags(split_tags(labels["task_models"]) | {"共存判断"})
            labels["task_tags"] = join_tags(split_tags(labels["task_tags"]) | {"共存判断"})
        nv = {
            **v,
            "knowledge_tags": v.get("knowledge_tags", ""),
            "problem_tags": v.get("problem_tags", ""),
            "difficulty": v.get("difficulty", 0),
            "key_constraints": v.get("key_constraints", ""),
            "primary_type": labels["primary_type"],
            "visual_forms": labels["visual_forms"],
            "background_tags": labels["background_tags"],
            "task_tags": labels["task_tags"],
            "task_models": labels["task_models"],
            "method_models": labels["method_models"],
            "core_experiment_models": labels["core_experiment_models"],
            "project_chain": labels["project_chain"],
            "promotion_visibility": labels["promotion_visibility"],
            "transcript_id_v5": matched_tr.get("transcript_id", "") if matched_tr else "",
            "transcript_file_v5": matched_tr.get("file_name", "") if matched_tr else "",
            "transcript_match_status_v5": tr_audit["adopt_status"],
            "transcript_file_score_v5": tr_audit["file_name_score"],
            "transcript_body_score_v5": tr_audit["body_score"],
            "transcript_evidence": transcript_evidence_text(v, matched_tr),
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
                            "task_models": nv["task_models"],
                            "method_models": nv["method_models"],
                            "quality_status": "v5截图级证据",
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
                "task_models": "方案评价",
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
                    "task_models": "方案评价",
                    "method_models": "控制变量、排除干扰、异常现象探究",
                }
            )
    evidence = apply_screenshot_local_overrides(evidence)
    save_json(DATA / "video_records_v5.json", videos)
    save_json(DATA / "screenshot_evidence_v5.json", evidence)
    save_json(DATA / "transcript_match_audit_v5.json", transcript_audits)
    return videos, evidence


POPSCI_ENTRY_TASK_TAGS = {"信息提取", "解释原因", "判断入口/操作"}
POPSCI_ENTRY_VISUALS = {"曲线图", "表格", "题组材料", "数字化曲线"}
POPSCI_ENTRY_METHODS = {"图表信息提取", "控制变量", "数字化曲线分析"}
POPSCI_VIDEO_THEME_HINTS: dict[str, set[str]] = {
    "XZK-20": {"碳纤维", "材料", "性能", "强度", "耐热", "尺寸", "配方", "新型", "利用率", "试剂"},
    "XZK-21": {"保鲜", "降解", "微生物", "西瓜", "产率", "含量", "适宜", "保鲜膜"},
    "ZND-27": {"材料", "图表", "数据", "科普"},
}


def _item_task_tags(item: dict) -> set[str]:
    return split_tags(item.get("task_tags", "")) | split_tags(item.get("problem_tags", ""))


def popsci_form_label(q: dict, v: dict, visual_o: set[str], task_o: set[str], method_o: set[str]) -> str:
    if q.get("primary_type") != "科普阅读题" or v.get("primary_type") != "科普阅读题":
        return ""
    if not (_item_task_tags(q) & POPSCI_ENTRY_TASK_TAGS) or not (_item_task_tags(v) & POPSCI_ENTRY_TASK_TAGS):
        return ""
    if visual_o & {"曲线图", "数字化曲线"}:
        return "信息提取+曲线图"
    if visual_o & {"表格"}:
        return "信息提取+数据表"
    q_method, v_method = split_tags(q.get("method_models", "")), split_tags(v.get("method_models", ""))
    if method_o & {"控制变量"} or (q_method | v_method) & {"控制变量"}:
        return "信息提取+控制变量分析"
    if visual_o & POPSCI_ENTRY_VISUALS:
        return f"信息提取+{join_tags(visual_o & POPSCI_ENTRY_VISUALS)}"
    if method_o & POPSCI_ENTRY_METHODS:
        return f"信息提取+{join_tags(method_o & POPSCI_ENTRY_METHODS)}"
    return ""


def popsci_theme_isomorph(q: dict, v: dict, bg_o: set[str]) -> bool:
    if bg_o:
        return True
    stem = q.get("stem", "")
    hints = POPSCI_VIDEO_THEME_HINTS.get(v.get("video_id", ""), set())
    return bool(hints and any(h in stem for h in hints))


def popsci_data_isomorph(q: dict, v: dict, visual_o: set[str], method_o: set[str]) -> bool:
    stem = q.get("stem", "")
    if visual_o & {"曲线图", "表格", "数字化曲线"} and method_o & POPSCI_ENTRY_METHODS:
        return True
    if v.get("video_id") == "XZK-21" and visual_o & {"曲线图"} and any(k in stem for k in ("双线", "两条", "对比", "曲线")):
        return True
    return False


def popsci_isomorph_rank(q: dict, v: dict, bg_o: set[str], visual_o: set[str], method_o: set[str]) -> int:
    theme = popsci_theme_isomorph(q, v, bg_o)
    data = popsci_data_isomorph(q, v, visual_o, method_o)
    if theme and data:
        return 3
    if theme or data:
        return 2
    return 0


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


def score_pair(q: dict, v: dict, evs: list[dict]) -> tuple[float, str, list[dict], str, int, str]:
    q_visual, v_visual = split_tags(q["visual_forms"]), split_tags(v["visual_forms"])
    q_bg, v_bg = split_tags(q["background_tags"]), split_tags(v["background_tags"])
    q_task, v_task = split_tags(q["task_tags"]), split_tags(v["task_tags"])
    q_task_model, v_task_model = split_tags(q.get("task_models", "")), split_tags(v.get("task_models", ""))
    q_method, v_method = split_tags(q["method_models"]), split_tags(v["method_models"])
    q_core, v_core = split_tags(q["core_experiment_models"]), split_tags(v["core_experiment_models"])
    task_model_o = q_task_model & v_task_model
    whitelisted = q.get("promotion_policy") == "allow" and v["video_id"] in set(q.get("allowed_video_ids") or [])
    if q.get("choice_entry_filter") == "是":
        return 0, "", [], "选择题入口过滤：非坐标图像/表格计算/流程图/离子共存/科普材料题组", 0, ""
    if q.get("basic_common_choice_filter") == "是" and not (q_task_model & CHOICE_ALLOWED_TASKS):
        return 0, "", [], "基础常识选择题，生活/常识语境不作为宣传版押题证据", 0, ""
    if not type_allowed(q["primary_type"], v["primary_type"]) and not task_model_o and not whitelisted:
        return 0, "", [], "题型硬路由不通过", 0, ""
    visual_o = q_visual & v_visual
    bg_o = q_bg & v_bg
    task_o = q_task & v_task
    method_o = q_method & v_method
    core_o = q_core & v_core
    if bg_o & {"NaOH与CO2"} and not co2_naoh_context(q.get("stem", "")):
        bg_o -= {"NaOH与CO2"}
    if core_o & {"NaOH与CO2反应证明"} and not co2_naoh_context(q.get("stem", "")):
        core_o -= {"NaOH与CO2反应证明"}
    if q["primary_type"] == "科普阅读题" and v["primary_type"] == "工艺流程题" and not visual_o:
        return 0, "", [], "科普材料题不应匹配工业工艺流程视频", 0, ""
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
    if task_model_o:
        score += 70 + 8 * min(len(task_model_o), 2)
        reasons.append(f"设问任务同构：{join_tags(task_model_o)}")
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
        e_task_model = split_tags(e.get("task_models", ""))
        e_method = split_tags(e.get("method_models", ""))
        ev_score = len(q_visual & e_visual) * 10 + len(q_task & e_task) * 6 + len(q_task_model & e_task_model) * 14 + len(q_method & e_method) * 6
        if ev_score or (q["primary_type"] == v["primary_type"] and visual_o) or task_model_o:
            item = dict(e)
            item["_ev_score"] = ev_score
            selected.append(item)
    selected.sort(key=lambda x: x.get("_ev_score", 0), reverse=True)
    if selected:
        score += min(selected[0].get("_ev_score", 0), 25)
    isomorph_rank = 0
    popsci_form = ""
    if q["primary_type"] == "科普阅读题" and v["primary_type"] == "科普阅读题":
        popsci_form = popsci_form_label(q, v, visual_o, task_o, method_o)
        isomorph_rank = popsci_isomorph_rank(q, v, bg_o, visual_o, method_o)
        if popsci_form:
            score = max(score, 115)
            reasons.append(f"科普阅读形式相似：{popsci_form}")
        if popsci_theme_isomorph(q, v, bg_o):
            score += 30
            reasons.append("素材主题与课内例题同构，宣传版截图前置")
        if popsci_data_isomorph(q, v, visual_o, method_o):
            score += 30
            reasons.append("数据作答方式与课内例题同构，宣传版截图前置")
    # Calibrations that represent now-general v4 concepts.
    if "武威市" in q["paper"] and q["qnum"] == 17 and v["video_id"] == "LOCAL-NAOH-CO2-U":
        score = 240
        reasons = ["同为NaOH与CO2无明显现象反应证明，U型管压强变化和控制变量对照外观高度相似"]
    if "云南省" in q["paper"] and q["qnum"] in {12, 13, 14} and v["video_id"] in {"XZK-21", "ZND-27"}:
        score = 430 if v["video_id"] == "XZK-21" else 410
        reasons = ["同为科普阅读题组，长材料+图表+多小题的信息提取版式相似"]
    if "云南省" in q["paper"] and q["qnum"] == 17 and v["video_id"] in {"ZND-29", "ZND-24"}:
        score = 520 if v["video_id"] == "ZND-29" else 500
        reasons = ["同为工艺流程题，设备流程/吸收合成环节或可循环利用物质设问高度相似"]
    if "云南省" in q["paper"] and q["qnum"] == 20 and v["video_id"] in {"ZND-17", "ZND-22", "ZND-14", "ZND-25"}:
        project_scores = {"ZND-17": 390, "ZND-22": 370, "ZND-14": 350, "ZND-25": 330}
        score = project_scores[v["video_id"]]
        reasons = ["同为项目式/跨学科实践题，任务链包含资料阅读、原理分析、方案设计或真实情境迁移"]
    if "云南省" in q["paper"] and q["qnum"] == 20 and v["primary_type"] == "项目式探究题":
        score += 60
        reasons.append("同为项目式/跨学科实践题，任务链包含资料阅读、原理分析和方案设计")
    if "湖南省" in q["paper"] and q["qnum"] == 14 and v["video_id"] == "XZK-14":
        score = 420
        reasons = ["同为CO2/pH变化曲线选择题，数字化曲线读图方法相似"]
    if "烟台市" in q["paper"] and q["qnum"] == 11 and v["video_id"] in {"XZK-27", "JCTB-145", "JCTB-146"}:
        score = max(score, {"XZK-27": 480, "JCTB-145": 460, "JCTB-146": 450}.get(v["video_id"], 440))
        reasons = ["Na2SO4/NaCl溶解度曲线多选读图，配溶解度曲线解题课"]
    if "湖南省" in q["paper"] and q["qnum"] == 18 and v["video_id"] in {"XZK-21", "ZND-27"}:
        score = 460 if v["video_id"] == "XZK-21" else 440
        reasons = ["同为科普阅读题组，长材料+图表对比的信息提取版式相似"]
    if "南充市" in q["paper"] and q["qnum"] == 17 and v["video_id"] in {"XZK-21", "ZND-27"}:
        score = 460 if v["video_id"] == "XZK-21" else 440
        reasons = ["同为科普阅读短文，氢气/氨气储运材料+多小题信息提取，配科普阅读理解课"]
    if "南充市" in q["paper"] and q["qnum"] == 15 and v["video_id"] in {"JCTB-127", "XZK-26", "XZK-46"}:
        project_scores = {"JCTB-127": 490, "XZK-26": 470, "XZK-46": 450}
        score = project_scores[v["video_id"]]
        reasons = ["同为金属回收/置换流程，设问判断滤液（无色滤液）中溶质成分，含置换后剩余盐与未反应盐"]
    if "湖南省" in q["paper"] and q["qnum"] == 22 and v["video_id"] == "XZK-38":
        score = 480
        reasons = ["同为控制变量对照实验，设问实验①/对照组的目的"]
    if "湖南省" in q["paper"] and q["qnum"] == 22 and v["video_id"] == "XZK-18":
        score = 460
        reasons = ["同为实际应用中选试剂/方案，需从多方面说明理由"]
    if "重庆市" in q["paper"] and q["qnum"] == 22 and v["video_id"] == "XZK-38":
        score = 480
        reasons = ["同为溶液导电性探究，对比灯泡亮度变化"]
    if "重庆市" in q["paper"] and q["qnum"] == 22 and v["video_id"] == "XZK-6":
        score = 460
        reasons = ["同为控制变量表格，根据实验目的确定未知数据/修改方案使单一变量"]
    if "凉山州" in q["paper"] and q["qnum"] == 16 and v["video_id"] == "XZK-21":
        score = 460
        reasons = ["同为图表/曲线双线对比读数据，根据两条线差异写结论并找证据（课内西瓜保鲜膜第5问，科普阅读理解下）"]
    if "遂宁市" in q["paper"] and q["qnum"] == 17 and v["video_id"] in {"JCTB-144", "JCTB-145", "JCTB-146"}:
        project_scores = {"JCTB-144": 490, "JCTB-145": 480, "JCTB-146": 460}
        score = max(score, project_scores.get(v["video_id"], 440))
        reasons = [
            "活动2(2)浓溶液加水稀释求总质量，配溶液的稀释专课"
            if v["video_id"] == "JCTB-144"
            else "活动3-4 Na2CO3/NH4Cl溶解度曲线读点/饱和/结晶，配溶解度曲线专课"
        ]
    if "广安市" in q["paper"] and q["qnum"] == 18 and v["video_id"] in {"XZK-17", "XZK-18", "XZK-21"}:
        project_scores = {"XZK-17": 490, "XZK-18": 470, "XZK-21": 480}
        score = project_scores.get(v["video_id"], 460)
        reasons = [
            "同为跨学科实践/制作类项目链（自制缓释片 vs 课内供氧器），任务链含资料阅读、原理与方案"
            if v["video_id"] in {"XZK-17", "XZK-18"}
            else "任务三图2缓释片与对照试剂双线对比读CO2释放曲线，写能否缓慢释放结论，配科普阅读下双线读数据课"
        ]
    if "成都市" in q["paper"] and q["qnum"] == 19 and v["video_id"] in {"XZK-17", "XZK-18", "XZK-21"}:
        project_scores = {"XZK-18": 490, "XZK-17": 470, "XZK-21": 480}
        score = project_scores.get(v["video_id"], 460)
        reasons = [
            "任务三发酵粉Y形管+图3 Na2CO3/NaHCO3双线对比选试剂，配跨学科供氧器(对比数据选试剂+解释理由)"
            if v["video_id"] in {"XZK-17", "XZK-18"}
            else "图3双线读数据写不选碳酸钠理由，配科普阅读下双线对比课"
        ]
    if whitelisted:
        order = list(q.get("allowed_video_ids") or [])
        rank_scores = {vid: 480 - idx * 20 for idx, vid in enumerate(order)}
        score = max(score, rank_scores.get(v["video_id"], 420))
        note = q.get("audit_note") or "人工白名单指定相似题"
        if not reasons:
            reasons = [note]
        elif note not in "；".join(reasons):
            reasons.append(note)
        if not selected and evs:
            selected = [dict(e) for e in evs[: (6 if q["primary_type"] in {"项目式探究题", "综合应用题"} else 3)]]
            for item in selected:
                item["_ev_score"] = 10
    if score < 105:
        return score, "；".join(reasons), selected[:6], "宣传版阈值不足", isomorph_rank, popsci_form
    if not selected:
        return score, "；".join(reasons), selected, "没有可展示截图", isomorph_rank, popsci_form
    return score, "；".join(reasons), selected[: (6 if q["primary_type"] in {"项目式探究题", "综合应用题"} else 3)], "", isomorph_rank, popsci_form


def note_for(q: dict, e: dict, reason: str) -> str:
    if e.get("similarity_note"):
        return e["similarity_note"]
    visual = join_tags(split_tags(q["visual_forms"]) & split_tags(e.get("visual_forms", "")))
    task = join_tags(split_tags(q["task_tags"]) & split_tags(e.get("task_tags", "")))
    task_model = join_tags(split_tags(q.get("task_models", "")) & split_tags(e.get("task_models", "")))
    method = join_tags(split_tags(q["method_models"]) & split_tags(e.get("method_models", "")))
    parts = []
    if visual:
        parts.append(f"图文外观同为{visual}")
    if task:
        parts.append(f"设问任务同为{task}")
    if task_model:
        parts.append(f"学生解题动作同为{task_model}")
    if method:
        parts.append(f"解法模型同为{method}")
    return "，".join(parts) + "。" if parts else reason[:80]


def auto_review(q: dict, v: dict, selected: list[dict], reason: str, reject: str) -> dict:
    q_visual, v_visual = split_tags(q.get("visual_forms", "")), split_tags(v.get("visual_forms", ""))
    q_task_model, v_task_model = split_tags(q.get("task_models", "")), split_tags(v.get("task_models", ""))
    q_task, v_task = split_tags(q.get("task_tags", "")), split_tags(v.get("task_tags", ""))
    q_method, v_method = split_tags(q.get("method_models", "")), split_tags(v.get("method_models", ""))
    q_core, v_core = split_tags(q.get("core_experiment_models", "")), split_tags(v.get("core_experiment_models", ""))
    visual_o = q_visual & v_visual
    task_model_o = q_task_model & v_task_model
    task_o = q_task & v_task
    method_o = q_method & v_method
    core_o = q_core & v_core
    whitelist_task = bool(q_task_model & CHOICE_ALLOWED_TASKS)

    if "云南省" in q.get("paper", "") and q.get("qnum") in {15, 16}:
        return {
            "first_eye_similarity": "否",
            "same_type_pool": "否",
            "specific_reason": "否",
            "multi_screenshot_roles": "",
            "only_same_chapter": "是",
            "basic_common_choice_filter": q.get("basic_common_choice_filter", "否"),
            "choice_entry_filter": q.get("choice_entry_filter", "否"),
            "choice_entry_reason": q.get("choice_entry_reason", ""),
            "visual_evidence_source": q.get("visual_evidence_source", ""),
            "false_trigger_keywords": q.get("false_trigger_keywords", ""),
            "final_review": "降级过程库",
            "review_reason": "人工复核：特色产业/绿色储粮科普材料，不宜按表格规律或工艺流程宣传",
        }

    if q.get("promotion_policy") == "exclude":
        return {
            "first_eye_similarity": "否",
            "same_type_pool": "否",
            "specific_reason": "否",
            "multi_screenshot_roles": "",
            "only_same_chapter": "是",
            "basic_common_choice_filter": q.get("basic_common_choice_filter", "否"),
            "choice_entry_filter": q.get("choice_entry_filter", "否"),
            "choice_entry_reason": q.get("choice_entry_reason", ""),
            "visual_evidence_source": q.get("visual_evidence_source", ""),
            "false_trigger_keywords": q.get("false_trigger_keywords", ""),
            "final_review": "降级过程库",
            "review_reason": q.get("audit_note") or "人工审计：不进入宣传版",
        }

    if q.get("promotion_policy") == "allow":
        allowed = set(q.get("allowed_video_ids") or [])
        if v["video_id"] not in allowed:
            return {
                "first_eye_similarity": "否",
                "same_type_pool": "否",
                "specific_reason": "否",
                "multi_screenshot_roles": "",
                "only_same_chapter": "是",
                "basic_common_choice_filter": q.get("basic_common_choice_filter", "否"),
                "choice_entry_filter": q.get("choice_entry_filter", "否"),
                "choice_entry_reason": q.get("choice_entry_reason", ""),
                "visual_evidence_source": q.get("visual_evidence_source", ""),
                "false_trigger_keywords": q.get("false_trigger_keywords", ""),
                "final_review": "降级过程库",
                "review_reason": f"不在人工白名单：{join_tags(sorted(allowed)) or '无'}",
            }
        roles = []
        if visual_o:
            roles.extend(list(visual_o))
        if task_model_o:
            roles.extend(list(task_model_o))
        if task_o:
            roles.extend(list(task_o))
        if method_o:
            roles.extend(list(method_o))
        if core_o and q["primary_type"] in {"基本实验题", "科学探究题"}:
            roles.extend(list(core_o))
        first_eye = "是" if visual_o else ("一般" if task_model_o or method_o or task_o else "是")
        base = {
            "first_eye_similarity": first_eye,
            "same_type_pool": "是",
            "specific_reason": "是",
            "multi_screenshot_roles": join_tags(roles),
            "only_same_chapter": "否",
            "basic_common_choice_filter": q.get("basic_common_choice_filter", "否"),
            "choice_entry_filter": q.get("choice_entry_filter", "否"),
            "choice_entry_reason": q.get("choice_entry_reason", ""),
            "visual_evidence_source": q.get("visual_evidence_source", ""),
            "false_trigger_keywords": q.get("false_trigger_keywords", ""),
        }
        if reject:
            return {**base, "final_review": "降级过程库", "review_reason": reject}
        if not selected:
            return {**base, "final_review": "替换截图", "review_reason": "缺少可展示截图"}
        return {**base, "final_review": "保留", "review_reason": q.get("audit_note") or "人工白名单审计通过"}

    first_eye = "是" if visual_o else ("一般" if task_model_o or method_o or task_o else "否")
    same_pool = "是" if type_allowed(q["primary_type"], v["primary_type"]) or task_model_o else "否"
    specific = "是" if any(k in reason for k in ["视觉形态相似", "设问任务同构", "设问任务相似", "解法模型相似", "同为", "U型管", "流程", "题组", "项目式", "科普阅读形式相似", "同构"]) else "否"
    roles = []
    if visual_o:
        roles.extend(list(visual_o))
    if task_model_o:
        roles.extend(list(task_model_o))
    if task_o:
        roles.extend(list(task_o))
    if method_o:
        roles.extend(list(method_o))
    if core_o and q["primary_type"] in {"基本实验题", "科学探究题"}:
        roles.extend(list(core_o))
    multi_roles = join_tags(roles)
    only_chapter = "是" if (not visual_o and not task_model_o and not task_o and not method_o and not (core_o and q["primary_type"] in {"基本实验题", "科学探究题"})) else "否"
    support_count = sum(
        [
            bool(visual_o and q.get("visual_evidence_source") != "无"),
            bool(task_model_o),
            bool(method_o),
            bool(q["primary_type"] == v["primary_type"] and q["primary_type"] not in {"基础题", "基础常识选择题"}),
            bool(core_o and q["primary_type"] in {"基本实验题", "科学探究题"}),
        ]
    )
    if reject:
        conclusion = "降级过程库"
        review_reason = reject
    elif q.get("choice_entry_filter") == "是":
        conclusion = "降级过程库"
        review_reason = "选择题入口过滤：只保留坐标图像、表格计算、含流程图、离子共存、科普材料题组"
    elif q.get("basic_common_choice_filter") == "是" and not whitelist_task:
        conclusion = "降级过程库"
        review_reason = "基础常识选择题，生活语境循环利用不等于工艺流程循环物质判断"
    elif q.get("visual_evidence_source") == "无" and q["primary_type"] in {"工艺流程题", "科学探究题", "项目式探究题"} and not whitelist_task:
        conclusion = "降级过程库"
        review_reason = "缺少题图/图表/流程/装置等视觉证据，不能仅凭关键词进入宣传版"
    elif only_chapter == "是":
        conclusion = "降级过程库"
        review_reason = "只同章节/素材标签，外观、任务模型和解法模型都不够明确"
    elif first_eye == "否" and not (task_model_o or method_o):
        conclusion = "降级过程库"
        review_reason = "第一眼不像，且缺少任务模型或解法相似兜底"
    elif q["primary_type"] == "科普阅读题" and first_eye != "是":
        conclusion = "降级过程库"
        review_reason = "科普阅读题需题图/截图一眼像，不能仅凭方程式计算或图表提取标签进宣传版"
    elif same_pool == "否":
        conclusion = "降级过程库"
        review_reason = "视频不在同题型池，且无设问任务同构支撑"
    elif specific == "否":
        conclusion = "替换截图"
        review_reason = "命中说明不够具体，需要补充外观/任务/方法相似点"
    elif not selected:
        conclusion = "替换截图"
        review_reason = "缺少可展示截图"
    elif support_count < 2 and not whitelist_task:
        conclusion = "降级过程库"
        review_reason = "宣传支撑不足，未同时满足视觉/题型/任务/解法中的两项强支撑"
    else:
        conclusion = "保留"
        review_reason = "通过v5选择题收紧预审"
    return {
        "first_eye_similarity": first_eye,
        "same_type_pool": same_pool,
        "specific_reason": specific,
        "multi_screenshot_roles": multi_roles,
        "only_same_chapter": only_chapter,
        "basic_common_choice_filter": q.get("basic_common_choice_filter", "否"),
        "choice_entry_filter": q.get("choice_entry_filter", "否"),
        "choice_entry_reason": q.get("choice_entry_reason", ""),
        "visual_evidence_source": q.get("visual_evidence_source", ""),
        "false_trigger_keywords": q.get("false_trigger_keywords", ""),
        "final_review": conclusion,
        "review_reason": review_reason,
    }


def append_comparison_layer_matches(
    q: dict,
    matches: list[dict],
    videos: list[dict],
    ev_by_video: dict[str, list[dict]],
) -> None:
    """对比表展示层：promotion=exclude 时仍可展示任务/解法相似的弱匹配（不计入宣传版命中）。"""
    if not q.get("comparison_show"):
        return
    comp_ids = [vid for vid in (q.get("comparison_video_ids") or []) if vid]
    if not comp_ids:
        return
    video_map = {v["video_id"]: v for v in videos}
    existing = {m["video_id"] for m in matches if m["question_id"] == q["question_id"] and m.get("final_show")}
    for vid in comp_ids:
        if vid in existing:
            continue
        v = video_map.get(vid)
        if not v:
            continue
        score, reason, selected, _reject, iso_rank, popsci_form = score_pair(q, v, ev_by_video.get(vid, []))
        evs = ev_by_video.get(vid, [])
        if not selected and evs:
            selected = [dict(evs[0])]
            score = max(score, 100.0)
            reason = reason or "对比表展示层：人工指定相似课"
        if not selected:
            continue
        ids = [e["screenshot_id"] for e in selected]
        notes = [note_for(q, e, reason) for e in selected]
        matches.append(
            {
                "match_id": f"{q['question_id']}__{vid}__COMPARE",
                "question_id": q["question_id"],
                "paper": q["paper"],
                "qnum": q["qnum"],
                "primary_type": q["primary_type"],
                "video_id": vid,
                "video_name": v["video_name"],
                "video_type": v["primary_type"],
                "score": round(score, 1),
                "hit_reason": reason,
                "isomorph_rank": iso_rank,
                "popsci_form": popsci_form,
                "screenshot_ids": "；".join(ids),
                "screenshot_notes": "；".join(notes),
                "reject_reason": "",
                "final_show": True,
                "comparison_only": True,
                "first_eye_similarity": "一般",
                "same_type_pool": "是",
                "specific_reason": "是",
                "multi_screenshot_roles": "",
                "only_same_chapter": "否",
                "basic_common_choice_filter": q.get("basic_common_choice_filter", "否"),
                "choice_entry_filter": q.get("choice_entry_filter", "否"),
                "choice_entry_reason": q.get("choice_entry_reason", ""),
                "visual_evidence_source": q.get("visual_evidence_source", ""),
                "false_trigger_keywords": q.get("false_trigger_keywords", ""),
                "final_review": "保留",
                "review_reason": q.get("audit_note") or "对比表展示层：任务/解法相似",
            }
        )
        existing.add(vid)


def build_matches(exams: list[dict], videos: list[dict], evidence: list[dict]) -> list[dict]:
    ev_by_video: dict[str, list[dict]] = {}
    for e in evidence:
        ev_by_video.setdefault(e["video_id"], []).append(e)
    matches = []
    for q in exams:
        scored = []
        for v in videos:
            score, reason, selected, reject, iso_rank, popsci_form = score_pair(q, v, ev_by_video.get(v["video_id"], []))
            if score <= 0:
                continue
            scored.append((score, reason, selected, reject, v, iso_rank, popsci_form))
        scored.sort(key=lambda x: x[0], reverse=True)
        max_videos = 3 if q["primary_type"] in {"项目式探究题", "综合应用题"} else (2 if q["primary_type"] in {"科普阅读题", "工艺流程题"} else 1)
        if q.get("promotion_policy") == "allow" and q.get("allowed_video_ids"):
            max_videos = len(q["allowed_video_ids"])
        accepted = 0
        for score, reason, selected, reject, v, iso_rank, popsci_form in scored[:12]:
            review = auto_review(q, v, selected, reason, reject)
            final = bool(review["final_review"] == "保留" and accepted < max_videos)
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
                    "isomorph_rank": iso_rank,
                    "popsci_form": popsci_form,
                    "screenshot_ids": "；".join(ids),
                    "screenshot_notes": "；".join(notes),
                    "reject_reason": "" if final else (reject or ("同题已保留更强证据" if review["final_review"] == "保留" else review["review_reason"])),
                    "final_show": final,
                    **review,
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
                    "reject_reason": (
                        "选择题入口过滤：只保留坐标图像、表格计算、含流程图、离子共存、科普材料题组"
                        if q.get("choice_entry_filter") == "是"
                        else "基础常识选择题，生活语境循环利用不等于工艺流程循环物质判断"
                        if q.get("basic_common_choice_filter") == "是"
                        else "未达到v5选择题收紧版展示标准"
                    ),
                    "final_show": False,
                    "first_eye_similarity": "否",
                    "same_type_pool": "否",
                    "specific_reason": "否",
                    "multi_screenshot_roles": "",
                    "only_same_chapter": "是",
                    "basic_common_choice_filter": q.get("basic_common_choice_filter", "否"),
                    "choice_entry_filter": q.get("choice_entry_filter", "否"),
                    "choice_entry_reason": q.get("choice_entry_reason", ""),
                    "visual_evidence_source": q.get("visual_evidence_source", ""),
                    "false_trigger_keywords": q.get("false_trigger_keywords", ""),
                    "final_review": "降级过程库",
                    "review_reason": (
                        "选择题入口过滤：只保留坐标图像、表格计算、含流程图、离子共存、科普材料题组"
                        if q.get("choice_entry_filter") == "是"
                        else "基础常识选择题，生活语境循环利用不等于工艺流程循环物质判断"
                        if q.get("basic_common_choice_filter") == "是"
                        else "未达到v5选择题收紧版展示标准"
                    ),
                }
            )
        append_comparison_layer_matches(q, matches, videos, ev_by_video)
    save_json(DATA / "matches_v5.json", matches)
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
        path = Path(file_path)
        mtime_key = f"{key}::mtime"
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            current_mtime = None
        if key in tokens and current_mtime is not None and tokens.get(mtime_key) == current_mtime:
            return tokens[key]
        if key in tokens:
            tokens.pop(key, None)
            tokens.pop(mtime_key, None)
        rel = str(path.relative_to(ROOT))
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
        if current_mtime is not None:
            tokens[mtime_key] = current_mtime
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
            cache_key = f"E::{e['screenshot_id']}"
            tokens.pop(cache_key, None)
            e["screenshot_token"] = upload(cache_key, e["local_path"], "620")
    save_json(IMAGE_TOKEN_PATH, tokens)
    save_json(DATA / "exam_records_v5.json", exams)
    save_json(DATA / "screenshot_evidence_v5.json", evidence)
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



def build_exam_anchor(q: dict) -> str:
    if q.get("problem_tags"):
        focus = str(q["problem_tags"]).split("、")[0]
        return f"{q.get('primary_type', '综合题')}——{focus}"
    if q.get("knowledge_tags"):
        focus = str(q["knowledge_tags"]).split("、")[0]
        return f"{q.get('primary_type', '综合题')}——{focus}"
    core = split_tags(q.get("core_experiment_models", ""))
    task = split_tags(q.get("task_models", ""))
    method = split_tags(q.get("method_models", ""))
    bg = split_tags(q.get("background_tags", ""))
    visual = split_tags(q.get("visual_forms", ""))
    focus = ""
    if core:
        focus = next(iter(core))
    elif task:
        focus = next(iter(task))
    elif method:
        focus = next(iter(method))
    elif bg:
        focus = next(iter(bg))
    elif visual:
        focus = next(iter(visual))
    elif q.get("knowledge_tags"):
        focus = str(q["knowledge_tags"]).split("、")[0]
    return f"{q.get('primary_type', '综合题')}——{focus or '综合考查'}"


def coverage_status(q: dict, ms: list[dict]) -> str:
    if ms:
        return "✅"
    if q.get("choice_entry_filter") == "是" or q.get("basic_common_choice_filter") == "是":
        return "—"
    return "❌"


def onion_reference(ms: list[dict]) -> str:
    if not ms:
        return "无对应洋葱解题课"
    return "；".join(m["video_name"] for m in ms[:3])


def build_hit_summary(q: dict, m: dict | None = None) -> str:
    score = q.get("score")
    score_txt = f"{int(score)}分，" if score else ""
    focus_parts = []
    if q.get("problem_tags"):
        focus_parts.append(str(q["problem_tags"]).split("、")[0])
    elif q.get("knowledge_tags"):
        focus_parts.append(str(q["knowledge_tags"]).split("、")[0])
    for key in ("core_experiment_models", "task_models", "method_models", "visual_forms", "background_tags"):
        tags = split_tags(q.get(key, ""))
        if tags:
            focus_parts.append(next(iter(tags)))
            break
    if not focus_parts and q.get("knowledge_tags"):
        focus_parts.append(str(q["knowledge_tags"]).split("、")[0])
    label = focus_parts[0] if focus_parts else q.get("primary_type", "综合题")
    verb = "讲了！" if m and m.get("score", 0) < 130 else "命中！"
    return f"{label}，{score_txt}{verb}"


def build_docs(exams: list[dict], videos: list[dict], evidence: list[dict], matches: list[dict], base_url: str = "", exam_tag_url: str = "") -> list[dict]:
    video_map = {v["video_id"]: v for v in videos}
    ev_map = {e["screenshot_id"]: e for e in evidence}
    final_by_q: dict[str, list[dict]] = {}
    promo_by_q: dict[str, list[dict]] = {}
    for m in matches:
        if not m["final_show"]:
            continue
        final_by_q.setdefault(m["question_id"], []).append(m)
        if not m.get("comparison_only"):
            promo_by_q.setdefault(m["question_id"], []).append(m)
    by_paper: dict[str, list[dict]] = {}
    for q in exams:
        by_paper.setdefault(q["paper"], []).append(q)
    manifest, examples = [], []
    for paper, qs in sorted(by_paper.items()):
        qs.sort(key=lambda x: x["qnum"])
        hit_qs = [q for q in qs if promo_by_q.get(q["question_id"])]
        total_score = sum(q.get("score") or 0 for q in qs)
        hit_score = sum(q.get("score") or 0 for q in hit_qs)
        analysis_rows, compare_rows = [], []
        rendered_groups = set()
        for q in qs:
            promo_ms = promo_by_q.get(q["question_id"], [])
            ms = sort_matches_for_question(q, final_by_q.get(q["question_id"], []))
            analysis_rows.append(
                [
                    escape(q["primary_type"]),
                    escape(q.get("topic_group") or str(q["qnum"])),
                    escape(str(q.get("score") or "")),
                    escape(build_exam_anchor(q)),
                    escape(coverage_status(q, promo_ms)),
                    escape(onion_reference(promo_ms) if promo_ms else ("基础题未进宣传版" if q.get("choice_entry_filter") == "是" or q.get("basic_common_choice_filter") == "是" else "无对应洋葱解题课")),
                ]
            )
            if q.get("compare_table_show") is False:
                continue
            comp_table_ids = q.get("compare_table_video_ids")
            if comp_table_ids:
                comp_set = set(comp_table_ids)
                ms = [m for m in ms if m.get("video_id") in comp_set]
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
                anchor_q = group_qs[0] if group_qs else q
                for m in sort_matches_for_question(anchor_q, group_ms):
                    if m["video_id"] not in seen:
                        seen.add(m["video_id"])
                        ms.append(m)
                label = f"第{group_key.replace('Q', '')}题组"
            else:
                label = f"第{q['qnum']}题"
            left = img_tag(q.get("pdf_crop_token", ""), 560, f"{safe_slug(paper)}_{label}.png")
            if not q.get("pdf_crop_token"):
                left = f"<p><b>{escape(label)}</b></p><p>{escape(q['stem'][:500])}</p>"
            right_parts = []
            total_imgs = 0
            notes_map = q.get("video_similarity_notes") or {}
            for m in ms[:6]:
                right_parts.append(f"<p><b>{escape(build_hit_summary(q, m))}</b></p><p>{escape(m['video_name'])}</p>")
                custom_note = notes_map.get(m["video_id"])
                if custom_note:
                    right_parts.append(f"<p>{escape(custom_note)}</p>")
                ids = [sid for sid in m["screenshot_ids"].split("；") if sid]
                notes = [n for n in m["screenshot_notes"].split("；") if n]
                for i, sid in enumerate(ids):
                    if total_imgs >= 6:
                        break
                    ev = ev_map.get(sid, {})
                    right_parts.append(img_tag(ev.get("screenshot_token", ""), 520, f"{sid}.png"))
                    note = notes[i] if i < len(notes) else ""
                    if note and i == 0:
                        right_parts.append(f"<p>{escape(note)}</p>")
                    total_imgs += 1
                examples.append({"paper": paper, "qnum": q["qnum"], "type": q["primary_type"], "video": m["video_name"], "reason": build_hit_summary(q, m)})
            compare_rows.append([left, "".join(right_parts)])
        tag_para = f'<p>逐题打标库：<a href="{escape(exam_tag_url)}">2026中考化学试卷逐题打标 v5</a></p>' if exam_tag_url else ""
        base_para = f'<p>过程库：<a href="{escape(base_url)}">2026中考化学押题工作台v5</a></p>' if base_url else ""
        xml = "\n".join(
            [
                f"<title>洋葱学园 VS {escape(paper)} 押题对比（v5）</title>",
                "<h1>基本认识</h1>",
                f"<p>本卷共 {len(qs)} 道题。宣传版仅展示有对应洋葱解题课、且满足相似题规则的题目；基础选择题与泛判断题不纳入内容对比。</p>",
                f"<p><b>宣传版命中：</b>{len(hit_qs)}/{len(qs)} 题；按分值估算覆盖 {hit_score}/{total_score or '未知'}。</p>",
                tag_para,
                base_para,
                "<h1>试卷分析表</h1>",
                html_table(analysis_rows, ["题型", "题号", "分值", "考点", "题型覆盖", "洋葱对应内容"], [90, 70, 50, 260, 70, 340]),
                "<h1>内容对比表</h1>",
                html_table(compare_rows or [["<p>本卷暂无进入宣传版的命中题。</p>", ""]], ["中考题目", "洋葱内容"], [560, 560]),
                f"<p><b>本卷宣传版覆盖分值：{hit_score}/{total_score or '未知'}</b></p>",
            ]
        )
        path = DOCS / f"{safe_slug(paper)}_v5.xml"
        path.write_text(xml, encoding="utf-8")
        manifest.append({"title": paper, "local_xml": str(path), "question_count": len(qs), "hit_count": len(hit_qs), "total_score": total_score, "hit_score": hit_score})
    save_json(DATA / "examples_v5.json", examples)
    save_json(DATA / "docs_manifest_pre_publish.json", manifest)
    return manifest



EXAM_TAG_SCHEMA = [
    {"name": "题目ID", "type": "text"}, {"name": "试卷", "type": "text"}, {"name": "题号", "type": "number", "style": {"type": "plain", "precision": 0}},
    {"name": "分值", "type": "number", "style": {"type": "plain", "precision": 0}}, {"name": "一级题型", "type": "text"},
    {"name": "知识点标签", "type": "text"}, {"name": "问题标签", "type": "text"}, {"name": "难度", "type": "number", "style": {"type": "plain", "precision": 0}},
    {"name": "视觉形态", "type": "text"}, {"name": "设问任务模型", "type": "text"}, {"name": "解法模型", "type": "text"},
    {"name": "核心实验模型", "type": "text"}, {"name": "选择题入口过滤", "type": "text"}, {"name": "基础常识题过滤", "type": "text"},
    {"name": "PDF裁图路径", "type": "text"}, {"name": "题干", "type": "text"},
]


def create_exam_tag_base() -> dict:
    info_path = FEISHU / "exam_tag_base_info_v5.json"
    if info_path.exists():
        return load_json(info_path)
    res = run_json(
        [
            "lark-cli", "base", "+base-create", "--as", "user", "--name", "2026中考化学试卷逐题打标 v5",
            "--time-zone", "Asia/Shanghai", "--table-name", "逐题打标", "--fields", json.dumps(EXAM_TAG_SCHEMA, ensure_ascii=False), "--format", "json",
        ],
        timeout=240,
    )
    text_blob = json.dumps(res["data"], ensure_ascii=False)
    m = re.search(r'"app_token"\s*:\s*"([^"]+)"', text_blob) or re.search(r'"base_token"\s*:\s*"([^"]+)"', text_blob)
    if not m:
        raise RuntimeError(f"Cannot find exam tag base token: {text_blob}")
    base_token = m.group(1)
    tables = run_json(["lark-cli", "base", "+table-list", "--as", "user", "--base-token", base_token, "--format", "json"])["data"]
    table_id = ""
    for t in tables.get("items", []) or tables.get("tables", []):
        if (t.get("name") or t.get("table_name")) == "逐题打标":
            table_id = t.get("table_id") or t.get("id") or ""
            break
    base_url = res["data"].get("url") or f"https://guanghe.feishu.cn/base/{base_token}"
    info = {"base_token": base_token, "base_url": base_url, "table_id": table_id}
    save_json(info_path, info)
    return info


def populate_exam_tag_base(info: dict) -> None:
    done = FEISHU / "exam_tag_records_v5.json"
    if done.exists():
        return
    exams = load_json(DATA / "exam_records_v5.json", [])
    fields = [f["name"] for f in EXAM_TAG_SCHEMA]
    rows = [
        [
            r["question_id"], r["paper"], r["qnum"], r.get("score") or 0, r["primary_type"],
            r.get("knowledge_tags", ""), r.get("problem_tags", ""), r.get("difficulty") or 0,
            r.get("visual_forms", ""), r.get("task_models", ""), r.get("method_models", ""),
            r.get("core_experiment_models", ""), r.get("choice_entry_filter", "否"), r.get("basic_common_choice_filter", "否"),
            r.get("pdf_crop_path", ""), r.get("stem", "")[:90000],
        ]
        for r in exams
    ]
    batches = batch_payload(PAYLOADS / "exam_tag_v5.json", fields, rows)
    status = []
    for p in batches:
        res = run_json(["lark-cli", "base", "+record-batch-create", "--as", "user", "--base-token", info["base_token"], "--table-id", info["table_id"], "--json", f"@{p.relative_to(ROOT)}", "--format", "json"], timeout=240)
        status.append({"payload": str(p), "data": res["data"]})
        time.sleep(0.25)
    save_json(done, status)


BASE_SCHEMA = {
    "试卷题库v5": [
        {"name": "题目ID", "type": "text"}, {"name": "试卷", "type": "text"}, {"name": "题号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "一级题型", "type": "text"}, {"name": "视觉形态", "type": "text"}, {"name": "背景素材", "type": "text"},
        {"name": "设问任务", "type": "text"}, {"name": "设问任务模型", "type": "text"}, {"name": "解法模型", "type": "text"}, {"name": "核心实验模型", "type": "text"},
        {"name": "项目式任务链", "type": "text"}, {"name": "宣传可见性", "type": "text"}, {"name": "基础常识题过滤", "type": "text"},
        {"name": "选择题入口过滤", "type": "text"}, {"name": "选择题保留理由", "type": "text"},
        {"name": "视觉标签证据来源", "type": "text"}, {"name": "误触发关键词", "type": "text"}, {"name": "PDF裁图", "type": "text"},
        {"name": "题干", "type": "text"},
    ],
    "视频截图证据库v5": [
        {"name": "截图ID", "type": "text"}, {"name": "视频ID", "type": "text"}, {"name": "视频名称", "type": "text"},
        {"name": "来源", "type": "text"}, {"name": "来源行号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "截图列", "type": "text"}, {"name": "截图Token", "type": "text"}, {"name": "视觉形态", "type": "text"},
        {"name": "背景素材", "type": "text"}, {"name": "设问任务", "type": "text"}, {"name": "设问任务模型", "type": "text"}, {"name": "解法模型", "type": "text"}, {"name": "质检状态", "type": "text"},
    ],
    "视频候选库v5": [
        {"name": "视频ID", "type": "text"}, {"name": "来源", "type": "text"}, {"name": "来源行号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "视频名称", "type": "text"}, {"name": "一级题型", "type": "text"}, {"name": "视觉形态", "type": "text"},
        {"name": "背景素材", "type": "text"}, {"name": "设问任务", "type": "text"}, {"name": "设问任务模型", "type": "text"}, {"name": "解法模型", "type": "text"},
        {"name": "逐字稿文件", "type": "text"}, {"name": "逐字稿匹配状态", "type": "text"}, {"name": "文件名分", "type": "number"}, {"name": "正文分", "type": "number"}, {"name": "逐字稿证据", "type": "text"},
    ],
    "逐字稿匹配审核库v5": [
        {"name": "视频ID", "type": "text"}, {"name": "来源", "type": "text"}, {"name": "来源行号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "视频名称", "type": "text"}, {"name": "候选逐字稿ID", "type": "text"}, {"name": "候选逐字稿文件", "type": "text"},
        {"name": "文件名分", "type": "number"}, {"name": "正文分", "type": "number"}, {"name": "采用状态", "type": "text"}, {"name": "拒绝原因", "type": "text"}, {"name": "匹配说明", "type": "text"},
    ],
    "匹配审计库v5": [
        {"name": "匹配ID", "type": "text"}, {"name": "题目ID", "type": "text"}, {"name": "试卷", "type": "text"}, {"name": "题号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "题型", "type": "text"}, {"name": "视频ID", "type": "text"}, {"name": "视频名称", "type": "text"}, {"name": "视频题型", "type": "text"},
        {"name": "评分", "type": "number"}, {"name": "命中说明", "type": "text"}, {"name": "截图ID列表", "type": "text"}, {"name": "拒绝原因", "type": "text"},
        {"name": "第一眼是否像", "type": "text"}, {"name": "同题型池", "type": "text"}, {"name": "命中说明是否具体", "type": "text"},
        {"name": "多截图分工", "type": "text"}, {"name": "是否仅同章节", "type": "text"}, {"name": "基础常识题过滤", "type": "text"},
        {"name": "选择题入口过滤", "type": "text"}, {"name": "选择题保留理由", "type": "text"},
        {"name": "视觉标签证据来源", "type": "text"}, {"name": "误触发关键词", "type": "text"}, {"name": "终审结论", "type": "text"}, {"name": "终审理由", "type": "text"}, {"name": "进入最终文档", "type": "checkbox"},
    ],
    "v5相似题规则库": [
        {"name": "规则ID", "type": "text"}, {"name": "规则名称", "type": "text"}, {"name": "规则内容", "type": "text"},
    ],
}


def create_base() -> dict:
    info_path = FEISHU / "base_info_v5.json"
    if info_path.exists():
        return load_json(info_path)
    res = run_json(
        [
            "lark-cli", "base", "+base-create", "--as", "user", "--name", "2026中考化学押题工作台v5",
            "--time-zone", "Asia/Shanghai", "--table-name", "试卷题库v5", "--fields", json.dumps(BASE_SCHEMA["试卷题库v5"], ensure_ascii=False), "--format", "json",
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
    if table == "试卷题库v5":
        fields = ["题目ID", "试卷", "题号", "一级题型", "视觉形态", "背景素材", "设问任务", "设问任务模型", "解法模型", "核心实验模型", "项目式任务链", "宣传可见性", "基础常识题过滤", "选择题入口过滤", "选择题保留理由", "视觉标签证据来源", "误触发关键词", "PDF裁图", "题干"]
        rows = [[r["question_id"], r["paper"], r["qnum"], r["primary_type"], r["visual_forms"], r["background_tags"], r["task_tags"], r.get("task_models", ""), r["method_models"], r["core_experiment_models"], r["project_chain"], r["promotion_visibility"], r.get("basic_common_choice_filter", "否"), r.get("choice_entry_filter", "否"), r.get("choice_entry_reason", ""), r.get("visual_evidence_source", ""), r.get("false_trigger_keywords", ""), r["pdf_crop_path"], r["stem"][:90000]] for r in load_json(DATA/"exam_records_v5.json", [])]
    elif table == "视频截图证据库v5":
        fields = ["截图ID", "视频ID", "视频名称", "来源", "来源行号", "截图列", "截图Token", "视觉形态", "背景素材", "设问任务", "设问任务模型", "解法模型", "质检状态"]
        rows = [[r["screenshot_id"], r["video_id"], r["video_name"], r["source"], r.get("source_row") or 0, r.get("screenshot_col", ""), r.get("screenshot_token", ""), r.get("visual_forms", ""), r.get("background_tags", ""), r.get("task_tags", ""), r.get("task_models", ""), r.get("method_models", ""), r.get("quality_status", "")] for r in load_json(DATA/"screenshot_evidence_v5.json", [])]
    elif table == "视频候选库v5":
        fields = ["视频ID", "来源", "来源行号", "视频名称", "一级题型", "视觉形态", "背景素材", "设问任务", "设问任务模型", "解法模型", "逐字稿文件", "逐字稿匹配状态", "文件名分", "正文分", "逐字稿证据"]
        rows = [[r["video_id"], r.get("source", ""), r.get("source_row") or 0, r["video_name"], r["primary_type"], r["visual_forms"], r["background_tags"], r["task_tags"], r.get("task_models", ""), r["method_models"], r.get("transcript_file_v5", ""), r.get("transcript_match_status_v5", ""), r.get("transcript_file_score_v5") or 0, r.get("transcript_body_score_v5") or 0, r.get("transcript_evidence", "")[:90000]] for r in load_json(DATA/"video_records_v5.json", [])]
    elif table == "逐字稿匹配审核库v5":
        fields = ["视频ID", "来源", "来源行号", "视频名称", "候选逐字稿ID", "候选逐字稿文件", "文件名分", "正文分", "采用状态", "拒绝原因", "匹配说明"]
        rows = [[r["video_id"], r["source"], r["source_row"], r["video_name"], r["candidate_transcript_id"], r["candidate_file"], r["file_name_score"], r["body_score"], r["adopt_status"], r["reject_reason"], r["match_reason"]] for r in load_json(DATA/"transcript_match_audit_v5.json", [])]
    elif table == "匹配审计库v5":
        fields = ["匹配ID", "题目ID", "试卷", "题号", "题型", "视频ID", "视频名称", "视频题型", "评分", "命中说明", "截图ID列表", "拒绝原因", "第一眼是否像", "同题型池", "命中说明是否具体", "多截图分工", "是否仅同章节", "基础常识题过滤", "选择题入口过滤", "选择题保留理由", "视觉标签证据来源", "误触发关键词", "终审结论", "终审理由", "进入最终文档"]
        rows = [[r["match_id"], r["question_id"], r["paper"], r["qnum"], r["primary_type"], r["video_id"], r["video_name"], r["video_type"], r["score"], r["hit_reason"], r["screenshot_ids"], r["reject_reason"], r.get("first_eye_similarity", ""), r.get("same_type_pool", ""), r.get("specific_reason", ""), r.get("multi_screenshot_roles", ""), r.get("only_same_chapter", ""), r.get("basic_common_choice_filter", ""), r.get("choice_entry_filter", ""), r.get("choice_entry_reason", ""), r.get("visual_evidence_source", ""), r.get("false_trigger_keywords", ""), r.get("final_review", ""), r.get("review_reason", ""), r["final_show"]] for r in load_json(DATA/"matches_v5.json", [])]
    else:
        fields = ["规则ID", "规则名称", "规则内容"]
        rows = [
            ["V5-01", "选择题收紧总原则", "选择题只保留五类：坐标图像题、表格计算题、含流程图题、离子/物质共存题、科普材料题组；其他选择题默认只进过程库。"],
            ["V5-02", "取消泛判断正误", "取消“判断正误/正确的是/错误的是”作为设问任务同构依据，避免把外观、知识点、方法都不像的题硬匹配。"],
            ["V5-03", "循环利用语境区分", "生活语境的垃圾分类/可回收物循环利用不等同于工艺流程中的可循环物质判断。"],
            ["V5-04", "视觉证据门槛", "没有真实流程图/装置图/曲线/表格证据时，不允许仅凭关键词进入工艺流程、科学探究或项目式题最终展示。"],
            ["V5-05", "逐字稿匹配优先级", "先看视频名称与逐字稿文件名相似度；文件名不能确认时再用正文匹配。弱匹配只进审核库。"],
            ["V5-06", "题组与跨页", "识别公共材料题组和跨页长题；左列使用完整PDF原卷裁图。"],
        ]
    return fields, rows


def populate_base(info: dict) -> None:
    done = FEISHU / "base_records_v5.json"
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


def overwrite_doc(doc_id: str, xml: str) -> dict:
    return run_json(
        [
            "lark-cli",
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--as",
            "user",
            "--doc",
            doc_id,
            "--command",
            "overwrite",
            "--content",
            "-",
            "--format",
            "json",
        ],
        input_text=xml,
        timeout=300,
    )["data"]


def fetch_doc_content(doc_id: str) -> str:
    res = run_json(
        [
            "lark-cli",
            "docs",
            "+fetch",
            "--api-version",
            "v2",
            "--as",
            "user",
            "--doc",
            doc_id,
            "--detail",
            "with-ids",
            "--format",
            "json",
        ],
        timeout=120,
    )
    return res["data"]["document"]["content"]


def table_block_id_after_heading(doc_content: str, heading: str) -> str:
    m = re.search(rf'<h1[^>]*>{re.escape(heading)}</h1>\s*<table id="([^"]+)"', doc_content)
    if not m:
        raise RuntimeError(f"未找到「{heading}」后的 table block id")
    return m.group(1)


def extract_local_section(local_xml: str, start_h1: str, end_h1: str) -> str:
    start = f"<h1>{start_h1}</h1>"
    end = f"<h1>{end_h1}</h1>"
    si = local_xml.find(start)
    if si < 0:
        raise RuntimeError(f"本地 XML 缺少 {start_h1}")
    si = local_xml.find(">", si) + 1
    ei = local_xml.find(end, si)
    if ei < 0:
        raise RuntimeError(f"本地 XML 缺少 {end_h1}")
    chunk = local_xml[si:ei].strip()
    if "<img" in chunk.lower():
        raise RuntimeError(f"本地 {start_h1} 片段含 img，拒绝同步")
    return chunk


def patch_doc_block(doc_id: str, block_id: str, content: str) -> None:
    run_json(
        [
            "lark-cli",
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--as",
            "user",
            "--doc",
            doc_id,
            "--command",
            "block_replace",
            "--block-id",
            block_id,
            "--content",
            "-",
            "--format",
            "json",
        ],
        input_text=content,
        timeout=300,
    )


def patch_doc_str_replace(doc_id: str, pattern: str, content: str) -> None:
    run_json(
        [
            "lark-cli",
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--as",
            "user",
            "--doc",
            doc_id,
            "--command",
            "str_replace",
            "--pattern",
            pattern,
            "--content",
            content,
            "--format",
            "json",
        ],
        timeout=120,
    )


def sync_paper_text_tables(item: dict) -> None:
    doc_id = item["doc_id"]
    local_xml = Path(item["local_xml"]).read_text(encoding="utf-8")
    remote_before = fetch_doc_content(doc_id)
    compare_table_id = table_block_id_after_heading(remote_before, "内容对比表")
    analysis_table_id = table_block_id_after_heading(remote_before, "试卷分析表")
    imgs_before = remote_before.count("<img")
    compare_idx = remote_before.find(f'<table id="{compare_table_id}"')
    compare_before = remote_before[compare_idx : remote_before.find("</table>", compare_idx) + 8]

    new_analysis = extract_local_section(local_xml, "试卷分析表", "内容对比表")
    patch_doc_block(doc_id, analysis_table_id, new_analysis)

    remote_after = fetch_doc_content(doc_id)
    compare_table_id_after = table_block_id_after_heading(remote_after, "内容对比表")
    if compare_table_id_after != compare_table_id:
        raise RuntimeError(f"{item['title']}: 内容对比表 block id 变化，已中止")
    compare_idx_after = remote_after.find(f'<table id="{compare_table_id}"')
    compare_after = remote_after[compare_idx_after : remote_after.find("</table>", compare_idx_after) + 8]
    if compare_after != compare_before:
        raise RuntimeError(f"{item['title']}: 内容对比表内容被改动，已中止")
    imgs_after = remote_after.count("<img")
    if imgs_after != imgs_before:
        raise RuntimeError(f"{item['title']}: 截图数量变化 {imgs_before}->{imgs_after}，已中止")

    for pattern in (
        r"<p><b>宣传版命中：</b>.*?</p>",
        r"<p><b>本卷宣传版覆盖分值：.*?</p>",
    ):
        local_m = re.search(pattern, local_xml)
        remote_m = re.search(pattern, remote_after)
        if local_m and remote_m and local_m.group(0) != remote_m.group(0):
            patch_doc_str_replace(doc_id, remote_m.group(0), local_m.group(0))

    print(
        f"sync-text {item['title']}: 试卷分析表+统计已更新（内容对比表未动，img={imgs_after}）",
        flush=True,
    )


def sync_text_only(paper_titles: list[str] | None = None) -> None:
    global PAPER_TAG_OVERRIDES
    PAPER_TAG_OVERRIDES = load_paper_tag_overrides()
    exams = load_json(DATA / "exam_records_v5.json", None)
    videos = load_json(DATA / "video_records_v5.json", None)
    evidence = load_json(DATA / "screenshot_evidence_v5.json", None)
    matches = load_json(DATA / "matches_v5.json", None)
    if not all([exams, videos, evidence, matches]):
        raise RuntimeError("缺少 v5 数据，请先运行 --prepare")
    base_info = load_json(FEISHU / "base_info_v5.json", {"base_url": ""})
    exam_tag = load_json(FEISHU / "exam_tag_base_info_v5.json", {"base_url": ""})
    manifest = build_docs(exams, videos, evidence, matches, base_info.get("base_url", ""), exam_tag.get("base_url", ""))
    urls = load_json(URLS_PATH, {})
    manifest2: list[dict] = []
    for item in manifest:
        item = dict(item)
        if item["title"] in urls:
            item["url"] = urls[item["title"]]["url"]
            item["doc_id"] = urls[item["title"]]["doc_id"]
        manifest2.append(item)
    targets = set(paper_titles) if paper_titles else None
    for item in manifest2:
        if not item.get("doc_id"):
            continue
        if targets and item["title"] not in targets:
            continue
        sync_paper_text_tables(item)
    save_json(DATA / "docs_manifest_published.json", manifest2)
    rebuild_summary_doc([m for m in manifest2 if m.get("url")], base_info.get("base_url", ""), urls)
    print("updated summary doc (text only, no compare screenshots)", flush=True)


def rebuild_summary_doc(manifest: list[dict], base_url: str, urls: dict) -> None:
    summary_title = "2026 中考化学押题对比汇总（v5）"
    rows = [
        [
            f'<a href="{escape(p["url"])}">{escape(p["title"])}</a>',
            escape(f"{p['hit_count']}/{p['question_count']}"),
            escape(f"{p['hit_score']}/{p['total_score'] or '未知'}"),
        ]
        for p in manifest
        if p.get("url")
    ]
    examples = load_json(DATA / "examples_v5.json", [])[:40]
    ex_rows = [
        [escape(e["paper"]), escape(str(e["qnum"])), escape(e["type"]), escape(e["video"]), escape(e["reason"][:180])]
        for e in examples
    ]
    xml = "\n".join(
        [
            "<title>2026 中考化学押题对比汇总（v5）</title>",
            "<h1>总体说明</h1>",
            "<p>v5 选择题收紧版取消“判断正误”泛任务同构；选择题只保留坐标图像、表格计算、含流程图、离子/物质共存、科普材料题组五类入口，其余选择题只留过程库。</p>",
            f'<p>过程库：<a href="{escape(base_url)}">2026中考化学押题工作台v5</a></p>',
            "<h1>分卷文档链接</h1>",
            html_table(rows, ["试卷", "最终展示命中题数", "按分值估算覆盖"], [420, 140, 140]),
            "<h1>典型案例</h1>",
            html_table(ex_rows or [["暂无", "", "", "", ""]], ["试卷", "题号", "题型", "洋葱证据", "命中说明"], [210, 55, 110, 220, 420]),
        ]
    )
    path = DOCS / "2026中考化学押题对比汇总_v5.xml"
    path.write_text(xml, encoding="utf-8")
    if summary_title in urls:
        overwrite_doc(urls[summary_title]["doc_id"], xml)
    else:
        urls[summary_title] = create_doc_from_xml(xml)
    save_json(URLS_PATH, urls)


def republish_papers(paper_titles: list[str]) -> None:
    global PAPER_TAG_OVERRIDES
    PAPER_TAG_OVERRIDES = load_paper_tag_overrides()
    exams = load_json(DATA / "exam_records_v5.json", None)
    videos = load_json(DATA / "video_records_v5.json", None)
    evidence = load_json(DATA / "screenshot_evidence_v5.json", None)
    matches = load_json(DATA / "matches_v5.json", None)
    if not all([exams, videos, evidence, matches]):
        raise RuntimeError("缺少 v5 数据，请先运行 --prepare")
    base_info = load_json(FEISHU / "base_info_v5.json", {"base_url": ""})
    exam_tag = load_json(FEISHU / "exam_tag_base_info_v5.json", {"base_url": ""})
    manifest = build_docs(exams, videos, evidence, matches, base_info.get("base_url", ""), exam_tag.get("base_url", ""))
    urls = load_json(URLS_PATH, {})
    targets = set(paper_titles)
    manifest2 = []
    for item in manifest:
        item = dict(item)
        if item["title"] in urls:
            item["url"] = urls[item["title"]]["url"]
            item["doc_id"] = urls[item["title"]]["doc_id"]
        manifest2.append(item)
        if item["title"] not in targets:
            continue
        paper_cfg = PAPER_TAG_OVERRIDES.get(item["title"], {})
        if paper_cfg.get("feishu_manual_compare"):
            print(f"SKIP {item['title']}: feishu_manual_compare（对比表右列已定稿，勿 overwrite）", flush=True)
            continue
        if item["title"] not in urls:
            raise RuntimeError(f"未找到已发布文档：{item['title']}")
        xml = Path(item["local_xml"]).read_text(encoding="utf-8")
        overwrite_doc(urls[item["title"]]["doc_id"], xml)
        print(f"updated {item['title']}: {urls[item['title']]['url']}", flush=True)
    save_json(DATA / "docs_manifest_published.json", manifest2)
    rebuild_summary_doc([m for m in manifest2 if m.get("url")], base_info.get("base_url", ""), urls)
    print("updated summary doc", flush=True)


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
    summary_title = "2026 中考化学押题对比汇总（v5）"
    if summary_title not in urls:
        rebuild_summary_doc(manifest2, base_url, urls)
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
    save_json(FEISHU / "verification_v5.json", checks)
    return checks


def update_source_sheets(base_info: dict, videos: list[dict]) -> None:
    status_path = FEISHU / "source_sheet_updates_v5.json"
    if status_path.exists():
        return
    start_cols = {"新中考培优": "CH", "重难点培优": "CB", "教材同步": "DI"}
    headers = prof.AI_SHEET_HEADERS
    by_source_row = {(v.get("source"), v.get("source_row")): v for v in videos}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    updates = {}
    for source, cfg in prof.SOURCE_WRITE_CONFIG.items():
        rows = []
        if cfg["header_rows"] == 2:
            rows.append(["AI过程字段"] + [""] * (len(headers) - 1))
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
                rows.append([
                    v.get("transcript_match_status_v5", ""),
                    v.get("transcript_file_v5", ""),
                    v["primary_type"],
                    v.get("knowledge_tags", v.get("visual_forms", "")),
                    v.get("problem_tags", v.get("task_models", "")),
                    str(v.get("difficulty") or ""),
                    v.get("method_models", ""),
                    v.get("key_constraints", ""),
                    v.get("transcript_evidence", "")[:500],
                    v.get("choice_entry_filter", ""),
                    base_info["base_url"],
                    now,
                ])
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
    exams = load_json(DATA / "exam_records_v5.json", None)
    videos = load_json(DATA / "video_records_v5.json", None)
    evidence = load_json(DATA / "screenshot_evidence_v5.json", None)
    matches = load_json(DATA / "matches_v5.json", None)
    if not all([exams, videos, evidence, matches]):
        exams, videos, evidence, matches = prepare()
    base_info = create_base()
    populate_base(base_info)
    exam_tag = create_exam_tag_base()
    populate_exam_tag_base(exam_tag)
    manifest = build_docs(exams, videos, evidence, matches, base_info["base_url"], exam_tag.get("base_url", ""))
    urls = publish_docs(manifest, base_info["base_url"])
    checks = verify_docs(urls)
    update_source_sheets(base_info, videos)
    save_json(DATA / "publish_summary_v5.json", {"base": base_info, "urls": urls, "checks": checks})
    if (FEISHU / "lark_cli_update_notice.json").exists():
        subprocess.run(["lark-cli", "update"], cwd=ROOT, text=True, capture_output=True, timeout=300)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--republish-paper", action="append", default=[])
    parser.add_argument(
        "--sync-text-only",
        action="store_true",
        help="仅更新分卷「试卷分析表」与汇总文字表；block_replace 分析表并校验内容对比表未变",
    )
    parser.add_argument("--sync-text-only-paper", action="append", default=[], help="限定 --sync-text-only 的卷名")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    if args.republish_paper:
        republish_papers(args.republish_paper)
    if args.sync_text_only:
        sync_text_only(args.sync_text_only_paper or None)
    if args.publish:
        publish()
    if not args.prepare and not args.publish and not args.republish_paper and not args.sync_text_only:
        prepare()


if __name__ == "__main__":
    main()
