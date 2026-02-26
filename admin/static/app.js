/* ═══════════════════════════════════════════════════════
   meMCP Admin UI — Application Logic
   ═══════════════════════════════════════════════════════ */
'use strict';

// ────────────────────────────────────────────────────────
// STATE
// ────────────────────────────────────────────────────────
let _creds = null;          // { username, password }
let _currentTab = 'dashboard';
let _jobsInterval = null;
let _currentLogFile = null;
let _dbOffset = 0;
const _entityCache = {};    // id → entity object (for click-to-inspect)

// ────────────────────────────────────────────────────────
// API LAYER
// ────────────────────────────────────────────────────────
function _basicAuth() {
  return 'Basic ' + btoa(_creds.username + ':' + _creds.password);
}

async function api(method, path, body) {
  const opts = {
    method,
    headers: { 'Authorization': _basicAuth() },
    credentials: 'omit',
  };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  if (resp.status === 401 || resp.status === 503) {
    _logout();
    return null;
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try { const d = await resp.json(); detail = d.detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

// ────────────────────────────────────────────────────────
// UTILITIES
// ────────────────────────────────────────────────────────
function _esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _badge(cls, text) {
  return `<span class="badge badge-${_esc(cls)}">${_esc(text)}</span>`;
}

function _fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
    + ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function _fmtBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

function _loading(containerId) {
  document.getElementById(containerId).innerHTML =
    `<div class="loading-block"><span class="spinner"></span> Loading…</div>`;
}

function _err(containerId, msg) {
  document.getElementById(containerId).innerHTML =
    `<div class="alert alert-error">${_esc(msg)}</div>`;
}

// ────────────────────────────────────────────────────────
// AUTH
// ────────────────────────────────────────────────────────
document.getElementById('login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const btn   = document.getElementById('login-btn');
  const errEl = document.getElementById('login-error');
  errEl.style.display = 'none';
  btn.disabled    = true;
  btn.textContent = 'Signing in…';

  const username = document.getElementById('l-user').value;
  const password = document.getElementById('l-pass').value;
  _creds = { username, password };

  try {
    const resp = await fetch('/tokens', {
      headers: { 'Authorization': _basicAuth() },
      credentials: 'omit',
    });
    if (resp.status === 401 || resp.status === 503) {
      let msg = 'Invalid credentials';
      try { const d = await resp.json(); msg = d.detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    if (!resp.ok) throw new Error('Server error ' + resp.status);

    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('app').style.display = 'block';
    _showTab('dashboard');
  } catch (ex) {
    errEl.textContent   = ex.message;
    errEl.style.display = 'block';
    _creds = null;
  }

  btn.disabled    = false;
  btn.textContent = 'Sign in';
});

document.getElementById('logout-btn').addEventListener('click', _logout);

function _logout() {
  _creds = null;
  _stopJobPolling();
  document.getElementById('app').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('l-pass').value = '';
}

// ────────────────────────────────────────────────────────
// NAVIGATION
// ────────────────────────────────────────────────────────
document.getElementById('main-nav').addEventListener('click', e => {
  const btn = e.target.closest('[data-tab]');
  if (btn) _showTab(btn.dataset.tab);
});

function _showTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  const el = document.getElementById('tab-' + name);
  if (el) el.classList.add('active');
  _currentTab = name;

  _stopJobPolling();

  if      (name === 'dashboard') _loadDashboard();
  else if (name === 'tokens')   loadTokens();
  else if (name === 'logs')     loadLogs();
  else if (name === 'database') browseDB(0);
  else if (name === 'sources')  loadSources();
  else if (name === 'jobs') {
    loadJobs();
    _jobsInterval = setInterval(loadJobs, 5000);
  }
}

function _stopJobPolling() {
  if (_jobsInterval) { clearInterval(_jobsInterval); _jobsInterval = null; }
}

// ────────────────────────────────────────────────────────
// MODAL
// ────────────────────────────────────────────────────────
function _openModal(title, html) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML    = html;
  document.getElementById('modal-overlay').classList.add('open');
}

function _closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
}

document.getElementById('modal-overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('modal-overlay')) _closeModal();
});
document.getElementById('modal-close-btn').addEventListener('click', _closeModal);
document.getElementById('modal-footer-close').addEventListener('click', _closeModal);

