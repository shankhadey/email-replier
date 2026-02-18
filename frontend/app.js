/* ═══════════════════════════════════════════════
   INBOX PILOT  —  Frontend App
═══════════════════════════════════════════════ */

const API = '';
let currentFilter = 'pending';
let currentItem = null;
let allEmails = [];
let config = {};
let editMode = false;
let pollHandle = null;

// ── Init ─────────────────────────────────────────

async function init() {
  await loadConfig();
  await loadQueue();
  await loadSchedulerStatus();
  startPolling();

  document.getElementById('btn-run-now').addEventListener('click', runNow);
  document.getElementById('btn-settings').addEventListener('click', openSettings);
}

// ── Config ───────────────────────────────────────

async function loadConfig() {
  try {
    const res = await fetch(`${API}/api/config`);
    config = await res.json();
    renderAutonomy(config.autonomy_level);
    renderSettingsForm();
  } catch (e) {
    console.error('Config load error', e);
  }
}

async function setAutonomy(level) {
  try {
    const res = await fetch(`${API}/api/config`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ autonomy_level: level }),
    });
    config = await res.json();
    renderAutonomy(level);
    toast(`Autonomy set to level ${level}`, 'success');
  } catch (e) {
    toast('Failed to update autonomy', 'error');
  }
}

function renderAutonomy(level) {
  const ticks = document.querySelectorAll('.slider-tick');
  ticks.forEach(t => {
    t.classList.toggle('active', parseInt(t.dataset.level) === level);
  });
  const descs = {
    1: 'All emails reviewed before sending',
    2: 'Auto-send confident, known, non-critical emails',
    3: 'Full autopilot — review queue for attachments only',
  };
  document.getElementById('autonomy-description').textContent = descs[level] || '';
}

// ── Queue ────────────────────────────────────────

async function loadQueue() {
  try {
    const res = await fetch(`${API}/api/queue`);
    allEmails = await res.json();
    renderQueue();
    renderStats();
  } catch (e) {
    console.error('Queue load error', e);
  }
}

function setFilter(filter) {
  currentFilter = filter;
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.filter === filter);
  });
  renderQueue();
}

function renderQueue() {
  const list = document.getElementById('queue-list');
  const empty = document.getElementById('empty-state');

  let filtered = allEmails;
  if (currentFilter === 'pending') filtered = allEmails.filter(e => e.status === 'pending');
  else if (currentFilter === 'sent') filtered = allEmails.filter(e => e.status === 'sent');
  else if (currentFilter === 'drafted') filtered = allEmails.filter(e => e.status === 'drafted');

  if (filtered.length === 0) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }

  empty.classList.add('hidden');
  list.innerHTML = filtered.map(e => renderCard(e)).join('');
}

function renderCard(email) {
  const cls = email.classification || {};
  const priority = cls.sender_priority || 'unknown';
  const confidence = typeof cls.confidence === 'number' ? cls.confidence : 0;
  const isCritical = cls.is_critical;
  const pct = Math.round(confidence * 100);
  const confidenceColor = confidence >= 0.8 ? 'var(--green)' : confidence >= 0.6 ? 'var(--yellow)' : 'var(--red)';

  const senderName = extractSenderName(email.sender);

  const badges = [];
  if (email.status === 'pending') badges.push(`<span class="badge badge-pending">REVIEW</span>`);
  if (email.status === 'sent') badges.push(`<span class="badge badge-sent">SENT</span>`);
  if (email.status === 'drafted') badges.push(`<span class="badge badge-drafted">DRAFTED</span>`);
  if (email.status === 'discarded') badges.push(`<span class="badge badge-discarded">DISCARDED</span>`);
  if (isCritical) badges.push(`<span class="badge badge-critical">CRITICAL</span>`);
  if (priority === 'high') badges.push(`<span class="badge badge-high">HIGH PRI</span>`);
  if (priority === 'unknown') badges.push(`<span class="badge badge-unknown">UNKNOWN</span>`);

  return `
    <div class="email-card status-${email.status}" onclick="openModal(${email.id})">
      <div class="card-main">
        <div class="card-top">
          <span class="card-sender">${esc(senderName)}</span>
          <span class="card-subject">${esc(email.subject)}</span>
        </div>
        <div class="card-snippet">${esc(email.snippet || '')}</div>
        <div class="confidence-bar-wrap" style="margin-top:8px">
          ${badges.join('')}
        </div>
      </div>
      <div class="card-meta">
        <div class="confidence-bar-wrap">
          <div class="confidence-bar-bg">
            <div class="confidence-bar-fill" style="width:${pct}%;background:${confidenceColor}"></div>
          </div>
          <span class="confidence-label">${pct}%</span>
        </div>
        <span class="mono" style="font-size:9px;color:var(--text-dimmer)">${formatTime(email.created_at)}</span>
      </div>
    </div>
  `;
}

