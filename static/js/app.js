import {
  appState, loadState, saveState, setStatus, escapeHtml, formatPrice, normalizeCountryName, getFullCountryName,
  createRetailerPill, getProductMeta, normalizeRetailerId, normalizeProductId,
} from './utils.js';
import {
  loadMeta, createJob, pollJob, loadHistory as loadHistoryApi, mapHistoryResponse, mapScrapeResult, ocrScan, loadLatestFetch,
  getCurrentUser, logout, updateOwnProfile, listUsers, createUser, updateUserRole, setUserActive,
  resetUserPassword, deleteUser, getUserStats, getDbStatus,
} from './api.js';
import { renderProductTags, renderProductSelect, renderPresets, addProduct, resetProducts, removeProduct } from './products.js';
import { renderVariationCharts } from './charts.js';

let currentUser = null; // { id, username, role } — set once during init()

const fetchState = { selectedRetailer: 'all', selectedProduct: 'all' };
let lastPivot = null;   // { dates, rows, counts }
let lastDetail = [];    // flat rows from the most recent fetch(es), merged per product/retailer

// Local YYYY-MM-DD, used to expire the persisted "Latest Fetch Detail" table once the day rolls over.
function todayStr() {
  return new Date().toLocaleDateString('en-CA'); // en-CA formats as YYYY-MM-DD
}

// Detail-table persistence lives directly in localStorage (its own key), rather than
// going through appState/saveState — those only round-trip a fixed set of known fields,
// so a custom field like this one would silently come back empty on every reload.
const DETAIL_STORAGE_KEY = 'marketpulse_detail_cache_v1';

function loadPersistedDetail() {
  try {
    const raw = localStorage.getItem(DETAIL_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return (parsed && Array.isArray(parsed.rows)) ? parsed : null;
  } catch (error) {
    console.warn('[MarketPulse] Could not read persisted detail cache:', error);
    return null;
  }
}

function savePersistedDetail(rows) {
  try {
    localStorage.setItem(DETAIL_STORAGE_KEY, JSON.stringify({ date: todayStr(), rows }));
  } catch (error) {
    console.warn('[MarketPulse] Could not save detail cache:', error);
  }
}

/* ============================== TABS ============================== */

function bindTabs() {
  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach((tab) => tab.classList.toggle('active', tab === button));
      document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.remove('active'));
      const id = button.getAttribute('data-tab');
      document.getElementById(`tab-${id}`)?.classList.add('active');
      if (id === 'variation') { renderVariationTab(); renderAnalyticsTab(); }
      if (id === 'scheduler') renderSchedulerUI();
    });
  });
}

/* ============================== AUTH: topbar + role gating ============================== */

// Roles below "editor" (i.e. viewer) can look at everything but can't
// trigger scrapes or OCR scans - those buttons are disabled rather than
// hidden, so it's clear the feature exists but requires a higher role.
function applyRoleGating(user) {
  const isViewer = user.role === 'viewer';
  const isAdmin = user.role === 'admin';

  const userMgmtItem = document.getElementById('menuUserManagement');
  const dbSettingsItem = document.getElementById('menuDbSettings');
  const adminSectionLabel = document.getElementById('menuAdminSectionLabel');
  if (userMgmtItem) userMgmtItem.style.display = isAdmin ? '' : 'none';
  if (dbSettingsItem) dbSettingsItem.style.display = isAdmin ? '' : 'none';
  if (adminSectionLabel) adminSectionLabel.style.display = isAdmin ? '' : 'none';

  ['fetchBtn', 'syncBtn'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.disabled = isViewer;
    el.title = isViewer ? 'Your account is view-only — ask an admin for Editor access to fetch prices.' : '';
    el.classList.toggle('disabled-viewer', isViewer);
  });

  const ocrButton = document.getElementById('btnOCR');
  if (ocrButton) {
    ocrButton.disabled = isViewer;
    ocrButton.title = isViewer ? 'Your account is view-only — ask an admin for Editor access to run OCR scans.' : '';
  }
}

function renderUserBadge(user) {
  const menuBtn = document.getElementById('userMenuBtn');
  const nameEl = document.getElementById('userBadgeName');
  const avatarEl = document.getElementById('userAvatar');
  const avatarLgEl = document.getElementById('userAvatarLg');
  const headerNameEl = document.getElementById('userMenuName');
  const roleDotEl = document.getElementById('userMenuRoleDot');
  const roleTextEl = document.getElementById('userMenuRoleText');
  const metaEl = document.getElementById('userMenuMeta');
  if (!menuBtn) return;

  nameEl.textContent = user.username;
  if (headerNameEl) headerNameEl.textContent = user.username;
  if (roleDotEl) roleDotEl.className = `role-dot role-dot-${user.role}`;
  if (roleTextEl) roleTextEl.textContent = `${roleLabel(user.role)} access`;

  const initials = (user.username || '?').trim().slice(0, 2).toUpperCase();
  [avatarEl, avatarLgEl].forEach((el) => {
    if (!el) return;
    if (user.avatar_url) {
      el.textContent = '';
      el.style.backgroundImage = `url("${user.avatar_url}")`;
      el.classList.add('has-image');
    } else {
      el.textContent = initials;
      el.style.backgroundImage = '';
      el.classList.remove('has-image');
    }
  });

  if (metaEl) {
    const parts = [];
    if (user.auth_provider === 'google') parts.push('Signed in with Google');
    if (user.created_at) parts.push(`Member since ${formatJoinedDate(user.created_at)}`);
    metaEl.textContent = parts.join(' · ');
  }

  menuBtn.style.display = '';
}

/* ---- user menu dropdown + the two modals it opens ---- */

function toggleUserMenu() {
  const panel = document.getElementById('userMenuPanel');
  const btn = document.getElementById('userMenuBtn');
  const isOpen = panel.classList.contains('open');
  closeAllDropdowns();
  if (!isOpen) { panel.classList.add('open'); btn.classList.add('open'); }
}

function openModal(id) {
  document.getElementById(id)?.classList.add('open');
}

function closeModal(id) {
  document.getElementById(id)?.classList.remove('open');
}

function collapseAddUserPanel() {
  const panel = document.getElementById('addUserPanel');
  const toggle = document.getElementById('toggleAddUser');
  panel?.classList.remove('open');
  if (toggle) {
    toggle.setAttribute('aria-expanded', 'false');
    toggle.innerHTML = '<span class="add-user-toggle-icon">+</span> Add User';
  }
}

function bindUserMenu() {
  document.getElementById('userMenuBtn')?.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleUserMenu();
  });

  document.getElementById('menuUserManagement')?.addEventListener('click', () => {
    closeAllDropdowns();
    const status = document.getElementById('adminStatus');
    if (status) status.style.display = 'none';
    collapseAddUserPanel();

    userFilterState.search = '';
    userFilterState.role = 'all';
    userFilterState.status = 'all';
    const searchInput = document.getElementById('userSearchInput');
    const roleFilter = document.getElementById('userRoleFilter');
    const statusFilter = document.getElementById('userStatusFilter');
    if (searchInput) searchInput.value = '';
    if (roleFilter) roleFilter.value = 'all';
    if (statusFilter) statusFilter.value = 'all';

    openModal('userMgmtOverlay');
    refreshUsersList();
  });

  document.getElementById('toggleAddUser')?.addEventListener('click', () => {
    const panel = document.getElementById('addUserPanel');
    const toggle = document.getElementById('toggleAddUser');
    if (!panel || !toggle) return;
    const willOpen = !panel.classList.contains('open');
    panel.classList.toggle('open', willOpen);
    toggle.setAttribute('aria-expanded', String(willOpen));
    toggle.innerHTML = willOpen
      ? '<span class="add-user-toggle-icon">+</span> Cancel'
      : '<span class="add-user-toggle-icon">+</span> Add User';
    if (willOpen) document.getElementById('newUserName')?.focus();
  });

  document.getElementById('menuChangePassword')?.addEventListener('click', () => {
    closeAllDropdowns();
    resetChangePasswordForm();
    openModal('changePwOverlay');
  });

  document.getElementById('menuDbSettings')?.addEventListener('click', () => {
    closeAllDropdowns();
    openModal('dbSettingsOverlay');
    refreshDbStatus();
  });

  document.getElementById('btnRefreshDbStatus')?.addEventListener('click', () => refreshDbStatus());

  document.getElementById('closeDbSettings')?.addEventListener('click', () => closeModal('dbSettingsOverlay'));
  document.getElementById('dbSettingsOverlay')?.addEventListener('click', (event) => {
    if (event.target.id === 'dbSettingsOverlay') closeModal('dbSettingsOverlay');
  });

  document.getElementById('cpUsername')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') handleChangePassword();
  });

  document.getElementById('menuSignOut')?.addEventListener('click', async () => {
    closeAllDropdowns();
    try {
      await logout();
    } finally {
      window.location.href = '/login';
    }
  });

  document.getElementById('closeUserMgmt')?.addEventListener('click', () => closeModal('userMgmtOverlay'));
  document.getElementById('closeChangePw')?.addEventListener('click', () => closeModal('changePwOverlay'));

  // click on the dimmed backdrop (not the card itself) closes the modal
  document.getElementById('userMgmtOverlay')?.addEventListener('click', (event) => {
    if (event.target.id === 'userMgmtOverlay') closeModal('userMgmtOverlay');
  });
  document.getElementById('changePwOverlay')?.addEventListener('click', (event) => {
    if (event.target.id === 'changePwOverlay') closeModal('changePwOverlay');
  });

  document.getElementById('btnAddUser')?.addEventListener('click', handleAddUser);
  document.getElementById('cpSubmit')?.addEventListener('click', handleChangePassword);

  bindUserFilters();
}

/* ---- User Management modal: list, add, edit, revoke ---- */

function roleLabel(role) {
  return role === 'admin' ? 'Admin' : role === 'editor' ? 'Editor' : 'Viewer';
}

function formatJoinedDate(createdAt) {
  const datePart = (createdAt || '').slice(0, 10); // "YYYY-MM-DD"
  const match = datePart.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : '—';
}

