/* Drag / resize the Home calendar and negotiate a local reschedule plan
 * inside the existing Home AI panel. Loaded after home.js and app-bridge.js.
 * The panel remains the single place where a plan is proposed and applied.
 */
(function () {
  'use strict';

  var R = window.Rescheduler;
  if (!R) return;

  var pending = null;
  var drag = null;
  var suppressClick = false;
  var persona = 'P5';

  function dates() {
    return [anchor, shiftDate(anchor, 1), shiftDate(anchor, 2)];
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function blockEvent(b, ds) {
    var day = ds.indexOf(b.date);
    if (day < 0) return null;
    var hardness = hlocked(b) ? 0 : (b.color === 'gold' ? 1 : 3);
    var kind = b.color === 'gold' ? 'meeting' : (b.task ? 'task' : 'focus');
    return {
      id: b.id,
      title: b.title,
      day: day,
      start: Math.round(b.start * 60),
      dur: Math.max(15, Math.round((b.end - b.start) * 60)),
      hardness: hardness,
      kind: kind,
      energy: kind === 'focus' ? 'deep' : 'medium',
      splittable: true,
      notify: hardness <= 2
    };
  }

  function collect(blocks) {
    var ds = dates();
    return blocks.map(function (b) { return blockEvent(b, ds); }).filter(Boolean);
  }

  function conflictsOf(id) {
    var b = hs.blocks.find(function (x) { return x.id === id; });
    if (!b) return [];
    return hs.blocks.filter(function (x) {
      return x.id !== id && x.date === b.date && x.start < b.end && x.end > b.start;
    });
  }

  function attach() {
    if (page !== 'home' || hview !== 'plan') return;
    var hint = document.querySelector('.hcalhint');
    if (hint && !hint.dataset.rsHint) {
      hint.dataset.rsHint = '1';
      hint.textContent += ' · 可拖动时间块 / 上下边缘改时长';
    }
    document.querySelectorAll('.hblock').forEach(function (el) {
      if (el.dataset.rsBound) return;
      var b = hs.blocks.find(function (x) { return x.id === el.dataset.hblock; });
      if (!b || hlocked(b)) return;
      el.dataset.rsBound = '1';
      el.classList.add('rs-draggable');
      el.insertAdjacentHTML('beforeend',
        '<span class="rs-handle rs-top" data-rs-handle="top"></span>' +
        '<span class="rs-handle rs-bottom" data-rs-handle="bottom"></span>');
      el.addEventListener('pointerdown', onDown);
    });
  }

  function onDown(e) {
    if (e.button !== 0) return;
    if (page !== 'home' || hview !== 'plan') return;
    var el = e.target.closest('.hblock');
    if (!el || !el.dataset.hblock) return;
    var b = hs.blocks.find(function (x) { return x.id === el.dataset.hblock; });
    if (!b || hlocked(b)) return;
    var handle = e.target.closest('[data-rs-handle]');
    drag = {
      id: b.id,
      mode: handle ? handle.dataset.rsHandle : 'move',
      startY: e.clientY,
      height: Math.max(120, el.parentElement.clientHeight || 600),
      orig: { start: b.start, end: b.end, date: b.date },
      base: structuredClone(hs.blocks),
      baseState: structuredClone(hs),
      el: el,
      moved: false
    };
    el.classList.add('rs-dragging');
    if (el.setPointerCapture) el.setPointerCapture(e.pointerId);
    e.preventDefault();
    e.stopPropagation();
  }

  function onMove(e) {
    if (!drag) return;
    var delta = Math.round(((e.clientY - drag.startY) / drag.height * 24) * 4) / 4;
    if (!delta && !drag.moved) return;
    var b = hs.blocks.find(function (x) { return x.id === drag.id; });
    if (!b) return;
    var duration = drag.orig.end - drag.orig.start;
    if (drag.mode === 'move') {
      b.start = clamp(drag.orig.start + delta, 0, 24 - duration);
      b.end = b.start + duration;
    } else if (drag.mode === 'top') {
      b.start = clamp(drag.orig.start + delta, 0, drag.orig.end - 0.25);
      b.end = drag.orig.end;
    } else {
      b.end = clamp(drag.orig.end + delta, drag.orig.start + 0.25, 24);
      b.start = drag.orig.start;
    }
    drag.moved = true;
    var top = b.start / 24 * 100;
    var height = (b.end - b.start) / 24 * 100;
    drag.el.style.top = top + '%';
    drag.el.style.height = height + '%';
    drag.el.title = b.title + ' ' + timeText(b.start) + '–' + timeText(b.end);
    e.preventDefault();
  }

  function onUp() {
    if (!drag) return;
    var d = drag;
    drag = null;
    d.el.classList.remove('rs-dragging');
    suppressClick = true;
    window.setTimeout(function () { suppressClick = false; }, 250);
    if (!d.moved) {
      blockEditor(d.id);
      return;
    }
    var conflicts = conflictsOf(d.id);
    if (conflicts.length) {
      showProposal(d.base, d.id, d.baseState);
      return;
    }
    var moved = hs.blocks.find(function (x) { return x.id === d.id; });
    hundo = structuredClone(d.baseState);
    hsave();
    hmessage = '已移动「' + (moved ? moved.title : '') + '」，没有冲突。';
    render();
    toast('已移动 · 无冲突');
  }

  function showProposal(baseBlocks, draggedId, baseState) {
    var ds = dates();
    var baseEvents = collect(baseBlocks);
    var draftEvents = collect(hs.blocks);
    var p = R.propose({
      base: baseEvents,
      draft: draftEvents,
      draggedId: draggedId,
      persona: persona,
      constraints: { avoid: [], prefer: [], shorten: {}, dayOffset: 0, part: null, force: null }
    });
    pending = {
      base: structuredClone(baseBlocks),
      baseState: structuredClone(baseState),
      draggedId: draggedId,
      plan: p,
      active: p.best,
      alt: 0,
      constraints: { avoid: [], prefer: [], shorten: {}, dayOffset: 0, part: null, force: null }
    };
    hdraft = null;
    hmessage = '';
    render();
    injectCard();
  }

  function cardHTML(plan) {
    var changes = plan.changeList || [];
    var bill = plan.bill || {};
    var rows = changes.length ? changes.map(function (c) {
      var from = dates()[c.from.day] || '';
      var to = dates()[c.to.day] || '';
      var dayChange = from !== to ? from.slice(5) + '→' + to.slice(5) + ' ' : '';
      return '<li><b>' + esc(c.title) + '</b><span>' + dayChange +
        timeText(c.from.start / 60) + '–' + timeText(c.from.start / 60 + c.from.dur / 60) +
        ' → ' + timeText(c.to.start / 60) + '–' + timeText(c.to.start / 60 + c.to.dur / 60) +
        '</span><em>' + c.action + '</em></li>';
    }).join('') : '<li class="rs-empty">保留当前拖放位置</li>';
    var alts = (pending.plan.alternatives || []).map(function (a, i) {
      return '<button type="button" data-rs-alt="' + i + '" class="' +
        (pending.active === a ? 'active' : '') + '">备选 ' + (i + 1) + ' · ' + a.kind + '</button>';
    }).join('');
    return '<div class="rs-card">' +
      '<div class="rs-kicker"><span>CALENDAR RESCHEDULE</span>' +
        '<select class="rs-persona" aria-label="排布画像">' +
        Object.keys(R.PERSONAS).map(function (key) {
          return '<option value="' + key + '" ' + (key === persona ? 'selected' : '') + '>' +
            key + ' · ' + R.PERSONAS[key].short + '</option>';
        }).join('') +
        '</select></div>' +
      '<div class="rs-summary">' + esc(plan.summary) + '</div>' +
      '<div class="rs-bill">' +
        '<span>改动<b>' + (bill.changes || 0) + '</b></span>' +
        '<span>目标<b>' + (bill.goal == null ? 100 : bill.goal) + '%</b></span>' +
        '<span>冲突<b>' + (bill.conflict || 0) + '</b></span>' +
        '<span>通知<b>' + (bill.notify || 0) + '</b></span>' +
      '</div>' +
      '<ul class="rs-changes">' + rows + '</ul>' +
      (alts ? '<div class="rs-alts">' + alts + '</div>' : '') +
      '<div class="rs-actions">' +
        '<button type="button" class="rs-apply">' + (plan.conflict > 0 ? '仍然应用（保留冲突）' : '应用方案') + '</button>' +
        '<button type="button" class="rs-keep">保留原样</button>' +
      '</div>' +
      '<p class="rs-hint">继续输入「别动客户会」「缩短专注块到 45 分钟」「换到明天下午」，我再算。</p>' +
      '</div>';
  }

  function injectCard() {
    var scroller = document.querySelector('.hai-scroll');
    if (!scroller || !pending) return;
    scroller.classList.add('with-response');
    scroller.innerHTML = cardHTML(pending.active || pending.plan.best);
    scroller.querySelector('.rs-apply').onclick = applyPlan;
    scroller.querySelector('.rs-keep').onclick = keepPlan;
    var personaSelect = scroller.querySelector('.rs-persona');
    if (personaSelect) {
      personaSelect.onchange = function () {
        persona = personaSelect.value;
        var p = R.propose({
          base: collect(pending.base),
          draft: collect(hs.blocks),
          draggedId: pending.draggedId,
          persona: persona,
          constraints: pending.constraints
        });
        pending.plan = p;
        pending.active = p.best;
        pending.alt = 0;
        injectCard();
      };
    }
    scroller.querySelectorAll('[data-rs-alt]').forEach(function (b) {
      b.onclick = function () {
        pending.active = pending.plan.alternatives[Number(b.dataset.rsAlt)];
        injectCard();
      };
    });
    var input = document.querySelector('#homeComposer input');
    if (input) input.placeholder = '继续谈：别动客户会 / 缩短专注块 / 换到明天下午';
  }

  function applyPlan() {
    if (!pending) return;
    var plan = pending.active || pending.plan.best;
    var ds = dates();
    var byId = {};
    plan.schedule.forEach(function (e) { byId[e.id] = e; });
    hundo = structuredClone(pending.baseState);
    hs.blocks = hs.blocks.map(function (b) {
      var e = byId[b.id];
      if (!e || e.day == null || !ds[e.day]) return b;
      return Object.assign({}, b, {
        date: ds[e.day],
        start: e.start / 60,
        end: (e.start + e.dur) / 60
      });
    });
    hsave();
    hmessage = plan.conflict > 0 ? '已应用，冲突先保留。' : '已应用重排方案，冲突已清空。';
    pending = null;
    render();
    toast('已应用重排方案');
  }

  function keepPlan() {
    if (!pending) return;
    hs.blocks = structuredClone(pending.base);
    hsave();
    hmessage = '已保留原样，日历没有变化。';
    pending = null;
    render();
    toast('已保留原样');
  }

  function parseConstraints(text) {
    var c = pending.constraints;
    var titles = hs.blocks.map(function (b) { return b.title; });
    titles.forEach(function (title) {
      var short = title.split(' · ')[0];
      if (text.indexOf(short) === -1 && text.indexOf(title) === -1) return;
      if (/别动|不要动|别碰|保留|锁住/.test(text) &&
          (text.indexOf(short) !== -1 || text.indexOf(title) !== -1)) {
        if (c.avoid.indexOf(title) === -1) c.avoid.push(title);
      }
      if (/优先|先动/.test(text) && c.prefer.indexOf(title) === -1) c.prefer.push(title);
      if (text.indexOf('缩短') !== -1) {
        var m = text.match(/(\d+)\s*分钟/);
        if (m) {
          c.shorten[title] = Number(m[1]);
          c.force = { title: title, action: 'shorten', amount: Number(m[1]) };
        }
      }
    });
    if (text.indexOf('明天') !== -1 || text.indexOf('次日') !== -1) c.dayOffset = 1;
    if (text.indexOf('上午') !== -1 || text.indexOf('早上') !== -1) c.part = 'morning';
    else if (text.indexOf('下午') !== -1 || text.indexOf('中午') !== -1) c.part = 'afternoon';
    else if (text.indexOf('晚上') !== -1 || text.indexOf('傍晚') !== -1) c.part = 'evening';
    if (/换到|挪到|移到/.test(text)) {
      var dragged = hs.blocks.find(function (b) { return b.id === pending.draggedId; });
      var target = c.force ? c.force.title : (dragged ? dragged.title : '');
      if (target) c.force = { title: target, action: 'move' };
    }
    return c;
  }

  function negotiate(text) {
    if (!pending) return false;
    if (/换一个|再试|备选/.test(text)) {
      var alts = pending.plan.alternatives || [];
      if (alts.length) {
        pending.alt = (pending.alt + 1) % alts.length;
        pending.active = alts[pending.alt];
        injectCard();
      }
      return true;
    }
    var constraints = parseConstraints(text);
    var p = R.propose({
      base: collect(pending.base),
      draft: collect(hs.blocks),
      draggedId: pending.draggedId,
      persona: persona,
      constraints: constraints
    });
    pending.plan = p;
    pending.active = p.best;
    pending.alt = 0;
    injectCard();
    return true;
  }

  // The Home composer is owned by home.js; intercept only while a local
  // reschedule proposal is active.
  document.addEventListener('submit', function (e) {
    if (e.target.id !== 'homeComposer' || !pending) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var input = e.target.querySelector('input');
    var text = input ? input.value.trim() : '';
    if (text) {
      negotiate(text);
      input.value = '';
    }
  }, true);

  document.addEventListener('click', function (e) {
    if (suppressClick) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);

  document.addEventListener('pointermove', onMove, { passive: false });
  document.addEventListener('pointerup', onUp);
  document.addEventListener('pointercancel', onUp);

  var previousRender = window.render || (typeof render === 'function' ? render : null);
  if (typeof previousRender === 'function') {
    window.render = function () {
      previousRender.apply(this, arguments);
      attach();
      if (pending) injectCard();
    };
  }

  var view = document.getElementById('view');
  if (view && window.MutationObserver) {
    new MutationObserver(function () {
      attach();
      if (pending && !document.querySelector('.hai-scroll .rs-card')) injectCard();
    }).observe(view, { childList: true, subtree: true });
  }
})();
