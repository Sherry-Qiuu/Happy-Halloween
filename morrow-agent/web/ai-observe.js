/* ai-observe.js — 把「AI 观察」从模板换成真的生成结果
 *
 * 三种来源：
 *   auto   —— 每天到点（默认 21:30）自动生成一次，存进当天反思记录
 *   manual —— 用户点「生成观察」
 *   chat   —— 在反思页和 AI 聊完，点「结束并生成观察」
 *
 * 观察结果存在 data.reviews[<key>].insight，跟着账号数据一起同步，
 * 所以刷新、换设备都能看到；没生成过就显示入口，不假装有内容。
 */
(function () {
  'use strict';

  const HOUR_KEY = 'morrow-observe-hour';
  const DEFAULT_HOUR = 21.5; // 21:30
  let running = false;
  let lastCheck = 0;

  function entries() {
    return typeof data === 'object' && data ? data : null;
  }

  function currentEntry() {
    if (typeof review !== 'function') return null;
    return review();
  }

  function saveEntry(entry) {
    const doc = entries();
    if (!doc || typeof key !== 'function') return;
    doc.reviews = doc.reviews || {};
    doc.reviews[key()] = entry;
    if (typeof save === 'function') save();
  }

  function dueHour() {
    const stored = localStorage.getItem(HOUR_KEY);
    const value = stored === null ? NaN : Number(stored);
    return Number.isFinite(value) && value >= 0 && value <= 24 ? value : DEFAULT_HOUR;
  }

  function hourOptions(selected) {
    return [20, 21, 21.5, 22, 22.5, 23]
      .map((value) => {
        const label = clockLabel(value);
        return `<option value="${value}"${value === selected ? ' selected' : ''}>${label}</option>`;
      })
      .join('');
  }

  function hourPicker() {
    return (
      '<label class="observe-hour">每天 <select data-observe-hour>' +
      hourOptions(dueHour()) +
      '</select> 自动整理</label>'
    );
  }

  function clockLabel(hoursValue) {
    const whole = Math.floor(hoursValue);
    const minutes = Math.round((hoursValue - whole) * 60);
    return `${String(whole).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
  }

  function fmtTime(ts) {
    const date = new Date((ts || Date.now() / 1000) * 1000);
    return `${date.getMonth() + 1}-${String(date.getDate()).padStart(2, '0')} ${String(
      date.getHours()
    ).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  }

  function esc(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  const SOURCE_LABEL = {
    auto: '定时生成',
    manual: '手动生成',
    chat: '对话后生成',
    mock: '离线估算',
  };

  function renderInsight(entry) {
    const insight = entry && entry.insight;
    const body = document.getElementById('aiObserveBody');
    const label = document.getElementById('aiObserveLabel');
    if (!body) return;

    if (!insight) {
      if (label) label.textContent = '未生成';
      body.innerHTML =
        '<p class="insight-empty">到点会自动整理一次；也可以现在生成，' +
        '或者在反思页和 AI 聊完后生成。</p>' +
        '<div class="row"><button type="button" data-ai-observe="manual">生成今天的观察</button>' +
        hourPicker() +
        '</div>';
      return;
    }

    const facts = (insight.facts || [])
      .map((item) => `<li>${esc(item)}</li>`)
      .join('');
    const patterns = (insight.patterns || [])
      .map((item) => `<li>${esc(item)}</li>`)
      .join('');
    const question = (insight.questions || [])[0] || '';
    if (label) label.textContent = SOURCE_LABEL[insight.source] || '已生成';

    body.innerHTML =
      (facts ? `<p class="observe-title">记录到的事实</p><ul class="observe-list">${facts}</ul>` : '') +
      (patterns ? `<p class="observe-title">可能的模式</p><ul class="observe-list">${patterns}</ul>` : '') +
      (insight.experiment
        ? `<p class="observe-title">可以试的调整</p><p class="observe-text">${esc(insight.experiment)}</p>`
        : '') +
      (question ? `<p class="observe-q">${esc(question)}</p>` : '') +
      '<div class="observe-foot"><small>' +
      esc(fmtTime(insight.generated_at)) +
      ' · ' +
      esc(SOURCE_LABEL[insight.source] || '已生成') +
      '</small><button type="button" data-ai-observe="manual" class="observe-again">重新生成</button>' +
      hourPicker() +
      '</div>';
  }

  function renderAll() {
    const entry = currentEntry();
    if (!entry) return;
    renderInsight(entry);
  }

  function conversationText() {
    try {
      if (typeof chats !== 'object' || typeof chatkey !== 'function') return '';
      const list = chats[chatkey()] || [];
      return list
        .filter((message) => message.text)
        .slice(-6)
        .map((message) => `${message.role === 'user' ? '用户' : 'AI'}：${message.text}`)
        .join('\n');
    } catch (_error) {
      return '';
    }
  }

  async function generateObserve(mode) {
    if (running) return;
    running = true;
    const entry = currentEntry();
    if (!entry) {
      running = false;
      return;
    }
    if (typeof toast === 'function') toast('正在整理观察…');
    try {
      const state = typeof window.xjAppState === 'function' ? window.xjAppState() : {};
      const response = await fetch('/api/agent/observe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          state,
          reflection: entry.text || '',
          conversation: mode === 'chat' ? conversationText() : '',
        }),
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }
      if (!response.ok) throw new Error(payload.error || `服务端返回 ${response.status}`);
      const observation = payload.observation || {};
      observation.source = mode;
      entry.insight = observation;
      saveEntry(entry);
      if (typeof reportTab !== 'undefined') reportTab = true;
      if (typeof render === 'function') render();
      renderAll();
      if (typeof toast === 'function') toast('观察已更新');
    } catch (error) {
      if (typeof toast === 'function') toast(`生成失败：${error.message || error}`);
    } finally {
      running = false;
    }
  }

  /* 定时：到了设定时间、当天还没有定时生成过，就补一次 */
  function checkDue() {
    const now = Date.now();
    if (now - lastCheck < 60 * 1000) return;
    lastCheck = now;
    const entry = currentEntry();
    if (!entry) return;
    const hour = dueHour();
    const current = new Date();
    const dayHour = current.getHours() + current.getMinutes() / 60;
    if (dayHour < hour) return;
    if (entry.insight && entry.insight.source === 'auto') return;
    generateObserve('auto');
  }

  document.addEventListener('change', (event) => {
    const select = event.target.closest && event.target.closest('[data-observe-hour]');
    if (!select) return;
    localStorage.setItem(HOUR_KEY, select.value);
    lastCheck = 0;
    checkDue();
  });

  document.addEventListener('click', (event) => {
    const target = event.target.closest && event.target.closest('[data-ai-observe]');
    if (!target) return;
    event.preventDefault();
    generateObserve(target.getAttribute('data-ai-observe') || 'manual');
  });

  window.xjObserveNow = function xjObserveNow(mode) {
    return generateObserve(mode || 'manual');
  };
  window.xjObserveRender = renderAll;
  window.xjObserveDue = checkDue;

  const wrap = () => {
    if (typeof window.render === 'function' && !window.__xjObserveWrapped) {
      const base = window.render;
      window.render = function wrappedObserveRender() {
        const result = base.apply(this, arguments);
        renderAll();
        return result;
      };
      window.__xjObserveWrapped = true;
    }
    renderAll();
    checkDue();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wrap, { once: true });
  } else {
    wrap();
  }
  setInterval(checkDue, 5 * 60 * 1000);
})();
