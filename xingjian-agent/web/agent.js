/*
 * Xingjian - DeepSeek agent bridge.
 *
 * index.html is a self-contained prototype: its AI panel is a mock.
 * This file replaces the mock with a real streaming assistant without
 * editing the prototype's logic. It overrides four globals that the
 * prototype already calls (ai, send, events, generate) and leaves every
 * other interaction untouched.
 *
 * Contract with the server (see server.py):
 *   GET  /api/agent/health  -> {configured, model, mock}
 *   POST /api/agent/chat    -> text/event-stream (delta / proposal / error / done)
 *   POST /api/agent/report  -> {report}
 */
(function () {
  'use strict';

  const SEED_DATE = XJ_TODAY;

  const agent = {
    endpoint: '',
    reachable: false,
    configured: false,
    mock: false,
    model: '',
    streaming: false,
    controller: null,
    chatKey: null,
    startedAt: 0,
  };

  let messageSeq = 0;
  // 只有用户点「采用建议」时才会置位；没有它，任何 AI 改动都写不进时间表
  let approvalGate = null;

  // ------------------------------------------------------------------
  // Endpoint discovery
  // ------------------------------------------------------------------
  function readEndpoint() {
    const meta = document.querySelector('meta[name="agent-endpoint"]');
    const fromMeta = meta && meta.content ? meta.content.trim() : '';
    const fromQuery = new URLSearchParams(location.search).get('agent') || '';
    let endpoint = (fromQuery || fromMeta).replace(/\/+$/, '');
    if (!endpoint && location.protocol === 'file:') {
      endpoint = 'http://127.0.0.1:8000';
    }
    agent.endpoint = endpoint;
  }

  function apiURL(path) {
    return (agent.endpoint || '') + path;
  }

  // ------------------------------------------------------------------
  // Small helpers
  // ------------------------------------------------------------------
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const pad = (value) => String(value).padStart(2, '0');

  function clockOf(hours) {
    const whole = Math.floor(hours);
    const minutes = Math.round((hours - whole) * 60);
    return `${pad(whole)}:${pad(minutes)}`;
  }

  /* 本地日期，别用 toISOString（那是 UTC，早上 8 点前会差一天） */
  function localDate(value) {
    const d = value instanceof Date ? value : new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function toHours(text) {
    const [hour, minute] = String(text).split(':').map(Number);
    return hour + (minute || 0) / 60;
  }

  function span(start, duration) {
    const from = toHours(start);
    return `${start}–${clockOf(from + duration / 60)}`;
  }

  function withBreaks(text) {
    return esc(text || '').replace(/\n/g, '<br>');
  }

  function currentContext() {
    if (page === 'home') return 'home';
    if (page === 'project') return 'project';
    return 'review';
  }

  function friendlyError(error) {
    const message = (error && error.message) || String(error);
    if (/Failed to fetch|NetworkError|Load failed/i.test(message)) {
      return `连不上本地服务（${agent.endpoint || location.origin}）。` +
        '先运行 python server.py，再刷新页面。';
    }
    return message || '请求失败，请稍后再试';
  }

  // ------------------------------------------------------------------
  // Chat persistence: 每次打开页面 = 一次新对话，旧对话归档进历史
  // ------------------------------------------------------------------
  const CHAT_PREFIX_V1 = 'xingjian-agent-chats-v1';
  const CHAT_STORE_V2 = 'xingjian-agent-sessions-v2';
  const MAX_STORED = 60;
  const MAX_SESSIONS = 40;

  const sessionState = { active: {}, sessions: [] };

  function scopeName() {
    return window.XJ && XJ.scope ? XJ.scope() : 'guest';
  }

  function v1Key() {
    return `${CHAT_PREFIX_V1}:${scopeName()}`;
  }

  function v2Key() {
    return `${CHAT_STORE_V2}:${scopeName()}`;
  }

  function newSessionId() {
    return 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  function ensureSession(chatKey) {
    let id = sessionState.active[chatKey];
    let record = id ? sessionState.sessions.find((item) => item.id === id) : null;
    if (!record) {
      id = newSessionId();
      record = {
        id,
        chatKey,
        startedAt: Date.now(),
        endedAt: null,
        messages: [],
      };
      sessionState.sessions.push(record);
      sessionState.active[chatKey] = id;
    }
    return record;
  }

  /* 归档当前会话，并开一段新的（刷新页面时调用） */
  function startSession(chatKey) {
    const activeId = sessionState.active[chatKey];
    const current = chats[chatKey] || [];
    if (activeId) {
      const previous = sessionState.sessions.find((item) => item.id === activeId);
      if (previous) {
        if (current.length) {
          previous.messages = current.slice(-MAX_STORED);
          previous.endedAt = previous.endedAt || Date.now();
        } else if (!previous.messages.length) {
          sessionState.sessions = sessionState.sessions.filter(
            (item) => item.id !== activeId
          );
        }
      }
    }
    const record = {
      id: newSessionId(),
      chatKey,
      startedAt: Date.now(),
      endedAt: null,
      messages: [],
    };
    sessionState.sessions.push(record);
    sessionState.active[chatKey] = record.id;
    chats[chatKey] = [];
    trimSessions();
    return record.id;
  }

  function trimSessions() {
    const keep = sessionState.sessions.filter(
      (item) => item.messages.length || item.id === sessionState.active[item.chatKey]
    );
    sessionState.sessions = keep.slice(-MAX_SESSIONS);
    const alive = new Set(sessionState.sessions.map((item) => item.id));
    Object.keys(sessionState.active).forEach((key) => {
      if (!alive.has(sessionState.active[key])) delete sessionState.active[key];
    });
  }

  function readStored() {
    try {
      const raw = localStorage.getItem(v2Key());
      if (!raw) return false;
      const stored = JSON.parse(raw);
      if (!stored || stored.v !== 2) return false;
      sessionState.sessions = Array.isArray(stored.sessions) ? stored.sessions : [];
      sessionState.active = stored.active && typeof stored.active === 'object' ? stored.active : {};
      return true;
    } catch (_error) {
      return false;
    }
  }

  function migrateV1() {
    try {
      const raw = localStorage.getItem(v1Key());
      if (!raw) return;
      const stored = JSON.parse(raw);
      Object.keys(stored).forEach((key) => {
        if (!Array.isArray(stored[key]) || !stored[key].length) return;
        sessionState.sessions.push({
          id: newSessionId(),
          chatKey: key,
          startedAt: Date.now(),
          endedAt: Date.now(),
          messages: stored[key].map((message) => ({ ...message, pending: false })),
        });
      });
    } catch (_error) {
      /* 旧数据坏了就丢掉，不影响使用 */
    }
  }

  function loadChats() {
    if (!readStored()) migrateV1();
    // 本次页面加载 = 新会话：出现过的会话位全部归档并重开
    const keys = new Set(Object.keys(sessionState.active));
    ['home', 'project', 'review'].forEach((key) => keys.add(key));
    keys.forEach((key) => startSession(key));
    saveChats();
  }

  function saveChats() {
    try {
      Object.keys(chats).forEach((key) => {
        const record = ensureSession(key);
        record.messages = (chats[key] || []).slice(-MAX_STORED);
      });
      trimSessions();
      localStorage.setItem(
        v2Key(),
        JSON.stringify({
          v: 2,
          active: sessionState.active,
          sessions: sessionState.sessions,
        })
      );
    } catch (_error) {
      /* quota errors are not worth interrupting the demo */
    }
  }

  /* 历史抽屉用：本地归档的会话（新的在前） */
  window.xjLocalThreads = function xjLocalThreads() {
    const names = { home: '今天', project: '目标', review: '反思' };
    return sessionState.sessions
      .filter((item) => item.messages && item.messages.length)
      .slice()
      .sort((left, right) => (right.startedAt || 0) - (left.startedAt || 0))
      .map((item) => {
        const context = String(item.chatKey || '').split('-')[0];
        const firstUser = item.messages.find((message) => message.role === 'user');
        const stamp = new Date(item.startedAt || Date.now());
        const label = `${names[context] || '对话'} · ${stamp.getMonth() + 1}-${String(
          stamp.getDate()
        ).padStart(2, '0')} ${String(stamp.getHours()).padStart(2, '0')}:${String(
          stamp.getMinutes()
        ).padStart(2, '0')}`;
        return {
          key: item.id,
          context,
          label,
          count: item.messages.filter((message) => message.role === 'user').length,
          preview: firstUser ? String(firstUser.text || '').slice(0, 40) : '',
          messages: item.messages
            .filter((message) => message.text)
            .map((message) => ({ role: message.role, text: message.text })),
        };
      });
  };

  function findMessage(id) {
    const list = chats[chatkey()] || [];
    return list.find((message) => message.id === id) || null;
  }

  // ------------------------------------------------------------------
  // App state for the model
  // ------------------------------------------------------------------
  function scheduleRows(date) {
    const rows = [];
    ensureSchedule().forEach((event) => {
      if (event.date === date && !event.actual) {
        rows.push({
          title: event.title,
          date: event.date,
          start: clockOf(event.start),
          duration: event.dur,
        });
      }
    });
    (data.extra || []).forEach((event) => {
      if (event.date === date && !event.actual) {
        rows.push({
          title: event.title,
          date: event.date,
          start: clockOf(event.start),
          duration: event.dur,
        });
      }
    });
    return rows.sort((left, right) => left.start.localeCompare(right.start));
  }

  function usedRows(date) {
    const rows = [];
    if (date === SEED_DATE) {
      SEED_ACTUAL.forEach((event) => {
        rows.push({
          title: event.title,
          date: event.date,
          start: clockOf(event.start),
          duration: event.dur,
          estimated: true,
        });
      });
    }
    (data.extra || []).forEach((event) => {
      if (event.date === date && event.actual) {
        rows.push({
          title: event.title,
          date: event.date,
          start: clockOf(event.start),
          duration: event.dur,
        });
      }
    });
    return rows;
  }

  /* 首页用的是独立的 hs 数据（时间块 / 待办 / 实际记录），
     模型必须拿到同一份，否则会反问"看不到排期"。 */
  function homeModel() {
    try {
      if (typeof hs === 'object' && hs && Array.isArray(hs.blocks)) return hs;
    } catch (_error) {
      /* page without the home layer */
    }
    return null;
  }

  function visibleHomeDays() {
    try {
      return [0, 1, 2].map((offset) => shiftDate(anchor, offset));
    } catch (_error) {
      return [date];
    }
  }

  function homeRows(iso) {
    const model = homeModel();
    if (!model) return { scheduled: [], used: [] };
    const scheduled = (model.blocks || [])
      .filter((block) => block.date === iso)
      .map((block) => ({
        title: block.title,
        date: block.date,
        start: clockOf(block.start),
        duration: Math.max(5, Math.round((block.end - block.start) * 60)),
        locked: !!block.locked,
      }))
      .sort((left, right) => left.start.localeCompare(right.start));
    const used = (model.actual || [])
      .filter((block) => block.date === iso)
      .map((block) => ({
        title: block.title,
        date: block.date,
        start: clockOf(block.start),
        duration: Math.max(5, Math.round((block.end - block.start) * 60)),
        estimated: !!block.estimated,
      }))
      .sort((left, right) => left.start.localeCompare(right.start));
    return { scheduled, used };
  }

  function saveHome() {
    try {
      if (typeof hsave === 'function') hsave();
      return true;
    } catch (_error) {
      return false;
    }
  }

  function appState() {
    const now = new Date();
    const today = localDate(now);
    const day = page === 'weekly' ? null : date;
    const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
    const goals = (data.goals || []).map((item, index) => ({
      index,
      title: item.title,
      standard: item.standard,
      expected_date: item.date,
      stage: item.stage || 0,
      stage_name: (item.stages || [])[item.stage || 0] || null,
      next_actions: (item.actions || []).slice(0, 4),
    }));
    const home = homeModel();
    const state = {
      today,
      today_label: `${today} ${weekdays[now.getDay()]}`,
      now: `${pad(now.getHours())}:${pad(now.getMinutes())}`,
      timezone: 'Asia/Shanghai',
      page,
      view: page === 'weekly' ? 'weekly' : 'day',
      viewing_date: day,
      user_name: data.name || '明',
      tasks: home
        ? home.tasks.map((task) => ({
            title: task.title,
            minutes: task.minutes,
            goal: task.goal,
            done: !!task.done,
            tags: task.tags || '',
            priority: task.priority || '',
            span: task.start && task.end ? `${task.start} → ${task.end}` : '',
            locked: !!task.locked,
          }))
        : (data.tasks || []).map((task) => ({
            title: task.title,
            minutes: task.minutes,
            goal: task.goal,
            done: !!task.done,
          })),
      goals,
      current_goal_index: goal,
      reflection: (review().text || '').slice(0, 1500),
      review_tags: review().tags || [],
      reviewed_report: (review().report || '').slice(0, 800),
    };
    if (home) {
      const days = visibleHomeDays();
      state.schedule_days = days;
      state.scheduled = days.flatMap((iso) => homeRows(iso).scheduled);
      state.used = days.flatMap((iso) => homeRows(iso).used);
      state.locked_titles = (home.blocks || [])
        .filter((block) => block.locked)
        .map((block) => block.title);
      state.today_tasks = home.tasks
        .filter((task) => task.start <= date && task.end >= date)
        .map((task) => task.title);
    } else if (page === 'weekly') {
      state.week = [0, 1, 2, 3, 4, 5, 6].map((offset) => {
        const base = selected.startsWith('week-')
          ? new Date(`${selected.slice(5)}T12:00:00`)
          : new Date(`${SEED_DATE}T12:00:00`);
        base.setDate(base.getDate() + offset);
        const iso = localDate(base);
        return {
          date: iso,
          scheduled: scheduleRows(iso),
          used: usedRows(iso),
        };
      });
    } else {
      state.scheduled = scheduleRows(day);
      state.used = usedRows(day);
    }
    return state;
  }

  // ------------------------------------------------------------------
  // Transport
  // ------------------------------------------------------------------
  async function postJSON(path, body) {
    const response = await fetch(apiURL(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload && payload.error ? payload.error : '';
      throw new Error(detail || `服务端返回 ${response.status}`);
    }
    return payload || {};
  }

  function dispatchFrame(frame, handlers) {
    let name = 'message';
    const dataLines = [];
    frame.split('\n').forEach((line) => {
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    });
    if (!dataLines.length) return;
    let payload = null;
    try {
      payload = JSON.parse(dataLines.join('\n'));
    } catch (_error) {
      return;
    }
    if (name === 'delta' && payload.text) handlers.onDelta(payload.text);
    else if (name === 'proposal') handlers.onProposal(payload);
    else if (name === 'clarify' && handlers.onClarify) handlers.onClarify(payload);
    else if (name === 'status' && handlers.onStatus) handlers.onStatus(payload);
    else if (name === 'error') handlers.onError(payload.message || '出错了');
  }

  async function streamChat(body, handlers, signal) {
    const response = await fetch(apiURL('/api/agent/chat'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) {
      let detail = `服务端返回 ${response.status}`;
      try {
        const payload = await response.json();
        if (payload && payload.error) detail = payload.error;
      } catch (_error) {
        /* keep the status line */
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
      buffer = (buffer + decoder.decode(value, { stream: true }))
        .replace(/\r\n/g, '\n');
      let index = buffer.indexOf('\n\n');
      while (index !== -1) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        dispatchFrame(frame, handlers);
        index = buffer.indexOf('\n\n');
      }
    }
    if (buffer.trim()) dispatchFrame(buffer, handlers);
  }

  // ------------------------------------------------------------------
  // Web Speech API input (shared by every AI composer)
  // ------------------------------------------------------------------
  const speech = {
    recognition: null,
    input: null,
    active: false,
  };

  function speechSupported() {
    return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  function updateSpeechButtons() {
    document.querySelectorAll('[data-mic], [data-hvoice], #goalVoice').forEach((button) => {
      button.textContent = speech.active ? '■' : '♩';
      button.classList.toggle('is-listening', speech.active);
      button.setAttribute('aria-pressed', speech.active ? 'true' : 'false');
      button.title = speech.active ? '停止语音输入' : '开始语音输入';
    });
  }

  function stopSpeech() {
    if (speech.recognition) {
      try { speech.recognition.stop(); } catch (_error) { /* already stopped */ }
    }
    speech.active = false;
    speech.recognition = null;
    speech.input = null;
    updateSpeechButtons();
  }

  const MIC_HELP = [
    '麦克风被浏览器拦住了。三步打开：',
    '1) 地址栏左侧的 🔒/⚙ 图标 → 网站设置 → 麦克风 → 允许；',
    '2) 刷新页面后重新点麦克风；',
    '3) 还不行就检查 Windows 设置 → 隐私和安全性 → 麦克风，允许桌面应用访问。',
    '改的过程中可以直接打字，文字输入一样能对话。',
  ];

  async function microphoneState() {
    try {
      if (!navigator.permissions || !navigator.permissions.query) return 'unknown';
      const status = await navigator.permissions.query({ name: 'microphone' });
      return status.state;
    } catch (_error) {
      return 'unknown';
    }
  }

  function explainMicrophone() {
    MIC_HELP.forEach((line, index) => {
      setTimeout(() => toast(line), index * 1200);
    });
    const first = document.querySelector('.workspace .composer input, .workspace .hai .composer input');
    if (first) {
      first.placeholder = '麦克风被禁用：直接在这里输入文字也可以';
      first.focus();
    }
  }

  async function toggleSpeech(input) {
    if (speech.active) {
      stopSpeech();
      toast('已停止语音输入');
      return false;
    }
    if (!speechSupported()) {
      toast('当前浏览器不支持语音识别，请直接输入文字');
      return false;
    }
    const state = await microphoneState();
    if (state === 'denied') {
      explainMicrophone();
      return false;
    }
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new Recognition();
    recognition.lang = document.documentElement.lang === 'zh-CN' ? 'zh-CN' : 'zh-CN';
    recognition.continuous = true;
    recognition.interimResults = true;
    speech.recognition = recognition;
    speech.input = input;
    speech.active = true;
    updateSpeechButtons();
    toast('正在听，请说话；再次点击停止');
    let committed = input.value || '';
    recognition.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) committed += (committed ? ' ' : '') + transcript.trim();
        else interim += transcript;
      }
      if (speech.input === input) {
        input.value = (committed + (interim ? ` ${interim}` : '')).trim();
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    };
    recognition.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        explainMicrophone();
      } else if (event.error === 'audio-capture') {
        toast('没有检测到麦克风设备，请检查系统音频输入后重试');
      } else if (event.error === 'network') {
        toast('语音识别需要联网（浏览器把它交给云端处理），网络被拦截时可改用文字输入');
      } else if (event.error !== 'aborted') {
        toast(`语音识别失败（${event.error || '未知错误'}），请直接输入文字`);
      }
      stopSpeech();
    };
    recognition.onend = () => {
      if (speech.recognition === recognition) stopSpeech();
    };
    try { recognition.start(); } catch (error) {
      stopSpeech();
      toast(`无法开始语音识别：${error.message || '请直接输入文字'}`);
    }
    return true;
  }

  window.xjVoiceInput = toggleSpeech;

  // Structured domain calls used by goal/replan/reflection surfaces.
  window.xjStructured = async function xjStructured(path, payload) {
    const response = await fetch(apiURL(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    let body = {};
    try { body = await response.json(); } catch (_error) { /* keep status */ }
    if (!response.ok) throw new Error(body.error || `服务端返回 ${response.status}`);
    return body;
  };

  // 服务端长期记忆里的历史会话（跨设备、跨刷新）
  window.xjAgentHistory = async function xjAgentHistory() {
    const response = await fetch(apiURL('/api/agent/history'), {
      cache: 'no-store',
    });
    if (!response.ok) throw new Error(`历史服务返回 ${response.status}`);
    return response.json();
  };

  // 服务端画像（行为推断 + 显式设定）
  window.xjAgentProfile = async function xjAgentProfile() {
    const response = await fetch(apiURL('/api/agent/profile'), {
      cache: 'no-store',
    });
    if (!response.ok) throw new Error(`画像服务返回 ${response.status}`);
    return response.json();
  };


  // ------------------------------------------------------------------
  // Panel rendering
  // ------------------------------------------------------------------
  const CHANGES = {
    move_event: {
      tag: 'move',
      label: (change) =>
        `${change.title} → ${change.start}（${change.duration} 分钟）`,
    },
    add_event: {
      tag: 'new',
      label: (change) =>
        `${change.title} · ${change.date} ${change.start}（${change.duration} 分钟）`,
    },
    remove_event: {
      tag: 'drop',
      label: (change) => `${change.title} · ${change.date}`,
    },
    add_task: {
      tag: 'todo',
      label: (change) => `${change.title}（${change.minutes} 分钟）`,
    },
    add_next_action: {
      tag: 'next',
      label: (change) => `${change.text}（${change.minutes} 分钟）`,
    },
    log_actual: {
      tag: 'log',
      label: (change) =>
        `${change.title} · ${change.start}（${change.duration} 分钟）`,
    },
    add_goal: {
      tag: 'goal',
      label: (change) => {
        const milestones = Array.isArray(change.milestones)
          ? change.milestones.length
          : 0;
        const tail = change.date ? ' · 截止 ' + change.date : '';
        const steps = milestones ? ` · ${milestones} 个里程碑` : '';
        return `${change.title}${tail}${steps}`;
      },
    },
  };

  const CHANGE_TAGS = {
    move_event: '调整',
    add_event: '新增',
    remove_event: '删除',
    add_task: '待办',
    add_next_action: '下一步',
    log_actual: '补记',
    add_goal: '目标',
  };

  function clarifyHTML(message) {
    const clarify = message.clarify || {};
    const questions = clarify.questions || [];
    if (!questions.length) return '';
    const rows = questions
      .map((item, index) => {
        const choices = item.choices || [];
        const chips = choices
          .map(
            (choice) =>
              `<button type="button" class="clarifychip" data-clarify-choice ` +
              `data-q="${index}" data-value="${esc(choice)}">${esc(choice)}</button>`
          )
          .join('');
        return (
          `<li><p class="clarifyq">${esc(item.q || '')}</p>` +
          (chips ? `<div class="clarifychips">${chips}</div>` : '') +
          '</li>'
        );
      })
      .join('');
    return (
      '<div class="clarify">' +
      `<div class="clarifysummary">${esc(clarify.summary || '先确认几件事')}</div>` +
      `<ol class="clarifylist">${rows}</ol>` +
      '<div class="refinerow"><button type="button" class="primary" data-clarify-submit ' +
      `data-msg="${message.id}">提交回答</button></div>` +
      '</div>'
    );
  }

  function submitClarify(messageId) {
    const message = findMessage(Number(messageId));
    if (!message || !message.clarify) return;
    const questions = message.clarify.questions || [];
    const picked = Array.from(document.querySelectorAll('[data-clarify-choice].picked'));
    const answers = [];
    questions.forEach((item, index) => {
      const hit = picked.filter((node) => Number(node.dataset.q) === index);
      if (hit.length) {
        answers.push(`${item.q}${hit.map((node) => node.dataset.value).join('、')}`);
      }
    });
    const input = document.querySelector('.workspace .hai .composer input, .workspace .ai .composer input');
    const typed = input ? input.value.trim() : '';
    const text = [answers.join('；'), typed].filter(Boolean).join('；');
    if (!text) {
      toast('先选一个答案，或直接写一句');
      return;
    }
    if (input) input.value = '';
    window.send(text);
  }

  function proposalHTML(message) {
    const proposal = message.proposal;
    const items = (proposal.changes || [])
      .map((change) => {
        const meta = CHANGES[change.type];
        const label = meta ? meta.label(change) : change.type;
        const tag = CHANGE_TAGS[change.type] || '';
        const badge = tag ? `<span class="chgtag">${tag}</span>` : '';
        return `<li>${badge}${esc(label)}</li>`;
      })
      .join('');
    const actions = message.applied
      ? '<small class="muted">已应用到你今天的计划</small>'
      : `<div class="row"><button class="primary" ` +
        `data-agent-apply="${message.id}">采用建议</button>` +
        `<button data-agent-keep="${message.id}">保留现状</button></div>` +
        '<div class="refinerow">' +
        '<button type="button" data-agent-refine="finer">拆细一点</button>' +
        '<button type="button" data-agent-refine="coarser">合并一些</button>' +
        '<button type="button" data-agent-refine="explain">说说你的考虑</button>' +
        '</div>';
    const bill = proposal.bill || null;
    const billRow = bill
      ? '<div class="billrow">' +
        `<span>改动 ${bill.changes ?? (proposal.changes || []).length} 项</span>` +
        (bill.goal_pct !== undefined ? `<span>目标保留 ${bill.goal_pct}%</span>` : '') +
        (bill.slack_minutes !== undefined ? `<span>留白 ${bill.slack_minutes} 分钟</span>` : '') +
        (bill.notifications ? `<span>需通知 ${bill.notifications}</span>` : '') +
        '</div>'
      : '';
    const cross = proposal.cross_check;
    let crossBlock = '';
    if (cross && cross.agrees === false && (cross.changes || []).length) {
      const rows = cross.changes
        .map((change) => {
          const meta = CHANGES[change.type];
          return `<li>${esc(meta ? meta.label(change) : change.type)}</li>`;
        })
        .join('');
      crossBlock =
        '<div class="crossbox">' +
        '<div class="crosshead"><strong>策略引擎给了不同方案</strong>' +
        `<small>${esc(cross.summary || '')}</small></div>` +
        `<ul class="changelist crosslist">${rows}</ul>` +
        '<div class="refinerow">' +
        `<button type="button" data-planner-apply="${message.id}">改用策略方案</button>` +
        '</div></div>';
    } else if (cross && cross.agrees) {
      crossBlock = '<div class="crossok">策略引擎核算一致</div>';
    }
    return (
      '<div class="proposal">' +
      `<div class="proposalsummary"><em class="pendtag">建议 · 未生效</em>${esc(proposal.summary || '')}</div>` +
      (items ? `<ul class="changelist">${items}</ul>` : '') +
      billRow +
      crossBlock +
      actions +
      '</div>'
    );
  }

  function messageHTML(message) {
    if (message.role === 'user') {
      return `<div class="message user">${withBreaks(message.text)}</div>`;
    }
    let content = '';
    if (message.text) content = withBreaks(message.text);
    else if (message.pending) {
      content =
        '<span class="typing"><i></i><i></i><i></i></span>' +
        (message.status ? `<small class="agentstatus">${esc(message.status)}</small>` : '');
    }
    if (message.error) {
      content += `<div class="agenterror">${esc(message.error)}</div>`;
    }
    const card = message.clarify
      ? clarifyHTML(message)
      : message.proposal
      ? proposalHTML(message)
      : '';
    if (!content) return card;
    const nudge =
      !card && message.text && !message.pending
        ? '<div class="refinerow"><button type="button" data-agent-refine="act">' +
          '直接给方案</button></div>'
        : '';
    return `<div class="message assistant">${content}</div>${card}${nudge}`;
  }

  function emptyHTML(context) {
    const welcome =
      context === 'home'
        ? '今天有变化，随时告诉我。'
        : context === 'project'
        ? '从这个目标的下一步开始。'
        : page === 'weekly'
        ? '这一周，什么值得继续？'
        : '今天有什么变化？';
    const hint =
      context === 'home'
        ? '调整计划、补记付出，都可以从一句话开始。'
        : context === 'project'
        ? '你决定方向，我帮你把行动变具体。'
        : '你写自己的反思，我帮你整理事实与观察。';
    const meta = agent.configured
      ? `<small class="agenthint">DeepSeek · ${esc(agent.model)}</small>`
      : '<small class="agenthint">离线演示 · 未连接 AI 服务</small>';
    const example =
      context === 'home' ? '下午临时开会，帮我调整' : '帮我想清楚下一步';
    return (
      `<p class="welcome">${welcome}</p>` +
      `<small>${hint}</small><br>${meta}` +
      '<div class="chips" style="margin-top:8px">' +
      `<button data-example="${context}">${example}</button></div>`
    );
  }

  function statusTitle() {
    if (agent.mock) return '本地模拟模式：回复由服务端 mock 生成，配置 DEEPSEEK_API_KEY 后自动切换真实模型';
    if (agent.configured) return `DeepSeek 已连接 · ${agent.model}`;
    if (agent.reachable) return '服务端在线，但还没有配置 DEEPSEEK_API_KEY';
    return `没有连上 ${agent.endpoint || location.origin}`;
  }

  window.ai = function ai(context) {
    const key = chatkey();
    const list = chats[key] || [];
    const label =
      context === 'home'
        ? '今天'
        : context === 'project'
        ? '当前目标'
        : page === 'weekly'
        ? '本周'
        : '当天';
    const dot = agent.configured ? 'ok' : agent.reachable ? 'warn' : 'off';
    const collapse = expanded
      ? '<button data-collapse style="border:0;min-height:25px;' +
        'padding:0 6px;font-size:12px">收起</button>'
      : '';
    const body = list.length
      ? list.map(messageHTML).join('')
      : emptyHTML(context);
    const composer = agent.streaming && agent.chatKey === key
      ? '<form class="composer"><input aria-label="与 AI 对话" ' +
        'placeholder="正在回复…"><button type="button" class="primary" ' +
        'data-agent-stop aria-label="停止生成">■</button></form>'
      : '<form class="composer"><input aria-label="与 AI 对话" ' +
        'placeholder="说说你的想法…"><button type="button" data-mic ' +
        'title="开始语音输入" aria-label="语音输入" aria-pressed="false">♩</button>' +
        '<button class="primary" aria-label="发送">↑</button></form>';
    const endChat =
      context === 'review'
        ? '<button class="endchat" data-end>结束并生成观察</button>'
        : '';
    return (
      `<section class="ai" data-context="${context}">` +
      '<div class="aihead"><span><span class="spark">✧</span>Morrow AI ' +
      `<small>· ${label}</small>` +
      `<span class="agentdot ${dot}" title="${esc(statusTitle())}"></span>` +
      `</span>${collapse}</div>` +
      `<div class="aibody">${body}</div>${composer}${endChat}` +
      '</section>'
    );
  };

  // ------------------------------------------------------------------
  // Sending
  // ------------------------------------------------------------------
  function historyFor(list) {
    const rows = [];
    list.forEach((message) => {
      if (!message.text || message.error) return;
      rows.push({ role: message.role, content: message.text });
    });
    return rows.slice(-10);
  }

  function scrollBody() {
    const body = document.querySelector('.aibody');
    if (body) body.scrollTop = body.scrollHeight;
  }

  function lastAssistantNode() {
    const nodes = document.querySelectorAll('.aibody .message.assistant');
    return nodes.length ? nodes[nodes.length - 1] : null;
  }

  function stopStream() {
    if (agent.controller) agent.controller.abort();
  }

  window.send = async function send(value) {
    const text = String(value == null ? '' : value).trim();
    if (!text) return;
    if (agent.streaming) {
      toast('AI 还在回复，先停一下再说');
      return;
    }

    const context = currentContext();
    const key = chatkey();
    const list = chats[key] || (chats[key] = []);
    list.push({ role: 'user', text, id: ++messageSeq });

    const reply = {
      role: 'assistant',
      text: '',
      pending: true,
      proposal: null,
      id: ++messageSeq,
      chatKey: key,
    };
    list.push(reply);
    expanded = true;
    agent.streaming = true;
    agent.chatKey = key;
    agent.startedAt = Date.now();

    if (!agent.configured) {
      saveChats();
      render();
      scrollBody();
      await offlineReply(list, text, context, reply);
      return;
    }

    saveChats();
    render();
    scrollBody();

    const controller = new AbortController();
    agent.controller = controller;
    try {
      await streamChat(
        { context, messages: historyFor(list), state: appState() },
        {
          onDelta(chunk) {
            reply.text += chunk;
            reply.pending = false;
            if (chatkey() !== reply.chatKey) return;
            const node = lastAssistantNode();
            if (node) node.textContent = reply.text;
            scrollBody();
          },
          onStatus(status) {
            reply.status = (status && status.text) || '';
            if (!reply.text) reply.pending = true;
            render();
          },
          onClarify(clarify) {
            reply.clarify = clarify;
            reply.pending = false;
            saveChats();
            render();
            scrollBody();
          },
          onProposal(proposal) {
            reply.proposal = proposal;
            reply.pending = false;
            // 有建议时切回「计划」视图，改动才看得见
            try {
              if (page === 'home' && typeof hview !== 'undefined' && hview !== 'plan') {
                hview = 'plan';
              }
            } catch (_error) {
              /* 首页层没加载时忽略 */
            }
            saveChats();
            render();
            scrollBody();
          },
          onError(message) {
            reply.error = message;
            reply.pending = false;
          },
        },
        controller.signal
      );
    } catch (error) {
      if (error && error.name === 'AbortError') {
        reply.error = '已停止生成';
        if (!reply.text) reply.text = '';
      } else {
        reply.error = friendlyError(error);
      }
    } finally {
      agent.streaming = false;
      agent.controller = null;
      reply.pending = false;
      saveChats();
      render();
      scrollBody();
    }
  };

  async function offlineReply(list, text, context, reply) {
    await sleep(420);
    const demo = offlineContent(text, context);
    reply.pending = false;
    reply.text = demo.text;
    reply.offline = true;
    if (demo.proposal) reply.proposal = demo.proposal;
    agent.streaming = false;
    agent.chatKey = null;
    saveChats();
    render();
    scrollBody();
  }

  function offlineContent(text, context) {
    const wantsChange = /会|调整|推迟|改到|临时|冲突|来不及/.test(text);
    const head = agent.reachable
      ? '（离线演示）服务端在线，但还没有配置 DEEPSEEK_API_KEY。'
      : '（离线演示）没有连上本地 AI 服务，先运行 python server.py。';
    if (!wantsChange) {
      return {
        text:
          `${head}这次先用本地规则回应：${text.slice(0, 20)}。` +
          '配好 key 之后，这里会是真实模型的回答。',
        proposal: null,
      };
    }
    return {
      text:
        `${head}先用本地规则给一版草案：14:00 到 16:00 被会议占掉之后，` +
        '英语练习的余量只剩傍晚，先把练习挪到 16:30。',
      proposal: {
        summary: '把英语练习挪到 16:30，并把临时会议写进 14:00–16:00',
        changes: [
          {
            type: 'move_event',
            date: SEED_DATE,
            title: '英语面试 · 自我介绍',
            start: '16:30',
            duration: 30,
          },
          {
            type: 'add_event',
            date: SEED_DATE,
            title: '临时会议',
            start: '14:00',
            duration: 120,
          },
        ],
      },
    };
  }

  // ------------------------------------------------------------------
  // Applying proposals
  // ------------------------------------------------------------------
  const SEED_SCHEDULE = [
    {
      title: '英语面试 · 自我介绍',
      date: SEED_DATE,
      start: 14,
      dur: 30,
      color: '',
      note: '14:00–14:30',
    },
    {
      title: '整理作品集素材',
      date: SEED_DATE,
      start: 11,
      dur: 45,
      color: 'blue',
      note: '11:00–11:45',
    },
  ];

  const SEED_ACTUAL = [
    {
      title: '英语练习',
      date: SEED_DATE,
      start: 9,
      dur: 25,
      color: '',
      note: '09:00–09:25 · 已记录',
    },
    {
      title: '临时整理资料',
      date: SEED_DATE,
      start: 10.3333,
      dur: 40,
      color: 'blue',
      note: '10:20–11:00 · 估算',
    },
  ];

  function ensureSchedule() {
    if (Array.isArray(data.schedule)) return data.schedule;
    data.schedule = SEED_SCHEDULE.map((event) => ({ ...event }));
    if (data.moved) {
      const target = data.schedule.find((event) =>
        event.title.startsWith('英语面试')
      );
      if (target) {
        target.start = 16.5;
        target.note = '16:30–17:00';
      }
      data.schedule.push({
        title: '临时会议',
        date: SEED_DATE,
        start: 14,
        dur: 120,
        color: 'sand',
        note: '14:00–16:00',
      });
    }
    save();
    return data.schedule;
  }

  function findScheduled(date, title) {
    const rows = ensureSchedule().filter((event) => event.date === date);
    return (
      rows.find((event) => event.title === title) ||
      rows.find((event) => event.title.includes(title) ||
        title.includes(event.title)) ||
      null
    );
  }

  function nextTaskId() {
    return (data.tasks || []).reduce(
      (max, task) => Math.max(max, Number(task.id) || 0),
      0
    ) + 1;
  }

  function applyChange(change) {
    // 没有用户确认就拒绝任何写入：AI 只能提议，不能改表
    if (!approvalGate) return false;
    // 目标存在 data.goals（首页和目标页共用），先单独处理
    if (change.type === 'add_goal') return applyGoalAdd(change);
    // 首页的时间块 / 待办存在 hs 里，改动要落到同一份数据上，
    // 并且写入前留一份快照，让「撤销上次修改」可用。
    if (page === 'home' && homeModel()) return applyHomeChange(change);
    switch (change.type) {
      case 'add_task': {
        data.tasks = data.tasks || [];
        data.tasks.push({
          id: nextTaskId(),
          title: change.title,
          minutes: change.minutes,
          goal: change.goal,
          done: false,
        });
        return true;
      }
      case 'add_event': {
        ensureSchedule().push({
          title: change.title,
          date: change.date,
          start: toHours(change.start),
          dur: change.duration,
          color: colorForChange(change),
          note: span(change.start, change.duration),
        });
        return true;
      }
      case 'move_event': {
        const target = findScheduled(change.date, change.title);
        if (!target) return false;
        target.start = toHours(change.start);
        target.dur = change.duration;
        target.note = span(change.start, change.duration);
        return true;
      }
      case 'remove_event': {
        const rows = ensureSchedule();
        const index = rows.findIndex(
          (event) =>
            event.date === change.date && event.title === change.title
        );
        if (index === -1) return false;
        rows.splice(index, 1);
        return true;
      }
      case 'add_next_action': {
        const goalItem = (data.goals || [])[change.goal_index];
        if (!goalItem) return false;
        goalItem.actions = goalItem.actions || [];
        goalItem.actions.push(`${change.text} · ${change.minutes} 分钟`);
        return true;
      }
      case 'log_actual': {
        data.extra = data.extra || [];
        data.extra.push({
          title: change.title,
          date: change.date,
          start: toHours(change.start),
          dur: change.duration,
          actual: true,
          note: `${span(change.start, change.duration)} · 补记`,
        });
        return true;
      }
      default:
        return false;
    }
  }

  function homeUndoSnapshot() {
    try {
      if (typeof hundo !== 'undefined') hundo = structuredClone(homeModel());
    } catch (_error) {
      /* undo is a bonus, never block the change */
    }
  }

  const CHANGE_COLORS = { 学习: 'blue', 工作: 'gold', 生活: 'green', 个人成长: 'purple', 临时支持: 'pink' };

  function colorForChange(change) {
    const text = `${change.category || ''} ${change.title || ''} ${change.goal || ''}`;
    for (const [key, value] of Object.entries(CHANGE_COLORS)) {
      if (text.includes(key)) return value;
    }
    if (/论文|实验|投稿|课程|学习|阅读/.test(text)) return 'blue';
    if (/面试|投递|会议|工作|汇报/.test(text)) return 'gold';
    return 'blue';
  }

  function tagForChange(change) {
    const text = `${change.category || ''} ${change.title || ''} ${change.goal || ''}`;
    if (/论文|实验|投稿|课程|学习|阅读|复习|练习/.test(text)) return '学习';
    if (/面试|投递|会议|汇报|工作|项目|周报/.test(text)) return '工作';
    if (/运动|做饭|休息|散步|生活/.test(text)) return '生活';
    if (/反思|复盘|日记|冥想|成长/.test(text)) return '个人成长';
    if (/临时|帮忙|协作/.test(text)) return '临时支持';
    return change.category || '工作';
  }

  function applyHomeChange(change) {
    const model = homeModel();
    if (!model) return false;
    const stamp = () => Math.random().toString(36).slice(2, 10);
    const blockOf = (iso, title) =>
      (model.blocks || []).find(
        (block) => block.date === iso && block.title === title
      );
    switch (change.type) {
      case 'add_task': {
        homeUndoSnapshot();
        model.tasks = model.tasks || [];
        model.tasks.push({
          id: stamp(),
          title: change.title,
          tags: '',
          priority: '中',
          start: date,
          end: date,
          subtasks: [],
          description: '',
          locked: false,
          goal: change.goal || '',
          minutes: change.minutes,
          done: false,
        });
        return saveHome();
      }
      case 'add_event': {
        homeUndoSnapshot();
        model.blocks = model.blocks || [];
        const start = toHours(change.start);
        model.blocks.push({
          id: stamp(),
          date: change.date,
          title: change.title,
          start,
          end: start + change.duration / 60,
          tags: tagForChange(change),
          color: colorForChange(change),
        });
        return saveHome();
      }
      case 'move_event': {
        const target = blockOf(change.date, change.title);
        if (!target) return false;
        homeUndoSnapshot();
        target.start = toHours(change.start);
        target.end = target.start + change.duration / 60;
        return saveHome();
      }
      case 'remove_event': {
        const target = blockOf(change.date, change.title);
        if (!target) return false;
        homeUndoSnapshot();
        model.blocks = model.blocks.filter((block) => block !== target);
        return saveHome();
      }
      case 'log_actual': {
        homeUndoSnapshot();
        model.actual = model.actual || [];
        const start = toHours(change.start);
        model.actual.push({
          id: stamp(),
          date: change.date,
          title: change.title,
          start,
          end: start + change.duration / 60,
        });
        return saveHome();
      }
      case 'add_next_action':
        return applyGoalNextAction(change);
      default:
        return false;
    }
  }

  function applyGoalAdd(change) {
    data.goals = data.goals || [];
    const title = String(change.title || '').trim();
    if (!title) return false;
    if (data.goals.some((goal) => goal.title === title)) return false;
    const milestones = Array.isArray(change.milestones)
      ? change.milestones.filter((item) => item && item.title)
      : [];
    const stages = milestones.length
      ? milestones.map((item) =>
          item.date ? `${item.title} · ${item.date}` : item.title
        )
      : ['明确第一阶段成果', '推进关键行动', '验证并完成目标'];
    data.goals.push({
      title,
      standard: change.standard || title,
      date: change.date || '',
      category: change.category || '学习',
      budget: 10,
      rangeStart: '',
      rangeEnd: change.date || '',
      stages,
      stage: 0,
      outcomes: [],
      actions: [],
      projectTasks: [],
      spentMinutes: 0,
    });
    return true;
  }

  function applyGoalNextAction(change) {
    const goalItem = (data.goals || [])[change.goal_index];
    if (!goalItem) return false;
    goalItem.actions = goalItem.actions || [];
    goalItem.actions.push(`${change.text} · ${change.minutes} 分钟`);
    return true;
  }

  function applyProposal(id) {
    const message = findMessage(id);
    if (!message || !message.proposal) return;
    let applied = 0;
    let missed = 0;
    approvalGate = { messageId: message.id, source: 'ai', at: Date.now() };
    try {
      (message.proposal.changes || []).forEach((change) => {
        if (applyChange(change)) applied += 1;
        else missed += 1;
      });
    } finally {
      approvalGate = null;
    }
    if (applied) {
      if (page === 'home' && homeModel()) {
        // 改动后把日历定位到受影响的那一天，别让用户以为没生效
        try {
          const iso = (message.proposal.changes || [])
            .map((change) => change.date)
            .filter(Boolean)[0];
          if (iso) {
            anchor = shiftDate(iso, -1);
            date = iso;
            hview = 'plan';
          }
        } catch (_error) {
          /* 首页层未加载时忽略 */
        }
        saveHome();
      } else {
        save();
      }
    }
    message.applied = applied > 0;
    saveChats();
    render();
    if (page === 'home' && applied) {
      // 确认之后把日历停在改动发生的那几个小时，证明改到了哪里
      try {
        const hour = Math.min.apply(
          null,
          (message.proposal.changes || [])
            .map((change) => toHours(change.start))
            .filter((value) => !Number.isNaN(value))
        );
        if (Number.isFinite(hour)) {
          window.__xjPreviewHour = hour;
          scrollCalendarTo(hour, 0.18);
          setTimeout(() => scrollCalendarTo(hour, 0.18), 260);
        }
      } catch (_error) {
        /* 定位失败不影响改动本身 */
      }
    }
    if (!applied) {
      toast('没有找到可以应用的改动，日程没有变化');
      return;
    }
    sendFeedback('accept', 'ai', message.proposal);
    toast(
      missed
        ? `已应用 ${applied} 项改动，${missed} 项找不到对应对象`
        : `已应用 ${applied} 项改动`
    );
  }

  const REFINE_PROMPTS = {
    finer:
      '这个拆分太粗了：请保持目标不变，把每条下一步拆成 15–30 分钟能开始的小动作，' +
      '只调整拆解，不要新增目标。',
    coarser:
      '这个拆分太细了：请把同一件事的多个子任务合并，一个交付结果只保留一个目标、' +
      '每个目标最多 3 个里程碑，多余的合并掉。',
    explain:
      '先不要改数据，只用两句话说明你为什么这样拆目标、这样估算时间。',
    act: '直接按现在掌握的信息给出改动块，不要再提问；不确定的地方按你的估算并注明。',
  };

  function askRefine(kind) {
    const text = REFINE_PROMPTS[kind];
    if (!text) return;
    if (agent.streaming) {
      toast('等这条回复结束再说');
      return;
    }
    if (typeof window.send === 'function') window.send(text);
  }

  function applyPlannerPlan(messageId) {
    const message = findMessage(Number(messageId));
    if (!message || !message.proposal) return;
    const cross = message.proposal.cross_check;
    if (!cross || !(cross.changes || []).length) return;
    message.proposal = {
      summary: cross.summary || '策略引擎方案',
      changes: cross.changes,
      bill: cross.bill || {},
    };
    message.applied = false;
    saveChats();
    applyProposal(Number(messageId));
    sendFeedback('choose', 'planner', message.proposal);
  }

  async function sendFeedback(action, source, proposal) {
    try {
      await postJSON('/api/agent/feedback', {
        action,
        source,
        summary: (proposal && proposal.summary) || '',
        changes: (proposal && proposal.changes) || [],
      });
    } catch (_error) {
      /* 反馈失败不影响主流程 */
    }
  }

  function dismissProposal(id) {
    const message = findMessage(id);
    if (!message) return;
    sendFeedback('dismiss', 'ai', message.proposal);
    message.proposal = null;
    saveChats();
    render();
    toast('已保留当前安排');
  }

  // ------------------------------------------------------------------
  // Calendar adapter: the prototype hardcodes today's events, this
  // version reads them from data.schedule so agent changes are visible.
  // ------------------------------------------------------------------
  function eventHTML(event) {
    return (
      `<button class="event ${event.color || ''}" ` +
      `style="top:${(event.start - 8) * 48}px;` +
      `height:${Math.max(28, (event.dur / 60) * 48)}px" ` +
      `data-event="${esc(event.title)}" ` +
      `data-note="${esc(event.note || event.dur + ' 分钟')}">` +
      `<b>${esc(event.title)}</b>${esc(event.note || event.dur + ' 分钟')}` +
      '</button>'
    );
  }

  window.events = function events(day) {
    const rows = [];
    if (day === SEED_DATE && actual) rows.push(...SEED_ACTUAL);
    ensureSchedule().forEach((event) => {
      if (event.date === day && Boolean(event.actual) === actual) {
        rows.push(event);
      }
    });
    (data.extra || []).forEach((event) => {
      if (event.date === day && Boolean(event.actual) === actual) {
        rows.push(event);
      }
    });
    return rows.map(eventHTML).join('');
  };

  // ------------------------------------------------------------------
  // Reflection report
  // ------------------------------------------------------------------
  window.generate = async function generate() {
    const state = review();
    if (!agent.configured) {
      state.report = offlineReport(state.text);
      data.reviews[key()] = state;
      save();
      reportTab = true;
      render();
      toast('离线演示：已用本地规则生成观察');
      return;
    }
    if (typeof window.xjObserveNow === 'function') {
      await window.xjObserveNow('chat');
      const observation = (data.reviews[key()] || {}).insight;
      if (observation) {
        state.report = [
          '记录到的事实\n' + (observation.facts || []).map((item) => '· ' + item).join('\n'),
          '可能的模式\n' + (observation.patterns || []).map((item) => '· ' + item).join('\n'),
          '可以试的调整\n' + (observation.experiment || ''),
        ]
          .filter(Boolean)
          .join('\n\n');
        data.reviews[key()] = state;
        save();
        reportTab = true;
        render();
      }
      return;
    }
    toast('正在整理观察…');
    try {
      const payload = await postJSON('/api/agent/report', {
        state: appState(),
        reflection: state.text || '',
      });
      state.report = payload.report || '';
      data.reviews[key()] = state;
      save();
      reportTab = true;
      render();
      toast('AI 观察已更新');
    } catch (error) {
      toast(`生成失败：${friendlyError(error)}`);
    }
  };

  function offlineReport(text) {
    const head = page === 'weekly' ? '本周观察' : '每日观察';
    const quote = text && text.trim()
      ? `你写到「${text.trim().slice(0, 40)}」。`
      : '这次还没有写反思正文。';
    return (
      `${head}\n\n` +
      '记录到的事实\n' +
      '今天记录了两段投入，合计约 65 分钟，其余时段没有记录（估算）。' +
      quote +
      '\n\n可以带走的判断\n' +
      '哪一件事做完之后，你会觉得今天没有白过？'
    );
  }

  // ------------------------------------------------------------------
  // Boot
  // ------------------------------------------------------------------
  async function refreshHealth() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    try {
      const response = await fetch(apiURL('/api/agent/health'), {
        signal: controller.signal,
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const info = await response.json();
      agent.reachable = true;
      agent.configured = !!info.configured;
      agent.mock = !!info.mock;
      agent.model = info.model || '';
    } catch (_error) {
      agent.reachable = false;
      agent.configured = false;
    } finally {
      clearTimeout(timer);
    }
  }

  function paintTopbar() {
    const pill = document.querySelector('#agentState');
    if (!pill) return;
    pill.textContent = agent.mock
      ? 'Morrow AI · 模拟模式'
      : agent.configured
      ? `Morrow AI · ${agent.model}`
      : 'Morrow AI · 离线';
    pill.title = statusTitle();
  }

  document.addEventListener('click', (event) => {
    const target = event.target.closest
      ? event.target.closest(
          '[data-agent-apply],[data-agent-keep],[data-agent-stop],[data-agent-refine],' +
            '[data-clarify-choice],[data-clarify-submit],[data-planner-apply]'
        )
      : null;
    if (!target) return;
    if (target.hasAttribute('data-agent-stop')) {
      event.preventDefault();
      stopStream();
      return;
    }
    if (target.hasAttribute('data-planner-apply')) {
      event.preventDefault();
      applyPlannerPlan(target.getAttribute('data-planner-apply'));
      return;
    }
    if (target.hasAttribute('data-clarify-choice')) {
      event.preventDefault();
      target.classList.toggle('picked');
      target.setAttribute('aria-pressed', target.classList.contains('picked') ? 'true' : 'false');
      return;
    }
    if (target.hasAttribute('data-clarify-submit')) {
      event.preventDefault();
      submitClarify(target.getAttribute('data-msg'));
      return;
    }
    if (target.hasAttribute('data-agent-apply')) {
      event.preventDefault();
      applyProposal(Number(target.getAttribute('data-agent-apply')));
      return;
    }
    if (target.hasAttribute('data-agent-refine')) {
      event.preventDefault();
      askRefine(target.getAttribute('data-agent-refine'));
      return;
    }
    event.preventDefault();
    dismissProposal(Number(target.getAttribute('data-agent-keep')));
  });

  readEndpoint();
  loadChats();
  ensureSchedule();

  /* 首页的 AI 条由 home.js 渲染，它不认识 agent 的对话记录；
     这里把同一份 chats.home 画进 .hai-scroll，保证一条管线一处渲染。 */
  function paintHomeChat() {
    if (page !== 'home') return;
    const scroller = document.querySelector('.hai-scroll');
    if (!scroller) return;
    const list = chats['home'] || [];
    if (!list.length) return;
    scroller.classList.add('with-response');
    scroller.innerHTML = list.map(messageHTML).join('');
    scroller.scrollTop = scroller.scrollHeight;
  }
  window.xjHomeChatHTML = paintHomeChat;

  /* ------------------------------------------------------------------
   * 时间表预览：把建议里的增删改画到首页日历上
   *   未改动 → 变灰；删除 → 红色虚线；移动/新增 → 目标位置画出新块
   * ------------------------------------------------------------------ */
  function pendingProposal() {
    const list = chats[chatkey()] || [];
    for (let index = list.length - 1; index >= 0; index -= 1) {
      const message = list[index];
      if (message.role === 'assistant' && message.proposal && !message.applied) {
        return message;
      }
    }
    return null;
  }

  function blockIdFor(dateISO, title) {
    const model = homeModel();
    if (!model) return null;
    const hit = (model.blocks || []).find(
      (block) => block.date === dateISO && block.title === title
    );
    return hit ? hit.id : null;
  }

  function clearPreview() {
    window.__xjPreviewHour = null;
    document
      .querySelectorAll(
        '.hblock.ai-preview-dim, .hblock.ai-preview-old, .hblock.ai-preview-remove'
      )
      .forEach((node) =>
        node.classList.remove(
          'ai-preview-dim',
          'ai-preview-old',
          'ai-preview-remove'
        )
      );
    document.querySelectorAll('.hblock.ai-preview-ghost').forEach((node) => node.remove());
    document.querySelectorAll('.ai-preview-legend').forEach((node) => node.remove());
  }

  function ghostBlock(column, change, kind) {
    if (!column) return;
    const start = toHours(change.start);
    const dur = Number(change.duration) || 60;
    const ghost = document.createElement('div');
    ghost.className = `hblock ai-preview-ghost ai-preview-${kind}`;
    ghost.style.top = `${(start / 24) * 100}%`;
    ghost.style.height = `${(dur / 60 / 24) * 100}%`;
    ghost.textContent = `未生效 · ${change.title} ${change.start}`;
    ghost.title = `${change.title} ${change.start}–${clockOf(start + dur / 60)}`;
    column.appendChild(ghost);
  }

  function scrollCalendarTo(hour, ratio) {
    const scroller = document.querySelector('.hscroll');
    const grid = document.querySelector('.hgrid');
    if (!scroller || !grid) return;
    const usable = Math.max(0, scroller.clientHeight - 38);
    const factor = typeof ratio === 'number' ? ratio : 0.12;
    scroller.scrollTop = Math.max(0, (hour / 24) * grid.scrollHeight - usable * factor);
  }

  function paintProposalPreview() {
    clearPreview();
    if (page !== 'home' || !homeModel()) return;
    if (typeof hview !== 'undefined' && hview !== 'plan') return;
    const message = pendingProposal();
    if (!message) return;

    const changes = (message.proposal.changes || []).filter((change) =>
      ['add_event', 'move_event', 'remove_event', 'log_actual'].includes(change.type)
    );
    if (!changes.length) return;

    const affected = new Set();
    changes.forEach((change) => {
      if (change.type === 'move_event' || change.type === 'remove_event') {
        const id = blockIdFor(change.date, change.title);
        if (id) affected.add(id);
      }
    });

    document.querySelectorAll('[data-hblock]').forEach((node) => {
      if (affected.has(node.getAttribute('data-hblock'))) {
        node.classList.add('ai-preview-old');
        const change = changes.find(
          (item) =>
            (item.type === 'move_event' || item.type === 'remove_event') &&
            blockIdFor(item.date, item.title) === node.getAttribute('data-hblock')
        );
        if (change && change.type === 'remove_event') {
          node.classList.add('ai-preview-remove');
        }
      } else {
        node.classList.add('ai-preview-dim');
      }
    });

    changes.forEach((change) => {
      const column = document.querySelector(
        `[data-hcanvas="${change.date}"]`
      );
      if (!column) return;
      if (change.type === 'add_event' || change.type === 'log_actual' || change.type === 'move_event') {
        ghostBlock(column, change, change.type === 'move_event' ? 'move' : 'add');
      }
    });

    const head = document.querySelector('.hcalhead');
    if (head && !head.querySelector('.ai-preview-legend')) {
      const legend = document.createElement('span');
      legend.className = 'ai-preview-legend';
      legend.innerHTML =
        '<i class="dot-keep"></i>不变' +
        '<i class="dot-add"></i>新增' +
        '<i class="dot-move"></i>移动' +
        '<i class="dot-remove"></i>删除';
      head.appendChild(legend);
    }

    // 把日历滚到改动发生的那几小时，别让预览藏在面板后面。
    // home.js 的 fit() 会在面板展开/窗口变化时重设 scrollTop，
    // 所以这里补几次，确保最后一次是我们的位置。
    const hours = changes
      .map((change) => {
        if (change.start) {
          const direct = toHours(change.start);
          if (!Number.isNaN(direct)) return direct;
        }
        // 删除类改动没有 start，用被删那块自己的位置
        const model = homeModel();
        const id = blockIdFor(change.date, change.title);
        const block =
          model && (model.blocks || []).find((item) => item.id === id);
        return block ? block.start : NaN;
      })
      .filter((hour) => !Number.isNaN(hour));
    if (hours.length) {
      const target = Math.min(...hours);
      window.__xjPreviewHour = target;
      const scrollToPreview = () => {
        if (window.__xjPreviewHour !== target) return;
        scrollCalendarTo(target);
      };
      requestAnimationFrame(scrollToPreview);
      setTimeout(scrollToPreview, 180);
      setTimeout(scrollToPreview, 480);
    }
  }

  if (typeof window.render === 'function' && !window.__xjRenderWrapped) {
    const baseRender = window.render;
    window.render = function wrappedRender() {
      const result = baseRender.apply(this, arguments);
      paintHomeChat();
      paintProposalPreview();
      return result;
    };
    window.__xjRenderWrapped = true;
  }

  window.xjSaveChats = saveChats;
  window.xjAppState = appState;
  window.__xingjianAgent = agent;
  window.__xjAgentLoaded = true;
  if (window.__xjReady) render();

  refreshHealth().then(() => {
    paintTopbar();
    if (window.__xjReady) render();
  });
})();