// ────────────────────────────────────────────────────────
// DASHBOARD
// ────────────────────────────────────────────────────────
async function _loadDashboard() {
  try {
    const [stats, tokens, jobs] = await Promise.all([
      api('GET', '/db/stats'),
      api('GET', '/tokens'),
      api('GET', '/jobs'),
    ]);
    if (!stats || !tokens || !jobs) return;

    document.getElementById('s-entities').textContent    = stats.total_entities;
    document.getElementById('s-tok-active').textContent  =
      tokens.tokens.filter(t => t.status === 'active').length;
    document.getElementById('s-tok-total').textContent   = tokens.count;
    document.getElementById('s-jobs-running').textContent =
      jobs.jobs.filter(j => j.status === 'running').length;

    document.getElementById('dash-flavors').innerHTML = `
      <table>
        <thead><tr><th>Flavor</th><th>Entities</th></tr></thead>
        <tbody>${Object.entries(stats.by_flavor).map(([f, c]) =>
          `<tr><td>${_badge(f, f)}</td><td>${c}</td></tr>`).join('')}
        </tbody>
      </table>`;

    document.getElementById('dash-tags').innerHTML = stats.tags.length
      ? `<table>
          <thead><tr><th>Tag Type</th><th>Unique</th><th>Assignments</th></tr></thead>
          <tbody>${stats.tags.map(t =>
            `<tr><td>${_esc(t.tag_type)}</td><td>${t.unique_tags}</td><td>${t.total_assignments}</td></tr>`
          ).join('')}</tbody>
         </table>`
      : '<div class="empty">No tags yet.</div>';

  } catch (ex) {
    console.error('Dashboard error', ex);
  }
}

// ────────────────────────────────────────────────────────
// TOKENS
// ────────────────────────────────────────────────────────
async function loadTokens() {
  _loading('tokens-table');
  try {
    const data = await api('GET', '/tokens');
    if (!data) return;
    if (!data.tokens.length) {
      document.getElementById('tokens-table').innerHTML =
        '<div class="empty">No tokens yet.</div>';
      return;
    }
    document.getElementById('tokens-table').innerHTML = `
      <table>
        <thead><tr>
          <th>ID</th><th>Owner</th><th>Tier</th><th>Status</th>
          <th>Expires</th><th>Calls</th><th>Actions</th>
        </tr></thead>
        <tbody>${data.tokens.map(t => `
          <tr>
            <td>${t.id}</td>
            <td>${_esc(t.owner_name)}</td>
            <td>${_badge(t.tier, t.tier)}</td>
            <td>${_badge(t.status, t.status)}</td>
            <td>${_fmtDate(t.expires_at)}</td>
            <td>${t.call_count}</td>
            <td>
              <span class="flex gap-8">
                <button class="btn btn-ghost btn-sm" onclick="viewTokenStats(${t.id})">Stats</button>
                ${t.status === 'active'
                  ? `<button class="btn btn-danger btn-sm"
                       onclick="revokeToken(${t.id}, '${_esc(t.owner_name)}')">Revoke</button>`
                  : ''}
              </span>
            </td>
          </tr>`).join('')}
        </tbody>
      </table>`;
  } catch (ex) { _err('tokens-table', ex.message); }
}

