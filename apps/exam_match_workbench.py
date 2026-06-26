#!/usr/bin/env python3
"""Local web workbench for exam-paper vs video-course match auditing."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from email.policy import default as email_policy
import html
import json
import re
import shutil
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REAL_DATA = ROOT / "outputs" / "2026_yt_visual_v5" / "data"
REAL_FEISHU = ROOT / "outputs" / "2026_yt_visual_v5" / "feishu"
SAMPLE_DATA = ROOT / "sample_data"
SAMPLE_FEISHU = SAMPLE_DATA / "feishu"
DATA = REAL_DATA if (REAL_DATA / "matches_v5.json").exists() else SAMPLE_DATA
FEISHU = REAL_FEISHU if (REAL_DATA / "matches_v5.json").exists() else SAMPLE_FEISHU
MATCHES_PATH = DATA / "matches_v5.json"
OVERRIDE_DIR = DATA / "paper_tag_overrides"
DOC_URLS_PATH = FEISHU / "doc_urls_v5.json"
RUN_LOG_DIR = ROOT / "outputs" / "app_run_logs"
INTAKE_DIR = ROOT / "inputs" / "new_papers"
PIPELINE_PAPER_DIR = ROOT / "试卷" / "2026中考卷"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def paper_slug(paper: str) -> str:
    for path in OVERRIDE_DIR.glob("*_v5.json"):
        obj = load_json(path, {})
        if obj.get("paper") == paper:
            return path.stem.removesuffix("_v5")
    slug = (
        paper.replace("2026年", "")
        .replace("中考化学试卷", "")
        .replace("省", "")
        .replace("市", "")
        .replace("州", "")
        .replace("县", "")
    )
    return urllib.parse.quote(slug, safe="") or "paper"


def safe_name(text: str, fallback: str = "paper") -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text or "").strip("._")
    return text or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for idx in range(2, 200):
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"too many duplicate files for {path.name}")


def override_path_for(paper: str) -> Path:
    for path in OVERRIDE_DIR.glob("*_v5.json"):
        obj = load_json(path, {})
        if obj.get("paper") == paper:
            return path
    return OVERRIDE_DIR / f"{paper_slug(paper)}_v5.json"


def load_matches() -> list[dict[str, Any]]:
    return load_json(MATCHES_PATH, [])


def load_overrides() -> dict[str, dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    for path in sorted(OVERRIDE_DIR.glob("*_v5.json")):
        obj = load_json(path, {})
        paper = obj.get("paper")
        if paper:
            overrides[paper] = obj
    return overrides


def parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    if "multipart/form-data" not in content_type:
        raise ValueError("upload must use multipart/form-data")
    raw = b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    message = BytesParser(policy=email_policy).parsebytes(raw)
    fields: dict[str, str] = {}
    files: dict[str, dict[str, Any]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = {"filename": filename, "content": payload, "content_type": part.get_content_type()}
        else:
            fields[name] = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return fields, files


def list_intakes() -> list[dict[str, Any]]:
    items = []
    for path in sorted(INTAKE_DIR.glob("*/intake.json")):
        item = load_json(path, {})
        item["manifest_path"] = str(path)
        items.append(item)
    return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)


def save_upload_intake(fields: dict[str, str], files: dict[str, dict[str, Any]]) -> dict[str, Any]:
    paper = (fields.get("paper_title") or "").strip()
    if not paper:
        for item in files.values():
            if item.get("filename"):
                paper = Path(item["filename"]).stem
                break
    if not paper:
        raise ValueError("paper title is required")

    required = {"paper_word": [".docx"], "paper_pdf": [".pdf"]}
    optional = {"official_analysis": [".pdf", ".docx", ".txt", ".md"]}
    for field, exts in required.items():
        if field not in files or not files[field]["content"]:
            raise ValueError(f"{field} is required")
        suffix = Path(files[field]["filename"]).suffix.lower()
        if suffix not in exts:
            raise ValueError(f"{field} must be one of {', '.join(exts)}")
    for field, exts in optional.items():
        if field in files and files[field]["content"]:
            suffix = Path(files[field]["filename"]).suffix.lower()
            if suffix not in exts:
                raise ValueError(f"{field} must be one of {', '.join(exts)}")

    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    folder = INTAKE_DIR / safe_name(paper, "paper")
    folder.mkdir(parents=True, exist_ok=True)
    saved_files: dict[str, dict[str, str]] = {}
    for field, item in files.items():
        if not item["content"]:
            continue
        filename = safe_name(item["filename"], field + Path(item["filename"]).suffix)
        target = unique_path(folder / filename)
        target.write_bytes(item["content"])
        saved_files[field] = {
            "filename": filename,
            "path": str(target),
            "content_type": item.get("content_type", ""),
        }

    copied: list[dict[str, str]] = []
    copy_to_pipeline = fields.get("copy_to_pipeline") == "true"
    if copy_to_pipeline and PIPELINE_PAPER_DIR.exists():
        for field in ["paper_word", "paper_pdf", "official_analysis"]:
            saved = saved_files.get(field)
            if not saved:
                continue
            source = Path(saved["path"])
            target = unique_path(PIPELINE_PAPER_DIR / source.name)
            shutil.copy2(source, target)
            copied.append({"field": field, "path": str(target)})

    manifest = {
        "paper": paper,
        "created_at": created_at,
        "status": "uploaded",
        "files": saved_files,
        "pipeline_copy_enabled": copy_to_pipeline,
        "pipeline_paper_dir": str(PIPELINE_PAPER_DIR),
        "pipeline_copied": copied,
        "next_steps": [
            "核对 Word/PDF/解析是否同卷同名",
            "运行 professional 抽题或补齐 exam_records",
            "运行 visual_v5 --prepare 生成候选",
            "在工作台逐题审计 override",
        ],
    }
    save_json(folder / "intake.json", manifest)
    return {"ok": True, "intake": manifest, "manifest_path": str(folder / "intake.json")}


def summarize() -> dict[str, Any]:
    matches = load_matches()
    overrides = load_overrides()
    urls = load_json(DOC_URLS_PATH, {})
    papers: dict[str, dict[str, Any]] = {}
    seen_questions: set[tuple[str, int]] = set()
    for match in matches:
        paper = match.get("paper", "")
        if not paper:
            continue
        item = papers.setdefault(
            paper,
            {
                "paper": paper,
                "question_count": 0,
                "candidate_count": 0,
                "final_show_count": 0,
                "allow_count": 0,
                "exclude_count": 0,
                "manual_compare": False,
                "url": urls.get(paper, {}).get("url", ""),
            },
        )
        item["candidate_count"] += 1
        if match.get("final_show"):
            item["final_show_count"] += 1
        key = (paper, int(match.get("qnum") or 0))
        if key not in seen_questions:
            seen_questions.add(key)
            item["question_count"] += 1
    for paper, override in overrides.items():
        item = papers.setdefault(
            paper,
            {
                "paper": paper,
                "question_count": 0,
                "candidate_count": 0,
                "final_show_count": 0,
                "allow_count": 0,
                "exclude_count": 0,
                "manual_compare": False,
                "url": urls.get(paper, {}).get("url", ""),
            },
        )
        item["manual_compare"] = bool(override.get("feishu_manual_compare"))
        for question in override.get("questions", {}).values():
            if question.get("promotion") == "allow":
                item["allow_count"] += 1
            elif question.get("promotion") == "exclude":
                item["exclude_count"] += 1
    return {
        "has_data": MATCHES_PATH.exists(),
        "data_mode": "real" if DATA == REAL_DATA else "sample",
        "matches_path": str(MATCHES_PATH),
        "intakes": list_intakes(),
        "papers": sorted(papers.values(), key=lambda x: x["paper"]),
    }


def paper_detail(paper: str) -> dict[str, Any]:
    matches = [m for m in load_matches() if m.get("paper") == paper]
    override = load_overrides().get(paper, {"paper": paper, "questions": {}})
    by_q: dict[str, dict[str, Any]] = {}
    for match in matches:
        qnum = str(match.get("qnum") or "")
        item = by_q.setdefault(
            qnum,
            {
                "qnum": qnum,
                "primary_type": match.get("primary_type", ""),
                "candidates": [],
                "override": override.get("questions", {}).get(qnum, {}),
            },
        )
        item["candidates"].append(match)
    for qnum, q_override in override.get("questions", {}).items():
        by_q.setdefault(
            str(qnum),
            {"qnum": str(qnum), "primary_type": q_override.get("primary_type", ""), "candidates": [], "override": q_override},
        )
    return {
        "paper": paper,
        "override_path": str(override_path_for(paper)),
        "manual_compare": bool(override.get("feishu_manual_compare")),
        "questions": [by_q[k] for k in sorted(by_q, key=lambda x: int(x) if x.isdigit() else 999)],
    }


def update_question(payload: dict[str, Any]) -> dict[str, Any]:
    paper = str(payload.get("paper") or "")
    qnum = str(payload.get("qnum") or "")
    if not paper or not qnum:
        raise ValueError("paper and qnum are required")
    path = override_path_for(paper)
    obj = load_json(path, {"paper": paper, "questions": {}})
    obj.setdefault("paper", paper)
    questions = obj.setdefault("questions", {})
    question = questions.setdefault(qnum, {})
    for key in ["primary_type", "promotion", "audit_note", "problem_tags", "visual_forms", "method_models"]:
        if key in payload:
            question[key] = payload[key]
    if "allowed_video_ids" in payload:
        value = payload["allowed_video_ids"]
        if isinstance(value, str):
            value = [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
        question["allowed_video_ids"] = value
    save_json(path, obj)
    return {"ok": True, "path": str(path), "question": question}


def run_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action")
    paper = str(payload.get("paper") or "")
    confirm = str(payload.get("confirm") or "")
    commands = {
        "prepare": [".venv/bin/python", "scripts/visual_v5_2026_yt.py", "--prepare"],
        "sop_scan": [".venv/bin/python", ".cursor/skills/exam-video-match-audit/scripts/sop_scan.py"],
        "crop_scan": [".venv/bin/python", ".cursor/skills/exam-video-match-audit/scripts/crop_scan.py"],
    }
    if action == "republish":
        if confirm != "REPUBLISH" or not paper:
            raise ValueError("republish requires paper and confirm=REPUBLISH")
        commands[action] = [".venv/bin/python", "scripts/visual_v5_2026_yt.py", "--republish-paper", paper]
    if action not in commands:
        raise ValueError(f"unknown action: {action}")
    started = time.strftime("%Y%m%d-%H%M%S")
    log_path = RUN_LOG_DIR / f"{started}-{action}.log"
    proc = subprocess.run(commands[action], cwd=ROOT, text=True, capture_output=True, timeout=900)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "$ " + " ".join(commands[action]) + "\n\nSTDOUT:\n" + proc.stdout + "\nSTDERR:\n" + proc.stderr,
        encoding="utf-8",
    )
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "log_path": str(log_path), "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>押题对比审计工作台</title>
  <style>
    :root { color-scheme: light; --ink:#17202a; --muted:#637083; --line:#d8dee8; --bg:#f6f7f9; --panel:#fff; --accent:#0f766e; --warn:#b45309; --bad:#b91c1c; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }
    header { padding: 18px 24px; background: var(--panel); border-bottom: 1px solid var(--line); display:flex; align-items:center; justify-content:space-between; gap: 16px; }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    main { display: grid; grid-template-columns: 360px 1fr; min-height: calc(100vh - 66px); }
    aside { border-right: 1px solid var(--line); background: var(--panel); padding: 16px; overflow:auto; }
    section { padding: 18px 22px; overflow:auto; }
    button, select, input, textarea { font: inherit; }
    button { border: 1px solid var(--line); background: #fff; padding: 8px 10px; border-radius: 6px; cursor:pointer; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.danger { color: var(--bad); }
    select, input, textarea { width:100%; border:1px solid var(--line); border-radius:6px; padding:8px 10px; background:#fff; }
    textarea { min-height: 96px; resize: vertical; }
    .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .tabs { display:flex; gap:8px; margin-bottom:14px; }
    .tabs button.active { background: var(--accent); border-color: var(--accent); color:#fff; }
    .paper { border:1px solid var(--line); border-radius:8px; padding:10px; margin-bottom:10px; cursor:pointer; background:#fff; }
    .paper.active { border-color: var(--accent); box-shadow: inset 4px 0 0 var(--accent); }
    .paper h3 { margin:0 0 8px; font-size:14px; line-height:1.35; }
    .meta { color: var(--muted); font-size: 12px; display:flex; gap:8px; flex-wrap:wrap; }
    .badge { border:1px solid var(--line); border-radius:999px; padding:2px 7px; background:#fff; }
    .badge.allow { color: var(--accent); border-color:#99d4ce; }
    .badge.warn { color: var(--warn); border-color:#e7c48c; }
    .grid { display:grid; grid-template-columns: 1fr 420px; gap:16px; }
    .panel { background: var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    .question { border-bottom:1px solid var(--line); padding:12px 0; }
    .question:last-child { border-bottom:0; }
    .question h3 { margin:0 0 8px; font-size:15px; }
    .candidate { padding:10px; border:1px solid var(--line); border-radius:8px; margin-top:8px; background:#fbfcfd; }
    .candidate strong { display:block; margin-bottom:4px; }
    .row { display:grid; grid-template-columns: 120px 1fr; gap:10px; align-items:start; margin-bottom:10px; }
    pre { white-space:pre-wrap; background:#101820; color:#e6edf5; padding:12px; border-radius:8px; max-height:240px; overflow:auto; }
    a { color:#0f5f9e; text-decoration:none; }
    a:hover { text-decoration:underline; }
    @media (max-width: 980px) { main, .grid { grid-template-columns:1fr; } aside { border-right:0; border-bottom:1px solid var(--line); } }
  </style>
</head>
<body>
  <header>
    <h1>中考化学押题对比审计工作台</h1>
    <div class="toolbar">
      <button onclick="runAction('prepare')">生成候选</button>
      <button onclick="runAction('sop_scan')">SOP 扫描</button>
      <button onclick="runAction('crop_scan')">裁图扫描</button>
      <button class="primary" onclick="showUpload()">新卷上传</button>
    </div>
  </header>
  <main>
    <aside>
      <div class="meta" id="dataStatus"></div>
      <div id="papers"></div>
    </aside>
    <section>
      <div id="content" class="panel">正在读取本地数据...</div>
    </section>
  </main>
<script>
let state = { papers: [], current: null, detail: null };

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || res.statusText);
  return data;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function zhPromotion(value) {
  return ({allow: '宣传版通过', exclude: '排除', unset: '未设置'}[value || 'unset']) || value;
}

function zhBool(value) {
  if (value === true || value === 'true') return '是';
  if (value === false || value === 'false') return '否';
  return value || '否';
}

function zhStatus(value) {
  return ({uploaded: '已上传'}[value]) || value || '未知';
}

function zhAction(action) {
  return ({
    prepare: '生成候选',
    sop_scan: 'SOP 扫描',
    crop_scan: '裁图扫描',
    republish: '重发当前卷'
  }[action]) || action;
}

async function loadSummary() {
  const data = await api('/api/summary');
  state.papers = data.papers;
  const modeText = data.data_mode === 'real' ? '真实数据' : '脱敏样例';
  document.getElementById('dataStatus').innerHTML = data.has_data ? `<span class="badge allow">已读取匹配数据</span><span class="badge">${modeText}</span>` : `<span class="badge warn">未找到数据</span>`;
  state.intakes = data.intakes || [];
  renderPapers();
  if (!state.current && state.papers.length) await selectPaper(state.papers[0].paper);
}

function renderPapers() {
  document.getElementById('papers').innerHTML = state.papers.map(p => `
    <div class="paper ${state.current === p.paper ? 'active' : ''}" onclick="selectPaper('${esc(p.paper)}')">
      <h3>${esc(p.paper)}</h3>
      <div class="meta">
        <span class="badge">题 ${p.question_count}</span>
        <span class="badge allow">宣传版 ${p.allow_count}</span>
        <span class="badge">最终展示 ${p.final_show_count}</span>
        ${p.manual_compare ? '<span class="badge warn">手调保护</span>' : ''}
      </div>
    </div>`).join('') + `
    <div class="panel">
      <h3>新卷交接包</h3>
      <div class="meta">${(state.intakes || []).length ? state.intakes.slice(0, 4).map(x => `<span class="badge">${esc(x.paper)}</span>`).join('') : '<span class="badge">暂无上传</span>'}</div>
    </div>`;
}

function showUpload() {
  document.getElementById('content').innerHTML = `
    <div class="grid">
      <div class="panel">
        <h2>新卷上传</h2>
        <p class="meta">上传标准交接包：Word 用于抽题，PDF 用于裁题图，官方解析用于设问任务审计。</p>
        <form id="uploadForm">
          <div class="row"><label>标准卷名</label><input name="paper_title" placeholder="2026年××市中考化学试卷" required /></div>
          <div class="row"><label>真题 Word</label><input name="paper_word" type="file" accept=".docx" required /></div>
          <div class="row"><label>真题 PDF</label><input name="paper_pdf" type="file" accept=".pdf" required /></div>
          <div class="row"><label>官方解析</label><input name="official_analysis" type="file" accept=".pdf,.docx,.txt,.md" /></div>
          <div class="row"><label>同步流水线</label><label><input name="copy_to_pipeline" type="checkbox" value="true" checked style="width:auto" /> 若存在试卷目录，同步到试卷/2026中考卷</label></div>
          <button class="primary" type="submit">上传交接包</button>
        </form>
      </div>
      <div class="panel">
        <h2>上传记录</h2>
        <div id="intakeList">${renderIntakes()}</div>
        <h2>上传结果</h2>
        <pre id="uploadLog">暂无</pre>
      </div>
    </div>`;
  document.getElementById('uploadForm').addEventListener('submit', uploadIntake);
}

function renderIntakes() {
  const items = state.intakes || [];
  if (!items.length) return '<p class="meta">暂无上传记录。</p>';
  return items.map(x => `<div class="candidate"><strong>${esc(x.paper)}</strong><div class="meta"><span>${esc(x.created_at)}</span><span>${esc(zhStatus(x.status))}</span></div><p>${esc(x.manifest_path || '')}</p></div>`).join('');
}

async function uploadIntake(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  if (!data.has('copy_to_pipeline')) data.set('copy_to_pipeline', 'false');
  const log = document.getElementById('uploadLog');
  log.textContent = '上传中...';
  try {
    const res = await fetch('/api/upload-intake', { method:'POST', body:data });
    const payload = await res.json();
    if (!res.ok || payload.error) throw new Error(payload.error || res.statusText);
    log.textContent = JSON.stringify(payload, null, 2);
    await loadSummary();
    document.getElementById('intakeList').innerHTML = renderIntakes();
    form.reset();
  } catch (err) {
    log.textContent = String(err);
  }
}

async function selectPaper(paper) {
  state.current = paper;
  renderPapers();
  state.detail = await api('/api/paper?paper=' + encodeURIComponent(paper));
  renderDetail();
}

function renderDetail() {
  const d = state.detail;
  const p = state.papers.find(x => x.paper === d.paper) || {};
  document.getElementById('content').innerHTML = `
    <div class="grid">
      <div class="panel">
        <h2>${esc(d.paper)}</h2>
        <div class="meta">
          <span class="badge">审计配置：${esc(d.override_path)}</span>
          ${p.url ? `<a class="badge" href="${esc(p.url)}" target="_blank">飞书文档</a>` : ''}
          ${d.manual_compare ? '<span class="badge warn">飞书手调保护</span>' : ''}
        </div>
        ${d.questions.map(renderQuestion).join('')}
      </div>
      <div class="panel">
        <h2>发布操作</h2>
        <p class="meta">整卷重发会覆盖飞书文档内容；手调右列的卷请保持保护。</p>
        <div class="row"><label>确认文本</label><input id="confirmRepublish" placeholder="输入 REPUBLISH 确认覆盖" /></div>
        <button class="danger" onclick="runAction('republish')">重发当前卷</button>
        <h2>运行日志</h2>
        <pre id="runLog">暂无</pre>
      </div>
    </div>`;
}

function renderQuestion(q) {
  const o = q.override || {};
  const candidates = q.candidates || [];
  const top = candidates.slice(0, 4);
  return `
    <div class="question">
      <h3>第 ${esc(q.qnum)} 题 <span class="badge">${esc(o.primary_type || q.primary_type || '未标题型')}</span> <span class="badge ${o.promotion === 'allow' ? 'allow' : ''}">${esc(zhPromotion(o.promotion))}</span></h3>
      <div class="row"><label>审计结论</label><select id="promotion-${q.qnum}"><option value="exclude" ${o.promotion === 'exclude' ? 'selected' : ''}>排除</option><option value="allow" ${o.promotion === 'allow' ? 'selected' : ''}>宣传版通过</option></select></div>
      <div class="row"><label>视频白名单</label><input id="videos-${q.qnum}" value="${esc((o.allowed_video_ids || []).join(', '))}" placeholder="例如：XZK-21, JCTB-158" /></div>
      <div class="row"><label>审计理由</label><textarea id="note-${q.qnum}">${esc(o.audit_note || '')}</textarea></div>
      <button class="primary" onclick="saveQuestion('${esc(q.qnum)}')">保存 Q${esc(q.qnum)}</button>
      ${top.map(c => `<div class="candidate"><strong>${esc(c.video_id || '无匹配')} ${esc(c.video_name || '')}</strong><div class="meta"><span>匹配分 ${esc(c.score)}</span><span>最终展示 ${esc(zhBool(c.final_show))}</span></div><p>${esc(c.hit_reason || c.reject_reason || c.review_reason || '')}</p></div>`).join('')}
    </div>`;
}

async function saveQuestion(qnum) {
  const payload = {
    paper: state.current,
    qnum,
    promotion: document.getElementById('promotion-' + qnum).value,
    allowed_video_ids: document.getElementById('videos-' + qnum).value,
    audit_note: document.getElementById('note-' + qnum).value
  };
  const data = await api('/api/question', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  document.getElementById('runLog').textContent = '已保存：' + data.path;
  await selectPaper(state.current);
  await loadSummary();
}

async function runAction(action) {
  const payload = {action, paper: state.current};
  if (action === 'republish') payload.confirm = document.getElementById('confirmRepublish')?.value || '';
  document.getElementById('runLog').textContent = `${zhAction(action)}运行中...`;
  try {
    const data = await api('/api/run', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    document.getElementById('runLog').textContent = `是否成功：${zhBool(data.ok)}\n退出码：${data.returncode}\n日志文件：${data.log_path}\n\n标准输出：\n${data.stdout}\n\n错误输出：\n${data.stderr}`;
    await loadSummary();
  } catch (err) {
    document.getElementById('runLog').textContent = String(err);
  }
}

loadSummary().catch(err => document.getElementById('content').textContent = err);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/summary":
                self._send_json(summarize())
            elif parsed.path == "/api/intakes":
                self._send_json({"intakes": list_intakes()})
            elif parsed.path == "/api/paper":
                paper = query.get("paper", [""])[0]
                self._send_json(paper_detail(paper))
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:
            self._send_json({"error": html.escape(str(exc))}, 500)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/question":
                self._send_json(update_question(self._read_json()))
            elif self.path == "/api/run":
                self._send_json(run_action(self._read_json()))
            elif self.path == "/api/upload-intake":
                length = int(self.headers.get("Content-Length") or "0")
                fields, files = parse_multipart(self.rfile.read(length), self.headers.get("Content-Type", ""))
                self._send_json(save_upload_intake(fields, files))
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Exam Match Workbench: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
