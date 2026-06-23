/* ═══════════════════════════════════════════════════════
   meMCP Admin UI — Application Logic (DB-First)
   ═══════════════════════════════════════════════════════ */
'use strict';

// ────────────────────────────────────────────────────────
// STATE
// ────────────────────────────────────────────────────────
let _creds = null;
let _currentTab = 'dashboard';
let _jobsInterval = null;
let _currentLogFile = null;
let _dbOffset = 0;
const _entityCache = {};

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

async function apiUpload(path, formData) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Authorization': _basicAuth() },
    credentials: 'omit',
    body: formData,
  });
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

    // Check if wizard is needed
    try {
      const readiness = await api('GET', '/config/readiness');
      if (readiness && readiness.needs_wizard) {
        _startWizard(readiness);
      } else {
        document.getElementById('app').style.display = 'block';
        _showTab('dashboard');
      }
    } catch (_) {
      document.getElementById('app').style.display = 'block';
      _showTab('dashboard');
    }
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
  document.getElementById('wizard-screen').style.display = 'none';
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
  else if (name === 'tokens')    loadTokens();
  else if (name === 'logs')      loadLogs();
  else if (name === 'database')  browseDB(0);
  else if (name === 'sources')   loadSources();
  else if (name === 'prompts')   { loadPrompts(); loadMcpPrompts(); }
  else if (name === 'settings')  loadSettings();
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
    const [stats, tokens, jobs, seedStatus] = await Promise.all([
      api('GET', '/db/stats'),
      api('GET', '/tokens'),
      api('GET', '/jobs'),
      api('GET', '/seed-status'),
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

    // Seed status
    if (seedStatus) {
      const el = document.getElementById('dash-seed-status');
      if (seedStatus.all_seeded) {
        el.innerHTML = '<div class="text-sm text-muted">Config source: <strong>Database</strong> (seeded)</div>';
      } else {
        let details = [];
        for (const [section, seeded] of Object.entries(seedStatus.sections)) {
          if (!seeded) details.push(`• ${section}`);
        }
        if (seedStatus.prompts_count === 0) details.push('• prompts');
        
        el.innerHTML = `<div class="alert alert-warn">
          <div style="margin-bottom:0.5em"><strong>Database needs initialization</strong></div>
          <div style="margin-bottom:0.8em; font-size:0.9em">
            Seed will populate default configuration (prompts, metrics, chat settings, etc.)
            <div style="margin-top:0.4em; opacity:0.8">Missing: ${details.join(' ')}</div>
          </div>
          <button class="btn btn-primary btn-sm" onclick="triggerSeed()">Seed Database</button>
        </div>`;
      }
    }

    // Wizard banner
    try {
      const readiness = await api('GET', '/config/readiness');
      if (readiness && readiness.needs_wizard) {
        const el = document.getElementById('dash-seed-status');
        el.innerHTML = `<div class="alert alert-error" style="display:flex;align-items:center;justify-content:space-between">
          <span>Setup incomplete — some configuration is missing.</span>
          <button class="btn btn-primary btn-sm" onclick="_startWizard()">Run Setup Wizard</button>
        </div>` + el.innerHTML;
      }
    } catch (_) { /* non-fatal */ }

  } catch (ex) {
    console.error('Dashboard error', ex);
  }
}