document.getElementById('tok-form').addEventListener('submit', async e => {
  e.preventDefault();
  const owner  = document.getElementById('tok-owner').value.trim();
  const days   = parseInt(document.getElementById('tok-days').value) || 30;
  const tier   = document.getElementById('tok-tier').value;
  const result = document.getElementById('tok-result');
  result.innerHTML = '';
  try {
    const data = await api('POST', '/tokens', { owner, days, tier });
    if (!data) return;
    result.innerHTML = `
      <div class="token-revealed">
        <strong>Token created — copy the value now, it won't be shown again.</strong>
        <div class="token-value">${_esc(data.token)}</div>
        <div class="text-sm text-muted mt-12">
          ID: <strong>${data.token_id}</strong> &nbsp;·&nbsp;
          Owner: <strong>${_esc(data.owner)}</strong> &nbsp;·&nbsp;
          Tier: ${_badge(data.tier, data.tier)} &nbsp;·&nbsp;
          Expires: ${_fmtDate(data.expires_at)}
        </div>
      </div>`;
    loadTokens();
  } catch (ex) {
    result.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
});

async function revokeToken(id, owner) {
  if (!confirm(`Revoke token #${id} (${owner})?\nThis cannot be undone.`)) return;
  try {
    await api('DELETE', '/tokens/' + id);
    loadTokens();
  } catch (ex) { alert('Error: ' + ex.message); }
}

async function viewTokenStats(id) {
  _openModal('Token #' + id + ' — Statistics',
    '<div class="loading-block"><span class="spinner"></span> Loading…</div>');
  try {
    const d = await api('GET', '/tokens/' + id + '/stats');
    if (!d) return;

    const epRows = Object.entries(d.endpoint_breakdown)
      .sort((a, b) => b[1] - a[1])
      .map(([ep, cnt]) => `<tr><td>${_esc(ep)}</td><td>${cnt}</td></tr>`)
      .join('');

    const recentRows = d.recent_requests.map(r => `
      <tr>
        <td>${_esc(r.endpoint)}</td>
        <td>${_fmtDate(r.timestamp)}</td>
        <td>${r.tokens_used ?? '—'}</td>
        <td class="text-sm"
          style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          ${_esc(r.input_preview)}
        </td>
      </tr>`).join('');

    document.getElementById('modal-body').innerHTML = `
      <div class="flex gap-8 items-center mb-12">
        ${_badge(d.status, d.status)}
        ${_badge(d.tier, d.tier)}
        <span class="text-muted text-sm">Owner: <strong>${_esc(d.owner)}</strong></span>
        <span class="text-muted text-sm">Expires: ${_fmtDate(d.expires_at)}</span>
      </div>
      <p class="mb-12">Total logged calls: <strong>${d.total_logged_calls}</strong></p>
      ${epRows ? `
        <p class="card-title mb-8">Endpoint Breakdown</p>
        <table class="mb-12">
          <thead><tr><th>Endpoint</th><th>Calls</th></tr></thead>
          <tbody>${epRows}</tbody>
        </table>` : ''}
      ${recentRows ? `
        <p class="card-title mb-8">Recent Requests (last 20)</p>
        <table>
          <thead><tr>
            <th>Endpoint</th><th>Time</th><th>Tokens</th><th>Input Preview</th>
          </tr></thead>
          <tbody>${recentRows}</tbody>
        </table>` : '<p class="text-muted">No usage logs recorded yet.</p>'}`;
  } catch (ex) {
    document.getElementById('modal-body').innerHTML =
      `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

// ────────────────────────────────────────────────────────
// LOGS
// ────────────────────────────────────────────────────────
async function loadLogs() {
  _loading('log-files');
  try {
    const data = await api('GET', '/logs');
    if (!data) return;
    if (!data.logs.length) {
      document.getElementById('log-files').innerHTML =
        '<div class="empty">No log files.</div>';
      return;
    }
    document.getElementById('log-files').innerHTML = data.logs.map(f => `
      <div class="file-item" id="file-${_esc(f.name)}" onclick="viewLog('${_esc(f.name)}')">
        <div>
          <div class="file-name">${_esc(f.name)}</div>
          <div class="file-meta">${_fmtBytes(f.size)} · ${_fmtDate(f.modified)}</div>
        </div>
        <span class="text-muted">›</span>
      </div>`).join('');
  } catch (ex) { _err('log-files', ex.message); }
}

async function viewLog(filename) {
  _currentLogFile = filename;
  document.getElementById('log-viewer-name').textContent  = filename;
  document.getElementById('log-reload-btn').style.display = '';
  document.getElementById('log-viewer').textContent       = 'Loading…';

  document.querySelectorAll('.file-item').forEach(el => el.classList.remove('selected'));
  const item = document.getElementById('file-' + filename);
  if (item) item.classList.add('selected');

  try {
    const data = await api('GET', '/logs/' + encodeURIComponent(filename));
    if (!data) return;
    const viewer = document.getElementById('log-viewer');
    viewer.textContent = data.content || '(empty file)';
    viewer.scrollTop   = viewer.scrollHeight;
  } catch (ex) {
    document.getElementById('log-viewer').textContent = 'Error: ' + ex.message;
  }
}

function reloadLog() {
  if (_currentLogFile) viewLog(_currentLogFile);
}

// ────────────────────────────────────────────────────────
// DATABASE BROWSER
// ────────────────────────────────────────────────────────
async function browseDB(offset) {
  if (offset == null) offset = 0;
  _dbOffset = offset;
  _loading('db-results');
  document.getElementById('db-pagination').innerHTML = '';

  const flavor   = document.getElementById('db-flavor').value;
  const category = document.getElementById('db-category').value.trim();
  const search   = document.getElementById('db-search').value.trim();
  const tag      = document.getElementById('db-tag').value.trim();
  const limit    = parseInt(document.getElementById('db-limit').value) || 50;

  const qs = new URLSearchParams({ limit, offset });
  if (flavor)   qs.set('flavor', flavor);
  if (category) qs.set('category', category);
  if (search)   qs.set('search', search);
  if (tag)      qs.set('tag', tag);

  try {
    const data = await api('GET', '/db?' + qs);
    if (!data) return;
    if (!data.entities.length) {
      document.getElementById('db-results').innerHTML =
        '<div class="empty">No entities match your filters.</div>';
      return;
    }

    data.entities.forEach(e => { _entityCache[e.id] = e; });

    document.getElementById('db-results').innerHTML = `
      <table>
        <thead><tr>
          <th>ID</th><th>Flavor</th><th>Category</th><th>Source</th>
          <th>Title / Slug</th><th>Updated</th>
        </tr></thead>
        <tbody>${data.entities.map(e => `
          <tr class="clickable" onclick="viewEntity(${e.id})">
            <td>${e.id}</td>
            <td>${_badge(e.flavor || 'mcp', e.flavor || '?')}</td>
            <td>${_esc(e.category || '—')}</td>
            <td class="text-sm"
              style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${_esc(e.source || '—')}
            </td>
            <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${_esc(e.title || e.slug || '—')}
            </td>
            <td>${_fmtDate(e.updated_at)}</td>
          </tr>`).join('')}
        </tbody>
      </table>`;

    const pg = document.getElementById('db-pagination');
    if (offset > 0) {
      const btn = document.createElement('button');
      btn.className   = 'btn btn-ghost btn-sm';
      btn.textContent = '← Previous';
      btn.onclick     = () => browseDB(Math.max(0, offset - limit));
      pg.appendChild(btn);
    }
    const info = document.createElement('span');
    info.className = 'text-muted text-sm items-center flex';
    info.textContent = `Showing ${offset + 1}–${offset + data.entities.length}`;
    pg.appendChild(info);
    if (data.count === limit) {
      const btn = document.createElement('button');
      btn.className   = 'btn btn-ghost btn-sm';
      btn.textContent = 'Next →';
      btn.onclick     = () => browseDB(offset + limit);
      pg.appendChild(btn);
    }
  } catch (ex) { _err('db-results', ex.message); }
}

function viewEntity(id) {
  const e = _entityCache[id];
  if (!e) return;
  const rows = Object.entries(e)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => {
      const val   = typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v);
      const isLong = val.length > 100;
      return `<tr>
        <td>${_esc(k)}</td>
        <td>${isLong
          ? `<div style="max-height:120px;overflow:auto;white-space:pre-wrap;font-size:12px">${_esc(val)}</div>`
          : _esc(val)}
        </td>
      </tr>`;
    }).join('');
  _openModal(
    `Entity #${id} — ${_esc(e.title || e.slug || '')}`,
    `<table class="kv-table">${rows}</table>`
  );
}

// ────────────────────────────────────────────────────────
// SOURCES
// ────────────────────────────────────────────────────────
async function loadSources() {
  _loading('sources-table');
  try {
    const data = await api('GET', '/sources');
    if (!data) return;
    if (!data.sources.length) {
      document.getElementById('sources-table').innerHTML =
        '<div class="empty">No sources configured.</div>';
      return;
    }
    document.getElementById('sources-table').innerHTML = `
      <table>
        <thead><tr>
          <th>ID</th><th>Section</th><th>Connector</th><th>URL</th>
          <th>LLM</th><th>Enabled</th><th>Actions</th>
        </tr></thead>
        <tbody>${data.sources.map(s => `
          <tr>
            <td class="font-mono">${_esc(s.id)}</td>
            <td>${_badge(s.section || 'mcp', s.section || '—')}</td>
            <td>${_esc(s.connector || '—')}</td>
            <td class="text-sm"
              style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${s.url
                ? `<a href="${_esc(s.url)}" target="_blank"
                     style="color:var(--primary)">${_esc(s.url)}</a>`
                : '—'}
            </td>
            <td>${_badge(s.llm_processing ? 'yes' : 'no', s.llm_processing ? 'yes' : 'no')}</td>
            <td>${_badge(s.enabled !== false ? 'active' : 'revoked',
                         s.enabled !== false ? 'yes' : 'no')}</td>
            <td>
              ${s.id.startsWith('oeuvre.')
                ? `<button class="btn btn-danger btn-sm"
                     onclick="deleteSource('${_esc(s.id)}')">Delete</button>`
                : '<span class="text-muted text-sm">—</span>'}
            </td>
          </tr>`).join('')}
        </tbody>
      </table>`;
  } catch (ex) { _err('sources-table', ex.message); }
}

async function deleteSource(id) {
  if (!confirm(`Delete source "${id}"?\nThis removes it from config.content.yaml and cannot be undone.`)) return;
  try {
    await api('DELETE', '/sources/' + encodeURIComponent(id));
    loadSources();
  } catch (ex) { alert('Error: ' + ex.message); }
}

// ────────────────────────────────────────────────────────
// JOBS
// ────────────────────────────────────────────────────────
document.getElementById('scrape-form').addEventListener('submit', async e => {
  e.preventDefault();
  const source      = document.getElementById('j-source').value.trim() || null;
  const force       = document.getElementById('j-force').checked;
  const disable_llm = document.getElementById('j-no-llm').checked;
  const llm_only    = document.getElementById('j-llm-only').checked;
  const export_yaml = document.getElementById('j-yaml').checked;
  const result      = document.getElementById('scrape-result');
  result.innerHTML  = '';
  try {
    const data = await api('POST', '/scrape', { source, force, disable_llm, llm_only, export_yaml });
    if (!data) return;
    result.innerHTML = `<div class="alert alert-success">
      Job started — ID: <strong>${_esc(data.job_id)}</strong>
    </div>`;
    loadJobs();
  } catch (ex) {
    result.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
});

async function loadJobs() {
  try {
    const data = await api('GET', '/jobs');
    if (!data) return;
    const el = document.getElementById('jobs-table');
    if (!data.jobs.length) {
      el.innerHTML = '<div class="empty">No jobs recorded yet.</div>';
      return;
    }
    const sorted = [...data.jobs].sort((a, b) => {
      if (a.status === 'running' && b.status !== 'running') return -1;
      if (b.status === 'running' && a.status !== 'running') return  1;
      return new Date(b.started_at) - new Date(a.started_at);
    });
    el.innerHTML = `
      <table>
        <thead><tr>
          <th>Job ID</th><th>Started</th><th>Status</th><th>Exit</th>
          <th>Command</th><th>Actions</th>
        </tr></thead>
        <tbody>${sorted.map(j => `
          <tr>
            <td class="font-mono">${_esc(j.job_id)}</td>
            <td>${_fmtDate(j.started_at)}</td>
            <td>
              ${_badge(j.status, j.status)}
              ${j.status === 'running' ? ' <span class="spinner"></span>' : ''}
            </td>
            <td>${j.return_code != null ? j.return_code : '—'}</td>
            <td class="font-mono text-sm"
              style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${_esc(j.command)}
            </td>
            <td>
              <button class="btn btn-ghost btn-sm"
                onclick="viewJobOutput('${_esc(j.job_id)}')">Output</button>
            </td>
          </tr>`).join('')}
        </tbody>
      </table>`;
  } catch (ex) {
    document.getElementById('jobs-table').innerHTML =
      `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

async function viewJobOutput(jobId) {
  _openModal('Job Output — ' + jobId,
    '<div class="loading-block"><span class="spinner"></span> Loading…</div>');
  try {
    const d = await api('GET', '/jobs/' + encodeURIComponent(jobId));
    if (!d) return;
    document.getElementById('modal-body').innerHTML = `
      <div class="flex gap-8 items-center mb-12">
        ${_badge(d.status, d.status)}
        <span class="text-muted text-sm">
          Exit code: <strong>${d.return_code != null ? d.return_code : '—'}</strong>
        </span>
        <span class="text-muted text-sm">Started: ${_fmtDate(d.started_at)}</span>
      </div>
      <p class="font-mono text-sm mb-8" style="color:var(--text-muted)">
        $ ${_esc(d.command)}
      </p>
      <div class="log-viewer">${_esc(d.output || '(no output captured yet)')}</div>`;
  } catch (ex) {
    document.getElementById('modal-body').innerHTML =
      `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}
