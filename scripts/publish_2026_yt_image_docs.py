#!/usr/bin/env python3
"""Publish image-rich 2026 chemistry exam vs Onion comparison docs."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

import compare_2026_yt as base


ROOT = Path("/Users/mbpro/Desktop/AI/chem-exam-analysis")
WORK = ROOT / "outputs" / "2026_yt_compare"
IMAGE_WORK = ROOT / "outputs" / "2026_yt_compare_image"
RAW = WORK / "raw"
DOCS = IMAGE_WORK / "docs"
CARDS = IMAGE_WORK / "question_cards"
FEISHU = IMAGE_WORK / "feishu"
RICH_CELLS = IMAGE_WORK / "rich_cells"

URLS_PATH = FEISHU / "doc_urls.json"
QUESTION_TOKEN_PATH = FEISHU / "question_image_tokens.json"
STAGING_PATH = FEISHU / "staging_doc.json"


SOURCE_CONFIGS = {
    "新中考培优": {
        "raw": "xinzhongkao_peiyou.json",
        "url": base.SHEET_URLS["新中考培优"],
        "sheet_id": "L43kZ6",
        "header_index": 1,
        "hierarchy_cols": ["A", "B", "C"],
        "video_cols": ["K", "H"],
        "text_cols": ["L", "I", "U"],
        "cid_prefix": "XZK",
    },
    "重难点培优": {
        "raw": "zhongnandian_peiyou.json",
        "url": base.SHEET_URLS["重难点培优"],
        "sheet_id": "jG6GT9",
        "header_index": 0,
        "hierarchy_cols": ["C", "D", "AN", "AO", "AP"],
        "video_cols": ["E"],
        "text_cols": ["I", "J", "K"],
        "cid_prefix": "ZND",
    },
    "教材同步": {
        "raw": "jiaocai_tongbu.json",
        "url": base.SHEET_URLS["教材同步"],
        "sheet_id": "e1mSBg",
        "header_index": 0,
        "hierarchy_cols": ["C", "D", "E"],
        "video_cols": ["H", "AF", "E"],
        "text_cols": ["AR", "BV", "BW"],
        "cid_prefix": "JCTB",
    },
}


def run_json(cmd: list[str], *, input_text: str | None = None, timeout: int = 180) -> dict:
    proc = subprocess.run(cmd, input=input_text, text=True, capture_output=True, timeout=timeout)
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
    return payload


def load_rows(raw_name: str) -> list[dict]:
    return base.load_rows(raw_name)


def col_to_num(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + ord(ch.upper()) - 64
    return n


def cols_between(cols: Iterable[str]) -> tuple[str, str]:
    ordered = sorted(cols, key=col_to_num)
    return ordered[0], ordered[-1]


def safe_slug(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")


def video_columns(rows: list[dict], header_index: int) -> list[str]:
    header = rows[header_index]["values"]
    return sorted([col for col, val in header.items() if val == "视频截图"], key=col_to_num)


def collect_sheet_media() -> dict[str, dict[str, dict]]:
    """Return source -> row -> col -> {tokens, texts} for screenshot columns."""
    RICH_CELLS.mkdir(parents=True, exist_ok=True)
    media_by_source: dict[str, dict[str, dict]] = {}

    for source, cfg in SOURCE_CONFIGS.items():
        cache_path = RICH_CELLS / f"{safe_slug(source)}.json"
        if cache_path.exists():
            media_by_source[source] = json.loads(cache_path.read_text(encoding="utf-8"))
            continue

        rows = load_rows(cfg["raw"])
        cols = video_columns(rows, cfg["header_index"])
        start_col, end_col = cols_between(cols)
        first_row = 1
        last_row = max(row["row_number"] for row in rows)
        source_media: dict[str, dict] = {}

        for chunk_start in range(first_row, last_row + 1, 30):
            chunk_end = min(last_row, chunk_start + 29)
            payload = run_json(
                [
                    "lark-cli",
                    "sheets",
                    "+cells-get",
                    "--url",
                    cfg["url"],
                    "--sheet-id",
                    cfg["sheet_id"],
                    "--range",
                    f"{start_col}{chunk_start}:{end_col}{chunk_end}",
                    "--include",
                    "value",
                    "--max-chars",
                    "500000",
                    "--format",
                    "json",
                ],
                timeout=120,
            )
            if payload["data"].get("has_more"):
                raise RuntimeError(f"{source} cells-get was truncated at rows {chunk_start}-{chunk_end}")
            for rng in payload["data"]["ranges"]:
                if rng.get("truncated"):
                    raise RuntimeError(f"{source} cells-get range was truncated: {rng.get('actual_range')}")
                row_indices = rng["row_indices"]
                col_indices = rng["col_indices"]
                for i, row_cells in enumerate(rng["cells"]):
                    row_no = str(row_indices[i])
                    for j, cell in enumerate(row_cells):
                        col = col_indices[j]
                        if col not in cols:
                            continue
                        tokens = []
                        texts = []
                        for item in cell.get("rich_text", []) or []:
                            if item.get("type") == "embed-image" and item.get("image_token"):
                                tokens.append(
                                    {
                                        "token": item["image_token"],
                                        "width": item.get("image_width"),
                                        "height": item.get("image_height"),
                                    }
                                )
                            elif item.get("text"):
                                texts.append(str(item["text"]))
                        if cell.get("value"):
                            texts.append(str(cell["value"]))
                        if tokens or texts:
                            source_media.setdefault(row_no, {})[col] = {"tokens": tokens, "texts": texts}

        cache_path.write_text(json.dumps(source_media, ensure_ascii=False, indent=2), encoding="utf-8")
        media_by_source[source] = source_media
    return media_by_source


def row_has_screenshot(source_media: dict[str, dict], row_number: int, cols: list[str]) -> bool:
    cells = source_media.get(str(row_number), {})
    return any(cells.get(col, {}).get("tokens") or cells.get(col, {}).get("texts") for col in cols)


def screenshot_refs_for_row(source_media: dict[str, dict], row: dict, cols: list[str]) -> list[str]:
    out = []
    cells = source_media.get(str(row["row_number"]), {})
    for col in cols:
        text = base.row_get(row, col)
        cell = cells.get(col, {})
        texts = [t for t in cell.get("texts", []) if t and t != text]
        token_count = len(cell.get("tokens", []))
        label = text or "；".join(texts) or ("截图" if token_count else "")
        if label:
            out.append(f"{col}:{label}")
    return out


def sheet_images_for_candidate(media_by_source: dict[str, dict], cand: dict, limit: int = 3) -> list[dict]:
    cfg = SOURCE_CONFIGS.get(cand["source"])
    if not cfg:
        return []
    rows = load_rows(cfg["raw"])
    cols = video_columns(rows, cfg["header_index"])
    cells = media_by_source.get(cand["source"], {}).get(str(cand["row"]), {})
    images = []
    for col in cols:
        for token in cells.get(col, {}).get("tokens", []):
            images.append({"col": col, **token})
            if len(images) >= limit:
                return images
    return images


def build_candidates_with_media(media_by_source: dict[str, dict]) -> list[base.Candidate]:
    candidates: list[base.Candidate] = []

    for source, cfg in SOURCE_CONFIGS.items():
        rows = load_rows(cfg["raw"])
        cols = video_columns(rows, cfg["header_index"])
        merged: dict[str, str] = {}
        for row in rows[cfg["header_index"] + 1 :]:
            if source == "教材同步" and base.row_get(row, "AI") != "解题课":
                continue

            merged = base.fill_down(merged, {c: base.row_get(row, c) for c in cfg["hierarchy_cols"]})
            if not row_has_screenshot(media_by_source[source], row["row_number"], cols):
                continue

            refs = screenshot_refs_for_row(media_by_source[source], row, cols)
            video = next((base.row_get(row, c) for c in cfg["video_cols"] if base.row_get(row, c)), "")
            hierarchy = " - ".join(
                x
                for x in [
                    *(merged.get(c, "") for c in cfg["hierarchy_cols"][:3]),
                    *(base.row_get(row, c) for c in cfg["hierarchy_cols"][3:]),
                ]
                if x
            )
            text = " ".join(
                x
                for x in [
                    hierarchy,
                    video,
                    *(base.row_get(row, c) for c in cfg["text_cols"]),
                    " ".join(refs),
                ]
                if x
            )
            candidates.append(
                base.Candidate(
                    cid=f"{cfg['cid_prefix']}-{row['row_number']}",
                    source=source,
                    sheet_url=cfg["url"],
                    row=row["row_number"],
                    video_name=video,
                    hierarchy=hierarchy,
                    course_type=base.row_get(row, "AI") if source == "教材同步" else "解题课",
                    screenshot_refs=refs,
                    text=text,
                    topics=base.infer_topics(text),
                )
            )
    return candidates


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/Users/mbpro/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.13/site-packages/PIL/fonts/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=1 if bold and "PingFang" in path else 0)
            except Exception:
                continue
    return ImageFont.load_default()


def wrap_text_by_width(draw: ImageDraw.ImageDraw, text: str, font_obj: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for para in re.split(r"\s+", base.normalize_text(text)):
        if not para:
            continue
        current = ""
        for ch in para:
            trial = current + ch
            if draw.textbbox((0, 0), trial, font=font_obj)[2] <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines or [""]


def fit_image(path: str, max_width: int) -> Image.Image | None:
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return None
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, max(1, int(img.height * ratio))))
    return img


def render_question_card(paper_title: str, question: base.Question) -> Path:
    paper_dir = CARDS / safe_slug(paper_title)
    paper_dir.mkdir(parents=True, exist_ok=True)
    out_path = paper_dir / f"q{question.number:02d}.png"

    width = 1200
    pad = 48
    title_font = font(38, bold=True)
    body_font = font(30)
    small_font = font(24)
    line_gap = 12
    tmp = Image.new("RGB", (width, 100), "white")
    draw = ImageDraw.Draw(tmp)
    content_width = width - pad * 2

    title = f"{paper_title}  第{question.number}题" + (f"（{question.score}分）" if question.score else "")
    lines = wrap_text_by_width(draw, question.text, body_font, content_width)
    image_objs = [img for img in (fit_image(p, content_width) for p in question.images) if img is not None]

    title_h = draw.textbbox((0, 0), title, font=title_font)[3] + 10
    line_h = draw.textbbox((0, 0), "测试", font=body_font)[3] + line_gap
    small_h = draw.textbbox((0, 0), "题图", font=small_font)[3] + 8
    height = pad + title_h + 18 + len(lines) * line_h + 24
    for img in image_objs:
        height += small_h + img.height + 26
    height += pad

    canvas = Image.new("RGB", (width, max(height, 420)), "#ffffff")
    draw = ImageDraw.Draw(canvas)
    y = pad
    draw.text((pad, y), title, fill="#111111", font=title_font)
    y += title_h + 10
    draw.line((pad, y, width - pad, y), fill="#dddddd", width=2)
    y += 24
    for line in lines:
        draw.text((pad, y), line, fill="#222222", font=body_font)
        y += line_h
    y += 12
    for idx, img in enumerate(image_objs, 1):
        draw.text((pad, y), f"题图 {idx}", fill="#555555", font=small_font)
        y += small_h
        canvas.paste(img, (pad, y))
        y += img.height + 26

    canvas.save(out_path, "PNG")
    return out_path


def create_doc_from_xml(xml: str) -> dict:
    return run_json(
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
        timeout=240,
    )["data"]["document"]


def ensure_staging_doc() -> dict:
    FEISHU.mkdir(parents=True, exist_ok=True)
    if STAGING_PATH.exists():
        return json.loads(STAGING_PATH.read_text(encoding="utf-8"))
    doc = create_doc_from_xml(
        "<title>2026中考化学题目截图素材库（含截图版）</title>"
        "<p>此文档用于上传题目组合截图，最终押题对比文档会复用这些图片 token。</p>"
    )
    STAGING_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def upload_question_images(card_map: dict[str, dict[str, str]]) -> dict[str, str]:
    tokens: dict[str, str] = load_json(QUESTION_TOKEN_PATH, {})
    staging = ensure_staging_doc()
    doc_id = staging["document_id"]

    for paper, qcards in card_map.items():
        for qnum, image_path in qcards.items():
            key = f"{paper}#{qnum}"
            if tokens.get(key):
                continue
            rel_path = str(Path(image_path).relative_to(ROOT))
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
                    "700",
                    "--align",
                    "center",
                    "--format",
                    "json",
                ],
                timeout=180,
            )
            tokens[key] = payload["data"]["file_token"]
            save_json(QUESTION_TOKEN_PATH, tokens)
            print(f"uploaded question image {key}", flush=True)
    return tokens


def verdict(matches: list[dict]) -> str:
    return base.verdict(matches)


def img_tag(token: str, width: int = 520, name: str = "") -> str:
    name_attr = f' name="{escape(name)}"' if name else ""
    return f'<img src="{escape(token)}" width="{width}"{name_attr}/>'


def match_summary_plain(matches: list[dict]) -> str:
    if not matches:
        return "未找到强相关解题课截图证据"
    lines = []
    for m in matches:
        c = m["candidate"]
        refs = "；".join(c["screenshot_refs"][:3])
        lines.append(f"{c['video_name']}（{c['source']} 行{c['row']}，{refs}）：{m['reason_type']}，{m['reason']}")
    return "\n".join(lines)


def match_cell(matches: list[dict]) -> str:
    if not matches:
        return "<p>未找到强相关解题课截图证据。</p>"
    parts = []
    image_count = 0
    for m in matches:
        c = m["candidate"]
        refs = "；".join(c["screenshot_refs"][:3])
        parts.append(
            f"<p><b>{escape(c['video_name'] or c['source'])}</b>："
            f"{escape(m['reason_type'])}，{escape(m['reason'])}。"
            f"来源：{escape(c['source'])} 行{escape(str(c['row']))}"
            f"{'，' + escape(refs) if refs else ''}。</p>"
        )
        for image in c.get("sheet_images", []):
            if image_count >= 3:
                break
            parts.append(img_tag(image["token"], width=520, name=f"{c['source']}_{c['row']}_{image['col']}.jpg"))
            image_count += 1
        if image_count >= 3:
            break
    if image_count == 0:
        parts.append("<p>该候选行未读取到可直插截图 token，仅保留时间点/截图列说明。</p>")
    return "".join(parts)


def html_table(rows: list[list[str]], header: list[str], widths: list[int] | None = None) -> str:
    widths = widths or [140] * len(header)
    colgroup = "<colgroup>" + "".join(f'<col width="{w}"/>' for w in widths) + "</colgroup>"
    thead = "<thead><tr>" + "".join(f'<th background-color="light-gray">{escape(h)}</th>' for h in header) + "</tr></thead>"
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f'<td vertical-align="top">{cell}</td>' for cell in row) + "</tr>")
    return f"<table>{colgroup}{thead}<tbody>{''.join(body)}</tbody></table>"


def decorate_matches_with_images(matches_by_q: dict[int, list[dict]], media_by_source: dict[str, dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for qnum, matches in matches_by_q.items():
        new_matches = []
        for m in matches:
            mm = json.loads(json.dumps(m, ensure_ascii=False))
            mm["candidate"]["sheet_images"] = sheet_images_for_candidate(media_by_source, mm["candidate"], limit=3)
            new_matches.append(mm)
        out[qnum] = new_matches
    return out


def build_paper_doc(
    title: str,
    questions: list[base.Question],
    matches_by_q: dict[int, list[dict]],
    question_tokens: dict[str, str],
) -> tuple[str, dict]:
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
                escape(match_summary_plain(ms)).replace("\n", "<br/>"),
            ]
        )
        qtoken = question_tokens.get(f"{title}#{q.number}")
        if qtoken:
            left_cell = f"<p><b>第{q.number}题</b></p>{img_tag(qtoken, width=560, name=f'{safe_slug(title)}_q{q.number}.png')}"
        else:
            left_cell = f"<p><b>第{q.number}题</b></p><p>{escape(base.short(q.text, 420))}</p>"
        compare_rows.append([left_cell, match_cell(ms)])

    xml = "\n".join(
        [
            f"<title>洋葱学园 VS {escape(title)} 押题对比（含截图版）</title>",
            "<h1>基本认识</h1>",
            f"<p>本卷共抽取 {len(questions)} 道题；按“核心知识点相同、素材相似、题型方法相似”任一条件判定押中。</p>",
            f"<p><b>强/弱命中题数：</b>{hit_count}/{len(questions)}；其中强命中 {strong_count} 道。</p>",
            f"<p><b>按分值估算覆盖：</b>{hit_score}/{total_score or '未知'}。</p>",
            "<p>说明：内容对比表左列为中考题目组合截图，包含题干、选项和题图；右列为洋葱解题课命中说明和视频截图证据。</p>",
            "<h1>试卷分析表</h1>",
            html_table(analysis_rows, ["题型", "题号", "分值", "考点/题型", "押中判定", "洋葱对应内容"], [90, 60, 60, 150, 80, 360]),
            "<h1>内容对比表</h1>",
            html_table(compare_rows, ["中考题目", "洋葱内容"], [560, 560]),
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
        link = p.get("url") or ""
        link_cell = f'<a href="{escape(link)}">{escape(p["title"])}</a>' if link.startswith("http") else escape(p["title"])
        rows.append([link_cell, escape(rate), escape(score), escape(str(p["strong_count"]))])

    topic_counts: dict[str, int] = {}
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
                escape(match_summary_plain(ex["matches"])).replace("\n", "<br/>"),
            ]
        )

    return "\n".join(
        [
            "<title>2026 中考化学押题对比汇总（含截图版）</title>",
            "<h1>总体说明</h1>",
            "<p>本汇总基于 16 套 2026 年中考化学试卷，以及洋葱学园三张飞书表格中的解题课视频截图生成。</p>",
            "<p>判定标准：核心知识点相同、素材相似、题型方法相似，满足任一条即计为押中；分卷正文已放置试卷题目截图和洋葱视频截图，便于逐题对比。</p>",
            "<h1>分卷文档链接</h1>",
            html_table(rows, ["试卷", "命中题数", "按分值估算覆盖", "强命中题数"], [360, 100, 130, 100]),
            "<h1>模块命中统计</h1>",
            html_table(topic_rows, ["知识模块", "命中题次数"], [260, 120]),
            "<h1>典型命中案例</h1>",
            html_table(ex_rows, ["试卷", "题号", "模块", "洋葱证据"], [220, 60, 160, 520]),
        ]
    )


def prepare_local_artifacts() -> tuple[list[dict], dict, dict[str, dict[str, str]], dict[str, dict]]:
    DOCS.mkdir(parents=True, exist_ok=True)
    CARDS.mkdir(parents=True, exist_ok=True)

    media_by_source = collect_sheet_media()
    candidates = build_candidates_with_media(media_by_source)
    save_json(IMAGE_WORK / "candidate_bank_with_images.json", [asdict(c) for c in candidates])

    card_map: dict[str, dict[str, str]] = {}
    all_results = {}
    papers_meta = []
    examples = []

    for docx_path in sorted(base.PAPER_DIR.glob("*.docx")):
        title, questions = base.extract_questions(docx_path)
        card_map[title] = {}
        for q in questions:
            card_map[title][str(q.number)] = str(render_question_card(title, q))

        matches_by_q = {q.number: base.match_question(q, candidates) for q in questions}
        matches_by_q = decorate_matches_with_images(matches_by_q, media_by_source)

        all_results[title] = {
            "questions": [asdict(q) for q in questions],
            "matches": {str(k): v for k, v in matches_by_q.items()},
            "docx": str(docx_path),
        }
        for q in questions:
            ms = matches_by_q[q.number]
            if verdict(ms) == "✅":
                examples.append({"paper": title, "qnum": q.number, "topics": q.topics, "matches": ms})

        meta = {
            "title": title,
            "question_count": len(questions),
            "hit_count": sum(1 for q in questions if verdict(matches_by_q[q.number]).startswith("✅")),
            "strong_count": sum(1 for q in questions if verdict(matches_by_q[q.number]) == "✅"),
            "total_score": sum(q.score for q in questions if q.score),
            "hit_score": sum(q.score for q in questions if verdict(matches_by_q[q.number]).startswith("✅")),
            "docx": str(docx_path),
        }
        papers_meta.append(meta)

    save_json(IMAGE_WORK / "match_results_with_images.json", all_results)
    save_json(IMAGE_WORK / "papers_meta_pre_publish.json", papers_meta)
    save_json(IMAGE_WORK / "examples.json", examples)
    return papers_meta, all_results, card_map, media_by_source


def write_xml_docs(papers_meta: list[dict], all_results: dict, question_tokens: dict[str, str], urls: dict) -> list[dict]:
    published_meta = []
    examples = load_json(IMAGE_WORK / "examples.json", [])
    title_to_meta = {p["title"]: dict(p) for p in papers_meta}

    for title, result in all_results.items():
        questions = [base.Question(**q) for q in result["questions"]]
        matches_by_q = {int(k): v for k, v in result["matches"].items()}
        xml, meta = build_paper_doc(title, questions, matches_by_q, question_tokens)
        safe = safe_slug(title)
        xml_path = DOCS / f"{safe}_含截图版.xml"
        xml_path.write_text(xml, encoding="utf-8")
        meta.update(title_to_meta[title])
        meta["local_xml"] = str(xml_path)
        if title in urls:
            meta["url"] = urls[title]["url"]
        published_meta.append(meta)

    summary_xml = build_summary_doc(published_meta, examples)
    summary_path = DOCS / "2026中考化学押题对比汇总_含截图版.xml"
    summary_path.write_text(summary_xml, encoding="utf-8")
    save_json(IMAGE_WORK / "papers_meta.json", published_meta)
    return published_meta


def create_final_docs(papers_meta: list[dict], all_results: dict, question_tokens: dict[str, str]) -> dict:
    urls = load_json(URLS_PATH, {})
    write_xml_docs(papers_meta, all_results, question_tokens, urls)
    published_meta = json.loads((IMAGE_WORK / "papers_meta.json").read_text(encoding="utf-8"))

    for paper in published_meta:
        title = paper["title"]
        if urls.get(title, {}).get("url"):
            print(f"skip {title}: {urls[title]['url']}", flush=True)
            continue
        xml = Path(paper["local_xml"]).read_text(encoding="utf-8")
        doc = create_doc_from_xml(xml)
        urls[title] = {"url": doc["url"], "document_id": doc["document_id"]}
        save_json(URLS_PATH, urls)
        print(f"created {title}: {doc['url']}", flush=True)

    write_xml_docs(papers_meta, all_results, question_tokens, urls)
    summary_title = "2026 中考化学押题对比汇总（含截图版）"
    if not urls.get(summary_title, {}).get("url"):
        summary_path = DOCS / "2026中考化学押题对比汇总_含截图版.xml"
        doc = create_doc_from_xml(summary_path.read_text(encoding="utf-8"))
        urls[summary_title] = {"url": doc["url"], "document_id": doc["document_id"]}
        save_json(URLS_PATH, urls)
        print(f"created {summary_title}: {doc['url']}", flush=True)
    else:
        print(f"skip {summary_title}: {urls[summary_title]['url']}", flush=True)
    return urls


def verify_docs(urls: dict) -> dict:
    checks = {}
    for title, item in urls.items():
        payload = run_json(
            [
                "lark-cli",
                "docs",
                "+fetch",
                "--api-version",
                "v2",
                "--doc",
                item["url"],
                "--format",
                "json",
            ],
            timeout=120,
        )
        content = payload["data"]["document"]["content"]
        checks[title] = {
            "url": item["url"],
            "has_table": "<table" in content,
            "image_count": content.count("<img "),
            "has_compare_table": "内容对比表" in content or title.startswith("2026 中考化学押题对比汇总"),
        }
        print(f"verified {title}: {checks[title]['image_count']} images", flush=True)
    save_json(FEISHU / "verification.json", checks)
    return checks


def main() -> None:
    papers_meta, all_results, card_map, _media_by_source = prepare_local_artifacts()
    question_tokens = upload_question_images(card_map)
    urls = create_final_docs(papers_meta, all_results, question_tokens)
    checks = verify_docs(urls)
    print(
        json.dumps(
            {
                "docs": len(urls),
                "question_images": len(question_tokens),
                "summary": urls.get("2026 中考化学押题对比汇总（含截图版）", {}).get("url"),
                "verified": len(checks),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(textwrap.dedent(str(exc)), file=sys.stderr)
        sys.exit(1)
