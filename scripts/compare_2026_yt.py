#!/usr/bin/env python3
"""Build 2026 chemistry exam vs Onion course comparison artifacts."""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass, asdict
from html import escape
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from docx import Document


ROOT = Path("/Users/mbpro/Desktop/AI/chem-exam-analysis")
WORK = ROOT / "outputs" / "2026_yt_compare"
RAW = WORK / "raw"
DOCS = WORK / "docs"
ASSETS = WORK / "assets"
PAPER_DIR = ROOT / "试卷" / "2026中考卷"

SHEET_URLS = {
    "新中考培优": "https://guanghe.feishu.cn/sheets/shtcnbn6dCyvVtQAoaizuI3ip8e?sheet=L43kZ6",
    "重难点培优": "https://guanghe.feishu.cn/sheets/Tx9ps9ENsh9zfIt4UBycXV6snjd?sheet=jG6GT9",
    "教材同步": "https://guanghe.feishu.cn/sheets/shtcnbn6dCyvVtQAoaizuI3ip8e?sheet=e1mSBg",
}

DOCX_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


CHEM_TOPICS = [
    ("化学变化与物理变化", ["化学变化", "物理变化", "化学性质", "物理性质", "燃烧", "烧制"]),
    ("实验安全与基本操作", ["实验室", "安全", "药品", "试剂", "加热", "玻璃棒", "仪器", "量筒", "滴管", "过滤", "蒸发"]),
    ("空气与氧气", ["空气", "氧气", "助燃", "红磷", "氧含量", "氧气含量", "制取氧气", "催化剂", "过氧化氢", "铁丝", "燃烧"]),
    ("水与净化", ["水", "净化", "硬水", "软水", "电解水", "蒸馏", "过滤", "吸附"]),
    ("物质构成与元素", ["分子", "原子", "离子", "元素", "元素周期表", "化学式", "氧化物", "相对原子质量", "质量分数", "微粒"]),
    ("化学用语与化合价", ["化合价", "化学式", "离子符号", "方程式", "质量守恒", "配平", "符号"]),
    ("溶液与溶解度", ["溶液", "溶质", "溶剂", "溶解度", "饱和", "不饱和", "溶质质量分数", "结晶", "曲线"]),
    ("金属与材料", ["金属", "铁", "铜", "铝", "锌", "合金", "生锈", "腐蚀", "置换", "材料"]),
    ("酸碱盐", ["酸", "碱", "盐", "pH", "中和", "氢氧化钠", "盐酸", "硫酸", "碳酸钠", "碳酸盐", "复分解"]),
    ("碳与二氧化碳", ["碳", "二氧化碳", "CO2", "一氧化碳", "碳循环", "温室", "降碳", "气体制取"]),
    ("实验探究与控制变量", ["探究", "控制变量", "对照", "实验方案", "猜想", "现象", "结论", "数字化", "传感器", "异常"]),
    ("工艺流程与推断", ["工艺流程", "推断", "除杂", "鉴别", "框图", "流程图", "分离提纯"]),
    ("化学计算", ["化学计算", "方程式计算", "相对分子质量", "质量分数", "溶质质量分数", "数据处理", "图像计算"]),
    ("化学与生活/能源/化肥", ["化肥", "复合肥", "营养", "钙", "铁", "能源", "燃料", "环保", "跨学科", "航天", "新能源"]),
]

STOP_WORDS = set(
    "下列 有关 说法 正确 不正确 的是 其中 我国 关于 进行 可以 一定 属于 具有 说明 判断 选择 "
    "中考 化学 试卷 题目 选项 中国 年份 过程 物质 实验"
    .split()
)


@dataclass
class Candidate:
    cid: str
    source: str
    sheet_url: str
    row: int
    video_name: str
    hierarchy: str
    course_type: str
    screenshot_refs: list[str]
    text: str
    topics: list[str]


@dataclass
class Question:
    number: int
    score: int
    qtype: str
    text: str
    images: list[str]
    topics: list[str]