function renderStats() {
  const pending  = allEmails.filter(e => e.status === 'pending').length;
  const sent     = allEmails.filter(e => e.status === 'sent').length;
  const drafted  = allEmails.filter(e => e.status === 'drafted').length;
  const skipped  = allEmails.filter(e => e.status === 'discarded').length;

  document.getElementById('stat-pending').textContent  = pending;
  document.getElementById('stat-sent').textContent     = sent;
  document.getElementById('stat-drafted').textContent  = drafted;
  document.getElementById('stat-skipped').textContent  = skipped;
}

// ── Modal ────────────────────────────────────────

async function openModal(id) {
  try {
    const res = await fetch(`${API}/api/queue/${id}`);
    currentItem = await res.json();
    renderModal(currentItem);
    editMode = false;
    document.getElementById('modal-overlay').classList.remove('hidden');
  } catch (e) {
    toast('Failed to load email', 'error');
  }
}

function renderModal(item) {
  const cls = item.classification || {};

  document.getElementById('modal-sender').textContent = extractSenderName(item.sender);
  document.getElementById('modal-subject').textContent = item.subject;
  document.getElementById('modal-original').textContent = item.body || '';

  document.getElementById('modal-draft-view').textContent = item.draft_reply || '';
  document.getElementById('modal-draft-edit').value = item.draft_reply || '';
  document.getElementById('modal-draft-view').classList.remove('hidden');
  document.getElementById('modal-draft-edit').classList.add('hidden');
  document.getElementById('btn-save-draft').classList.add('hidden');

  // Classification grid
  const classFields = {
    'SENDER': cls.sender_priority || '-',
    'CONFIDENCE': cls.confidence !== undefined ? `${Math.round(cls.confidence * 100)}%` : '-',
    'CRITICAL': cls.is_critical !== undefined ? String(cls.is_critical) : '-',
    'CALENDAR': cls.needs_calendar !== undefined ? String(cls.needs_calendar) : '-',
    'GDRIVE': cls.needs_gdrive !== undefined ? String(cls.needs_gdrive) : '-',
    'REASONING': cls.reasoning || '-',
  };

  const grid = document.getElementById('modal-classification');
  grid.innerHTML = Object.entries(classFields).map(([k, v]) => `
    <div class="class-item">
      <div class="class-key">${k}</div>
      <div class="class-val ${v.toLowerCase()}">${esc(String(v))}</div>
    </div>
  `).join('');

  // Footer
  const routingReason = cls.routing_reason || '';
  document.getElementById('modal-routing-reason').textContent = routingReason ? `// ${routingReason}` : '';

  const actionsEl = document.getElementById('modal-actions');
  if (item.status === 'pending') {
    actionsEl.innerHTML = `
      <button class="btn-discard" onclick="takeAction(${item.id}, 'discard')">Discard</button>
      <button class="btn-to-draft" onclick="takeAction(${item.id}, 'draft')">Save to Drafts</button>
      <button class="btn-send" onclick="takeAction(${item.id}, 'send')">Send</button>
    `;
  } else {
    actionsEl.innerHTML = `<span class="mono" style="font-size:11px;color:var(--text-dimmer)">// ${item.action_taken || item.status}</span>`;
  }
}

function closeModal(event) {
  if (event && event.target !== document.getElementById('modal-overlay')) return;
  document.getElementById('modal-overlay').classList.add('hidden');
  currentItem = null;
}

function toggleEdit() {
  editMode = !editMode;
  document.getElementById('modal-draft-view').classList.toggle('hidden', editMode);
  document.getElementById('modal-draft-edit').classList.toggle('hidden', !editMode);
  document.getElementById('btn-save-draft').classList.toggle('hidden', !editMode);
}

