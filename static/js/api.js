import { appState, setStatus, toDateKey } from './utils.js';

const API_BASE = '';

// Every API call goes through this so a lost/expired session bounces the
// user back to /login instead of the page silently failing to load data.
async function handleAuthRedirect(response) {
  if (response.status === 401) {
    let body = {};
    try { body = await response.clone().json(); } catch { /* not JSON */ }
    if (body.auth_required) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      // Never resolves - we're navigating away.
      return new Promise(() => {});
    }
  }
  return response;
}

export async function getCurrentUser() {
  const response = await fetch(`${API_BASE}/api/auth/me?_=${Date.now()}`, { cache: 'no-store' });
  if (!response.ok) throw new Error('Unable to check login status.');
  const data = await response.json();
  return data.user || null;
}

export async function login(username, password) {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    throw new Error(data.error || 'Invalid username or password.');
  }
  return data.user;
}

export async function logout() {
  await fetch(`${API_BASE}/api/auth/logout`, { method: 'POST' });
}

export async function changeOwnPassword(currentPassword, newPassword) {
  const response = await fetch(`${API_BASE}/api/auth/password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to change password.');
  return true;
}

// Updates the logged-in user's own username and/or password in one call.
// Pass null/undefined/empty-string for newUsername or newPassword to leave
// that field unchanged. Returns the updated { id, username, role } user.
export async function updateOwnProfile(currentPassword, newUsername, newPassword) {
  const response = await fetch(`${API_BASE}/api/auth/profile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      current_password: currentPassword,
      new_username: newUsername || undefined,
      new_password: newPassword || undefined,
    }),
  });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to update account.');
  return data.user;
}

// ---- Admin: user management ----

export async function listUsers(filters = {}) {
  const params = new URLSearchParams();
  if (filters.search) params.set('search', filters.search);
  if (filters.role && filters.role !== 'all') params.set('role', filters.role);
  if (filters.status && filters.status !== 'all') params.set('status', filters.status);
  params.set('_', String(Date.now()));

  const response = await fetch(`${API_BASE}/api/users?${params.toString()}`, { cache: 'no-store' });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to load users.');
  return data.users;
}

export async function getUserStats() {
  const response = await fetch(`${API_BASE}/api/users/stats?_=${Date.now()}`, { cache: 'no-store' });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to load user stats.');
  return data.stats;
}

export async function getDbStatus() {
  const response = await fetch(`${API_BASE}/api/admin/db/status?_=${Date.now()}`, { cache: 'no-store' });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to check database status.');
  return data.status;
}

export async function createUser(username, password, role) {
  const response = await fetch(`${API_BASE}/api/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, role }),
  });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to create user.');
  return data.users;
}

export async function updateUserRole(userId, role) {
  const response = await fetch(`${API_BASE}/api/users/${userId}/role`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to update role.');
  return data.users;
}

export async function setUserActive(userId, isActive) {
  const response = await fetch(`${API_BASE}/api/users/${userId}/active`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: isActive }),
  });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to update user status.');
  return data.users;
}

export async function resetUserPassword(userId, password) {
  const response = await fetch(`${API_BASE}/api/users/${userId}/password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to reset password.');
  return true;
}

export async function deleteUser(userId) {
  const response = await fetch(`${API_BASE}/api/users/${userId}`, { method: 'DELETE' });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to delete user.');
  return data.users;
}

export async function loadMeta() {
  const response = await fetch(`${API_BASE}/api/meta?_=${Date.now()}`, { cache: 'no-store' });
  await handleAuthRedirect(response);
  if (!response.ok) {
    throw new Error('Unable to load retailer and product metadata from the Flask backend.');
  }
  const data = await response.json();
  appState.meta = {
    retailers: data.retailers || [],
    products: data.products || [],
  };
  return appState.meta;
}

export async function createProduct(name, emoji) {
  const response = await fetch(`${API_BASE}/api/products`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, emoji }),
  });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to add product.');
  return data.product;
}

export async function deleteProduct(productId) {
  const response = await fetch(`${API_BASE}/api/products/${encodeURIComponent(productId)}`, { method: 'DELETE' });
  await handleAuthRedirect(response);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to delete product.');
  return true;
}

export async function fetchPrices(payload) {
  const response = await fetch(`${API_BASE}/api/fetch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  await handleAuthRedirect(response);

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || 'The scrape request failed.');
  }

  const data = await response.json();
  return (data.results || []).map((item) => mapScrapeResult(item));
}

export async function createJob(payload) {
  const response = await fetch(`${API_BASE}/api/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  await handleAuthRedirect(response);

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || 'Unable to start the background scrape job.');
  }

  return response.json();
}

