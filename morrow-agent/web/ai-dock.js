/* ============================================================
   ai-dock.js — 把全站 AI 提问区收成底部一条小对话框

   · 默认折叠：只显示一行输入，页面主体占满
   · 聚焦输入框、出现对话 / 草案时：向上拉高（ai-dock.css 负责高度）
   · 收起按钮或 Esc：回到一条
   · 只切换 .workspace 上的 ai-open 类，不改任何业务逻辑
   ============================================================ */
(function () {
  'use strict';

  const OPEN_CLASS = 'ai-open';
  const MESSAGE_SELECTOR = [
    '.hai-scroll .message',
    '.hai-scroll .draftbox',
    '.hai-scroll .alertbox',
    '.aibody .message',
    '.aibody .proposal',
    '.aibody .draftbox'
  ].join(',');

  let userClosed = false;
  let wantOpen = false;
  let lastCount = -1;
  let lastRoute = null;
  let frame = 0;

  function workspace() {
    return document.querySelector('.workspace');
  }

  function panel() {
    return document.querySelector('.workspace > .hai, .workspace > .ai');
  }

  /* 当前页面对应的会话位置：换页才重置折叠状态，同页重渲染保持 */
  function routeKey() {
    const title = document.querySelector('#pageTitle');
    const key =
      typeof window.chatkey === 'function' ? String(window.chatkey()) : '';
    return (title ? title.textContent : '') + '|' + key;
  }

  function messageCount() {
    const host = panel();
    return host ? host.querySelectorAll(MESSAGE_SELECTOR).length : 0;
  }

  function focusInside() {
    const active = document.activeElement;
    return !!(active && active.closest && active.closest('.hai, .ai'));
  }

  /* 注入一个 ✧ 徽标和一个收起按钮，让收起后的条更像一个小对话入口 */
  function decorate() {
    const host = panel();
    if (!host) return;

    host.classList.toggle('has-content', messageCount() > 0);

    host.querySelectorAll('.composer').forEach((form) => {
      if (!form.querySelector('.ai-dock-badge')) {
        const badge = document.createElement('span');
        badge.className = 'ai-dock-badge';
        badge.setAttribute('aria-hidden', 'true');
        badge.textContent = '✧';
        form.insertBefore(badge, form.firstChild);

        // 收起时给这一条一个明确的身份，别让人以为是搜索框
        const name = document.createElement('span');
        name.className = 'ai-dock-name';
        name.textContent = 'Morrow AI';
        form.insertBefore(name, badge.nextSibling);
      }

      // 历史入口放在输入条里，折叠时也能点到
      if (!form.querySelector('[data-ai-history]')) {
        const history = document.createElement('button');
        history.type = 'button';
        history.className = 'ai-dock-history';
        history.setAttribute('data-ai-history', '1');
        history.setAttribute('aria-label', '查看历史会话');
        history.title = '历史会话';
        history.textContent = '◷';
        const voice = form.querySelector('[data-mic], [data-hvoice], #goalVoice');
        if (voice) form.insertBefore(history, voice);
        else form.insertBefore(history, form.lastElementChild);
      }
    });

    const head = host.querySelector('.hai-top, .aihead');
    if (head && !head.querySelector('[data-ai-dock-close]')) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ai-dock-close';
      button.setAttribute('data-ai-dock-close', '1');
      button.setAttribute('aria-label', '收起 AI 面板');
      button.title = '收起 AI 面板';
      button.textContent = '⌄';
      head.appendChild(button);
    }
  }

  /* ------------------------------------------------------------
     历史会话：先读服务端长期记忆，读不到就退回本地聊天记录
     ------------------------------------------------------------ */
  function escapeHTML(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function localSessions() {
    if (typeof window.xjLocalThreads !== 'function') return [];
    return (window.xjLocalThreads() || [])
      .filter((thread) => thread.count > 0)
      .map((thread) => ({
        id: thread.key,
        label: thread.label,
        context: thread.context,
        count: thread.count,
        preview: thread.preview,
        messages: thread.messages,
        local: true,
      }));
  }

  async function loadSessions() {
    const local = localSessions();
    let server = [];
    let source = local.length ? 'local' : 'empty';
    if (typeof window.xjAgentHistory === 'function') {
      try {
        const data = await window.xjAgentHistory();
        if (data && Array.isArray(data.sessions)) {
          server = data.sessions;
          if (server.length) source = local.length ? 'mixed' : 'server';
        }
      } catch (_error) {
        /* 服务端拿不到就用本机的 */
      }
    }
    // 本机会话有完整问答，排前面；服务端记忆补在后面
    const seen = new Set(local.map((item) => item.preview));
    const merged = local.concat(
      server.filter((item) => !seen.has(item.preview || ''))
    );
    return { source, sessions: merged, notes: [] };
  }

  function paintSession(session) {
    return (
      '<section class="ai-history-thread">' +
      `<h3>${escapeHTML(session.label || '')}</h3>` +
      (session.messages || [])
        .map(
          (message) =>
            `<div class="ai-history-bubble ${message.role === 'user' ? 'user' : 'assistant'}">` +
            `${escapeHTML(message.text)}</div>`
        )
        .join('') +
      '</section>'
    );
  }

  function portraitHTML(profile) {
    const derived = (profile && profile.derived) || {};
    if (!derived.samples) {
      return (
        '<div class="ai-portrait">' +
        '<div class="ai-portrait-head"><strong>画像</strong><small>还没量出来</small></div>' +
        '<p class="ai-history-empty">聊两句、排过几块时间之后，这里会出现从你真实记录里量出来的习惯。</p>' +
        '</div>'
      );
    }
    const rows = [];
    if (derived.block_minutes) rows.push(['常见时间块', `${derived.block_minutes} 分钟`]);
    if (derived.blocks_per_day !== undefined) rows.push(['每天排块数', `${derived.blocks_per_day}`]);
    if (derived.day_start && derived.day_end) rows.push(['作息区间', `${derived.day_start}–${derived.day_end}`]);
    if (derived.density) {
      const label = { sparse: '偏稀疏', normal: '正常', dense: '偏密集' }[derived.density] || derived.density;
      rows.push(['排布密度', label]);
    }
    if (derived.goal_count !== undefined) rows.push(['在推进目标', `${derived.goal_count} 个`]);
    if (derived.completion !== null && derived.completion !== undefined) {
      rows.push(['今日完成率', `${Math.round(derived.completion * 100)}%`]);
    }
    if (derived.estimate_bias) rows.push(['实际/计划', `${derived.estimate_bias}`]);
    const items = rows
      .map(
        ([key, value]) =>
          `<div class="ai-portrait-row"><span>${escapeHTML(key)}</span><b>${escapeHTML(value)}</b></div>`
      )
      .join('');
    const notes = (derived.notes || [])
      .map((note) => `<li>${escapeHTML(note)}</li>`)
      .join('');
    return (
      '<div class="ai-portrait">' +
      `<div class="ai-portrait-head"><strong>画像</strong><small>基于 ${derived.samples} 条真实记录 · 更新 ${derived.updates || 0} 次</small></div>` +
      `<div class="ai-portrait-rows">${items}</div>` +
      (notes ? `<ul class="ai-portrait-notes">${notes}</ul>` : '') +
      '</div>'
    );
  }

  async function loadProfile() {
    if (typeof window.xjAgentProfile !== 'function') return null;
    try {
      return await window.xjAgentProfile();
    } catch (_error) {
      return null;
    }
  }

  async function openHistory() {
    const host = panel();
    if (!host) return;
    const meta = await loadSessions();
    const profile = await loadProfile();
    const sessions = meta.sessions || [];
    const list = sessions.length
      ? sessions
          .map(
            (session, index) =>
              `<button type="button" class="ai-history-row" data-ai-history-open="${index}">` +
              `<strong>${escapeHTML(session.label || '')}</strong>` +
              `<small>${escapeHTML(session.preview || '')} · ${session.count} 轮</small>` +
              '</button>'
          )
          .join('')
      : '<p class="ai-history-empty">还没有历史会话。聊过几句之后这里就会留下记录。</p>';

    const html =
      '<section class="ai-history">' +
      '<div class="ai-history-head"><h2>历史与画像</h2>' +
      `<small>${meta.source === 'server' ? '来自服务端记忆' : meta.source === 'local' ? '本机保存的对话' : meta.source === 'mixed' ? '本机对话 + 服务端记忆' : '暂无记录'}</small></div>` +
      portraitHTML(profile) +
      `<div class="ai-history-list">${list}</div>` +
      '<div class="ai-history-thread-wrap" hidden></div>' +
      '<div class="row"><button type="button" data-close>关闭</button></div>' +
      '</section>';

    const modal = document.getElementById('modal');
    if (typeof window.show === 'function' && modal) {
      window.show(html);
      modal.classList.add('ai-history-modal');
      modal.addEventListener(
        'close',
        () => modal.classList.remove('ai-history-modal'),
        { once: true }
      );
      bindHistory(modal, sessions);
      return;
    }
    // 没有模态框时的兜底：直接在面板里展开
    const body = host.querySelector('.hai-scroll, .aibody');
    if (body) body.innerHTML = html;
  }

  function bindHistory(modal, sessions) {
    const wrap = modal.querySelector('.ai-history-thread-wrap');
    const listEl = modal.querySelector('.ai-history-list');
    modal.querySelectorAll('[data-ai-history-open]').forEach((button) => {
      button.addEventListener('click', () => {
        const session = sessions[Number(button.getAttribute('data-ai-history-open'))];
        if (!session || !wrap) return;
        wrap.innerHTML = paintSession(session);
        wrap.hidden = false;
        if (listEl) listEl.hidden = true;
        if (!wrap.querySelector('[data-ai-history-back]')) {
          const back = document.createElement('button');
          back.type = 'button';
          back.className = 'ai-history-back';
          back.setAttribute('data-ai-history-back', '1');
          back.textContent = '← 返回列表';
          back.addEventListener('click', () => {
            wrap.hidden = true;
            if (listEl) listEl.hidden = false;
          });
          wrap.insertBefore(back, wrap.firstChild);
        }
      });
    });
  }

  function scrollPanel() {
    const body = panel() && panel().querySelector('.hai-scroll, .aibody');
    if (body) body.scrollTop = body.scrollHeight;
  }

  function paint() {
    frame = 0;
    const ws = workspace();
    if (!ws) {
      // 设置页等没有 AI 条的页面：不要把展开状态留在 body 上
      document.body.classList.remove(OPEN_CLASS);
      lastRoute = null;
      wantOpen = false;
      userClosed = false;
      lastCount = -1;
      return;
    }

    const count = messageCount();

    // 只有换页才重置：新页面默认收起，有历史时用徽标上的小点提示
    const route = routeKey();
    if (route !== lastRoute) {
      lastRoute = route;
      wantOpen = false;
      userClosed = count > 0;
      lastCount = count;
    }

    if (count > lastCount) userClosed = false; // 新消息到达就重新打开
    lastCount = count;

    const open = !userClosed && (count > 0 || focusInside() || wantOpen);
    if (ws.classList.contains(OPEN_CLASS) !== open) {
      ws.classList.toggle(OPEN_CLASS, open);
      if (open) requestAnimationFrame(scrollPanel);
    }
    document.body.classList.toggle(OPEN_CLASS, open);
    decorate();
  }

  function schedule() {
    if (frame) return;
    frame = requestAnimationFrame(paint);
  }

  function openDock() {
    wantOpen = true;
    userClosed = false;
    schedule();
  }

  function closeDock() {
    userClosed = true;
    wantOpen = false;
    schedule();
  }

  /* ------------------------------------------------------------
     事件：全部走委托，页面重渲染后依然生效
     ------------------------------------------------------------ */
  document.addEventListener(
    'focusin',
    (event) => {
      if (event.target.closest && event.target.closest('.hai, .ai')) openDock();
    },
    true
  );

  document.addEventListener(
    'focusout',
    (event) => {
      if (event.target.closest && event.target.closest('.hai, .ai')) schedule();
    },
    true
  );

  document.addEventListener(
    'click',
    (event) => {
      if (!event.target.closest) return;

      if (event.target.closest('[data-ai-dock-close]')) {
        event.preventDefault();
        closeDock();
        return;
      }

      if (event.target.closest('[data-ai-history]')) {
        event.preventDefault();
        openHistory();
        return;
      }

      const inside = event.target.closest('.hai, .ai');
      if (inside) {
        openDock();
        // 折叠态点条上任意位置，都直接聚焦输入框
        const ws = workspace();
        if (ws && !ws.classList.contains(OPEN_CLASS)) {
          const input = inside.querySelector('.composer input');
          if (input && !event.target.closest('button')) input.focus();
        }
        return;
      }

      // 点面板之外：没有对话内容就自动收回一条
      if (messageCount() === 0) {
        wantOpen = false;
        userClosed = false;
        schedule();
      }
    },
    true
  );

  document.addEventListener(
    'keydown',
    (event) => {
      if (event.key !== 'Escape') return;
      const ws = workspace();
      if (!ws || !ws.classList.contains(OPEN_CLASS)) return;
      closeDock();
      if (focusInside() && document.activeElement.blur) {
        document.activeElement.blur();
      }
    },
    true
  );

  /* ------------------------------------------------------------
     页面重渲染（#view 被整体替换）后重新同步状态
     ------------------------------------------------------------ */
  const view = document.getElementById('view');
  if (view && typeof MutationObserver === 'function') {
    new MutationObserver(schedule).observe(view, {
      childList: true,
      subtree: true
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', schedule, { once: true });
  } else {
    schedule();
  }
  window.addEventListener('resize', schedule);
  window.__xjAiDock = { open: openDock, close: closeDock, refresh: schedule };
})();
