const STAGES = [
  ["standardization", "资料标准化"], ["diagnosis", "题型、趋势与覆盖诊断"],
  ["selection", "好题候选与生产优先级"], ["mother_question", "母题整合"],
  ["lesson_plan", "教案"], ["transcript", "逐字稿"], ["storyboard", "分镜与成品"],
];
const STRATEGIES = {auto: "自动推进", exceptions: "只看异常", confirm: "每次确认", blocked: "禁止自动执行"};
const FIELDS = [
  ["name", "生产项目名称", false], ["target_students", "目标学生层", false],
  ["target_region", "目标地区", false], ["target_exam_type", "目标考试类型", false],
  ["target_year", "目标年份", false], ["content_scope", "内容 / 课程范围", false],
  ["planned_artifact", "计划产物", true], ["problem_to_solve", "本次要解决的问题", true],
];
const PERMISSIONS = {
  feishu_write: "写入飞书云文档", pmo_update: "更新 PMO 状态",
  notify_contractor: "触达兼职负责人", project_rule_promotion: "项目规则达标后自动升级",
};
let state = null;
let selectedOnly = false;
let basePreview = null;
let manifestPreview = null;

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "操作失败");
  return data;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>'"]/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch]));
}

function toast(message, error = false) {
  const node = document.querySelector("#toast");
  node.textContent = message;
  node.style.background = error ? "#a92f37" : "#17233b";
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 2600);
}

async function load() {
  state = await api("/api/state");
  render();
}

function render() {
  const project = state.project;
  document.querySelector("#project-title").textContent = project.name;
  document.querySelector("#rule-version").textContent = `规则版本 ${project.rule_version}`;
  document.querySelector("#next-action").textContent = state.summary.ai_next_action;
  document.querySelector("#project-contract").textContent = `${project.target_students}｜${project.target_region}｜${project.target_exam_type} ${project.target_year}｜${project.planned_artifact}`;
  renderMetrics();
  renderStages();
  renderReviews();
  renderEvents();
  renderQuestions();
  renderDataFoundation();
  renderArtifacts();
  renderRules();
  renderSettings();
}

function renderDataFoundation() {
  document.querySelector("#state-revision").textContent = `状态修订 r${state.summary.state_revision}`;
  const runsBySnapshot = Object.fromEntries(state.standardization_runs.map(item => [item.source_snapshot_id, item]));
  document.querySelector("#snapshot-list").innerHTML = state.source_snapshots.length ? [...state.source_snapshots].reverse().map(item => {
    const run = runsBySnapshot[item.id];
    const canStandardize = ["local_files", "local_folder", "cb_export"].includes(item.source_type) && item.file_count > 0;
    const action = run
      ? `<span class="tag good">已形成 ${run.question_asset_count} 个题目资产</span>`
      : canStandardize ? `<button class="button secondary small" data-standardize="${item.id}">开始标准化</button>` : `<span class="tag">等待适配器</span>`;
    return `<div class="snapshot-card"><strong>${esc(item.source_label)} <span class="tag">${esc(item.status)}</span></strong><small>${esc(item.source_type)} · ${item.file_count} 个文件 · ${item.total_bytes} bytes<br>${esc(item.created_at)} · 截止 ${esc(item.data_cutoff || "未填写")}</small><small class="checksum">SHA ${esc(item.immutable_checksum.slice(0, 16))}…</small><div class="snapshot-action">${action}</div></div>`;
  }).join("") : `<p class="quiet">尚未冻结来源。正式诊断前至少需要一个可追溯输入快照。</p>`;
  document.querySelectorAll("[data-standardize]").forEach(button => button.onclick = () => standardizeSnapshot(button.dataset.standardize));

  renderStandardAssets();
  renderBasePreview();
  renderManifestPreview();
  renderDiagnosisFoundation();
  renderVideoEvidence();
  renderCalibrationWorkbench();

  const failed = state.jobs.filter(job => job.status === "failed").length;
  const health = document.querySelector("#job-health");
  health.textContent = failed ? `${failed} 个失败任务` : `${state.jobs.length} 个任务 · 无失败`;
  health.className = `status ${failed ? "waiting" : "completed"}`;
  document.querySelector("#job-list").innerHTML = state.jobs.length ? [...state.jobs].reverse().slice(0, 14).map(job => `
    <div class="job-row"><strong>${esc(job.stage)}</strong><small>${esc(job.idempotency_key.slice(0, 16))}…</small><span class="job-mode ${job.execution_mode}">${job.execution_mode === "dry_run" ? "契约预演" : "正式执行"}</span><span>${esc(job.status)}</span><span>尝试 ${job.attempts}</span></div>`).join("") : `<p class="quiet">启动一轮运行后，每个阶段都会生成幂等任务记录。</p>`;

  const resultsByFreeze = Object.fromEntries(state.backtest_results.map(item => [item.freeze_id, item]));
  document.querySelector("#backtest-list").innerHTML = state.prediction_freezes.length ? [...state.prediction_freezes].reverse().map(freeze => {
    const result = resultsByFreeze[freeze.id];
    if (result) return `<div class="snapshot-card"><strong>${freeze.training_years.join("、")} → ${result.observation_year}</strong><small>Precision ${metric(result.precision, result.precision_numerator, result.precision_denominator)} · Recall ${metric(result.recall, result.recall_numerator, result.recall_denominator)}<br>误判 ${result.false_positive_ids.length} · 漏判 ${result.false_negative_ids.length} · 样本 ${result.sample_count}</small><small class="checksum">${esc(freeze.immutable_checksum.slice(0, 16))}…</small></div>`;
    return `<div class="snapshot-card"><strong>${freeze.training_years.join("、")} → ${freeze.validation_years.join("、")} <span class="tag">待后验数据</span></strong><small>规则 ${esc(freeze.rule_version)} · 截止 ${esc(freeze.data_cutoff)}</small><textarea data-observations="${freeze.id}" style="width:100%;margin-top:8px;min-height:58px">[{"entity_id":"structure-a","actual_positive":true},{"entity_id":"structure-b","actual_positive":false}]</textarea><div style="display:flex;gap:6px;margin-top:6px"><input data-observation-year="${freeze.id}" value="${freeze.validation_years[0]}" style="width:80px"><button class="button secondary small" data-evaluate="${freeze.id}">用后验观察集计算</button></div></div>`;
  }).join("") : `<p class="quiet">尚未冻结预测。没有冻结记录时，未来年份不能用于证明规则进步。</p>`;
  document.querySelectorAll("[data-evaluate]").forEach(button => button.onclick = () => evaluateFreeze(button.dataset.evaluate));
}

