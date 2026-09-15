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
let currentCandidateIds = [];

const ROLE_LABELS = ["母题候选", "核心例题", "同构练习", "变式练习", "迁移练习", "检测题", "基础巩固题"];
const USAGE_SCENARIOS = ["视频生产", "习题册", "作业", "学案", "专题资料", "备考题池"];

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
  const labelLibrary = (state.label_library_snapshots || []).at(-1);
  document.querySelector("#label-library-version").textContent = labelLibrary
    ? `标签库 ${labelLibrary.prompt_versions.question_type}/${labelLibrary.prompt_versions.question}/${labelLibrary.prompt_versions.knowledge}`
    : "标签库未冻结";
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
  renderTagConfigurations();
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

function renderTagConfigurations() {
  const node = document.querySelector("#tag-config-summary");
  const health = document.querySelector("#tag-config-health");
  const activeId = state.project.active_tag_configuration_id;
  const config = (state.tag_configurations || []).find(item => item.id === activeId);
  if (!config) {
    health.textContent = "尚未配置";
    health.className = "status waiting";
    node.innerHTML = `<div class="evidence-limit"><strong>默认可运行</strong><span>未配置标签不会卡住标准化；进入诊断前建议保存项目基线，以便后续规则版本可追溯。</span></div><div class="quality-gate-list"><span><b>题号边界</b>核查缺号、重号和跨页断裂</span><span><b>答案区边界</b>独立标题行触发，避免误切题干</span><span><b>原题图表</b>与题目绑定，缺图显式异常</span><span><b>未知标签</b>进入待映射，不静默留空</span></div>`;
    return;
  }
  health.textContent = config.status;
  health.className = "status completed";
  const dimensions = config.selected_dimensions.map(key => `<span class="tag good">${esc(config.dimension_labels[key] || key)}</span>`).join("");
  const mappings = Object.entries(config.source_field_mapping || {}).map(([source, target]) => `<span class="tag">${esc(source)} → ${esc(config.dimension_labels[target] || target)}</span>`).join("") || `<span class="quiet">没有原标签映射；由 AI 建立项目初版并标注来源。</span>`;
  const extensions = (config.project_extensions || []).map(item => `<span class="tag frequency">${esc(item)}</span>`).join("") || `<span class="quiet">暂无项目扩展维度。</span>`;
  const gates = Object.entries(config.parse_quality_policy || {}).filter(([key]) => key !== "version").map(([key, value]) => `<span><b>${esc({question_boundary:"题号边界",answer_section_boundary:"答案区边界",image_binding:"题图绑定",deduplication:"重复核查",unmatched_labels:"未知标签"}[key] || key)}</b>${esc(value)}</span>`).join("");
  node.innerHTML = `<div class="base-meta"><strong>${esc(config.name)}</strong><span>${esc(config.onboarding_mode_label)} · ${esc(config.version)}</span></div><p class="quiet">三层资产：${esc(config.three_layer_model.join(" → "))}。AI 补标：${config.ai_fill_missing ? "开启" : "关闭"}；主流程：不阻塞。</p><div class="chip-row">${dimensions}</div><details open><summary>原字段映射</summary><div class="chip-row">${mappings}</div></details><details><summary>当前项目扩展</summary><div class="chip-row">${extensions}</div></details><div class="quality-gate-list">${gates}</div><div class="evidence-limit"><strong>分析粒度</strong><span>${esc(config.analysis_units.option)} ${esc(config.analysis_units.specialized)}</span></div>`;
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
      ? `<strong>${esc(best.video_id)}｜${esc(best.video_name)}</strong><span class="catalog-line">课库：${esc((best.catalogs || []).join("、") || "未标注")}${best.identity_status === "候选同一视频" ? " · 同名簇待核对" : ""}</span><span class="catalog-line">片段：${esc(best.segment_type || "未标注")} ${esc(best.segment_locator || "无时间码")} · 教学目标门禁：${esc(teachingTargetGateLabel(best.teaching_target_gate))}</span><small>${esc(best.reason)}</small>`
      : `<strong>暂无视频候选</strong><small>${esc(item.reason)}</small>`;
    return `<div class="coverage-row"><div><strong>${esc(item.source_name)} · 第 ${esc(item.question_no)} 题</strong><small>${esc(item.status)}：${esc(item.reason)}</small></div><div>${candidate}</div></div>`;
  }).join("") : "";
  const transcriptUnmatched = state.video_assets.filter(item => item.transcript_status === "未匹配").length;
  const catalogCounts = videoImport.catalog_counts || {};
  const catalogCards = Object.entries(catalogCounts).map(([catalog, count]) => `<span class="tag catalog-tag"><b>${esc(catalog)}</b> ${count} 条目录记录</span>`).join("");
  const overlapBreakdown = videoImport.cross_catalog_entity_count
    ? `已确认 ${videoImport.confirmed_cross_catalog_entity_count || 0} 组 · 待核对 ${videoImport.candidate_cross_catalog_entity_count ?? videoImport.cross_catalog_entity_count} 组`
    : "没有跨课库同名簇";
  node.innerHTML = `<div class="diagnosis-summary"><div><b>${videoImport.listing_count || videoImport.record_count}</b><span>课库目录记录</span></div><div><b>${videoImport.video_asset_count}</b><span>去重视频实体</span></div><div><b>${videoImport.cross_catalog_entity_count || 0}</b><span>跨课库同名簇</span></div><div><b>${strong}</b><span>强逐字稿证据</span></div><div><b>${weak}</b><span>弱匹配待复核</span></div><div><b>${transcriptUnmatched}</b><span>逐字稿未匹配</span></div></div>
    <div class="catalog-strip">${catalogCards || `<span class="quiet">旧版清单未记录课库统计，重新导入后补齐。</span>`}<span class="tag catalog-tag">${esc(overlapBreakdown)}</span></div>
    <div class="calibration-actions"><p class="quiet">视频证据适配器 ${esc(videoImport.adapter_version)}。候选会同时检索教材同步课、重难点培优和中考总复习培优；跨课库同名视频只占一个候选位置，但保留全部目录归属。旧 matches 和宣传白名单不作为生产覆盖结论。</p><div>${action}</div></div>${summary}<div class="evidence-limit"><strong>保守边界</strong><span>${coverage ? esc(coverage.evidence_limits.join(" ")) : "需要金样本后才能运行题目—视频证据对照。"}</span></div><div class="coverage-rail">${rows}</div>`;
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
    const videos = coverageItem.candidates.length ? coverageItem.candidates.map(candidate => `<li><strong>${esc(candidate.video_id)}｜${esc(candidate.video_name)}</strong><span>课库：${esc((candidate.catalogs || []).join("、") || "未标注")}${candidate.identity_status === "候选同一视频" ? " · 同名簇待核对" : ""}</span><span>片段：${esc(candidate.segment_type || "未标注")} ${esc(candidate.segment_locator || "无时间码")} · 教学目标门禁：${esc(teachingTargetGateLabel(candidate.teaching_target_gate))}</span><span>${esc(candidate.reason)}</span></li>`).join("") : `<li><span>${esc(coverageItem.reason)}</span></li>`;
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
    return `<article class="standard-asset-card"><div class="standard-asset-visual">${visual}</div><div><p class="eyebrow">${esc(asset.source_name)} · ${esc(asset.question_no || `第 ${asset.ordinal} 项`)}</p><h4>${esc(asset.title)}</h4><div class="chip-row"><span class="tag good">${types.map(typeLabel).join("＋")}</span><span class="tag">图片：${esc(imageIntegrityLabel(asset.image_integrity))}</span><span class="tag">重复 ${asset.duplicate_count || 1} 条</span>${issueTags}</div><div class="chip-row">${normalized}</div>${tagProfileMarkup(asset.tag_profile)}<p>${esc(asset.raw_text || "无可提取文字；请查看保留的原题图片。")}</p></div></article>`;
  }).join("") : `<p class="quiet">尚无标准题目资产。请先冻结本地资料并点击“开始标准化”，或从 Base 只读预览后导入。</p>`;
}

