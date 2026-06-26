---
name: exam-video-match-audit
description: >-
  Audits exam-paper vs video-course matching for promotion docs (题图像+任务像+解法像).
  Writes paper_tag_overrides JSON, runs visual pipeline prepare/republish, and scans for
  mis-matches (composite sub-questions, curve types, filter/solubility/yield videos).
  Use when doing 押题对比、相似题审计、promotion allow/exclude、paper_tag_overrides、
  visual_v5、洋葱解题课匹配、飞书分卷重发, or fixing allowed_video_ids whitelist.
---

# 试卷×视频押题对比审计

宣传版标准：**题图像 + 任务像 + 解法像**。过程库可留弱相关；不能仅凭「都有表格/都含 CO₂」进宣传版。

## 双层标准（扬州 / 苏州 / 湖南 / 重庆定稿）

| 层级 | 字段 | 标准 | 计分 |
|------|------|------|------|
| **宣传版** | `promotion: allow` + `allowed_video_ids` | 题型大类对大类；三像严门禁 | 计入分析表 ✅ 与分值覆盖 |
| **对比表展示** | `comparison_show: true` + `comparison_video_ids` | 大题锚重点小问；**任务像+解法像**即可（题图/素材可弱） | **不计**宣传版命中数 |

**选择题**：默认不进；破例须任务专且复杂（扬州 Q18 溶解度专课、Q20 柱图+控制变量）。

**项目式/跨学科**：用 `task_video_map` 按任务块/小问挂多课；**专课优先于泛方法论**（苏州 Q21 制氧三课，勿堆 XZK-36）。**按截图场景选课**（重庆 Q22 项目一导电性 → XZK-38 下，非 XZK-36 上）。

**对比表策展**：分析表 `allowed_video_ids` 可宽；对比表用 `compare_table_video_ids` / `compare_table_show` 精选（湖南 Q19 只展示 ZND-14；Q14 不占行）。

**解法相似**：看 **条件怎么给**（图/表/流程/设问句式）+ **学生动作**；素材行业可不同。子模式见 SOP §8.6–§8.8（双曲线+依据 → `ZND-9`；设问拆课 → 湖南 Q22；项目块 → 重庆 Q22）。

**飞书已定稿**：卷级 `feishu_manual_compare: true` → **禁止** `--republish-paper` 覆盖右列（湖南/重庆/扬州/苏州）。

## 开始前向用户确认的资料

| 级别 | 资料 |
|------|------|
| 必需（新卷推荐） | **Word（.docx）+ PDF + 官方解析/答案**、标准卷名（与文件名一致） |
| 必需（课库侧） | 视频元数据表（video_id+课名）、视频截图、相似题规则文档 |
| 不需要业务方单独给 | **题号/分值/题型表** — 由 AI 从 Word + 解析整理入库 |
| 强烈建议 | 逐字稿、飞书过程库 Base、新中考培优表（截图 X 列） |
| 可接受降级 | 仅 PDF + 解析（无 Word 时 AI 可抽题，但更慢、易 OCR 误差）；**勿仅 Word 无 PDF**（左列题图不准） |

缺解析或 video_id↔课名对照时，先索要再写白名单。

## 项目路径（chem-exam-analysis 默认）

| 用途 | 路径 |
|------|------|
| 引擎 | `scripts/visual_v5_2026_yt.py` |
| 逐卷 override | `outputs/2026_yt_visual_v5/data/paper_tag_overrides/<卷>_v5.json` |
| 视频课名 | `outputs/2026_yt_visual_v5/data/video_records_v5.json` |
| SOP | `outputs/2026_yt_visual_v5/docs/similar_question_sop_v5.md` |
| 方法论 | `outputs/2026_yt_visual_v5/docs/中考化学押题对比方法论_v5.xml` |
| 分卷链接 | `outputs/2026_yt_visual_v5/feishu/doc_urls_v5.json` |
| 规则原文 | https://guanghe.feishu.cn/docx/AQPjdCbcwoqjnlxspOgcuqQWnnd |

换仓库/年份时：改 `PAPER_DIR`、override 目录、易混 ID 表（见 [reference.md](reference.md)）。

## 执行流程

```
1. 读 SOP + 该卷 override（或从零建）
2. 逐题：解析 → 题型 → problem_tags → promotion
3. allow 题：拆小问 → 曲线分型 → 查 video_name → 写 audit_note + allowed_video_ids
4. --prepare → 验证 matches final_show
5. --republish-paper "2026年××中考化学试卷"
6. （修过典型案例后）跑 sop_scan.py 全库回归
7. （重跑 --prepare 后）跑 crop_scan.py 检查跨页裁图
```

### 命令

```bash
cd chem-exam-analysis
.venv/bin/python scripts/visual_v5_2026_yt.py --prepare
.venv/bin/python scripts/visual_v5_2026_yt.py --republish-paper "2026年四川省成都市中考化学试卷"
# 首次全量：--publish
```

需 `lark-cli` 飞书鉴权。

## 飞书对比表：只改左列（强制）

用户有时会**在飞书里手改右列**（视频截图、命中说明）。以下约定优先级高于默认流程：