function latestDiagnosableSnapshot() {
  const assetSnapshotIds = new Set(state.question_assets.map(asset => asset.source_snapshot_id));
  return [...state.source_snapshots].reverse().find(snapshot => assetSnapshotIds.has(snapshot.id));
}

function renderDiagnosisFoundation() {
  const node = document.querySelector("#diagnosis-foundation");
  const health = document.querySelector("#diagnosis-health");
  const snapshot = latestDiagnosableSnapshot();
  if (!snapshot) {
    health.textContent = "等待题目资产";
    health.className = "status waiting";
    node.innerHTML = `<p class="quiet">请先导入并标准化一批真实题目。</p>`;
    return;
  }
  const run = [...(state.diagnostic_runs || [])].reverse().find(item => item.source_snapshot_id === snapshot.id);
  if (!run) {
    health.textContent = "尚未诊断";
    health.className = "status waiting";
    node.innerHTML = `<div class="calibration-actions"><p class="quiet">将分析 ${state.question_assets.filter(asset => asset.source_snapshot_id === snapshot.id).length} 个题目资产。仅共同底层结构进入频次分组；同知识点但考查逻辑不同的题不会硬拼。</p><button class="button primary" id="run-real-diagnosis">运行首轮真实诊断</button></div>`;
    node.querySelector("#run-real-diagnosis").onclick = () => runRealDiagnosis(snapshot.id);
    return;
  }
  const summary = run.summary;
  const sample = [...(state.gold_sample_sets || [])].reverse().find(item => item.diagnostic_run_id === run.id);
  health.textContent = run.status === "completed" ? "诊断完成" : "完成 · 有证据边界";
  health.className = "status completed";
  const limits = run.evidence_limits.length
    ? `<div class="evidence-limit"><strong>当前证据边界</strong><span>${esc(run.evidence_limits.join(" "))}</span></div>` : "";
  const sampleControl = sample
    ? `<span class="tag good">已生成 ${sample.actual_size} 题金样本</span>`
    : `<label class="sample-size">抽样规模<select id="gold-sample-size"><option>30</option><option selected>40</option><option>50</option></select></label><button class="button primary small" id="create-gold-sample">生成待校准金样本</button>`;
  const sampleRows = sample ? sample.items.slice(0, 12).map(item => {
    const asset = state.question_assets.find(candidate => candidate.id === item.asset_id);
    const image = asset?.content_blocks.find(block => block.type === "image" && block.path);
    const action = image ? `<a class="button secondary small" href="${assetUrl(image.path)}" target="_blank" rel="noopener">查看原题</a>` : "";
    return `<div class="calibration-row"><div><strong>${esc(item.source_name)} · 第 ${esc(item.question_no)} 题</strong><small>${esc(item.rationale)}</small></div><div class="chip-row"><span class="tag frequency">${esc(item.frequency_level)}</span><span class="tag ${item.quality_recommendation === "异常复核" ? "risk" : "good"}">${esc(item.quality_recommendation)}</span>${action}</div></div>`;
  }).join("") : "";
  const coverage = sample ? `<p class="quiet">抽样覆盖 ${sample.coverage.paper_count} 套试卷、${sample.coverage.question_types.length} 类题型、${sample.coverage.with_visual_count} 道含图题，并强制纳入 ${sample.coverage.exception_count} 个异常案例。下方先展示 12 题，完整集合保存在当前项目状态中。</p>` : "";
  node.innerHTML = `${limits}<div class="diagnosis-summary"><div><b>${summary.high_frequency_count}</b><span>同年跨地区高频</span></div><div><b>${summary.quality_candidate_count}</b><span>AI 候选好题</span></div><div><b>${summary.frequency_unresolved_count}</b><span>缺底层结构，不判频次</span></div><div><b>${summary.trend_unresolved_count}</b><span>多年趋势待补证据</span></div><div><b>${summary.priority_unresolved_count}</b><span>优先级待视频/学生证据</span></div></div>
    <div class="calibration-actions"><p class="quiet">规则 ${esc(run.rule_version)} · ${summary.paper_count} 套试卷 · ${summary.asset_count} 道题。好题候选仍需核验科学性；高频标签不会自动等于好题。</p><div>${sampleControl}</div></div>${coverage}<div class="calibration-rail">${sampleRows}</div>`;
  if (!sample) node.querySelector("#create-gold-sample").onclick = () => createGoldSample(run.id);
}