// Relative "time ago" for the last-login line, e.g. "3h ago", "5d ago".
// Falls back to the plain date once it's more than a month old.
function formatLastLogin(lastLoginAt) {
  if (!lastLoginAt) return 'Never signed in';
  const then = new Date(lastLoginAt.replace(' ', 'T'));
  if (Number.isNaN(then.getTime())) return 'Never signed in';
  const diffMs = Date.now() - then.getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return 'Active just now';
  if (minutes < 60) return `Active ${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `Active ${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `Active ${days}d ago`;
  return `Last seen ${formatJoinedDate(lastLoginAt)}`;
}

function renderUsersList(users) {
  const wrap = document.getElementById('usersBody');
  if (!wrap) return;
  if (!users.length) {
    wrap.innerHTML = '<div class="empty-state">No accounts yet</div>';
    return;
  }

  wrap.innerHTML = users.map((u) => {
    const isGoogle = u.auth_provider === 'google';
    const initials = (u.username || '?').trim().slice(0, 2).toUpperCase();
    const avatarHtml = u.avatar_url
      ? `<span class="user-card-avatar has-image" style="background-image:url('${escapeHtml(u.avatar_url)}')"></span>`
      : `<span class="user-card-avatar">${escapeHtml(initials)}</span>`;
    const providerBadge = isGoogle
      ? '<span class="provider-badge provider-google">G Google</span>'
      : '<span class="provider-badge">🔒 Password</span>';

    return `
    <div class="user-card ${u.is_active ? '' : 'inactive'}" data-user-id="${u.id}">
      <div class="user-card-top">
        <div class="user-card-id">
          ${avatarHtml}
          <div>
            <div class="user-card-name">${escapeHtml(u.username)} ${providerBadge}</div>
            <div class="user-card-username">${u.is_active ? '@' + escapeHtml(u.username) : 'Deactivated'}</div>
            <div class="user-card-last-login">${escapeHtml(formatLastLogin(u.last_login_at))}</div>
          </div>
        </div>
        <span class="role-chip role-${u.role}">${roleLabel(u.role).toUpperCase()}</span>
      </div>
      <div class="user-card-bottom">
        <div class="user-card-controls">
          <select class="role-select" data-action="role" data-id="${u.id}" ${u.id === currentUser?.id ? 'disabled title="You can\'t change your own role"' : ''}>
            <option value="viewer" ${u.role === 'viewer' ? 'selected' : ''}>viewer</option>
            <option value="editor" ${u.role === 'editor' ? 'selected' : ''}>editor</option>
            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>admin</option>
          </select>
          <button type="button" class="user-card-btn danger" data-action="delete" data-id="${u.id}" ${u.id === currentUser?.id ? 'disabled title="You can\'t delete your own account"' : ''}>Revoke</button>
        </div>
        <div class="user-card-joined">joined ${formatJoinedDate(u.created_at)}</div>
      </div>
      <div class="user-card-secondary">
        <button type="button" class="user-card-link" data-action="reset-password" data-id="${u.id}">${isGoogle ? 'Set Password' : 'Change Password'}</button>
        <button type="button" class="user-card-link" data-action="toggle-active" data-id="${u.id}" data-active="${u.is_active ? '1' : '0'}" ${u.id === currentUser?.id ? 'disabled title="You can\'t deactivate your own account"' : ''}>
          ${u.is_active ? 'Deactivate' : 'Activate'}
        </button>
      </div>
    </div>
  `;
  }).join('');

  wrap.querySelectorAll('[data-action="role"]').forEach((select) => {
    select.addEventListener('change', async (event) => {
      const id = Number(event.target.dataset.id);
      try {
        await updateUserRole(id, event.target.value);
        setStatus('✓ Role updated', 'ok');
        refreshUsersList();
      } catch (error) {
        setStatus(`✗ ${error.message}`, 'err');
        refreshUsersList();
      }
    });
  });

  wrap.querySelectorAll('[data-action="toggle-active"]').forEach((button) => {
    button.addEventListener('click', async (event) => {
      const id = Number(event.currentTarget.dataset.id);
      const nextActive = event.currentTarget.dataset.active !== '1';
      try {
        await setUserActive(id, nextActive);
        setStatus(nextActive ? '✓ Account activated' : '✓ Account deactivated', 'ok');
        refreshUsersList();
      } catch (error) {
        setStatus(`✗ ${error.message}`, 'err');
      }
    });
  });

  wrap.querySelectorAll('[data-action="reset-password"]').forEach((button) => {
    button.addEventListener('click', async (event) => {
      const id = Number(event.currentTarget.dataset.id);
      const newPassword = window.prompt('Enter a new password for this account (min. 6 characters):');
      if (newPassword === null) return;
      try {
        await resetUserPassword(id, newPassword);
        setStatus('✓ Password reset', 'ok');
      } catch (error) {
        setStatus(`✗ ${error.message}`, 'err');
      }
    });
  });

  wrap.querySelectorAll('[data-action="delete"]').forEach((button) => {
    button.addEventListener('click', async (event) => {
      const id = Number(event.currentTarget.dataset.id);
      const card = event.currentTarget.closest('.user-card');
      const username = card?.querySelector('.user-card-name')?.textContent || 'this user';
      if (!window.confirm(`Revoke access for "${username}"? This permanently deletes the account.`)) return;
      try {
        await deleteUser(id);
        setStatus('✓ User deleted', 'ok');
        refreshUsersList();
      } catch (error) {
        setStatus(`✗ ${error.message}`, 'err');
      }
    });
  });
}

const userFilterState = { search: '', role: 'all', status: 'all' };
let userFilterDebounce = null;

function renderUserStats(stats) {
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
  setText('statTotal', stats.total);
  setText('statActive', stats.active);
  setText('statInactive', stats.inactive);
  setText('statAdmins', stats.admins);
  setText('statEditors', stats.editors);
  setText('statViewers', stats.viewers);
}

async function refreshUserStats() {
  try {
    const stats = await getUserStats();
    renderUserStats(stats);
  } catch {
    // Non-critical — the account list itself still loads and works fine
    // without the stats bar, so fail silently here.
  }
}

async function refreshDbStatus() {
  const box = document.getElementById('dbStatusBody');
  const button = document.getElementById('btnRefreshDbStatus');
  if (box) box.innerHTML = '<div class="empty-state">Checking connection…</div>';
  if (button) button.disabled = true;
  try {
    const status = await getDbStatus();
    renderDbStatus(status);
  } catch (error) {
    if (box) box.innerHTML = `<div class="empty-state">✗ ${escapeHtml(error.message)}</div>`;
  } finally {
    if (button) button.disabled = false;
  }
}

function renderDbStatus(status) {
  const box = document.getElementById('dbStatusBody');
  if (!box) return;

  const dotClass = status.connected ? 'status-active' : 'status-inactive';
  const label = status.connected
    ? '● Connected'
    : (status.configured ? '○ Connection failed' : '○ Not configured');

  const stats = status.collections || {};
  const fmt = (value) => (value === null || value === undefined ? '—' : value);

  box.innerHTML = `
    <div class="db-conn-line"><span class="${dotClass}">${label}</span></div>
    ${status.error ? `<div class="status-line error" style="display:block; margin-top:8px;">${escapeHtml(status.error)}</div>` : ''}
    <div class="um-stats-grid" style="margin-top:16px;">
      <div class="um-stat accent-admin">
        <div class="um-stat-value long-value">${escapeHtml(String(fmt(status.db_name)))}</div>
        <div class="um-stat-label">Database</div>
      </div>
      <div class="um-stat accent-editor">
        <div class="um-stat-value">${status.latency_ms != null ? `${status.latency_ms} ms` : '—'}</div>
        <div class="um-stat-label">Ping</div>
      </div>
      <div class="um-stat accent-viewer">
        <div class="um-stat-value">${fmt(stats.users)}</div>
        <div class="um-stat-label">Users</div>
      </div>
      <div class="um-stat accent-admin">
        <div class="um-stat-value">${fmt(stats.price_records)}</div>
        <div class="um-stat-label">Price Records</div>
      </div>
      <div class="um-stat accent-editor">
        <div class="um-stat-value">${fmt(stats.distinct_products)}</div>
        <div class="um-stat-label">Products Tracked</div>
      </div>
      <div class="um-stat accent-inactive">
        <div class="um-stat-value long-value">${escapeHtml(String(fmt(status.host)))}</div>
        <div class="um-stat-label">Host</div>
      </div>
    </div>
  `;
}

async function refreshUsersList() {
  const wrap = document.getElementById('usersBody');
  if (wrap) wrap.innerHTML = '<div class="empty-state">Loading users…</div>';
  refreshUserStats();
  try {
    const users = await listUsers(userFilterState);
    if (!users.length && (userFilterState.search || userFilterState.role !== 'all' || userFilterState.status !== 'all')) {
      if (wrap) wrap.innerHTML = '<div class="um-empty-filtered">No accounts match these filters.</div>';
      return;
    }
    renderUsersList(users);
  } catch (error) {
    if (wrap) wrap.innerHTML = `<div class="empty-state">Unable to load users: ${escapeHtml(error.message)}</div>`;
  }
}

function bindUserFilters() {
  const searchInput = document.getElementById('userSearchInput');
  const roleFilter = document.getElementById('userRoleFilter');
  const statusFilter = document.getElementById('userStatusFilter');

  searchInput?.addEventListener('input', (event) => {
    clearTimeout(userFilterDebounce);
    userFilterDebounce = setTimeout(() => {
      userFilterState.search = event.target.value.trim();
      refreshUsersList();
    }, 250);
  });

  roleFilter?.addEventListener('change', (event) => {
    userFilterState.role = event.target.value;
    refreshUsersList();
  });

  statusFilter?.addEventListener('change', (event) => {
    userFilterState.status = event.target.value;
    refreshUsersList();
  });
}

async function handleAddUser() {
  const nameEl = document.getElementById('newUserName');
  const passEl = document.getElementById('newUserPassword');
  const roleEl = document.getElementById('newUserRole');
  const status = document.getElementById('adminStatus');

  const showStatus = (message, type) => {
    if (!status) return;
    status.style.display = 'block';
    status.className = `status-line ${type === 'err' ? 'error' : 'ok'}`;
    status.textContent = message;
  };

  const username = nameEl.value.trim();
  const password = passEl.value;
  const role = roleEl.value;

  if (!username || !password) {
    showStatus('Enter a username and password.', 'err');
    return;
  }

  try {
    await createUser(username, password, role);
    showStatus(`✓ Created account "${username}"`, 'ok');
    nameEl.value = '';
    passEl.value = '';
    roleEl.value = 'viewer';
    refreshUsersList();
    setTimeout(collapseAddUserPanel, 900);
  } catch (error) {
    showStatus(`✗ ${error.message}`, 'err');
  }
}

/* ---- Account Settings modal (username + password) ---- */

function resetChangePasswordForm() {
  ['cpCurrent', 'cpNew', 'cpConfirm'].forEach((id) => { const el = document.getElementById(id); if (el) el.value = ''; });
  const usernameEl = document.getElementById('cpUsername');
  if (usernameEl) usernameEl.value = currentUser?.username || '';
  const status = document.getElementById('cpStatus');
  if (status) status.style.display = 'none';
}

async function handleChangePassword() {
  const usernameEl = document.getElementById('cpUsername');
  const newUsername = (usernameEl?.value || '').trim();
  const current = document.getElementById('cpCurrent').value;
  const next = document.getElementById('cpNew').value;
  const confirm = document.getElementById('cpConfirm').value;
  const status = document.getElementById('cpStatus');
  const submitBtn = document.getElementById('cpSubmit');

  const showStatus = (message, type) => {
    if (!status) return;
    status.style.display = 'block';
    status.className = `status-line ${type === 'err' ? 'error' : 'ok'}`;
    status.textContent = message;
  };

  const usernameChanged = newUsername && newUsername !== currentUser?.username;
  const wantsPasswordChange = next || confirm;

  if (!current) {
    showStatus('Enter your current password to save changes.', 'err');
    return;
  }
  if (!usernameChanged && !wantsPasswordChange) {
    showStatus('Nothing to update.', 'err');
    return;
  }
  if (usernameChanged && newUsername.length < 1) {
    showStatus('Username can\'t be empty.', 'err');
    return;
  }
  if (wantsPasswordChange) {
    if (next.length < 6) {
      showStatus('New password must be at least 6 characters.', 'err');
      return;
    }
    if (next !== confirm) {
      showStatus('New password and confirmation don\'t match.', 'err');
      return;
    }
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Saving…';
  try {
    const updatedUser = await updateOwnProfile(current, usernameChanged ? newUsername : null, wantsPasswordChange ? next : null);
    if (updatedUser) {
      currentUser = updatedUser;
      renderUserBadge(updatedUser);
    }
    showStatus('✓ Account updated', 'ok');
    setTimeout(() => closeModal('changePwOverlay'), 900);
  } catch (error) {
    showStatus(`✗ ${error.message}`, 'err');
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Save Changes';
  }
}

// Confirms there's a valid session before wiring up the rest of the app.
// The Flask "/" route already redirects unauthenticated requests to
// /login, but this catches the case where a cached page is opened after
// the session has since expired server-side.
async function initAuth() {
  const user = await getCurrentUser().catch(() => null);
  if (!user) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    return null;
  }
  currentUser = user;
  renderUserBadge(user);
  applyRoleGating(user);
  return user;
}

/* ============================== HEADER ============================== */

function setDateChip() {
  const chip = document.getElementById('dateChip');
  if (chip) chip.textContent = new Date().toLocaleDateString('en-US', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}

function updateTicker(flatRows) {
  const track = document.getElementById('tickerTrack');
  if (!track) return;
  if (!flatRows.length) { track.innerHTML = ''; return; }
  const grouped = {};
  flatRows.forEach((row) => {
    if (!row.price) return;
    (grouped[row.product] ||= []).push(row.price);
  });
  const items = Object.entries(grouped).map(([id, prices]) => {
    const meta = getProductMeta(id);
    const avg = (prices.reduce((a, b) => a + b, 0) / prices.length).toFixed(2);
    return `<span class="tick-item"><span class="tick-name">${escapeHtml((meta.name || id).toUpperCase())}</span><span class="tick-price">${avg}</span>AED/kg</span>`;
  });
  track.innerHTML = [...items, ...items].join(''); // duplicate for a seamless scroll if you add a marquee animation
}

/* ==================== PYTHON BACKEND STATUS CARD ==================== */
/* This replaces the old "Anthropic API key" box — there's no key to store,
   we just confirm the Flask backend (running your scraper.py) is reachable. */

async function checkBackend(showToast = false) {
  const pill = document.getElementById('backendPill');
  const label = document.getElementById('backendPillText');
  if (pill && label) { pill.className = 'live-pill checking'; label.textContent = 'checking'; }
  try {
    await loadMeta();
    if (pill && label) { pill.className = 'live-pill'; label.textContent = 'LIVE'; }
    if (showToast) setStatus('✓ Python backend reachable', 'ok');
    return true;
  } catch (error) {
    if (pill && label) { pill.className = 'live-pill err'; label.textContent = 'OFFLINE'; }
    if (showToast) setStatus(`✗ ${error.message}`, 'err');
    return false;
  }
}

/* ============================== DROPDOWNS ============================== */

function buildDropdown({ btnEl, labelEl, panelEl, items, selected, onSelect }) {
  panelEl.innerHTML = '';
  items.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'dd-item' + (item.id === 'all' ? ' all-option' : '') + (item.id === selected ? ' selected' : '');
    row.innerHTML = item.emoji ? `<span>${item.emoji}</span><span>${escapeHtml(item.name)}</span>` : `<span>${escapeHtml(item.name)}</span>`;
    row.addEventListener('click', () => {
      onSelect(item);
      labelEl.textContent = item.name;
      closeAllDropdowns();
    });
    panelEl.appendChild(row);
  });
  btnEl.onclick = (event) => {
    event.stopPropagation();
    const isOpen = panelEl.classList.contains('open');
    closeAllDropdowns();
    if (!isOpen) { panelEl.classList.add('open'); btnEl.classList.add('open'); }
  };
}

