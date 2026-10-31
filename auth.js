/*
 * Xingjian - login, account management, and server sync.
 *
 * The prototype kept everything in localStorage. This bridge adds a
 * session cookie and mirrors the same document to /api/data, without
 * rewriting the prototype's render functions:
 *
 *   - auth.js defines window.XJ before the prototype's inline script;
 *   - index.html reads/writes localStorage through XJ.dataKey();
 *   - save() calls XJ.queueSave() so every user edit is debounced to the
 *     server;
 *   - every account action calls the JSON API in server.py.
 *
 * When the service cannot be reached, the page offers a local-only demo
 * mode so the prototype still opens from a static file server.
 */
(function () {
  'use strict';

  const API = {
    me: '/api/auth/me',
    login: '/api/auth/login',
    register: '/api/auth/register',
    emailCode: '/api/auth/email-code',
    resetPassword: '/api/auth/password/reset',
    logout: '/api/auth/logout',
    data: '/api/data',
    account: '/api/account',
    password: '/api/account/password',
    logoutAll: '/api/account/logout-all'
  };

  const SCOPE_KEY = 'xingjian-scope';
  const USER_KEY = 'xingjian-user';
  const GUEST = 'guest';
  const SAVE_DELAY = 500;
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  let currentUser = null;
  let authMode = 'login';
  // The server decides at boot whether sign-up needs an email code.
  let emailRequired = true;
  let localMode = false;
  let authBusy = false;
  let saveTimer = null;
  let saving = false;
  let dirty = false;
  let lastSavedAt = '';
  let syncState = 'loading';
  let codeTimer = null;
  let codeCountdown = 0;

  // ------------------------------------------------------------------
  // Storage and small helpers
  // ------------------------------------------------------------------
  function readStorage(key) {
    try {
      return localStorage.getItem(key);
    } catch (_error) {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      localStorage.setItem(key, value);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function removeStorage(key) {
    try {
      localStorage.removeItem(key);
    } catch (_error) {
      /* private mode: nothing to clear */
    }
  }

  function scope() {
    return readStorage(SCOPE_KEY) || GUEST;
  }

  function dataKey() {
    const current = scope();
    // Keep reading the pre-account prototype key until the first sign-in
    // has migrated it to a per-user cache.
    return current === GUEST ? 'xingjian-v02' : 'xingjian-v02:' + current;
  }

  function pendingKey() {
    return 'xingjian-pending:' + scope();
  }

  function safe(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/[&<>"']/g, (character) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[character]));
  }

  function formatDate(value) {
    if (!value) return '';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'long',
      day: 'numeric'
    });
  }

  async function request(path, options) {
    const opts = options || {};
    const init = Object.assign(
      {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store'
      },
      opts
    );
    const timeoutMs = init.timeout || 15000;
    delete init.timeout;
    const headers = Object.assign(
      { Accept: 'application/json' },
      opts.headers || {}
    );
    if (init.body !== undefined && init.body !== null) {
      headers['Content-Type'] = 'application/json';
    }
    init.headers = headers;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    init.signal = controller.signal;

    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      const offline = new Error(
        error && error.name === 'AbortError'
          ? '服务器响应超时，请稍后再试。'
          : '无法连接服务器，请确认 python server.py 正在运行。'
      );
      offline.status = 0;
      offline.cause = error;
      throw offline;
    } finally {
      clearTimeout(timeout);
    }

    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = null;
      }
    }
    if (!response.ok) {
      const failure = new Error(
        (payload && payload.error) || `请求失败（${response.status}）`
      );
      failure.status = response.status;
      failure.code = payload && payload.code;
      throw failure;
    }
    return payload || {};
  }

  function normalizeData(value) {
    const base = JSON.parse(JSON.stringify(seed));
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return base;
    }
    const merged = Object.assign(base, value);
    if (!Array.isArray(merged.tasks)) merged.tasks = [];
    if (!Array.isArray(merged.goals)) merged.goals = [];
    if (!Array.isArray(merged.extra)) merged.extra = [];
    if (
      !merged.reviews ||
      typeof merged.reviews !== 'object' ||
      Array.isArray(merged.reviews)
    ) {
      merged.reviews = {};
    }
    if (typeof merged.name !== 'string' || !merged.name.trim()) {
      merged.name = '明';
    }
    merged.moved = Boolean(merged.moved);
    merged.reminder = Boolean(merged.reminder);
    return merged;
  }

  // ------------------------------------------------------------------
  // Public surface used by index.html and agent.js
  // ------------------------------------------------------------------
  const XJ = {
    scope: scope,
    dataKey: dataKey,
    queueSave: queueSave,
    getUser: function () {
      return currentUser;
    },
    openAccount: openAccount,
    start: start
  };
  window.XJ = XJ;

  // ------------------------------------------------------------------
  // Top bar and sync state
  // ------------------------------------------------------------------
  function setSync(next) {
    syncState = next;
    const element = document.getElementById('syncState');
    if (!element) return;
    const labels = {
      loading: '正在载入',
      synced: '已同步',
      saving: '保存中…',
      error: '未同步',
      expired: '登录已过期',
      local: '本地演示'
    };
    element.textContent = labels[next] || '已同步';
    element.setAttribute('data-state', next);
    element.title = lastSavedAt
      ? `最近保存：${new Date(lastSavedAt).toLocaleString('zh-CN')}`
      : '数据保存在你的账号下';
  }

  function paintUser() {
    let display = '本地演示';
    let initial = '行';
    if (currentUser) {
      display = currentUser.display_name || currentUser.username;
      initial = display.trim().slice(0, 1).toUpperCase();
    } else if (typeof data === 'object' && data && data.name) {
      display = data.name;
      initial = display.trim().slice(0, 1).toUpperCase();
    }

    const nameNodes = [
      document.getElementById('accountName'),
      document.getElementById('sidebarName')
    ];
    nameNodes.forEach((node) => {
      if (node) node.textContent = display;
    });
    [
      document.getElementById('accountAvatar'),
      document.getElementById('sidebarAvatar')
    ].forEach((node) => {
      if (node) node.textContent = initial || '行';
    });

    const button = document.getElementById('accountBtn');
    if (button) {
      button.title = currentUser
        ? `@${currentUser.username} · 账号与同步`
        : '本地演示模式';
    }
    setSync(syncState);
  }

  function wrapRender() {
    if (window.__xjRenderWrapped) return;
    const original = window.render;
    if (typeof original !== 'function') return;
    const wrapped = function () {
      const result = original.apply(this, arguments);
      try {
        decorate();
      } catch (error) {
        console.error('[xingjian] decorate failed', error);
      }
      return result;
    };
    window.__xjRenderWrapped = true;
    window.render = wrapped;
  }

  function decorate() {
    paintUser();
    if (page !== 'settings' || !currentUser) return;
    const view = document.getElementById('view');
    const settings = view && view.querySelector('.settings');
    if (!settings || document.getElementById('accountSettingsRow')) return;

    const row = document.createElement('div');
    row.className = 'settingrow';
    row.id = 'accountSettingsRow';
    row.innerHTML =
      '<div><h3>账号与安全</h3><p>@' +
      safe(currentUser.username) +
      ' · 修改密码、退出登录或删除账号</p></div>' +
      '<button type="button" data-account>管理账号</button>';
    const anchor = settings.querySelector('#settingsSave');
    settings.insertBefore(row, anchor || null);
    row.querySelector('[data-account]').addEventListener('click', openAccount);
  }

  function wireAccountButton() {
    const button = document.getElementById('accountBtn');
    if (button) {
      button.addEventListener('click', openAccount);
    }
  }

  // ------------------------------------------------------------------
  // Boot
  // ------------------------------------------------------------------
  async function start() {
    wrapRender();
    wireAccountButton();
    paintUser();
    setSync('loading');

    try {
      const health = await request('/api/health');
      emailRequired =
        health.email_required === undefined
          ? true
          : Boolean(health.email_required);
    } catch (_error) {
      emailRequired = true;
    }

    const signed = new URLSearchParams(location.search).has('signed');
    try {
      const payload = await request(API.me);
      currentUser = payload.user;
      writeStorage(USER_KEY, JSON.stringify(currentUser));

      const storedScope = readStorage(SCOPE_KEY);
      if (!signed && storedScope !== currentUser.id) {
        writeStorage(SCOPE_KEY, currentUser.id);
        location.replace(location.pathname + '?signed=1');
        return;
      }
      if (signed) {
        history.replaceState(null, '', location.pathname);
      }

      await loadUserData();
      ready();
    } catch (error) {
      if (error.status === 401) {
        showAuth();
        return;
      }
      showAuth({ offline: true, message: error.message });
    }
  }

  async function loadUserData() {
    // A failed save is parked here so a refresh or a later login does not
    // silently replace the user's newest edits with the older server copy.
    const pending = readStorage(pendingKey());
    if (pending) {
      try {
        data = normalizeData(JSON.parse(pending));
        writeStorage(dataKey(), JSON.stringify(data));
        dirty = true;
        setSync('saving');
        setTimeout(flushSave, 300);
        return;
      } catch (_error) {
        removeStorage(pendingKey());
      }
    }

    const payload = await request(API.data);
    if (payload.data && typeof payload.data === 'object') {
      data = normalizeData(payload.data);
      writeStorage(dataKey(), JSON.stringify(data));
      lastSavedAt = payload.updated_at || '';
      setSync('synced');
      return;
    }
    // First sign-in on this account: keep the local prototype state and
    // publish it to the server so nothing the user already wrote is lost.
    data = normalizeData(data);
    dirty = true;
    setSync('saving');
    setTimeout(flushSave, 300);
  }

  function ready() {
    document.body.classList.remove('xj-booting', 'xj-locked');
    hideAuth();
    window.__xjReady = true;
    setSync(syncState === 'saving' ? 'saving' : 'synced');
    try {
      render();
    } catch (error) {
      console.error('[xingjian] initial render failed', error);
    }
  }

  // ------------------------------------------------------------------
  // Debounced server sync
  // ------------------------------------------------------------------
  function queueSave() {
    if (localMode || !currentUser) return;
    dirty = true;
    setSync('saving');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, SAVE_DELAY);
  }

  async function flushSave() {
    if (saving || !dirty || localMode || !currentUser) return;
    saving = true;
    dirty = false;
    setSync('saving');
    const snapshot = data;
    try {
      const payload = await request(API.data, {
        method: 'PUT',
        body: JSON.stringify({ data: snapshot })
      });
      lastSavedAt = payload.updated_at || '';
      removeStorage(pendingKey());
      setSync('synced');
    } catch (error) {
      writeStorage(pendingKey(), JSON.stringify(snapshot));
      if (error.status === 401) {
        currentUser = null;
        setSync('expired');
        showAuth({
          message: '登录已过期，请重新登录。页面里的改动仍在本地。'
        });
      } else {
        setSync('error');
        console.error('[xingjian] save failed', error);
        if (typeof toast === 'function') {
          toast('同步失败：' + error.message);
        }
      }
    } finally {
      saving = false;
      if (dirty) {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(flushSave, 1500);
      }
    }
  }

  window.addEventListener('pagehide', function () {
    if (!dirty || localMode || !currentUser) return;
    try {
      fetch(API.data, {
        method: 'PUT',
        keepalive: true,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: data })
      });
    } catch (_error) {
      /* best effort only */
    }
  });

  // ------------------------------------------------------------------
  // Login / register surface
  // ------------------------------------------------------------------
  function showAuth(options) {
    const settings = options || {};
    document.body.classList.add('xj-locked');
    document.body.classList.remove('xj-booting');

    let overlay = document.getElementById('xj-auth');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'xj-auth';
      overlay.innerHTML =
        '<div class="authwrap">' +
        '<div class="authbrand">行间<small>让计划贴近生活</small></div>' +
        '<div class="authcard">' +
        '<div class="authtabs">' +
        '<button type="button" data-auth-tab="login">登录</button>' +
        '<button type="button" data-auth-tab="register">注册</button>' +
        '</div>' +
        '<p class="authlead" id="authLead"></p>' +
        '<form id="authForm" novalidate></form>' +
        '<p class="autherr" id="authError" role="alert" hidden></p>' +
        '<p class="authnote" id="authNote"></p>' +
        '<button class="authlocal" id="authLocal" type="button" hidden>' +
        '先以本地演示模式打开</button>' +
        '<p class="authfoot">数据保存在你的账号下，换设备登录后可以继续。</p>' +
        '</div></div>';
      document.body.appendChild(overlay);

      overlay.querySelectorAll('[data-auth-tab]').forEach((tab) => {
        tab.addEventListener('click', () => {
          authMode = tab.getAttribute('data-auth-tab');
          setAuthError('');
          renderAuthForm(overlay);
        });
      });
      overlay
        .querySelector('#authForm')
        .addEventListener('submit', submitAuth);
      overlay.querySelector('#authLocal').addEventListener('click', () => {
        enterLocalMode();
      });
    }

    const localButton = overlay.querySelector('#authLocal');
    if (settings.offline) {
      setAuthNote(
        settings.message ||
          '无法连接服务器。你仍然可以先用本地模式体验原型。',
        true
      );
      localButton.hidden = false;
    } else {
      setAuthNote(settings.message || '');
      localButton.hidden = true;
    }
    renderAuthForm(overlay);
    const first = overlay.querySelector('input[name="username"]');
    if (first) setTimeout(() => first.focus(), 40);
  }

  function hideAuth() {
    resetCodeCountdown();
    const overlay = document.getElementById('xj-auth');
    if (overlay) overlay.remove();
    document.body.classList.remove('xj-locked');
  }

  function renderAuthForm(overlay) {
    const form = overlay.querySelector('#authForm');
    const lead = overlay.querySelector('#authLead');
    const tabs = overlay.querySelector('.authtabs');
    resetCodeCountdown();
    if (tabs) tabs.hidden = authMode === 'reset';
    overlay.querySelectorAll('[data-auth-tab]').forEach((tab) => {
      tab.classList.toggle(
        'active',
        tab.getAttribute('data-auth-tab') === authMode
      );
    });

    if (authMode === 'register') {
      lead.textContent = emailRequired
        ? '注册需要验证邮箱。验证码会发送到你的邮箱，6 位数字，10 分钟内有效。'
        : '注册只需要用户名和密码，不需要邮箱验证。';
      form.innerHTML =
        '<label class="authfield"><span>用户名</span>' +
        '<input name="username" autocomplete="username" maxlength="32" ' +
        'placeholder="3–32 位字母、数字或 _ . -" required></label>' +
        '<label class="authfield"><span>显示名称（可选）</span>' +
        '<input name="display_name" autocomplete="nickname" maxlength="40" ' +
        'placeholder="例如：小明"></label>' +
        (emailRequired
          ? '<label class="authfield"><span>邮箱</span>' +
            '<input name="email" type="email" autocomplete="email" ' +
            'maxlength="254" placeholder="用于接收验证码，例如 name@example.com" ' +
            'required></label>' +
            '<div class="authfield"><span>邮箱验证码</span>' +
            '<div class="authcoderow">' +
            '<input name="code" inputmode="numeric" autocomplete="one-time-code" ' +
            'maxlength="6" placeholder="6 位数字" required>' +
            '<button type="button" class="authsend" id="authSendCode">' +
            '发送验证码</button>' +
            '</div></div>'
          : '') +
        '<label class="authfield"><span>密码</span>' +
        '<input name="password" type="password" autocomplete="new-password" ' +
        'minlength="8" maxlength="128" placeholder="至少 8 个字符" required>' +
        '</label>' +
        '<label class="authfield"><span>确认密码</span>' +
        '<input name="confirm" type="password" autocomplete="new-password" ' +
        'minlength="8" maxlength="128" placeholder="再输入一次" required>' +
        '</label>' +
        '<button class="primary authsubmit" type="submit">创建账号</button>';
    } else if (authMode === 'reset') {
      lead.textContent =
        '输入注册时验证的邮箱，获取验证码后设置新密码。';
      form.innerHTML =
        '<label class="authfield"><span>注册邮箱</span>' +
        '<input name="email" type="email" autocomplete="email" maxlength="254" ' +
        'placeholder="name@example.com" required></label>' +
        '<div class="authfield"><span>邮箱验证码</span>' +
        '<div class="authcoderow">' +
        '<input name="code" inputmode="numeric" autocomplete="one-time-code" ' +
        'maxlength="6" placeholder="6 位数字" required>' +
        '<button type="button" class="authsend" id="authSendCode">发送验证码</button>' +
        '</div></div>' +
        '<label class="authfield"><span>新密码</span>' +
        '<input name="password" type="password" autocomplete="new-password" ' +
        'minlength="8" maxlength="128" placeholder="至少 8 个字符" required>' +
        '</label>' +
        '<label class="authfield"><span>确认新密码</span>' +
        '<input name="confirm" type="password" autocomplete="new-password" ' +
        'minlength="8" maxlength="128" placeholder="再输入一次" required>' +
        '</label>' +
        '<button class="primary authsubmit" type="submit">重置密码</button>' +
        '<p class="authswitch"><button type="button" class="linkbutton" ' +
        'id="authBackLogin">返回登录</button></p>';
    } else {
      lead.textContent =
        '用用户名和密码登录。邮箱只在注册验证和找回密码时使用。';
      form.innerHTML =
        '<label class="authfield"><span>用户名</span>' +
        '<input name="username" autocomplete="username" maxlength="32" ' +
        'placeholder="你的用户名" required></label>' +
        '<label class="authfield"><span>密码</span>' +
        '<input name="password" type="password" autocomplete="current-password" ' +
        'maxlength="128" placeholder="你的密码" required></label>' +
        '<button class="primary authsubmit" type="submit">登录</button>' +
        '<p class="authswitch">' +
        (emailRequired
          ? '<button type="button" class="linkbutton" id="authForgot">' +
            '忘记密码？</button> 用注册邮箱收验证码重置'
          : '') +
        '</p>';
    }

    const forgot = form.querySelector('#authForgot');
    if (forgot) {
      forgot.addEventListener('click', () => {
        authMode = 'reset';
        setAuthError('');
        setAuthNote('');
        renderAuthForm(overlay);
      });
    }
    const back = form.querySelector('#authBackLogin');
    if (back) {
      back.addEventListener('click', () => {
        authMode = 'login';
        setAuthError('');
        setAuthNote('');
        renderAuthForm(overlay);
      });
    }
    const send = form.querySelector('#authSendCode');
    if (send) {
      send.addEventListener('click', () => {
        sendEmailCode(authMode, overlay);
      });
    }
  }

  function sendEmailCode(purpose, overlay) {
    if (authBusy) return;
    const form = overlay.querySelector('#authForm');
    const emailInput = form.querySelector('input[name="email"]');
    const button = form.querySelector('#authSendCode');
    const email = emailInput ? emailInput.value.trim() : '';
    setAuthError('');
    if (!EMAIL_RE.test(email)) {
      setAuthError('请输入有效的邮箱地址。');
      if (emailInput) emailInput.focus();
      return;
    }
    if (button) {
      button.disabled = true;
      button.textContent = '发送中…';
    }
    const body = { email: email, purpose: purpose };
    if (purpose === 'register') {
      const usernameInput = form.querySelector('input[name="username"]');
      if (usernameInput) body.username = usernameInput.value.trim();
    }
    request(API.emailCode, {
      method: 'POST',
      body: JSON.stringify(body),
      timeout: 35000
    })
      .then((payload) => {
        if (!button) return;
        startCodeCountdown(
          button,
          Number(payload.resend_in) > 0 ? Number(payload.resend_in) : 60
        );
        setAuthNote(
          payload.message || '验证码已发送，请查收邮箱（也看一眼垃圾邮件）。'
        );
        const codeInput = form.querySelector('input[name="code"]');
        if (codeInput) codeInput.focus();
      })
      .catch((error) => {
        if (button) {
          button.disabled = false;
          button.textContent = '发送验证码';
        }
        setAuthError(error.message || '验证码发送失败，请稍后再试。');
        if (error.status === 0) {
          setAuthNote('无法连接服务器，请确认 python server.py 正在运行。', true);
        }
      });
  }

  function startCodeCountdown(button, seconds) {
    if (codeTimer) {
      clearInterval(codeTimer);
      codeTimer = null;
    }
    codeCountdown = Math.max(1, Math.min(Number(seconds) || 60, 3600));
    button.disabled = true;
    const paint = () => {
      if (codeCountdown <= 0) {
        if (codeTimer) clearInterval(codeTimer);
        codeTimer = null;
        button.disabled = false;
        button.textContent = '重新发送';
        return;
      }
      button.textContent = '重新发送（' + codeCountdown + 's）';
      codeCountdown -= 1;
    };
    paint();
    codeTimer = setInterval(paint, 1000);
  }

  function resetCodeCountdown() {
    if (codeTimer) {
      clearInterval(codeTimer);
      codeTimer = null;
    }
    codeCountdown = 0;
  }

  function setAuthNote(message, offline) {
    const note = document.getElementById('authNote');
    if (!note) return;
    note.textContent = message || '';
    note.classList.toggle('offline', Boolean(offline));
  }

  async function submitAuth(event) {
    event.preventDefault();
    if (authBusy) return;
    const form = event.currentTarget;
    const formData = new FormData(form);
    const mode = authMode;
    const username = String(formData.get('username') || '').trim();
    const password = String(formData.get('password') || '');
    const displayName = String(formData.get('display_name') || '').trim();
    const confirm = String(formData.get('confirm') || '');
    const email = String(formData.get('email') || '').trim();
    const code = String(formData.get('code') || '').trim();

    setAuthError('');

    if (mode === 'reset') {
      if (!EMAIL_RE.test(email)) {
        return setAuthError('请输入注册时使用的邮箱。');
      }
      if (!/^\d{6}$/.test(code)) {
        return setAuthError('请输入 6 位数字验证码。');
      }
      if (password.length < 8) {
        return setAuthError('新密码至少需要 8 个字符。');
      }
      if (password !== confirm) {
        return setAuthError('两次输入的新密码不一致。');
      }
      setAuthBusy(true);
      try {
        const payload = await request(API.resetPassword, {
          method: 'POST',
          body: JSON.stringify({
            email: email,
            code: code,
            new_password: password
          })
        });
        authMode = 'login';
        resetCodeCountdown();
        renderAuthForm(document.getElementById('xj-auth'));
        setAuthNote(
          payload.message || '密码已重置，请用新密码登录。'
        );
        const usernameInput = document.querySelector(
          '#authForm input[name="username"]'
        );
        if (usernameInput) usernameInput.focus();
      } catch (error) {
        setAuthError(error.message || '密码重置失败，请稍后再试。');
      } finally {
        setAuthBusy(false);
      }
      return;
    }

    if (!username) return setAuthError('请输入用户名。');
    if (password.length < 8) return setAuthError('密码至少需要 8 个字符。');
    if (mode === 'register') {
      if (emailRequired) {
        if (!EMAIL_RE.test(email)) {
          return setAuthError('请输入有效的邮箱地址，用于接收验证码。');
        }
        if (!/^\d{6}$/.test(code)) {
          return setAuthError('请输入邮箱里收到的 6 位数字验证码。');
        }
      }
      if (password !== confirm) {
        return setAuthError('两次输入的密码不一致。');
      }
    }
    if (displayName.length > 40) {
      return setAuthError('显示名称不能超过 40 个字符。');
    }

    setAuthBusy(true);
    try {
      const path = mode === 'register' ? API.register : API.login;
      const body =
        mode === 'register'
          ? {
              username: username,
              password: password,
              display_name: displayName
            }
          : { username: username, password: password };
      if (mode === 'register' && emailRequired) {
        body.email = email;
        body.code = code;
      }
      const payload = await request(path, {
        method: 'POST',
        body: JSON.stringify(body)
      });
      currentUser = payload.user;
      writeStorage(USER_KEY, JSON.stringify(currentUser));
      migrateLocalData(currentUser.id);
      writeStorage(SCOPE_KEY, currentUser.id);
      location.replace(location.pathname + '?signed=1');
    } catch (error) {
      setAuthError(error.message || '请求失败，请稍后再试。');
      if (error.status === 0) {
        setAuthNote('无法连接服务器，可以先用本地模式体验。', true);
        const localButton = document.getElementById('authLocal');
        if (localButton) localButton.hidden = false;
      }
      setAuthBusy(false);
    }
  }

  function migrateLocalData(userId) {
    if (readStorage('xingjian-migrated-v1') === '1') return;
    const legacyData = readStorage('xingjian-v02');
    if (legacyData) {
      const target = 'xingjian-v02:' + userId;
      if (!readStorage(target)) writeStorage(target, legacyData);
    }
    const legacyChats = readStorage('xingjian-agent-chats-v1');
    if (legacyChats) {
      const target = 'xingjian-agent-chats-v1:' + userId;
      if (!readStorage(target)) writeStorage(target, legacyChats);
    }
    writeStorage('xingjian-migrated-v1', '1');
  }

  function setAuthBusy(next) {
    authBusy = next;
    const form = document.getElementById('authForm');
    if (!form) return;
    const button = form.querySelector('button[type="submit"]');
    if (!button) return;
    button.disabled = next;
    button.textContent = next
      ? '请稍候…'
      : authMode === 'register'
        ? '创建账号'
        : authMode === 'reset'
          ? '重置密码'
          : '登录';
    const send = form.querySelector('#authSendCode');
    if (send) send.disabled = next || codeCountdown > 0;
  }

  function setAuthError(message) {
    const element = document.getElementById('authError');
    if (!element) return;
    element.textContent = message || '';
    element.hidden = !message;
  }

  function enterLocalMode() {
    localMode = true;
    currentUser = null;
    // Never show another account's cached document in the local demo.
    removeStorage(SCOPE_KEY);
    removeStorage(USER_KEY);
    const guest = readStorage('xingjian-v02');
    if (guest) {
      try {
        data = normalizeData(JSON.parse(guest));
      } catch (_error) {
        data = normalizeData(null);
      }
    } else {
      data = normalizeData(null);
    }
    hideAuth();
    document.body.classList.remove('xj-booting', 'xj-locked');
    window.__xjReady = true;
    setSync('local');
    try {
      render();
    } catch (error) {
      console.error('[xingjian] local render failed', error);
    }
  }

  // ------------------------------------------------------------------
  // Account modal
  // ------------------------------------------------------------------
  function openAccount() {
    if (!currentUser) {
      showLocalAccount();
      return;
    }
    if (typeof show !== 'function') return;

    const joined = formatDate(currentUser.created_at);
    show(
      '<div class="accountmodalbody">' +
        '<div class="row between"><h2>账号</h2>' +
        '<button type="button" data-close class="smallbutton">关闭</button></div>' +
        '<p class="goalmeta">@' +
        safe(currentUser.username) +
        (joined ? ' · 注册于 ' + safe(joined) : '') +
        '</p>' +
        (currentUser.email
          ? '<p class="goalmeta">邮箱：' +
            safe(currentUser.email) +
            (currentUser.email_verified ? ' · 已验证' : ' · 未验证') +
            '</p>'
          : '<p class="goalmeta">未绑定邮箱：登录仍使用用户名和密码，' +
            '但无法用邮箱找回密码。</p>') +
        '<div class="accountblock">' +
        '<label class="accountlabel">显示名称' +
        '<input id="acctDisplay" maxlength="40" value="' +
        safe(currentUser.display_name || currentUser.username) +
        '"></label>' +
        '<div class="row"><button class="primary" id="acctSaveProfile">保存资料</button></div>' +
        '<p class="autherr" id="acctProfileError" hidden></p>' +
        '</div>' +
        '<hr class="accounthr">' +
        '<h3>修改密码</h3>' +
        '<p class="goalmeta">修改后，其他设备上的登录会立即失效。</p>' +
        '<label class="accountlabel">当前密码' +
        '<input id="acctCurrent" type="password" autocomplete="current-password"></label>' +
        '<label class="accountlabel">新密码' +
        '<input id="acctNew" type="password" autocomplete="new-password" ' +
        'placeholder="至少 8 个字符"></label>' +
        '<label class="accountlabel">确认新密码' +
        '<input id="acctConfirm" type="password" autocomplete="new-password"></label>' +
        '<div class="row"><button id="acctSavePassword">更新密码</button></div>' +
        '<p class="autherr" id="acctPasswordError" hidden></p>' +
        '<hr class="accounthr">' +
        '<div class="row between accountactions">' +
        '<button id="acctLogoutAll" type="button">退出所有设备</button>' +
        '<button id="acctLogout" type="button">退出登录</button>' +
        '</div>' +
        '<div class="accountdanger">' +
        '<h3>删除账号</h3>' +
        '<p>账号和全部数据会立即删除，无法恢复。</p>' +
        '<label class="accountlabel">输入密码确认' +
        '<input id="acctDeletePassword" type="password" ' +
        'autocomplete="current-password"></label>' +
        '<button class="danger" id="acctDelete" type="button">删除账号</button>' +
        '<p class="autherr" id="acctDangerError" hidden></p>' +
        '</div>' +
        '</div>'
    );

    const dialog = document.getElementById('modal');
    dialog.classList.add('accountmodal');
    dialog.addEventListener(
      'close',
      () => dialog.classList.remove('accountmodal'),
      { once: true }
    );
    bindAccountDialog(dialog);
  }

  function bindAccountDialog(dialog) {
    dialog.querySelector('#acctSaveProfile').addEventListener('click', async () => {
      const input = dialog.querySelector('#acctDisplay');
      const name = input.value.trim();
      clearAccountError('acctProfileError');
      if (!name) {
        return setAccountError('acctProfileError', '显示名称不能为空。');
      }
      if (name.length > 40) {
        return setAccountError('acctProfileError', '显示名称不能超过 40 个字符。');
      }
      const button = dialog.querySelector('#acctSaveProfile');
      button.disabled = true;
      try {
        const payload = await request(API.account, {
          method: 'PATCH',
          body: JSON.stringify({ display_name: name })
        });
        currentUser = payload.user;
        writeStorage(USER_KEY, JSON.stringify(currentUser));
        if (typeof data === 'object' && data) {
          data.name = currentUser.display_name;
          save();
        }
        paintUser();
        dialog.close();
        if (typeof toast === 'function') toast('资料已更新');
      } catch (error) {
        setAccountError('acctProfileError', error.message);
      } finally {
        button.disabled = false;
      }
    });

    dialog.querySelector('#acctSavePassword').addEventListener('click', async () => {
      const currentPassword = dialog.querySelector('#acctCurrent').value;
      const newPassword = dialog.querySelector('#acctNew').value;
      const confirmPassword = dialog.querySelector('#acctConfirm').value;
      clearAccountError('acctPasswordError');
      if (!currentPassword) {
        return setAccountError('acctPasswordError', '请输入当前密码。');
      }
      if (newPassword.length < 8) {
        return setAccountError('acctPasswordError', '新密码至少需要 8 个字符。');
      }
      if (newPassword !== confirmPassword) {
        return setAccountError('acctPasswordError', '两次输入的新密码不一致。');
      }
      const button = dialog.querySelector('#acctSavePassword');
      button.disabled = true;
      try {
        await request(API.password, {
          method: 'POST',
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword
          })
        });
        dialog.close();
        if (typeof toast === 'function') {
          toast('密码已更新，其他设备已退出登录');
        }
      } catch (error) {
        setAccountError('acctPasswordError', error.message);
      } finally {
        button.disabled = false;
      }
    });

    dialog.querySelector('#acctLogout').addEventListener('click', async () => {
      await signOut(API.logout);
    });

    dialog.querySelector('#acctLogoutAll').addEventListener('click', async () => {
      if (!window.confirm('退出所有设备？包括当前这一台。')) return;
      await signOut(API.logoutAll);
    });

    dialog.querySelector('#acctDelete').addEventListener('click', async () => {
      const password = dialog.querySelector('#acctDeletePassword').value;
      clearAccountError('acctDangerError');
      if (!password) {
        return setAccountError('acctDangerError', '请输入密码以确认删除。');
      }
      if (!window.confirm('删除账号和全部数据？此操作无法撤销。')) return;
      const button = dialog.querySelector('#acctDelete');
      button.disabled = true;
      try {
        await request(API.account, {
          method: 'DELETE',
          body: JSON.stringify({ password: password })
        });
        removeStorage(dataKey());
        removeStorage(pendingKey());
        removeStorage('xingjian-agent-chats-v1:' + scope());
        removeStorage(SCOPE_KEY);
        removeStorage(USER_KEY);
        location.replace(location.pathname);
      } catch (error) {
        setAccountError('acctDangerError', error.message);
        button.disabled = false;
      }
    });
  }

  async function signOut(path) {
    try {
      await request(path, { method: 'POST', body: '{}' });
    } catch (error) {
      if (typeof toast === 'function') {
        toast('退出登录失败：' + error.message);
      }
      return;
    }
    removeStorage(SCOPE_KEY);
    removeStorage(USER_KEY);
    location.replace(location.pathname);
  }

  function showLocalAccount() {
    if (typeof show !== 'function') return;
    show(
      '<h2>本地演示模式</h2>' +
        '<p class="goalmeta">当前没有连接服务器，改动只保存在这个浏览器里。' +
        '登录后可同步到账号。</p>' +
        '<div class="row"><button data-close>继续使用</button>' +
        '<button class="primary" id="localToLogin">登录 / 注册</button></div>'
    );
    const button = document.getElementById('localToLogin');
    if (button) {
      button.addEventListener('click', () => {
        document.getElementById('modal').close();
        localMode = false;
        showAuth();
      });
    }
  }

  function setAccountError(id, message) {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = message || '';
    element.hidden = !message;
  }

  function clearAccountError(id) {
    setAccountError(id, '');
  }

  // ------------------------------------------------------------------
  // Start once the prototype has defined its globals
  // ------------------------------------------------------------------
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
