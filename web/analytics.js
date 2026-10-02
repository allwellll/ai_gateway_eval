const $ = id => document.getElementById(id);
const labels = { good: '良好', warning: '关注', bad: '问题', unknown: '无法评测' };
const dimensions = { model: '模型', domain: '域名', key_hash: 'Key', key_recorded: 'Key', fingerprint: '指纹', top_model: '实际模型', candy: 'Candy', verdict: '判定' };
const presets = ['gpt-6-astra', 'gpt-6-sol', 'claude-opus-5-5'];
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function icon(name) { const element = node('i'); element.dataset.lucide = name; return element; }
function percent(value) { return value == null ? '—' : `${(value * 100).toFixed(0)}%`; }
function score(value) { return value == null ? '—' : (value * 100).toFixed(1); }
function keyName(group) { return group.key_prefix ? group.key_prefix.slice(0, 6) + '***' : '未记录'; }
function date(value) {
  return new Date(value).toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}
export function eventDay(event) {
  return event.time_precision === 'date' ? event.tested_date : new Date(event.tested_at).toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' });
}
export function orderedEvents(events) {
  return [...events].sort((a, b) => eventDay(a).localeCompare(eventDay(b)) ||
    (a.time_precision === 'date') - (b.time_precision === 'date') ||
    (a.time_precision === 'date' ? 0 : new Date(a.tested_at) - new Date(b.tested_at)) || a.id - b.id);
}
export function fingerprintStatus(event) {
  const verdict = normalizedVerdict(event);
  return verdict === '无法评测' ? 'unknown' : verdict === '换模' ? 'bad' : verdict === '真' ? 'good' : 'warning';
}
export function normalizedVerdict(event) {
  const [correct, scored] = String(event.candy || '').split('/').map(Number);
  const lowCandy = Number.isInteger(correct) && Number.isInteger(scored) && scored > 0 && correct >= 0 && correct <= scored && correct * 2 <= scored;
  const lowAccuracy = Number.isFinite(event.candy_accuracy) && event.candy_accuracy <= .5;
  return event.verdict === '真' && (lowCandy || lowAccuracy)
    ? '存疑' : event.verdict;
}
export function candyStatus(event) {
  return event.candy_accuracy == null ? 'unknown' : event.candy_accuracy > .5 && event.candy_accuracy < .6 ? 'bad' :
    event.candy_accuracy <= .5 ? 'warning' :
    event.candy_accuracy < .8 || (event.candy_answers || '').includes('?') ? 'warning' : 'good';
}
function eventStamp(event) { return event.time_precision === 'date' ? `${event.tested_date} · 仅日期` : date(event.tested_at); }
function showEvent(event, group = event) {
  const fields = {
    '渠道': `${group.domain} / ${keyName(group)}`, '模型': event.model,
    '时间': eventStamp(event), '综合状态': labels[event.status],
    '指纹': event.fingerprint || '未记录', '实际模型': event.top_model || '未记录', '指纹判定': normalizedVerdict(event) || '存疑',
    'Candy': `${event.candy || '未记录'} · ${percent(event.candy_accuracy)}`, '逐轮答案': event.candy_answers || '未记录',
    '备注': event.note || '未记录',
  };
  $('analytics-detail').replaceChildren();
  for (const [label, value] of Object.entries(fields)) $('analytics-detail').append(node('dt', label), node('dd', value));
  $('analytics-dialog').showModal();
}
function miniSequence(group) {
  const container = node('div', null, 'mini-sequence');
  let lastDay;
  for (const event of orderedEvents(group.sequence).slice(-18)) {
    const day = eventDay(event);
    if (day !== lastDay) { container.append(node('span', day.slice(5).replace('-', '/'), 'mini-day')); lastDay = day; }
    const point = node('button', null, 'sequence-point'); point.type = 'button';
    point.title = `${eventStamp(event)} · 指纹 ${normalizedVerdict(event) || '存疑'} · Candy ${event.candy || '未记录'}`;
    point.setAttribute('aria-label', point.title);
    point.append(node('i', null, `quality-${fingerprintStatus(event)}`), node('i', null, `quality-${candyStatus(event)}`));
    point.addEventListener('click', () => showEvent(event, group)); container.append(point);
  }
  return container;
}
function openDatePicker(input) {
  input.focus();
  try { input.showPicker?.(); } catch { /* Native focus remains available in older browsers. */ }
}

