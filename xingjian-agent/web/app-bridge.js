/* app-bridge.js — 把 V0.13 前端接回账号与 AI 后端。
 *
 *  1. 账号同步：前端自己的状态键（首页块、记忆库、侧栏偏好…）打包进账号文档
 *     的 __app 字段，随 XJ.queueSave() 一起上传；登录后若服务端有更新的副本，
 *     写回 localStorage 并重载一次。
 *  2. 首页 AI：账号已登录且后端 agent 可用时，把「说说你的想法」接到
 *     /api/agent/chat（SSE 流式）；不可用时回落到原型自带的本地草案流程。
 *  3. 账号菜单：给新侧栏补「账号与同步」入口与状态行。
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------------
   * 0. helpers
   * ------------------------------------------------------------------ */
  const APP_PREFIXES = [
    'xingjian-home-',
    'xingjian-workspace-',
    'xingjian-history-',
    'xingjian-nav-',
  ];
  const RESTORE_FLAG = 'xj-app-restored';

  const isAppKey = (key) => APP_PREFIXES.some((p) => String(key).indexOf(p) === 0);
  const currentUser = () =>
    window.XJ && typeof window.XJ.getUser === 'function' ? window.XJ.getUser() : null;
  const agent = () => window.__xingjianAgent || null;
  const appData = () => (typeof data === 'object' && data ? data : null);

  /* ------------------------------------------------------------------
   * 1. 账号同步：状态键 ⇄ data.__app
   * ------------------------------------------------------------------ */
  const nativeSet = Storage.prototype.setItem;
  let pushTimer = null;

  function collectKeys() {
    const out = {};
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key && isAppKey(key)) out[key] = localStorage.getItem(key);
    }
    return out;
  }

  function pushToAccount() {
    const doc = appData();
    if (!doc || !currentUser()) return;
    // 记忆库在内存里初始化，没改过就不会落盘；首次同步时补写一份。
    try {
      if (typeof workspaceData === 'object' && workspaceData && !localStorage.getItem('xingjian-workspace-v11')) {
        nativeSet.call(localStorage, 'xingjian-workspace-v11', JSON.stringify(workspaceData));
      }
    } catch (_error) {
      /* optional */
    }
    doc.__app = collectKeys();
    if (pushTimer) clearTimeout(pushTimer);
    pushTimer = setTimeout(() => {
      if (window.XJ && typeof window.XJ.queueSave === 'function') window.XJ.queueSave();
    }, 600);
  }

  Storage.prototype.setItem = function (key, value) {
    const result = nativeSet.call(this, key, value);
    try {
      if (isAppKey(key) && currentUser()) pushToAccount();
    } catch (_error) {
      /* storage may be unavailable in private mode */
    }
    return result;
  };

  function restoreFromAccount() {
    const doc = appData();
    if (!doc || !doc.__app || !currentUser()) return false;
    const pending = Object.keys(doc.__app).filter(
      (key) => isAppKey(key) && localStorage.getItem(key) !== doc.__app[key]
    );
    if (!pending.length) return false;
    if (sessionStorage.getItem(RESTORE_FLAG)) return false;
    pending.forEach((key) => nativeSet.call(localStorage, key, doc.__app[key]));
    sessionStorage.setItem(RESTORE_FLAG, '1');
    location.reload();
    return true;
  }

  let syncTimer = setInterval(() => {
    if (!currentUser() || !appData()) return;
    clearInterval(syncTimer);
    syncTimer = null;
    if (!appData().__app) {
      pushToAccount();
    } else {
      restoreFromAccount();
    }
  }, 500);

  /* ------------------------------------------------------------------
   * 2. 首页 AI：DeepSeek 流式对话，失败回落本地草案
   * ------------------------------------------------------------------ */
  const chatHistory = [];
  let streaming = false;

  function apiBase() {
    const meta = document.querySelector('meta[name="agent-endpoint"]');
    const raw = meta && meta.getAttribute('content');
    return raw ? String(raw).replace(/\/+$/, '') : '';
  }

  function chatBody(text) {
    const state = {};
    try {
      if (typeof hs === 'object' && hs.tasks) {
        state.tasks = hs.tasks.map((t) => ({ title: t.title, done: !!t.done }));
      }
      if (typeof data === 'object' && data && Array.isArray(data.goals)) {
        state.goals = data.goals.map((g) => ({ title: g.title, date: g.date }));
      }
    } catch (_error) {
      /* state is a nice-to-have */
    }
    chatHistory.push({ role: 'user', content: text });
    return {
      context: 'home',
      messages: chatHistory.slice(-10),
      state: state,
    };
  }

  function bubble(role, text) {
    const el = document.createElement('div');
    el.className = 'message ' + role;
    el.textContent = text || '';
    return el;
  }

  function chatShell(userText) {
    const scroller = document.querySelector('.hai-scroll');
    if (!scroller) return null;
    scroller.classList.add('with-response');
    scroller.innerHTML = '';
    scroller.appendChild(bubble('user', userText));
    const reply = bubble('assistant', '');
    reply.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    scroller.appendChild(reply);
    scroller.scrollTop = scroller.scrollHeight;
    return reply;
  }

  async function streamChat(body, onDelta) {
    const response = await fetch(apiBase() + '/api/agent/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let detail = '服务端返回 ' + response.status;
      try {
        const payload = await response.json();
        if (payload && payload.error) detail = payload.error;
      } catch (_error) {
        /* keep status */
      }
      throw new Error(detail);
    }
    if (!response.body) throw new Error('当前浏览器不支持流式响应');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index = buffer.indexOf('\n\n');
      while (index !== -1) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        let name = 'message';
        const dataLines = [];
        frame.split('\n').forEach((line) => {
          if (line.indexOf('event:') === 0) name = line.slice(6).trim();
          else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).trim());
        });
        index = buffer.indexOf('\n\n');
        if (!dataLines.length) continue;
        let payload = null;
        try {
          payload = JSON.parse(dataLines.join('\n'));
        } catch (_error) {
          continue;
        }
        if (name === 'delta' && payload.text) onDelta(payload.text);
        else if (name === 'error') throw new Error(payload.message || '出错了');
      }
    }
  }

  async function chat(text) {
    const reply = chatShell(text);
    if (!reply) return;
    streaming = true;
    let text_ = '';
    try {
      await streamChat(chatBody(text), (delta) => {
        text_ += delta;
        reply.textContent = text_;
        const scroller = document.querySelector('.hai-scroll');
        if (scroller) scroller.scrollTop = scroller.scrollHeight;
      });
      if (!text_) {
        reply.textContent = '（没有收到内容）';
        return;
      }
      chatHistory.push({ role: 'assistant', content: text_ });
      const status = document.querySelector('.hai-top small');
      if (status) status.textContent = 'DeepSeek · ' + (agent().model || '已连接');
      try {
        hmessage = text_;
        hdraft = null;
      } catch (_error) {
        /* legacy globals only */
      }
    } catch (error) {
      reply.remove();
      chatHistory.pop();
      // 后端不可用：回落到原型自带的本地草案流程
      if (typeof window.__localHomeInput === 'function') {
        window.__localHomeInput(text);
        return;
      }
      const scroller = document.querySelector('.hai-scroll');
      if (scroller) {
        const note = bubble('assistant', error.message || '暂时连不上 AI');
        scroller.appendChild(note);
      }
    } finally {
      streaming = false;
    }
  }

  /* 只在账号已登录且后端报告 AI 可用时接管；否则保持原来的本地草案。 */
  const localInput = window.aiHomeInput;
  if (typeof localInput === 'function') {
    window.__localHomeInput = localInput;
    window.aiHomeInput = function (text) {
      const value = String(text == null ? '' : text).trim();
      if (!value) return undefined;
      const field = document.querySelector('#homeComposer input');
      if (field) field.value = '';
      const ready = agent() && agent().configured && currentUser();
      if (!ready || streaming) return localInput.apply(this, arguments);
      return chat(value);
    };
  }

  /* ------------------------------------------------------------------
   * 3. 账号菜单桥接
   * ------------------------------------------------------------------ */
  function decorateAccount() {
    const menu = document.getElementById('accountMenu');
    if (!menu || menu.querySelector('[data-account]')) return;

    const status = document.createElement('small');
    status.id = 'syncState';
    status.className = 'sync-state';
    status.textContent = '本地演示';
    menu.insertBefore(status, menu.firstChild);

    const item = document.createElement('button');
    item.type = 'button';
    item.setAttribute('data-account', '');
    item.textContent = '账号与同步';
    item.addEventListener('click', function (event) {
      event.stopPropagation();
      menu.classList.add('hidden');
      const toggle = document.getElementById('workspaceAccount');
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
      if (window.XJ && typeof window.XJ.openAccount === 'function') window.XJ.openAccount();
    });
    menu.appendChild(item);
  }

  decorateAccount();
  ['sidebarName', 'sidebarAvatar', 'agentState'].forEach(function (id) {
    if (document.getElementById(id)) return;
    const span = document.createElement('span');
    span.id = id;
    span.hidden = true;
    document.body.appendChild(span);
  });
})();