function closeAllDropdowns() {
  document.querySelectorAll('.dd-panel').forEach((panel) => panel.classList.remove('open'));
  document.querySelectorAll('.dd-btn').forEach((button) => button.classList.remove('open'));
}

function renderFetchDropdowns() {
  const retailerItems = [{ id: 'all', name: 'All Retailers' }, ...appState.meta.retailers];
  const productItems = [{ id: 'all', name: 'All Configured Products' }, ...appState.meta.products];

  buildDropdown({
    btnEl: document.getElementById('retailerBtn'),
    labelEl: document.getElementById('retailerLabel'),
    panelEl: document.getElementById('retailerPanel'),
    items: retailerItems,
    selected: fetchState.selectedRetailer,
    onSelect: (item) => { fetchState.selectedRetailer = item.id; renderFetchDropdowns(); loadPivot(); },
  });

  buildDropdown({
    btnEl: document.getElementById('productBtn'),
    labelEl: document.getElementById('productLabel'),
    panelEl: document.getElementById('productPanel'),
    items: productItems,
    selected: fetchState.selectedProduct,
    onSelect: (item) => { fetchState.selectedProduct = item.id; renderFetchDropdowns(); loadPivot(); },
  });
}

/* ============================== PIVOT TABLE ============================== */

function formatDateHeader(iso) {
  const date = new Date(`${iso}T00:00:00`);
  return {
    week: date.toLocaleDateString('en-US', { weekday: 'short' }).toUpperCase(),
    day: date.toLocaleDateString('en-US', { day: '2-digit', month: 'short' }).toUpperCase(),
  };
}

function flattenPivot(payload) {
  const rows = [];
  (payload.rows || []).forEach((row) => {
    Object.entries(row.prices || {}).forEach(([date, price]) => {
      const rawOrigin = row.origins?.[date] ?? row.origin_country ?? row.originCountry ?? row.country ?? row.origin ?? '—';
      rows.push({
        date,
        retailer: row.retailer_id || row.retailer_label || 'unknown',
        product: row.product_id || row.product_label || 'unknown',
        price: Number(price) || 0,
        origin_country: normalizeCountryName(rawOrigin),
      });
    });
  });
  return rows;
}

// Walks backwards from colIdx through this row's own price history to find the
// closest earlier date it actually has a price for (dates can be sparse per row).
function findPriorPivotPrice(row, dates, colIdx) {
  for (let i = colIdx - 1; i >= 0; i--) {
    const value = row.prices[dates[i]];
    if (value !== undefined && value !== null) return Number(value);
  }
  return null;
}

function renderPivotTable(data) {
  const headRow = document.getElementById('headRow');
  const bodyRows = document.getElementById('bodyRows');
  const table = document.getElementById('priceTable');
  const emptyState = document.getElementById('emptyState');

  headRow.innerHTML = '';
  bodyRows.innerHTML = '';

  const { dates, rows, counts } = data;

  document.getElementById('summaryLine').innerHTML =
    `<b>${counts.dates}</b> dates <span class="sep">·</span> ` +
    `<b>${counts.products}</b> products <span class="sep">·</span> ` +
    `<b>${counts.retailers}</b> retailers <span class="sep">·</span> AED per kg`;

  if (!rows.length) {
    table.style.display = 'none';
    emptyState.style.display = 'block';
    updateStats(data, []);
    return;
  }

  table.style.display = 'table';
  emptyState.style.display = 'none';

  const productHeader = document.createElement('th');
  productHeader.className = 'row-head';
  productHeader.textContent = 'Product';
  headRow.appendChild(productHeader);

  const retailerHeader = document.createElement('th');
  retailerHeader.className = 'retailer-col';
  retailerHeader.textContent = 'Retailer';
  headRow.appendChild(retailerHeader);

  dates.forEach((iso) => {
    const { week, day } = formatDateHeader(iso);
    const header = document.createElement('th');
    header.className = 'date-col';
    header.innerHTML = `<span class="wk">${week}</span><span class="dt">${day}</span>`;
    headRow.appendChild(header);
  });

  // Group rows by product so the Product cell is shown once (spanning all its
  // retailer rows) instead of being repeated on every row.
  const groups = new Map();
  rows.forEach((row) => {
    const key = row.product_id || row.product_label;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });

  groups.forEach((groupRows) => {
    groupRows.forEach((row, idx) => {
      const tr = document.createElement('tr');

      if (idx === 0) {
        const productCell = document.createElement('td');
        productCell.className = 'row-head';
        productCell.rowSpan = groupRows.length;
        productCell.innerHTML = `<div class="prod-cell"><span class="emoji">${row.product_emoji || '🛒'}</span>${escapeHtml(row.product_label || row.product_id)}</div>`;
        tr.appendChild(productCell);
      }

      const retailerCell = document.createElement('td');
      retailerCell.className = 'retailer-cell';
      retailerCell.innerHTML = createRetailerPill(row.retailer_id || row.retailer_label);
      tr.appendChild(retailerCell);

      dates.forEach((iso, colIdx) => {
        const td = document.createElement('td');
        const value = row.prices[iso];
        // origin can differ day to day, so look it up per-date rather than
        // once per row; falls back to a row-level field for older data shapes.
        const origin = normalizeCountryName(
          row.origins?.[iso] ?? row.origin_country ?? row.originCountry
          ?? row.country ?? row.origin ?? row.country_of_origin ?? row.countryOfOrigin ?? ''
        );
        if (value === undefined || value === null) {
          td.className = 'empty price-cell';
          td.textContent = '—';
        } else {
          td.className = 'price-cell';
          // Only the last (most recent) date column gets an up/down indicator,
          // comparing against the closest earlier date this row has a price for.
          let trendHtml = '';
          let priceNumClass = 'price-num';
          if (colIdx === dates.length - 1) {
            const prior = findPriorPivotPrice(row, dates, colIdx);
            if (prior !== null && prior > 0 && Number(value) > 0) {
              const diff = Number(value) - prior;
              if (Math.abs(diff) >= 0.005) {
                const up = diff > 0;
                const dir = up ? 'up' : 'down';
                td.className = `price-cell trend-${dir}`;
                priceNumClass = `price-num trend-${dir}`;
                trendHtml = `<span class="pivot-trend ${dir}" title="${up ? 'Up' : 'Down'} vs ${formatPrice(prior)} AED previous fetch">${up ? '▲' : '▼'}</span>`;
              }
            }
          }
          td.innerHTML = `<div class="${priceNumClass}">${Number(value).toFixed(2)}${trendHtml}</div>` +
            (origin && origin !== '—' ? `<div class="price-country" title="${escapeHtml(origin)}"><span class="origin-code">${escapeHtml(origin)}</span></div>` : '');
        }
        tr.appendChild(td);
      });
      bodyRows.appendChild(tr);
    });
  });
}

function updateStats(pivot, flatRows) {
  document.getElementById('sRetailers').textContent = appState.meta.retailers.length || '—';
  document.getElementById('sProducts').textContent = appState.products.length || '—';
  document.getElementById('sDates').textContent = pivot?.counts?.dates ?? (pivot?.dates?.length || '—');
  const prices = flatRows.map((r) => r.price).filter((p) => p > 0);
  document.getElementById('sTotal').textContent = prices.length || '—';
  document.getElementById('sAvg').textContent = prices.length ? (prices.reduce((a, b) => a + b, 0) / prices.length).toFixed(2) : '—';
}

async function loadPivot() {
  try {
    const payload = await loadHistoryApi({ retailer: fetchState.selectedRetailer, product: fetchState.selectedProduct });
    console.log('[MarketPulse] GET /api/history →', payload);
    const mapped = mapHistoryResponse(payload);
    lastPivot = mapped;
    renderPivotTable(mapped);
    const flatRows = flattenPivot(mapped);
    appState.allData = flatRows;
    saveState();
    updateStats(mapped, flatRows);
    updateTicker(flatRows);
  } catch (error) {
    setStatus(`✗ ${error.message}`, 'err');
  }
}

/* ============================== DETAIL TABLE (latest fetch) ============================== */

// Search-URL patterns per retailer, used to build the "click to verify" link on each price.
// Keyed by a fully-collapsed alphanumeric id (see collapseRetailerKey below) rather than
// whatever normalizeRetailerId happens to produce - that only lowercases and collapses
// whitespace, so "Union Coop" normalizes to "union coop" while this map used to be keyed
// "union_coop": an exact-string mismatch that silently dropped the verify link for Union
// Coop specifically while the single-word retailer names matched by coincidence.
const RETAILER_SEARCH_URLS = {
  carrefour: 'https://www.carrefouruae.com/mafuae/en/search?keyword=',
  lulu: 'https://www.luluhypermarket.com/en-ae/search?q=',
  barakat: 'https://barakatfresh.ae/search?q=',
  kibsons: 'https://www.kibsons.com/search?q=',
  unioncoop: 'https://www.unioncoop.ae/en/search?q=',
};

// Strips spaces, underscores, and hyphens entirely (in addition to lowercasing) so any
// spelling/formatting variant of a retailer id or name - "Union Coop", "union_coop",
// "UnionCoop", "union-coop" - collapses to the same lookup key ("unioncoop"). This is
// deliberately more permissive than normalizeRetailerId, which is used elsewhere for
// display/grouping and needs to preserve word boundaries.
function collapseRetailerKey(value) {
  return String(value || '').trim().toLowerCase().replace(/[\s_-]+/g, '');
}

function buildVerifyUrl(retailerId, productLabel) {
  const key = collapseRetailerKey(retailerId);
  const base = RETAILER_SEARCH_URLS[key];
  if (!base) return null;
  return base + encodeURIComponent(productLabel || '');
}

// Key used to identify "the same product at the same retailer" across fetches,
// so a new fetch updates that specific row instead of adding a duplicate for
// the same product/retailer pair. A product can still have one row per retailer.
function detailRowKey(row) {
  return `${normalizeRetailerId(row.retailer)}|${normalizeProductId(row.product)}`;
}

// Best-effort timestamp for a row, used to decide which fetch of a product is newer.
function detailRowTimestamp(row) {
  const fetchedAt = row.fetched_at ? Date.parse(row.fetched_at) : NaN;
  if (!Number.isNaN(fetchedAt)) return fetchedAt;
  const dateOnly = row.date ? Date.parse(row.date) : NaN;
  return Number.isNaN(dateOnly) ? 0 : dateOnly;
}

// Merges newly fetched rows into the existing set: each (product, retailer) pair
// keeps only its single most-recently-fetched entry, so re-fetching that same
// pair replaces the old price instead of adding a duplicate row.
function mergeDetailRows(existingRows, incomingRows) {
  const map = new Map();
  const consider = (row) => {
    const key = detailRowKey(row);
    const current = map.get(key);
    if (!current || detailRowTimestamp(row) >= detailRowTimestamp(current)) {
      map.set(key, row);
    }
  };
  existingRows.forEach(consider);
  incomingRows.forEach(consider);
  return [...map.values()];
}

// Looks up the most recent price on record for this product/retailer that's
// strictly before item.date. The detail table only ever holds the latest
// fetch (one row per product/retailer), so "previous price" has to come
// from the multi-date history (lastPivot), not from the detail rows
// themselves.
function findPreviousDatedPrice(item) {
  if (!lastPivot || !Array.isArray(lastPivot.rows) || !lastPivot.rows.length) return null;

  const pivotRow = lastPivot.rows.find((row) =>
    normalizeRetailerId(row.retailer_id || row.retailer_label) === normalizeRetailerId(item.retailer) &&
    normalizeProductId(row.product_id || row.product_label) === normalizeProductId(item.product)
  );
  if (!pivotRow || !pivotRow.prices) return null;

  // ISO "YYYY-MM-DD" strings sort correctly with a plain string sort.
  const priorDates = Object.keys(pivotRow.prices)
    .filter((d) => d < item.date && pivotRow.prices[d] !== undefined && pivotRow.prices[d] !== null)
    .sort();
  if (!priorDates.length) return null;

  const previousDate = priorDates[priorDates.length - 1];
  return { date: previousDate, price: Number(pivotRow.prices[previousDate]) || 0 };
}