def colnum_to_letter(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.replace("（", "(").replace("）", ")").replace("：", ":")


def row_get(row: dict, col: str) -> str:
    return str(row.get("values", {}).get(col, "") or "").strip()


def load_rows(name: str) -> list[dict]:
    with (RAW / name).open(encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("ok"):
        raise RuntimeError(f"Failed to load {name}: {data}")
    return data["data"]["rows"]


def infer_topics(text: str) -> list[str]:
    found = []
    compact = (text or "").lower()
    for topic, keys in CHEM_TOPICS:
        if any(k.lower() in compact for k in keys):
            found.append(topic)
    return found


def keywords(text: str) -> set[str]:
    chunks = set(re.findall(r"[A-Za-z][A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text or ""))
    return {c for c in chunks if c not in STOP_WORDS and len(c) >= 2}


def fill_down(current: dict[str, str], updates: dict[str, str]) -> dict[str, str]:
    out = dict(current)
    for key, value in updates.items():
        if value:
            out[key] = value
    return out


def build_candidates() -> list[Candidate]:
    candidates: list[Candidate] = []

    # 新中考培优：header row 2, all columns named 视频截图 are valid.
    rows = load_rows("xinzhongkao_peiyou.json")
    header = rows[1]["values"]
    video_cols = [col for col, val in header.items() if val == "视频截图"]
    merged = {}
    for row in rows[2:]:
        merged = fill_down(merged, {c: row_get(row, c) for c in ["A", "B", "C"]})
        refs = [f"{col}:{row_get(row, col)}" for col in video_cols if row_get(row, col)]
        if not refs:
            continue
        video = row_get(row, "K") or row_get(row, "H")
        hierarchy = " - ".join(x for x in [merged.get("A", ""), merged.get("B", ""), merged.get("C", "")] if x)
        text = " ".join(
            x
            for x in [
                hierarchy,
                video,
                row_get(row, "L"),
                row_get(row, "I"),
                row_get(row, "U"),
                " ".join(refs),
            ]
            if x
        )
        candidates.append(
            Candidate(
                cid=f"XZK-{row['row_number']}",
                source="新中考培优",
                sheet_url=SHEET_URLS["新中考培优"],
                row=row["row_number"],
                video_name=video,
                hierarchy=hierarchy,
                course_type=row_get(row, "L"),
                screenshot_refs=refs,
                text=text,
                topics=infer_topics(text),
            )
        )

    # 重难点培优：header row 1, all columns named 视频截图 are valid.
    rows = load_rows("zhongnandian_peiyou.json")
    header = rows[0]["values"]
    video_cols = [col for col, val in header.items() if val == "视频截图"]
    merged = {}
    for row in rows[1:]:
        merged = fill_down(merged, {c: row_get(row, c) for c in ["A", "C", "D"]})
        refs = [f"{col}:{row_get(row, col)}" for col in video_cols if row_get(row, col)]
        if not refs:
            continue
        video = row_get(row, "E")
        hierarchy = " - ".join(x for x in [merged.get("C", ""), merged.get("D", ""), row_get(row, "AN"), row_get(row, "AO"), row_get(row, "AP")] if x)
        text = " ".join(
            x
            for x in [
                hierarchy,
                video,
                row_get(row, "I"),
                row_get(row, "J"),
                row_get(row, "K"),
                " ".join(refs),
            ]
            if x
        )
        candidates.append(
            Candidate(
                cid=f"ZND-{row['row_number']}",
                source="重难点培优",
                sheet_url=SHEET_URLS["重难点培优"],
                row=row["row_number"],
                video_name=video,
                hierarchy=hierarchy,
                course_type="解题课",
                screenshot_refs=refs,
                text=text,
                topics=infer_topics(text),
            )
        )

    # 教材同步：header row 1, filter 课程类型=解题课, then screenshot columns only.
    rows = load_rows("jiaocai_tongbu.json")
    header = rows[0]["values"]
    video_cols = [col for col, val in header.items() if val == "视频截图"]
    merged = {}
    for row in rows[1:]:
        merged = fill_down(merged, {c: row_get(row, c) for c in ["A", "B", "C", "D", "E"]})
        if row_get(row, "AI") != "解题课":
            continue
        refs = [f"{col}:{row_get(row, col)}" for col in video_cols if row_get(row, col)]
        if not refs:
            continue
        video = row_get(row, "H") or row_get(row, "AF") or row_get(row, "E")
        hierarchy = " - ".join(x for x in [merged.get("C", ""), merged.get("D", ""), merged.get("E", "")] if x)
        text = " ".join(
            x
            for x in [
                hierarchy,
                video,
                row_get(row, "AR"),
                row_get(row, "BV"),
                row_get(row, "BW"),
                " ".join(refs),
            ]
            if x
        )
        candidates.append(
            Candidate(
                cid=f"JCTB-{row['row_number']}",
                source="教材同步",
                sheet_url=SHEET_URLS["教材同步"],
                row=row["row_number"],
                video_name=video,
                hierarchy=hierarchy,
                course_type=row_get(row, "AI"),
                screenshot_refs=refs,
                text=text,
                topics=infer_topics(text),
            )
        )

    return candidates


def relationship_map(docx_path: Path) -> dict[str, str]:
    out = {}
    with zipfile.ZipFile(docx_path) as zf:
        xml = zf.read("word/_rels/document.xml.rels")
    root = ET.fromstring(xml)
    for rel in root:
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target", "")
        if rid and "media/" in target:
            out[rid] = "word/" + target if not target.startswith("/") else target.lstrip("/")
    return out


def document_blocks(docx_path: Path) -> list[dict]:
    with zipfile.ZipFile(docx_path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find("w:body", DOCX_NS)
    blocks = []
    if body is None:
        return blocks
    for child in body:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            texts = [t.text or "" for t in child.findall(".//w:t", DOCX_NS)]
            imgs = [b.attrib.get(f"{{{DOCX_NS['r']}}}embed") for b in child.findall(".//a:blip", DOCX_NS)]
            blocks.append({"kind": "p", "text": normalize_text("".join(texts)), "images": [x for x in imgs if x]})
        elif tag == "tbl":
            texts = [t.text or "" for t in child.findall(".//w:t", DOCX_NS)]
            imgs = [b.attrib.get(f"{{{DOCX_NS['r']}}}embed") for b in child.findall(".//a:blip", DOCX_NS)]
            blocks.append({"kind": "table", "text": normalize_text(" ".join(texts)), "images": [x for x in imgs if x]})
    return blocks


def extract_images(docx_path: Path, paper_slug: str, rids: Iterable[str]) -> list[str]:
    rels = relationship_map(docx_path)
    out_dir = ASSETS / paper_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    seen = set()
    with zipfile.ZipFile(docx_path) as zf:
        for idx, rid in enumerate(rids, 1):
            if rid in seen or rid not in rels:
                continue
            seen.add(rid)
            src = rels[rid]
            suffix = Path(src).suffix or ".png"
            dest = out_dir / f"{rid}{suffix}"
            with zf.open(src) as rf, dest.open("wb") as wf:
                shutil.copyfileobj(rf, wf)
            copied.append(str(dest))
    return copied


QUESTION_RE = re.compile(r"^(\d{1,2})[．.、]\s*(?:[·•]\s*)?(?:[（(]?(\d+)分[）)]?)?")


def extract_questions(docx_path: Path) -> tuple[str, list[Question]]:
    paper_title = docx_path.stem
    blocks = document_blocks(docx_path)
    segments = []
    current = None
    qtype = ""
    for block in blocks:
        text = block["text"]
        if not text:
            if current and block["images"]:
                current["rids"].extend(block["images"])
            continue
        if "选择题" in text and len(text) < 80:
            qtype = "选择题"
        elif any(k in text for k in ["非选择题", "填空题", "实验题", "综合题", "计算题"]) and len(text) < 100:
            qtype = text[:30]
        m = QUESTION_RE.match(text)
        if m:
            if current:
                segments.append(current)
            current = {
                "number": int(m.group(1)),
                "score": int(m.group(2) or 0),
                "qtype": qtype or "试题",
                "parts": [text],
                "rids": list(block["images"]),
            }
        elif current:
            current["parts"].append(text)
            current["rids"].extend(block["images"])
    if current:
        segments.append(current)

    # Use python-docx as a fallback if XML segmentation failed.
    if not segments:
        doc = Document(docx_path)
        qtype = ""
        current = None
        for para in doc.paragraphs:
            text = normalize_text(para.text)
            if not text:
                continue
            if "选择题" in text and len(text) < 80:
                qtype = "选择题"
            m = QUESTION_RE.match(text)
            if m:
                if current:
                    segments.append(current)
                current = {"number": int(m.group(1)), "score": int(m.group(2) or 0), "qtype": qtype or "试题", "parts": [text], "rids": []}
            elif current:
                current["parts"].append(text)
        if current:
            segments.append(current)

    questions = []
    seen_numbers = set()
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", paper_title)
    for seg in segments:
        number = seg["number"]
        if number <= 0 or number in seen_numbers:
            continue
        text = normalize_text(" ".join(seg["parts"]))
        if "【答案】" in text or "【分析】" in text or "声明:试题解析" in text:
            continue
        images = extract_images(docx_path, slug, seg["rids"])
        seen_numbers.add(number)
        questions.append(
            Question(
                number=number,
                score=seg["score"],
                qtype=seg["qtype"],
                text=text,
                images=images,
                topics=infer_topics(text),
            )
        )
    return paper_title, questions


def match_question(question: Question, candidates: list[Candidate]) -> list[dict]:
    qkw = keywords(question.text)
    qtext = question.text
    scored = []
    for cand in candidates:
        topic_overlap = set(question.topics) & set(cand.topics)
        if not question.topics or not cand.topics:
            topic_overlap = set()
        ckw = keywords(cand.text)
        kw_overlap = qkw & ckw
        if not topic_overlap and len(kw_overlap) < 3:
            continue
        score = len(topic_overlap) * 10 + len(kw_overlap) * 1.2
        # Specific chemistry signals should outrank broad overlaps such as "air" or "experiment".
        special_signals = [
            (r"CO2|二氧化碳|降碳|碳循环", "碳与二氧化碳"),
            (r"溶解度|溶质质量分数|饱和|结晶", "溶液与溶解度"),
            (r"pH|中和|酸|碱|盐酸|氢氧化钠|碳酸盐", "酸碱盐"),
            (r"控制变量|对照|探究|猜想|数字化|传感器", "实验探究与控制变量"),
            (r"化合价|化学式|相对分子质量|质量分数", "物质构成与元素"),
            (r"除杂|鉴别|推断|流程图|工艺流程", "工艺流程与推断"),
        ]
        for pattern, topic in special_signals:
            if re.search(pattern, qtext, re.I):
                score += 8 if topic in cand.topics else -4
        if cand.video_name and cand.video_name in question.text:
            score += 10
        if score < 10:
            continue
        if topic_overlap:
            reason_type = "核心知识点相同"
            reason = "、".join(sorted(topic_overlap))
        elif kw_overlap:
            reason_type = "题型方法/素材相似"
            reason = "关键词重合：" + "、".join(sorted(list(kw_overlap))[:5])
        else:
            reason_type = "弱相关"
            reason = "文本相关"
        scored.append(
            {
                "score": round(score, 1),
                "reason_type": reason_type,
                "reason": reason,
                "candidate": asdict(cand),
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:3]


def short(text: str, limit: int = 180) -> str:
    text = normalize_text(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def verdict(matches: list[dict]) -> str:
    if not matches:
        return "❌"
    return "✅" if matches[0]["score"] >= 18 else "✅?"


def html_table(rows: list[list[str]], header: list[str]) -> str:
    colgroup = "<colgroup>" + "".join('<col width="140"/>' for _ in header) + "</colgroup>"
    thead = "<thead><tr>" + "".join(f'<th background-color="light-gray">{escape(h)}</th>' for h in header) + "</tr></thead>"
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f'<td vertical-align="top">{cell}</td>' for cell in row) + "</tr>")
    return f"<table>{colgroup}{thead}<tbody>{''.join(body)}</tbody></table>"


def match_summary(matches: list[dict], rich: bool = False) -> str:
    if not matches:
        return escape("未找到强相关解题课截图证据")
    parts = []
    for m in matches:
        c = m["candidate"]
        src = f"{c['source']} 行{c['row']}"
        refs = "；".join(c["screenshot_refs"][:3])
        line = f"{c['video_name']}（{src}，{refs}）：{m['reason_type']}，{m['reason']}"
        if rich:
            parts.append(f"<p>{escape(line)}</p>")
        else:
            parts.append(escape(line))
    return "".join(parts) if rich else "<br/>".join(parts)


def build_paper_doc(title: str, questions: list[Question], matches_by_q: dict[int, list[dict]]) -> tuple[str, dict]:
    total_score = sum(q.score for q in questions if q.score)
    hit_score = sum(q.score for q in questions if verdict(matches_by_q[q.number]).startswith("✅"))
    hit_count = sum(1 for q in questions if verdict(matches_by_q[q.number]).startswith("✅"))
    strong_count = sum(1 for q in questions if verdict(matches_by_q[q.number]) == "✅")

    analysis_rows = []
    compare_rows = []
    for q in questions:
        ms = matches_by_q[q.number]
        topics = "、".join(q.topics) if q.topics else "综合/待人工复核"
        analysis_rows.append(
            [
                escape(q.qtype),
                escape(str(q.number)),
                escape(str(q.score or "")),
                escape(topics),
                escape(verdict(ms)),
                match_summary(ms),
            ]
        )
        compare_rows.append(
            [
                f"<p><b>第{q.number}题</b></p><p>{escape(short(q.text, 360))}</p>",
                match_summary(ms, rich=True),
            ]
        )

    xml = "\n".join(
        [
            f"<title>洋葱学园 VS {escape(title)} 押题对比</title>",
            "<h1>基本认识</h1>",
            f"<p>本卷共抽取 {len(questions)} 道题；按“核心知识点相同、素材相似、题型方法相似”任一条件判定押中。</p>",
            f"<p><b>强/弱命中题数：</b>{hit_count}/{len(questions)}；其中强命中 {strong_count} 道。</p>",
            f"<p><b>按分值估算覆盖：</b>{hit_score}/{total_score or '未知'}。</p>",
            "<p>说明：本版优先保留每题 1-3 个最有说服力的解题课截图时间点；截图来源以表名、行号和时间点标注，便于回表核验。</p>",
            "<h1>试卷分析表</h1>",
            html_table(analysis_rows, ["题型", "题号", "分值", "考点/题型", "押中判定", "洋葱对应内容"]),
            "<h1>内容对比表</h1>",
            html_table(compare_rows, ["中考题目", "洋葱内容"]),
        ]
    )
    meta = {
        "title": title,
        "question_count": len(questions),
        "hit_count": hit_count,
        "strong_count": strong_count,
        "total_score": total_score,
        "hit_score": hit_score,
    }
    return xml, meta


def build_summary_doc(papers: list[dict], all_examples: list[dict]) -> str:
    rows = []
    for p in papers:
        rate = f"{p['hit_count']}/{p['question_count']}"
        score = f"{p['hit_score']}/{p['total_score'] or '未知'}"
        link = p.get("url") or p.get("local_xml", "")
        link_cell = f'<a href="{escape(link)}">{escape(p["title"])}</a>' if link.startswith("http") else escape(p["title"])
        rows.append([link_cell, escape(rate), escape(score), escape(str(p["strong_count"]))])

    topic_counts = {}
    for ex in all_examples:
        for topic in ex.get("topics", []):
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
    topic_rows = [[escape(k), escape(str(v))] for k, v in sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)]
    ex_rows = []
    for ex in all_examples[:30]:
        ex_rows.append(
            [
                escape(ex["paper"]),
                escape(str(ex["qnum"])),
                escape("、".join(ex["topics"]) or "综合"),
                match_summary(ex["matches"]),
            ]
        )

    return "\n".join(
        [
            "<title>2026 中考化学押题对比汇总</title>",
            "<h1>总体说明</h1>",
            "<p>本汇总基于 16 套 2026 年中考化学试卷，以及洋葱学园三张飞书表格中的解题课视频截图时间点生成。</p>",
            "<p>判定标准：核心知识点相同、素材相似、题型方法相似，满足任一条即计为押中；正文优先展示强证据。</p>",
            "<h1>分卷文档链接</h1>",
            html_table(rows, ["试卷", "命中题数", "按分值估算覆盖", "强命中题数"]),
            "<h1>模块命中统计</h1>",
            html_table(topic_rows, ["知识模块", "命中题次数"]),
            "<h1>典型命中案例</h1>",
            html_table(ex_rows, ["试卷", "题号", "模块", "洋葱证据"]),
        ]
    )


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    candidates = build_candidates()
    (WORK / "candidate_bank.json").write_text(json.dumps([asdict(c) for c in candidates], ensure_ascii=False, indent=2), encoding="utf-8")

    papers_meta = []
    examples = []
    full_results = {}
    for docx_path in sorted(PAPER_DIR.glob("*.docx")):
        title, questions = extract_questions(docx_path)
        matches_by_q = {q.number: match_question(q, candidates) for q in questions}
        xml, meta = build_paper_doc(title, questions, matches_by_q)
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title)
        xml_path = DOCS / f"{safe}.xml"
        xml_path.write_text(xml, encoding="utf-8")
        meta["local_xml"] = str(xml_path)
        meta["docx"] = str(docx_path)
        papers_meta.append(meta)
        full_results[title] = {
            "meta": meta,
            "questions": [asdict(q) for q in questions],
            "matches": {str(k): v for k, v in matches_by_q.items()},
        }
        for q in questions:
            ms = matches_by_q[q.number]
            if verdict(ms) == "✅":
                examples.append({"paper": title, "qnum": q.number, "topics": q.topics, "matches": ms})

    summary_xml = build_summary_doc(papers_meta, examples)
    (DOCS / "2026中考化学押题对比汇总.xml").write_text(summary_xml, encoding="utf-8")
    (WORK / "match_results.json").write_text(json.dumps(full_results, ensure_ascii=False, indent=2), encoding="utf-8")
    (WORK / "papers_meta.json").write_text(json.dumps(papers_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidates": len(candidates), "papers": len(papers_meta), "docs_dir": str(DOCS)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