async function triggerSeed() {
  try {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Seeding...';
    
    const result = await api('POST', '/seed');
    
    // Poll for job completion
    let attempts = 0;
    while (attempts < 30) {
      await new Promise(r => setTimeout(r, 500));
      try {
        const status = await api('GET', '/seed-status');
        if (status.all_seeded) {
          btn.textContent = 'Seeding Complete!';
          btn.style.backgroundColor = '#28a745';
          setTimeout(() => {
            _loadDashboard(); // Refresh dashboard
            btn.disabled = false;
            btn.textContent = 'Seed Database';
            btn.style.backgroundColor = '';
          }, 1500);
          return;
        }
      } catch (_) { /* keep polling */ }
      attempts++;
    }
    
    // Fallback if polling times out
    btn.disabled = false;
    btn.textContent = 'Seed Database';
    alert('Seed job started. Dashboard will update when complete (watch Jobs tab for progress).');
  } catch (ex) {
    alert('Error: ' + ex.message);
    event.target.disabled = false;
    event.target.textContent = 'Seed Database';
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
      document.getElementById('tokens-table').innerHTML = '<div class="empty">No tokens yet.</div>';
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
          ID: <strong>${data.token_id}</strong> &nbsp;&middot;&nbsp;
          Owner: <strong>${_esc(data.owner)}</strong> &nbsp;&middot;&nbsp;
          Tier: ${_badge(data.tier, data.tier)} &nbsp;&middot;&nbsp;
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
      document.getElementById('log-files').innerHTML = '<div class="empty">No log files.</div>';
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
// DATABASE BROWSER + ENTITY EDITING
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
          <th>Title</th><th>Updated</th><th>Actions</th>
        </tr></thead>
        <tbody>${data.entities.map(e => `
          <tr>
            <td class="text-sm">${_esc(e.id).substring(0,8)}…</td>
            <td>${_badge(e.flavor || 'mcp', e.flavor || '?')}</td>
            <td>${_esc(e.category || '—')}</td>
            <td class="text-sm"
              style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${_esc(e.source || '—')}
            </td>
            <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${_esc(e.title || '—')}
            </td>
            <td class="text-sm">${_fmtDate(e.updated_at)}</td>
            <td>
              <span class="flex gap-4">
                <button class="btn btn-ghost btn-sm" onclick="editEntity('${_esc(e.id)}')">Edit</button>
                <button class="btn btn-danger btn-sm" onclick="deleteEntityUI('${_esc(e.id)}', '${_esc(e.title)}')">Del</button>
              </span>
            </td>
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

async function editEntity(id) {
  _openModal('Edit Entity',
    '<div class="loading-block"><span class="spinner"></span> Loading…</div>');
  try {
    const data = await api('GET', '/db/' + id);
    if (!data) return;
    const e = data.entity;
    document.getElementById('modal-body').innerHTML = `
      <form id="entity-edit-form" onsubmit="saveEntity(event, '${_esc(e.id)}')">
        <div class="form-row mb-8">
          <div class="field field-sm"><label>Flavor</label>
            <select id="ee-flavor">
              ${['personal','stages','oeuvre','identity'].map(f =>
                `<option ${e.flavor===f?'selected':''}>${f}</option>`).join('')}
            </select>
          </div>
          <div class="field field-sm"><label>Category</label>
            <input type="text" id="ee-category" value="${_esc(e.category||'')}">
          </div>
          <div class="field field-sm"><label>Visibility</label>
            <select id="ee-visibility">
              <option ${e.visibility==='public'?'selected':''}>public</option>
              <option ${e.visibility==='private'?'selected':''}>private</option>
            </select>
          </div>
        </div>
        <div class="field mb-8"><label>Title</label>
          <input type="text" id="ee-title" value="${_esc(e.title||'')}" required>
        </div>
        <div class="field mb-8"><label>Description</label>
          <textarea id="ee-description" rows="4" style="width:100%">${_esc(e.description||'')}</textarea>
        </div>
        <div class="form-row mb-8">
          <div class="field field-md"><label>URL</label>
            <input type="text" id="ee-url" value="${_esc(e.url||'')}">
          </div>
          <div class="field field-sm"><label>Source</label>
            <input type="text" id="ee-source" value="${_esc(e.source||'')}">
          </div>
        </div>
        <div class="form-row mb-8">
          <div class="field field-sm"><label>Start Date</label>
            <input type="text" id="ee-start" value="${_esc(e.start_date||'')}" placeholder="2020-01">
          </div>
          <div class="field field-sm"><label>End Date</label>
            <input type="text" id="ee-end" value="${_esc(e.end_date||'')}" placeholder="2023-06">
          </div>
          <div class="field field-sm"><label>Date</label>
            <input type="text" id="ee-date" value="${_esc(e.date||'')}" placeholder="2023-01-15">
          </div>
          <label class="checkbox-row" style="margin-top:20px">
            <input type="checkbox" id="ee-current" ${e.is_current?'checked':''}> Current
          </label>
        </div>
        <hr style="margin:12px 0;border-color:var(--border)">
        <p class="card-title mb-8">Tags</p>
        <div class="field mb-8"><label>Technologies (comma-separated)</label>
          <input type="text" id="ee-tech" value="${_esc((e.technologies||[]).join(', '))}">
        </div>
        <div class="field mb-8"><label>Skills (comma-separated)</label>
          <input type="text" id="ee-skills" value="${_esc((e.skills||[]).join(', '))}">
        </div>
        <div class="field mb-8"><label>Tags (comma-separated)</label>
          <input type="text" id="ee-tags" value="${_esc((e.tags||[]).join(', '))}">
        </div>
        <button type="submit" class="btn btn-primary">Save</button>
      </form>`;
  } catch (ex) {
    document.getElementById('modal-body').innerHTML =
      `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

async function saveEntity(event, id) {
  event.preventDefault();
  const fields = {
    flavor: document.getElementById('ee-flavor').value,
    category: document.getElementById('ee-category').value || null,
    title: document.getElementById('ee-title').value,
    description: document.getElementById('ee-description').value || null,
    url: document.getElementById('ee-url').value || null,
    source: document.getElementById('ee-source').value || null,
    start_date: document.getElementById('ee-start').value || null,
    end_date: document.getElementById('ee-end').value || null,
    date: document.getElementById('ee-date').value || null,
    is_current: document.getElementById('ee-current').checked,
    visibility: document.getElementById('ee-visibility').value,
  };
  const tags = {
    technologies: document.getElementById('ee-tech').value.split(',').map(s=>s.trim()).filter(Boolean),
    skills: document.getElementById('ee-skills').value.split(',').map(s=>s.trim()).filter(Boolean),
    tags: document.getElementById('ee-tags').value.split(',').map(s=>s.trim()).filter(Boolean),
  };
  try {
    await api('PUT', '/db/' + id, fields);
    await api('PUT', '/db/' + id + '/tags', tags);
    _closeModal();
    browseDB(_dbOffset);
  } catch (ex) { alert('Save error: ' + ex.message); }
}

async function deleteEntityUI(id, title) {
  if (!confirm(`Delete entity "${title}"?\nThis cannot be undone.`)) return;
  try {
    await api('DELETE', '/db/' + id);
    browseDB(_dbOffset);
  } catch (ex) { alert('Error: ' + ex.message); }
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
        '<div class="empty">No sources configured. Seed from YAML or add manually.</div>';
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
            <td class="font-mono text-sm">${_esc(s.id)}</td>
            <td>${_badge(s.section || 'mcp', s.section || '—')}</td>
            <td>${_esc(s.connector || '—')}</td>
            <td class="text-sm"
              style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${s.url ? `<a href="${_esc(s.url)}" target="_blank" style="color:var(--primary)">${_esc(s.url)}</a>` : '—'}
            </td>
            <td>${_badge(s.llm_processing ? 'yes' : 'no', s.llm_processing ? 'yes' : 'no')}</td>
            <td>${_badge(s.enabled !== false ? 'active' : 'revoked', s.enabled !== false ? 'yes' : 'no')}</td>
            <td>
              ${s.id.startsWith('oeuvre.')
                ? `<button class="btn btn-danger btn-sm" onclick="deleteSource('${_esc(s.id)}')">Delete</button>`
                : '<span class="text-muted text-sm">—</span>'}
            </td>
          </tr>`).join('')}
        </tbody>
      </table>`;
  } catch (ex) { _err('sources-table', ex.message); }
}

async function deleteSource(id) {
  if (!confirm(`Delete source "${id}"?\nThis cannot be undone.`)) return;
  try {
    await api('DELETE', '/sources/' + encodeURIComponent(id));
    loadSources();
  } catch (ex) { alert('Error: ' + ex.message); }
}

function showAddSourceForm() {
  _openModal('Add Source', `
    <form onsubmit="addSource(event)">
      <div class="form-row mb-8">
        <div class="field field-md"><label>Name</label>
          <input type="text" id="as-name" placeholder="e.g. blog_2" required>
        </div>
        <div class="field field-sm"><label>Connector</label>
          <select id="as-connector">
            <option>github_api</option><option>medium_raw</option>
            <option>manual</option><option>sitemap</option>
            <option>rss</option><option>html</option>
          </select>
        </div>
      </div>
      <div class="field mb-8"><label>URL</label>
        <input type="text" id="as-url" placeholder="https://... or file://data/...">
      </div>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Sub-type</label>
          <select id="as-subtype">
            <option value="">—</option>
            <option>coding</option><option>article</option>
            <option>blog_post</option><option>book</option><option>website</option>
          </select>
        </div>
        <div class="field field-sm"><label>Limit</label>
          <input type="number" id="as-limit" value="0" min="0">
        </div>
        <label class="checkbox-row" style="margin-top:20px">
          <input type="checkbox" id="as-llm" checked> LLM processing
        </label>
      </div>
      <button type="submit" class="btn btn-primary">Add</button>
    </form>`);
}

async function addSource(event) {
  event.preventDefault();
  const name = document.getElementById('as-name').value.trim();
  const body = {
    connector: document.getElementById('as-connector').value,
    url: document.getElementById('as-url').value.trim() || null,
    sub_type_override: document.getElementById('as-subtype').value || null,
    limit: parseInt(document.getElementById('as-limit').value) || 0,
    llm_processing: document.getElementById('as-llm').checked,
    enabled: true,
  };
  try {
    await api('POST', '/sources?name=' + encodeURIComponent(name) + '&section=oeuvre', body);
    _closeModal();
    loadSources();
  } catch (ex) { alert('Error: ' + ex.message); }
}

// File upload
document.getElementById('upload-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fileInput = document.getElementById('upload-file');
  const source    = document.getElementById('upload-source').value.trim();
  const result    = document.getElementById('upload-result');
  result.innerHTML = '';

  if (!fileInput.files.length) {
    result.innerHTML = '<div class="alert alert-error">Please select a file.</div>';
    return;
  }
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);

  let url = '/upload';
  if (source) url += '?linked_source=' + encodeURIComponent(source);

  try {
    const data = await apiUpload(url, formData);
    result.innerHTML = `<div class="alert alert-success">
      Uploaded: <strong>${_esc(data.filename)}</strong> (${_fmtBytes(data.size)})
    </div>`;
    fileInput.value = '';
  } catch (ex) {
    result.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
});

// ────────────────────────────────────────────────────────
// PROMPTS
// ────────────────────────────────────────────────────────
async function loadPrompts() {
  _loading('prompts-list');
  try {
    const data = await api('GET', '/prompts');
    if (!data) return;
    if (!data.prompts.length) {
      document.getElementById('prompts-list').innerHTML =
        '<div class="empty">No prompts found. Run seed to populate.</div>';
      return;
    }

    // Group by category
    const grouped = {};
    data.prompts.forEach(p => {
      if (!grouped[p.category]) grouped[p.category] = [];
      grouped[p.category].push(p);
    });

    let html = '';
    for (const [cat, prompts] of Object.entries(grouped)) {
      html += `<div class="mb-16">
        <h3 class="card-title mb-8">${_esc(cat)}</h3>
        ${prompts.map(p => `
          <div class="flex gap-8 items-center mb-8" style="border-bottom:1px solid var(--border);padding-bottom:8px">
            <div style="flex:1">
              <strong>${_esc(p.name)}</strong>
              <span class="text-muted text-sm">(${_esc(p.prompt_id)}, v${p.version})</span>
            </div>
            <button class="btn btn-ghost btn-sm" onclick="editPrompt('${_esc(p.prompt_id)}')">Edit</button>
          </div>`).join('')}
      </div>`;
    }
    document.getElementById('prompts-list').innerHTML = html;
  } catch (ex) { _err('prompts-list', ex.message); }
}

async function editPrompt(id) {
  _openModal('Edit Prompt',
    '<div class="loading-block"><span class="spinner"></span> Loading…</div>');
  try {
    const data = await api('GET', '/prompts/' + encodeURIComponent(id));
    if (!data) return;
    const p = data.prompt;
    document.getElementById('modal-body').innerHTML = `
      <form onsubmit="savePrompt(event, '${_esc(p.prompt_id)}')">
        <div class="flex gap-8 items-center mb-12">
          <strong>${_esc(p.name)}</strong>
          ${_badge(p.category, p.category)}
          <span class="text-muted text-sm">v${p.version}</span>
        </div>
        <div class="field mb-8"><label>Prompt Content</label>
          <textarea id="pe-content" rows="15" style="width:100%;font-family:monospace;font-size:13px">${_esc(p.content)}</textarea>
        </div>
        <button type="submit" class="btn btn-primary">Save</button>
      </form>`;
  } catch (ex) {
    document.getElementById('modal-body').innerHTML =
      `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

async function savePrompt(event, id) {
  event.preventDefault();
  const content = document.getElementById('pe-content').value;
  try {
    await api('PUT', '/prompts/' + encodeURIComponent(id), { content });
    _closeModal();
    loadPrompts();
  } catch (ex) { alert('Save error: ' + ex.message); }
}

// ────────────────────────────────────────────────────────
// MCP PROMPT TEMPLATES
// ────────────────────────────────────────────────────────
async function loadMcpPrompts() {
  _loading('mcp-prompts-list');
  try {
    const data = await api('GET', '/mcp-prompts');
    if (!data) return;
    if (!data.prompts.length) {
      document.getElementById('mcp-prompts-list').innerHTML =
        '<div class="empty">No MCP prompts found. Run seed to populate defaults.</div>';
      return;
    }
    let html = data.prompts.map(p => `
      <div class="flex gap-8 items-center mb-8" style="border-bottom:1px solid var(--border);padding-bottom:8px">
        <div style="flex:1">
          <strong>${_esc(p.name)}</strong>
          <span class="text-muted text-sm">(${_esc(p.id)})</span>
          <div class="text-muted text-sm">${_esc(p.description)}</div>
        </div>
        <button class="btn btn-ghost btn-sm" onclick="editMcpPrompt('${_esc(p.id)}')">Edit</button>
        <button class="btn btn-ghost btn-sm" style="color:var(--danger)" onclick="deleteMcpPrompt('${_esc(p.id)}')">Delete</button>
      </div>`).join('');
    document.getElementById('mcp-prompts-list').innerHTML = html;
  } catch (ex) { _err('mcp-prompts-list', ex.message); }
}

function _mcpPromptForm(p, isNew) {
  const action = isNew ? 'createMcpPrompt(event)' : `saveMcpPrompt(event, '${_esc(p.id)}')`;
  return `
    <form onsubmit="${action}">
      <div class="field mb-8"><label>ID (kebab-case)</label>
        <input id="mp-id" value="${_esc(p.id || '')}" ${isNew ? '' : 'readonly'} required
               style="width:100%" pattern="[a-z0-9-]+" title="lowercase letters, numbers, hyphens">
      </div>
      <div class="field mb-8"><label>Name</label>
        <input id="mp-name" value="${_esc(p.name || '')}" required style="width:100%">
      </div>
      <div class="field mb-8"><label>Description</label>
        <input id="mp-desc" value="${_esc(p.description || '')}" required style="width:100%">
      </div>
      <div class="field mb-8"><label>Use Case</label>
        <input id="mp-usecase" value="${_esc(p.use_case || '')}" required style="width:100%">
      </div>
      <div class="field mb-8"><label>Prompt Template</label>
        <textarea id="mp-template" rows="10" style="width:100%;font-family:monospace;font-size:13px" required>${_esc(p.prompt_template || '')}</textarea>
      </div>
      <button type="submit" class="btn btn-primary">${isNew ? 'Create' : 'Save'}</button>
    </form>`;
}

function addMcpPrompt() {
  _openModal('New MCP Prompt', _mcpPromptForm({}, true));
}

async function editMcpPrompt(id) {
  _openModal('Edit MCP Prompt',
    '<div class="loading-block"><span class="spinner"></span> Loading…</div>');
  try {
    const data = await api('GET', '/mcp-prompts/' + encodeURIComponent(id));
    if (!data) return;
    document.getElementById('modal-body').innerHTML = _mcpPromptForm(data.prompt, false);
  } catch (ex) {
    document.getElementById('modal-body').innerHTML =
      `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

async function createMcpPrompt(event) {
  event.preventDefault();
  try {
    await api('POST', '/mcp-prompts', {
      id: document.getElementById('mp-id').value,
      name: document.getElementById('mp-name').value,
      description: document.getElementById('mp-desc').value,
      use_case: document.getElementById('mp-usecase').value,
      prompt_template: document.getElementById('mp-template').value,
    });
    _closeModal();
    loadMcpPrompts();
  } catch (ex) { alert('Create error: ' + ex.message); }
}

async function saveMcpPrompt(event, id) {
  event.preventDefault();
  try {
    await api('PUT', '/mcp-prompts/' + encodeURIComponent(id), {
      name: document.getElementById('mp-name').value,
      description: document.getElementById('mp-desc').value,
      use_case: document.getElementById('mp-usecase').value,
      prompt_template: document.getElementById('mp-template').value,
    });
    _closeModal();
    loadMcpPrompts();
  } catch (ex) { alert('Save error: ' + ex.message); }
}

async function deleteMcpPrompt(id) {
  if (!confirm(`Delete MCP prompt "${id}"?`)) return;
  try {
    await api('DELETE', '/mcp-prompts/' + encodeURIComponent(id));
    loadMcpPrompts();
  } catch (ex) { alert('Delete error: ' + ex.message); }
}

// ────────────────────────────────────────────────────────
// SETTINGS (chat, identity, i18n, metrics)
// ────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const [chat, identity, i18n, metrics] = await Promise.all([
      api('GET', '/config/chat'),
      api('GET', '/config/identity'),
      api('GET', '/config/i18n'),
      api('GET', '/config/metrics'),
    ]);
    if (chat)     renderChatConfig(chat.config);
    if (identity) renderIdentityConfig(identity.config);
    if (i18n)     renderI18nConfig(i18n.config);
    if (metrics)  renderMetricsConfig(metrics.config);
  } catch (ex) {
    console.error('Settings load error', ex);
  }
}