// Small up/down indicator comparing today's fetched price to the closest
// earlier date on record for the same product/retailer.
//
// Convention: a price INCREASE is flagged red (it now costs more), a
// DECREASE is flagged green (it's now cheaper) - the reverse of a typical
// stock ticker, since this is tracking cost rather than portfolio value.
// If there's no earlier price to compare against, nothing renders rather
// than guessing a direction.
function buildTrendBadge(item) {
  const previous = findPreviousDatedPrice(item);
  if (!previous || !(previous.price > 0) || !(item.price > 0)) {
    return '<span class="mu">—</span>';
  }

  const diff = item.price - previous.price;
  const pct = (diff / previous.price) * 100;
  const prevTitle = `vs ${formatPrice(previous.price)} AED on ${escapeHtml(previous.date)}`;

  if (Math.abs(diff) < 0.005) {
    return `<span class="trend-badge flat" title="No change ${prevTitle}">▬ 0.0%</span>`;
  }
  const up = diff > 0;
  return `<span class="trend-badge ${up ? 'up' : 'down'}" title="${up ? 'Up' : 'Down'} ${prevTitle}">` +
    `${up ? '▲' : '▼'} ${up ? '+' : '−'}${Math.abs(pct).toFixed(1)}%</span>`;
}

function renderDetailTable(rows) {
  const panel = document.getElementById('detailPanel');
  const wrap = document.getElementById('detailWrap');
  const sub = document.getElementById('detailSub');
  if (!rows.length) { panel.style.display = 'none'; return; }
  panel.style.display = 'block';

  const sorted = [...rows].sort((a, b) => a.product.localeCompare(b.product) || a.retailer.localeCompare(b.retailer));
  const html = '<table class="dtbl"><thead><tr><th>Date</th><th>Product</th><th>Retailer</th><th>Price</th><th>Trend</th><th>Unit</th><th>Origin Country</th><th>Fetched At</th></tr></thead><tbody>' +
    sorted.map((item) => {
      const meta = getProductMeta(item.product);
      const verifyUrl = item.price > 0 ? buildVerifyUrl(item.retailer, meta.name || item.product) : null;
      const priceCell = item.price > 0
        ? (verifyUrl
            ? `<a class="price-badge" href="${verifyUrl}" target="_blank" rel="noopener noreferrer" title="Click to verify on ${escapeHtml(item.retailer || 'retailer')} site">
                 <span class="price-num">${formatPrice(item.price)}</span><span class="price-cur">AED</span>
                 <svg class="verify-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                   <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                   <path d="M15 3h6v6"/><path d="M10 14 21 3"/>
                 </svg>
               </a>`
            : `<div class="price-box"><span class="price-num">${formatPrice(item.price)}</span><span class="price-cur">AED</span></div>`)
        : '<span class="mu">N/A</span>';
      return `<tr>
        <td class="mu">${escapeHtml(item.date)}</td>
        <td><div class="prod-cell"><span class="prod-emoji">${meta.emoji || '🛒'}</span><span class="prod-name">${escapeHtml(meta.name || item.product)}</span></div></td>
        <td>${createRetailerPill(item.retailer)}</td>
        <td>${priceCell}</td>
        <td>${buildTrendBadge(item)}</td>
        <td class="mu">per kg</td>
        <td><span class="origin-code" style="margin-right:6px;">${escapeHtml(normalizeCountryName(item.origin_country))}</span><span class="mu">${escapeHtml(getFullCountryName(item.origin_country))}</span></td>
        <td class="mu">${escapeHtml(item.fetched_at ? new Date(item.fetched_at).toLocaleTimeString('en-AE') : '—')}</td>
      </tr>`;
    }).join('') + '</tbody></table>';
  wrap.innerHTML = html;
  sub.textContent = `${rows.length} row${rows.length === 1 ? '' : 's'} · one row per product per retailer, latest fetch only · click any price to verify on retailer site · trend vs. previous date on record`;
}

/* ============================== FETCH ACTION ============================== */

async function fetchLatestPrices() {
  const button = document.getElementById('fetchBtn');
  const statusLine = document.getElementById('statusLine');
  const errorList = document.getElementById('errorList');

  button.disabled = true;
  errorList.innerHTML = '';
  statusLine.className = 'status-line';
  statusLine.textContent = 'Starting…';

  try {
    const job = await createJob({ retailer: fetchState.selectedRetailer, product: fetchState.selectedProduct });
    console.log('[MarketPulse] POST /api/jobs →', job);
    if (!job.ok) {
      statusLine.classList.add('error');
      statusLine.textContent = job.error || 'Could not start the fetch.';
      return;
    }

    let currentJob = job;
    while (currentJob.status !== 'done') {
      await new Promise((resolve) => setTimeout(resolve, 1200));
      currentJob = await pollJob(currentJob.job_id);
      console.log(`[MarketPulse] GET /api/jobs/${currentJob.job_id} →`, currentJob);
      if (!currentJob.ok) {
        statusLine.classList.add('error');
        statusLine.textContent = currentJob.error || 'Lost track of the job.';
        return;
      }
      statusLine.textContent = `Fetching… ${currentJob.completed}/${currentJob.total}`;
    }

    console.log('[MarketPulse] Job finished, raw results:', currentJob.results);

    const skipped = currentJob.results.filter((item) => !item.ok && item.skipped);
    const failures = currentJob.results.filter((item) => !item.ok && !item.skipped);
    const successes = currentJob.results.filter((item) => item.ok).length;
    const attempted = currentJob.results.length - skipped.length;

    statusLine.classList.add('ok');
    statusLine.textContent = `Fetched ${successes}/${attempted} price(s).` +
      (skipped.length ? ` · ${skipped.length} skipped (delisted).` : '');

    if (failures.length || skipped.length) {
      console.warn('[MarketPulse] Failed items:', failures, 'Skipped items:', skipped);
      errorList.innerHTML =
        failures
          .map((item) => `<div>⚠ ${escapeHtml(item.product_label || '')} @ ${escapeHtml(item.supermarket || '')}: ${escapeHtml(item.error || '')}</div>`)
          .join('') +
        skipped
          .map((item) => `<div class="skipped">ℹ ${escapeHtml(item.product_label || '')} @ ${escapeHtml(item.supermarket || '')}: ${escapeHtml(item.error || '')}</div>`)
          .join('');
    }

    const freshRows = currentJob.results.filter((item) => item.ok === true).map((item) => mapScrapeResult(item));
    console.log('[MarketPulse] Mapped fresh rows from this fetch:', freshRows);
    lastDetail = mergeDetailRows(lastDetail, freshRows);
    savePersistedDetail(lastDetail);
    console.log('[MarketPulse] Full detail set after merge (latest price per product):', lastDetail);
    renderDetailTable(lastDetail);

    await loadPivot();
    console.log('[MarketPulse] Pivot reloaded from /api/history:', lastPivot);
  } catch (error) {
    console.error('[MarketPulse] Fetch flow threw an error:', error);
    statusLine.classList.add('error');
    statusLine.textContent = 'Request failed: ' + error.message;
  } finally {
    button.disabled = false;
  }
}

/* ============================== CSV / COPY ============================== */

function toCsv(headers, rows) {
  return [headers, ...rows].map((row) => row.map((cell) => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
}

function downloadCsv(filename, csv) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function pivotToCsv() {
  if (!lastPivot || !lastPivot.rows.length) return '';
  const headers = ['Product', 'Retailer', ...lastPivot.dates];
  const rows = lastPivot.rows.map((row) => [
    row.product_label || row.product_id,
    row.retailer_label || row.retailer_id,
    ...lastPivot.dates.map((d) => (row.prices[d] !== undefined ? Number(row.prices[d]).toFixed(2) : '')),
  ]);
  return toCsv(headers, rows);
}

function detailToCsv() {
  if (!lastDetail.length) return '';
  const headers = ['Date', 'Product', 'Retailer', 'Price (AED)', 'Unit', 'Origin Country', 'Source URL', 'Fetched At'];
  const rows = lastDetail.map((r) => [r.date, r.product, r.retailer, (r.price || 0).toFixed(2), r.unit || 'per kg', normalizeCountryName(r.origin_country) || '—', r.url || '', r.fetched_at || '']);
  return toCsv(headers, rows);
}

function wireCsvCopyButtons() {
  const wire = (csvBtnId, copyBtnId, buildCsv, filename) => {
    document.getElementById(csvBtnId)?.addEventListener('click', () => {
      const csv = buildCsv();
      if (!csv) { setStatus('Nothing to export yet.', 'err'); return; }
      downloadCsv(filename, csv);
    });
    document.getElementById(copyBtnId)?.addEventListener('click', () => {
      const csv = buildCsv();
      if (!csv) { setStatus('Nothing to copy yet.', 'err'); return; }
      navigator.clipboard.writeText(csv).then(() => setStatus('✓ Copied to clipboard', 'ok'));
    });
  };
  wire('fetchCsvBtn', 'fetchCopyBtn', pivotToCsv, 'marketpulse-prices.csv');
  wire('pivotCsvBtn', 'pivotCopyBtn', pivotToCsv, 'marketpulse-prices.csv');
  wire('detailCsvBtn', 'detailCopyBtn', detailToCsv, 'marketpulse-latest-fetch.csv');
}

/* ============================== PRODUCTS TAB ============================== */

function bindProductsTab() {
  const addAndRefresh = async () => { await addProduct(); refreshProductDependents(); renderFetchDropdowns(); };

  document.getElementById('btnAddProduct')?.addEventListener('click', addAndRefresh);
  document.getElementById('btnResetProducts')?.addEventListener('click', () => { resetProducts(); refreshProductDependents(); renderFetchDropdowns(); });
  document.getElementById('newName')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); addAndRefresh(); }
  });
  document.getElementById('prodTags')?.addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-index]');
    if (!button) return;
    await removeProduct(Number(button.getAttribute('data-index')));
    refreshProductDependents();
    renderFetchDropdowns();
  });
  document.getElementById('presetRow')?.addEventListener('click', async (event) => {
    const chip = event.target.closest('span[data-id]');
    if (!chip) return;
    const name = chip.textContent.trim().replace(/^\S+\s*/, '') || chip.getAttribute('data-id');
    const emoji = chip.getAttribute('data-emoji');
    const nameInput = document.getElementById('newName');
    const emojiInput = document.getElementById('newEmoji');
    // Reuse addProduct() so presets go through the same backend call as a
    // manually-typed product (and therefore also land in Fetch & Analyse).
    if (nameInput) nameInput.value = name;
    if (emojiInput) emojiInput.value = emoji || '';
    await addProduct();
    refreshProductDependents();
    renderFetchDropdowns();
  });
}

function refreshProductDependents() {
  renderProductTags();
  renderProductSelect();
  renderBasketBuilder();
  renderPresets();
  document.getElementById('sProducts').textContent = appState.products.length || '—';
}

/* ============================== PRICE VARIATION TAB ============================== */

function populateVariationSelects() {
  const varRetailer = document.getElementById('varRetailer');
  const varProduct = document.getElementById('varProduct');
  if (varRetailer) {
    const current = varRetailer.value;
    varRetailer.innerHTML = '<option value="all">All Retailers</option>' +
      appState.meta.retailers.map((r) => `<option value="${escapeHtml(r.id)}">${escapeHtml(r.name)}</option>`).join('');
    if ([...varRetailer.options].some((o) => o.value === current)) varRetailer.value = current;
  }
  renderProductSelect(); // fills #varProduct too
}

function updateVariationStats(data30, dates, retailers, products) {
  document.getElementById('vRetailers').textContent = retailers.length || '—';
  document.getElementById('vProducts').textContent = products.length || '—';
  document.getElementById('vDates').textContent = dates.length || '—';
  document.getElementById('vPoints').textContent = data30.length || '—';
  const prices = data30.map((r) => r.price).filter((p) => p > 0);
  document.getElementById('vAvg').textContent = prices.length ? (prices.reduce((a, b) => a + b, 0) / prices.length).toFixed(2) : '—';
}