export async function pollJob(jobId, onProgress, attempts = 6) {
  // Transient failures (proxy 502/503 while the host is under load, reset
  // connections) are retried with backoff - a single bad poll must not kill
  // a minutes-long job. 4xx is different: the job is genuinely gone
  // (e.g. the server restarted and its in-memory jobs were wiped), so fail
  // fast with a message that says to just fetch again.
  let lastError = new Error('The job could not be polled.');
  for (let i = 0; i < attempts; i++) {
    let response;
    try {
      response = await fetch(`${API_BASE}/api/jobs/${jobId}?_=${Date.now()}`, { cache: 'no-store' });
    } catch {
      lastError = new Error('Lost connection while polling the job.');
      await new Promise((resolve) => setTimeout(resolve, 2000));
      continue;
    }
    await handleAuthRedirect(response);
    if (response.ok) {
      const data = await response.json();
      if (typeof onProgress === 'function') {
        onProgress(data);
      }
      return data;
    }
    if (response.status >= 400 && response.status < 500) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || 'The job is no longer on the server (it may have restarted). Fetch again.');
    }
    const error = await response.json().catch(() => ({}));
    lastError = new Error(error.error || 'The job could not be polled.');
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw lastError;
}

export async function loadHistory(payload = {}) {
  const params = new URLSearchParams();
  Object.entries(payload).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value));
    }
  });
  params.set('_', String(Date.now()));
  const query = params.toString();
  const response = await fetch(`${API_BASE}/api/history?${query}`, { cache: 'no-store' });
  await handleAuthRedirect(response);
  if (!response.ok) {
    throw new Error('Unable to load scrape history.');
  }
  return response.json();
}

export function mapScrapeResult(item) {
  const price = parseFloat(String(item.per_kg_price || '').match(/\d+(?:\.\d+)?/)?.[0] || '0');
  const origin = item.origin_country ?? item.originCountry ?? item.country ?? item.origin
    ?? item.country_of_origin ?? item.countryOfOrigin ?? item.source_country ?? item.product_origin ?? '';
  return {
    date: toDateKey(new Date()),
    fetched_at: new Date().toISOString(),
    retailer: String(item.supermarket || '').trim(),
    product: String(item.product_label || item.product_id || '').trim(),
    price: Number.isFinite(price) ? price : 0,
    origin_country: String(origin).trim() || '—',
    unit: 'per kg',
    currency: 'AED',
    source: 'scraper',
    url: String(item.url || '').trim(),
  };
}

// Latest-fetch snapshot from MongoDB (one row per product+retailer,
// always the most recent scrape - see /api/latest and database/prices.py's
// save_latest_fetch). This is what backs the "Latest Fetch Results" table now,
// instead of the old browser-only localStorage cache.
export async function loadLatestFetch() {
  const response = await fetch(`${API_BASE}/api/latest?_=${Date.now()}`, { cache: 'no-store' });
  await handleAuthRedirect(response);
  if (!response.ok) {
    throw new Error('Unable to load the latest fetch snapshot.');
  }
  const data = await response.json();
  return (data.rows || []).map((item) => mapLatestFetchRow(item));
}

export function mapLatestFetchRow(item) {
  const price = parseFloat(String(item.per_kg_price || '').match(/\d+(?:\.\d+)?/)?.[0] || '0');
  return {
    date: item.date || toDateKey(new Date()),
    fetched_at: item.timestamp || item.date || new Date().toISOString(),
    retailer: String(item.supermarket || '').trim(),
    product: String(item.product_label || item.product_id || '').trim(),
    price: Number.isFinite(price) ? price : 0,
    origin_country: String(item.country_of_origin || '').trim() || '—',
    unit: 'per kg',
    currency: 'AED',
    source: 'mongodb',
    url: String(item.url || '').trim(),
  };
}

export async function ocrScan(file) {
  const formData = new FormData();
  formData.append('image', file);

  const response = await fetch(`${API_BASE}/api/ocr`, {
    method: 'POST',
    body: formData, // no Content-Type header — the browser sets the multipart boundary itself
  });
  await handleAuthRedirect(response);

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || 'OCR request failed.');
  }

  return response.json(); // expected: { items: [{ product, price, confidence }], confidence }
}

// Only the most recent 30 days are shown in the Fetch & Analyse pivot table.
// This is a display-only trim: the backend/MongoDB still hold (and return)
// the full price_history, we just slice the tail off here before rendering.
const HISTORY_DISPLAY_DAYS = 30;

function last30Days(dates) {
  const sorted = [...(dates || [])].sort(); // ISO yyyy-mm-dd sorts chronologically as strings
  return sorted.slice(-HISTORY_DISPLAY_DAYS);
}

export function mapHistoryResponse(data) {
  const allDates = data.dates || [];
  const dates = last30Days(allDates);
  const dateSet = new Set(dates);

  const rows = (data.rows || []).map((row) => {
    const prices = row.prices || {};
    const origins = row.origins || {};
    const filteredPrices = {};
    const filteredOrigins = {};
    Object.keys(prices).forEach((date) => {
      if (dateSet.has(date)) filteredPrices[date] = prices[date];
    });
    Object.keys(origins).forEach((date) => {
      if (dateSet.has(date)) filteredOrigins[date] = origins[date];
    });
    return { ...row, prices: filteredPrices, origins: filteredOrigins };
  }).filter((row) => Object.keys(row.prices).length > 0); // drop rows with no data in the window

  const distinctProducts = new Set(rows.map((r) => r.product_id || r.product_label));
  const distinctRetailers = new Set(rows.map((r) => r.retailer_id || r.retailer_label || r.retailer));

  return {
    dates,
    rows,
    counts: {
      dates: dates.length,
      products: distinctProducts.size,
      retailers: distinctRetailers.size,
    },
  };
}