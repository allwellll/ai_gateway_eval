import { endpointFor, protocolForModel } from './lib/routing.js';
import { setupAnalytics } from './analytics.js?v=20261003-2';
const $ = id => document.getElementById(id);
const state = { current: [], job: null, polling: false, busy: false };
function readStore(storage, key) { try { return storage.getItem(key); } catch { return null; } }
function writeStore(storage, key, value) { try { value ? storage.setItem(key, value) : storage.removeItem(key); } catch { /* Storage is optional. */ } }
function defaultBackend() {
  if (window.GATEWAY_CONFIG?.backendUrl) return window.GATEWAY_CONFIG.backendUrl;
  if (location.port === '8080' || location.port === '') return location.origin;
  if (['localhost', '127.0.0.1'].includes(location.hostname)) return 'http://127.0.0.1:8080';
  return `${location.protocol}//${location.hostname}:8080`;
}
let backend = defaultBackend();
function showAlert(message) { $('alert').textContent = message; $('alert').hidden = !message; }
function selectedModels() { return [...$('models').querySelectorAll('input:checked')].map(input => input.value); }
function el(tag, text, className) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; }
function maskedKey(item) { return item.key_prefix ? `${item.key_prefix.slice(0, 6)}***` : '未记录'; }
function updatePreview() {
  const models = selectedModels(), list = $('endpoints');
  list.replaceChildren();
  $('request-count').textContent = `${models.length} 个模型 · ${models.length * (3 + Number($('candy-runs').value))} 次请求`;
  if (!$('base-url').value.trim()) { list.append(el('span', '输入接口地址后显示', 'hint')); return; }
  try {
    const seen = new Set();
    for (const model of models) {
      const endpoint = endpointFor($('base-url').value, model);
      if (seen.has(endpoint)) continue;
      seen.add(endpoint);
      const row = el('div', null, 'endpoint-row');
      row.append(el('b', protocolForModel(model)), el('code', endpoint)); list.append(row);
    }
    if (!models.length) list.append(el('span', '请至少选择一个模型', 'hint'));
  } catch (error) { list.append(el('span', error.message, 'hint')); }
}
function addModel() {
  const model = $('custom-model').value.trim();
  if (!model) return;
  if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,149}$/.test(model)) { showAlert('模型名称格式不正确'); return; }
  const existing = [...$('models').querySelectorAll('input')].find(node => node.value === model);
  if (existing) existing.checked = true;
  else {
    const label = el('label', null, 'model-chip');
    const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = model; checkbox.checked = true;
    label.append(checkbox, el('span', protocolForModel(model) === 'messages' ? '✺' : '✳', 'model-symbol'), el('span', model));
    $('models').append(label);
  }
  $('custom-model').value = ''; showAlert(''); updatePreview();
}
function normalizeBackend(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('请输入不含凭据或参数的 HTTP(S) 服务地址');
  if (location.protocol === 'https:' && url.protocol !== 'https:') throw new Error('当前网页使用 HTTPS，请填写 HTTPS 后端地址，或直接打开服务器网页。');
  return url.href.replace(/\/$/, '');
}
async function api(path, options = {}) {
  const root = normalizeBackend(backend), controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}) };
  const token = $('service-token')?.value.trim();
  if (token) headers.Authorization = `Bearer ${token}`;
  try {
    const response = await fetch(root + path, { ...options, headers, signal: controller.signal, credentials: 'omit' });
    let data;
    try { data = await response.json(); } catch { throw new Error('服务没有返回 JSON，请检查后端地址与 Nginx 转发配置'); }
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : data.detail;
      const error = new Error(detail || `服务请求失败（HTTP ${response.status}）`);
      error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('连接服务超时，请检查服务状态；已提交的任务可能仍在执行');
    if (error instanceof TypeError) throw new Error('无法连接服务，请检查地址、HTTPS、CORS 和服务器端口');
    throw error;
  } finally { clearTimeout(timer); }
}
async function connect() {
  const status = $('service-status'), message = $('connection-message');
  message.replaceChildren();
  try {
    backend = normalizeBackend(defaultBackend());
    await api('/health');
    status.textContent = '评测服务已连接'; $('service-dot').className = 'status-dot connected';
    message.textContent = `已连接 ${backend}`;
    return true;
  } catch (error) {
    status.textContent = '评测服务未连接'; $('service-dot').className = 'status-dot error';
    message.append(el('span', error.message + ' '));
    $('service-panel').hidden = false;
    return false;
  }
}
function setBusy(busy) {
  state.busy = busy;
  $('eval-inputs').disabled = busy;
  $('start-button').disabled = busy;
  $('start-button').textContent = busy ? '评测进行中…' : '开始评测 ↗';
  $('cancel-button').hidden = !busy;
  if ($('service-connect')) $('service-connect').disabled = busy;
  if ($('backend-url')) $('backend-url').disabled = busy;
}
function showProgress(job) {
  state.job = job;
  $('progress-section').hidden = false;
  $('progress-title').textContent = job.phase;
  $('progress-counter').textContent = `${job.completed} / ${job.total}`;
  $('progress-bar').max = job.total || 1; $('progress-bar').value = job.completed;
  const container = $('model-progress'); container.replaceChildren();
  for (const [model, status] of Object.entries(job.models)) {
    const row = el('div', null, 'model-step-row');
    row.append(el('span', model + (status.elapsed_seconds != null ? ` · ${status.elapsed_seconds}s` : ''), 'model-step-name'));
    const steps = el('div', null, 'step-dots');
    for (let i = 0; i < status.steps.length; i++) {
      if (i === 0 || i === 3) steps.append(el('small', i === 0 ? '指纹' : 'Candy'));
      const dot = el('span', null, `step-dot ${status.steps[i]}`);
      dot.title = `${i < 3 ? '指纹' : 'Candy'} ${i < 3 ? i + 1 : i - 2} · ${status.steps[i]}`;
      steps.append(dot);
    }
    row.append(steps); container.append(row);
  }
  state.current = job.results;
  $('retry-save').hidden = !job.results.length || job.saved || ['queued', 'running'].includes(job.status);
  $('export-button').hidden = !job.results.length;
  renderResults();
}
async function pollJob(id) {
  if (state.polling) return;
  state.polling = true; $('resume-button').hidden = true;
  try {
    while (true) {
      const job = await api(`/api/evaluations/${encodeURIComponent(id)}`);
      showProgress(job);
      if (!['queued', 'running'].includes(job.status)) {
        setBusy(false); writeStore(sessionStorage, 'gateway.task', null);
        if (job.status === 'failed' || job.status === 'save_failed') showAlert(job.phase);
        if (! $('analytics-view').hidden) analytics.refresh();
        break;
      }
      setBusy(true);
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
  } catch (error) {
    if (error.status === 404) {
      writeStore(sessionStorage, 'gateway.task', null);
      setBusy(false);
      showAlert(error.message);
      return;
    }
    showAlert(error.message + '。进度查询已暂停，后台任务不会因此取消。');
    $('resume-button').hidden = false;
    // Keep the task ID and avoid submitting duplicate paid requests.
  } finally { state.polling = false; }
}
$('eval-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy) return;
  try {
    const models = selectedModels();
    if (!models.length) throw new Error('请至少选择一个模型');
    if (models.length > 12) throw new Error('一次最多评测 12 个模型');
    for (const model of models) endpointFor($('base-url').value, model);
    const payload = { base_url: $('base-url').value.trim(), api_key: $('api-key').value.trim(), models,
      candy_runs: Number($('candy-runs').value), concurrency: Number($('concurrency').value), timeout: Number($('timeout').value),
      tested_date: $('tested-date').value || null, model_rate: $('model-rate').value || null,
      recharge_rate: $('recharge-rate').value || null, note: $('note').value.trim() };
    if (!payload.api_key) throw new Error('请输入 API key');
    normalizeBackend(backend); showAlert(''); setBusy(true);
    let created;
    try { created = await api('/api/evaluations', { method: 'POST', body: JSON.stringify(payload) }); }
    finally { payload.api_key = ''; }
    $('api-key').type = 'password'; $('toggle-key').textContent = '显示';
    state.current = []; renderResults();
    writeStore(sessionStorage, 'gateway.task', JSON.stringify({ id: created.id, backend }));
    await pollJob(created.id);
  } catch (error) { setBusy(false); showAlert(error.message); }
});
function selectTab(tab) {
  renderResults();
}
function renderResults() {
  const items = state.current;
  $('results-body').replaceChildren(); $('empty-state').hidden = items.length > 0;
  $('result-count').textContent = items.length;
  const emptyTitle = $('empty-state').querySelector('h3'), emptyText = $('empty-state').querySelector('p');
  emptyTitle.textContent = '第一份结果，从一次评测开始';
  emptyText.textContent = '输入接口地址与 key，选择模型后点击「开始评测」。';
  for (const item of items) {
    const row = el('tr');
    const site = el('td', item.domain); site.append(el('small', item.tested_date));
    const key = el('td'); key.append(el('code', maskedKey(item)));
    const model = el('td'); model.append(el('code', item.model), el('small', `/${protocolForModel(item.model)}`));
    const rate = el('td', `${item.model_rate ?? '—'} / ${item.recharge_rate ?? '—'}`);
    const fingerprint = el('td'); fingerprint.append(el('span', item.fingerprint || '—', 'fingerprint'), el('small', item.top_model || '未归因'));
    const candy = el('td', item.candy || '—'); candy.append(el('small', item.candy_answers || '—'));
    const verdict = el('td'); verdict.append(el('span', item.verdict === '换模' ? '疑似换模' : item.verdict, 'verdict ' + ({ 真: 'true', 换模: 'swapped', 无法评测: 'failed' }[item.verdict] || '')));
    const detail = el('td'); const button = el('button', '查看 ↗', 'text-button'); button.type = 'button'; button.addEventListener('click', () => showDetail(item)); detail.append(button);
    row.append(site, key, model, rate, fingerprint, candy, verdict, detail); $('results-body').append(row);
  }
  $('table-info').textContent = state.job ? (state.job.saved ? '本次结果已保存到 PostgreSQL' : '结果尚未全部保存') : '结果会在评测后自动保存';
}
function showDetail(item) {
  const pairs = { '站点': item.domain, 'API key': maskedKey(item), '测试模型': item.model, '日期': item.tested_date,
    '执行时间': new Date(item.tested_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }),
    '出口 IP': item.request_ip || '未知', '模型倍率': item.model_rate ?? '未知', '充值倍率': item.recharge_rate ?? '未知',
    '指纹': item.fingerprint, '归因模型': item.top_model || '未归因', 'Candy': item.candy,
    '逐轮答案': item.candy_answers, '判定': item.verdict === '换模' ? '疑似换模' : item.verdict, '备注': item.note || '—' };
  $('detail-content').replaceChildren();
  for (const [name, value] of Object.entries(pairs)) $('detail-content').append(el('dt', name), el('dd', value));
  $('detail-dialog').showModal();
}
$('models').addEventListener('change', updatePreview); $('base-url').addEventListener('input', updatePreview);
$('candy-runs').addEventListener('change', updatePreview); $('add-model').addEventListener('click', addModel);
$('custom-model').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); addModel(); } });
$('toggle-key').addEventListener('click', () => { const show = $('api-key').type === 'password'; $('api-key').type = show ? 'text' : 'password'; $('toggle-key').textContent = show ? '隐藏' : '显示'; });
$('service-toggle').addEventListener('click', () => { $('service-panel').hidden = !$('service-panel').hidden; });
if ($('service-connect')) $('service-connect').addEventListener('click', async () => { if (await connect()) analytics.refresh(true); });
for (const [id, page] of [['evaluate-nav', 'evaluate'], ['ranking-nav', 'ranking'], ['history-nav', 'history']]) {
  $(id).addEventListener('click', event => {
    event.preventDefault();
    const hash = `#${page}`;
    if (location.hash !== hash) location.hash = hash;
    else navigate();
  });
}
$('close-dialog').addEventListener('click', () => $('detail-dialog').close());
$('cancel-button').addEventListener('click', async () => {
  let id = state.job?.id;
  try {
    if (!id) id = JSON.parse(readStore(sessionStorage, 'gateway.task') || '{}').id;
    if (!id) return;
    const job = await api(`/api/evaluations/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
    showProgress(job); setBusy(false); writeStore(sessionStorage, 'gateway.task', null);
  } catch (error) { showAlert(error.message); }
});
$('resume-button').addEventListener('click', () => {
  try { const saved = JSON.parse(readStore(sessionStorage, 'gateway.task') || '{}'); if (saved.id) pollJob(saved.id); }
  catch { showAlert('没有可恢复的任务'); }
});
$('retry-save').addEventListener('click', async () => {
  try { showProgress(await api(`/api/evaluations/${state.job.id}/save`, { method: 'POST' })); showAlert(''); }
  catch (error) { showAlert(error.message); }
});
$('export-button').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(state.current, null, 2)], { type: 'application/json' }));
  const link = el('a'); link.href = url; link.download = `gateway-evaluation-${Date.now()}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
const analytics = setupAnalytics(api);
function navigate(refresh = true) {
  const page = location.hash === '#evaluate' ? 'evaluate' : location.hash === '#history' ? 'history' : 'ranking';
  const analyticsPage = page !== 'evaluate';
  $('analytics-view').hidden = !analyticsPage;
  $('evaluation-view').hidden = analyticsPage;
  // The ranking view owns the history table; the history nav is a shortcut to it.
  $('ranking-section').hidden = !analyticsPage;
  $('records-section').hidden = !analyticsPage;
  $('analysis-title').textContent = page === 'history' ? '历史记录' : '评测榜单';
  $('page-name').textContent = page === 'evaluate' ? '新建评测' : page === 'history' ? '历史记录' : '评测榜单';
  for (const id of ['evaluate-nav', 'ranking-nav', 'history-nav']) $(id).classList.toggle('active', id === `${page}-nav`);
  if (analyticsPage && refresh) analytics.refresh();
  if (page === 'evaluate' || page === 'ranking') { window.scrollTo(0, 0); }
  if (page === 'history') requestAnimationFrame(() => $('records-section').scrollIntoView({ block: 'start' }));
}
window.addEventListener('hashchange', () => navigate());
async function initialize() {
  navigate(false);
  updatePreview(); renderResults();
  const ready = await connect();
  if (ready && !$('analytics-view').hidden) analytics.refresh();
  if (!ready) {
    $('analysis-status').textContent = '尚未连接评测服务，请在服务设置中连接后刷新分析。';
    $('analysis-status').className = 'analysis-status error';
  }
  try {
    const saved = JSON.parse(readStore(sessionStorage, 'gateway.task') || '{}');
    if (saved.id && saved.backend === backend) {
      setBusy(true); $('progress-section').hidden = false;
      if (ready) pollJob(saved.id); else $('resume-button').hidden = false;
    }
  } catch { /* An invalid previous session should not prevent a new evaluation. */ }
}
initialize();