function renderChatConfig(cfg) {
  const persona = cfg.persona || {};
  const starters = cfg.starters || [];
  document.getElementById('chat-config-form').innerHTML = `
    <form onsubmit="saveChatConfig(event)">
      <div class="form-row mb-8">
        <div class="field field-md"><label>Persona Name</label>
          <input type="text" id="cc-pname" value="${_esc(persona.name||'')}">
        </div>
        <div class="field field-md"><label>Tagline</label>
          <input type="text" id="cc-ptagline" value="${_esc(persona.tagline||'')}">
        </div>
      </div>
      <div class="field mb-8"><label>Tone</label>
        <input type="text" id="cc-ptone" value="${_esc(persona.tone||'')}" style="width:100%">
      </div>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>LLM Host</label>
          <select id="cc-host">
            <option ${cfg.host==='groq'?'selected':''}>groq</option>
            <option ${cfg.host==='ollama'?'selected':''}>ollama</option>
          </select>
        </div>
        <div class="field field-md"><label>Model</label>
          <input type="text" id="cc-model" value="${_esc(cfg.model||'')}">
        </div>
      </div>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Rate limit/min</label>
          <input type="number" id="cc-rate" value="${cfg.rate_limit_per_minute||20}">
        </div>
        <div class="field field-sm"><label>Max history</label>
          <input type="number" id="cc-hist" value="${cfg.max_history||10}">
        </div>
        <div class="field field-sm"><label>Max input chars</label>
          <input type="number" id="cc-maxin" value="${cfg.max_input_chars||2000}">
        </div>
        <div class="field field-sm"><label>Max output chars</label>
          <input type="number" id="cc-maxout" value="${cfg.max_output_chars||3000}">
        </div>
      </div>
      <div class="field mb-8"><label>Starters (one per line)</label>
        <textarea id="cc-starters" rows="4" style="width:100%">${_esc(starters.join('\n'))}</textarea>
      </div>
      <button type="submit" class="btn btn-primary">Save Chat Config</button>
    </form>`;
}