function renderManifestPreview() {
  const node = document.querySelector("#manifest-preview");
  if (!manifestPreview) return;
  const report = manifestPreview.image_report;
  const mappingKeys = [
    ["source_id", "题目 ID"], ["source_paper", "试卷"], ["question_no", "题号"],
    ["question_text", "题目文本（必选）"], ["question_image", "本地题图路径"], ["difficulty", "难度"],
    ["knowledge_tags", "全部涉及知识"], ["core_knowledge_tags", "核心知识"], ["prerequisite_knowledge_tags", "前置/工具知识"], ["distractor_knowledge_tags", "干扰项知识"],
    ["question_type", "整题题型"], ["question_tags", "小问问题任务"], ["solution_tags", "解法"],
    ["condition_tags", "条件"], ["context_tags", "情景"], ["thinking_method_tags", "思想方法"], ["unit_tag_profiles", "逐小问标签 JSON"], ["score", "分值"],
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
    ["knowledge_tags", "全部涉及知识"], ["core_knowledge_tags", "核心知识"], ["prerequisite_knowledge_tags", "前置/工具知识"], ["distractor_knowledge_tags", "干扰项知识"],
    ["question_type", "整题题型"], ["question_tags", "小问问题任务"], ["solution_tags", "解法"],
    ["condition_tags", "条件"], ["context_tags", "情景"], ["thinking_method_tags", "思想方法"], ["unit_tag_profiles", "逐小问标签 JSON"],
    ["difficulty", "难度"], ["historical_ai_quality", "历史 AI 好题"],
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

function teachingTargetGateLabel(value) {
  return ({passed: "通过", mentioned_only: "仅提及，不通过", target_missing: "视频目标待标", target_mismatch: "目标不相交", question_core_unresolved: "题目核心知识待识别"})[value] || value || "未执行";
}

function tagProfileMarkup(profile) {
  if (!profile) return "";
  const knowledge = profile.knowledge || {};
  const groups = [
    ["整题题型", profile.question_type ? [profile.question_type] : profile.question_type_candidates],
    ["核心知识", knowledge.core], ["前置/工具", knowledge.prerequisite], ["全部涉及", knowledge.all], ["干扰项", knowledge.distractor],
    ["问题", profile.question], ["解法", profile.solution], ["条件", profile.condition],
    ["情景", profile.context], ["思想方法", profile.thinking_method],
  ].filter(([, values]) => Array.isArray(values) && values.length);
  if (!groups.length) return `<div class="tag-profile empty"><span>六维标签待识别 · 不会把“出现过”自动当成核心知识</span></div>`;
  return `<div class="tag-profile">${groups.map(([label, values]) => `<span><b>${esc(label)}</b>${esc(values.join("、"))}</span>`).join("")}</div>`;
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
  const selection = (state.selection_runs || []).at(-1);
  if (selection) {
    renderSelectionOverview(selection);
    const reviews = Object.fromEntries((state.selection_reviews || []).filter(item => item.selection_run_id === selection.id).map(item => [item.candidate_id, item]));
    const questions = selection.results.filter(item => {
      const decision = reviews[item.id]?.decision || item.ai_next_route;
      return (!selectedOnly || decision === "进入课程生产") && JSON.stringify(item).toLowerCase().includes(search);
    });
    currentCandidateIds = questions.map(item => item.id);
    renderDownstreamTaskCenter(selection, questions);
    document.querySelector("#question-list").innerHTML = questions.map(item => selectionCard(item, reviews[item.id])).join("") || `<div class="empty-state"><strong>没有符合条件的真实候选</strong><span>调整筛选条件后再试。</span></div>`;
    document.querySelectorAll("[data-selection-review]").forEach(form => form.onsubmit = saveSelectionReview);
    return;
  }
  currentCandidateIds = [];
  renderDownstreamTaskCenter(null, []);
  renderSelectionOverview(null);
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

function renderDownstreamTaskCenter(selection, visibleQuestions) {
  const node = document.querySelector("#downstream-task-center");
  if (!selection) {
    node.innerHTML = `<p class="quiet">形成真实候选池后，可把当前筛选结果冻结为习题册、作业、学案、专题资料或备考题池任务。</p>`;
    return;
  }
  const tasks = (state.downstream_tasks || []).filter(item => {
    const set = (state.question_sets || []).find(candidate => candidate.id === item.question_set_id);
    return set?.selection_run_id === selection.id;
  }).reverse();
  const taskRows = tasks.length ? tasks.map(item => {
    const set = (state.question_sets || []).find(candidate => candidate.id === item.question_set_id);
    return `<div class="downstream-task-row"><div><strong>${esc(item.name)}</strong><small>${esc(item.task_type)} · ${set?.item_count || 0} 题 · ${esc(item.target_region || "未限定地区")}</small></div><span class="tag">${esc(item.status)}</span></div>`;
  }).join("") : `<p class="quiet">尚未建立下游任务。新建后会冻结题目、小问、角色标签与原题图引用。</p>`;
  node.innerHTML = `<div class="panel-title"><div><p class="eyebrow">多用途题目资产</p><h3>基于当前筛选结果新建任务</h3></div><span class="status completed">${visibleQuestions.length} 题当前可见</span></div>
    <p class="quiet">视频优先级只服务课程生产；习题册等任务会重新按目标学生、难度梯度和题目角色编排。</p>
    <form id="downstream-task-form" class="downstream-task-form">
      <label>任务类型<select name="task_type">${["习题册","作业","学案","专题资料","备考题池","视频生产","自定义"].map(item => `<option>${item}</option>`).join("")}</select></label>
      <label>任务名称<input name="name" value="${esc(state.project.content_scope)}习题册"></label>
      <label>目标学生<input name="target_students" value="${esc(state.project.target_students)}"></label>
      <label>内容范围<input name="content_scope" value="${esc(state.project.content_scope)}"></label>
      <label class="task-goal">产出目标<input name="output_goal" placeholder="自定义任务必填；其他类型可补充册次、题量或时长"></label>
      <button class="button primary" id="create-downstream-task" type="submit">计算可用题目…</button>
    </form>
    <p class="selection-footnote" id="downstream-task-hint"></p>
    <div class="downstream-task-list">${taskRows}</div>`;
  node.querySelector("#downstream-task-form").onsubmit = createDownstreamTask;
  node.querySelector('[name="task_type"]').onchange = updateDownstreamTaskCount;
  updateDownstreamTaskCount();
}

function downstreamCandidateIds(taskType) {
  const selection = state.selection_runs.at(-1);
  const reviews = Object.fromEntries((state.selection_reviews || []).filter(item => item.selection_run_id === selection.id).map(item => [item.candidate_id, item]));
  return currentCandidateIds.filter(id => {
    if (taskType === "自定义") return true;
    const candidate = selection.results.find(item => item.id === id);
    const scenarios = reviews[id]?.usage_scenarios || candidate?.ai_usage_scenarios || [];
    return scenarios.includes(taskType);
  });
}

function updateDownstreamTaskCount() {
  const form = document.querySelector("#downstream-task-form");
  if (!form) return;
  const taskType = form.elements.task_type.value;
  const ids = downstreamCandidateIds(taskType);
  const button = form.querySelector("#create-downstream-task");
  button.disabled = !ids.length;
  button.textContent = ids.length ? `冻结 ${ids.length} 道“${taskType}”可用题并建任务` : `当前筛选内无“${taskType}”可用题`;
  document.querySelector("#downstream-task-hint").textContent = taskType === "自定义"
    ? "自定义任务使用当前全部可见题目，并要求填写产出目标。"
    : `只冻结当前可见且已标记“${taskType}”的题；如需增减，请先修改题卡的可用场景并保存。`;
}

function selectionContext() {
  const coverage = (state.coverage_runs || []).at(-1);
  if (!coverage) return null;
  return {
    coverage,
    diagnosis: (state.diagnostic_runs || []).find(item => item.id === coverage.diagnostic_run_id),
    sample: (state.gold_sample_sets || []).find(item => item.id === coverage.gold_sample_id),
  };
}

function renderSelectionOverview(selection) {
  const node = document.querySelector("#selection-foundation");
  if (!selection) {
    const context = selectionContext();
    node.innerHTML = context ? `<div class="panel-title"><div><p class="eyebrow">真实候选池</p><h3>历史覆盖结果尚未形成生产排序</h3></div><span class="status waiting">可补算</span></div><div class="calibration-actions"><p class="quiet">将基于 ${context.sample.actual_size} 道金样本生成题目单元、好题判断和教研预测优先级；无教师反馈也会继续。</p><button class="button primary small" id="create-selection">形成真实候选池</button></div>` : `<div class="panel-title"><div><p class="eyebrow">真实候选池</p><h3>等待真实诊断与视频覆盖</h3></div><span class="status waiting">未就绪</span></div><p class="quiet">当前下方仍为脱敏交互样例。完成数据页中的真实诊断、金样本和保守覆盖后会自动切换为真实候选。</p>`;
    node.querySelector("#create-selection")?.addEventListener("click", createSelectionRun);
    return;
  }
  const s = selection.summary;
  const reviewCount = (state.selection_reviews || []).filter(item => item.selection_run_id === selection.id).length;
  node.innerHTML = `<div class="panel-title"><div><p class="eyebrow">真实候选池 · ${esc(selection.rule_version)}</p><h3>${s.evaluated_count} 道金样本已完成独立判断</h3></div><span class="status completed">教研预测</span></div>
    <div class="diagnosis-summary selection-summary"><div><b>${s.high_frequency_count}</b><span>高频</span></div><div><b>${s.good_question_count}</b><span>好题候选</span></div><div><b>${s.high_frequency_and_good_count}</b><span>高频且好题</span></div><div><b>${s.p1_count} / ${s.p2_count}</b><span>P1 / P2</span></div><div><b>${reviewCount}</b><span>教师已介入</span></div></div>
    <div class="evidence-limit"><strong>证据边界</strong><span>${esc(selection.evidence_limits.join(" "))}</span></div>
    <div class="calibration-actions"><p class="quiet">“只看已入选”按当前有效去向筛选“进入课程生产”。批量通过只接受非异常项，不覆盖老师已纠正的结果。</p><button class="button secondary small" id="batch-pass-selections">批量通过非异常项</button></div>`;
  node.querySelector("#batch-pass-selections").onclick = () => batchPassSelections(selection.id);
}

function selectionCard(item, review) {
  const asset = state.question_assets.find(candidate => candidate.id === item.asset_id);
  const firstImage = asset?.content_blocks?.find(block => block.type === "image" && block.path);
  const visual = firstImage ? `<img src="${assetUrl(firstImage.path)}" alt="${esc(item.title)}的原题图" loading="lazy">` : `<div class="asset-visual-placeholder">${asset?.image_integrity === "missing" ? "检测到缺图" : "本题未声明独立题图"}</div>`;
  const priority = item.production_priority.recommendation;
  const priorityClass = priority.startsWith("P") ? priority : "stop";
  const decision = review?.decision || item.ai_next_route;
  const selectedUnits = new Set(review?.selected_unit_ids || item.units.map(unit => unit.id));
  const roles = new Set(review?.role_labels || item.ai_role_labels || []);
  const scenarios = new Set(review?.usage_scenarios || item.ai_usage_scenarios || []);
  const unitInputs = item.units.map(unit => `<label><input type="checkbox" data-selection-unit="${esc(unit.id)}" ${selectedUnits.has(unit.id) ? "checked" : ""}>${esc(unit.label)}</label>`).join("");
  const coverage = item.coverage.candidates?.[0];
  const videoEvidence = coverage ? `${coverage.video_name || coverage.video_id} · ${(coverage.catalogs || []).join("、")} · ${coverage.evidence_level} · ${teachingTargetGateLabel(coverage.teaching_target_gate)}${coverage.segment_locator ? ` · ${coverage.segment_locator}` : ""}` : "暂无可核验视频候选";
  const saved = review ? `<span class="tag ${review.status === "corrected" ? "risk" : "good"}">${review.status === "corrected" ? "已纠正" : "已接受"}</span>` : `<span class="tag">未介入 · 不阻塞</span>`;
  return `<form class="question-card real-candidate" data-selection-review="${esc(item.id)}">
    <div class="question-visual">${visual}<div class="question-source">${esc(item.source_name)} · 第 ${esc(item.question_no)} 题</div></div>
    <div class="question-body">
      <div class="question-head"><div><p class="eyebrow">${esc(item.id)}</p><h3>${esc(item.title)}</h3></div><div class="priority ${priorityClass}">${esc(priority)}</div></div>
      <div class="chip-row"><span class="tag frequency">频次：${esc(item.frequency.level)} · ${item.frequency.numerator}/${item.frequency.denominator}</span><span class="tag ${item.quality.is_good_candidate ? "good" : ""}">好题：${esc(item.quality.recommendation)}</span><span class="tag">难度：${esc(item.difficulty.level)}</span><span class="tag ${item.exception ? "risk" : ""}">${esc(item.content_health.status)}</span>${saved}</div>
      ${tagProfileMarkup(item.tag_profile)}
      <div class="evidence-grid selection-evidence">
        <div class="evidence-block"><label>好题理由 · 与高频独立</label><strong>${item.quality.score}/${item.quality.score_denominator} 项支持</strong><p>${esc(item.quality.reason)}</p></div>
        <div class="evidence-block"><label>视频覆盖 · ${esc(item.coverage.best_evidence_level)}</label><strong>${esc(item.coverage.status)}</strong><p>${esc(item.coverage.reason)}<br>${esc(videoEvidence)}</p></div>
        <div class="evidence-block"><label>学生价值 · ${esc(item.learner_value.evidence_status)}</label><strong>${esc(item.learner_value.level)}</strong><p>${esc(item.learner_value.reason)}</p></div>
        <div class="evidence-block"><label>优先级 · ${esc(item.production_priority.status)}</label><strong>${esc(item.production_priority.minimum_intervention)}</strong><p>${esc(item.production_priority.reason)}</p></div>
      </div>
      <div class="candidate-dimensions"><span>迁移价值：<b>${esc(item.migration_value)}</b></span><span>跨地区复用：<b>${esc(item.cross_region_reuse)}</b></span><span>既有资产替代：<b>${esc(item.existing_asset_substitutability)}</b></span><span>预计成本：<b>${esc(item.production_cost.level)}</b></span></div>
      <div class="unit-row"><strong>进入后续的题目单元：</strong>${unitInputs}</div>
      <div class="reuse-routing"><div><strong>题目角色（可多选）</strong><div class="route-options">${ROLE_LABELS.map(value => `<label><input type="checkbox" name="role_labels" value="${value}" ${roles.has(value) ? "checked" : ""}>${value}</label>`).join("")}</div></div><div><strong>可用场景（可多选）</strong><div class="route-options">${USAGE_SCENARIOS.map(value => `<label><input type="checkbox" name="usage_scenarios" value="${value}" ${scenarios.has(value) ? "checked" : ""}>${value}</label>`).join("")}</div></div></div>
      <div class="teacher-review selection-review-row"><select name="decision" aria-label="当前视频与课程去向">${["按 AI 建议推进","进入课程生产","仅保留好题池","进入母题改造","暂不使用"].map(value => `<option ${value === (review ? decision : "按 AI 建议推进") ? "selected" : ""}>${value}</option>`).join("")}</select><input name="reason" value="${esc(review?.status === "corrected" && !review.reuse_adjusted ? review.reason : "")}" placeholder="改视频去向或裁剪小问时填写理由"><button class="button secondary small" type="submit">保存本题决定</button></div>
      <p class="selection-footnote">当前视频/课程去向：AI 建议 ${esc(item.ai_next_route)}。角色和场景是复用偏好，不会反向篡改好题判断或视频优先级。</p>
    </div>
  </form>`;
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

async function createTagConfiguration(event) {
  event.preventDefault();
  const values = new FormData(event.currentTarget);
  let mapping = {};
  try {
    const rawMapping = String(values.get("source_field_mapping") || "").trim();
    if (rawMapping) mapping = JSON.parse(rawMapping);
    const payload = {
      name: values.get("name"), subject: values.get("subject"),
      onboarding_mode: values.get("onboarding_mode"),
      selected_dimensions: values.getAll("selected_dimensions"),
      source_field_mapping: mapping,
      custom_dimensions: String(values.get("custom_dimensions") || "").split(/[、,，;；|]+/).map(item => item.trim()).filter(Boolean),
    };
    const result = await api("/api/tag-configurations", {method: "POST", body: JSON.stringify(payload)});
    await load();
    toast(`已启用“${result.name}”；没有标签也不会阻塞 AI 运行`);
  } catch (error) { toast(error.message, true); }
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

async function createSelectionRun() {
  const context = selectionContext();
  if (!context) return;
  const button = document.querySelector("#create-selection");
  if (button) { button.disabled = true; button.textContent = "计算中…"; }
  try {
    await api("/api/selections", {method: "POST", body: JSON.stringify({
      diagnostic_run_id: context.diagnosis.id, gold_sample_id: context.sample.id,
      coverage_run_id: context.coverage.id,
    })});
    await load();
    toast("真实候选池已生成；学生价值与优先级均保留证据等级");
  } catch (error) { toast(error.message, true); }
}

async function saveSelectionReview(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const selection = state.selection_runs.at(-1);
  const values = new FormData(form);
  const selectedUnitIds = [...form.querySelectorAll("[data-selection-unit]:checked")].map(node => node.dataset.selectionUnit);
  try {
    await api("/api/selection-reviews", {method: "POST", body: JSON.stringify({
      selection_run_id: selection.id, candidate_id: form.dataset.selectionReview,
      decision: values.get("decision"), selected_unit_ids: selectedUnitIds,
      role_labels: values.getAll("role_labels"), usage_scenarios: values.getAll("usage_scenarios"),
      reason: values.get("reason"),
    })});
    await load();
    toast("候选题决定已保存；纠正理由只进入当前项目隔离规则");
  } catch (error) { toast(error.message, true); }
}

async function createDownstreamTask(event) {
  event.preventDefault();
  const selection = state.selection_runs.at(-1);
  const values = new FormData(event.currentTarget);
  const taskType = values.get("task_type");
  const candidateIds = downstreamCandidateIds(taskType);
  try {
    const result = await api("/api/downstream-tasks", {method: "POST", body: JSON.stringify({
      selection_run_id: selection.id, candidate_ids: candidateIds,
      task_type: taskType, name: values.get("name"),
      target_students: values.get("target_students"), content_scope: values.get("content_scope"),
      output_goal: values.get("output_goal"), output_formats: ["Word", "飞书云文档"],
    })});
    await load();
    toast(`已冻结 ${result.question_set.item_count} 题；${result.task.task_type}任务契约已建立`);
  } catch (error) { toast(error.message, true); }
}

async function batchPassSelections(selectionRunId) {
  const button = document.querySelector("#batch-pass-selections");
  if (button) { button.disabled = true; button.textContent = "处理中…"; }
  try {
    const result = await api("/api/selection-reviews/batch-pass", {method: "POST", body: JSON.stringify({selection_run_id: selectionRunId})});
    await load();
    toast(`已批量通过 ${result.passed_count} 题，保留 ${result.skipped_count} 个异常或人工纠正对象`);
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
document.querySelector("#tag-config-form").onsubmit = createTagConfiguration;
document.querySelector("#base-preview-form").onsubmit = previewBase;
document.querySelector("#manifest-preview-form").onsubmit = previewManifest;
document.querySelector("#freeze-form").onsubmit = freezePredictions;
document.querySelector("#standard-asset-search").oninput = renderStandardAssets;
document.querySelector("#asset-integrity-filter").onchange = renderStandardAssets;
document.querySelector("#question-search").oninput = renderQuestions;
document.querySelector("#show-selected").onclick = event => { selectedOnly = !selectedOnly; event.target.textContent = selectedOnly ? "显示全部" : "只看视频入选"; renderQuestions(); };
load().catch(error => toast(error.message, true));