function renderVariationTab() {
  const retailerValue = document.getElementById('varRetailer')?.value || 'all';
  const productValue = document.getElementById('varProduct')?.value || 'all';
  const rangeValue = document.getElementById('varRange')?.value || '30';

  // Anchor the range window to the most recent date we actually have data
  // for, not to the real-world "today" - otherwise stale/older fetch history
  // silently disappears from this tab even though appState.allData still has it.
  const latestDate = appState.allData.reduce((latest, row) => (row.date > latest ? row.date : latest), '');
  let cutoffStr = '0000-00-00';
  if (rangeValue !== 'all') {
    const cutoff = latestDate ? new Date(`${latestDate}T00:00:00`) : new Date();
    cutoff.setDate(cutoff.getDate() - Number(rangeValue));
    cutoffStr = cutoff.toISOString().slice(0, 10);
  }

  const rangeLabel = rangeValue === 'all' ? 'All time' : `Last ${rangeValue} days`;
  const subEl = document.getElementById('varSub');
  if (subEl) subEl.textContent = rangeLabel;

  const data30 = appState.allData.filter((row) =>
    row.date >= cutoffStr &&
    (retailerValue === 'all' || normalizeRetailerId(row.retailer) === normalizeRetailerId(retailerValue)) &&
    (productValue === 'all' || normalizeProductId(row.product) === normalizeProductId(productValue)));

  const grid = document.getElementById('varChartsGrid');
  const focusPanel = document.getElementById('varFocusPanel');

  if (!data30.length) {
    grid.innerHTML = '<div class="empty-state">No variation data yet — fetch some prices first.</div>';
    focusPanel.style.display = 'none';
    document.getElementById('insightLeaderboard').innerHTML = '';
    document.getElementById('insightMovers').innerHTML = '';
    document.getElementById('vs-up').textContent = '0';
    document.getElementById('vs-down').textContent = '0';
    document.getElementById('vs-flat').textContent = '0';
    document.getElementById('vs-dates').textContent = '0';
    updateVariationStats([], [], [], []);
    return;
  }

  const dates = [...new Set(data30.map((r) => r.date))].sort();
  const products = [...new Set(data30.map((r) => r.product))].sort();
  const retailers = [...new Set(data30.map((r) => r.retailer))].sort();
  const lookup = {};
  data30.forEach((row) => { lookup[`${row.date}|${normalizeRetailerId(row.retailer)}|${normalizeProductId(row.product)}`] = row; });

  updateVariationStats(data30, dates, retailers, products);
  try {
    renderVariationCharts(data30, dates, retailers, products, lookup);
  } catch (error) {
    console.error('[MarketPulse] renderVariationCharts failed, continuing with tables:', error);
  }
  renderMarketInsights(dates, retailers, products, lookup);
  bindVariationCsvExport(dates, retailers, products, lookup);

  // "Check a specific product across specific supermarkets": once the person
  // narrows to a single product, show a focused per-retailer comparison.
  if (productValue !== 'all' && products.length === 1) {
    renderProductFocus(products[0], dates, retailers, lookup);
    focusPanel.style.display = 'block';
  } else {
    focusPanel.style.display = 'none';
  }
}

// Compares one product's price across every supermarket currently in view,
// sorted cheapest-first, with the best (lowest latest) price called out.
function renderProductFocus(productId, dates, retailers, lookup) {
  const meta = getProductMeta(productId);
  const titleEl = document.getElementById('varFocusTitle');
  const subEl = document.getElementById('varFocusSub');
  if (titleEl) titleEl.textContent = `${meta.emoji || '🛒'} ${meta.name || productId} — Price by Supermarket`;
  if (subEl) subEl.textContent = `${retailers.length} retailer${retailers.length !== 1 ? 's' : ''} · ${dates[0]} → ${dates[dates.length - 1]}`;

  const latestDate = dates[dates.length - 1];
  const previousDate = dates.length > 1 ? dates[dates.length - 2] : null;

  const rows = retailers.map((retailerId) => {
    const latest = lookup[`${latestDate}|${normalizeRetailerId(retailerId)}|${normalizeProductId(productId)}`];
    const previous = previousDate ? lookup[`${previousDate}|${normalizeRetailerId(retailerId)}|${normalizeProductId(productId)}`] : null;
    const seriesPrices = dates
      .map((date) => lookup[`${date}|${normalizeRetailerId(retailerId)}|${normalizeProductId(productId)}`]?.price)
      .filter((p) => p > 0);
    const min = seriesPrices.length ? Math.min(...seriesPrices) : null;
    const max = seriesPrices.length ? Math.max(...seriesPrices) : null;
    let changePct = null;
    if (latest?.price && previous?.price) {
      changePct = ((latest.price - previous.price) / previous.price) * 100;
    }
    return {
      retailerId,
      latestPrice: latest?.price || null,
      changePct,
      min,
      max,
      origin: latest?.origin_country ? getFullCountryName(latest.origin_country) : '—',
    };
  }).filter((row) => row.latestPrice !== null);

  rows.sort((a, b) => a.latestPrice - b.latestPrice);
  const bestPrice = rows.length ? rows[0].latestPrice : null;

  const body = document.getElementById('varFocusBody');
  if (!body) return;

  if (!rows.length) {
    body.innerHTML = '<div class="empty-state">No prices for this product in the selected range.</div>';
    return;
  }

  const header = ['Supermarket', 'Latest Price', 'Change vs Prev.', 'Min (range)', 'Max (range)', 'Origin'];
  body.innerHTML = `<table class="focus-tbl"><thead><tr>${header.map((h) => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>` +
    rows.map((row) => {
      const isBest = row.latestPrice === bestPrice;
      const changeClass = row.changePct === null ? 'focus-change-flat' : row.changePct > 0.5 ? 'focus-change-up' : row.changePct < -0.5 ? 'focus-change-down' : 'focus-change-flat';
      const changeText = row.changePct === null ? '—' : `${row.changePct > 0 ? '+' : ''}${row.changePct.toFixed(2)}%`;
      return `<tr class="${isBest ? 'best-row' : ''}">
        <td>${createRetailerPill(row.retailerId)}</td>
        <td><span class="focus-price">AED ${row.latestPrice.toFixed(2)}</span>${isBest ? '<span class="best-badge">🏆 Best Price</span>' : ''}</td>
        <td class="${changeClass}">${changeText}</td>
        <td>AED ${row.min !== null ? row.min.toFixed(2) : '—'}</td>
        <td>AED ${row.max !== null ? row.max.toFixed(2) : '—'}</td>
        <td>${escapeHtml(row.origin)}</td>
      </tr>`;
    }).join('') + '</tbody></table>';
}

// "Best Value Retailers": for every product, find whichever retailer had the
// lowest latest price, and tally up how often each retailer wins. Answers
// "which supermarket is generally cheapest?" at a glance.
function renderInsightLeaderboard(dates, retailers, products, lookup) {
  const container = document.getElementById('insightLeaderboard');
  if (!container) return;
  const latestDate = dates[dates.length - 1];

  const wins = {};
  retailers.forEach((r) => { wins[r] = 0; });

  products.forEach((productId) => {
    const priced = retailers
      .map((retailerId) => ({ retailerId, row: lookup[`${latestDate}|${normalizeRetailerId(retailerId)}|${normalizeProductId(productId)}`] }))
      .filter((entry) => entry.row && entry.row.price > 0);
    if (!priced.length) return;
    const minPrice = Math.min(...priced.map((entry) => entry.row.price));
    priced.filter((entry) => entry.row.price === minPrice).forEach((entry) => { wins[entry.retailerId] += 1; });
  });

  const ranked = Object.entries(wins).sort((a, b) => b[1] - a[1]);
  const medals = ['🥇', '🥈', '🥉'];

  if (!ranked.length || ranked.every(([, count]) => count === 0)) {
    container.innerHTML = '<div class="empty-state">Not enough overlapping prices yet.</div>';
    return;
  }

  container.innerHTML = ranked.map(([retailerId, count], index) => `
    <div class="insight-row">
      <div class="insight-left">
        <span class="insight-rank">${medals[index] || index + 1}</span>
        ${createRetailerPill(retailerId)}
      </div>
      <span class="insight-count">${count} product${count !== 1 ? 's' : ''} cheapest</span>
    </div>`).join('');
}

// "Biggest Movers": average each product's price across retailers on the
// latest vs. previous date, rank by % change, and surface the top gainers
// and fallers. Answers "what actually changed since last time?"
function renderInsightMovers(dates, retailers, products, lookup) {
  const container = document.getElementById('insightMovers');
  if (!container) return;

  if (dates.length < 2) {
    container.innerHTML = '<div class="empty-state">Need at least 2 fetch dates to compare.</div>';
    return;
  }

  const latestDate = dates[dates.length - 1];
  const previousDate = dates[dates.length - 2];

  const movers = products.map((productId) => {
    const latestPrices = retailers.map((r) => lookup[`${latestDate}|${normalizeRetailerId(r)}|${normalizeProductId(productId)}`]).filter((row) => row && row.price > 0).map((row) => row.price);
    const prevPrices = retailers.map((r) => lookup[`${previousDate}|${normalizeRetailerId(r)}|${normalizeProductId(productId)}`]).filter((row) => row && row.price > 0).map((row) => row.price);
    if (!latestPrices.length || !prevPrices.length) return null;
    const latestAvg = latestPrices.reduce((a, b) => a + b, 0) / latestPrices.length;
    const prevAvg = prevPrices.reduce((a, b) => a + b, 0) / prevPrices.length;
    if (!prevAvg) return null;
    const pct = ((latestAvg - prevAvg) / prevAvg) * 100;
    return { productId, pct };
  }).filter(Boolean);

  if (!movers.length) {
    container.innerHTML = '<div class="empty-state">No comparable prices between the last two dates.</div>';
    return;
  }

  movers.sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct));
  const top = movers.slice(0, 5);

  container.innerHTML = top.map((mover) => {
    const meta = getProductMeta(mover.productId);
    const changeClass = mover.pct > 0.5 ? 'focus-change-up' : mover.pct < -0.5 ? 'focus-change-down' : 'focus-change-flat';
    const arrow = mover.pct > 0.5 ? '▲' : mover.pct < -0.5 ? '▼' : '●';
    return `
      <div class="insight-row">
        <div class="insight-left">
          <span class="insight-name">${escapeHtml(meta.emoji || '🛒')} ${escapeHtml(meta.name || mover.productId)}</span>
        </div>
        <span class="insight-mover-pct ${changeClass}">${arrow} ${mover.pct > 0 ? '+' : ''}${mover.pct.toFixed(1)}%</span>
      </div>`;
  }).join('');
}

function renderMarketInsights(dates, retailers, products, lookup) {
  renderInsightLeaderboard(dates, retailers, products, lookup);
  renderInsightMovers(dates, retailers, products, lookup);
}

