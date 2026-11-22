/**
 * Financial Model Admin Portal - Main JavaScript
 * Handles navigation, API communication, and UI interactions
 */

// ============================================
// Configuration & State
// ============================================
const API_BASE = '/admin/api';
const API_PLATFORM = '/v1';

let currentPage = 'dashboard';
let currentUser = null;
let sidebarOpen = false;
let userMenuOpen = false;

// ============================================
// DOM Elements
// ============================================
const sidebar = document.getElementById('sidebar');
const mainContent = document.getElementById('mainContent');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarOverlay = document.getElementById('sidebarOverlay');
const userMenu = document.getElementById('userMenu');
const userDropdown = document.getElementById('userDropdown');
const toastContainer = document.getElementById('toastContainer');
const modalContainer = document.getElementById('modalContainer');
const breadcrumb = document.getElementById('breadcrumb');

// ============================================
// Utility Functions
// ============================================

/** Show toast notification */
function showToast(message, type = 'info', duration = 5000) {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span class="toast-message">${escapeHtml(message)}</span>
    <button class="toast-close" onclick="this.parentElement.remove()" aria-label="Dismiss">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
      </svg>
    </button>
  `;
  toastContainer.appendChild(toast);
  if (duration > 0) {
    setTimeout(() => toast.remove(), duration);
  }
}

/** Escape HTML to prevent XSS */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/** Format date for display */
function formatDate(timestamp) {
  if (!timestamp) return '—';
  const date = new Date(timestamp * 1000);
  return date.toLocaleDateString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit'
  });
}

/** Format relative time */
function formatRelativeTime(timestamp) {
  if (!timestamp) return '—';
  const now = Date.now() / 1000;
  const diff = now - timestamp;
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return formatDate(timestamp);
}

/** Get auth headers */
function getAuthHeaders() {
  const token = localStorage.getItem('admin_token');
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

/** API request wrapper */
async function apiRequest(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeaders(),
    ...options.headers,
  };

  try {
    const response = await fetch(url, { ...options, headers });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(data.detail || `HTTP ${response.status}`);
    }
    return data;
  } catch (error) {
    console.error(`API Error (${endpoint}):`, error);
    throw error;
  }
}

/** Platform API request (non-admin) */
async function platformRequest(endpoint, options = {}) {
  const url = `${API_PLATFORM}${endpoint}`;
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeaders(),
    ...options.headers,
  };

  try {
    const response = await fetch(url, { ...options, headers });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(data.detail || `HTTP ${response.status}`);
    }
    return data;
  } catch (error) {
    console.error(`Platform API Error (${endpoint}):`, error);
    throw error;
  }
}

/** Create modal */
function createModal(title, body, footer = '') {
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="modal-header">
        <h3 class="modal-title" id="modal-title">${escapeHtml(title)}</h3>
        <button class="modal-close" onclick="closeModal(this)" aria-label="Close">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
      <div class="modal-body">${body}</div>
      ${footer ? `<div class="modal-footer">${footer}</div>` : ''}
    </div>
  `;
  modalContainer.appendChild(modal);
  requestAnimationFrame(() => modal.classList.add('open'));
  return modal;
}

/** Close modal */
function closeModal(button) {
  const modal = button.closest('.modal-overlay');
  if (modal) {
    modal.classList.remove('open');
    setTimeout(() => modal.remove(), 200);
  }
}

/** Close modal on overlay click */
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay')) {
    closeModal(e.target.querySelector('.modal-close') || { closest: () => e.target });
  }
});

/** Show loading state */
function showLoading(container) {
  container.innerHTML = `
    <div class="loading">
      <div class="spinner"></div>
    </div>
  `;
}

/** Show empty state */
function showEmptyState(container, title, description, action = null) {
  container.innerHTML = `
    <div class="empty-state">
      ${action ? `<button class="btn btn-primary" onclick="${action}">${escapeHtml(action.text)}</button>` : ''}
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
        <rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>
      </svg>
      <div class="empty-state-title">${escapeHtml(title)}</div>
      <div class="empty-state-description">${escapeHtml(description)}</div>
      ${action ? `<button class="btn btn-primary mt-3" onclick="${action.onclick}">${escapeHtml(action.text)}</button>` : ''}
    </div>
  `;
}

/** Debounce function */
function debounce(fn, delay) {
  let timeoutId;
  return (...args) => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => fn(...args), delay);
  };
}

// ============================================
// Navigation & Layout
// ============================================