function renderVideoEvidence() {
  const node = document.querySelector("#video-evidence-foundation");
  const health = document.querySelector("#video-evidence-health");
  const videoImport = [...(state.video_imports || [])].reverse()[0];
  if (!videoImport) {
    health.textContent = "等待视频清单";
    health.className = "status waiting";
    node.innerHTML = `<form id="video-manifest-form" class="compact-form"><label>视频证据 JSON 路径<textarea name="path" placeholder="包含 video_id、video_name、结构标签与逐字稿证据的本地 JSON" required></textarea></label><div class="form-grid mini"><label>来源名称<input name="source_label" value="既有视频证据库"></label><label>数据截止时间<input name="data_cutoff" type="date"></label></div><p class="form-hint">只冻结视频资产和证据字段；不会把旧宣传匹配的命中结论带入生产诊断。</p><button class="button primary" type="submit">冻结视频证据库</button></form>`;
    node.querySelector("#video-manifest-form").onsubmit = importVideoManifest;
    return;
  }
  const counts = videoImport.transcript_status_counts || {};
  const strong = (counts["强匹配-文件名"] || 0) + (counts["强匹配-文件名+正文"] || 0) + (counts["本地素材直接匹配"] || 0);
  const weak = (counts["弱匹配待人工复核"] || 0) + (counts["弱匹配待复核"] || 0);
  const diagnostic = [...(state.diagnostic_runs || [])].reverse()[0];
  const sample = diagnostic && [...(state.gold_sample_sets || [])].reverse().find(item => item.diagnostic_run_id === diagnostic.id);
  const coverage = sample && [...(state.coverage_runs || [])].reverse().find(item => item.gold_sample_id === sample.id && item.video_import_id === videoImport.id);
  health.textContent = coverage ? "保守候选已生成" : "视频资产已接入";
  health.className = "status completed";
  const action = !coverage && diagnostic && sample
    ? `<button class="button primary small" id="run-coverage-diagnosis">运行保守覆盖候选</button>` : "";
  const summary = coverage ? `<div class="diagnosis-summary video-summary"><div><b>${coverage.summary["部分覆盖候选"] || 0}</b><span>结构＋任务＋逐字稿证据</span></div><div><b>${coverage.summary["证据不足"] || 0}</b><span>结构相似但门禁不足</span></div><div><b>${coverage.summary["未发现可核验证据"] || 0}</b><span>未召回可靠候选</span></div><div><b>${coverage.summary["无法判断"] || 0}</b><span>题目底层结构待补</span></div></div>` : "";
  const rows = coverage ? coverage.results.slice(0, 12).map(item => {
    const best = item.candidates[0];
    const candidate = best
      ? `<strong>${esc(best.video_id)}｜${esc(best.video_name)}</strong><small>${esc(best.reason)}</small>`
      : `<strong>暂无视频候选</strong><small>${esc(item.reason)}</small>`;
    return `<div class="coverage-row"><div><strong>${esc(item.source_name)} · 第 ${esc(item.question_no)} 题</strong><small>${esc(item.status)}：${esc(item.reason)}</small></div><div>${candidate}</div></div>`;
  }).join("") : "";
  const transcriptUnmatched = state.video_assets.filter(item => item.transcript_status === "未匹配").length;
  node.innerHTML = `<div class="diagnosis-summary"><div><b>${videoImport.video_asset_count}</b><span>唯一视频资产</span></div><div><b>${strong}</b><span>强逐字稿证据</span></div><div><b>${weak}</b><span>弱匹配待复核</span></div><div><b>${videoImport.duplicate_video_ids.length}</b><span>重复视频 ID</span></div><div><b>${transcriptUnmatched}</b><span>逐字稿未匹配</span></div></div>
    <div class="calibration-actions"><p class="quiet">视频证据适配器 ${esc(videoImport.adapter_version)}。旧 matches 和宣传白名单不作为生产覆盖结论；自动判断最高只到“部分覆盖候选”。</p><div>${action}</div></div>${summary}<div class="evidence-limit"><strong>保守边界</strong><span>${coverage ? esc(coverage.evidence_limits.join(" ")) : "需要金样本后才能运行题目—视频证据对照。"}</span></div><div class="coverage-rail">${rows}</div>`;
  if (action) node.querySelector("#run-coverage-diagnosis").onclick = () => runCoverageDiagnosis(diagnostic.id, sample.id, videoImport.id);
}

function renderCalibrationWorkbench() {
  const node = document.querySelector("#calibration-workbench");
  const health = document.querySelector("#calibration-health");
  const diagnostic = [...(state.diagnostic_runs || [])].reverse()[0];
  const sample = diagnostic && [...(state.gold_sample_sets || [])].reverse().find(item => item.diagnostic_run_id === diagnostic.id);
  const coverage = sample && [...(state.coverage_runs || [])].reverse().find(item => item.gold_sample_id === sample.id);
  if (!diagnostic || !sample || !coverage) {
    health.textContent = "等待金样本与覆盖候选";
    health.className = "status waiting";
    node.innerHTML = `<p class="quiet">完成真实诊断、金样本抽样和视频候选后，这里才会出现可选校准。没有教师反馈也不会阻塞 AI 主流程。</p>`;
    return;
  }
  const diagnosisByAsset = Object.fromEntries(diagnostic.results.map(item => [item.asset_id, item]));
  const coverageByAsset = Object.fromEntries(coverage.results.map(item => [item.asset_id, item]));
  const reviews = (state.calibration_reviews || []).filter(item => item.gold_sample_id === sample.id);
  const reviewByAsset = Object.fromEntries(reviews.map(item => [item.asset_id, item]));
  const corrected = reviews.filter(item => item.status === "corrected").length;
  const followup = reviews.filter(item => item.requires_followup).length;
  const unreviewed = sample.items.length - reviews.length;
  health.textContent = `${reviews.length}/${sample.items.length} 已处理`;
  health.className = `status ${unreviewed ? "waiting" : "completed"}`;

  const option = (value, current) => `<option value="${esc(value)}" ${value === current ? "selected" : ""}>${esc(value)}</option>`;
  const cards = sample.items.slice(0, 12).map(item => {
    const asset = state.question_assets.find(candidate => candidate.id === item.asset_id);
    const diagnosis = diagnosisByAsset[item.asset_id];
    const coverageItem = coverageByAsset[item.asset_id];
    const review = reviewByAsset[item.asset_id];
    const values = review?.effective_values || {
      structural_keys: diagnosis.structural_keys,
      frequency: diagnosis.frequency.level,
      quality: diagnosis.quality.recommendation,
      science: diagnosis.quality.dimensions["科学性"].status,
      coverage: coverageItem.status,
    };
    const image = asset?.content_blocks.find(block => block.type === "image" && block.path);
    const visual = image ? `<a href="${assetUrl(image.path)}" target="_blank" rel="noopener"><img src="${assetUrl(image.path)}" alt="${esc(asset.title)}原题图" loading="lazy"></a>` : `<div class="asset-visual-placeholder">本题没有已物化题图</div>`;
    const videos = coverageItem.candidates.length ? coverageItem.candidates.map(candidate => `<li><strong>${esc(candidate.video_id)}｜${esc(candidate.video_name)}</strong><span>${esc(candidate.reason)}</span></li>`).join("") : `<li><span>${esc(coverageItem.reason)}</span></li>`;
    const issue = asset?.issue_codes?.length ? `<span class="tag risk">异常复核</span>` : "";
    const saved = review ? `<span class="tag ${review.status === "corrected" ? "frequency" : "good"}">${review.status === "corrected" ? "已纠正" : "已接受"}</span>` : `<span class="tag">未介入</span>`;
    const teacherReason = review?.reason_source === "teacher" ? review.reason : "";
    return `<form class="calibration-card" data-calibration-asset="${esc(item.asset_id)}"><div class="calibration-visual">${visual}</div><div class="calibration-body"><div class="calibration-card-head"><div><p class="eyebrow">${esc(item.source_name)} · 第 ${esc(item.question_no)} 题</p><h4>${esc(asset?.title || "题目资产")}</h4></div><div class="chip-row">${issue}${saved}</div></div><div class="calibration-ai"><span>AI 高频：<b>${esc(diagnosis.frequency.level)}</b></span><span>AI 好题：<b>${esc(diagnosis.quality.recommendation)}</b></span><span>覆盖：<b>${esc(coverageItem.status)}</b></span></div><details><summary>查看最多 3 个视频候选及证据</summary><ul class="candidate-evidence-list">${videos}</ul></details><div class="calibration-fields"><label>底层结构<input name="structural_keys" value="${esc(values.structural_keys.join("、"))}"></label><label>频次<select name="frequency">${["高频","中频","低频","不可判断"].map(value => option(value, values.frequency)).join("")}</select></label><label>好题判断<select name="quality">${["AI候选好题","备选","暂不推荐","异常复核","好题","非好题","待复核"].map(value => option(value, values.quality)).join("")}</select></label><label>科学性<select name="science">${["待人工核验","待核验","通过","有问题"].map(value => option(value, values.science)).join("")}</select></label><label>视频覆盖<select name="coverage">${["部分覆盖候选","证据不足","未发现可核验证据","无法判断","充分覆盖","部分覆盖","组合支撑但缺综合迁移","未覆盖"].map(value => option(value, values.coverage)).join("")}</select></label><label class="reason-field">纠正理由<input name="reason" value="${esc(teacherReason)}" placeholder="只有改动 AI 判断时必填；直接接受可留空"></label></div><div class="calibration-footer"><small>任何纠正只形成当前项目的隔离实验建议，不会自动污染公共规则或公共 Skill。</small><button class="button secondary small" type="submit">保存本题校准</button></div></div></form>`;
  }).join("");
  node.innerHTML = `<div class="diagnosis-summary video-summary"><div><b>${reviews.length}</b><span>已接受或纠正</span></div><div><b>${corrected}</b><span>形成隔离规则建议</span></div><div><b>${followup}</b><span>仍有证据边界</span></div><div><b>${unreviewed}</b><span>未介入但不阻塞</span></div></div><div class="calibration-actions"><p class="quiet">批量通过只接受非异常项，已有人工纠正不会被覆盖；科学性“待人工核验”等证据边界会原样保留。</p><button class="button primary small" id="batch-pass-calibrations">批量通过非异常项</button></div><div class="calibration-workbench-list">${cards}</div>`;
  node.querySelectorAll("[data-calibration-asset]").forEach(form => form.onsubmit = saveCalibration);
  node.querySelector("#batch-pass-calibrations").onclick = () => batchPassCalibrations(diagnostic.id, sample.id, coverage.id);
}

