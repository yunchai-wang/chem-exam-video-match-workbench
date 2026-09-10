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
  renderArtifacts();
  renderRules();
  renderSettings();
}

function renderMetrics() {
  const s = state.summary;
  const items = [[s.question_count, "标准题目资产"], [s.candidate_count, "已入选候选"], [s.p1_count, "P1 重点生产"], [s.waiting_review_count, "待教师处理"], [s.rule_iteration, "规则迭代轮次"]];
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
  try { const result = await api(`/api/artifacts/${id}/feedback`, {method: "POST", body: JSON.stringify({text})}); await load(); toast(`已归因到“${result.feedback.root_stage_label}”，局部重跑完成`); }
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

document.querySelectorAll(".nav-item").forEach(button => button.onclick = () => {
  document.querySelectorAll(".nav-item").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".page").forEach(x => x.classList.remove("active"));
  button.classList.add("active");
  document.querySelector(`#page-${button.dataset.page}`).classList.add("active");
});
document.querySelector("#start-run").onclick = startRun;
document.querySelector("#auto-mode").onclick = setFullAuto;
document.querySelector("#project-form").onsubmit = saveProject;
document.querySelector("#question-search").oninput = renderQuestions;
document.querySelector("#show-selected").onclick = event => { selectedOnly = !selectedOnly; event.target.textContent = selectedOnly ? "显示全部" : "只看已入选"; renderQuestions(); };
load().catch(error => toast(error.message, true));