async function saveChatConfig(event) {
  event.preventDefault();
  const body = {
    host: document.getElementById('cc-host').value,
    model: document.getElementById('cc-model').value,
    rate_limit_per_minute: parseInt(document.getElementById('cc-rate').value) || 20,
    max_history: parseInt(document.getElementById('cc-hist').value) || 10,
    max_input_chars: parseInt(document.getElementById('cc-maxin').value) || 2000,
    max_output_chars: parseInt(document.getElementById('cc-maxout').value) || 3000,
    persona: {
      name: document.getElementById('cc-pname').value,
      tagline: document.getElementById('cc-ptagline').value,
      tone: document.getElementById('cc-ptone').value,
    },
    starters: document.getElementById('cc-starters').value.split('\n').map(s=>s.trim()).filter(Boolean),
  };
  try {
    await api('PUT', '/config/chat', body);
    alert('Chat config saved.');
  } catch (ex) { alert('Error: ' + ex.message); }
}

function renderIdentityConfig(cfg) {
  const data = cfg.data || cfg;
  const langs = Object.keys(data).filter(k => typeof data[k] === 'object');
  if (!langs.length) {
    document.getElementById('identity-config-form').innerHTML =
      '<div class="empty">No identity data. Run seed or add manually.</div>';
    return;
  }

  let html = '<form onsubmit="saveIdentityConfig(event)">';
  for (const lang of langs) {
    const d = data[lang] || {};
    html += `<fieldset style="border:1px solid var(--border);padding:12px;margin-bottom:12px;border-radius:8px">
      <legend><strong>${_esc(lang.toUpperCase())}</strong></legend>
      <div class="form-row mb-8">
        <div class="field field-md"><label>Name</label>
          <input type="text" class="id-field" data-lang="${_esc(lang)}" data-key="name" value="${_esc(d.name||'')}">
        </div>
        <div class="field field-md"><label>Tagline</label>
          <input type="text" class="id-field" data-lang="${_esc(lang)}" data-key="tagline" value="${_esc(d.tagline||'')}">
        </div>
        <div class="field field-md"><label>Location</label>
          <input type="text" class="id-field" data-lang="${_esc(lang)}" data-key="location" value="${_esc(d.location||'')}">
        </div>
      </div>
      <div class="form-row mb-8">
        <div class="field field-md"><label>GitHub URL</label>
          <input type="text" class="id-field" data-lang="${_esc(lang)}" data-key="github_url" value="${_esc(d.github_url||'')}">
        </div>
        <div class="field field-md"><label>LinkedIn URL</label>
          <input type="text" class="id-field" data-lang="${_esc(lang)}" data-key="linkedin_url" value="${_esc(d.linkedin_url||'')}">
        </div>
      </div>
      <div class="form-row mb-8">
        <div class="field field-md"><label>Blog URL</label>
          <input type="text" class="id-field" data-lang="${_esc(lang)}" data-key="blog_url" value="${_esc(d.blog_url||'')}">
        </div>
        <div class="field field-md"><label>Medium URL</label>
          <input type="text" class="id-field" data-lang="${_esc(lang)}" data-key="medium_url" value="${_esc(d.medium_url||'')}">
        </div>
      </div>
      <div class="field mb-8"><label>Description</label>
        <textarea class="id-field" data-lang="${_esc(lang)}" data-key="description" rows="3" style="width:100%">${_esc(d.description||'')}</textarea>
      </div>
    </fieldset>`;
  }
  html += '<button type="submit" class="btn btn-primary">Save Identity</button></form>';
  document.getElementById('identity-config-form').innerHTML = html;
}

async function saveIdentityConfig(event) {
  event.preventDefault();
  const result = {};
  document.querySelectorAll('.id-field').forEach(el => {
    const lang = el.dataset.lang;
    const key = el.dataset.key;
    if (!result[lang]) result[lang] = {};
    result[lang][key] = el.tagName === 'TEXTAREA' ? el.value : el.value;
  });
  try {
    await api('PUT', '/config/identity', { data: result });
    alert('Identity saved.');
  } catch (ex) { alert('Error: ' + ex.message); }
}

function renderI18nConfig(cfg) {
  const langs = cfg.target_languages || [];
  const sleep = cfg.batch_sleep_seconds || 0.4;
  document.getElementById('i18n-config-form').innerHTML = `
    <form onsubmit="saveI18nConfig(event)">
      <div class="form-row mb-8">
        <div class="field field-md"><label>Target Languages (comma-separated)</label>
          <input type="text" id="i18n-langs" value="${_esc(langs.join(', '))}">
        </div>
        <div class="field field-sm"><label>Batch sleep (seconds)</label>
          <input type="number" id="i18n-sleep" value="${sleep}" step="0.1" min="0">
        </div>
      </div>
      <button type="submit" class="btn btn-primary">Save i18n</button>
    </form>`;
}

async function saveI18nConfig(event) {
  event.preventDefault();
  const body = {
    target_languages: document.getElementById('i18n-langs').value.split(',').map(s=>s.trim()).filter(Boolean),
    batch_sleep_seconds: parseFloat(document.getElementById('i18n-sleep').value) || 0.4,
  };
  try {
    await api('PUT', '/config/i18n', body);
    alert('i18n config saved.');
  } catch (ex) { alert('Error: ' + ex.message); }
}