function renderStandardAssets() {
  const issues = state.standardization_runs.reduce((sum, item) => sum + (item.issue_count || 0), 0);
  const health = document.querySelector("#standardization-health");
  health.textContent = issues ? `${issues} 个问题待处理` : `${state.question_assets.length} 个资产 · 无阻断`;
  health.className = `status ${issues ? "waiting" : "completed"}`;
  const search = (document.querySelector("#standard-asset-search")?.value || "").trim().toLowerCase();
  const integrity = document.querySelector("#asset-integrity-filter")?.value || "";
  const filtered = state.question_assets.filter(asset => (!integrity || asset.image_integrity === integrity) && (!search || JSON.stringify(asset).toLowerCase().includes(search)));
  const items = [...filtered].reverse().slice(0, 48);
  document.querySelector("#asset-filter-count").textContent = `显示 ${items.length} / 命中 ${filtered.length} / 共 ${state.question_assets.length}`;
  document.querySelector("#standard-asset-list").innerHTML = items.length ? items.map(asset => {
    const types = [...new Set(asset.content_blocks.map(block => block.type))];
    const firstImage = asset.content_blocks.find(block => block.type === "image" && block.path);
    const visual = firstImage
      ? `<img src="${assetUrl(firstImage.path)}" alt="${esc(asset.title)}的原题图片" loading="lazy">`
      : `<div class="asset-visual-placeholder">${asset.image_integrity === "remote_reference_unmaterialized" ? "远程图片待物化" : asset.image_integrity === "missing" ? "检测到缺图" : "本题未声明图表"}</div>`;
    const issueTags = asset.issue_codes.map(code => `<span class="tag risk">${esc(code)}</span>`).join("");
    const normalized = Object.entries(asset.normalized_fields || {}).filter(([, value]) => value != null && value !== "").slice(0, 5).map(([key, value]) => `<span class="tag">${esc(key)}：${esc(Array.isArray(value) ? value.join("、") : value)}</span>`).join("");
    return `<article class="standard-asset-card"><div class="standard-asset-visual">${visual}</div><div><p class="eyebrow">${esc(asset.source_name)} · ${esc(asset.question_no || `第 ${asset.ordinal} 项`)}</p><h4>${esc(asset.title)}</h4><div class="chip-row"><span class="tag good">${types.map(typeLabel).join("＋")}</span><span class="tag">图片：${esc(imageIntegrityLabel(asset.image_integrity))}</span><span class="tag">重复 ${asset.duplicate_count || 1} 条</span>${issueTags}</div><div class="chip-row">${normalized}</div><p>${esc(asset.raw_text || "无可提取文字；请查看保留的原题图片。")}</p></div></article>`;
  }).join("") : `<p class="quiet">尚无标准题目资产。请先冻结本地资料并点击“开始标准化”，或从 Base 只读预览后导入。</p>`;
}

function renderManifestPreview() {
  const node = document.querySelector("#manifest-preview");
  if (!manifestPreview) return;
  const report = manifestPreview.image_report;
  const mappingKeys = [
    ["source_id", "题目 ID"], ["source_paper", "试卷"], ["question_no", "题号"],
    ["question_text", "题目文本（必选）"], ["question_image", "本地题图路径"], ["difficulty", "难度"],
    ["knowledge_tags", "知识点"], ["question_type", "题型"], ["score", "分值"],
    ["visual_forms", "视觉形态"], ["background_tags", "背景素材"], ["task_tags", "设问任务"],
    ["method_models", "解法模型"], ["source_page", "来源页码"],
  ];
  const options = manifestPreview.fields.map(field => `<option value="${esc(field.name)}">${esc(field.name)}</option>`).join("");
  const rate = report.coverage_rate == null ? "未映射题图字段" : `${(report.coverage_rate * 100).toFixed(1)}%`;
  const rows = manifestPreview.records.slice(0, 3).map(record => `<tr><td>${esc(record.record_id)}</td><td>${esc(JSON.stringify(record.fields).slice(0, 260))}</td></tr>`).join("");
  node.innerHTML = `<div class="local-health-grid"><div><b>${manifestPreview.record_count}</b><span>题目记录</span></div><div><b>${report.unique_existing_local_images}</b><span>唯一可用题图</span></div><div><b>${rate}</b><span>有图记录覆盖率</span></div><div class="${report.records_with_missing_local_image || report.suspicious_local_images ? "health-risk" : ""}"><b>${report.records_with_missing_local_image} / ${report.suspicious_local_images}</b><span>缺失 / 疑似误裁</span></div></div>
    <div class="mapping-grid">${mappingKeys.map(([key, label]) => `<label><span>${label}</span><select data-manifest-mapping="${key}"><option value="">不映射</option>${options}</select></label>`).join("")}</div>
    <details><summary>查看原始字段与记录样本</summary><div class="chip-row">${manifestPreview.fields.map(field => `<span class="tag">${esc(field.name)}</span>`).join("")}</div><table class="preview-table"><tbody>${rows}</tbody></table></details>
    <p class="quiet">导入时只冻结实际存在的本地题图；缺失项保留原引用并进入异常复核，不会伪装成“图片完整”。</p>
    <button class="button primary" id="import-manifest" type="button">冻结清单与本地题图</button>`;
  Object.entries(manifestPreview.suggested_mapping || {}).forEach(([key, value]) => {
    const select = node.querySelector(`[data-manifest-mapping="${key}"]`);
    if (select) select.value = value;
  });
  node.querySelector("#import-manifest").onclick = importManifest;
}

