#!/usr/bin/env python3
"""Create a v3.2 patch for Yunnan Q17 process-flow evidence and Q20 crop."""

from __future__ import annotations

import json
import subprocess
from html import escape
from pathlib import Path

import fitz
from PIL import Image

import visual_v3_2026_yt as v3


ROOT = Path("/Users/mbpro/Desktop/AI/chem-exam-analysis")
WORK = ROOT / "outputs" / "2026_yt_visual_v32_yunnan_process_patch"
DOCS = WORK / "docs"
FEISHU = WORK / "feishu"
IMAGES = WORK / "images"
PAPER = "2026年云南省中考化学试卷"


def ensure_dirs() -> None:
    for path in [WORK, DOCS, FEISHU, IMAGES]:
        path.mkdir(parents=True, exist_ok=True)


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


def ensure_staging_doc() -> dict:
    staging_path = v3.FEISHU / "staging_doc.json"
    if staging_path.exists():
        return load_json(staging_path)
    return v3.ensure_staging_doc()


def upload_image(key: str, file_path: Path, width: str = "720") -> str:
    token_path = FEISHU / "image_tokens_v32.json"
    tokens = load_json(token_path) if token_path.exists() else {}
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
    return tokens[key]


def stitch_pdf_clips(clips: list[tuple[int, fitz.Rect]], out: Path) -> Path:
    pdf = ROOT / "试卷" / "2026中考卷" / f"{PAPER}.pdf"
    doc = fitz.open(pdf)
    parts = []
    for idx, (page_idx, rect) in enumerate(clips, 1):
        pix = doc[page_idx].get_pixmap(matrix=fitz.Matrix(2.15, 2.15), clip=rect, alpha=False)
        part = out.with_name(f"{out.stem}_part{idx}.png")
        pix.save(str(part))
        parts.append(Image.open(part).convert("RGB"))
    doc.close()
    width = max(img.width for img in parts)
    height = sum(img.height for img in parts)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for img in parts:
        canvas.paste(img, (0, y))
        y += img.height
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def make_q20_full_crop() -> Path:
    pdf = ROOT / "试卷" / "2026中考卷" / f"{PAPER}.pdf"
    doc = fitz.open(pdf)
    clips = [
        (8, fitz.Rect(42, 326, doc[8].rect.width - 42, doc[8].rect.height - 25)),
        (9, fitz.Rect(42, 45, doc[9].rect.width - 42, doc[9].rect.height - 25)),
    ]
    doc.close()
    return stitch_pdf_clips(clips, IMAGES / "yunnan_q20_full.png")


def img(token: str, width: int = 540, name: str = "") -> str:
    name_attr = f' name="{escape(name)}"' if name else ""
    return f'<img src="{escape(token)}" width="{width}"{name_attr}/>' if token else ""


def table(rows: list[list[str]], headers: list[str], widths: list[int]) -> str:
    colgroup = "<colgroup>" + "".join(f'<col width="{w}"/>' for w in widths) + "</colgroup>"
    thead = "<thead><tr>" + "".join(f'<th background-color="light-gray">{escape(h)}</th>' for h in headers) + "</tr></thead>"
    tbody = "<tbody>" + "".join("<tr>" + "".join(f'<td vertical-align="top">{c}</td>' for c in row) + "</tr>" for row in rows) + "</tbody>"
    return f"<table>{colgroup}{thead}{tbody}</table>"


def existing_q_token(qnum: int) -> str:
    exams = load_json(v3.DATA / "exam_records_v3.json")
    q = next(x for x in exams if x["paper"] == PAPER and x["qnum"] == qnum)
    return q.get("pdf_crop_token", "")


def rich_tokens() -> dict:
    p = ROOT / "outputs" / "2026_yt_compare_image" / "rich_cells" / "重难点培优.json"
    data = load_json(p)
    return {
        "ZND-29-N": data["29"]["N"]["tokens"][0]["token"],
        "ZND-29-P": data["29"]["P"]["tokens"][0]["token"],
        "ZND-24-S": data["24"]["S"]["tokens"][0]["token"],
        "ZND-24-T": data["24"]["T"]["tokens"][0]["token"],
    }