// Builds the same product x retailer x date grid as before for CSV export,
// without rendering a visible table for it.
function bindVariationCsvExport(dates, retailers, products, lookup) {
  const button = document.getElementById('btnExportVariation');
  if (!button) return;
  button.onclick = () => {
    const latestDate = dates[dates.length - 1];
    const previousDate = dates.length > 1 ? dates[dates.length - 2] : null;
    const rows = [];
    products.forEach((productId) => {
      retailers.forEach((retailerId) => {
        const latest = lookup[`${latestDate}|${normalizeRetailerId(retailerId)}|${normalizeProductId(productId)}`];
        const previous = previousDate ? lookup[`${previousDate}|${normalizeRetailerId(retailerId)}|${normalizeProductId(productId)}`] : null;
        const prices = dates.map((date) => {
          const row = lookup[`${date}|${normalizeRetailerId(retailerId)}|${normalizeProductId(productId)}`];
          return row && row.price ? row.price.toFixed(2) : '';
        });
        let change = '';
        if (latest?.price && previous?.price) {
          const pct = ((latest.price - previous.price) / previous.price) * 100;
          change = `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
        }
        rows.push([productId, retailerId, normalizeCountryName(latest?.origin_country) || '', ...prices, change]);
      });
    });
    const header = ['Product', 'Retailer', 'Origin Country', ...dates, 'Change %'];
    const csv = toCsv(header, rows);
    downloadCsv('price-variation.csv', csv);
  };
}

/* ============================== ANALYTICS TAB ============================== */
// Decision-support analytics for analytical/procurement users: statistical
// volatility, retailer pricing behaviour, outlier detection, trend/forecast,
// and a basket-cost comparator. All derived client-side from appState.allData
// (the same flat rows the Price Variation tab uses), so no backend changes.

const basketQty = {}; // productId -> quantity in kg, persisted only in-memory for this session

function mean(arr) { return arr.reduce((a, b) => a + b, 0) / arr.length; }

function median(arr) {
  const sorted = [...arr].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// Sample standard deviation (n-1 denominator) - fine down to n=2, returns 0 below that.
function stddev(arr, avg) {
  if (arr.length < 2) return 0;
  const variance = arr.reduce((a, b) => a + (b - avg) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(variance);
}

// Same "anchor to latest known date, not real-world today" window logic used
// by the Price Variation tab, applied independently here for the Analytics tab.
function getAnalyticsRows(rangeValue) {
  const latestDate = appState.allData.reduce((latest, row) => (row.date > latest ? row.date : latest), '');
  let cutoffStr = '0000-00-00';
  if (rangeValue !== 'all') {
    const cutoff = latestDate ? new Date(`${latestDate}T00:00:00`) : new Date();
    cutoff.setDate(cutoff.getDate() - Number(rangeValue));
    cutoffStr = cutoff.toISOString().slice(0, 10);
  }
  return appState.allData.filter((row) => row.date >= cutoffStr);
}

// Per-product statistical summary across every retailer/date in view, ranked
// by coefficient of variation (CV = stddev/mean) - the standard way to compare
// volatility across products that sit at very different price levels.
function computeProductStats(rows) {
  const byProduct = new Map();
  rows.forEach((row) => {
    if (row.price <= 0) return;
    const key = normalizeProductId(row.product);
    if (!byProduct.has(key)) byProduct.set(key, { productId: row.product, prices: [] });
    byProduct.get(key).prices.push(row.price);
  });
  return [...byProduct.values()].map(({ productId, prices }) => {
    const avg = mean(prices);
    const sd = stddev(prices, avg);
    const cv = avg ? (sd / avg) * 100 : 0;
    return {
      productId, count: prices.length, min: Math.min(...prices), max: Math.max(...prices),
      mean: avg, median: median(prices), std: sd, cv,
    };
  }).sort((a, b) => b.cv - a.cv);
}

function volatilityBadge(cv) {
  if (cv < 10) return '<span class="ana-badge ana-badge-low">Low</span>';
  if (cv < 25) return '<span class="ana-badge ana-badge-med">Medium</span>';
  return '<span class="ana-badge ana-badge-high">High</span>';
}

function renderProductStatsTable(stats) {
  const body = document.getElementById('statsTableBody');
  if (!body) return;
  if (!stats.length) {
    body.innerHTML = '<div class="empty-state">No priced data yet — fetch some prices first.</div>';
    return;
  }
  const header = ['Product', 'Samples', 'Min', 'Max', 'Mean', 'Median', 'Std Dev', 'CV %', 'Volatility'];
  body.innerHTML = `<table class="focus-tbl"><thead><tr>${header.map((h) => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>` +
    stats.map((row) => {
      const meta = getProductMeta(row.productId);
      return `<tr>
        <td>${escapeHtml(meta.emoji || '🛒')} ${escapeHtml(meta.name || row.productId)}</td>
        <td>${row.count}</td>
        <td>AED ${row.min.toFixed(2)}</td>
        <td>AED ${row.max.toFixed(2)}</td>
        <td>AED ${row.mean.toFixed(2)}</td>
        <td>AED ${row.median.toFixed(2)}</td>
        <td>${row.std.toFixed(2)}</td>
        <td>${row.cv.toFixed(1)}%</td>
        <td>${volatilityBadge(row.cv)}</td>
      </tr>`;
    }).join('') + '</tbody></table>';
}

// For every date+product where 2+ retailers reported a price, measure how far
// each retailer's price sat from that day's market average, then average
// those deviations per retailer. Negative = generally cheaper than market,
// positive = generally pricier ("premium" positioning).
function computeRetailerPositioning(rows) {
  const groups = new Map();
  rows.forEach((row) => {
    if (row.price <= 0) return;
    const key = `${row.date}|${normalizeProductId(row.product)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });

  const deviations = {}; // normalized retailer id -> { label, values: [] }
  groups.forEach((entries) => {
    if (entries.length < 2) return; // need at least 2 retailers to define a "market" price
    const avg = mean(entries.map((e) => e.price));
    if (!avg) return;
    entries.forEach((e) => {
      const key = normalizeRetailerId(e.retailer);
      if (!deviations[key]) deviations[key] = { retailerId: e.retailer, values: [] };
      deviations[key].values.push(((e.price - avg) / avg) * 100);
    });
  });

  return Object.values(deviations)
    .map((d) => ({ retailerId: d.retailerId, avgDeviation: mean(d.values), samples: d.values.length }))
    .sort((a, b) => a.avgDeviation - b.avgDeviation);
}

function renderRetailerPositioning(positioning) {
  const container = document.getElementById('retailerPositioningBody');
  if (!container) return;
  if (!positioning.length) {
    container.innerHTML = '<div class="empty-state">Need 2+ retailers priced on the same day to compare.</div>';
    return;
  }
  container.innerHTML = positioning.map((p) => {
    const isDiscount = p.avgDeviation < -0.5;
    const isPremium = p.avgDeviation > 0.5;
    const label = isDiscount ? 'Discount' : isPremium ? 'Premium' : 'Market avg';
    const cls = isDiscount ? 'focus-change-down' : isPremium ? 'focus-change-up' : 'focus-change-flat';
    return `
      <div class="insight-row">
        <div class="insight-left">
          ${createRetailerPill(p.retailerId)}
          <span class="ana-metric-note">${p.samples} samples</span>
        </div>
        <span class="${cls}">${label} · ${p.avgDeviation > 0 ? '+' : ''}${p.avgDeviation.toFixed(1)}%</span>
      </div>`;
  }).join('');
}

// Per retailer+product time series, flag any point whose z-score (distance
// from that series' own mean, in standard deviations) is 2 or more - a quick,
// distribution-free way to surface pricing errors or genuine spikes.
function detectAnomalies(rows) {
  const series = new Map();
  rows.forEach((row) => {
    if (row.price <= 0) return;
    const key = `${normalizeRetailerId(row.retailer)}|${normalizeProductId(row.product)}`;
    if (!series.has(key)) series.set(key, []);
    series.get(key).push(row);
  });

  const anomalies = [];
  series.forEach((entries) => {
    if (entries.length < 4) return; // too few points for a meaningful z-score
    const prices = entries.map((e) => e.price);
    const avg = mean(prices);
    const sd = stddev(prices, avg);
    if (!sd) return;
    entries.forEach((e) => {
      const z = (e.price - avg) / sd;
      if (Math.abs(z) >= 2) anomalies.push({ ...e, z, avg });
    });
  });

  return anomalies.sort((a, b) => Math.abs(b.z) - Math.abs(a.z)).slice(0, 8);
}

function renderAnomalies(anomalies) {
  const container = document.getElementById('anomalyBody');
  if (!container) return;
  if (!anomalies.length) {
    container.innerHTML = '<div class="empty-state">No statistical outliers in this range.</div>';
    return;
  }
  container.innerHTML = anomalies.map((a) => {
    const meta = getProductMeta(a.product);
    const up = a.price > a.avg;
    return `
      <div class="insight-row">
        <div class="insight-left">
          <span class="insight-name">${escapeHtml(meta.emoji || '🛒')} ${escapeHtml(meta.name || a.product)}</span>
          <span class="ana-metric-note">${createRetailerPill(a.retailer)} · ${escapeHtml(a.date)}</span>
        </div>
        <span class="${up ? 'focus-change-up' : 'focus-change-down'}">AED ${a.price.toFixed(2)} (z=${a.z.toFixed(1)})</span>
      </div>`;
  }).join('');
}

// Simple ordinary-least-squares linear regression of each product's daily
// average price (across retailers) against day index, giving a slope
// (AED/day), an R^2 fit quality, and a naive one-step-ahead forecast.
function computeProductTrends(rows, dates) {
  const productIds = [...new Set(rows.map((r) => r.product))];
  return productIds.map((productId) => {
    const points = dates.map((date, idx) => {
      const dayPrices = rows
        .filter((r) => r.date === date && normalizeProductId(r.product) === normalizeProductId(productId) && r.price > 0)
        .map((r) => r.price);
      return dayPrices.length ? { x: idx, y: mean(dayPrices) } : null;
    }).filter(Boolean);

    if (points.length < 2) return null;

    const n = points.length;
    const sumX = points.reduce((a, p) => a + p.x, 0);
    const sumY = points.reduce((a, p) => a + p.y, 0);
    const sumXY = points.reduce((a, p) => a + p.x * p.y, 0);
    const sumX2 = points.reduce((a, p) => a + p.x * p.x, 0);
    const denom = n * sumX2 - sumX * sumX;
    const slope = denom ? (n * sumXY - sumX * sumY) / denom : 0;
    const intercept = (sumY - slope * sumX) / n;

    const meanY = sumY / n;
    const ssTot = points.reduce((a, p) => a + (p.y - meanY) ** 2, 0);
    const ssRes = points.reduce((a, p) => a + (p.y - (slope * p.x + intercept)) ** 2, 0);
    const r2 = ssTot ? Math.max(0, 1 - ssRes / ssTot) : 0;

    const lastX = points[points.length - 1].x;
    return { productId, slope, r2, current: points[points.length - 1].y, forecast: slope * (lastX + 1) + intercept };
  }).filter(Boolean).sort((a, b) => Math.abs(b.slope) - Math.abs(a.slope));
}

function renderTrends(trends) {
  const body = document.getElementById('trendTableBody');
  if (!body) return;
  if (!trends.length) {
    body.innerHTML = '<div class="empty-state">Need at least 2 fetch dates to compute a trend.</div>';
    return;
  }
  const header = ['Product', 'Trend', 'Slope (AED/day)', 'Fit (R²)', 'Current Avg', 'Forecast Next'];
  body.innerHTML = `<table class="focus-tbl"><thead><tr>${header.map((h) => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>` +
    trends.map((t) => {
      const meta = getProductMeta(t.productId);
      const dir = t.slope > 0.01 ? 'up' : t.slope < -0.01 ? 'down' : 'flat';
      const arrow = dir === 'up' ? '▲ Rising' : dir === 'down' ? '▼ Falling' : '● Stable';
      return `<tr>
        <td>${escapeHtml(meta.emoji || '🛒')} ${escapeHtml(meta.name || t.productId)}</td>
        <td class="ana-forecast-${dir}">${arrow}</td>
        <td>${t.slope > 0 ? '+' : ''}${t.slope.toFixed(3)}</td>
        <td>${t.r2.toFixed(2)}</td>
        <td>AED ${t.current.toFixed(2)}</td>
        <td>AED ${Math.max(0, t.forecast).toFixed(2)}</td>
      </tr>`;
    }).join('') + '</tbody></table>';
}

// Builds (or rebuilds) the editable quantity grid from the currently
// configured products. Called on init and whenever the product list changes.
function renderBasketBuilder() {
  const wrap = document.getElementById('basketQtyGrid');
  if (!wrap) return;
  wrap.innerHTML = appState.products.map((p) => {
    const val = basketQty[p.id] ?? 1;
    basketQty[p.id] = val;
    return `<div class="basket-qty-item">
      <span class="basket-qty-label">${escapeHtml(p.emoji || '🛒')} ${escapeHtml(p.name || p.id)}</span>
      <input type="number" min="0" step="0.5" class="text-input basket-qty-input" data-product="${escapeHtml(p.id)}" value="${val}">
    </div>`;
  }).join('');
  wrap.querySelectorAll('.basket-qty-input').forEach((input) => {
    input.addEventListener('input', () => {
      basketQty[input.dataset.product] = Number(input.value) || 0;
      renderBasketResults();
    });
  });
}

// Prices out the current basket (qty x latest price) at every retailer that
// has coverage for at least one basket item, ranked cheapest-first - the
// direct "where should I actually shop" answer.
function renderBasketResults() {
  const body = document.getElementById('basketResultsBody');
  if (!body) return;

  const rangeValue = document.getElementById('anaRange')?.value || '30';
  const rows = getAnalyticsRows(rangeValue);
  if (!rows.length) {
    body.innerHTML = '<div class="empty-state">No price data yet — fetch some prices first.</div>';
    return;
  }

  const latestDate = rows.reduce((latest, row) => (row.date > latest ? row.date : latest), '');
  const retailers = [...new Set(rows.map((r) => r.retailer))];

  const results = retailers.map((retailerId) => {
    let total = 0, covered = 0;
    const missing = [];
    appState.products.forEach((p) => {
      const qty = basketQty[p.id] || 0;
      if (!qty) return;
      const match = rows.find((r) =>
        r.date === latestDate &&
        normalizeRetailerId(r.retailer) === normalizeRetailerId(retailerId) &&
        normalizeProductId(r.product) === normalizeProductId(p.id) &&
        r.price > 0);
      if (match) { total += qty * match.price; covered += 1; } else missing.push(p.name || p.id);
    });
    return { retailerId, total, covered, missing };
  }).filter((r) => r.covered > 0).sort((a, b) => a.total - b.total);

  if (!results.length) {
    body.innerHTML = '<div class="empty-state">Set a quantity above for at least one product to compare baskets.</div>';
    return;
  }

  const cheapest = results[0].total;
  const header = ['Supermarket', 'Basket Total', 'vs Cheapest', 'Items Priced'];
  body.innerHTML = `<table class="focus-tbl"><thead><tr>${header.map((h) => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>` +
    results.map((r) => {
      const diff = r.total - cheapest;
      const isBest = diff <= 0.01;
      const diffText = isBest ? '—' : `+AED ${diff.toFixed(2)}`;
      const missingNote = r.missing.length
        ? ` <span class="ana-metric-note" title="Missing: ${escapeHtml(r.missing.join(', '))}">⚠ ${r.missing.length} missing</span>` : '';
      return `<tr class="${isBest ? 'best-row' : ''}">
        <td>${createRetailerPill(r.retailerId)}</td>
        <td><span class="focus-price">AED ${r.total.toFixed(2)}</span>${isBest ? '<span class="best-badge">🏆 Cheapest</span>' : ''}</td>
        <td class="${isBest ? 'focus-change-down' : 'focus-change-flat'}">${diffText}</td>
        <td>${r.covered}/${appState.products.length}${missingNote}</td>
      </tr>`;
    }).join('') + '</tbody></table>';
}

// For each product, groups observed prices by origin country and finds the
// cheapest vs priciest sourcing origin, plus the % spread between them - a
// direct "which origin should we buy from" signal for procurement.
function computeOriginAnalysis(rows) {
  const byProduct = new Map();
  rows.forEach((row) => {
    if (row.price <= 0 || !row.origin_country || row.origin_country === '—') return;
    const key = normalizeProductId(row.product);
    if (!byProduct.has(key)) byProduct.set(key, { productId: row.product, byOrigin: new Map() });
    const entry = byProduct.get(key);
    if (!entry.byOrigin.has(row.origin_country)) entry.byOrigin.set(row.origin_country, []);
    entry.byOrigin.get(row.origin_country).push(row.price);
  });

  return [...byProduct.values()].map(({ productId, byOrigin }) => {
    const origins = [...byOrigin.entries()]
      .map(([origin, prices]) => ({ origin, avg: mean(prices), count: prices.length }))
      .sort((a, b) => a.avg - b.avg);
    if (!origins.length) return null;
    const cheapest = origins[0];
    const priciest = origins[origins.length - 1];
    const spread = origins.length > 1 && cheapest.avg ? ((priciest.avg - cheapest.avg) / cheapest.avg) * 100 : 0;
    return { productId, origins, cheapest, priciest, spread };
  }).filter(Boolean).sort((a, b) => b.spread - a.spread);
}

function renderOriginAnalysis(analysis) {
  const body = document.getElementById('originTableBody');
  if (!body) return;
  if (!analysis.length) {
    body.innerHTML = '<div class="empty-state">No origin-country data in this range yet.</div>';
    return;
  }
  const header = ['Product', 'Origins Seen', 'Cheapest Origin', 'Priciest Origin', 'Price Spread'];
  body.innerHTML = `<table class="focus-tbl"><thead><tr>${header.map((h) => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>` +
    analysis.map((a) => {
      const meta = getProductMeta(a.productId);
      const single = a.origins.length === 1;
      return `<tr>
        <td>${escapeHtml(meta.emoji || '🛒')} ${escapeHtml(meta.name || a.productId)}</td>
        <td>${a.origins.length}</td>
        <td><span class="focus-change-down">${escapeHtml(a.cheapest.origin)}</span> · AED ${a.cheapest.avg.toFixed(2)}</td>
        <td>${single ? '—' : `<span class="focus-change-up">${escapeHtml(a.priciest.origin)}</span> · AED ${a.priciest.avg.toFixed(2)}`}</td>
        <td>${single ? '—' : `${a.spread.toFixed(1)}%`}</td>
      </tr>`;
    }).join('') + '</tbody></table>';
}

// Pearson correlation coefficient between two equal-length numeric series.
function pearsonCorrelation(xs, ys) {
  const n = xs.length;
  const mx = mean(xs), my = mean(ys);
  let num = 0, dx2 = 0, dy2 = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - mx, dy = ys[i] - my;
    num += dx * dy; dx2 += dx * dx; dy2 += dy * dy;
  }
  const denom = Math.sqrt(dx2 * dy2);
  return denom ? num / denom : 0;
}

// For every pair of retailers, correlates their prices on the (date, product)
// points they both reported. High positive r = prices move together (shared
// supplier / tacit coordination); near zero or negative = independent or
// inverse pricing - useful context when deciding who to negotiate with.
function computeRetailerCorrelations(rows) {
  const byRetailer = new Map();
  rows.forEach((row) => {
    if (row.price <= 0) return;
    const rid = normalizeRetailerId(row.retailer);
    if (!byRetailer.has(rid)) byRetailer.set(rid, { retailerId: row.retailer, prices: new Map() });
    byRetailer.get(rid).prices.set(`${row.date}|${normalizeProductId(row.product)}`, row.price);
  });

  const retailers = [...byRetailer.values()];
  const pairs = [];
  for (let i = 0; i < retailers.length; i++) {
    for (let j = i + 1; j < retailers.length; j++) {
      const a = retailers[i], b = retailers[j];
      const commonKeys = [...a.prices.keys()].filter((k) => b.prices.has(k));
      if (commonKeys.length < 4) continue; // too few shared points for a meaningful r
      const xs = commonKeys.map((k) => a.prices.get(k));
      const ys = commonKeys.map((k) => b.prices.get(k));
      pairs.push({ a: a.retailerId, b: b.retailerId, r: pearsonCorrelation(xs, ys), samples: commonKeys.length });
    }
  }
  return pairs.sort((x, y) => Math.abs(y.r) - Math.abs(x.r));
}

function correlationLabel(r) {
  if (r >= 0.7) return { text: 'Moves together', cls: 'focus-change-up' };
  if (r <= -0.5) return { text: 'Inverse pricing', cls: 'focus-change-down' };
  if (Math.abs(r) < 0.3) return { text: 'Independent', cls: 'focus-change-flat' };
  return { text: 'Weak link', cls: 'focus-change-flat' };
}

function renderRetailerCorrelations(pairs) {
  const body = document.getElementById('correlationTableBody');
  if (!body) return;
  if (!pairs.length) {
    body.innerHTML = '<div class="empty-state">Need 2+ retailers with 4+ shared product/date prices to correlate.</div>';
    return;
  }
  const header = ['Retailer Pair', 'Correlation (r)', 'Reading', 'Shared Prices'];
  body.innerHTML = `<table class="focus-tbl"><thead><tr>${header.map((h) => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>` +
    pairs.map((p) => {
      const label = correlationLabel(p.r);
      return `<tr>
        <td>${createRetailerPill(p.a)} <span class="ana-metric-note">vs</span> ${createRetailerPill(p.b)}</td>
        <td>${p.r.toFixed(2)}</td>
        <td class="${label.cls}">${label.text}</td>
        <td>${p.samples}</td>
      </tr>`;
    }).join('') + '</tbody></table>';
}

function renderAnalyticsTab() {
  const rangeValue = document.getElementById('anaRange')?.value || '30';
  const rangeLabel = rangeValue === 'all' ? 'All time' : `Last ${rangeValue} days`;
  const subEl = document.getElementById('anaSub');
  if (subEl) subEl.textContent = rangeLabel;

  const rows = getAnalyticsRows(rangeValue);

  if (!rows.length) {
    renderProductStatsTable([]);
    renderRetailerPositioning([]);
    renderAnomalies([]);
    renderTrends([]);
    renderOriginAnalysis([]);
    renderRetailerCorrelations([]);
    renderBasketResults();
    return;
  }

  const dates = [...new Set(rows.map((r) => r.date))].sort();

  renderProductStatsTable(computeProductStats(rows));
  renderRetailerPositioning(computeRetailerPositioning(rows));
  renderAnomalies(detectAnomalies(rows));
  renderTrends(computeProductTrends(rows, dates));
  renderOriginAnalysis(computeOriginAnalysis(rows));
  renderRetailerCorrelations(computeRetailerCorrelations(rows));
  renderBasketResults();
}

/* ============================== IMPORT IMAGES / OCR TAB ============================== */

let ocrSelectedFile = null;
let ocrResults = [];

function handleOcrFile(file) {
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    setStatus('Please choose an image file (JPG, PNG, JPEG, WEBP).', 'err');
    return;
  }

  ocrSelectedFile = file;

  const reader = new FileReader();
  reader.onload = (event) => {
    const img = document.getElementById('previewImage');
    const card = document.getElementById('previewCard');
    if (img) img.src = event.target.result;
    if (card) card.style.display = 'block';
  };
  reader.readAsDataURL(file);

  const status = document.getElementById('ocrStatus');
  if (status) status.textContent = `Selected: ${file.name}. Click "Scan Image" to run OCR.`;
}

function renderOcrResults(items) {
  const body = document.getElementById('ocrBody');
  if (!body) return;

  if (!items.length) {
    body.innerHTML = '<tr><td colspan="8"><div class="empty-state">📸<br><br>No text/prices detected in this image.</div></td></tr>';
    return;
  }

  body.innerHTML = items.map((item, index) => {

    let priceClass = "price-same";

    if (item.previous_price !== undefined &&
        item.previous_price !== null &&
        item.previous_price > 0) {

        if (item.price > item.previous_price) {
            priceClass = "price-up";      // Price increased → Red
        } else if (item.price < item.previous_price) {
            priceClass = "price-down";    // Price decreased → Green
        }
    }

    return `
    <tr>
      <td>${index + 1}</td>
      <td><b>${escapeHtml(item.country)}</b></td>
      <td><span class="rpill">${escapeHtml(item.shipment)}</span></td>
      <td><div class="prod-name">${escapeHtml(item.product)}</div></td>
      <td>${escapeHtml(item.weight)}</td>
      <td>${escapeHtml(item.packing)}</td>

      <td>
        <div class="price-box ${priceClass}">
          <span class="price-num">
            ${item.price > 0 ? item.price.toFixed(2) : 'NA'}
          </span>
        </div>
      </td>

      <td>
        ${item.price > 0
            ? '<span style="color:var(--green)">✓ Valid</span>'
            : '<span style="color:var(--orange)">⚠ NA / Unpriced</span>'}
      </td>
    </tr>
    `;
}).join('');
}

function updateOcrStats(items, overallConfidence, imageCount) {
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
  setText('ocrImages', imageCount);
  setText('ocrProducts', items.length);
  setText('ocrPrices', items.filter((item) => item.price).length);
  setText('ocrConfidence', `${Math.round(overallConfidence || 0)}%`);
}

function ocrResultsToCsv() {
  if (!ocrResults.length) return '';
  // Expanded header array matching your sheet's columns
  const headers = ['#', 'Country', 'Shipment', 'Product', 'Weight', 'Packing', 'Price (AED)', 'Confidence %'];
  const rows = ocrResults.map((item, index) => [
    index + 1,
    item.country,
    item.shipment,
    item.product,
    item.weight,
    item.packing,
    item.price ? item.price.toFixed(2) : 'NA',
    Math.round(item.confidence),
  ]);
  return toCsv(headers, rows);
}

function clearOcr() {
  ocrSelectedFile = null;
  ocrResults = [];

  const input = document.getElementById('ocrImage');
  if (input) input.value = '';

  const card = document.getElementById('previewCard');
  if (card) card.style.display = 'none';
  const img = document.getElementById('previewImage');
  if (img) img.src = '';

  const status = document.getElementById('ocrStatus');
  if (status) status.textContent = 'Ready. Select an image to begin OCR.';

  renderOcrResults([]);
  updateOcrStats([], 0, 0);
}

function bindOCR() {
  const uploadBox = document.getElementById('uploadBox');
  const fileInput = document.getElementById('ocrImage');
  const btnOCR = document.getElementById('btnOCR');
  const btnClearOCR = document.getElementById('btnClearOCR');

  if (!uploadBox || !fileInput || !btnOCR) return;

  // Track scanning state to avoid async race conditions
  let isScanning = false;
  let currentScanId = 0;

  // File picked via "Browse Files"
  fileInput.addEventListener('change', (event) => {
    if (event.target.files?.[0]) {
      handleOcrFile(event.target.files[0]);
    }
  });

  // Drag & drop handlers
  uploadBox.addEventListener('dragover', (event) => {
    event.preventDefault();
    uploadBox.classList.add('drag-over');
  });

  uploadBox.addEventListener('dragleave', () => {
    uploadBox.classList.remove('drag-over');
  });

  uploadBox.addEventListener('drop', (event) => {
    event.preventDefault();
    uploadBox.classList.remove('drag-over');
    
    // Prevent dropping files if a scan is already running
    if (isScanning) {
      setStatus('Please wait until the current scan completes.', 'err');
      return;
    }

    const file = event.dataTransfer.files?.[0];
    if (file) {
      fileInput.files = event.dataTransfer.files; // Sync file input state
      handleOcrFile(file);
    }
  });

  // Run OCR Engine Pipeline
  btnOCR.addEventListener('click', async () => {
    if (!ocrSelectedFile) {
      setStatus('Select an image first.', 'err');
      return;
    }
    
    if (isScanning) return; // Guard duplicate execution clicks

    const status = document.getElementById('ocrStatus');
    
    // Initialize loading states
    isScanning = true;
    btnOCR.disabled = true;
    if (btnClearOCR) btnClearOCR.disabled = true;
    
    // Increment scan identifier token to track async execution context
    const thisScanId = ++currentScanId;
    
    if (status) {
      status.className = 'status-line';
      status.textContent = 'Processing matrix filters & scanning document…';
    }

    try {
      const data = await ocrScan(ocrSelectedFile);

      // Guard: Check if user changed the view/cleared data while waiting for response
      if (thisScanId !== currentScanId) return;

      if (!data || data.ok === false) {
        throw new Error(data?.error || 'OCR core processing layer failure.');
      }

      const rawProducts = data.products || [];
      
      // === REPLACE THE OLD MAP BLOCK WITH THIS UPDATED ONE ===
      ocrResults = rawProducts.map((item) => ({
        country: item.country || '—',
        shipment: item.shipment || '—',
        product: item.product || '—',
        weight: item.weight || '—',
        packing: item.packing || '—',
        price: parseFloat(item.price) || 0,
        confidence: Math.min(100, Math.max(0, (Number(item.confidence) || 0) * 100)), // Clamp percentages between 0-100
      }));
      // =======================================================

      const overallConfidence = ocrResults.length
        ? ocrResults.reduce((sum, item) => sum + item.confidence, 0) / ocrResults.length
        : 0;

      // Update structural presentation layers
      renderOcrResults(ocrResults);
      updateOcrStats(ocrResults, overallConfidence, 1);
      
      if (status) {
        status.className = 'status-line ok';
        status.textContent = ocrResults.length
          ? `✓ Extraction complete: Found ${ocrResults.length} item(s).`
          : '⚠ Matrix structural analysis complete, but no valid price blocks were found.';
      }
    } catch (error) {
      // Guard against old async calls overwriting current error state
      if (thisScanId !== currentScanId) return;

      console.error('[MarketPulse] OCR Module Integration Failure:', error);
      if (status) {
        status.className = 'status-line error';
        status.textContent = `✗ ${error.message}`;
      }
      setStatus(`✗ OCR failed: ${error.message}`, 'err');
    } finally {
      // Release engine locks safely if this context is still relevant
      if (thisScanId === currentScanId) {
        isScanning = false;
        btnOCR.disabled = currentUser?.role === 'viewer';
        if (btnClearOCR) btnClearOCR.disabled = false;
      }
    }
  });

  // Safe wrapper execution for cleaner implementation
  btnClearOCR?.addEventListener('click', () => {
    currentScanId++; // Invalidate running async promises on reset
    isScanning = false;
    btnOCR.disabled = currentUser?.role === 'viewer';
    if (btnClearOCR) btnClearOCR.disabled = false;
    clearOcr();
  });

  // Data Export Pipelines
  document.getElementById('ocrCsvBtn')?.addEventListener('click', () => {
    if (!ocrResults || !ocrResults.length) { 
      setStatus('No data available to export yet.', 'err'); 
      return; 
    }
    downloadCsv('marketpulse-ocr-results.csv', ocrResultsToCsv());
    setStatus('✓ CSV export saved successfully', 'ok');
  });

  document.getElementById('ocrCopyBtn')?.addEventListener('click', () => {
    const csv = ocrResultsToCsv();
    if (!csv) { 
      setStatus('No tabular text compiled to copy.', 'err'); 
      return; 
    }
    navigator.clipboard.writeText(csv)
      .then(() => setStatus('✓ Structured schema copied to clipboard', 'ok'))
      .catch((err) => console.error('Clipboard write permission denied:', err));
  });
}

/* ============================== SCHEDULER TAB ============================== */
// Client-side automatic fetch scheduler. There's no backend cron wired up, so
// this runs entirely via a JS timer in this browser tab: it can be
// backgrounded, but closing the tab/browser pauses it. Config and run history
// live in localStorage so they survive a page reload.

const SCHED_CONFIG_KEY = 'marketpulse_scheduler_config_v1';
const SCHED_LOG_KEY = 'marketpulse_scheduler_log_v1';
const SCHED_TICK_MS = 1000;
let schedTickHandle = null;

function loadSchedConfig() {
  const defaults = { enabled: false, frequency: 'daily', time: '09:00', intervalHours: 6, lastRun: null };
  try {
    const raw = localStorage.getItem(SCHED_CONFIG_KEY);
    return raw ? { ...defaults, ...JSON.parse(raw) } : defaults;
  } catch {
    return defaults;
  }
}

function saveSchedConfig(config) {
  try { localStorage.setItem(SCHED_CONFIG_KEY, JSON.stringify(config)); }
  catch (error) { console.warn('[MarketPulse] Could not persist scheduler config:', error); }
}

function loadSchedLog() {
  try { return JSON.parse(localStorage.getItem(SCHED_LOG_KEY) || '[]'); } catch { return []; }
}

function pushSchedLog(entry) {
  const log = loadSchedLog();
  log.unshift(entry);
  try { localStorage.setItem(SCHED_LOG_KEY, JSON.stringify(log.slice(0, 25))); }
  catch (error) { console.warn('[MarketPulse] Could not persist scheduler log:', error); }
}

// Daily mode: next occurrence of the chosen time (today if still ahead, else
// tomorrow). Interval mode: lastRun + N hours (or "now" if it's never run).
function computeNextRun(config) {
  const now = new Date();
  if (config.frequency === 'interval') {
    const intervalMs = Math.max(1, Number(config.intervalHours) || 6) * 3600 * 1000;
    if (!config.lastRun) return now;
    return new Date(new Date(config.lastRun).getTime() + intervalMs);
  }
  const [hh, mm] = (config.time || '09:00').split(':').map(Number);
  const next = new Date(now);
  next.setHours(hh || 0, mm || 0, 0, 0);
  if (next <= now) next.setDate(next.getDate() + 1);
  return next;
}

function formatSchedDateTime(value) {
  if (!value) return '—';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function renderSchedLog() {
  const body = document.getElementById('schedLogBody');
  if (!body) return;
  const log = loadSchedLog();
  if (!log.length) {
    body.innerHTML = '<div class="empty-state">No runs yet. Enable the schedule, or hit "Run Now" to try one immediately.</div>';
    return;
  }
  const header = ['When', 'Trigger', 'Result'];
  body.innerHTML = `<table class="focus-tbl"><thead><tr>${header.map((h) => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>` +
    log.map((entry) => `<tr>
      <td>${escapeHtml(formatSchedDateTime(entry.time))}</td>
      <td>${escapeHtml(entry.trigger === 'manual' ? 'Manual (Run Now)' : 'Scheduled')}</td>
      <td class="${entry.ok ? 'sched-log-ok' : 'sched-log-err'}">${escapeHtml(entry.message || (entry.ok ? 'Success' : 'Failed'))}</td>
    </tr>`).join('') + '</tbody></table>';
}

function renderSchedulerUI() {
  const config = loadSchedConfig();

  const enabledEl = document.getElementById('schedEnabled');
  const labelEl = document.getElementById('schedToggleLabel');
  const freqEl = document.getElementById('schedFrequency');
  const timeEl = document.getElementById('schedTime');
  const intervalEl = document.getElementById('schedInterval');
  const timeField = document.getElementById('schedTimeField');
  const intervalField = document.getElementById('schedIntervalField');
  const statusValue = document.getElementById('schedStatusValue');
  const nextRunEl = document.getElementById('schedNextRun');
  const lastRunEl = document.getElementById('schedLastRun');

  if (enabledEl) enabledEl.checked = config.enabled;
  if (labelEl) labelEl.textContent = config.enabled ? 'On' : 'Off';
  if (freqEl) freqEl.value = config.frequency;
  if (timeEl) timeEl.value = config.time;
  if (intervalEl) intervalEl.value = config.intervalHours;
  if (timeField) timeField.style.display = config.frequency === 'daily' ? '' : 'none';
  if (intervalField) intervalField.style.display = config.frequency === 'interval' ? '' : 'none';

  if (statusValue) {
    statusValue.textContent = config.enabled ? 'Active' : 'Disabled';
    statusValue.className = `sched-status-value ${config.enabled ? 'sched-on' : 'sched-off'}`;
  }
  if (nextRunEl) nextRunEl.textContent = config.enabled ? formatSchedDateTime(computeNextRun(config)) : '—';
  if (lastRunEl) lastRunEl.textContent = config.lastRun ? formatSchedDateTime(config.lastRun) : 'Never';

  renderSchedLog();
}

// Reuses the exact same fetchLatestPrices() flow as the ▶ Fetch Prices
// button, so a scheduled run pulls fresh data through the normal job/poll
// pipeline and updates the Fetch & Analyse table the same way a manual click would.
async function runScheduledFetch(trigger) {
  const config = loadSchedConfig();
  try {
    await fetchLatestPrices();
    const statusLine = document.getElementById('statusLine');
    const failed = statusLine?.classList.contains('error');
    config.lastRun = new Date().toISOString();
    saveSchedConfig(config);
    pushSchedLog({ time: config.lastRun, trigger, ok: !failed, message: statusLine?.textContent || (failed ? 'Failed' : 'Success') });
  } catch (error) {
    config.lastRun = new Date().toISOString();
    saveSchedConfig(config);
    pushSchedLog({ time: config.lastRun, trigger, ok: false, message: error.message || 'Failed' });
  }
  renderSchedulerUI();
}

function tickScheduler() {
  const config = loadSchedConfig();
  if (!config.enabled) return;
  if (new Date() >= computeNextRun(config)) {
    runScheduledFetch('scheduled');
  } else if (document.getElementById('tab-scheduler')?.classList.contains('active')) {
    // Keep the "Next Run" readout live while the person is looking at this tab.
    const nextRunEl = document.getElementById('schedNextRun');
    if (nextRunEl) nextRunEl.textContent = formatSchedDateTime(computeNextRun(config));
  }
}

function bindScheduler() {
  document.getElementById('schedEnabled')?.addEventListener('change', (event) => {
    const config = loadSchedConfig();
    config.enabled = event.target.checked;
    saveSchedConfig(config);
    renderSchedulerUI();
  });

  document.getElementById('schedFrequency')?.addEventListener('change', (event) => {
    const config = loadSchedConfig();
    config.frequency = event.target.value;
    saveSchedConfig(config);
    renderSchedulerUI();
  });

  document.getElementById('schedTime')?.addEventListener('change', (event) => {
    const config = loadSchedConfig();
    config.time = event.target.value || '09:00';
    saveSchedConfig(config);
    renderSchedulerUI();
  });

  document.getElementById('schedInterval')?.addEventListener('change', (event) => {
    const config = loadSchedConfig();
    config.intervalHours = Math.min(24, Math.max(1, Number(event.target.value) || 6));
    saveSchedConfig(config);
    renderSchedulerUI();
  });

  document.getElementById('schedRunNowBtn')?.addEventListener('click', () => runScheduledFetch('manual'));

  document.getElementById('schedClearLogBtn')?.addEventListener('click', () => {
    localStorage.removeItem(SCHED_LOG_KEY);
    renderSchedLog();
  });

  renderSchedulerUI();
  if (schedTickHandle) clearInterval(schedTickHandle);
  schedTickHandle = setInterval(tickScheduler, SCHED_TICK_MS);
}

/* ============================== INIT ============================== */

async function init() {
  const user = await initAuth();
  if (!user) return; // initAuth() is already redirecting to /login
  bindUserMenu();

  loadState();
  setDateChip();
  bindTabs();
  bindProductsTab();
  wireCsvCopyButtons();
  bindOCR()
  bindScheduler();

  refreshProductDependents();

  try {
    lastDetail = await loadLatestFetch();
    console.log('[MarketPulse] Loaded Latest Fetch Results from MongoDB (/api/latest):', lastDetail);
    savePersistedDetail(lastDetail); // keep the local cache as an offline-friendly mirror only
    renderDetailTable(lastDetail);
  } catch (error) {
    console.warn('[MarketPulse] Could not load latest fetch snapshot from backend, falling back to local cache:', error);
    const persisted = loadPersistedDetail();
    lastDetail = persisted?.rows?.length ? persisted.rows : [];
    renderDetailTable(lastDetail);
  }

  document.getElementById('syncBtn')?.addEventListener('click', async (event) => {
    event.currentTarget.classList.add('loading');
    await checkBackend();
    await loadPivot();
    populateVariationSelects();
    event.currentTarget.classList.remove('loading');
    setStatus('✓ Synced with the Flask backend', 'ok');
  });
  document.getElementById('backendPill')?.addEventListener('click', () => checkBackend(true));
  document.getElementById('fetchBtn')?.addEventListener('click', fetchLatestPrices);

  document.getElementById('varRetailer')?.addEventListener('change', renderVariationTab);
  document.getElementById('varProduct')?.addEventListener('change', renderVariationTab);
  document.getElementById('varRange')?.addEventListener('change', renderVariationTab);
  document.getElementById('varChartType')?.addEventListener('change', renderVariationTab);

  document.getElementById('anaRange')?.addEventListener('change', renderAnalyticsTab);

  document.addEventListener('click', closeAllDropdowns);

  await checkBackend();
  const meta = await loadMeta().catch(() => ({ retailers: [], products: [] }));
  appState.meta = meta;
  renderFetchDropdowns();
  populateVariationSelects();
  await loadPivot();
}

init();