function renderBasePreview() {
  const node = document.querySelector("#base-preview");
  if (!basePreview) return;
  const mappingKeys = [
    ["question_text", "题目文本（必选）"], ["question_image", "题目截图"], ["question_no", "题号"],
    ["year", "年份"], ["province", "省份"], ["city", "城市/地区"], ["exam_type", "考试类型"],
    ["knowledge_tags", "知识点"], ["difficulty", "难度"], ["historical_ai_quality", "历史 AI 好题"],
    ["historical_teacher_quality", "历史教研好题"], ["historical_production_choice", "历史生产入选"],
    ["library_has_video", "历史是否有视频"],
  ];
  const options = basePreview.fields.map(field => `<option value="${esc(field.name)}">${esc(field.name)}</option>`).join("");
  const rows = basePreview.records.slice(0, 3).map(record => `<tr><td>${esc(record.record_id)}</td><td>${esc(JSON.stringify(record.fields).slice(0, 260))}</td></tr>`).join("");
  node.innerHTML = `<div class="base-meta"><strong>${esc(basePreview.view_name || "全表 / 未识别视图名")}</strong><span>${basePreview.record_count} 条预览${basePreview.has_more ? " · 后续还有记录" : ""}</span></div>
    <p class="quiet">视图筛选：${esc(basePreview.view_filter ? JSON.stringify(basePreview.view_filter) : "无或未读取")}</p>
    <div class="mapping-grid">${mappingKeys.map(([key, label]) => `<label><span>${label}</span><select data-mapping="${key}"><option value="">不映射</option>${options}</select></label>`).join("")}</div>
    <details><summary>查看原始字段与记录样本</summary><div class="chip-row">${basePreview.fields.map(field => `<span class="tag">${esc(field.name)}</span>`).join("")}</div><table class="preview-table"><tbody>${rows}</tbody></table></details>
    <button class="button primary" id="import-base" type="button">按当前映射冻结并导入</button>`;
  Object.entries(basePreview.suggested_mapping || {}).forEach(([key, value]) => {
    const select = node.querySelector(`[data-mapping="${key}"]`);
    if (select) select.value = value;
  });
  node.querySelector("#import-base").onclick = importBase;
}

function assetUrl(path) {
  return `/api/assets/${String(path).split("/").map(encodeURIComponent).join("/")}`;
}

function typeLabel(type) {
  return ({text: "文字", formula: "公式", table: "表格", image: "原图", image_reference: "远程图引用"})[type] || type;
}

function imageIntegrityLabel(value) {
  return ({preserved: "已保留", partial: "部分缺失", remote_reference_unmaterialized: "待物化", missing: "缺失", no_visual_declared: "无图"})[value] || value;
}

function metric(value, numerator, denominator) {
  return value == null ? `—（${numerator}/${denominator}）` : `${(value * 100).toFixed(1)}%（${numerator}/${denominator}）`;
}

function renderMetrics() {
  const s = state.summary;
  const items = [[s.standardized_asset_count, "已标准化题目"], [s.candidate_count, "已入选候选"], [s.p1_count, "P1 重点生产"], [s.waiting_review_count, "待教师处理"], [s.rule_iteration, "规则迭代轮次"]];
  document.querySelector("#metrics").innerHTML = items.map(([value, label]) => `<div class="metric"><b>${esc(value)}</b><span>${label}</span></div>`).join("");
}

function renderStages() {
  const run = state.runs.at(-1);
  const statuses = run?.stage_states || {};
  const statusNode = document.querySelector("#run-status");
  statusNode.textContent = {waiting: "待教师处理", completed: "已完成", blocked: "已阻止", running: "运行中"}[state.summary.latest_run_status] || state.summary.latest_run_status;
  statusNode.className = `status ${run?.status || ""}`;
  document.querySelector("#stage-list").innerHTML = STAGES.map(([key, label], index) => {
    const status = statuses[key] || "pending";
    const strategy = STRATEGIES[state.project.intervention_strategies[key]];
    const statusText = {completed: "已完成", pending: "待运行", waiting: "待确认", blocked: "已阻止", not_affected: "本次不受影响"}[status] || status;
    return `<div class="stage ${status}"><span class="stage-index">${status === "completed" ? "✓" : index + 1}</span><div><strong>${label}</strong><small> · ${strategy}</small></div><span class="tag">${statusText}</span></div>`;
  }).join("");
}

function renderReviews() {
  const reviews = state.reviews.filter(item => item.status === "待确认");
  document.querySelector("#review-list").innerHTML = reviews.length ? reviews.map(item => `
    <div class="review-card"><span class="review-meta">${esc(item.stage_label)} · ${item.item_ids.length} 个对象</span><p>${esc(item.reason)}</p>
    <button class="button primary small" data-approve="${item.id}">通过本节点并继续</button></div>`).join("") :
    `<p class="quiet">当前没有必须由老师处理的节点。AI 会依据项目设置继续运行，并保留全部证据与撤销入口。</p>`;
  document.querySelectorAll("[data-approve]").forEach(button => button.onclick = () => approveReview(button.dataset.approve));
}

function renderEvents() {
  const events = [...state.events].reverse().slice(0, 8);
  document.querySelector("#event-stream").innerHTML = events.length ? events.map(item => `<div class="event"><time>${esc(item.created_at)}</time><strong>${esc(item.message)}</strong></div>`).join("") : `<p class="quiet">启动运行后，这里会记录每一步 AI 动作、规则版本和放行依据。</p>`;
}

