# Codex 训练营第三期作业：中考化学试卷 × 视频课相似题审计工作台

## 原始需求

每年中考真题发布后，业务方需要快速判断洋葱视频课是否覆盖了真题中的相似题，用于宣传展示、教研复盘和课程内容评估。

原始人工流程存在几个问题：

- 直接人工逐题对比效率低，16 套卷会形成大量重复判断。
- 相似题标准容易漂移，容易出现“都有表格”“都有 CO2”这类弱相似误配。
- 真题 PDF 题图、视频截图、审计理由、飞书宣传文档分散维护，复盘成本高。
- 后续年份或其他学科复用时，缺少稳定 SOP 和自动化回归检查。

## MVP 版本

MVP 做成一个本地 Web 工作台，底层复用已有 v5 引擎和项目 Skill。

核心能力：

1. 读取本地匹配结果、人工 override 和飞书发布链接。
2. 按试卷展示命中题数、覆盖分值、待审题数、飞书文档入口。
3. 展示单题候选视频、命中理由、拒绝理由和 final_show 状态。
4. 允许教研人员编辑单题 `promotion`、`allowed_video_ids`、`audit_note`。
5. 提供新卷上传入口，接收 Word、PDF、官方解析并生成 intake 清单。
6. 一键运行 `--prepare`、`sop_scan.py`、`crop_scan.py` 做发布前检查。
7. 对整卷飞书重发设置二次确认，保护已经手调过的右列视频截图。

## 验收标准

- 新增或修改一套卷后，可以通过工作台完成：选卷 -> 审题 -> 写白名单 -> 运行 prepare -> 跑 SOP 扫描 -> 跑裁图扫描。
- 新卷上传后生成 `inputs/new_papers/<卷名>/intake.json`，并在真实业务仓库中可同步到 `试卷/2026中考卷/`。
- 宣传版 `promotion: allow` 的题目必须满足“题图像 + 任务像 + 解法像”。
- 复合题必须在 `audit_note` 或 `task_video_map` 中说明小问与视频课的对应关系。
- `sop_scan.py` 的 P0 问题清零后再发布宣传版。
- `crop_scan.py` 无 P1 跨页裁图问题，或已人工确认。
- 飞书手调卷设置 `feishu_manual_compare: true` 时，APP 不允许无确认整卷覆盖。
- 项目可迁移：换年份或学科时，保留引擎、override 机制、Skill 结构，只替换数据源、规则表和易混 ID 表。

## 沉淀项目 Skills

项目级 Skill：`.cursor/skills/exam-video-match-audit/`

沉淀内容：

- 三像门禁：题图像、任务像、解法像。
- 双层标准：宣传版白名单与对比表展示层分离。
- 曲线类型门禁：产率、溶解度、滴定 pH、科普折线、双线证据等。
- 易混视频 ID 表：避免把流程课、溶解度课、控制变量课互相误配。
- 复合题拆小问方法：用 `task_video_map` 和 `video_similarity_notes` 固化审计理由。
- 回归脚本：`sop_scan.py` 和 `crop_scan.py`。

这部分就是本项目的 Superpower：不是只完成一次任务，而是把 Codex 的操作经验沉淀成后续可复用的审计能力。

## 项目代码 GitHub/GitLab

建议仓库名：

```text
chem-exam-video-match-workbench
```

建议设为 private。原因是项目包含真实试卷、内部视频截图、飞书文档链接和业务过程数据。对外展示时只提交代码、说明文档、项目 Skill 和脱敏样例。

推荐提交内容：

- `apps/exam_match_workbench.py`
- `scripts/*.py`
- `.cursor/skills/exam-video-match-audit/`
- `README.md`
- `docs/codex-training-submission.md`
- `.gitignore`
- `sample_data/` 脱敏样例

不推荐提交内容：

- `outputs/`
- `试卷/2026中考卷/`
- 真实 PDF/Word/截图
- 飞书 token、图片 token、Base record 导出

## 优秀作业亮点

- 有真实业务深度：已经服务 2026 中考化学 16 套卷押题对比。
- 有真实业务产物：飞书分卷宣传文档、过程库 Base、方法论 Wiki、汇总文档。
- 有人机协同边界：引擎负责粗筛、裁图、生成；人工负责宣传版最终白名单。
- 有质量回归：P0 错配扫描、跨页裁图扫描、手调飞书保护。
- 有可复用 Superpower：项目 Skill 能指导后续同事或 AI 助手接手新卷。
- 有可迁移性：保留 “试卷 + 课库 + 规则 + override + 发布” 架构，可迁移到其他年份或学科。

## 演示路径

1. 启动 APP：`.venv/bin/python apps/exam_match_workbench.py`
2. 选择一套卷，查看宣传版命中、待审题和飞书链接。
3. 打开一道复合题，展示候选视频和审计理由。
4. 修改 `allowed_video_ids` 或 `audit_note`，保存到 override。
5. 运行 `prepare`、`sop_scan`、`crop_scan`。
6. 展示项目 Skill，说明这套方法如何被沉淀为 Superpower。
