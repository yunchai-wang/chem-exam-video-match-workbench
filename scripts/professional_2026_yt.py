#!/usr/bin/env python3
"""Professional 2026 chemistry exam vs Onion-course comparison pipeline.

This script intentionally favors precision over coverage. It builds a process
Base from auditable labels, then publishes final Feishu docs from accepted
matches only.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from typing import Any, Iterable

from docx import Document

import compare_2026_yt as old_base
import publish_2026_yt_image_docs as old_image


ROOT = Path("/Users/mbpro/Desktop/AI/chem-exam-analysis")
WORK = ROOT / "outputs" / "2026_yt_professional"
RAW = WORK / "raw"
DATA = WORK / "data"
DOCS = WORK / "docs"
FEISHU = WORK / "feishu"
PAYLOADS = WORK / "payloads"
TRANSCRIPT_ROOT = Path("/Users/mbpro/Desktop/洋葱/AI学稿子提升审美/所有稿子汇总")
QUESTION_TOKENS_PATH = ROOT / "outputs" / "2026_yt_compare_image" / "feishu" / "question_image_tokens.json"
OLD_IMAGE_WORK = ROOT / "outputs" / "2026_yt_compare_image"

RULE_DOC = "https://guanghe.feishu.cn/docx/AQPjdCbcwoqjnlxspOgcuqQWnnd"
MATH_REF_DOC = "https://guanghe.feishu.cn/wiki/ECmKwRrvViyAGWk5d8lcs7Zkntc"

SOURCE_WRITE_CONFIG = {
    "新中考培优": {
        "url": old_base.SHEET_URLS["新中考培优"],
        "sheet_id": "L43kZ6",
        "raw": "xinzhongkao_peiyou.json",
        "header_rows": 2,
        "start_col": "AW",
        "max_row": 227,
    },
    "重难点培优": {
        "url": old_base.SHEET_URLS["重难点培优"],
        "sheet_id": "jG6GT9",
        "raw": "zhongnandian_peiyou.json",
        "header_rows": 1,
        "start_col": "AQ",
        "max_row": 192,
    },
    "教材同步": {
        "url": old_base.SHEET_URLS["教材同步"],
        "sheet_id": "e1mSBg",
        "raw": "jiaocai_tongbu.json",
        "header_rows": 1,
        "start_col": "BX",
        "max_row": 304,
    },
}

AI_SHEET_HEADERS = [
    "AI逐字稿匹配状态",
    "AI逐字稿文件",
    "AI视频题型标签",
    "AI知识点标签",
    "AI问题标签",
    "AI难度标签",
    "AI方法骨架",
    "AI关键约束",
    "AI工作台记录链接",
    "AI标签更新时间",
]

TYPE_WEIGHTS = {
    "基础题": ("knowledge", 0.70, "problem", 0.30),
    "基本实验题": ("knowledge", 0.50, "problem", 0.50),
    "科学探究题": ("problem", 0.70, "knowledge", 0.30),
    "推断题": ("problem", 0.70, "knowledge", 0.30),
    "计算题": ("problem", 0.70, "knowledge", 0.30),
    "科普阅读题": ("problem", 0.70, "knowledge", 0.30),
    "工艺流程题": ("problem", 0.70, "knowledge", 0.30),
    "综合应用题": ("problem", 0.50, "knowledge", 0.50),
}


@dataclass
class TranscriptRecord:
    transcript_id: str
    file_name: str
    file_path: str
    source_folder: str
    parse_status: str
    text: str
    summary: str


@dataclass
class VideoRecord:
    video_id: str
    source: str
    sheet_id: str
    source_row: int
    sheet_url: str
    video_name: str
    hierarchy: str
    screenshot_refs: str
    screenshot_tokens: str
    transcript_id: str
    transcript_file: str
    transcript_match_confidence: float
    transcript_match_status: str
    content_summary: str
    question_type: str
    knowledge_tags: str
    problem_tags: str
    difficulty: int
    method_skeleton: str
    key_constraints: str
    signatures: str
    qa_status: str


@dataclass
class ExamRecord:
    question_id: str
    paper: str
    qnum: int
    score: int
    raw_qtype: str
    question_type: str
    stem: str
    question_image_token: str
    local_images: str
    knowledge_tags: str
    problem_tags: str
    difficulty: int
    method_skeleton: str
    key_constraints: str
    signatures: str
    has_visual: bool
    qa_status: str


@dataclass
class MatchAudit:
    match_id: str
    question_id: str
    video_id: str
    paper: str
    qnum: int
    video_name: str
    source: str
    similarity_grade: str
    score: float
    hard_filter: str
    hit_dimensions: str
    reason: str
    reject_reason: str
    final_show: bool


def ensure_dirs() -> None:
    for path in [WORK, RAW, DATA, DOCS, FEISHU, PAYLOADS]:
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
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from {' '.join(cmd)}:\n{proc.stdout}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Command returned ok=false: {' '.join(cmd)}\n{json.dumps(payload, ensure_ascii=False)}")
    if payload.get("_notice", {}).get("update"):
        notice = payload["_notice"]["update"]
        save_json(FEISHU / "lark_cli_update_notice.json", notice)
    return payload


def safe_slug(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")


def normalize(text: str) -> str:
    text = old_base.normalize_text(text or "")
    return text.replace(" ", "")


def readable_list(values: Iterable[str]) -> str:
    return "、".join(dict.fromkeys([v for v in values if v]))


def tokenize_cn(text: str) -> set[str]:
    text = normalize(text)
    return set(re.findall(r"[A-Za-z][A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text))


def extract_docx_text(path: Path) -> str:
    doc = Document(path)
    parts: list[str] = []
    for para in doc.paragraphs:
        txt = old_base.normalize_text(para.text)
        if txt:
            parts.append(txt)
    for table in doc.tables:
        cells = []
        for row in table.rows:
            cells.extend(old_base.normalize_text(cell.text) for cell in row.cells if old_base.normalize_text(cell.text))
        if cells:
            parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_doc_text(path: Path) -> str:
    out = PAYLOADS / f"{safe_slug(path.stem)}.txt"
    proc = subprocess.run(["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="ignore"))
    text = proc.stdout.decode("utf-8", errors="ignore")
    out.write_text(text, encoding="utf-8")
    return "\n".join(old_base.normalize_text(line) for line in text.splitlines() if old_base.normalize_text(line))


def transcript_summary(text: str, limit: int = 420) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:limit] + ("…" if len(clean) > limit else "")


def build_transcripts() -> list[TranscriptRecord]:
    records: list[TranscriptRecord] = []
    files = sorted(TRANSCRIPT_ROOT.glob("*/*"))
    idx = 1
    for path in files:
        if path.suffix.lower() not in {".docx", ".doc"}:
            continue
        status = "解析成功"
        try:
            text = extract_docx_text(path) if path.suffix.lower() == ".docx" else extract_doc_text(path)
        except Exception as exc:
            status = f"解析失败：{exc}"
            text = ""
        records.append(
            TranscriptRecord(
                transcript_id=f"TR-{idx:03d}",
                file_name=path.name,
                file_path=str(path),
                source_folder=path.parent.name,
                parse_status=status,
                text=text,
                summary=transcript_summary(text),
            )
        )
        idx += 1
    return records


SIGNATURE_RULES: list[tuple[str, list[str], list[str], list[str], str]] = [
    ("测定空气中氧气含量", ["氧气含量", "测氧气", "红磷", "集气瓶", "空气中氧气"], ["空气与氧气"], ["装置原理分析", "误差分析", "实验改进"], "测氧气含量装置模型"),
    ("实验室制取氧气", ["制取氧气", "过氧化氢", "高锰酸钾", "氯酸钾", "二氧化锰"], ["空气与氧气"], ["气体制取", "装置选择"], "制气装置-发生收集检验"),
    ("电解水实验", ["电解水", "氢气", "氧气", "正极", "负极", "水的组成"], ["水与净化"], ["实验现象推理"], "电解水正负极与体积比"),
    ("控制变量实验", ["控制变量", "对照实验", "影响因素", "变量", "继续实验"], ["科学探究"], ["控制变量", "实验设计"], "变量控制-单一变量比较"),
    ("排除干扰因素", ["干扰因素", "排除干扰", "对照", "空白实验"], ["科学探究"], ["干扰排除", "实验设计"], "干扰因素识别与排除"),
    ("催化剂探究", ["催化剂", "催化效果", "反应速率", "过氧化氢分解"], ["空气与氧气", "科学探究"], ["催化剂探究", "控制变量"], "催化效果变量比较"),
    ("燃烧条件探究", ["燃烧条件", "可燃物", "着火点", "氧气", "燃烧"], ["燃烧与能源"], ["实验探究"], "燃烧条件三要素"),
    ("溶解度曲线", ["溶解度曲线", "溶解度", "饱和溶液", "不饱和", "结晶"], ["溶液与溶解度"], ["图像读取", "溶液状态判断"], "溶解度曲线读图"),
    ("溶液稀释与质量分数", ["溶液稀释", "溶质质量分数", "浓度", "稀释", "配制"], ["溶液与溶解度"], ["溶液计算"], "溶质守恒计算"),
    ("酸碱中和pH图像", ["pH", "中和反应", "酸碱", "滴加", "曲线"], ["酸碱盐"], ["图像分析", "反应后溶质判断"], "酸碱滴加pH曲线"),
    ("酸碱盐性质探究", ["酸碱盐", "复分解", "沉淀", "盐酸", "氢氧化钠", "碳酸钠"], ["酸碱盐"], ["实验探究", "物质成分检验"], "酸碱盐反应与现象判断"),
    ("碳酸盐鉴别", ["碳酸根", "碳酸氢盐", "碳酸盐", "石灰水", "气体检验"], ["酸碱盐", "碳与二氧化碳"], ["离子鉴别"], "碳酸盐加酸产气检验"),
    ("NaOH与CO2反应探究", ["CO2", "二氧化碳", "氢氧化钠", "NaOH", "压强", "U型管"], ["碳与二氧化碳", "酸碱盐"], ["数字化实验", "反应证明"], "CO2与NaOH吸收压强模型"),
    ("石灰水与CO2异常", ["石灰水", "二氧化碳", "异常现象", "碳酸钙", "澄清"], ["碳与二氧化碳", "酸碱盐"], ["异常现象解释"], "CO2与石灰水沉淀转化"),
    ("金属与酸图像", ["金属与酸", "氢气", "图像", "锌", "铁", "镁", "铝"], ["金属与材料"], ["图像分析", "定量比较"], "金属酸反应图像"),
    ("金属置换先后", ["金属置换", "置换反应", "活动性", "先后", "硫酸铜", "硝酸银"], ["金属与材料"], ["反应先后判断"], "金属活动性顺序置换"),
    ("金属回收工艺", ["金属回收", "废液", "回收", "金属", "流程"], ["金属与材料", "工艺流程"], ["工艺流程", "除杂回收"], "金属回收流程"),
    ("工艺流程", ["工艺流程", "流程图", "制备", "粗盐", "提纯", "废渣", "矿石"], ["工艺流程"], ["流程推断", "除杂提纯"], "工艺流程-物质转化与分离"),
    ("推断题", ["推断题", "物质推断", "框图推断", "转化关系", "均为初中化学常见物质"], ["推断"], ["物质推断"], "物质转化关系推断"),
    ("质量守恒与方程式计算", ["质量守恒", "化学方程式计算", "反应后", "质量求和"], ["化学计算"], ["守恒计算", "方程式计算"], "质量守恒-方程式定量"),
    ("表格图像综合计算", ["表格", "图像", "坐标", "数据", "计算", "质量分数"], ["化学计算"], ["数据处理", "图表计算"], "表格图像综合计算"),
    ("化合价与化学式", ["化合价", "化学式", "元素质量比", "质量分数"], ["化学用语"], ["化学式计算"], "化合价与化学式规则"),
    ("方程式配平", ["配平", "化学方程式", "缺项", "待定系数"], ["化学用语"], ["方程式配平"], "方程式守恒配平"),
    ("水的净化", ["水的净化", "过滤", "吸附", "蒸馏", "硬水", "软水"], ["水与净化"], ["实验操作", "净化流程"], "水净化操作流程"),
    ("科普阅读", ["科普阅读", "阅读理解", "资料", "信息", "短文"], ["化学与生活"], ["信息提取"], "科普阅读信息提取"),
]


def detect_signatures(text: str) -> list[str]:
    compact = normalize(text)
    hits = []

    def has(*keys: str) -> bool:
        return any(normalize(k) in compact for k in keys)

    def has_all(*keys: str) -> bool:
        return all(normalize(k) in compact for k in keys)

    if has("氧气含量", "测定空气中氧气", "测氧气") or (has("红磷", "白磷") and has("空气") and has("氧气")):
        hits.append("测定空气中氧气含量")
    if has("制取氧气", "实验室制氧") or (has("过氧化氢", "高锰酸钾", "氯酸钾") and has("氧气")):
        hits.append("实验室制取氧气")
    if has("电解水") or (has("正极", "负极") and has("氢气", "氧气") and has("水")):
        hits.append("电解水实验")
    if has("控制变量", "对照实验", "单一变量", "继续实验"):
        hits.append("控制变量实验")
    if has("干扰因素", "排除干扰", "空白实验"):
        hits.append("排除干扰因素")
    if has("催化剂", "催化效果") and has("速率", "过氧化氢", "分解", "二氧化锰"):
        hits.append("催化剂探究")
    if has("燃烧条件") or (has("可燃物", "着火点") and has("氧气", "空气")):
        hits.append("燃烧条件探究")
    if has("溶解度曲线") or (has("溶解度") and has("饱和", "不饱和", "结晶", "温度")):
        hits.append("溶解度曲线")
    if has("溶液稀释", "稀释") or (has("溶质质量分数") and has("溶液", "溶质", "质量")):
        hits.append("溶液稀释与质量分数")
    if has("pH") and has("中和", "滴加", "酸", "碱", "盐酸", "氢氧化钠"):
        hits.append("酸碱中和pH图像")
    if has("复分解") or (has("酸碱盐") and has("性质", "探究")) or (has("沉淀") and has("盐酸", "硫酸", "氢氧化钠", "碳酸钠")):
        hits.append("酸碱盐性质探究")
    if has("碳酸根", "碳酸氢盐", "碳酸盐") and has("检验", "鉴别", "石灰水", "气体"):
        hits.append("碳酸盐鉴别")
    if has("氢氧化钠", "NaOH") and has("二氧化碳", "CO2") and has("压强", "U型管", "传感器", "数字化", "反应"):
        hits.append("NaOH与CO2反应探究")
    if has("石灰水") and has("二氧化碳", "CO2") and has("异常", "沉淀", "碳酸钙"):
        hits.append("石灰水与CO2异常")
    if (has("金属与酸", "金属和酸") or (has("锌", "铁", "镁", "铝") and has("盐酸", "稀硫酸", "酸"))) and has("氢气", "图像", "曲线", "质量"):
        hits.append("金属与酸图像")
    if has("置换反应", "金属置换") or (has("活动性顺序", "金属活动性") and has("先后", "滤液", "滤渣", "硝酸银", "硫酸铜")):
        hits.append("金属置换先后")
    if has("金属回收") or (has("回收") and has("金属", "铜", "铁", "锌") and has("流程", "废液", "废渣")):
        hits.append("金属回收工艺")
    if has("工艺流程", "流程图") or (has("制备", "提纯", "除杂") and has("流程", "滤液", "滤渣", "废液", "矿石", "粗盐")):
        hits.append("工艺流程")
    if has("质量守恒", "化学方程式计算") or (has("根据化学方程式") and has("计算", "质量")):
        hits.append("质量守恒与方程式计算")
    if (has("表格", "坐标", "图像", "曲线") and has("计算", "质量分数", "反应后")) or has("表格与图像", "表格与坐标"):
        hits.append("表格图像综合计算")
    if has("化合价") or (has("化学式") and has("化合价", "质量比", "质量分数")):
        hits.append("化合价与化学式")
    if has("配平") or has("方程式缺项", "待定系数"):
        hits.append("方程式配平")
    if has("水的净化") or (has("过滤", "吸附", "蒸馏") and has("硬水", "软水", "净化")):
        hits.append("水的净化")
    if has("科普阅读", "阅读理解") or (has("资料", "短文") and has("阅读", "信息")):
        hits.append("科普阅读")

    # 推断题的 A/B/C/D 字母本身太常见，必须同时出现明确推断语境。
    if (
        "推断题" not in hits
        and re.search(r"[A-H][、,，][B-H][、,，][C-H]", text or "")
        and any(k in compact for k in ["转化", "推断", "框图", "常见物质", "反应关系"])
    ):
        hits.append("推断题")
    return hits


def tags_for_text(text: str) -> dict[str, Any]:
    compact = normalize(text)
    signatures = detect_signatures(text)
    knowledge: list[str] = []
    problems: list[str] = []
    methods: list[str] = []
    for sig, _keys, kn, pr, method in SIGNATURE_RULES:
        if sig in signatures:
            knowledge.extend(kn)
            problems.extend(pr)
            methods.append(method)

    old_topics = old_base.infer_topics(text)
    knowledge.extend(old_topics)

    if any(k in compact for k in ["工艺流程", "流程图", "制备", "回收", "废液", "废渣", "矿石", "提纯"]):
        qtype = "工艺流程题"
    elif any(k in compact for k in ["科普阅读", "阅读理解", "资料", "短文", "科技", "信息"]):
        qtype = "科普阅读题"
    elif any(k in compact for k in ["推断", "框图", "已知A", "A、B", "A~", "A～", "转化关系"]):
        qtype = "推断题"
    elif any(k in compact for k in ["计算", "质量分数", "根据化学方程式", "样品", "图像", "坐标", "表格"]) and any(k in compact for k in ["g", "质量", "分数", "数据", "曲线", "坐标", "计算"]):
        qtype = "计算题"
    elif any(k in compact for k in ["探究", "猜想", "对照", "控制变量", "影响因素", "数字化", "传感器", "实验方案"]):
        qtype = "科学探究题"
    elif any(k in compact for k in ["实验", "装置", "操作", "仪器", "制取", "过滤", "蒸发", "检验"]):
        qtype = "基本实验题"
    elif len(set(knowledge)) >= 3:
        qtype = "综合应用题"
    else:
        qtype = "基础题"

    if not problems:
        if qtype == "基础题":
            problems.append("概念辨析/性质判断")
        elif qtype == "基本实验题":
            problems.append("实验操作与现象判断")
        elif qtype == "科学探究题":
            problems.append("实验设计与证据推理")
        elif qtype == "工艺流程题":
            problems.append("流程转化与除杂提纯")
        elif qtype == "计算题":
            problems.append("定量关系建模")
        elif qtype == "推断题":
            problems.append("物质转化推断")
        elif qtype == "科普阅读题":
            problems.append("信息提取与迁移应用")
        else:
            problems.append("综合信息分析")

    if not methods:
        methods.append(TYPE_WEIGHTS.get(qtype, ("problem", 0.5, "knowledge", 0.5))[0] + "优先")

    difficulty = 1
    if qtype in {"科学探究题", "工艺流程题", "推断题", "计算题", "综合应用题"}:
        difficulty = 3
    if any(k in compact for k in ["综合", "创新", "数字化", "异常", "多次", "继续实验", "坐标", "曲线", "多步"]):
        difficulty = max(difficulty, 4)
    if len(text) > 700:
        difficulty = max(difficulty, 4)

    key_constraints = []
    for sig in signatures[:3]:
        key_constraints.append(sig)
    if any(k in compact for k in ["图", "曲线", "坐标", "表"]):
        key_constraints.append("依赖图表/数据读取")
    if any(k in compact for k in ["控制变量", "对照"]):
        key_constraints.append("单一变量与对照组")
    if any(k in compact for k in ["过量", "少量", "恰好", "完全反应"]):
        key_constraints.append("试剂用量/反应程度")

    return {
        "question_type": qtype,
        "knowledge_tags": readable_list(knowledge) or "综合/待复核",
        "problem_tags": readable_list(problems),
        "difficulty": difficulty,
        "method_skeleton": readable_list(methods),
        "key_constraints": readable_list(key_constraints) or "题干条件直接判断",
        "signatures": readable_list(signatures),
    }


def match_transcript(candidate: dict, transcripts: list[TranscriptRecord]) -> tuple[TranscriptRecord | None, float]:
    name = normalize(candidate.get("video_name") or "")
    context = normalize(" ".join([candidate.get("video_name", ""), candidate.get("hierarchy", ""), candidate.get("text", "")]))
    preferred = {
        "教材同步": "教材同步",
        "新中考培优": "新中考",
        "重难点培优": "B级重难点",
    }.get(candidate.get("source", ""), "")
    best: tuple[TranscriptRecord | None, float] = (None, 0.0)
    for tr in transcripts:
        file_key = normalize(Path(tr.file_name).stem)
        if not tr.text:
            continue
        ratio = difflib.SequenceMatcher(None, name, file_key).ratio() if name else 0
        name_tokens = tokenize_cn(name)
        file_tokens = tokenize_cn(file_key)
        overlap = len(name_tokens & file_tokens) / max(1, len(name_tokens | file_tokens))
        context_hits = sum(1 for token in list(name_tokens)[:8] if token and token in normalize(tr.text[:5000]))
        source_bonus = 0.08 if preferred and preferred in tr.source_folder else 0
        score = ratio * 0.55 + overlap * 0.25 + min(context_hits, 4) * 0.04 + source_bonus
        if score > best[1]:
            best = (tr, round(score, 3))
    return best


def build_video_records(candidates: list[old_base.Candidate], transcripts: list[TranscriptRecord], media_by_source: dict[str, dict]) -> list[VideoRecord]:
    records: list[VideoRecord] = []
    for cand in candidates:
        c = asdict(cand)
        tr, confidence = match_transcript(c, transcripts)
        transcript_text = tr.text if tr and confidence >= 0.42 else ""
        tag_text = cand.text
        if not detect_signatures(tag_text) and transcript_text and confidence >= 0.55:
            tag_text = " ".join([cand.text, transcript_text[:2500]])
        combined = " ".join([cand.text, transcript_text[:5000]])
        tags = tags_for_text(tag_text)
        sheet_images = old_image.sheet_images_for_candidate(media_by_source, c, limit=5)
        screenshot_tokens = "；".join(f"{img['col']}:{img['token']}" for img in sheet_images)
        records.append(
            VideoRecord(
                video_id=cand.cid,
                source=cand.source,
                sheet_id=SOURCE_WRITE_CONFIG[cand.source]["sheet_id"],
                source_row=cand.row,
                sheet_url=cand.sheet_url,
                video_name=cand.video_name,
                hierarchy=cand.hierarchy,
                screenshot_refs="；".join(cand.screenshot_refs),
                screenshot_tokens=screenshot_tokens,
                transcript_id=tr.transcript_id if tr else "",
                transcript_file=tr.file_name if tr else "",
                transcript_match_confidence=confidence,
                transcript_match_status="已匹配" if tr and confidence >= 0.42 else ("弱匹配待复核" if tr else "未匹配"),
                content_summary=transcript_summary(combined, 520),
                question_type=tags["question_type"],
                knowledge_tags=tags["knowledge_tags"],
                problem_tags=tags["problem_tags"],
                difficulty=tags["difficulty"],
                method_skeleton=tags["method_skeleton"],
                key_constraints=tags["key_constraints"],
                signatures=tags["signatures"],
                qa_status="AI初标",
            )
        )
    return records


def build_exam_records() -> tuple[list[ExamRecord], dict[str, list[old_base.Question]]]:
    question_tokens = load_json(QUESTION_TOKENS_PATH, {})
    exams: list[ExamRecord] = []
    papers: dict[str, list[old_base.Question]] = {}
    for docx in sorted(old_base.PAPER_DIR.glob("*.docx")):
        title, questions = old_base.extract_questions(docx)
        papers[title] = questions
        for q in questions:
            tags = tags_for_text(q.text)
            exams.append(
                ExamRecord(
                    question_id=f"{safe_slug(title)}-Q{q.number:02d}",
                    paper=title,
                    qnum=q.number,
                    score=q.score,
                    raw_qtype=q.qtype,
                    question_type=tags["question_type"],
                    stem=q.text,
                    question_image_token=question_tokens.get(f"{title}#{q.number}", ""),
                    local_images="；".join(q.images),
                    knowledge_tags=tags["knowledge_tags"],
                    problem_tags=tags["problem_tags"],
                    difficulty=tags["difficulty"],
                    method_skeleton=tags["method_skeleton"],
                    key_constraints=tags["key_constraints"],
                    signatures=tags["signatures"],
                    has_visual=bool(q.images),
                    qa_status="AI初标",
                )
            )
    return exams, papers


def split_tags(text: str) -> set[str]:
    return {x for x in re.split(r"[、；,，/ ]+", text or "") if x}


def audit_pair(q: ExamRecord, v: VideoRecord) -> MatchAudit:
    q_sig = split_tags(q.signatures)
    v_sig = split_tags(v.signatures)
    q_kn = split_tags(q.knowledge_tags)
    v_kn = split_tags(v.knowledge_tags)
    q_pr = split_tags(q.problem_tags)
    v_pr = split_tags(v.problem_tags)
    same_type = q.question_type == v.question_type
    sig_overlap = q_sig & v_sig
    kn_overlap = q_kn & v_kn
    pr_overlap = q_pr & v_pr
    method_overlap = split_tags(q.method_skeleton) & split_tags(v.method_skeleton)

    hard_filter = "不通过"
    reject_reason = ""
    hit_dimensions: list[str] = []
    score = 0.0

    if sig_overlap:
        hard_filter = "通过"
        hit_dimensions.append("核心模型/素材")
        score += 58 + 8 * min(len(sig_overlap), 3)
    elif same_type and kn_overlap and pr_overlap:
        hard_filter = "通过"
        hit_dimensions.append("题型方法")
        score += 38 + 8 * min(len(kn_overlap), 3) + 10 * min(len(pr_overlap), 2)
    else:
        reject_reason = "核心知识、任务目标或方法骨架未同时对齐"

    if hard_filter == "通过":
        if same_type:
            score += 10
            hit_dimensions.append("题型一致")
        if kn_overlap:
            score += 5 * min(len(kn_overlap), 4)
            hit_dimensions.append("知识点")
        if pr_overlap:
            score += 6 * min(len(pr_overlap), 3)
            hit_dimensions.append("问题标签")
        if method_overlap:
            score += 8
            hit_dimensions.append("方法骨架")
        if abs(q.difficulty - v.difficulty) <= 1:
            score += 4
        if q.question_type in {"科学探究题", "计算题", "工艺流程题", "推断题"} and not (sig_overlap or method_overlap):
            hard_filter = "不通过"
            reject_reason = "综合题缺少同构方法或核心模型，按规则不推荐"
            score = min(score, 49)

    grade = "不推荐"
    final_show = False
    reason = ""
    if hard_filter == "通过" and score >= 82 and sig_overlap:
        grade = "完美巩固"
        final_show = True
        reason = f"同属“{readable_list(sig_overlap)}”模型，知识锚点、任务目标和解题路径一致。"
    elif hard_filter == "通过" and score >= 72 and sig_overlap:
        grade = "进阶变式"
        final_show = True
        core = readable_list(sig_overlap)
        reason = f"围绕“{core}”迁移，问题标签/方法骨架可迁移，适合作为押题证据。"
    elif hard_filter == "通过":
        reject_reason = "相似度不足，只作为过程候选保留"

    return MatchAudit(
        match_id=f"{q.question_id}__{v.video_id}",
        question_id=q.question_id,
        video_id=v.video_id,
        paper=q.paper,
        qnum=q.qnum,
        video_name=v.video_name,
        source=v.source,
        similarity_grade=grade,
        score=round(score, 1),
        hard_filter=hard_filter,
        hit_dimensions=readable_list(hit_dimensions),
        reason=reason,
        reject_reason=reject_reason,
        final_show=final_show,
    )


def build_match_audit(exams: list[ExamRecord], videos: list[VideoRecord]) -> list[MatchAudit]:
    audits: list[MatchAudit] = []
    for q in exams:
        candidate_audits: list[MatchAudit] = []
        q_sigs = split_tags(q.signatures)
        q_kn = split_tags(q.knowledge_tags)
        q_pr = split_tags(q.problem_tags)
        for v in videos:
            v_sigs = split_tags(v.signatures)
            v_kn = split_tags(v.knowledge_tags)
            v_pr = split_tags(v.problem_tags)
            if not (q_sigs & v_sigs or (q.question_type == v.question_type and q_kn & v_kn and q_pr & v_pr)):
                continue
            candidate_audits.append(audit_pair(q, v))
        candidate_audits.sort(key=lambda x: x.score, reverse=True)
        accepted = 0
        for item in candidate_audits:
            if item.final_show and accepted < 1:
                accepted += 1
                audits.append(item)
            elif item.final_show:
                item.final_show = False
                item.reject_reason = "同题最终证据已保留最强1条，其余留在过程库"
                audits.append(item)
            elif item.score >= 45:
                audits.append(item)
        if not any(a.question_id == q.question_id for a in audits):
            audits.append(
                MatchAudit(
                    match_id=f"{q.question_id}__NO_MATCH",
                    question_id=q.question_id,
                    video_id="",
                    paper=q.paper,
                    qnum=q.qnum,
                    video_name="",
                    source="",
                    similarity_grade="不推荐",
                    score=0,
                    hard_filter="不通过",
                    hit_dimensions="",
                    reason="",
                    reject_reason="未召回到满足硬过滤的候选",
                    final_show=False,
                )
            )
    return audits


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


def build_docs(exams: list[ExamRecord], videos: list[VideoRecord], audits: list[MatchAudit]) -> list[dict]:
    DOCS.mkdir(parents=True, exist_ok=True)
    video_map = {v.video_id: v for v in videos}
    by_question: dict[str, list[MatchAudit]] = {}
    for a in audits:
        if a.final_show:
            by_question.setdefault(a.question_id, []).append(a)
    by_paper: dict[str, list[ExamRecord]] = {}
    for q in exams:
        by_paper.setdefault(q.paper, []).append(q)

    manifest: list[dict] = []
    examples: list[dict] = []
    for paper, questions in sorted(by_paper.items()):
        questions.sort(key=lambda q: q.qnum)
        hit_questions = [q for q in questions if by_question.get(q.question_id)]
        total_score = sum(q.score for q in questions if q.score)
        hit_score = sum(q.score for q in hit_questions if q.score)
        analysis_rows = []
        compare_rows = []
        for q in questions:
            ms = sorted(by_question.get(q.question_id, []), key=lambda m: m.score, reverse=True)
            onion = []
            for m in ms[:3]:
                v = video_map.get(m.video_id)
                if not v:
                    continue
                onion.append(f"{v.video_name}（{v.source} 行{v.source_row}）：{m.similarity_grade}，{m.reason}")
            analysis_rows.append(
                [
                    escape(q.question_type),
                    escape(str(q.qnum)),
                    escape(str(q.score or "")),
                    escape(f"{q.knowledge_tags}｜{q.problem_tags}"),
                    escape("押中" if ms else "未命中"),
                    escape("\n".join(onion) if onion else "未进入最终押题证据").replace("\n", "<br/>"),
                ]
            )
            if ms:
                left = f"<p><b>第{q.qnum}题</b></p>{img_tag(q.question_image_token, 560, f'{safe_slug(paper)}_q{q.qnum}.png')}"
                if not q.question_image_token:
                    left += f"<p>{escape(q.stem[:520])}</p>"
                right_parts = []
                for m in ms[:3]:
                    v = video_map[m.video_id]
                    right_parts.append(
                        f"<p><b>{escape(v.video_name)}</b></p>"
                        f"<p>{escape(m.similarity_grade)}：{escape(m.reason)}</p>"
                        f"<p>标签：{escape(v.knowledge_tags)}｜{escape(v.problem_tags)}</p>"
                    )
                    first_token = (v.screenshot_tokens.split("；")[0].split(":", 1)[1] if v.screenshot_tokens else "")
                    if first_token:
                        right_parts.append(img_tag(first_token, 520, f"{v.video_id}.png"))
                compare_rows.append([left, "".join(right_parts)])
                examples.append(
                    {
                        "paper": paper,
                        "qnum": q.qnum,
                        "question_type": q.question_type,
                        "knowledge_tags": q.knowledge_tags,
                        "problem_tags": q.problem_tags,
                        "matches": [asdict(m) for m in ms[:2]],
                    }
                )
        xml = "\n".join(
            [
                f"<title>洋葱学园 VS {escape(paper)} 押题对比（专业含截图版）</title>",
                "<h1>基本认识</h1>",
                f"<p>本卷共抽取 {len(questions)} 道题；专业版先对试卷题目和洋葱解题课分别打标签，再按相似题规则做硬过滤。</p>",
                f"<p><b>最终押中题数：</b>{len(hit_questions)}/{len(questions)}；<b>按分值估算覆盖：</b>{hit_score}/{total_score or '未知'}。</p>",
                "<p>判定口径：核心知识点相同、素材相似、题型方法相似任一成立，且知识锚点、任务目标、解题路径、关键约束未发生明显漂移，才进入最终展示。</p>",
                "<h1>试卷分析表</h1>",
                html_table(analysis_rows, ["题型", "题号", "分值", "考点/题型", "押中判定", "洋葱对应内容"], [90, 60, 60, 260, 80, 420]),
                "<h1>内容对比表</h1>",
                html_table(compare_rows or [["<p>本卷未筛出可进入最终交付的高置信押题证据。</p>", "<p>详见过程库中的候选审计与拒绝原因。</p>"]], ["中考题目", "洋葱内容"], [560, 560]),
            ]
        )
        local_xml = DOCS / f"{paper}_专业含截图版.xml"
        local_xml.write_text(xml, encoding="utf-8")
        manifest.append(
            {
                "title": paper,
                "question_count": len(questions),
                "hit_count": len(hit_questions),
                "total_score": total_score,
                "hit_score": hit_score,
                "local_xml": str(local_xml),
            }
        )

    topic_counts: dict[str, int] = {}
    for ex in examples:
        for tag in split_tags(ex["knowledge_tags"]):
            topic_counts[tag] = topic_counts.get(tag, 0) + 1
    summary_rows = []
    for item in manifest:
        link = item.get("url") or item["title"]
        link_cell = f'<a href="{escape(link)}">{escape(item["title"])}</a>' if str(link).startswith("http") else escape(link)
        summary_rows.append([link_cell, escape(f"{item['hit_count']}/{item['question_count']}"), escape(f"{item['hit_score']}/{item['total_score'] or '未知'}")])
    topic_rows = [[escape(k), escape(str(v))] for k, v in sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:30]]
    ex_rows = []
    for ex in examples[:30]:
        ex_rows.append([escape(ex["paper"]), escape(str(ex["qnum"])), escape(ex["question_type"]), escape(f"{ex['knowledge_tags']}｜{ex['problem_tags']}")])
    summary_xml = "\n".join(
        [
            "<title>2026 中考化学押题对比汇总（专业含截图版）</title>",
            "<h1>总体说明</h1>",
            "<p>本汇总基于 16 套 2026 年中考化学真题卷、洋葱学园三张课程表的视频截图，以及本地 135 份视频逐字稿生成。</p>",
            "<p>最终交付文档只展示通过硬过滤的高置信押题证据；逐题标签、视频标签、逐字稿匹配和所有候选审计均沉淀在独立多维表格工作台中。</p>",
            "<h1>分卷文档链接</h1>",
            html_table(summary_rows, ["试卷", "最终押中题数", "按分值估算覆盖"], [420, 120, 140]),
            "<h1>模块命中统计</h1>",
            html_table(topic_rows or [["暂无", "0"]], ["知识模块", "最终命中题次数"], [260, 140]),
            "<h1>典型命中案例</h1>",
            html_table(ex_rows or [["暂无", "", "", ""]], ["试卷", "题号", "题型", "标签"], [260, 70, 100, 520]),
            "<h1>工作台评估</h1>",
            "<p>本项目已按可复用工作台方式沉淀过程库。其他学科复用时，保留“试题库、课程视频库、逐字稿库、匹配审计库、规则与标签字典”的结构，替换学科标签体系和相似题规则即可。</p>",
        ]
    )
    summary_path = DOCS / "2026中考化学押题对比汇总_专业含截图版.xml"
    summary_path.write_text(summary_xml, encoding="utf-8")
    manifest.append({"title": "汇总", "local_xml": str(summary_path), "question_count": sum(i["question_count"] for i in manifest), "hit_count": sum(i["hit_count"] for i in manifest)})
    save_json(DATA / "examples.json", examples)
    save_json(DATA / "docs_manifest_pre_publish.json", manifest)
    return manifest


def prepare_local() -> None:
    ensure_dirs()
    media_by_source = old_image.collect_sheet_media()
    candidates = old_image.build_candidates_with_media(media_by_source)
    transcripts = build_transcripts()
    videos = build_video_records(candidates, transcripts, media_by_source)
    exams, _papers = build_exam_records()
    audits = build_match_audit(exams, videos)
    manifest = build_docs(exams, videos, audits)

    rules_content = ""
    rules_payload = load_json(RAW / "similarity_rules_doc.json", {})
    try:
        rules_content = rules_payload["data"]["document"]["content"]
    except Exception:
        pass
    rules_records = [
        {
            "规则ID": "RULE-001",
            "名称": "相似题硬过滤",
            "内容": "知识锚点、任务目标、解题/答题路径、关键约束必须基本对齐；只同章节、只同素材但任务漂移不推荐。",
            "来源": RULE_DOC,
        },
        {
            "规则ID": "RULE-002",
            "名称": "八大题型权重",
            "内容": json.dumps(TYPE_WEIGHTS, ensure_ascii=False),
            "来源": RULE_DOC,
        },
        {
            "规则ID": "RULE-003",
            "名称": "规则文档快照",
            "内容": rules_content[:12000],
            "来源": RULE_DOC,
        },
    ]

    save_json(DATA / "transcripts.json", [asdict(x) for x in transcripts])
    save_json(DATA / "video_records.json", [asdict(x) for x in videos])
    save_json(DATA / "exam_records.json", [asdict(x) for x in exams])
    save_json(DATA / "match_audit.json", [asdict(x) for x in audits])
    save_json(DATA / "rules_records.json", rules_records)
    print(f"Prepared: exams={len(exams)}, videos={len(videos)}, transcripts={len(transcripts)}, audits={len(audits)}, docs={len(manifest)}")


BASE_TABLE_SCHEMAS = {
    "试卷题库（逐题打标）": [
        {"name": "题目ID", "type": "text"},
        {"name": "试卷", "type": "text"},
        {"name": "题号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "分值", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "原题型", "type": "text"},
        {"name": "题型标签", "type": "text"},
        {"name": "题干", "type": "text"},
        {"name": "题目截图Token", "type": "text"},
        {"name": "本地题图路径", "type": "text"},
        {"name": "知识点标签", "type": "text"},
        {"name": "问题标签", "type": "text"},
        {"name": "难度标签", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "方法骨架", "type": "text"},
        {"name": "关键约束", "type": "text"},
        {"name": "核心模型签名", "type": "text"},
        {"name": "含题图/表格", "type": "checkbox"},
        {"name": "审核状态", "type": "text"},
    ],
    "视频候选库": [
        {"name": "视频ID", "type": "text"},
        {"name": "来源表", "type": "text"},
        {"name": "SheetID", "type": "text"},
        {"name": "来源行号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "视频名称", "type": "text"},
        {"name": "目录/章节", "type": "text"},
        {"name": "截图列/时间点", "type": "text"},
        {"name": "截图Token", "type": "text"},
        {"name": "逐字稿ID", "type": "text"},
        {"name": "逐字稿文件", "type": "text"},
        {"name": "逐字稿匹配置信度", "type": "number"},
        {"name": "逐字稿匹配状态", "type": "text"},
        {"name": "内容摘要", "type": "text"},
        {"name": "题型标签", "type": "text"},
        {"name": "知识点标签", "type": "text"},
        {"name": "问题标签", "type": "text"},
        {"name": "难度标签", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "方法骨架", "type": "text"},
        {"name": "关键约束", "type": "text"},
        {"name": "核心模型签名", "type": "text"},
        {"name": "审核状态", "type": "text"},
    ],
    "逐字稿库": [
        {"name": "逐字稿ID", "type": "text"},
        {"name": "文件名", "type": "text"},
        {"name": "文件路径", "type": "text"},
        {"name": "来源文件夹", "type": "text"},
        {"name": "解析状态", "type": "text"},
        {"name": "逐字稿内容", "type": "text"},
        {"name": "摘要", "type": "text"},
    ],
    "匹配审计库": [
        {"name": "匹配ID", "type": "text"},
        {"name": "题目ID", "type": "text"},
        {"name": "视频ID", "type": "text"},
        {"name": "试卷", "type": "text"},
        {"name": "题号", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "视频名称", "type": "text"},
        {"name": "来源表", "type": "text"},
        {"name": "相似度等级", "type": "text"},
        {"name": "评分", "type": "number"},
        {"name": "硬过滤", "type": "text"},
        {"name": "命中维度", "type": "text"},
        {"name": "命中说明", "type": "text"},
        {"name": "拒绝原因", "type": "text"},
        {"name": "进入最终文档", "type": "checkbox"},
    ],
    "规则与标签字典": [
        {"name": "规则ID", "type": "text"},
        {"name": "名称", "type": "text"},
        {"name": "内容", "type": "text"},
        {"name": "来源", "type": "text"},
    ],
}


def batch_rows(path: Path, fields: list[str], rows: list[list[Any]], batch_size: int = 200) -> list[Path]:
    paths = []
    for idx in range(0, len(rows), batch_size):
        payload = {"fields": fields, "rows": rows[idx : idx + batch_size]}
        p = path.with_name(f"{path.stem}_{idx//batch_size+1:03d}.json")
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        paths.append(p)
    return paths


def create_base() -> dict:
    prepare_needed = not (DATA / "exam_records.json").exists()
    if prepare_needed:
        prepare_local()
    base_info_path = FEISHU / "base_info.json"
    if base_info_path.exists():
        return load_json(base_info_path)

    initial_fields = json.dumps(BASE_TABLE_SCHEMAS["试卷题库（逐题打标）"], ensure_ascii=False)
    base_doc = run_json(
        [
            "lark-cli",
            "base",
            "+base-create",
            "--as",
            "user",
            "--name",
            "2026中考化学押题工作台（过程库）",
            "--time-zone",
            "Asia/Shanghai",
            "--table-name",
            "试卷题库（逐题打标）",
            "--fields",
            initial_fields,
            "--format",
            "json",
        ],
        timeout=240,
    )
    data = base_doc["data"]
    base_token = data.get("base", {}).get("app_token") or data.get("base_token") or data.get("app_token")
    if not base_token:
        # Current lark-cli versions use different envelope keys across tenants.
        text = json.dumps(data, ensure_ascii=False)
        m = re.search(r'app_token"?\s*:\s*"([^"]+)"', text) or re.search(r'base_token"?\s*:\s*"([^"]+)"', text)
        if not m:
            raise RuntimeError(f"Cannot locate base token in {json.dumps(data, ensure_ascii=False)}")
        base_token = m.group(1)

    tables = run_json(["lark-cli", "base", "+table-list", "--as", "user", "--base-token", base_token, "--format", "json"])["data"]
    table_map: dict[str, str] = {}
    for t in tables.get("items", []) or tables.get("tables", []) or []:
        name = t.get("name") or t.get("table_name")
        tid = t.get("table_id") or t.get("id")
        if name and tid:
            table_map[name] = tid

    for table_name, fields in BASE_TABLE_SCHEMAS.items():
        if table_name in table_map:
            continue
        created = run_json(
            [
                "lark-cli",
                "base",
                "+table-create",
                "--as",
                "user",
                "--base-token",
                base_token,
                "--name",
                table_name,
                "--fields",
                json.dumps(fields, ensure_ascii=False),
                "--format",
                "json",
            ],
            timeout=240,
        )
        text = json.dumps(created["data"], ensure_ascii=False)
        tid_match = re.search(r'"table_id"\s*:\s*"([^"]+)"', text) or re.search(r'"id"\s*:\s*"(tbl[^"]+)"', text)
        if not tid_match:
            tables = run_json(["lark-cli", "base", "+table-list", "--as", "user", "--base-token", base_token, "--format", "json"])["data"]
            for t in tables.get("items", []) or tables.get("tables", []) or []:
                name = t.get("name") or t.get("table_name")
                tid = t.get("table_id") or t.get("id")
                if name == table_name:
                    table_map[name] = tid
        else:
            table_map[table_name] = tid_match.group(1)

    base_url = data.get("url") or data.get("base", {}).get("url") or f"https://guanghe.feishu.cn/base/{base_token}"
    info = {"base_token": base_token, "base_url": base_url, "tables": table_map, "raw": data}
    save_json(base_info_path, info)
    return info


def create_views(base_info: dict) -> None:
    views_path = FEISHU / "base_views.json"
    if views_path.exists():
        return
    base_token = base_info["base_token"]
    table_map = base_info["tables"]
    created = []
    view_defs = {
        "试卷题库（逐题打标）": ["按试卷查看", "含题图题目", "待人工复核"],
        "视频候选库": ["已匹配逐字稿", "逐字稿弱匹配", "按来源表查看"],
        "逐字稿库": ["解析失败/待处理", "全部逐字稿"],
        "匹配审计库": ["最终押中", "待人工复核", "不推荐样本"],
        "规则与标签字典": ["规则总览"],
    }
    for table_name, views in view_defs.items():
        tid = table_map.get(table_name) or table_name
        for view_name in views:
            try:
                res = run_json(
                    [
                        "lark-cli",
                        "base",
                        "+view-create",
                        "--as",
                        "user",
                        "--base-token",
                        base_token,
                        "--table-id",
                        tid,
                        "--json",
                        json.dumps({"name": view_name, "type": "grid"}, ensure_ascii=False),
                        "--format",
                        "json",
                    ],
                    timeout=120,
                )
                created.append({"table": table_name, "view": view_name, "data": res["data"]})
            except Exception as exc:
                created.append({"table": table_name, "view": view_name, "error": str(exc)})
    save_json(views_path, created)


def rows_for_records(kind: str, records: list[dict]) -> tuple[list[str], list[list[Any]]]:
    if kind == "试卷题库（逐题打标）":
        fields = ["题目ID", "试卷", "题号", "分值", "原题型", "题型标签", "题干", "题目截图Token", "本地题图路径", "知识点标签", "问题标签", "难度标签", "方法骨架", "关键约束", "核心模型签名", "含题图/表格", "审核状态"]
        rows = [[r["question_id"], r["paper"], r["qnum"], r["score"], r["raw_qtype"], r["question_type"], r["stem"], r["question_image_token"], r["local_images"], r["knowledge_tags"], r["problem_tags"], r["difficulty"], r["method_skeleton"], r["key_constraints"], r["signatures"], r["has_visual"], r["qa_status"]] for r in records]
    elif kind == "视频候选库":
        fields = ["视频ID", "来源表", "SheetID", "来源行号", "视频名称", "目录/章节", "截图列/时间点", "截图Token", "逐字稿ID", "逐字稿文件", "逐字稿匹配置信度", "逐字稿匹配状态", "内容摘要", "题型标签", "知识点标签", "问题标签", "难度标签", "方法骨架", "关键约束", "核心模型签名", "审核状态"]
        rows = [[r["video_id"], r["source"], r["sheet_id"], r["source_row"], r["video_name"], r["hierarchy"], r["screenshot_refs"], r["screenshot_tokens"], r["transcript_id"], r["transcript_file"], r["transcript_match_confidence"], r["transcript_match_status"], r["content_summary"], r["question_type"], r["knowledge_tags"], r["problem_tags"], r["difficulty"], r["method_skeleton"], r["key_constraints"], r["signatures"], r["qa_status"]] for r in records]
    elif kind == "逐字稿库":
        fields = ["逐字稿ID", "文件名", "文件路径", "来源文件夹", "解析状态", "逐字稿内容", "摘要"]
        rows = [[r["transcript_id"], r["file_name"], r["file_path"], r["source_folder"], r["parse_status"], r["text"][:90000], r["summary"]] for r in records]
    elif kind == "匹配审计库":
        fields = ["匹配ID", "题目ID", "视频ID", "试卷", "题号", "视频名称", "来源表", "相似度等级", "评分", "硬过滤", "命中维度", "命中说明", "拒绝原因", "进入最终文档"]
        rows = [[r["match_id"], r["question_id"], r["video_id"], r["paper"], r["qnum"], r["video_name"], r["source"], r["similarity_grade"], r["score"], r["hard_filter"], r["hit_dimensions"], r["reason"], r["reject_reason"], r["final_show"]] for r in records]
    elif kind == "规则与标签字典":
        fields = ["规则ID", "名称", "内容", "来源"]
        rows = [[r["规则ID"], r["名称"], r["内容"], r["来源"]] for r in records]
    else:
        raise ValueError(kind)
    return fields, rows


def populate_base(base_info: dict) -> dict:
    records_status_path = FEISHU / "base_records.json"
    if records_status_path.exists():
        return load_json(records_status_path)
    datasets = {
        "试卷题库（逐题打标）": load_json(DATA / "exam_records.json", []),
        "视频候选库": load_json(DATA / "video_records.json", []),
        "逐字稿库": load_json(DATA / "transcripts.json", []),
        "匹配审计库": load_json(DATA / "match_audit.json", []),
        "规则与标签字典": load_json(DATA / "rules_records.json", []),
    }
    status = {}
    for table_name, records in datasets.items():
        fields, rows = rows_for_records(table_name, records)
        tid = base_info["tables"].get(table_name) or table_name
        payload_paths = batch_rows(PAYLOADS / f"{safe_slug(table_name)}.json", fields, rows)
        table_status = []
        for path in payload_paths:
            res = run_json(
                [
                    "lark-cli",
                    "base",
                    "+record-batch-create",
                    "--as",
                    "user",
                    "--base-token",
                    base_info["base_token"],
                    "--table-id",
                    tid,
                    "--json",
                    f"@{path.relative_to(ROOT)}",
                    "--format",
                    "json",
                ],
                timeout=240,
            )
            table_status.append({"payload": str(path), "data": res["data"]})
            time.sleep(0.4)
        status[table_name] = table_status
    save_json(records_status_path, status)
    return status


def create_docs() -> list[dict]:
    manifest = load_json(DATA / "docs_manifest_pre_publish.json", [])
    urls_path = FEISHU / "doc_urls_professional.json"
    urls = load_json(urls_path, {})
    for item in manifest:
        title = item["title"]
        if title in urls:
            item["url"] = urls[title]["url"]
            item["doc_id"] = urls[title]["doc_id"]
            continue
        xml = Path(item["local_xml"]).read_text(encoding="utf-8")
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
        urls[title] = {"url": url, "doc_id": doc_id, "raw": doc}
        item["url"] = url
        item["doc_id"] = doc_id
        save_json(urls_path, urls)
        print(f"created doc: {title} {url}", flush=True)
    save_json(DATA / "docs_manifest_published.json", manifest)
    return manifest


def republish_summary_with_links(manifest: list[dict]) -> None:
    items = [x for x in manifest if x["title"] != "汇总"]
    summary_rows = []
    for item in items:
        link_cell = f'<a href="{escape(item["url"])}">{escape(item["title"])}</a>'
        summary_rows.append([link_cell, escape(f"{item['hit_count']}/{item['question_count']}"), escape(f"{item['hit_score']}/{item['total_score'] or '未知'}")])
    examples = load_json(DATA / "examples.json", [])
    topic_counts: dict[str, int] = {}
    for ex in examples:
        for tag in split_tags(ex.get("knowledge_tags", "")):
            topic_counts[tag] = topic_counts.get(tag, 0) + 1
    topic_rows = [[escape(k), escape(str(v))] for k, v in sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:30]]
    ex_rows = [[escape(ex["paper"]), escape(str(ex["qnum"])), escape(ex["question_type"]), escape(f"{ex['knowledge_tags']}｜{ex['problem_tags']}")] for ex in examples[:30]]
    base_info = load_json(FEISHU / "base_info.json", {})
    base_link = base_info.get("base_url", "")
    base_para = f'<p>过程库：<a href="{escape(base_link)}">2026中考化学押题工作台（过程库）</a></p>' if base_link else ""
    summary_xml = "\n".join(
        [
            "<title>2026 中考化学押题对比汇总（专业含截图版）</title>",
            "<h1>总体说明</h1>",
            "<p>本汇总基于 16 套 2026 年中考化学真题卷、洋葱学园三张课程表的视频截图，以及本地 135 份视频逐字稿生成。</p>",
            "<p>最终交付文档只展示通过硬过滤的高置信押题证据；逐题标签、视频标签、逐字稿匹配和所有候选审计均沉淀在独立多维表格工作台中。</p>",
            base_para,
            "<h1>分卷文档链接</h1>",
            html_table(summary_rows, ["试卷", "最终押中题数", "按分值估算覆盖"], [420, 120, 140]),
            "<h1>模块命中统计</h1>",
            html_table(topic_rows or [["暂无", "0"]], ["知识模块", "最终命中题次数"], [260, 140]),
            "<h1>典型命中案例</h1>",
            html_table(ex_rows or [["暂无", "", "", ""]], ["试卷", "题号", "题型", "标签"], [260, 70, 100, 520]),
            "<h1>工作台评估</h1>",
            "<p>本项目已按可复用工作台方式沉淀过程库。其他学科复用时，保留“试题库、课程视频库、逐字稿库、匹配审计库、规则与标签字典”的结构，替换学科标签体系和相似题规则即可。</p>",
        ]
    )
    path = DOCS / "2026中考化学押题对比汇总_专业含截图版_带链接.xml"
    path.write_text(summary_xml, encoding="utf-8")
    urls = load_json(FEISHU / "doc_urls_professional.json", {})
    old_summary = urls.get("汇总", {})
    # Keep the first summary for traceability and create a final linked summary.
    if "汇总（最终带链接）" not in urls:
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
            input_text=summary_xml,
            timeout=300,
        )["data"]["document"]
        doc_id = doc["document_id"]
        url = doc.get("url") or doc.get("doc_url") or f"https://guanghe.feishu.cn/docx/{doc_id}"
        urls["汇总（最终带链接）"] = {"url": url, "doc_id": doc_id, "raw": doc, "previous_summary": old_summary}
        save_json(FEISHU / "doc_urls_professional.json", urls)


def verify_docs() -> dict:
    urls = load_json(FEISHU / "doc_urls_professional.json", {})
    verifications = {}
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
            verifications[title] = {"ok": True, "outline": content[:2000]}
        except Exception as exc:
            verifications[title] = {"ok": False, "error": str(exc)}
    save_json(FEISHU / "verification.json", verifications)
    return verifications


def update_source_sheets(base_info: dict) -> None:
    status_path = FEISHU / "source_sheet_updates.json"
    if status_path.exists():
        return
    videos = [VideoRecord(**x) for x in load_json(DATA / "video_records.json", [])]
    video_by_source_row = {(v.source, v.source_row): v for v in videos}
    record_status = load_json(FEISHU / "base_records.json", {})
    video_record_ids: dict[str, str] = {}
    for batch in record_status.get("视频候选库", []):
        data = batch.get("data", {})
        ids = data.get("record_id_list") or []
        rows = data.get("data") or []
        for rid, row in zip(ids, rows):
            try:
                vid = row[0]
            except Exception:
                vid = ""
            if vid:
                video_record_ids[vid] = rid
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    updates = {}
    for source, cfg in SOURCE_WRITE_CONFIG.items():
        max_row = cfg["max_row"]
        rows: list[list[str]] = []
        if cfg["header_rows"] == 2:
            rows.append(["AI过程字段"] + [""] * (len(AI_SHEET_HEADERS) - 1))
            rows.append(AI_SHEET_HEADERS)
            start_data_row = 3
        else:
            rows.append(AI_SHEET_HEADERS)
            start_data_row = 2
        for row_no in range(start_data_row, max_row + 1):
            v = video_by_source_row.get((source, row_no))
            if not v:
                rows.append([""] * len(AI_SHEET_HEADERS))
                continue
            record_id = video_record_ids.get(v.video_id, "")
            link = f"{base_info['base_url']}?table={base_info['tables'].get('视频候选库','')}&record={record_id}" if record_id else base_info["base_url"]
            rows.append(
                [
                    v.transcript_match_status,
                    v.transcript_file,
                    v.question_type,
                    v.knowledge_tags,
                    v.problem_tags,
                    str(v.difficulty),
                    v.method_skeleton,
                    v.key_constraints,
                    link,
                    now,
                ]
            )
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerows(rows)
        res = run_json(
            [
                "lark-cli",
                "sheets",
                "+csv-put",
                "--as",
                "user",
                "--url",
                cfg["url"],
                "--sheet-id",
                cfg["sheet_id"],
                "--start-cell",
                f"{cfg['start_col']}1",
                "--csv",
                "-",
                "--format",
                "json",
            ],
            input_text=csv_buf.getvalue(),
            timeout=240,
        )
        updates[source] = {"range_start": f"{cfg['start_col']}1", "rows": len(rows), "data": res["data"]}
    save_json(status_path, updates)


def update_lark_cli_if_needed() -> None:
    notice = load_json(FEISHU / "lark_cli_update_notice.json", None)
    if not notice:
        return
    proc = subprocess.run(["lark-cli", "update"], cwd=ROOT, text=True, capture_output=True, timeout=300)
    (FEISHU / "lark_cli_update.log").write_text(proc.stdout + "\nSTDERR:\n" + proc.stderr, encoding="utf-8")


def publish_all() -> None:
    if not (DATA / "exam_records.json").exists():
        prepare_local()
    base_info = create_base()
    create_views(base_info)
    populate_base(base_info)
    manifest = create_docs()
    republish_summary_with_links(manifest)
    verify_docs()
    update_source_sheets(base_info)
    update_lark_cli_if_needed()
    print("Published professional artifacts.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-local", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--update-sheets", action="store_true")
    args = parser.parse_args()

    if args.prepare_local:
        prepare_local()
    if args.publish:
        publish_all()
    if args.update_sheets:
        base_info = load_json(FEISHU / "base_info.json")
        update_source_sheets(base_info)
    if not any(vars(args).values()):
        prepare_local()


if __name__ == "__main__":
    main()