export function setupAnalytics(api) {
  const state = { window: '12h', offset: 0, filters: {}, filterLabels: {}, includeUnavailable: false,
    visibleModels: new Set(presets), models: [], modelsExpanded: false, request: 0 };
  function setFilter(field, value, text = value) {
    const keyField = field === 'key_recorded' ? 'key_recorded' : field;
    const activeField = field === 'key_recorded' && state.filters.key_recorded === value ? 'key_recorded' :
      field === 'key_hash' && state.filters.key_hash === value ? 'key_hash' : keyField;
    const removing = state.filters[activeField] === value;
    if (keyField === 'key_hash' || keyField === 'key_recorded') { delete state.filters.key_hash; delete state.filters.key_recorded; }
    if (removing) {
      delete state.filters[keyField]; delete state.filterLabels[keyField];
    } else {
      state.filters[keyField] = value; state.filterLabels[keyField] = text;
    }
    if (keyField === 'model') $('analysis-model').value = state.filters[keyField] || '';
    if (keyField === 'domain') $('analysis-domain').value = state.filters[keyField] || '';
    refresh(true);
  }
  function filterButton(field, value, text = value, className = '') {
    if (value == null || value === '') return node('span', text || '—', className);
    const button = node('button', text, `filter-value ${className}`); button.type = 'button';
    button.dataset.filter = field; button.title = `筛选${dimensions[field]}：${text}`;
    button.addEventListener('click', () => setFilter(field, value, text));
    return button;
  }
  function keyButton(record) {
    const button = filterButton(record.key_hash ? 'key_hash' : 'key_recorded', record.key_hash || false, keyName(record), 'key-value');
    // The display prefix can collide; the filter always uses the complete hash.
    if (record.key_hash) button.title += ` · 标识 ${record.key_hash.slice(0, 8)}`;
    return button;
  }
  function renderFilters() {
    const boxes = [$('analysis-filters'), $('history-filters')].filter(Boolean);
    for (const box of boxes) box.replaceChildren();
    for (const [field, value] of Object.entries(state.filters)) {
      const button = node('button', `${dimensions[field]}：${state.filterLabels[field] || value}`, 'filter-tag');
      button.type = 'button'; button.title = `移除${dimensions[field]}筛选`; button.append(icon('x'));
      button.addEventListener('click', () => {
        delete state.filters[field]; delete state.filterLabels[field];
        if (field === 'domain' || field === 'model') $(`analysis-${field}`).value = '';
        refresh(true);
      });
      boxes.forEach(box => box.append(button.cloneNode(true)));
      boxes.forEach(box => {
        const tag = box.lastElementChild;
        tag.addEventListener('click', () => {
          delete state.filters[field]; delete state.filterLabels[field];
          if (field === 'domain' || field === 'model') $(`analysis-${field}`).value = '';
          refresh(true);
        });
      });
    }
    if (Object.keys(state.filters).length) {
      const clear = node('button', '清除筛选', 'text-button'); clear.type = 'button';
      clear.addEventListener('click', () => {
        state.filters = {}; state.filterLabels = {};
        $('analysis-model').value = ''; $('analysis-domain').value = ''; refresh(true);
      });
      boxes.forEach(box => box.append(clear.cloneNode(true)));
      boxes.forEach(box => box.lastElementChild.addEventListener('click', () => {
        state.filters = {}; state.filterLabels = {};
        $('analysis-model').value = ''; $('analysis-domain').value = ''; refresh(true);
      }));
    }
    for (const group of [$('analysis-windows'), $('history-windows')].filter(Boolean)) for (const button of group.children) {
      const active = button.dataset.window === state.window;
      button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    }
    window.lucide?.createIcons();
  }
  function renderModelOptions(models) {
    const box = $('visible-models');
    if (!box) return;
    if (box._modelResizeHandler) window.removeEventListener('resize', box._modelResizeHandler);
    box.replaceChildren(node('strong', '模型'));
    const list = node('div', null, 'model-toggle-list');
    for (const model of models) {
      const label = node('label', null, 'model-toggle');
      const input = node('input'); input.type = 'checkbox'; input.value = model; input.checked = state.visibleModels.has(model);
      input.addEventListener('change', () => {
        if (input.checked) state.visibleModels.add(model); else state.visibleModels.delete(model);
        render(state.lastData || { leaderboards: [], history: { items: [], total: 0 }, total_groups: 0 });
      });
      label.append(input, node('span', model)); list.append(label);
    }
    const more = node('button', state.modelsExpanded ? '收起' : '展开更多', 'model-toggle-more');
    more.type = 'button'; more.id = 'model-toggle-more'; more.hidden = true;
    more.addEventListener('click', () => {
      state.modelsExpanded = !state.modelsExpanded;
      list.classList.toggle('expanded', state.modelsExpanded);
      more.textContent = state.modelsExpanded ? '收起' : '展开更多';
    });
    box.append(list, more);
    const updateMore = () => {
      const overflowing = list.scrollHeight > list.clientHeight + 1;
      if (!overflowing) state.modelsExpanded = false;
      list.classList.toggle('expanded', state.modelsExpanded);
      more.hidden = !overflowing;
      more.textContent = state.modelsExpanded ? '收起' : '展开更多';
    };
    box._modelResizeHandler = updateMore;
    window.addEventListener('resize', updateMore, { passive: true });
    requestAnimationFrame(updateMore);
    window.setTimeout(updateMore, 0);
  }
  function render(data) {
    state.lastData = data;
    state.models = [...new Set([...(data.models || []), ...data.leaderboards.map(item => item.model).filter(Boolean), ...presets])].sort((a, b) =>
      (presets.includes(a) ? presets.indexOf(a) : presets.length) - (presets.includes(b) ? presets.indexOf(b) : presets.length) || a.localeCompare(b));
    renderModelOptions(state.models);
    const modelSelect = $('analysis-model');
    const domainSelect = $('analysis-domain');
    if (modelSelect) {
      const selected = state.filters.model || '';
      modelSelect.replaceChildren(node('option', '全部模型'));
      modelSelect.firstElementChild.value = '';
      state.models.forEach(model => { const option = node('option', model); option.value = model; modelSelect.append(option); });
      modelSelect.value = selected;
    }
    if (domainSelect) {
      const domains = [...new Set([...(data.domains || []), ...data.history.items.map(item => item.domain).filter(Boolean)])].sort();
      const selected = state.filters.domain || '';
      domainSelect.replaceChildren(node('option', '全部域名'));
      domainSelect.firstElementChild.value = '';
      domains.forEach(domain => { const option = node('option', domain); option.value = domain; domainSelect.append(option); });
      domainSelect.value = selected;
    }
    const boards = data.leaderboards.filter(board => state.visibleModels.has(board.model));
    $('analysis-group-count').textContent = `${boards.length} 模型 · ${boards.reduce((sum, board) => sum + board.total_channels, 0)} 渠道`;
    $('model-leaderboards').replaceChildren();
    const orderedBoards = [...boards].sort((a, b) =>
      (presets.includes(a.model) ? presets.indexOf(a.model) : presets.length) -
      (presets.includes(b.model) ? presets.indexOf(b.model) : presets.length) || a.model.localeCompare(b.model));
    const boardsContainer = node('div', null, 'model-ranking-tables');
    for (const board of orderedBoards) {
      const section = node('section', null, 'model-board');
      const heading = node('div', null, 'model-board-heading');
      const title = node('h3'); title.append(filterButton('model', board.model));
      heading.append(title, node('span', `${board.total_channels} 渠道 · ${board.evaluations} 次`, 'analysis-secondary'));
      const table = node('table', null, 'model-ranking-table');
      const thead = node('thead');
      const headerRow = node('tr');
      for (const label of ['排名', '域名 / Key', '稳定性分数', '指纹', 'Candy', '数量', '评测历史序列图']) headerRow.append(node('th', label));
      thead.append(headerRow);
      const tbody = node('tbody');
      for (const group of board.channels) {
        const row = node('tr', null, 'top-channel');
        const rank = node('td', String(group.model_rank).padStart(2, '0'), 'rank-number');
        const domain = node('td', null, 'channel-name');
        domain.append(filterButton('domain', group.domain), node('small', keyName(group), 'channel-key'));
        const tone = group.stability_score == null ? 'unknown' : group.stability_score >= .8 ? 'good' : group.stability_score >= .6 ? 'warning' : 'bad';
        const stability = node('td', null, `stability score-${tone}`);
        stability.append(node('strong', score(group.stability_score)), node('small', group.stability_score == null ? '信息不足' : '稳定性'));
        const fingerprint = node('td', null, 'ranking-metric');
        fingerprint.append(node('strong', percent(group.identity_match_rate)), node('small', '匹配率'));
        const candy = node('td', null, 'ranking-metric');
        candy.append(node('strong', percent(group.candy_accuracy)), node('small', '正确率'));
        const count = node('td', null, 'ranking-metric');
        count.append(node('strong', String(group.evaluations)), node('small', group.is_reference ? '优先参考' : group.evaluations < 3 ? '样本少' : '次'));
        const sequence = node('td', null, 'channel-sequence');
        sequence.append(miniSequence(group));
        row.append(rank, domain, stability, fingerprint, candy, count, sequence); tbody.append(row);
      }
      table.append(thead, tbody); section.append(heading, table); boardsContainer.append(section);
    }
    if (!orderedBoards.length) $('model-leaderboards').append(node('p', '当前范围暂无已选模型数据', 'analysis-secondary'));
    else $('model-leaderboards').append(boardsContainer);
    $('analysis-record-count').textContent = data.history.total;
    $('analysis-records').replaceChildren();
    for (const record of data.history.items) {
      const row = node('tr');
      const timeCell = node('td', null, 'time-cell');
      const time = node('button', eventStamp(record), 'filter-value time-value'); time.type = 'button'; time.title = '选择评测日期';
      time.addEventListener('click', () => {
        const input = $('analysis-record-date'); input.value = eventDay(record);
        if (state.window === 'custom' && $('analysis-date-from').value === input.value && $('analysis-date-to').value === input.value) {
          $('analysis-date-from').value = ''; $('analysis-date-to').value = ''; state.window = '12h'; refresh(true); return;
        }
        input.style.left = `${Math.max(8, Math.min(innerWidth - 170, time.getBoundingClientRect().left))}px`;
        input.style.top = `${Math.max(0, Math.min(innerHeight - 40, time.getBoundingClientRect().bottom))}px`;
        openDatePicker(input);
      }); timeCell.append(time);
      const domain = node('td'); domain.append(filterButton('domain', record.domain));
      const model = node('td'); model.append(filterButton('model', record.model));
      const key = node('td'); key.append(keyButton(record));
      const fingerprint = node('td'); fingerprint.append(filterButton('fingerprint', record.fingerprint));
      const actual = node('small'); actual.append(filterButton('top_model', record.top_model)); fingerprint.append(actual);
      const candy = node('td'); candy.append(filterButton('candy', record.candy, record.candy, `score-${candyStatus(record)}`), node('small', record.candy_answers || '—'));
      const verdict = node('td'); verdict.append(filterButton('verdict', normalizedVerdict(record), normalizedVerdict(record), `verdict tone-${fingerprintStatus(record)}`));
      const detail = node('td'), button = node('button', null, 'analysis-icon'); button.type = 'button'; button.title = '查看评测详情'; button.setAttribute('aria-label', button.title);
      button.append(icon('ellipsis')); button.addEventListener('click', () => showEvent(record)); detail.append(button);
      row.append(timeCell, domain, model, key, fingerprint, candy, verdict, detail); $('analysis-records').append(row);
    }
    const empty = $('analysis-empty');
    if (empty) empty.hidden = data.history.total !== 0;
    $('analysis-prev').disabled = state.offset === 0;
    $('analysis-next').disabled = state.offset + data.history.limit >= data.history.total;
    $('analysis-page-info').textContent = `共 ${data.history.total} 条 · 第 ${Math.floor(state.offset / data.history.limit) + 1} / ${Math.max(1, Math.ceil(data.history.total / data.history.limit))} 页`;
    window.lucide?.createIcons();
    for (const sequence of document.querySelectorAll('.mini-sequence')) sequence.scrollLeft = sequence.scrollWidth;
  }
  async function refresh(reset = false) {
    if (reset) state.offset = 0;
    const request = ++state.request;
    renderFilters();
    $('analysis-status').className = 'analysis-status'; $('analysis-status').textContent = '正在查询…';
    $('analysis-refresh').disabled = true; $('analytics-view').classList.add('analysis-loading');
    const query = new URLSearchParams({ window: state.window, history_offset: state.offset, history_limit: 30, ...state.filters });
    if (state.window === 'custom') {
      for (const field of ['from', 'to']) if ($(`analysis-date-${field}`).value) query.set(`date_${field}`, $(`analysis-date-${field}`).value);
    }
    query.set('include_unavailable', String(state.includeUnavailable));
    try {
      const data = await api('/api/analytics/dashboard?' + query);
      if (request !== state.request) return;
      render(data); $('analytics-view').classList.remove('analysis-failed');
      $('analysis-status').textContent = `${date(data.from)} 至 ${date(data.to)} · 已更新`;
    } catch (error) {
      if (request !== state.request) return;
      // Do not leave results from a different filter visible under the new tags.
      $('analytics-view').classList.add('analysis-failed');
      $('analysis-status').textContent = `${error.message}。查询未完成，请重试。`;
      $('analysis-status').className = 'analysis-status error';
    } finally {
      if (request === state.request) { $('analytics-view').classList.remove('analysis-loading'); $('analysis-refresh').disabled = false; }
    }
  }
  function windowClick(event) {
    const button = event.target.closest('[data-window]'); if (!button) return;
    state.window = button.dataset.window;
    $('analysis-date-from').value = ''; $('analysis-date-to').value = ''; refresh(true);
  }
  $('analysis-windows').addEventListener('click', windowClick);
  $('history-windows')?.addEventListener('click', windowClick);
  $('analysis-refresh').addEventListener('click', () => refresh(true));
  for (const field of ['domain', 'model']) {
    const input = $(`analysis-${field}`);
    const update = () => {
      const value = input.value.trim();
      if (value === (state.filters[field] || '')) return;
      if (value) setFilter(field, value); else { delete state.filters[field]; delete state.filterLabels[field]; refresh(true); }
    };
    input.addEventListener('change', update);
  }
  for (const field of ['from', 'to']) {
    const input = $(`analysis-date-${field}`);
    input.addEventListener('click', () => openDatePicker(input));
    input.addEventListener('change', () => {
      state.window = $('analysis-date-from').value || $('analysis-date-to').value ? 'custom' : '12h'; refresh(true);
    });
  }
  const recordDate = node('input', null, 'record-date-picker'); recordDate.id = 'analysis-record-date'; recordDate.type = 'date'; recordDate.tabIndex = -1; recordDate.setAttribute('aria-label', '评测日期筛选'); document.body.append(recordDate);
  recordDate.addEventListener('change', () => {
    if (!recordDate.value) return;
    $('analysis-date-from').value = recordDate.value; $('analysis-date-to').value = recordDate.value;
    state.window = 'custom'; refresh(true);
  });
  $('analysis-include-unavailable')?.addEventListener('change', event => { state.includeUnavailable = event.target.checked; refresh(true); });
  $('analysis-prev').addEventListener('click', () => { state.offset = Math.max(0, state.offset - 30); refresh(); });
  $('analysis-next').addEventListener('click', () => { state.offset += 30; refresh(); });
  $('close-analytics-dialog').addEventListener('click', () => $('analytics-dialog').close());
  return { refresh };
}