function renderMetricsConfig(cfg) {
  if (!cfg || !Object.keys(cfg).length) {
    document.getElementById('metrics-config-form').innerHTML =
      '<div class="empty">No metrics config. Run seed to populate.</div>';
    return;
  }

  const prof = cfg.proficiency || {};
  const exp = cfg.experience_years || {};
  const div = cfg.diversity || {};
  const growth = cfg.growth || {};
  const rel = cfg.relevance || {};
  const relW = rel.weights || {};

  document.getElementById('metrics-config-form').innerHTML = `
    <form onsubmit="saveMetricsConfig(event)">
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Enabled</label>
          <select id="mc-enabled">
            <option ${cfg.enabled!==false?'selected':''}>true</option>
            <option ${cfg.enabled===false?'selected':''}>false</option>
          </select>
        </div>
        <div class="field field-sm"><label>Version</label>
          <input type="text" id="mc-version" value="${_esc(cfg.version||'1.0')}">
        </div>
      </div>

      <p class="card-title mb-8">Proficiency</p>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Recency weight</label>
          <input type="number" id="mc-prof-rw" value="${prof.recency_weight||0.6}" step="0.1">
        </div>
        <div class="field field-sm"><label>Duration weight</label>
          <input type="number" id="mc-prof-dw" value="${prof.duration_weight||0.4}" step="0.1">
        </div>
        <div class="field field-sm"><label>Halflife (years)</label>
          <input type="number" id="mc-prof-hl" value="${prof.recency_decay_halflife||3.0}" step="0.5">
        </div>
        <div class="field field-sm"><label>Min score</label>
          <input type="number" id="mc-prof-min" value="${prof.min_score||5.0}" step="1">
        </div>
      </div>

      <p class="card-title mb-8">Experience</p>
      <div class="form-row mb-8">
        <label class="checkbox-row">
          <input type="checkbox" id="mc-exp-dedup" ${exp.deduplicate_overlaps!==false?'checked':''}> Deduplicate overlaps
        </label>
        <div class="field field-sm"><label>Current bonus</label>
          <input type="number" id="mc-exp-bonus" value="${exp.current_bonus_multiplier||1.2}" step="0.1">
        </div>
      </div>

      <p class="card-title mb-8">Diversity</p>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Flavor weight</label>
          <input type="number" id="mc-div-fw" value="${div.flavor_weight||0.5}" step="0.1">
        </div>
        <div class="field field-sm"><label>Category weight</label>
          <input type="number" id="mc-div-cw" value="${div.category_weight||0.5}" step="0.1">
        </div>
        <div class="field field-sm"><label>Saturation threshold</label>
          <input type="number" id="mc-div-sat" value="${div.saturation_threshold||10}">
        </div>
      </div>

      <p class="card-title mb-8">Growth</p>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Min timespan (years)</label>
          <input type="number" id="mc-gr-ts" value="${growth.min_timespan_years||1.0}" step="0.5">
        </div>
        <div class="field field-sm"><label>Increasing threshold</label>
          <input type="number" id="mc-gr-inc" value="${growth.increasing_threshold||0.5}" step="0.1">
        </div>
        <div class="field field-sm"><label>Decreasing threshold</label>
          <input type="number" id="mc-gr-dec" value="${growth.decreasing_threshold||-0.3}" step="0.1">
        </div>
      </div>

      <p class="card-title mb-8">Relevance Weights</p>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Proficiency</label>
          <input type="number" id="mc-rw-prof" value="${relW.proficiency||0.30}" step="0.05">
        </div>
        <div class="field field-sm"><label>Frequency</label>
          <input type="number" id="mc-rw-freq" value="${relW.frequency||0.20}" step="0.05">
        </div>
        <div class="field field-sm"><label>Recency</label>
          <input type="number" id="mc-rw-rec" value="${relW.recency||0.20}" step="0.05">
        </div>
        <div class="field field-sm"><label>Diversity</label>
          <input type="number" id="mc-rw-div" value="${relW.diversity||0.15}" step="0.05">
        </div>
        <div class="field field-sm"><label>Experience</label>
          <input type="number" id="mc-rw-exp" value="${relW.experience||0.10}" step="0.05">
        </div>
        <div class="field field-sm"><label>Growth</label>
          <input type="number" id="mc-rw-gro" value="${relW.growth||0.05}" step="0.05">
        </div>
      </div>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Current bonus</label>
          <input type="number" id="mc-rel-cb" value="${rel.current_bonus||10}">
        </div>
        <div class="field field-sm"><label>Stale penalty</label>
          <input type="number" id="mc-rel-sp" value="${rel.stale_penalty||15}">
        </div>
        <div class="field field-sm"><label>Stale threshold (years)</label>
          <input type="number" id="mc-rel-st" value="${rel.stale_threshold_years||5}">
        </div>
      </div>

      <button type="submit" class="btn btn-primary">Save Metrics Config</button>
    </form>`;
}

async function saveMetricsConfig(event) {
  event.preventDefault();
  const body = {
    enabled: document.getElementById('mc-enabled').value === 'true',
    version: document.getElementById('mc-version').value,
    proficiency: {
      recency_weight: parseFloat(document.getElementById('mc-prof-rw').value),
      duration_weight: parseFloat(document.getElementById('mc-prof-dw').value),
      recency_decay_halflife: parseFloat(document.getElementById('mc-prof-hl').value),
      min_score: parseFloat(document.getElementById('mc-prof-min').value),
    },
    experience_years: {
      deduplicate_overlaps: document.getElementById('mc-exp-dedup').checked,
      current_bonus_multiplier: parseFloat(document.getElementById('mc-exp-bonus').value),
    },
    diversity: {
      flavor_weight: parseFloat(document.getElementById('mc-div-fw').value),
      category_weight: parseFloat(document.getElementById('mc-div-cw').value),
      saturation_threshold: parseInt(document.getElementById('mc-div-sat').value),
    },
    growth: {
      min_timespan_years: parseFloat(document.getElementById('mc-gr-ts').value),
      increasing_threshold: parseFloat(document.getElementById('mc-gr-inc').value),
      decreasing_threshold: parseFloat(document.getElementById('mc-gr-dec').value),
    },
    relevance: {
      weights: {
        proficiency: parseFloat(document.getElementById('mc-rw-prof').value),
        frequency: parseFloat(document.getElementById('mc-rw-freq').value),
        recency: parseFloat(document.getElementById('mc-rw-rec').value),
        diversity: parseFloat(document.getElementById('mc-rw-div').value),
        experience: parseFloat(document.getElementById('mc-rw-exp').value),
        growth: parseFloat(document.getElementById('mc-rw-gro').value),
      },
      current_bonus: parseInt(document.getElementById('mc-rel-cb').value),
      stale_penalty: parseInt(document.getElementById('mc-rel-sp').value),
      stale_threshold_years: parseInt(document.getElementById('mc-rel-st').value),
    },
  };
  try {
    await api('PUT', '/config/metrics', body);
    alert('Metrics config saved.');
  } catch (ex) { alert('Error: ' + ex.message); }
}

// ────────────────────────────────────────────────────────
// JOBS + ACTIONS
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