async function saveDraft() {
  if (!currentItem) return;
  const newDraft = document.getElementById('modal-draft-edit').value;
  try {
    await fetch(`${API}/api/queue/${currentItem.id}/draft`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft_reply: newDraft }),
    });
    currentItem.draft_reply = newDraft;
    document.getElementById('modal-draft-view').textContent = newDraft;
    toggleEdit();
    toast('Draft saved', 'success');
  } catch (e) {
    toast('Failed to save draft', 'error');
  }
}

async function takeAction(id, action) {
  try {
    await fetch(`${API}/api/queue/${id}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    const msgs = { send: 'Email sent', draft: 'Saved to Gmail drafts', discard: 'Discarded' };
    toast(msgs[action] || 'Done', 'success');
    document.getElementById('modal-overlay').classList.add('hidden');
    await loadQueue();
  } catch (e) {
    toast(`Failed: ${e.message}`, 'error');
  }
}

// ── Settings ─────────────────────────────────────

function openSettings() {
  renderSettingsForm();
  document.getElementById('settings-overlay').classList.remove('hidden');
}

function closeSettings(event) {
  if (event && event.target !== document.getElementById('settings-overlay')) return;
  document.getElementById('settings-overlay').classList.add('hidden');
}

function renderSettingsForm() {
  document.getElementById('setting-interval').value = config.poll_interval_minutes || 30;
  document.getElementById('setting-start').value = config.poll_start_hour ?? 7;
  document.getElementById('setting-end').value = config.poll_end_hour ?? 20;
  document.getElementById('setting-threshold').value = config.low_confidence_threshold ?? 0.70;
}

async function saveSettings() {
  const updates = {
    poll_interval_minutes: parseInt(document.getElementById('setting-interval').value),
    poll_start_hour:       parseInt(document.getElementById('setting-start').value),
    poll_end_hour:         parseInt(document.getElementById('setting-end').value),
    low_confidence_threshold: parseFloat(document.getElementById('setting-threshold').value),
  };

  try {
    const res = await fetch(`${API}/api/config`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    config = await res.json();
    document.getElementById('settings-overlay').classList.add('hidden');
    toast('Settings saved', 'success');
  } catch (e) {
    toast('Failed to save settings', 'error');
  }
}

// ── Scheduler ────────────────────────────────────

async function loadSchedulerStatus() {
  try {
    const res = await fetch(`${API}/api/scheduler/status`);
    const status = await res.json();
    updateStatusIndicator(status.running);
    if (status.last_run) {
      document.getElementById('last-poll-time').textContent =
        `Last poll: ${formatTime(status.last_run)}`;
    }
  } catch (e) {
    updateStatusIndicator(false);
  }
}

function updateStatusIndicator(running) {
  const dot = document.getElementById('status-dot');
  const label = document.getElementById('status-label');
  dot.className = 'status-dot ' + (running ? 'online' : 'offline');
  label.textContent = running ? 'scheduler active' : 'scheduler offline';
}

async function runNow() {
  const btn = document.getElementById('btn-run-now');
  btn.disabled = true;
  btn.textContent = 'Polling...';
  try {
    const res = await fetch(`${API}/api/scheduler/run-now`, { method: 'POST' });
    const data = await res.json();
    toast(`Processed ${data.processed} email(s)`, 'success');
    await loadQueue();
    await loadSchedulerStatus();
  } catch (e) {
    toast('Poll failed', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.51"/></svg> Poll now`;
  }
}

// ── Auto Refresh ─────────────────────────────────

function startPolling() {
  pollHandle = setInterval(async () => {
    await loadQueue();
    await loadSchedulerStatus();
  }, 15000); // refresh UI every 15s
}

// ── Helpers ──────────────────────────────────────

function extractSenderName(sender) {
  if (!sender) return 'Unknown';
  const match = sender.match(/^([^<]+)</);
  return match ? match[1].trim() : sender.replace(/<.*>/, '').trim() || sender;
}

function formatTime(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr + (isoStr.endsWith('Z') ? '' : 'Z'));
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffMin < 1440) return `${Math.floor(diffMin / 60)}h ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

function esc(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

let toastTimer;
function toast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.classList.remove('show'); }, 3000);
}

// ── Keyboard ─────────────────────────────────────

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.getElementById('modal-overlay').classList.add('hidden');
    document.getElementById('settings-overlay').classList.add('hidden');
  }
});

// ── Start ─────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);
