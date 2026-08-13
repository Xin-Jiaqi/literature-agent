const $ = (selector) => document.querySelector(selector);
const state = { me: null, projects: [], activeProject: null };

function csrf() {
  const row = document.cookie.split('; ').find((part) => part.startsWith('la_csrf='));
  return row ? decodeURIComponent(row.split('=').slice(1).join('=')) : '';
}

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = csrf();
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || body.error?.message || '请求失败');
  return body;
}

function showAuth(mode = 'login') {
  $('#auth-view').hidden = false; $('#app-view').hidden = true; $('#account').hidden = true;
  const login = mode === 'login';
  $('#login-form').hidden = !login; $('#register-form').hidden = login;
  $('#login-tab').classList.toggle('active', login); $('#register-tab').classList.toggle('active', !login);
  $('#login-tab').setAttribute('aria-selected', String(login));
  $('#register-tab').setAttribute('aria-selected', String(!login));
}

function lines(value) { return value.split('\n').map((x) => x.trim()).filter(Boolean); }
function money(micros) { return `¥${(Number(micros || 0) / 1_000_000).toFixed(2)}`; }
function toast(message) { const el = $('#toast'); el.textContent = message; el.hidden = false; setTimeout(() => { el.hidden = true; }, 2200); }
function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

async function bootstrap() {
  try { state.me = await api('/v1/me'); }
  catch { showAuth(); return; }
  $('#auth-view').hidden = true; $('#app-view').hidden = false; $('#account').hidden = false;
  $('#account-name').textContent = state.me.display_name || state.me.email;
  $('#today-label').textContent = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date());
  const usage = await api('/v1/usage/summary'); $('#usage-cost').textContent = money(usage.estimated_cost_micros);
  state.projects = await api('/v1/projects');
  if (!state.projects.length) { $('#setup-view').hidden = false; $('#dashboard').hidden = true; return; }
  state.activeProject = state.projects[0];
  $('#setup-view').hidden = true; $('#dashboard').hidden = false;
  renderProjects(); await loadFeed();
}

function renderProjects() {
  const list = $('#project-list'); list.replaceChildren();
  state.projects.forEach((project) => {
    const item = node('div', 'project-item');
    item.append(node('strong', '', project.title));
    item.append(node('span', '', `每日最多 ${project.daily_attention_limit} 篇重点论文`));
    list.append(item);
  });
}

async function loadFeed() {
  const rows = await api('/v1/recommendations/daily');
  $('#feed-count').textContent = `${rows.length} 篇`;
  $('#empty-feed').hidden = rows.length > 0; $('#feed').hidden = rows.length === 0;
  const feed = $('#feed'); feed.replaceChildren();
  rows.forEach((row) => {
    const exp = row.explanation || {};
    const chosen = row.disposition || '';
    const card = node('article', 'paper-card');
    const meta = node('div', 'paper-meta');
    meta.append(node('span', `priority ${row.priority.toLowerCase()}`, row.priority));
    meta.append(node('span', 'score', `相关度 ${row.rank_score}`));
    card.append(meta, node('h3', '', row.title), node('p', 'paper-reason', exp.reason || '等待解释'));
    const matched = exp.matched_terms?.length ? `命中：${exp.matched_terms.join(' · ')}` : '暂无直接命中词';
    card.append(node('div', 'matched', matched));
    const actions = node('div', 'paper-actions'); actions.dataset.id = row.recommendation_id;
    [['read', '要读'], ['later', '稍后'], ['ignore', '忽略']].forEach(([choice, label]) => {
      const button = node('button', chosen === choice ? 'selected' : '', label);
      button.type = 'button'; button.dataset.choice = choice; actions.append(button);
    });
    card.append(actions); feed.append(card);
  });
}

async function refresh() {
  if (!state.activeProject) return;
  const button = $('#refresh-recommendations'); button.disabled = true;
  try {
    const papers = await api('/v1/papers/recent?limit=50');
    if (!papers.length) throw new Error('公共论文库还没有可筛选记录');
    await api(`/v1/projects/${state.activeProject.project_id}/screen`, { method: 'POST', body: JSON.stringify({ work_ids: papers.map((p) => p.work_id) }) });
    await loadFeed(); toast('今日推荐已刷新');
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}

$('#login-tab').addEventListener('click', () => showAuth('login'));
$('#register-tab').addEventListener('click', () => showAuth('register'));
$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault(); $('#auth-error').textContent = '';
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try { await api('/v1/auth/login', { method: 'POST', body: JSON.stringify(data) }); await bootstrap(); }
  catch (error) { $('#auth-error').textContent = error.message; }
});
$('#register-form').addEventListener('submit', async (event) => {
  event.preventDefault(); $('#auth-error').textContent = '';
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try { await api('/v1/auth/register', { method: 'POST', body: JSON.stringify(data) }); await bootstrap(); }
  catch (error) { $('#auth-error').textContent = error.message; }
});
$('#setup-form').addEventListener('submit', async (event) => {
  event.preventDefault(); $('#setup-error').textContent = '';
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const project = await api('/v1/projects', { method: 'POST', body: JSON.stringify({ title: data.title, description: data.description, daily_attention_limit: Number(data.daily_attention_limit) }) });
    await api(`/v1/projects/${project.project_id}/profile`, { method: 'PUT', body: JSON.stringify({ summary: data.description, include_terms: lines(data.include_terms), exclude_terms: lines(data.exclude_terms) }) });
    state.projects = [project]; state.activeProject = project;
    $('#setup-view').hidden = true; $('#dashboard').hidden = false; renderProjects(); await loadFeed();
  } catch (error) { $('#setup-error').textContent = error.message; }
});
$('#feed').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-choice]'); if (!button) return;
  const actions = button.closest('.paper-actions');
  try {
    await api(`/v1/recommendations/${actions.dataset.id}/decision`, { method: 'POST', body: JSON.stringify({ disposition: button.dataset.choice }) });
    actions.querySelectorAll('button').forEach((x) => x.classList.toggle('selected', x === button)); toast('决定已保存');
  } catch (error) { toast(error.message); }
});
$('#refresh-recommendations').addEventListener('click', refresh);
$('#empty-refresh').addEventListener('click', refresh);
$('#logout').addEventListener('click', async () => { try { await api('/v1/auth/logout', { method: 'POST', body: '{}' }); } finally { state.me = null; showAuth(); } });

bootstrap();