def yunnan_patch_doc(q20_token: str, tokens: dict) -> str:
    q17_left = img(existing_q_token(17), 560, "yunnan_q17.png")
    q20_left = img(q20_token, 560, "yunnan_q20_full.png")
    q17_right = "".join(
        [
            "<p><b>工艺流程题（生产甲醇）</b></p>",
            "<p>同为设备流程图，均出现塔式设备/装置串联；题干关键词“吸收/合成”与视频截图中的“吸收塔/合成塔”外观和流程任务相似。</p>",
            img(tokens["ZND-29-N"], 520, "ZND-29-N.png"),
            "<p>同为从原料到产品的工业设备流程，含吸收塔、合成塔等关键设备。</p>",
            img(tokens["ZND-29-P"], 520, "ZND-29-P.png"),
            "<p>同属工艺流程信息读取，围绕塔设备中发生的反应和物质去向设问。</p>",
            "<p><b>价类图与工艺流程综合题</b></p>",
            "<p>同问“流程中可循环利用的物质”，设问形式与云南第17题第(5)问高度相似。</p>",
            img(tokens["ZND-24-T"], 520, "ZND-24-T.png"),
            "<p>同为先分析每步反应、再找最终产物是否在前面出现，从而判断可循环利用物质。</p>",
        ]
    )
    q20_right = "".join(
        [
            "<p><b>截图修复说明</b></p>",
            "<p>第20题为跨页题，v3 原裁图只截到第9页，漏掉第10页续表、pH/溶氧量曲线和方案设计任务。v3.2 左列已改为第9-10页完整拼接图。</p>",
        ]
    )
    analysis_rows = [
        ["工艺流程题", "17", "8", "塔设备流程、吸收/合成、可循环利用物质", "补充命中", "重难点培优：工艺流程题（生产甲醇）；价类图与工艺流程综合题"],
        ["科普/实验探究综合", "20", "9", "跨页表格、曲线、方案设计", "截图修复", "左列题目截图改为完整跨页裁图"],
    ]
    compare_rows = [
        [f"<p><b>第17题</b></p>{q17_left}", q17_right],
        [f"<p><b>第20题</b></p>{q20_left}", q20_right],
    ]
    return "\n".join(
        [
            "<title>洋葱学园 VS 2026年云南省中考化学试卷 押题对比（v3.2工艺流程修正版）</title>",
            "<h1>基本认识</h1>",
            "<p>v3.2 补充云南第17题的工艺流程塔设备/可循环物质匹配，并修复第20题跨页题目截图。</p>",
            "<h1>试卷分析表</h1>",
            table(analysis_rows, ["题型", "题号", "分值", "视觉锚点/核心模型", "更新", "洋葱对应内容"], [100, 55, 55, 250, 90, 420]),
            "<h1>内容对比表</h1>",
            table(compare_rows, ["中考题目", "洋葱内容"], [560, 560]),
        ]
    )


def process_doc(yunnan_url: str) -> str:
    rows = [
        ["第17题漏掉生产甲醇", "视频截图证据只打了“流程图”，没有从截图/逐字稿补出“吸收塔、合成塔、塔设备流程”。", "新增“塔设备/吸收塔/合成塔”作为工艺流程视觉锚点。"],
        ["第17题漏掉可循环利用物质", "价类图与工艺流程综合题的逐字稿已命中，但截图库只取前5张，最后一张T列未入库。", "重点视频补全全部截图列，T列作为“可循环利用物质”证据。"],
        ["第20题截图不完整", "最后一题没有下一题作为裁剪边界，PDF裁图只截了第9页。", "对跨页长题启用指定页拼接，第20题拼接第9-10页。"],
        ["逐字稿作用", "生产甲醇逐字稿为弱匹配，系统未采用强标签；价类图逐字稿已匹配并包含循环物质设问。", "弱匹配逐字稿可作为人工校准提示，强匹配逐字稿可直接补问题标签。"],
    ]
    return "\n".join(
        [
            "<title>v3.2规则补丁：云南17工艺流程与20题截图审计</title>",
            "<h1>补丁结论</h1>",
            f'<p>已新生成云南卷修正版交付文档：<a href="{escape(yunnan_url)}">云南卷 v3.2工艺流程修正版</a></p>',
            "<p>本补丁不覆盖旧 v3/v3.1 文档。</p>",
            "<h1>问题诊断</h1>",
            table(rows, ["问题", "原表现", "v3.2修正"], [130, 430, 430]),
            "<h1>新增规则</h1>",
            "<p>工艺流程题除“流程图”外，还需要识别设备型锚点：吸收塔、合成塔、反应塔、塔设备、循环利用物质、可循环使用物质。</p>",
            "<p>视频截图库不能只截前5张；对于命中“可循环利用物质”等末尾设问的视频，要补全全部截图列。</p>",
            "<p>PDF最后一题或跨页长题不能只依赖“下一题题号”裁剪，应检测页内续表/曲线/任务段落并拼接续页。</p>",
        ]
    )


def summary_doc(yunnan_url: str, process_url: str) -> str:
    return "\n".join(
        [
            "<title>2026 中考化学押题对比汇总（v3.2补丁）</title>",
            "<h1>本次补丁</h1>",
            table(
                [
                    [f'<a href="{escape(yunnan_url)}">云南卷 v3.2工艺流程修正版</a>', "第17题补充生产甲醇/可循环物质证据；第20题修复跨页截图"],
                    [f'<a href="{escape(process_url)}">v3.2规则补丁过程文档</a>', "记录规则漏洞、逐字稿作用和后续规则"],
                ],
                ["文档", "更新内容"],
                [420, 520],
            ),
        ]
    )


def main() -> None:
    ensure_dirs()
    q20_path = make_q20_full_crop()
    q20_token = upload_image("Q::YUNNAN_Q20_FULL_V32_CLEAN", q20_path)
    tokens = rich_tokens()
    y_xml = yunnan_patch_doc(q20_token, tokens)
    (DOCS / "云南卷_v3.2工艺流程修正版.xml").write_text(y_xml, encoding="utf-8")
    y_doc = create_doc_from_xml(y_xml)
    p_xml = process_doc(y_doc["url"])
    (DOCS / "v3.2规则补丁_云南17工艺流程与20题截图审计.xml").write_text(p_xml, encoding="utf-8")
    p_doc = create_doc_from_xml(p_xml)
    s_xml = summary_doc(y_doc["url"], p_doc["url"])
    (DOCS / "2026中考化学押题对比汇总_v3.2补丁.xml").write_text(s_xml, encoding="utf-8")
    s_doc = create_doc_from_xml(s_xml)
    urls = {"云南卷v3.2": y_doc, "过程补丁": p_doc, "汇总补丁": s_doc}
    save_json(FEISHU / "doc_urls_v32_patch.json", urls)
    print(json.dumps(urls, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