async function triggerAction(endpoint, label) {
  if (!confirm(`Run ${label}?`)) return;
  try {
    const data = await api('POST', endpoint);
    if (data) alert(`${label} job started (ID: ${data.job_id})`);
    if (_currentTab === 'jobs') loadJobs();
  } catch (ex) { alert('Error: ' + ex.message); }
}

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

// ────────────────────────────────────────────────────────
// SETUP WIZARD
// ────────────────────────────────────────────────────────

const _WIZ_STEPS = [
  { id: 'welcome',   label: 'Welcome' },
  { id: 'server',    label: 'Server' },
  { id: 'llm',       label: 'LLM' },
  { id: 'identity',  label: 'Identity' },
  { id: 'source',    label: 'Source' },
  { id: 'i18n',      label: 'Languages' },
  { id: 'endpoints', label: 'Access' },
  { id: 'finish',    label: 'Finish' },
];

let _wizStep = 0;
let _wizData = {};   // collects form data across steps
let _wizReadiness = {};

function _startWizard(readiness) {
  _wizStep = 0;
  _wizData = {};
  _wizReadiness = readiness || {};
  document.getElementById('wizard-screen').style.display = 'flex';
  document.getElementById('app').style.display = 'none';
  _renderWizardStep();
}

function _renderStepper() {
  document.getElementById('wizard-stepper').innerHTML = _WIZ_STEPS.map((s, i) =>
    `<div class="wiz-dot${i === _wizStep ? ' active' : ''}${i < _wizStep ? ' completed' : ''}"
          title="${_esc(s.label)}">${i < _wizStep ? '✓' : i + 1}</div>`
  ).join('');
  document.getElementById('wizard-step-label').textContent =
    `Step ${_wizStep + 1} of ${_WIZ_STEPS.length}: ${_WIZ_STEPS[_wizStep].label}`;
}

function _renderWizardStep() {
  _renderStepper();
  const ct = document.getElementById('wizard-content');
  const step = _WIZ_STEPS[_wizStep];

  // Nav buttons
  document.getElementById('wiz-back').style.display = _wizStep === 0 ? 'none' : '';
  const nextBtn = document.getElementById('wiz-next');
  if (step.id === 'finish') {
    nextBtn.textContent = 'Go to Dashboard';
  } else if (step.id === 'welcome') {
    nextBtn.textContent = 'Get Started';
  } else {
    nextBtn.textContent = 'Next';
  }

  // Dispatch to step renderer
  switch (step.id) {
    case 'welcome':   _wizStep_welcome(ct); break;
    case 'server':    _wizStep_server(ct);   break;
    case 'llm':       _wizStep_llm(ct);      break;
    case 'identity':  _wizStep_identity(ct); break;
    case 'source':    _wizStep_source(ct);   break;
    case 'i18n':      _wizStep_i18n(ct);     break;
    case 'endpoints': _wizStep_endpoints(ct); break;
    case 'finish':    _wizStep_finish(ct);   break;
  }
}

async function _wizardNext() {
  const step = _WIZ_STEPS[_wizStep];
  // Collect & save current step
  try {
    await _wizSaveStep(step.id);
  } catch (ex) {
    const errEl = document.getElementById('wiz-error');
    if (errEl) {
      errEl.textContent = ex.message;
      errEl.style.display = 'block';
    } else {
      alert('Error: ' + ex.message);
    }
    return;
  }

  if (_wizStep < _WIZ_STEPS.length - 1) {
    _wizStep++;
    _renderWizardStep();
  } else {
    _closeWizard();
  }
}

function _wizardBack() {
  if (_wizStep > 0) {
    _wizStep--;
    _renderWizardStep();
  }
}

function _wizardSkip() {
  if (confirm('Skip setup? You can re-launch it from the dashboard.')) {
    _closeWizard();
  }
}

function _closeWizard() {
  document.getElementById('wizard-screen').style.display = 'none';
  document.getElementById('app').style.display = 'block';
  _showTab('dashboard');
}

// ── Step renderers ──────────────────────────────────────

function _wizStep_welcome(ct) {
  const warnings = (_wizReadiness.warnings || []).map(w =>
    `<li>${_esc(w)}</li>`).join('');
  ct.innerHTML = `
    <h2>Welcome to meMCP</h2>
    <p>This wizard will help you configure the basics to get started.</p>
    ${warnings ? `<div class="alert alert-error" style="margin-top:12px">
      <strong>Things to set up:</strong><ul style="margin:8px 0 0 16px">${warnings}</ul>
    </div>` : '<div class="alert alert-success" style="margin-top:12px">Everything looks good! Walk through the steps to review your config.</div>'}
    <p class="text-muted text-sm" style="margin-top:16px">You can skip this wizard at any time and configure settings later via the admin panel.</p>
    <div id="wiz-error" class="alert alert-error" style="display:none;margin-top:12px"></div>`;
}

