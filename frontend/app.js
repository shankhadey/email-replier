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
let lastSeenEventId = 0;
let newEventCount = 0;
let activityCollapsed = localStorage.getItem('activityCollapsed') === 'true';
let currentUser = null;
let setupPollHandle = null;

// ── Init ─────────────────────────────────────────

async function init() {
  const authed = await checkAuth();
  if (!authed) return;
  await loadConfig();
  await Promise.all([loadQueue(), loadSchedulerStatus(), loadEvents()]);
  startPolling();
}

// ── Config ───────────────────────────────────────

async function loadConfig() {
  try {
    const res = await apiFetch(`${API}/api/config`);
    config = await res.json();
    renderAutonomy(config.autonomy_level);
    renderSettingsForm();
  } catch (e) {
    console.error('Config load error', e);
  }
}

async function setAutonomy(level) {
  try {
    const res = await apiFetch(`${API}/api/config`, {
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
    const res = await apiFetch(`${API}/api/queue`);
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
    const res = await apiFetch(`${API}/api/queue/${id}`);
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
    await apiFetch(`${API}/api/queue/${currentItem.id}/draft`, {
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
    await apiFetch(`${API}/api/queue/${id}/action`, {
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
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  set('setting-interval',  config.poll_interval_minutes    || 30);
  set('setting-start',     config.poll_start_hour          ?? 0);
  set('setting-end',       config.poll_end_hour            ?? 23);
  set('setting-threshold', config.low_confidence_threshold ?? 0.70);
  set('setting-lookback',  config.lookback_hours           ?? 72);
}

async function saveSettings() {
  const updates = {
    poll_interval_minutes: parseInt(document.getElementById('setting-interval').value),
    poll_start_hour:       parseInt(document.getElementById('setting-start').value),
    poll_end_hour:         parseInt(document.getElementById('setting-end').value),
    low_confidence_threshold: parseFloat(document.getElementById('setting-threshold').value),
    lookback_hours: parseInt(document.getElementById('setting-lookback').value),
  };

  try {
    const res = await apiFetch(`${API}/api/config`, {
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
    const res = await apiFetch(`${API}/api/scheduler/status`);
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
    const res = await apiFetch(`${API}/api/scheduler/run-now`, { method: 'POST' });
    const data = await res.json();
    toast(`Processed ${data.processed} email(s)`, 'success');
    await loadQueue();
    await loadSchedulerStatus();
    await loadEvents();
  } catch (e) {
    toast('Poll failed', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.51"/></svg> Poll now`;
  }
}

// ── Activity Log ─────────────────────────────────

const EVENT_META = {
  poll_start:      { glyph: '⟳', color: 'var(--text-dimmer)' },
  poll_end:        { glyph: '✓', color: 'var(--green)' },
  classified:      { glyph: '◆', color: 'var(--accent)' },
  drive_fetched:   { glyph: '⊕', color: 'var(--accent)' },
  calendar_checked:{ glyph: '◷', color: 'var(--accent)' },
  drafted:         { glyph: '✦', color: 'var(--yellow)' },
  sent:            { glyph: '↗', color: 'var(--green)' },
  queued:          { glyph: '◈', color: 'var(--yellow)' },
  skipped:         { glyph: '—', color: 'var(--text-dimmer)' },
  user_sent:       { glyph: '↗', color: 'var(--green)' },
  user_drafted:    { glyph: '◑', color: 'var(--yellow)' },
  user_discarded:  { glyph: '✕', color: 'var(--text-dimmer)' },
  error:           { glyph: '✕', color: 'var(--red)' },
};

async function loadEvents() {
  try {
    const res = await apiFetch(`${API}/api/events?limit=30`);
    const events = await res.json();
    if (!events.length) return;

    const maxId = events[0].id; // newest first
    const isFirst = lastSeenEventId === 0;

    if (!isFirst && maxId > lastSeenEventId) {
      const incoming = events.filter(e => e.id > lastSeenEventId);
      if (activityCollapsed) {
        newEventCount += incoming.length;
        updateNewBadge();
      }
    }

    if (!isFirst) {
      renderEvents(events, maxId > lastSeenEventId ? events.filter(e => e.id > lastSeenEventId).map(e => e.id) : []);
    } else {
      renderEvents(events, []);
    }

    lastSeenEventId = maxId;
  } catch (e) {
    // silent — activity log is non-critical
  }
}

function renderEvents(events, newIds) {
  const container = document.getElementById('activity-entries');
  if (!events.length) {
    container.innerHTML = '<div class="activity-empty">// no activity yet</div>';
    return;
  }

  container.innerHTML = events.map(ev => {
    const meta = EVENT_META[ev.event_type] || { glyph: '·', color: 'var(--text-dimmer)' };
    const isNew = newIds.includes(ev.id);
    return `
      <div class="activity-entry${isNew ? ' new' : ''}">
        <span class="activity-time">${formatTime(ev.created_at)}</span>
        <span class="activity-glyph" style="color:${meta.color}">${meta.glyph}</span>
        <span class="activity-message">${esc(ev.message)}</span>
      </div>
    `;
  }).join('');
}

function updateNewBadge() {
  const badge = document.getElementById('activity-new-badge');
  if (newEventCount > 0) {
    badge.textContent = `+${newEventCount}`;
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

function toggleActivityLog() {
  activityCollapsed = !activityCollapsed;
  localStorage.setItem('activityCollapsed', activityCollapsed);
  applyActivityCollapsed();
  if (!activityCollapsed) {
    newEventCount = 0;
    updateNewBadge();
  }
}

function applyActivityCollapsed() {
  const log     = document.getElementById('activity-log');
  const entries = document.getElementById('activity-entries');
  const btn     = document.getElementById('btn-activity-toggle');
  if (log)     log.classList.toggle('collapsed', activityCollapsed);
  if (entries) entries.classList.toggle('hidden', activityCollapsed);
  if (btn)     btn.textContent = activityCollapsed ? 'SHOW' : 'HIDE';
}

// ── Auto Refresh ─────────────────────────────────

function startPolling() {
  pollHandle = setInterval(async () => {
    await loadQueue();
    await loadSchedulerStatus();
    await loadEvents();
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

document.addEventListener('DOMContentLoaded', () => {
  // Wire up buttons synchronously — never blocked by async failures in init()
  document.getElementById('btn-run-now').addEventListener('click', runNow);
  document.getElementById('btn-settings').addEventListener('click', openSettings);
  applyActivityCollapsed();
  init();
});

// ── API fetch wrapper — handles 401 globally ──────

async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    showAuthWall();
    throw new Error('Not authenticated');
  }
  return res;
}

// ── Auth Check ───────────────────────────────────

async function checkAuth() {
  try {
    const res = await fetch(`${API}/api/me`);
    if (res.status === 401 || res.status === 404) {
      showAuthWall();
      return false;
    }
    currentUser = await res.json();
    hideAuthWall();
    updateUserInfo();
    if (currentUser.setup_status === 'pending') {
      showSetupBanner();
      startSetupPoll();
    } else if (currentUser.setup_status === 'complete') {
      showProfileBtn();
    }
    return true;
  } catch (e) {
    showAuthWall();
    return false;
  }
}

function updateUserInfo() {
  const emailEl = document.getElementById('user-email');
  const logoutBtn = document.getElementById('btn-logout');
  if (emailEl && currentUser) emailEl.textContent = currentUser.email || '';
  if (logoutBtn) logoutBtn.style.display = '';
}

async function logout() {
  try {
    await fetch(`${API}/auth/logout`, { method: 'POST' });
  } catch (e) { /* ignore */ }
  currentUser = null;
  if (pollHandle) clearInterval(pollHandle);
  if (setupPollHandle) clearInterval(setupPollHandle);
  showAuthWall();
}

function showSetupBanner() {
  const banner = document.getElementById('setup-banner');
  if (banner) banner.classList.remove('hidden');
}

function hideSetupBanner() {
  const banner = document.getElementById('setup-banner');
  if (banner) banner.classList.add('hidden');
}

function startSetupPoll() {
  if (setupPollHandle) clearInterval(setupPollHandle);
  setupPollHandle = setInterval(async () => {
    try {
      const res = await fetch(`${API}/api/me`);
      if (!res.ok) return;
      const user = await res.json();
      if (user.setup_status === 'complete') {
        clearInterval(setupPollHandle);
        setupPollHandle = null;
        hideSetupBanner();
        showProfileBtn();
        currentUser = user;
      }
    } catch (e) { /* ignore */ }
  }, 5000);
}

function showAuthWall() {
  document.getElementById('auth-wall').classList.remove('hidden');
  document.getElementById('main-content').classList.add('hidden');
  const emailEl = document.getElementById('user-email');
  const logoutBtn = document.getElementById('btn-logout');
  if (emailEl) emailEl.textContent = '';
  if (logoutBtn) logoutBtn.style.display = 'none';
}

function hideAuthWall() {
  document.getElementById('auth-wall').classList.add('hidden');
  document.getElementById('main-content').classList.remove('hidden');
}

// ── Profile modal ─────────────────────────────

let profileData = null;
let contactsData = [];

function showProfileBtn() {
  const btn = document.getElementById('btn-profile');
  if (btn) btn.classList.remove('hidden');
}

async function openProfile() {
  try {
    const [profileRes, contactsRes] = await Promise.all([
      apiFetch(`${API}/api/profile`),
      apiFetch(`${API}/api/contacts`),
    ]);
    profileData = await profileRes.json();
    contactsData = await contactsRes.json();
    renderWritingStyle(profileData);
    renderContacts(contactsData);
    switchProfileTab('style');
    document.getElementById('profile-overlay').classList.remove('hidden');
  } catch (e) {
    toast('Failed to load profile', 'error');
  }
}

function closeProfile(e) {
  if (e && e.target !== document.getElementById('profile-overlay')) return;
  document.getElementById('profile-overlay').classList.add('hidden');
}

function switchProfileTab(tab) {
  document.querySelectorAll('.profile-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  document.getElementById('profile-tab-style').classList.toggle('hidden', tab !== 'style');
  document.getElementById('profile-tab-contacts').classList.toggle('hidden', tab !== 'contacts');
}

function renderWritingStyle(profile) {
  const voice = (profile && profile.voice_profile) || {};
  document.getElementById('profile-traits').value = (voice.traits || []).join('\n');
  document.getElementById('profile-examples').value = (voice.examples || []).join('\n');
}

async function saveWritingStyle() {
  const traits = document.getElementById('profile-traits').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const examples = document.getElementById('profile-examples').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const updatedVoice = Object.assign({}, (profileData && profileData.voice_profile) || {}, { traits, examples });
  try {
    await apiFetch(`${API}/api/profile`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_profile: updatedVoice }),
    });
    if (profileData) profileData.voice_profile = updatedVoice;
    toast('Writing style saved', 'success');
  } catch (e) {
    toast('Failed to save writing style', 'error');
  }
}

// ── Contacts ──────────────────────────────────

function esc(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderContacts(contacts) {
  contactsData = contacts;
  const tbody = document.querySelector('#contacts-table tbody');
  tbody.innerHTML = '';
  contacts.forEach((c, i) => {
    tbody.appendChild(buildContactRow(c, i));
  });
}

function buildContactRow(c, i) {
  const tr = document.createElement('tr');
  tr.dataset.idx = i;
  tr.innerHTML = `
    <td>${esc(c.name)}</td>
    <td class="contact-email-cell">${esc(c.email)}</td>
    <td>${esc(c.relationship_type)}</td>
    <td>${esc(c.formality_level)}</td>
    <td>${c.interaction_count || 0}</td>
    <td class="contact-actions">
      <button class="btn-micro" data-action="edit" data-idx="${i}">EDIT</button>
      <button class="btn-micro btn-danger-micro" data-action="delete" data-idx="${i}">×</button>
    </td>`;
  return tr;
}

document.addEventListener('click', e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const idx = parseInt(btn.dataset.idx);
  const c = contactsData[idx];
  if (!c) return;
  if (btn.dataset.action === 'edit') startEditContact(c, idx);
  if (btn.dataset.action === 'delete') confirmDeleteContact(c.email, idx);
  if (btn.dataset.action === 'save-edit') commitEditContact(c, idx);
  if (btn.dataset.action === 'cancel-edit') cancelEditContact(c, idx);
});

function startEditContact(c, idx) {
  const tr = document.querySelector(`#contacts-table tr[data-idx="${idx}"]`);
  if (!tr) return;
  tr.innerHTML = `
    <td><input class="profile-input-sm" id="ce-name-${idx}" value="${esc(c.name)}"></td>
    <td class="contact-email-cell">${esc(c.email)}</td>
    <td><input class="profile-input-sm" id="ce-rel-${idx}" value="${esc(c.relationship_type)}"></td>
    <td><input class="profile-input-sm" id="ce-formal-${idx}" value="${esc(c.formality_level)}"></td>
    <td>${c.interaction_count || 0}</td>
    <td class="contact-actions">
      <button class="btn-micro btn-primary-micro" data-action="save-edit" data-idx="${idx}">SAVE</button>
      <button class="btn-micro" data-action="cancel-edit" data-idx="${idx}">CANCEL</button>
    </td>`;
}

function cancelEditContact(c, idx) {
  const tr = document.querySelector(`#contacts-table tr[data-idx="${idx}"]`);
  if (!tr) return;
  tr.replaceWith(buildContactRow(c, idx));
}

async function commitEditContact(c, idx) {
  const name = document.getElementById(`ce-name-${idx}`).value.trim();
  const rel = document.getElementById(`ce-rel-${idx}`).value.trim();
  const formal = document.getElementById(`ce-formal-${idx}`).value.trim();
  try {
    await apiFetch(`${API}/api/contacts/${encodeURIComponent(c.email)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name || null, relationship_type: rel || null, formality_level: formal || null }),
    });
    const updated = Object.assign({}, c, { name, relationship_type: rel, formality_level: formal });
    contactsData[idx] = updated;
    const tr = document.querySelector(`#contacts-table tr[data-idx="${idx}"]`);
    if (tr) tr.replaceWith(buildContactRow(updated, idx));
    toast('Contact updated', 'success');
  } catch (e) {
    toast('Failed to update contact', 'error');
  }
}

async function confirmDeleteContact(email, idx) {
  if (!confirm(`Delete contact ${email}?`)) return;
  try {
    await apiFetch(`${API}/api/contacts/${encodeURIComponent(email)}`, { method: 'DELETE' });
    contactsData.splice(idx, 1);
    renderContacts(contactsData);
    toast('Contact deleted', 'success');
  } catch (e) {
    toast('Failed to delete contact', 'error');
  }
}

async function addNewContact() {
  const name = document.getElementById('new-contact-name').value.trim();
  const email = document.getElementById('new-contact-email').value.trim();
  const rel = document.getElementById('new-contact-rel').value.trim();
  const formal = document.getElementById('new-contact-formal').value.trim();
  if (!email) { toast('Email is required', 'error'); return; }
  try {
    await apiFetch(`${API}/api/contacts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, name: name || null, relationship_type: rel || null, formality_level: formal || null }),
    });
    contactsData.push({ email, name, relationship_type: rel, formality_level: formal, interaction_count: 0 });
    renderContacts(contactsData);
    ['new-contact-name','new-contact-email','new-contact-rel','new-contact-formal'].forEach(id => {
      document.getElementById(id).value = '';
    });
    toast('Contact added', 'success');
  } catch (e) {
    toast('Failed to add contact', 'error');
  }
}