function renderQuestions() {
  const search = (document.querySelector("#question-search")?.value || "").toLowerCase();
  const questions = state.questions.filter(q => (!selectedOnly || q.selected_for_candidate) && JSON.stringify(q).toLowerCase().includes(search));
  document.querySelector("#question-list").innerHTML = questions.map(questionCard).join("") || `<div class="empty-state"><strong>没有符合条件的题目</strong><span>调整筛选条件后再试。</span></div>`;
  document.querySelectorAll("[data-candidate]").forEach(input => input.onchange = () => updateQuestion(input.dataset.candidate, {selected_for_candidate: input.checked}));
  document.querySelectorAll("[data-unit]").forEach(input => input.onchange = () => {
    const card = input.closest(".question-card");
    const ids = [...card.querySelectorAll("[data-unit]:checked")].map(node => node.dataset.unit);
    updateQuestion(card.dataset.question, {selected_unit_ids: ids});
  });
  document.querySelectorAll("[data-save-question-review]").forEach(button => button.onclick = () => {
    const card = button.closest(".question-card");
    updateQuestion(card.dataset.question, {
      teacher_quality_conclusion: card.querySelector("[data-quality-review]").value,
      teacher_coverage_conclusion: card.querySelector("[data-coverage-review]").value,
      teacher_feedback_reason: card.querySelector("[data-review-reason]").value,
    });
  });
}

function questionCard(q) {
  const priorityClass = q.production_priority.startsWith("P") ? q.production_priority : "stop";
  const chips = q.quality.dimensions.map(tag => `<span class="tag good">${esc(tag)}</span>`).join("");
  const units = q.units.map(unit => `<label><input type="checkbox" data-unit="${unit.id}" ${unit.selected ? "checked" : ""}>${esc(unit.label)}</label>`).join("");
  const videos = q.coverage.videos.length ? q.coverage.videos.map(v => `${v.title}（${v.evidence}）`).join("；") : "暂无可核验证据；老师可补人工结论";
  return `<article class="question-card" data-question="${q.id}">
    <div class="question-visual"><div class="image-placeholder">原题图表保留区<br>接入真实题目后显示整题原图<br>不将装置图、图像或表格转写丢失</div><div class="question-source">${esc(q.source)}</div></div>
    <div class="question-body">
      <div class="question-head"><div><p class="eyebrow">${esc(q.id)}</p><h3>${esc(q.title)}</h3></div><div class="priority ${priorityClass}">${esc(q.production_priority)}</div></div>
      <div class="chip-row"><span class="tag frequency">${esc(q.frequency.level)} · ${q.frequency.numerator}/${q.frequency.denominator}</span>${chips}<span class="tag ${q.content_health.status === "无明显问题" ? "" : "risk"}">${esc(q.content_health.status)}</span></div>
      <div class="evidence-grid">
        <div class="evidence-block"><label>高频证据</label><strong>${(q.frequency.rate * 100).toFixed(1)}% · ${esc(q.frequency.scope)}</strong><p>${esc(q.frequency.reason)}</p></div>
        <div class="evidence-block"><label>保守生产覆盖 · ${esc(q.coverage.evidence_level)}</label><strong>${esc(q.coverage.status)}</strong><p>${esc(q.coverage.reason)}<br>${esc(videos)}</p></div>
        <div class="evidence-block"><label>生产理由与最小干预</label><strong>${esc(q.intervention)}</strong><p>${esc(q.priority_reason)}</p></div>
      </div>
      <div class="unit-row"><strong>进入生产的小问：</strong>${units}</div>
      <div class="question-actions"><label class="candidate-toggle"><input type="checkbox" data-candidate="${q.id}" ${q.selected_for_candidate ? "checked" : ""}>进入好题生产候选池</label><span class="tag">AI 好题：${esc(q.quality.ai_conclusion)}｜教研：${esc(q.quality.teacher_conclusion)}</span></div>
      <div class="teacher-review">
        <select data-quality-review aria-label="教研好题结论"><option ${q.quality.teacher_conclusion === "待复核" ? "selected" : ""}>待复核</option><option ${q.quality.teacher_conclusion === "是" ? "selected" : ""}>是</option><option ${q.quality.teacher_conclusion === "否" ? "selected" : ""}>否</option><option ${q.quality.teacher_conclusion === "需拆分" ? "selected" : ""}>需拆分</option></select>
        <select data-coverage-review aria-label="教研覆盖结论">${["待复核","充分覆盖","部分覆盖","组合支撑但缺综合迁移","未覆盖","无法判断"].map(x => `<option ${q.coverage.teacher_conclusion === x ? "selected" : ""}>${x}</option>`).join("")}</select>
        <input data-review-reason aria-label="教研反馈理由" value="${esc(q.teacher_feedback_reason || "")}" placeholder="填写修正理由，供规则学习">
        <button class="button secondary small" data-save-question-review>保存教研反馈</button>
      </div>
    </div>
  </article>`;
}

function renderArtifacts() {
  const list = document.querySelector("#artifact-list");
  const empty = document.querySelector("#artifact-empty");
  empty.style.display = state.artifacts.length ? "none" : "grid";
  list.innerHTML = [...state.artifacts].reverse().map(a => `
    <article class="artifact"><div><p class="eyebrow">${esc(a.kind)} · V${a.version}</p><h3>${esc(a.title)}</h3><p class="artifact-meta">${esc(a.status)}｜${esc(a.updated_at)}</p><p>${esc(a.summary)}</p>
      <ol class="outline">${a.outline.map(x => `<li>${esc(x)}</li>`).join("")}</ol>
      ${a.revision_notes.map(n => `<div class="revision">V${n.version}：${esc(n.summary)}（${n.rerun_stages.join(" → ")}）</div>`).join("")}
    </div><div class="feedback-box"><strong>只在成品处反馈也可以</strong><p class="quiet">例如：“逐字稿不够口语”“第二题不适合做母题”“原题图表缺失”。AI 会逆向归因。</p><textarea data-feedback="${a.id}" placeholder="写下修改意见，不需要判断应该改哪条规则…"></textarea><button class="button primary" data-submit-feedback="${a.id}">提交反馈并局部重跑</button></div></article>`).join("");
  document.querySelectorAll("[data-submit-feedback]").forEach(button => button.onclick = () => submitFeedback(button.dataset.submitFeedback));
}

