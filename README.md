# Exam Video Match Workbench

中考化学试卷与洋葱视频课的相似题审计工作台。

这个项目沉淀自 2026 中考化学 16 套卷押题对比工作流，目标是把“真题试卷 + 视频课库 + 人工审计规则”变成可复用的业务工具：先由引擎完成题目抽取、候选匹配、PDF 题图裁切和飞书文档生成，再由教研人员通过三像门禁完成最终白名单审计。

## MVP

- 读取 `matches_v5.json`、`paper_tag_overrides/*.json`、`doc_urls_v5.json`
- 按试卷查看命中数、分值覆盖、待审题、飞书发布链接
- 在本地 Web App 中编辑单题 `promotion`、`allowed_video_ids`、`audit_note`
- 在 APP 中上传新卷交接包：真题 Word、真题 PDF、官方解析
- 一键运行本地校验：`--prepare`、`sop_scan.py`、`crop_scan.py`
- 对飞书整卷重发加确认门禁，避免覆盖手调右列

## Quick Start

```bash
cd chem-exam-video-match-workbench
.venv/bin/python apps/exam_match_workbench.py
```

打开终端输出里的本地地址，默认是 `http://127.0.0.1:8765`。

如果仓库里存在真实 `outputs/2026_yt_visual_v5/data/matches_v5.json`，工作台会读取真实业务数据；否则自动读取 `sample_data/` 脱敏样例，方便训练营评审直接查看界面。

## New Paper Intake

点击页面右上角「新卷上传」，上传：

- 真题 Word（`.docx`）：用于抽题、题号、分值、题型和题干结构化
- 真题 PDF（`.pdf`）：用于宣传版左列题图裁切
- 官方解析（`.pdf` / `.docx` / `.txt` / `.md`）：用于设问任务和相似度审计

上传后会生成：

```text
inputs/new_papers/<卷名>/intake.json
```

如果当前是真实业务仓库且存在 `试卷/2026中考卷/`，勾选「同步流水线」后会把 Word/PDF/解析同步到该目录，后续继续运行原有抽题与 `prepare` 流程。

## Superpowers / Skills

项目级 Skill 位于：

```text
.cursor/skills/exam-video-match-audit/
```

它沉淀了押题对比审计的核心规则，包括：

- `SKILL.md`：审计流程、双层标准、飞书发布注意事项
- `reference.md`：三像门禁、曲线类型、易混视频 ID、复合题专课映射
- `examples.md`：典型错配和标杆案例
- `scripts/sop_scan.py`：白名单规则回归扫描
- `scripts/crop_scan.py`：PDF 题图跨页裁切自检

## Data Safety

仓库默认忽略 `outputs/`、`inputs/`、试卷 PDF/Word、截图和飞书导出产物。对外提交时建议只提交代码、Skill、说明文档和 `sample_data/` 脱敏示例；真实试卷、内部视频截图、飞书 token 或发布记录不要推到公开仓库。

## Training Submission

训练营作业说明见 [docs/codex-training-submission.md](docs/codex-training-submission.md)。