async function _wizStep_server(ct) {
  ct.innerHTML = '<div class="loading-block"><span class="spinner"></span> Loading…</div>';
  try {
    const d = await api('GET', '/config/server');
    const cfg = (d && d.config) || {};
    _wizData.server = cfg;
    ct.innerHTML = `
      <h2>Server Configuration</h2>
      <p class="text-muted text-sm mb-12">How your MCP server is reachable.</p>
      <div class="form-row mb-8">
        <div class="field field-lg"><label>Base URL</label>
          <input type="text" id="wiz-base-url" value="${_esc(cfg.base_url || 'http://localhost:8000')}" placeholder="http://localhost:8000">
        </div>
      </div>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Port</label>
          <input type="number" id="wiz-port" value="${cfg.port || 8000}" min="1" max="65535">
        </div>
        <div class="field field-md"><label>Host</label>
          <input type="text" id="wiz-host" value="${_esc(cfg.host || '0.0.0.0')}" placeholder="0.0.0.0">
        </div>
      </div>
      <div id="wiz-error" class="alert alert-error" style="display:none"></div>`;
  } catch (ex) {
    ct.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

async function _wizStep_llm(ct) {
  ct.innerHTML = '<div class="loading-block"><span class="spinner"></span> Loading…</div>';
  try {
    const d = await api('GET', '/config/llm');
    const cfg = (d && d.config) || {};
    _wizData.llm = cfg;
    const backend = cfg.backend || 'none';
    ct.innerHTML = `
      <h2>LLM Backend</h2>
      <p class="text-muted text-sm mb-12">Choose how meMCP enriches and translates your data.</p>
      <div class="wiz-radio-group mb-12">
        <div class="wiz-radio-card${backend === 'ollama' ? ' selected' : ''}" data-val="ollama" onclick="_wizSelectLLM(this)">
          <strong>Ollama</strong>
          <span class="text-muted text-sm">Local LLM — free, private, requires Ollama running</span>
        </div>
        <div class="wiz-radio-card${backend === 'groq' ? ' selected' : ''}" data-val="groq" onclick="_wizSelectLLM(this)">
          <strong>Groq</strong>
          <span class="text-muted text-sm">Cloud API — fast, requires API key</span>
        </div>
        <div class="wiz-radio-card${backend === 'none' ? ' selected' : ''}" data-val="none" onclick="_wizSelectLLM(this)">
          <strong>None</strong>
          <span class="text-muted text-sm">No LLM — raw data only, no enrichment</span>
        </div>
      </div>
      <div id="wiz-llm-fields"></div>
      <div id="wiz-error" class="alert alert-error" style="display:none"></div>`;
    _wizRenderLLMFields(backend, cfg);
  } catch (ex) {
    ct.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

function _wizSelectLLM(el) {
  document.querySelectorAll('.wiz-radio-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  _wizRenderLLMFields(el.dataset.val, _wizData.llm || {});
}

function _wizRenderLLMFields(backend, cfg) {
  const ct = document.getElementById('wiz-llm-fields');
  if (backend === 'ollama') {
    ct.innerHTML = `
      <div class="form-row mb-8">
        <div class="field field-md"><label>Ollama URL</label>
          <input type="text" id="wiz-llm-url" value="${_esc(cfg.ollama_url || 'http://localhost:11434')}" placeholder="http://localhost:11434">
        </div>
        <div class="field field-md"><label>Model</label>
          <input type="text" id="wiz-llm-model" value="${_esc(cfg.ollama_model || 'llama3.2')}" placeholder="llama3.2">
        </div>
      </div>`;
  } else if (backend === 'groq') {
    ct.innerHTML = `
      <div class="form-row mb-8">
        <div class="field field-lg"><label>Groq API Key</label>
          <input type="password" id="wiz-llm-apikey" value="" placeholder="gsk_...">
          <span class="text-muted text-sm">Leave empty to keep existing key</span>
        </div>
        <div class="field field-md"><label>Model</label>
          <input type="text" id="wiz-llm-model" value="${_esc(cfg.groq_model || 'llama-3.3-70b-versatile')}" placeholder="llama-3.3-70b-versatile">
        </div>
      </div>`;
  } else {
    ct.innerHTML = '<p class="text-muted text-sm">No LLM will be used. Data will be stored as-is without enrichment or translation.</p>';
  }
}

async function _wizStep_identity(ct) {
  ct.innerHTML = '<div class="loading-block"><span class="spinner"></span> Loading…</div>';
  try {
    const d = await api('GET', '/config/identity');
    const cfg = (d && d.config) || {};
    _wizData.identity = cfg;
    ct.innerHTML = `
      <h2>Your Identity</h2>
      <p class="text-muted text-sm mb-12">Basic info about the person this meMCP instance represents.</p>
      <div class="form-row mb-8">
        <div class="field field-md"><label>Name <span style="color:var(--danger)">*</span></label>
          <input type="text" id="wiz-id-name" value="${_esc(cfg.name || '')}" placeholder="Your full name" required>
        </div>
        <div class="field field-md"><label>Tagline</label>
          <input type="text" id="wiz-id-tagline" value="${_esc(cfg.tagline || '')}" placeholder="e.g. Full-Stack Developer">
        </div>
      </div>
      <div class="field mb-8"><label>Bio</label>
        <textarea id="wiz-id-bio" rows="4" style="width:100%" placeholder="A short paragraph about yourself…">${_esc(cfg.bio || '')}</textarea>
      </div>
      <div id="wiz-error" class="alert alert-error" style="display:none"></div>`;
  } catch (ex) {
    ct.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

async function _wizStep_source(ct) {
  ct.innerHTML = '<div class="loading-block"><span class="spinner"></span> Loading…</div>';
  try {
    const d = await api('GET', '/sources');
    const existing = (d && d.sources) || [];
    if (existing.length > 0) {
      ct.innerHTML = `
        <h2>Data Sources</h2>
        <p class="text-muted text-sm mb-12">You already have <strong>${existing.length}</strong> source(s) configured. You can add more in the admin panel later.</p>
        <table><thead><tr><th>Name</th><th>Connector</th><th>Enabled</th></tr></thead>
        <tbody>${existing.map(s => `<tr><td>${_esc(s.name)}</td><td>${_esc(s.connector || '—')}</td><td>${s.enabled ? 'Yes' : 'No'}</td></tr>`).join('')}</tbody></table>
        <div id="wiz-error" class="alert alert-error" style="display:none"></div>`;
      return;
    }
    ct.innerHTML = `
      <h2>First Data Source</h2>
      <p class="text-muted text-sm mb-12">Add at least one source for meMCP to scrape. You can add more later.</p>
      <div class="form-row mb-8">
        <div class="field field-md"><label>Source Name <span style="color:var(--danger)">*</span></label>
          <input type="text" id="wiz-src-name" placeholder="e.g. github, blog" required>
        </div>
        <div class="field field-sm"><label>Connector</label>
          <select id="wiz-src-connector" onchange="_wizSrcConnectorChange()">
            <option value="github_api">GitHub API</option>
            <option value="medium_raw">Medium</option>
            <option value="sitemap">Sitemap</option>
            <option value="rss">RSS Feed</option>
            <option value="html">HTML page</option>
            <option value="manual">Manual / PDF</option>
          </select>
        </div>
      </div>
      <div id="wiz-src-fields"></div>
      <div class="form-row mb-8">
        <div class="field field-sm"><label>Limit</label>
          <input type="number" id="wiz-src-limit" value="0" min="0">
          <span class="text-muted text-sm">0 = no limit</span>
        </div>
        <label class="checkbox-row" style="margin-top:20px">
          <input type="checkbox" id="wiz-src-llm" checked> LLM processing
        </label>
      </div>
      <div id="wiz-error" class="alert alert-error" style="display:none"></div>`;
    _wizSrcConnectorChange();
  } catch (ex) {
    ct.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

function _wizSrcConnectorChange() {
  const connector = document.getElementById('wiz-src-connector').value;
  const ct = document.getElementById('wiz-src-fields');
  if (connector === 'manual') {
    ct.innerHTML = `<p class="text-muted text-sm mb-8">Upload files via the Sources tab after setup.</p>`;
  } else {
    ct.innerHTML = `
      <div class="field mb-8"><label>URL <span style="color:var(--danger)">*</span></label>
        <input type="text" id="wiz-src-url" placeholder="${_wizSrcPlaceholder(connector)}">
      </div>`;
  }
}

function _wizSrcPlaceholder(connector) {
  switch (connector) {
    case 'github_api':  return 'https://api.github.com/users/USERNAME/repos';
    case 'medium_raw':  return 'https://medium.com/@username';
    case 'sitemap':     return 'https://example.com/sitemap.xml';
    case 'rss':         return 'https://example.com/feed.xml';
    case 'html':        return 'https://example.com/page';
    default:            return 'https://...';
  }
}

async function _wizStep_i18n(ct) {
  ct.innerHTML = '<div class="loading-block"><span class="spinner"></span> Loading…</div>';
  try {
    const d = await api('GET', '/config/i18n');
    const cfg = (d && d.config) || {};
    const selected = (cfg.target_languages || '').split(',').map(s => s.trim()).filter(Boolean);
    const langs = [
      { code: 'de', name: 'German' },  { code: 'fr', name: 'French' },
      { code: 'es', name: 'Spanish' }, { code: 'it', name: 'Italian' },
      { code: 'pt', name: 'Portuguese' }, { code: 'ja', name: 'Japanese' },
      { code: 'zh', name: 'Chinese' }, { code: 'ko', name: 'Korean' },
    ];
    ct.innerHTML = `
      <h2>Languages</h2>
      <p class="text-muted text-sm mb-12">Select target languages for automatic translation. Requires an LLM backend. <em>(Optional)</em></p>
      <div class="wiz-lang-grid">
        ${langs.map(l => `
          <label class="checkbox-row">
            <input type="checkbox" name="wiz-lang" value="${l.code}"
              ${selected.includes(l.code) ? 'checked' : ''}> ${l.name} (${l.code})
          </label>`).join('')}
      </div>
      <div id="wiz-error" class="alert alert-error" style="display:none;margin-top:12px"></div>`;
  } catch (ex) {
    ct.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

async function _wizStep_endpoints(ct) {
  ct.innerHTML = '<div class="loading-block"><span class="spinner"></span> Loading…</div>';
  try {
    const d = await api('GET', '/config/endpoints');
    const cfg = (d && d.config) || {};
    // Check if any endpoint requires a token
    const anyProtected = Object.values(cfg).some(v => v === true || v === 'true');
    ct.innerHTML = `
      <h2>Access Control</h2>
      <p class="text-muted text-sm mb-12">Should API consumers need a token to access your MCP endpoints? <em>(Optional)</em></p>
      <div class="wiz-toggle${anyProtected ? ' on' : ''}" id="wiz-ep-toggle" onclick="_wizToggleEp()" style="margin:24px 0">
        <div class="wiz-toggle-switch"></div>
        <span id="wiz-ep-label">${anyProtected ? 'Tokens required' : 'Open access (no tokens)'}</span>
      </div>
      <input type="hidden" id="wiz-ep-protected" value="${anyProtected ? '1' : '0'}">
      <p class="text-muted text-sm">When enabled, all query/resource endpoints will require a valid API token. You can fine-tune per-endpoint access in Settings later.</p>
      <div id="wiz-error" class="alert alert-error" style="display:none;margin-top:12px"></div>`;
  } catch (ex) {
    ct.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

function _wizToggleEp() {
  const el = document.getElementById('wiz-ep-toggle');
  const input = document.getElementById('wiz-ep-protected');
  const on = el.classList.toggle('on');
  input.value = on ? '1' : '0';
  document.getElementById('wiz-ep-label').textContent =
    on ? 'Tokens required' : 'Open access (no tokens)';
}

async function _wizStep_finish(ct) {
  ct.innerHTML = '<div class="loading-block"><span class="spinner"></span> Loading…</div>';
  try {
    const readiness = await api('GET', '/config/readiness');
    const sections = (readiness && readiness.sections) || {};
    const warnings = (readiness && readiness.warnings) || [];
    const rows = Object.entries(sections).map(([ns, info]) =>
      `<tr><td>${_esc(ns)}</td><td>${info.present
        ? '<span style="color:var(--success)">✓ Configured</span>'
        : '<span style="color:var(--text-muted)">— Not set</span>'}</td>
       <td>${info.key_count} keys</td></tr>`).join('');
    ct.innerHTML = `
      <h2>Setup Complete</h2>
      <p class="text-muted text-sm mb-12">Review your configuration below.</p>
      <table class="wiz-summary-table">
        <thead><tr><th>Section</th><th>Status</th><th>Keys</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${warnings.length ? `<div class="alert alert-error" style="margin-top:12px">
        <strong>Warnings:</strong><ul style="margin:4px 0 0 16px">${warnings.map(w => `<li>${_esc(w)}</li>`).join('')}</ul>
      </div>` : ''}
      <div style="margin-top:20px;text-align:center">
        <button class="btn btn-primary" onclick="_wizRunIngest()" id="wiz-ingest-btn">Run Initial Ingestion</button>
        <p class="text-muted text-sm" style="margin-top:8px">This will scrape all configured sources. You can also do this later from the Jobs tab.</p>
      </div>
      <div id="wiz-ingest-result" style="margin-top:12px"></div>
      <div id="wiz-error" class="alert alert-error" style="display:none;margin-top:12px"></div>`;
  } catch (ex) {
    ct.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
  }
}

async function _wizRunIngest() {
  const btn = document.getElementById('wiz-ingest-btn');
  const result = document.getElementById('wiz-ingest-result');
  btn.disabled = true;
  btn.textContent = 'Starting…';
  try {
    const d = await api('POST', '/scrape', {});
    result.innerHTML = `<div class="alert alert-success">Ingestion job started (ID: ${_esc(d.job_id || '—')}). Check progress in the Jobs tab.</div>`;
    btn.textContent = 'Job Started';
  } catch (ex) {
    result.innerHTML = `<div class="alert alert-error">${_esc(ex.message)}</div>`;
    btn.disabled = false;
    btn.textContent = 'Retry Ingestion';
  }
}

// ── Step save logic ─────────────────────────────────────

async function _wizSaveStep(stepId) {
  switch (stepId) {
    case 'welcome':
      // Auto-seed defaults if not already seeded
      try {
        const ss = await api('GET', '/seed-status');
        if (ss && !ss.all_seeded) await api('POST', '/seed');
      } catch (_) { /* non-fatal */ }
      break;

    case 'server': {
      const base_url = document.getElementById('wiz-base-url').value.trim();
      const port = parseInt(document.getElementById('wiz-port').value) || 8000;
      const host = document.getElementById('wiz-host').value.trim() || '0.0.0.0';
      await api('PUT', '/config/server', { base_url, port, host });
      break;
    }

    case 'llm': {
      const sel = document.querySelector('.wiz-radio-card.selected');
      const backend = sel ? sel.dataset.val : 'none';
      const body = { backend };
      if (backend === 'ollama') {
        body.ollama_url = document.getElementById('wiz-llm-url').value.trim();
        body.ollama_model = document.getElementById('wiz-llm-model').value.trim();
      } else if (backend === 'groq') {
        const key = document.getElementById('wiz-llm-apikey').value.trim();
        if (key) body.groq_api_key = key;
        body.groq_model = document.getElementById('wiz-llm-model').value.trim();
      }
      await api('PUT', '/config/llm', body);
      break;
    }

    case 'identity': {
      const name = document.getElementById('wiz-id-name').value.trim();
      if (!name) throw new Error('Name is required');
      const tagline = document.getElementById('wiz-id-tagline').value.trim();
      const bio = document.getElementById('wiz-id-bio').value.trim();
      const body = { name };
      if (tagline) body.tagline = tagline;
      if (bio) body.bio = bio;
      await api('PUT', '/config/identity', body);
      break;
    }

    case 'source': {
      const nameEl = document.getElementById('wiz-src-name');
      if (!nameEl) break;  // sources already exist, skip
      const name = nameEl.value.trim();
      if (!name) throw new Error('Source name is required');
      const connector = document.getElementById('wiz-src-connector').value;
      const urlEl = document.getElementById('wiz-src-url');
      const url = urlEl ? urlEl.value.trim() : null;
      if (connector !== 'manual' && !url) throw new Error('URL is required');
      const limit = parseInt(document.getElementById('wiz-src-limit').value) || 0;
      const llm_processing = document.getElementById('wiz-src-llm').checked;
      await api('POST', '/sources?name=' + encodeURIComponent(name) + '&section=oeuvre', {
        connector, url, limit, llm_processing, enabled: true,
      });
      break;
    }

    case 'i18n': {
      const checked = [...document.querySelectorAll('input[name="wiz-lang"]:checked')]
        .map(cb => cb.value);
      await api('PUT', '/config/i18n', {
        target_languages: checked.join(','),
      });
      break;
    }

    case 'endpoints': {
      const protect = document.getElementById('wiz-ep-protected').value === '1';
      // Set all query endpoints to the chosen value
      await api('PUT', '/config/endpoints', {
        '/query': protect,
        '/resources': protect,
        '/resource': protect,
        '/context': protect,
        '/prompts': protect,
      });
      break;
    }

    case 'finish':
      // Nothing to save
      break;
  }
}