function renderRules() {
  document.querySelector("#rule-list").innerHTML = [...state.rules].reverse().map(rule => `
    <article class="rule-card"><div><p class="eyebrow">${esc(rule.scope)} · ${esc(rule.status)}</p><h3>${esc(rule.name)}</h3><p>${esc(rule.change_summary)}</p><span class="tag">${esc(rule.id)}</span></div>
      <div><div class="score-compare"><div class="score"><b>${Math.round((rule.baseline_precision || 0) * 100)}%</b><span>基线精确率</span></div><span>→</span><div class="score"><b>${rule.experiment_precision == null ? "待回测" : Math.round(rule.experiment_precision * 100) + "%"}</b><span>实验结果</span></div></div>
      <button class="button secondary small" data-publish="${rule.id}" style="width:100%;margin-top:10px">申请发布为公共规则</button></div></article>`).join("");
  document.querySelectorAll("[data-publish]").forEach(button => button.onclick = () => requestPublication(button.dataset.publish));
}

function renderSettings() {
  const project = state.project;
  document.querySelector("#project-fields").innerHTML = FIELDS.map(([key, label, wide]) => `<div class="field ${wide ? "wide" : ""}"><label>${label}</label><input name="${key}" value="${esc(project[key])}"></div>`).join("");
  document.querySelector("#strategy-grid").innerHTML = STAGES.map(([key, label]) => `<label class="strategy-row"><span>${label}</span><select name="strategy-${key}">${Object.entries(STRATEGIES).map(([value, text]) => `<option value="${value}" ${project.intervention_strategies[key] === value ? "selected" : ""}>${text}</option>`).join("")}</select></label>`).join("");
  document.querySelector("#permission-grid").innerHTML = Object.entries(PERMISSIONS).map(([key, label]) => `<label class="permission"><input type="checkbox" name="permission-${key}" ${project.preauthorizations[key] ? "checked" : ""}>${label}</label>`).join("");
}

async function startRun() {
  try { await api("/api/runs", {method: "POST", body: "{}"}); await load(); toast("AI 已按当前介入策略推进"); }
  catch (error) { toast(error.message, true); }
}
async function approveReview(id) {
  try { await api("/api/reviews/batch-pass", {method: "POST", body: JSON.stringify({review_id: id})}); await load(); toast("本节点已通过，AI 继续运行"); }
  catch (error) { toast(error.message, true); }
}
async function updateQuestion(id, patch) {
  try { await api(`/api/questions/${id}`, {method: "PATCH", body: JSON.stringify(patch)}); await load(); toast("题目结论已保存并重新计算优先级"); }
  catch (error) { toast(error.message, true); }
}
async function submitFeedback(id) {
  const text = document.querySelector(`[data-feedback="${id}"]`).value;
  try { const result = await api(`/api/artifacts/${id}/feedback`, {method: "POST", body: JSON.stringify({text})}); await load(); toast(`已归因到“${result.feedback.root_stage_label}”，重跑契约已执行`); }
  catch (error) { toast(error.message, true); }
}
async function requestPublication(id) {
  try { await api(`/api/rules/${id}/request-publication`, {method: "POST", body: "{}"}); await load(); toast("已创建人工审批请求；系统不会自动发布公共规则"); }
  catch (error) { toast(error.message, true); }
}
async function saveProject(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = {intervention_strategies: {}, preauthorizations: {}};
  FIELDS.forEach(([key]) => payload[key] = form.get(key));
  STAGES.forEach(([key]) => payload.intervention_strategies[key] = form.get(`strategy-${key}`));
  Object.keys(PERMISSIONS).forEach(key => payload.preauthorizations[key] = form.has(`permission-${key}`));
  try { await api("/api/project", {method: "PATCH", body: JSON.stringify(payload)}); await load(); toast("当前生产项目设置已保存"); }
  catch (error) { toast(error.message, true); }
}
async function setFullAuto() {
  const intervention_strategies = Object.fromEntries(STAGES.map(([key]) => [key, "auto"]));
  try { await api("/api/project", {method: "PATCH", body: JSON.stringify({intervention_strategies})}); await load(); toast("已切换为完全自动；只有公共规则发布仍需人工批准"); }
  catch (error) { toast(error.message, true); }
}

async function createSnapshot(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const sourceType = form.get("source_type");
  const location = String(form.get("location") || "").trim();
  const payload = {source_type: sourceType, source_label: form.get("source_label"), data_cutoff: form.get("data_cutoff") || null};
  if (sourceType === "local_folder") payload.path = location;
  else if (sourceType === "feishu_base") payload.url = location;
  else payload.paths = location.split(/[\n,]+/).map(x => x.trim()).filter(Boolean);
  try { await api("/api/source-snapshots", {method: "POST", body: JSON.stringify(payload)}); await load(); toast("来源快照已冻结，原始文件哈希已记录"); }
  catch (error) { toast(error.message, true); }
}

async function standardizeSnapshot(id) {
  try { await api(`/api/source-snapshots/${id}/standardize`, {method: "POST", body: "{}"}); await load(); toast("资料已标准化；文字、表格和原题图片已建立关联"); }
  catch (error) { toast(error.message, true); }
}

async function previewBase(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    basePreview = await api("/api/base/previews", {method: "POST", body: JSON.stringify({url: form.get("url"), limit: Number(form.get("limit"))})});
    renderBasePreview();
    toast("只读预览完成，请确认字段映射后再导入");
  } catch (error) { toast(error.message, true); }
}

async function importBase() {
  const form = new FormData(document.querySelector("#base-preview-form"));
  const mapping = {};
  document.querySelector("#base-preview").querySelectorAll("[data-mapping]").forEach(select => { if (select.value) mapping[select.dataset.mapping] = select.value; });
  try {
    await api("/api/base/imports", {method: "POST", body: JSON.stringify({url: form.get("url"), limit: Number(form.get("limit")), source_label: form.get("source_label"), data_cutoff: form.get("data_cutoff") || null, mapping})});
    basePreview = null;
    await load();
    toast("Base 当前读取结果已冻结；原字段、标准字段和映射契约均已保存");
  } catch (error) { toast(error.message, true); }
}

async function previewManifest(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    manifestPreview = await api("/api/manifests/previews", {method: "POST", body: JSON.stringify({path: form.get("path"), limit: Number(form.get("limit"))})});
    renderManifestPreview();
    toast("本地清单与题图路径检查完成，请确认字段映射");
  } catch (error) { toast(error.message, true); }
}

