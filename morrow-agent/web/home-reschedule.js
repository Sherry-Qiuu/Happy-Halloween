/* Local reschedule plans for the Home calendar.
 *
 * Dragging lives in calendar-dnd.js. When a drop lands on top of another
 * block, that module hands the drop to us instead of reverting it: we
 * score a few alternatives with window.Rescheduler and show the plan card
 * with the bill. The user applies the plan or keeps the original layout.
 */
(function () {
  'use strict';

  var R = window.Rescheduler;
  if (!R) return;

  var pending = null;
  var persona = 'P5';
  var CONSTRAINTS = {
    avoid: [],
    prefer: [],
    shorten: {},
    dayOffset: 0,
    part: null,
    force: null
  };

  function dates() {
    var count = typeof window.xjCalDays === 'function' ? window.xjCalDays() : 3;
    var out = [];
    for (var i = 0; i < count; i += 1) out.push(shiftDate(anchor, i));
    return out;
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
    return blocks
      .map(function (b) { return blockEvent(b, ds); })
      .filter(Boolean);
  }

  function pendingBase(from) {
    return hs.blocks.map(function (b) {
      if (b.id !== from.id) return b;
      return Object.assign({}, b, {
        date: from.from.date,
        start: from.from.start,
        end: from.from.end
      });
    });
  }

  /* Called by calendar-dnd.js when the drop overlaps another block. */
  function offer(detail) {
    if (page !== 'home' || hview !== 'plan') return false;
    if (!detail || !detail.id || !detail.from) return false;

    var base = pendingBase(detail);
    var plan = R.propose({
      base: collect(base),
      draft: collect(hs.blocks),
      draggedId: detail.id,
      persona: persona,
      constraints: CONSTRAINTS
    });
    if (!plan || !plan.best) return false;

    pending = {
      base: base,
      baseState: detail.baseState || null,
      draggedId: detail.id,
      plan: plan,
      active: plan.best
    };
    injectCard();
    return true;
  }

  function cardHTML(plan) {
    var changes = plan.changeList || [];
    var bill = plan.bill || {};
    var ds = dates();
    var rows = changes.length
      ? changes.map(function (c) {
        var from = ds[c.from.day] || '';
        var to = ds[c.to.day] || '';
        var dayChange = from !== to ? from.slice(5) + '→' + to.slice(5) + ' ' : '';
        return '<li><b>' + esc(c.title) + '</b><span>' + dayChange +
          timeText(c.from.start / 60) + '–' + timeText(c.from.start / 60 + c.from.dur / 60) +
          ' → ' + timeText(c.to.start / 60) + '–' + timeText(c.to.start / 60 + c.to.dur / 60) +
          '</span><em>' + c.action + '</em></li>';
      }).join('')
      : '<li class="rs-empty">保留当前拖放位置</li>';
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
        '<button type="button" class="rs-apply">' +
          (plan.conflict > 0 ? '仍然应用（保留冲突）' : '应用方案') + '</button>' +
        '<button type="button" class="rs-keep">保留原样</button>' +
      '</div>' +
      '<p class="rs-hint">这里和别的安排撞了。应用方案会把冲突的那几件一起挪开。</p>' +
      '</div>';
  }

  function injectCard() {
    var scroller = document.querySelector('.hai-scroll');
    if (!scroller || !pending) return;
    if (window.__xjAiDock && typeof window.__xjAiDock.open === 'function') {
      window.__xjAiDock.open();
    } else {
      var workspace = document.querySelector('.workspace');
      if (workspace) workspace.classList.add('ai-open');
    }
    scroller.classList.add('with-response');
    scroller.innerHTML = cardHTML(pending.active || pending.plan.best);
    scroller.querySelector('.rs-apply').onclick = applyPlan;
    scroller.querySelector('.rs-keep').onclick = keepPlan;
    var personaSelect = scroller.querySelector('.rs-persona');
    if (personaSelect) {
      personaSelect.onchange = function () {
        persona = personaSelect.value;
        pending.plan = R.propose({
          base: collect(pending.base),
          draft: collect(hs.blocks),
          draggedId: pending.draggedId,
          persona: persona,
          constraints: CONSTRAINTS
        });
        pending.active = pending.plan.best;
        injectCard();
      };
    }
    scroller.querySelectorAll('[data-rs-alt]').forEach(function (button) {
      button.onclick = function () {
        pending.active = pending.plan.alternatives[Number(button.dataset.rsAlt)];
        injectCard();
      };
    });
  }

  function restoreBase() {
    if (pending && pending.baseState && pending.baseState.blocks) {
      hs.blocks = structuredClone(pending.baseState.blocks);
    } else if (pending) {
      hs.blocks = structuredClone(pending.base);
    }
  }

  function applyPlan() {
    if (!pending) return;
    var plan = pending.active || pending.plan.best;
    var ds = dates();
    var byId = {};
    (plan.schedule || []).forEach(function (e) { byId[e.id] = e; });
    var before = pending.baseState || structuredClone(hs);
    hs.blocks = hs.blocks.map(function (b) {
      var e = byId[b.id];
      if (!e || e.day == null || !ds[e.day]) return b;
      return Object.assign({}, b, {
        date: ds[e.day],
        start: e.start / 60,
        end: (e.start + e.dur) / 60
      });
    });
    hundo = before;
    pending = null;
    hsave();
    hmessage = plan.conflict > 0 ? '已应用，冲突先保留。' : '已应用重排方案，冲突已清空。';
    render();
    toast('已应用重排方案');
  }

  function keepPlan() {
    if (!pending) return;
    restoreBase();
    pending = null;
    hsave();
    hmessage = '已保留原样，日历没有变化。';
    render();
    toast('已保留原样');
  }

  window.MorrowReschedule = { offer: offer };
})();