/** Toggle sidebar on mobile */
function toggleSidebar() {
  sidebarOpen = !sidebarOpen;
  sidebar.classList.toggle('open', sidebarOpen);
  sidebarOverlay.classList.toggle('open', sidebarOpen);
  sidebarToggle.setAttribute('aria-expanded', sidebarOpen);
}

/** Toggle user dropdown */
function toggleUserMenu() {
  userMenuOpen = !userMenuOpen;
  userMenu.classList.toggle('open', userMenuOpen);
  userMenu.querySelector('.user-button').setAttribute('aria-expanded', userMenuOpen);
}

/** Close user menu on outside click */
document.addEventListener('click', (e) => {
  if (!userMenu.contains(e.target)) {
    userMenuOpen = false;
    userMenu.classList.remove('open');
    userMenu.querySelector('.user-button').setAttribute('aria-expanded', 'false');
  }
});

/** Navigate to page */
async function navigate(page) {
  if (page === currentPage) return;

  // Update nav items
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.dataset.page === page);
  });

  // Update breadcrumb
  const pageNames = {
    dashboard: 'Dashboard',
    legal: 'Legal Documents',
    email: 'Email Templates',
    sso: 'SSO Providers',
    plans: 'Plans & Pricing',
    billing: 'Billing Settings',
    roles: 'Roles & Permissions',
    'account-kinds': 'Account Kinds',
    metrics: 'Metrics & Metering',
    endpoints: 'API Endpoints',
    messages: 'Messages',
    settings: 'Platform Settings',
  };
  breadcrumb.textContent = pageNames[page] || page;

  // Load page content
  currentPage = page;
  await loadPage(page);

  // Close mobile sidebar
  if (sidebarOpen) toggleSidebar();

  // Scroll to top
  mainContent.scrollTop = 0;
}

/** Load page content via fetch */
async function loadPage(page) {
  showLoading(document.getElementById('adminContent'));

  try {
    const response = await fetch(`/admin/pages/${page}`);
    if (response.ok) {
      const html = await response.text();
      document.getElementById('adminContent').innerHTML = html;
      // Initialize page-specific JS
      if (window[`init${page.charAt(0).toUpperCase() + page.slice(1)}Page`]) {
        window[`init${page.charAt(0).toUpperCase() + page.slice(1)}Page`]();
      }
    } else {
      throw new Error('Page not found');
    }
  } catch (error) {
    console.error('Failed to load page:', error);
    document.getElementById('adminContent').innerHTML = `
      <div class="card">
        <div class="card-body text-center" style="padding: var(--space-12);">
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom: var(--space-4); opacity: 0.5;">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
          <h3 style="margin-bottom: var(--space-2);">Failed to load page</h3>
          <p style="color: var(--color-fg-muted);">${escapeHtml(error.message)}</p>
          <button class="btn btn-primary mt-4" onclick="navigate('${page}')">Retry</button>
        </div>
      </div>
    `;
  }
}

/** Refresh current page data */
function refreshData() {
  if (window[`refresh${currentPage.charAt(0).toUpperCase() + currentPage.slice(1)}`]) {
    window[`refresh${currentPage.charAt(0).toUpperCase() + currentPage.slice(1)}`]();
  } else {
    navigate(currentPage);
  }
}

/** Logout */
async function logout() {
  localStorage.removeItem('admin_token');
  localStorage.removeItem('admin_user');
  window.location.href = '/admin/login';
}

// ============================================
// Form Helpers
// ============================================

/** Serialize form to JSON */
function serializeForm(form) {
  const data = {};
  new FormData(form).forEach((value, key) => {
    if (key.endsWith('[]')) {
      const cleanKey = key.slice(0, -2);
      if (!data[cleanKey]) data[cleanKey] = [];
      data[cleanKey].push(value);
    } else {
      data[key] = value;
    }
  });
  return data;
}

/** Populate form from object */
function populateForm(form, data) {
  Object.entries(data).forEach(([key, value]) => {
    const field = form.querySelector(`[name="${key}"]`);
    if (!field) return;

    if (field.type === 'checkbox') {
      field.checked = Boolean(value);
    } else if (field.type === 'radio') {
      const radio = form.querySelector(`[name="${key}"][value="${value}"]`);
      if (radio) radio.checked = true;
    } else if (field.tagName === 'SELECT' && field.multiple) {
      Array.from(field.options).forEach(opt => {
        opt.selected = Array.isArray(value) && value.includes(opt.value);
      });
    } else {
      field.value = value ?? '';
    }
  });
}