async function importManifest() {
  const form = new FormData(document.querySelector("#manifest-preview-form"));
  const button = document.querySelector("#import-manifest");
  const mapping = {};
  document.querySelector("#manifest-preview").querySelectorAll("[data-manifest-mapping]").forEach(select => {
    if (select.value) mapping[select.dataset.manifestMapping] = select.value;
  });
  button.disabled = true;
  button.textContent = "正在冻结题图…";
  try {
    const result = await api("/api/manifests/imports", {method: "POST", body: JSON.stringify({path: form.get("path"), source_label: form.get("source_label"), data_cutoff: form.get("data_cutoff") || null, mapping})});
    manifestPreview = null;
    await load();
    toast(`已接入 ${result.run.question_asset_count} 道题和 ${result.image_report.unique_existing_local_images} 张唯一题图`);
  } catch (error) {
    button.disabled = false;
    button.textContent = "冻结清单与本地题图";
    toast(error.message, true);
  }
}

async function runRealDiagnosis(snapshotId) {
  const button = document.querySelector("#run-real-diagnosis");
  if (button) { button.disabled = true; button.textContent = "诊断中…"; }
  try {
    await api("/api/diagnostics", {method: "POST", body: JSON.stringify({source_snapshot_id: snapshotId})});
    await load();
    toast("首轮真实诊断完成；证据不足的字段已明确保留为待验证");
  } catch (error) { toast(error.message, true); }
}

async function createGoldSample(diagnosticRunId) {
  const button = document.querySelector("#create-gold-sample");
  if (button) { button.disabled = true; button.textContent = "抽样中…"; }
  const size = Number(document.querySelector("#gold-sample-size")?.value || 40);
  try {
    await api("/api/gold-samples", {method: "POST", body: JSON.stringify({diagnostic_run_id: diagnosticRunId, size})});
    await load();
    toast(`已生成 ${size} 题待校准金样本`);
  } catch (error) { toast(error.message, true); }
}

async function importVideoManifest(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/video-manifests/imports", {method: "POST", body: JSON.stringify({path: form.get("path"), source_label: form.get("source_label"), data_cutoff: form.get("data_cutoff") || null})});
    await load();
    toast("视频证据库已冻结；旧宣传命中结论未进入生产覆盖字段");
  } catch (error) { toast(error.message, true); }
}

async function runCoverageDiagnosis(diagnosticRunId, goldSampleId, videoImportId) {
  const button = document.querySelector("#run-coverage-diagnosis");
  if (button) { button.disabled = true; button.textContent = "比对中…"; }
  try {
    await api("/api/coverage-diagnostics", {method: "POST", body: JSON.stringify({diagnostic_run_id: diagnosticRunId, gold_sample_id: goldSampleId, video_import_id: videoImportId})});
    await load();
    toast("保守视频覆盖候选已生成；充分覆盖仍保留教研复核门禁");
  } catch (error) { toast(error.message, true); }
}

async function saveCalibration(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  const diagnostic = [...state.diagnostic_runs].reverse()[0];
  const sample = [...state.gold_sample_sets].reverse().find(item => item.diagnostic_run_id === diagnostic.id);
  const coverage = [...state.coverage_runs].reverse().find(item => item.gold_sample_id === sample.id);
  const structuralKeys = String(values.get("structural_keys") || "").split(/[、,，;；|]+/).map(value => value.trim()).filter(Boolean);
  try {
    await api("/api/calibrations", {method: "POST", body: JSON.stringify({
      diagnostic_run_id: diagnostic.id, gold_sample_id: sample.id, coverage_run_id: coverage.id,
      asset_id: form.dataset.calibrationAsset, structural_keys: structuralKeys,
      frequency: values.get("frequency"), quality: values.get("quality"), science: values.get("science"),
      coverage: values.get("coverage"), reason: values.get("reason"),
    })});
    await load();
    toast("本题校准已保存；AI 主流程未被阻塞");
  } catch (error) { toast(error.message, true); }
}

async function batchPassCalibrations(diagnosticRunId, goldSampleId, coverageRunId) {
  const button = document.querySelector("#batch-pass-calibrations");
  if (button) { button.disabled = true; button.textContent = "处理中…"; }
  try {
    const result = await api("/api/calibrations/batch-pass", {method: "POST", body: JSON.stringify({diagnostic_run_id: diagnosticRunId, gold_sample_id: goldSampleId, coverage_run_id: coverageRunId})});
    await load();
    toast(`已批量通过 ${result.passed_count} 题，保留 ${result.skipped_count} 个异常或已纠正对象`);
  } catch (error) { toast(error.message, true); }
}

async function freezePredictions(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const payload = {
      training_years: String(form.get("training_years")).split(",").map(Number),
      validation_years: String(form.get("validation_years")).split(",").map(Number),
      data_cutoff: form.get("data_cutoff"), rule_version: form.get("rule_version"),
      sample_scope: {subject: state.project.subject, region: state.project.target_region, exam_type: state.project.target_exam_type},
      predictions: JSON.parse(form.get("predictions")),
    };
    await api("/api/backtests/freezes", {method: "POST", body: JSON.stringify(payload)}); await load(); toast("预测已冻结；后续验证不会改写本轮记录");
  } catch (error) { toast(error.message, true); }
}

async function evaluateFreeze(id) {
  try {
    const observations = JSON.parse(document.querySelector(`[data-observations="${id}"]`).value);
    const observation_year = Number(document.querySelector(`[data-observation-year="${id}"]`).value);
    await api(`/api/backtests/${id}/evaluate`, {method: "POST", body: JSON.stringify({observation_year, observations})}); await load(); toast("已按冻结预测计算真实分子、分母、误判和漏判");
  } catch (error) { toast(error.message, true); }
}

document.querySelectorAll(".nav-item").forEach(button => button.onclick = () => {
  document.querySelectorAll(".nav-item").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".page").forEach(x => x.classList.remove("active"));
  button.classList.add("active");
  document.querySelector(`#page-${button.dataset.page}`).classList.add("active");
});
document.querySelector("#start-run").onclick = startRun;
document.querySelector("#auto-mode").onclick = setFullAuto;
document.querySelector("#project-form").onsubmit = saveProject;
document.querySelector("#snapshot-form").onsubmit = createSnapshot;
document.querySelector("#base-preview-form").onsubmit = previewBase;
document.querySelector("#manifest-preview-form").onsubmit = previewManifest;
document.querySelector("#freeze-form").onsubmit = freezePredictions;
document.querySelector("#standard-asset-search").oninput = renderStandardAssets;
document.querySelector("#asset-integrity-filter").onchange = renderStandardAssets;
document.querySelector("#question-search").oninput = renderQuestions;
document.querySelector("#show-selected").onclick = event => { selectedOnly = !selectedOnly; event.target.textContent = selectedOnly ? "显示全部" : "只看已入选"; renderQuestions(); };
load().catch(error => toast(error.message, true));