| 用户要求 | 只改 | 禁止 |
|----------|------|------|
| 改「题干截图 / 题图 / 左列 / 裁图」 | **该题左列**中考题目图 | 右列视频截图、课名、命中理由、`allowed_video_ids` |
| 改「视频匹配 / 白名单 / 右列」 | override + 右列相关字段 | 未经要求勿动左列裁图 |

**禁止**为修一题题图而对整卷执行 `--republish-paper`：引擎会 `overwrite` 整篇文档，**覆盖用户手改过的所有右列截图**。

**只修左列时的做法：**

1. 本地修裁图逻辑或 `pdf_question_crops/` → `--prepare` 刷新 `pdf_crop_token`（可只动该卷该题）。
2. 在飞书对比表**仅替换该题左列图片**（手改或后续支持的局部 patch）；**不要**整卷 republish。
3. 若必须重发：先向用户确认「右列手改会被覆盖」，或请用户标明哪些题右列已定稿、跳过整卷重发。

典型已定稿勿覆盖：成都 Q19 右列等用户明示「不要改」的题。

## override 最小模板

### 宣传版 + 项目式拆小问（苏州 Q21 型）

```json
{
  "paper": "2026年××中考化学试卷",
  "feishu_manual_compare": true,
  "questions": {
    "21": {
      "primary_type": "科学探究题",
      "problem_tags": "控制变量、对照实验",
      "promotion": "allow",
      "allowed_video_ids": ["ZND-7", "ZND-8", "ZND-17"],
      "task_video_map": [
        {"anchor": "Ⅰ(1)(2) 制氧装置", "video_ids": ["ZND-7"], "note": "都是气体制取题"},
        {"anchor": "Ⅰ(3)(4) 催化对照", "video_ids": ["ZND-8"]},
        {"anchor": "Ⅱ CaO2供氧", "video_ids": ["ZND-17"]}
      ],
      "video_similarity_notes": {
        "ZND-7": "都是气体制取题",
        "ZND-8": "都考察控制变量法探究催化剂效果"
      },
      "audit_note": "勿堆 XZK-36 泛控制变量课"
    }
  }
}
```

### 对比表展示层（扬州 Q20–Q25 型）

```json
{
  "20": {
    "promotion": "exclude",
    "comparison_show": true,
    "comparison_video_ids": ["ZND-10", "XZK-21"],
    "video_similarity_notes": {
      "ZND-10": "都考察控制变量法探究催化剂效果"
    },
    "audit_note": "2分选择但柱图专且复杂→仅对比表"
  }
}
```

### 宣传版单题模板

```json
{
  "18": {
    "primary_type": "工艺流程题",
    "promotion": "allow",
    "allowed_video_ids": ["XZK-22", "XZK-27", "JCTB-145"],
    "audit_note": "双核心：流程+溶解度读图"
  }
}
```

## 七条硬门禁（写白名单前必过）

1. **拼盘题组**：前有材料的小选择 → 基础题，勿整组标科普阅读
2. **复合题拆小问**：流程+曲线/产率/滤液 → 每核心维度 ≥1 专课（见 reference）
3. **曲线类型**：产率极值 / 溶解度 / 滴定 pH / 科普折线 — 分型后再配课
4. **设问任务**：滤液溶质 → JCTB-127、XZK-26；产率适宜条件 → XZK-21 等读图课
5. **科普阅读两阶段**：**入围**=形式相似（信息提取+曲线图/数据表/控制变量）；**排序**=素材主题或数据作答同构 → 截图前置（`isomorph_rank`）
6. **ID 易混**：写 ID 前查 `video_name`（XZK-9≠滤液课，ZND-24≠产率曲线，XZK-46≠溶解度）
8. **专课替泛课**：有制氧/催化/U 型管/pH 表专课时，勿只挂 `XZK-36`/`XZK-22` 泛课（苏州 Q21/Q23/Q25）
9. **勿 CV 三连**：`XZK-36`+`XZK-6`+`XZK-3` 无 `task_video_map`/口语说明 → 改湖南 Q22 型按设问拆课（SOP §8.7）
10. **勿制氧机凑项目形式**：乙醇/健康饮水项目 ≠ `ZND-17` 制氧机（重庆 Q22 / 湖南 Q19 对比表策展）

## 全库扫描

修完标杆题后运行：

```bash
.venv/bin/python .cursor/skills/exam-video-match-audit/scripts/sop_scan.py
.venv/bin/python .cursor/skills/exam-video-match-audit/scripts/crop_scan.py
```

输出 P0/P1 待修清单；P2（audit_note 未拆小问）为文档质量项。

## 产出汇报格式

向用户回报时包含：
- 改了哪些卷/题、白名单变更
- `--prepare` / `--republish-paper` 是否成功
- 飞书分卷 URL（从 `doc_urls_v5.json`）
- 全库扫描剩余 P0/P1（如有）

## 延伸阅读

- 门禁与 ID 表：[reference.md](reference.md)
- 标杆错题：[examples.md](examples.md)
- 飞书方法论：https://guanghe.feishu.cn/docx/Wk4sdkWRyofZNWxL1ppcxWSFnpg
