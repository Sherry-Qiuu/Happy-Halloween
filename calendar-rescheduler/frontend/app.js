(function () {
  'use strict';

  var R = window.Rescheduler;
  var PX = 56;
  var $ = function (sel) { return document.querySelector(sel); };

  var SAMPLE = [
    { id: 'm1', title: '产品周会', day: 0, start: 600, dur: 60, hardness: 1, kind: 'meeting' },
    { id: 'm2', title: '客户会', day: 0, start: 840, dur: 60, hardness: 1, kind: 'meeting' },
    { id: 'f1', title: '专注块 · 作品集', day: 0, start: 900, dur: 90, hardness: 3, kind: 'focus', energy: 'deep' },
    { id: 't1', title: '高数作业', day: 0, start: 990, dur: 60, hardness: 3, kind: 'task', energy: 'deep' },
    { id: 'c1', title: '专业课', day: 1, start: 540, dur: 90, hardness: 1, kind: 'class' },
    { id: 'i1', title: '英语面试', day: 1, start: 660, dur: 30, hardness: 1, kind: 'meeting' },
    { id: 'd1', title: '设计评审', day: 1, start: 840, dur: 60, hardness: 1, kind: 'meeting' },
    { id: 'f2', title: '专注块 · 写作', day: 1, start: 960, dur: 90, hardness: 3, kind: 'focus', energy: 'deep' },
    { id: 's1', title: '组会', day: 2, start: 600, dur: 60, hardness: 1, kind: 'meeting' },
    { id: 'g1', title: '健身', day: 2, start: 780, dur: 60, hardness: 4, kind: 'routine' },
    { id: 'f3', title: '专注块 · 复习', day: 2, start: 900, dur: 90, hardness: 3, kind: 'focus', energy: 'deep' },
    { id: 'c2', title: '选修课', day: 3, start: 540, dur: 90, hardness: 1, kind: 'class' },
    { id: 'w1', title: '工作坊', day: 3, start: 840, dur: 120, hardness: 1, kind: 'meeting' },
    { id: 'p1', title: '朋友约饭', day: 3, start: 1110, dur: 90, hardness: 2, kind: 'social' },
    { id: 'o1', title: '1:1 · 产品对齐', day: 4, start: 660, dur: 30, hardness: 1, kind: 'meeting' },
    { id: 'r1', title: '周复盘', day: 4, start: 840, dur: 60, hardness: 3, kind: 'focus' },
    { id: 'f4', title: '专注块 · 作品集', day: 4, start: 960, dur: 90, hardness: 3, kind: 'focus', energy: 'deep' }
  ];

  var state = {
    schedule: R.clone(SAMPLE),
    base: null,
    draft: null,
    drag: null,
    draggedId: null,
    conflictPending: false,
    persona: 'P5',
    constraints: { avoid: [], prefer: [], shorten: {}, dayOffset: 0, part: null, force: null },
    messages: [],
    round: 0,
    typing: false,
    undo: [],
    lastProposal: null,
    toastTimer: null
  };

  var messagesEl, typingEl, calendarEl, dragTipEl, inputEl, statusEl, roundEl;

  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function range(start, dur) { return R.fmt(start) + '–' + R.fmt(start + dur); }
  function dayLabel(day) { return R.DAYS[day] || ('第 ' + (day + 1) + ' 天'); }
  function personaLabel() { return R.PERSONAS[state.persona].label; }
  function sameTitle(a, b) { return a === b || (a || '').indexOf(b) !== -1 || (b || '').indexOf(a) !== -1; }

  function conflictIds(schedule) {
    var ids = new Set();
    for (var i = 0; i < schedule.length; i++) {
      for (var j = i + 1; j < schedule.length; j++) {
        var a = schedule[i], b = schedule[j];
        if (a.day === b.day && Math.max(a.start, b.start) < Math.min(a.start + a.dur, b.start + b.dur)) {
          ids.add(a.id); ids.add(b.id);
        }
      }
    }
    return ids;
  }

  // ---------------------------------------------------------------- calendar
  function renderTimeCol() {
    var html = '';
    for (var h = 8; h < 20; h++) {
      html += '<span class="time-label" style="top:' + ((h - 8) * PX) + 'px">' + String(h).padStart(2, '0') + ':00</span>';
    }
    html += '<span class="time-label" style="top:' + (12 * PX) + 'px;transform:translateY(-100%)">20:00</span>';
    $('#timeCol').innerHTML = html;
  }

  function eventHTML(ev, conflicts) {
    var top = (ev.start - R.DAY_START) / 60 * PX;
    var height = Math.max(16, ev.dur / 60 * PX - 2);
    var tiny = height < 32 ? '1' : '0';
    var cls = 'event ' + ev.kind + (conflicts.has(ev.id) ? ' conflict' : '');
    return '<div class="' + cls + '" data-id="' + ev.id + '" data-tiny="' + tiny + '" ' +
      'style="top:' + top + 'px;height:' + height + 'px" title="' + esc(ev.title + ' · ' + range(ev.start, ev.dur)) + '">' +
      '<div class="handle top" data-handle="top"></div>' +
      '<div class="event-body"><b>' + esc(ev.title) + '</b><span class="etime">' + range(ev.start, ev.dur) + '</span></div>' +
      '<div class="handle bottom" data-handle="bottom"></div>' +
      '</div>';
  }

  function renderCalendar() {
    var schedule = state.draft || state.schedule;
    var conflicts = conflictIds(schedule);
    document.querySelectorAll('.day-col').forEach(function (col) {
      var day = Number(col.dataset.day);
      var list = schedule.filter(function (e) { return e.day === day; })
        .sort(function (a, b) { return a.start - b.start; });
      col.innerHTML = list.map(function (e) { return eventHTML(e, conflicts); }).join('');
    });
    var statEvents = $('#statEvents');
    var statConflicts = $('#statConflicts');
    if (statEvents) statEvents.textContent = schedule.length;
    if (statConflicts) statConflicts.textContent = conflicts.size;
    if (state.conflictPending) {
      setStatus('bad', conflicts.size + ' 处冲突 · AI 已收到');
    } else if (conflicts.size) {
      setStatus('bad', conflicts.size + ' 处冲突');
    } else {
      setStatus('ok', '无冲突');
    }
  }

  function updateEventElement(el, ev) {
    var top = (ev.start - R.DAY_START) / 60 * PX;
    var height = Math.max(16, ev.dur / 60 * PX - 2);
    el.style.top = top + 'px';
    el.style.height = height + 'px';
    el.dataset.tiny = height < 32 ? '1' : '0';
    var time = el.querySelector('.etime');
    if (time) time.textContent = range(ev.start, ev.dur);
    el.title = ev.title + ' · ' + range(ev.start, ev.dur);
  }

  function showTip(ev, x, y) {
    dragTipEl.textContent = ev.title + ' · ' + range(ev.start, ev.dur);
    dragTipEl.style.left = (x + 14) + 'px';
    dragTipEl.style.top = (y - 12) + 'px';
    dragTipEl.classList.remove('hidden');
  }
  function hideTip() { dragTipEl.classList.add('hidden'); }

  function setStatus(kind, text) {
    statusEl.className = 'cal-status ' + kind;
    statusEl.textContent = text;
  }

  // ---------------------------------------------------------------- drag
  function onPointerDown(e) {
    if (state.conflictPending) { toast('先处理右侧的冲突方案'); return; }
    if (e.button !== 0) return;
    var el = e.target.closest('.event');
    if (!el) return;
    var id = el.dataset.id;
    var ev = state.schedule.find(function (x) { return x.id === id; });
    if (!ev) return;
    var handle = e.target.closest('.handle');
    var mode = handle ? handle.dataset.handle : 'move';
    state.base = R.clone(state.schedule);
    state.draft = R.clone(state.schedule);
    state.drag = {
      id: id,
      mode: mode,
      startY: e.clientY,
      orig: { start: ev.start, dur: ev.dur, day: ev.day },
      el: el,
      moved: false
    };
    el.classList.add('dragging');
    if (el.setPointerCapture) el.setPointerCapture(e.pointerId);
    e.preventDefault();
  }

  function onPointerMove(e) {
    var d = state.drag;
    if (!d) return;
    var delta = Math.round(((e.clientY - d.startY) / PX * 60) / 15) * 15;
    if (!delta && !d.moved) return;
    var ev = state.draft.find(function (x) { return x.id === d.id; });
    if (!ev) return;
    if (d.mode === 'move') {
      ev.start = clamp(d.orig.start + delta, R.DAY_START, R.DAY_END - ev.dur);
    } else if (d.mode === 'top') {
      var end = d.orig.start + d.orig.dur;
      var ns = clamp(d.orig.start + delta, R.DAY_START, end - R.MIN_DUR);
      ev.start = ns;
      ev.dur = end - ns;
    } else {
      ev.dur = clamp(d.orig.dur + delta, R.MIN_DUR, R.DAY_END - ev.start);
    }
    d.moved = true;
    updateEventElement(d.el, ev);
    showTip(ev, e.clientX, e.clientY);
    e.preventDefault();
  }

  function onPointerUp() {
    var d = state.drag;
    if (!d) return;
    state.drag = null;
    d.el.classList.remove('dragging');
    hideTip();
    if (!d.moved) {
      state.draft = null;
      state.base = null;
      renderCalendar();
      return;
    }
    var ids = conflictIds(state.draft);
    if (ids.size) {
      state.conflictPending = true;
      state.draggedId = d.id;
      renderCalendar();
      setStatus('bad', ids.size + ' 处冲突 · AI 已收到');
      var ev = state.draft.find(function (x) { return x.id === d.id; });
      var others = Array.from(ids).filter(function (x) { return x !== d.id; })
        .map(function (x) { return state.draft.find(function (y) { return y.id === x; }); })
        .filter(Boolean);
      var verb = d.mode === 'move'
        ? '挪到 ' + range(ev.start, ev.dur)
        : '改成 ' + range(ev.start, ev.dur);
      addUserMessage('我把「' + ev.title + '」' + verb + '，和「' + others.map(function (o) { return o.title; }).join('」「') + '」冲突了。');
      requestAI();
      focusChat();
    } else {
      state.undo.push(R.clone(state.schedule));
      state.schedule = R.clone(state.draft);
      state.draft = null;
      state.base = null;
      state.draggedId = null;
      state.conflictPending = false;
      renderCalendar();
      var moved = state.schedule.find(function (x) { return x.id === d.id; });
      toast('已移动 · 无冲突');
      addSystemMessage('已更新：' + moved.title + ' ' + range(moved.start, moved.dur));
    }
  }

  // ---------------------------------------------------------------- chat
  function renderChat() {
    messagesEl.innerHTML = state.messages.map(function (m, i) {
      if (m.role === 'system') return '<div class="message system">' + esc(m.text) + '</div>';
      var body = '<div class="message ' + m.role + '">' + esc(m.text);
      if (m.proposal) body += proposalHTML(m, i);
      body += '</div>';
      return body;
    }).join('');
    typingEl.classList.toggle('hidden', !state.typing);
    roundEl.textContent = '第 ' + state.round + ' 轮';
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function kindLabel(kind) {
    return ({ keep: '保留', revert: '放回原位', move: '移动', shorten: '缩短', defer: '换天', mixed: '组合', none: '无冲突' })[kind] || kind;
  }

  function proposalHTML(m, index) {
    var plan = m.appliedPlan || m.activePlan || m.proposal.best;
    var alts = m.proposal.alternatives || [];
    var changes = plan.changeList || [];
    var bill = plan.bill || {};
    var tier = ({ preview: '需确认', toast: '轻提示', silent: '静默', digest: '摘要' })[plan.tier] || '需确认';
    var rows = changes.length ? changes.map(function (c) {
      var fromDay = c.from.day !== c.to.day ? dayLabel(c.from.day) + ' ' : '';
      var toDay = c.from.day !== c.to.day ? dayLabel(c.to.day) + ' ' : '';
      return '<li><b>' + esc(c.title) + '</b><span class="arrow">→</span>' +
        '<span>' + fromDay + range(c.from.start, c.from.dur) + ' / ' + toDay + range(c.to.start, c.to.dur) + '</span>' +
        '<em>' + c.action + '</em></li>';
    }).join('') : '<li class="empty">不需要改动，保留当前拖放位置</li>';
    var altButtons = alts.length ? '<div class="alts">' + alts.map(function (a, j) {
      var active = m.activePlan === a;
      return '<button type="button" data-alt="' + j + '" class="' + (active ? 'active' : '') + '">备选 ' + (j + 1) + ' · ' + kindLabel(a.kind) + '</button>';
    }).join('') + '</div>' : '';
    var actions = m.applied
      ? '<div class="p-note">' + esc(m.appliedText || '已应用') + '。</div>'
      : '<div class="actions">' +
        '<button type="button" class="primary" data-apply>' + (plan.conflict > 0 ? '仍然应用（保留冲突）' : '应用方案') + '</button>' +
        '<button type="button" class="ghostbtn" data-keep>保留原样</button>' +
        '<button type="button" class="ghostbtn" data-why>为什么</button>' +
        '</div>';
    return '<div class="proposal" data-msg="' + index + '">' +
      '<div class="p-head"><span class="p-badge">方案</span><span class="p-summary">' + esc(plan.summary) + '</span><span class="p-tier">' + tier + '</span></div>' +
      '<div class="bill">' +
        '<span>改动<b>' + (bill.changes || 0) + '</b></span>' +
        '<span>目标<b>' + (bill.goal == null ? 100 : bill.goal) + '%</b></span>' +
        '<span>冲突<b>' + (bill.conflict || 0) + '</b></span>' +
        '<span>通知<b>' + (bill.notify || 0) + '</b></span>' +
      '</div>' +
      '<ul class="changes">' + rows + '</ul>' +
      altButtons +
      actions +
      (plan.conflict > 0 && !m.applied ? '<div class="p-note">这份建议会保留冲突；你也可以继续和我说“别动客户会”“换到明天下午”。</div>' : '') +
      '</div>';
  }

  function addUserMessage(text) {
    state.messages.push({ role: 'user', text: text });
    renderChat();
  }
  function addAssistantMessage(text, proposal) {
    state.messages.push({
      role: 'assistant',
      text: text,
      proposal: proposal || null,
      activePlan: proposal ? proposal.best : null,
      applied: false
    });
    renderChat();
  }
  function addSystemMessage(text) {
    state.messages.push({ role: 'system', text: text });
    renderChat();
  }

  function focusChat() {
    if (window.innerWidth <= 1080) {
      var panel = $('#chatPanel');
      if (panel && panel.scrollIntoView) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function requestAI() {
    if (state.typing) return;
    if (!state.draft || !state.draggedId) return;
    state.typing = true;
    renderChat();
    window.setTimeout(function () {
      var p = R.propose({
        base: state.base || state.schedule,
        draft: state.draft,
        draggedId: state.draggedId,
        persona: state.persona,
        constraints: state.constraints
      });
      state.typing = false;
      if (!p || !p.best || p.best.kind === 'none') {
        addAssistantMessage('现在没有冲突；我把这个偏好记下了，下次拖动时生效。');
        return;
      }
      state.round += 1;
      state.lastProposal = p;
      var statChanges = $('#statChanges');
      if (statChanges) statChanges.textContent = p.best.changes;
      addAssistantMessage('按「' + personaLabel() + '」的偏好，我算了一版：' + p.best.summary + '。', p);
    }, 420);
  }

  function applyPlan(index) {
    var m = state.messages[index];
    if (!m || !m.proposal || m.applied) return;
    var plan = m.activePlan || m.proposal.best;
    state.undo.push(R.clone(state.schedule));
    state.schedule = R.clone(plan.schedule);
    state.base = null;
    state.draft = null;
    state.draggedId = null;
    state.conflictPending = false;
    m.applied = true;
    m.appliedPlan = plan;
    m.appliedText = '已应用';
    m.activePlan = null;
    state.lastProposal = null;
    renderCalendar();
    renderChat();
    setStatus(plan.conflict > 0 ? 'bad' : 'ok', plan.conflict > 0 ? '已应用 · 保留 ' + plan.conflict + ' 处冲突' : '无冲突');
    toast(plan.conflict > 0 ? '已应用，但保留了冲突' : '已应用 · 可撤销');
    addAssistantMessage(plan.conflict > 0 ? '已应用。冲突先留着，你可以继续谈。' : '已应用，这版冲突清空了。');
  }

  function keepOriginal(index) {
    var m = state.messages[index];
    if (!m || !m.proposal || m.applied) return;
    state.schedule = state.base ? R.clone(state.base) : R.clone(state.schedule);
    state.base = null;
    state.draft = null;
    state.draggedId = null;
    state.conflictPending = false;
    m.applied = true;
    m.appliedText = '已保留原样';
    m.activePlan = null;
    renderCalendar();
    renderChat();
    setStatus('ok', '无冲突');
    toast('已保留原样');
    addAssistantMessage('已保留原样，日历没有变。');
  }

  function explainPlan(index) {
    var m = state.messages[index];
    if (!m || !m.proposal) return;
    var plan = m.activePlan || m.proposal.best;
    var reason = (plan.explanation || []).join('；');
    var alts = (m.proposal.alternatives || []).map(function (a) { return kindLabel(a.kind); }).join('、') || '没有';
    addAssistantMessage('我选它，是因为在「' + personaLabel() + '」下：' + (reason || '它代价最小') +
      '。账单是：' + plan.summary + '。备选还有：' + alts + '。');
  }

  function parseIntent(text) {
    var prev = state.constraints;
    var next = {
      avoid: prev.avoid.slice(),
      prefer: prev.prefer.slice(),
      shorten: Object.assign({}, prev.shorten),
      dayOffset: prev.dayOffset || 0,
      part: prev.part || null,
      force: prev.force || null,
      why: /为什么|解释|凭什么/.test(text),
      alternative: /换一个|再试|备选|另一个/.test(text),
      reset: /重来|清除偏好|重置偏好/.test(text)
    };
    function add(list, value) { if (!list.some(function (x) { return sameTitle(x, value); })) list.push(value); }

    state.schedule.forEach(function (ev) {
      var short = ev.title.split(' · ')[0];
      if (text.indexOf(short) === -1 && text.indexOf(ev.title) === -1) return;
      var negative = ['别动', '不要动', '别碰', '保留', '锁住'].some(function (p) {
        return text.indexOf(p + short) !== -1 || text.indexOf(p + ev.title) !== -1;
      });
      var positive = ['优先', '先动', '动', '挪', '移'].some(function (p) {
        return text.indexOf(p + short) !== -1 || text.indexOf(p + ev.title) !== -1;
      });
      if (negative) {
        add(next.avoid, ev.title);
        next.prefer = next.prefer.filter(function (x) { return !sameTitle(x, ev.title); });
        if (next.force && sameTitle(next.force.title, ev.title)) next.force = null;
      } else if (positive) {
        add(next.prefer, ev.title);
      }
      if (text.indexOf('缩短') !== -1 && (text.indexOf(short) !== -1 || text.indexOf(ev.title) !== -1)) {
        var idx = text.indexOf('缩短');
        var m = text.slice(idx).match(/(\d+)\s*分钟/);
        if (m) {
          next.shorten[ev.title] = Number(m[1]);
          next.force = { title: ev.title, action: 'shorten', amount: Number(m[1]) };
        }
      }
      if (!negative && (text.indexOf(short) !== -1 || text.indexOf(ev.title) !== -1) &&
          /挪|移|换到|改到|放到|安排到/.test(text)) {
        next.force = { title: ev.title, action: 'move' };
      }
    });

    if (text.indexOf('明天') !== -1 || text.indexOf('次日') !== -1) next.dayOffset = 1;
    if (text.indexOf('上午') !== -1 || text.indexOf('早上') !== -1) next.part = 'morning';
    else if (text.indexOf('下午') !== -1 || text.indexOf('中午') !== -1) next.part = 'afternoon';
    else if (text.indexOf('晚上') !== -1 || text.indexOf('傍晚') !== -1) next.part = 'evening';
    return next;
  }

  function describeConstraints(c) {
    var bits = [];
    c.avoid.forEach(function (x) { bits.push('不动 ' + x); });
    c.prefer.forEach(function (x) { bits.push('优先 ' + x); });
    if (c.force) {
      bits.push(c.force.action === 'shorten'
        ? '把 ' + c.force.title + ' 缩到 ' + c.force.amount + ' 分钟'
        : '把 ' + c.force.title + ' 换时间');
    }
    Object.keys(c.shorten).forEach(function (k) {
      if (c.force && c.force.action === 'shorten' && sameTitle(k, c.force.title)) return;
      bits.push(k + ' 缩到 ' + c.shorten[k] + ' 分钟');
    });
    if (c.dayOffset === 1) bits.push('明天');
    if (c.part) bits.push({ morning: '上午', afternoon: '下午', evening: '晚上' }[c.part]);
    return bits;
  }

  function tryChatMove(text) {
    if (!/挪|移|换到|改到|放到|安排到/.test(text)) return false;
    if (/别动|不要动/.test(text)) return false;
    var ev = state.schedule.find(function (e) {
      var short = e.title.split(' · ')[0];
      return text.indexOf(e.title) !== -1 || text.indexOf(short) !== -1;
    });
    if (!ev && state.constraints.force) {
      ev = state.schedule.find(function (e) {
        return sameTitle(e.title, state.constraints.force.title);
      });
    }
    if (!ev) return false;
    var cand = R.suggestSlot(state.schedule, ev.id, state.persona, state.constraints);
    if (!cand) {
      addAssistantMessage('我在「' + personaLabel() + '」的边界内没找到合适的空位；可以试试缩短、换天，或者先保留冲突。');
      return 'moved';
    }
    state.base = R.clone(state.schedule);
    state.draft = R.clone(state.schedule);
    state.draggedId = ev.id;
    var d = state.draft.find(function (x) { return x.id === ev.id; });
    d.day = cand.day;
    d.start = cand.start;
    var ids = conflictIds(state.draft);
    if (ids.size) {
      state.conflictPending = true;
      renderCalendar();
      setStatus('bad', ids.size + ' 处冲突 · AI 已收到');
      return 'conflict';
    }
    state.undo.push(R.clone(state.schedule));
    state.schedule = R.clone(state.draft);
    state.draft = null;
    state.base = null;
    state.draggedId = null;
    renderCalendar();
    addAssistantMessage('已把「' + ev.title + '」挪到 ' + dayLabel(cand.day) + ' ' + range(cand.start, cand.dur) + '，没有冲突。');
    setStatus('ok', '无冲突');
    return 'moved';
  }

  function switchAlternative() {
    var last = null;
    for (var i = state.messages.length - 1; i >= 0; i--) {
      if (state.messages[i].proposal && !state.messages[i].applied) { last = state.messages[i]; break; }
    }
    if (!last || !last.proposal.alternatives || !last.proposal.alternatives.length) {
      addAssistantMessage('这版没有别的备选了；你可以直接说“别动客户会”或“换到明天下午”，我再算。');
      return;
    }
    var idx = last.proposal.alternatives.indexOf(last.activePlan);
    last.activePlan = last.proposal.alternatives[(idx + 1) % last.proposal.alternatives.length];
    renderChat();
    toast('已切换到备选 ' + ((idx + 1) % last.proposal.alternatives.length + 1));
  }

  function explainLast() {
    for (var i = state.messages.length - 1; i >= 0; i--) {
      if (state.messages[i].proposal) { explainPlan(i); return; }
    }
    addAssistantMessage('还没有方案可解释。先把一个时间块拖到冲突位置，我会给你算一版。');
  }

  function sendChat(text) {
    text = (text || '').trim();
    if (!text) return;
    inputEl.value = '';
    addUserMessage(text);
    var intent = parseIntent(text);

    if (intent.reset) {
      state.constraints = { avoid: [], prefer: [], shorten: {}, dayOffset: 0, part: null, force: null };
      addAssistantMessage('偏好已清空。现在按当前画像重新算。');
      if (state.conflictPending) requestAI();
      return;
    }
    if (intent.why) { explainLast(); return; }
    if (intent.alternative) { switchAlternative(); return; }

    var before = JSON.stringify(state.constraints);
    state.constraints = {
      avoid: intent.avoid,
      prefer: intent.prefer,
      shorten: intent.shorten,
      dayOffset: intent.dayOffset,
      part: intent.part,
      force: intent.force
    };
    if (JSON.stringify(state.constraints) !== before) {
      var bits = describeConstraints(state.constraints);
      if (bits.length) addSystemMessage('已记录：' + bits.join('；'));
    }

    if (state.conflictPending && state.draft) { requestAI(); return; }
    var moved = tryChatMove(text);
    if (moved === 'conflict') { requestAI(); return; }
    if (moved === 'moved') return;
    addAssistantMessage('现在没有冲突。你可以拖动一个时间块，或者说“把专注块挪到明天下午”。');
  }

  // ---------------------------------------------------------------- misc
  function toast(text) {
    var el = $('#toast');
    el.textContent = text;
    el.classList.remove('hidden');
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(function () { el.classList.add('hidden'); }, 2200);
  }

  function updatePersonaHint() {
    var p = R.PERSONAS[state.persona];
    $('#personaHint').textContent = p.label + ' · ' + p.short;
  }

  function welcome() {
    addAssistantMessage('把时间块拖到新位置，或拖动它的上下边缘改时长。产生冲突时，我会在对话里给方案；你也可以继续说“别动客户会”“换到明天下午”。');
  }

  function resetAll() {
    state.schedule = R.clone(SAMPLE);
    state.base = null;
    state.draft = null;
    state.drag = null;
    state.draggedId = null;
    state.conflictPending = false;
    state.constraints = { avoid: [], prefer: [], shorten: {}, dayOffset: 0, part: null, force: null };
    state.messages = [];
    state.round = 0;
    state.typing = false;
    state.undo = [];
    state.lastProposal = null;
    renderCalendar();
    welcome();
    setStatus('ok', '无冲突');
    var statChanges = $('#statChanges');
    if (statChanges) statChanges.textContent = '0';
    toast('已重置');
  }

  function undo() {
    if (!state.undo.length) { toast('没有可撤销的改动'); return; }
    state.schedule = state.undo.pop();
    state.base = null;
    state.draft = null;
    state.draggedId = null;
    state.conflictPending = false;
    renderCalendar();
    setStatus('ok', '无冲突');
    addSystemMessage('已撤销上一次应用。');
    toast('已撤销');
  }

  // ---------------------------------------------------------------- boot
  function init() {
    messagesEl = $('#messages');
    typingEl = $('#typing');
    calendarEl = $('#calendar');
    dragTipEl = $('#dragTip');
    inputEl = $('#chatInput');
    statusEl = $('#calStatus');
    roundEl = $('#roundLabel');

    renderTimeCol();
    var select = $('#persona');
    Object.keys(R.PERSONAS).forEach(function (key) {
      var opt = document.createElement('option');
      opt.value = key;
      opt.textContent = key + ' · ' + R.PERSONAS[key].label;
      select.appendChild(opt);
    });
    select.value = state.persona;
    updatePersonaHint();

    calendarEl.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointermove', onPointerMove, { passive: false });
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);

    messagesEl.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (!btn) return;
      var card = e.target.closest('.proposal');
      if (!card) return;
      var index = Number(card.dataset.msg);
      if (btn.hasAttribute('data-apply')) applyPlan(index);
      else if (btn.hasAttribute('data-keep')) keepOriginal(index);
      else if (btn.hasAttribute('data-why')) explainPlan(index);
      else if (btn.hasAttribute('data-alt')) {
        var m = state.messages[index];
        m.activePlan = m.proposal.alternatives[Number(btn.dataset.alt)];
        renderChat();
      }
    });

    $('#composer').addEventListener('submit', function (e) {
      e.preventDefault();
      sendChat(inputEl.value);
    });

    document.querySelectorAll('[data-chip]').forEach(function (b) {
      b.addEventListener('click', function () { sendChat(b.dataset.chip); });
    });

    select.addEventListener('change', function () {
      state.persona = select.value;
      updatePersonaHint();
      if (state.conflictPending && state.draft) {
        addSystemMessage('画像切换为 ' + state.persona + ' · ' + R.PERSONAS[state.persona].label + '，重新算一版。');
        requestAI();
      } else {
        toast('画像：' + R.PERSONAS[state.persona].label);
      }
    });

    $('#undoBtn').addEventListener('click', undo);
    $('#resetBtn').addEventListener('click', resetAll);

    renderCalendar();
    welcome();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
