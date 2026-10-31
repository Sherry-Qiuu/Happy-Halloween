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

  const CHAT_PREFIX = 'xingjian-agent-chats-v1';
  const MAX_STORED = 60;
  const SEED_DATE = '2026-10-31';

  function chatStorageKey() {
    const scope = window.XJ && XJ.scope ? XJ.scope() : 'guest';
    return `${CHAT_PREFIX}:${scope}`;
  }

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
  // Chat persistence
  // ------------------------------------------------------------------
  function loadChats() {
    try {
      const raw = localStorage.getItem(chatStorageKey());
      if (!raw) return;
      const stored = JSON.parse(raw);
      Object.keys(stored).forEach((key) => {
        if (Array.isArray(stored[key])) {
          chats[key] = stored[key].map((message) => ({
            ...message,
            pending: false,
          }));
        }
      });
    } catch (_error) {
      /* a corrupt cache must never block the app */
    }
  }

  function saveChats() {
    try {
      const trimmed = {};
      Object.keys(chats).forEach((key) => {
        trimmed[key] = chats[key].slice(-MAX_STORED);
      });
      localStorage.setItem(chatStorageKey(), JSON.stringify(trimmed));
    } catch (_error) {
      /* quota errors are not worth interrupting the demo */
    }
  }

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

  function appState() {
    const today = new Date().toISOString().slice(0, 10);
    const day = page === 'weekly' ? null : date;
    const goals = (data.goals || []).map((item, index) => ({
      index,
      title: item.title,
      standard: item.standard,
      expected_date: item.date,
      stage: item.stage || 0,
      stage_name: (item.stages || [])[item.stage || 0] || null,
      next_actions: (item.actions || []).slice(0, 4),
    }));
    const state = {
      today,
      now: new Date().toTimeString().slice(0, 5),
      page,
      view: page === 'weekly' ? 'weekly' : 'day',
      date: day,
      user_name: data.name || '明',
      tasks: (data.tasks || []).map((task) => ({
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
    if (page === 'weekly') {
      state.week = [0, 1, 2, 3, 4, 5, 6].map((offset) => {
        const base = selected.startsWith('week-')
          ? new Date(`${selected.slice(5)}T12:00:00`)
          : new Date(`${SEED_DATE}T12:00:00`);
        base.setDate(base.getDate() + offset);
        const iso = base.toISOString().slice(0, 10);
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
  };

  const CHANGE_TAGS = {
    move_event: '调整',
    add_event: '新增',
    remove_event: '删除',
    add_task: '待办',
    add_next_action: '下一步',
    log_actual: '补记',
  };

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
        `<button data-agent-keep="${message.id}">保留现状</button></div>`;
    return (
      '<div class="proposal">' +
      `<div class="proposalsummary">${esc(proposal.summary || '')}</div>` +
      (items ? `<ul class="changelist">${items}</ul>` : '') +
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
      content = '<span class="typing"><i></i><i></i><i></i></span>';
    }
    if (message.error) {
      content += `<div class="agenterror">${esc(message.error)}</div>`;
    }
    const card = message.proposal ? proposalHTML(message) : '';
    if (!content) return card;
    return `<div class="message assistant">${content}</div>${card}`;
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
        'title="演示语音转写" aria-label="模拟语音输入">♩</button>' +
        '<button class="primary" aria-label="发送">↑</button></form>';
    const endChat =
      context === 'review'
        ? '<button class="endchat" data-end>结束并生成观察</button>'
        : '';
    return (
      `<section class="ai" data-context="${context}">` +
      '<div class="aihead"><span><span class="spark">✧</span>行间 AI ' +
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
          onProposal(proposal) {
            reply.proposal = proposal;
            reply.pending = false;
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
          color: 'blue',
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

  function applyProposal(id) {
    const message = findMessage(id);
    if (!message || !message.proposal) return;
    let applied = 0;
    let missed = 0;
    (message.proposal.changes || []).forEach((change) => {
      if (applyChange(change)) applied += 1;
      else missed += 1;
    });
    if (applied) save();
    message.applied = applied > 0;
    saveChats();
    render();
    if (!applied) {
      toast('没有找到可以应用的改动，日程没有变化');
      return;
    }
    toast(
      missed
        ? `已应用 ${applied} 项改动，${missed} 项找不到对应对象`
        : `已应用 ${applied} 项改动`
    );
  }

  function dismissProposal(id) {
    const message = findMessage(id);
    if (!message) return;
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
    pill.textContent = agent.configured
      ? `DeepSeek 已连接 · ${agent.model}`
      : '交互预览 · AI 离线';
    pill.title = statusTitle();
  }

  document.addEventListener('click', (event) => {
    const target = event.target.closest
      ? event.target.closest(
          '[data-agent-apply],[data-agent-keep],[data-agent-stop]'
        )
      : null;
    if (!target) return;
    if (target.hasAttribute('data-agent-stop')) {
      event.preventDefault();
      stopStream();
      return;
    }
    if (target.hasAttribute('data-agent-apply')) {
      event.preventDefault();
      applyProposal(Number(target.getAttribute('data-agent-apply')));
      return;
    }
    event.preventDefault();
    dismissProposal(Number(target.getAttribute('data-agent-keep')));
  });

  readEndpoint();
  loadChats();
  ensureSchedule();
  window.__xingjianAgent = agent;
  window.__xjAgentLoaded = true;
  if (window.__xjReady) render();

  refreshHealth().then(() => {
    paintTopbar();
    if (window.__xjReady) render();
  });
})();