/** Validate form */
function validateForm(form) {
  let valid = true;
  form.querySelectorAll('[required]').forEach(field => {
    if (!field.value.trim()) {
      showFieldError(field, 'This field is required');
      valid = false;
    } else {
      clearFieldError(field);
    }
  });
  return valid;
}

function showFieldError(field, message) {
  clearFieldError(field);
  field.style.borderColor = 'var(--color-danger)';
  const error = document.createElement('div');
  error.className = 'form-error';
  error.textContent = message;
  field.parentNode.appendChild(error);
}

function clearFieldError(field) {
  field.style.borderColor = '';
  const error = field.parentNode.querySelector('.form-error');
  if (error) error.remove();
}

// ============================================
// Table Helpers
// ============================================

/** Render data table */
function renderTable(container, columns, rows, options = {}) {
  const { actions, emptyMessage = 'No data available', keyField = 'id' } = options;

  if (!rows.length) {
    container.innerHTML = `
      <div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>
        </svg>
        <div class="empty-state-title">No Data</div>
        <div class="empty-state-description">${escapeHtml(emptyMessage)}</div>
      </div>
    `;
    return;
  }

  const thead = columns.map(col => `<th>${escapeHtml(col.label)}</th>`).join('');
  const tbody = rows.map(row => {
    const cells = columns.map(col => {
      const value = row[col.key];
      if (col.render) return col.render(value, row);
      if (value === null || value === undefined) return '<span class="text-muted">—</span>';
      if (typeof value === 'boolean') return `<span class="badge ${value ? 'badge-success' : 'badge-neutral'}">${value ? 'Yes' : 'No'}</span>`;
      if (col.format === 'date') return formatDate(value);
      if (col.format === 'relative') return formatRelativeTime(value);
      return escapeHtml(String(value));
    }).join('');

    const actionCells = actions ? actions.map(a => {
      const onclick = typeof a.onclick === 'function' ? a.onclick(row) : a.onclick.replace('{id}', row[keyField]);
      return `<button class="btn btn-ghost btn-sm" onclick="${onclick}" title="${escapeHtml(a.title)}">${a.icon}</button>`;
    }).join('') : '';

    return `<tr>${cells}${actionCells ? `<td>${actionCells}</td>` : ''}</tr>`;
  }).join('');

  container.innerHTML = `
    <div class="table-container">
      <table class="table">
        <thead><tr>${thead}${actions ? '<th></th>' : ''}</tr></thead>
        <tbody>${tbody}</tbody>
      </table>
    </div>
  `;
}

// ============================================
// Authentication Check
// ============================================

async function checkAuth() {
  const token = localStorage.getItem('admin_token');
  if (!token) {
    window.location.href = '/admin/login';
    return false;
  }

  try {
    const user = await platformRequest('/me');
    currentUser = user;
    updateUserUI(user);
    return true;
  } catch (error) {
    localStorage.removeItem('admin_token');
    localStorage.removeItem('admin_user');
    window.location.href = '/admin/login';
    return false;
  }
}

function updateUserUI(user) {
  const tenant = user.tenant || {};
  const name = user.user?.name || tenant.name || 'Admin';
  const email = user.user?.email || 'admin@example.com';
  const initials = name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);

  document.getElementById('userAvatar').textContent = initials;
  document.getElementById('userName').textContent = name;
  document.getElementById('dropdownUserName').textContent = name;
  document.getElementById('dropdownUserEmail').textContent = email;
}

// ============================================
// Keyboard Shortcuts
// ============================================

document.addEventListener('keydown', (e) => {
  // Ctrl/Cmd + K for search (future)
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault();
    // TODO: Open search modal
  }

  // Escape to close modals/dropdowns
  if (e.key === 'Escape') {
    if (userMenuOpen) toggleUserMenu();
    const openModal = document.querySelector('.modal-overlay.open');
    if (openModal) closeModal(openModal.querySelector('.modal-close'));
    if (sidebarOpen) toggleSidebar();
  }
});

// ============================================
// Initialize
// ============================================

document.addEventListener('DOMContentLoaded', async () => {
  // Check authentication
  const authenticated = await checkAuth();
  if (!authenticated) return;

  // Load initial page
  await navigate(currentPage);
});

// Export for global access
window.AdminAPI = { apiRequest, platformRequest, showToast, createModal, closeModal };
window.navigate = navigate;
window.refreshData = refreshData;
window.logout = logout;