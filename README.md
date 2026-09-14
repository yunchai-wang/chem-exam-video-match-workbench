# Exam Video Match Workbench / L4 课程自进化平台

中考化学试卷与洋葱视频课的相似题审计工作台。

这个项目沉淀自 2026 中考化学 16 套卷押题对比工作流，目标是把“真题试卷 + 视频课库 + 人工审计规则”变成可复用的业务工具：先由引擎完成题目抽取、候选匹配、PDF 题图裁切和飞书文档生成，再由教研人员通过三像门禁完成最终白名单审计。

## MVP

- 读取 `matches_v5.json`、`paper_tag_overrides/*.json`、`doc_urls_v5.json`
- 按试卷查看命中数、分值覆盖、待审题、飞书发布链接
- 在本地 Web App 中编辑单题 `promotion`、`allowed_video_ids`、`audit_note`
- 在 APP 中上传新卷交接包：真题 Word、真题 PDF、官方解析
- 一键运行本地校验：`--prepare`、`sop_scan.py`、`crop_scan.py`
- 对飞书整卷重发加确认门禁，避免覆盖手调右列

## L4 课程自进化平台（一期可运行版本）

当前仓库已经新增一条独立的 L4 纵向闭环，用于验证：

- 生产项目目标与教师介入策略；
- 高频、好题、内容健康、视频生产覆盖和生产优先级的独立判断；
- “组合支撑但缺综合迁移”的保守覆盖口径；
- 自动推进、只看异常、每次确认、禁止自动执行四种节点策略；
- 成品后验反馈、上游归因、项目规则实验和最小范围重跑；
- 跨项目公共规则/公共 Skill 始终需要人工批准。

启动：

```bash
cd chem-exam-video-match-workbench
python3 apps/l4_course_evolution_workbench.py
```

打开 `http://127.0.0.1:8766`。首次启动会将 `sample_data/l4_workbench/seed.json` 复制成被 Git 忽略的本地状态文件 `outputs/l4_workbench/state.json`，刷新页面后设置和反馈仍会保留。

一期使用脱敏样例验证产品结构。Feishu Base 已支持通过本机 `lark-cli` 做只读预览、字段映射和本地冻结导入；本地 JSON/CSV 题目清单也可映射既有字段并优先复用本机题图。PMO、CB、素材库和正式云文档写入仍只展示连接与授权状态，不会误写真实业务数据。

### 当前生产就绪边界

已经真实可用：

- 本地 JSON 状态的跨线程/跨进程写锁、乐观版本校验、原子写入、自动备份与恢复；
- PDF、Word、表格、题图、本地文件夹和 CB 导出文件的不可变输入快照，含文件清单、大小与 SHA-256；
- Word、PDF、图片、文本、CSV、JSON 和 XLSX 的标准化入口；Word 会保留段落、公式文本、表格及内嵌原图，题干声明含图但未解析到图片时会阻断并报错；
- 本地 JSON/CSV 结构化题目清单预览、字段映射、题图覆盖检查与冻结导入；原始文件保持只读，重复题图按内容复用，缺图和疑似整卷长图进入异常清单；
- Feishu Base 只读预览、当前视图筛选回显、字段映射与本地冻结导入；原字段和标准字段并存，不覆盖原表；
- 标准题目资产的内容块、来源定位、指纹去重、图片完整性与异常清单；
- 首轮真实教研诊断：只按显式底层结构计算同年跨地区复现度，高频与好题分别给出；单年数据不冒充多年趋势，缺视频或学生证据时不强行生成生产优先级；
- 30/40/50 题待校准金样本自动抽样，覆盖试卷、题型、难度、图表形态和异常门禁，并保留每题入样理由；
- 视频证据 JSON 的本地冻结、视频 ID 去重、逐字稿证据分级和金样本保守覆盖候选；旧宣传匹配结论不写入生产覆盖字段，自动结果最高只到“部分覆盖候选”；
- 分阶段幂等任务记录、失败状态与重试基础；
- 按“训练年份早于验证年份”冻结预测，并用真实后验标签计算 TP/FP/FN/TN、精确率和召回率；
- 数据快照、任务健康和回测结果的工作台页面。

仍是明确标注的契约预演：

- 扫描 PDF 的中文 OCR、Base 远程题图下载物化、复杂数学公式的完整 OOXML/LaTeX 还原；
- 多年份趋势、最终生产优先级、母题、教案、逐字稿、分镜和 PPT/HTML 的真实 AI/Skill 执行器；
- Feishu Base 写回、PMO、CB、素材库、云文档及兼职触达的正式写入连接；
- 真实教师项目数据上的规则 A/B 结论。

因此当前版本适合直接用本地清单和题图建立受控真实样本，并开始教研校准；AI 候选好题仍须核验科学性，不应把单年复现度、结构预览或待补证据字段包装成正式趋势、生产优先级或比赛效果数据。实施清单见 [`docs/superpowers/plans/2026-09-10-l4-production-foundation.md`](docs/superpowers/plans/2026-09-10-l4-production-foundation.md)，完整路线与工期见 [`docs/superpowers/plans/2026-09-11-l4-full-delivery-roadmap.md`](docs/superpowers/plans/2026-09-11-l4-full-delivery-roadmap.md)。

资料解析可选依赖见 `requirements-l4.txt`。没有安装 `pypdf` 或 `openpyxl` 时，系统会显式报告相应解析能力缺失；PDF 页面原图仍优先通过本机 `pdftoppm` 保留，不会静默丢图。

## 原押题宣传审计工作台

```bash
cd chem-exam-video-match-workbench
python3 apps/exam_match_workbench.py
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
