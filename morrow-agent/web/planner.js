/* Persona-aware rescheduling core for the drag-and-negotiate demo.
 * Pure functions, no DOM. Works as a browser global and as a Node module.
 */
(function (global) {
  'use strict';

  var DAY_START = 6 * 60;
  var DAY_END = 23 * 60;
  var MIN_DUR = 15;

  var PERSONAS = {
    P1: { label: '期限冲刺', short: '目标优先', goal: 1.10, change: 0.20, continuity: 0.30, social: 0.40, energy: 0.30, debt: 0.35, accept: 0.10, buffer: 10 },
    P2: { label: '稳定节奏', short: '少动、保节奏', goal: 0.55, change: 1.00, continuity: 1.00, social: 1.10, energy: 0.50, debt: 0.70, accept: -0.10, buffer: 5 },
    P3: { label: '乐观估时', short: '多留缓冲', goal: 0.80, change: 0.50, continuity: 0.40, social: 0.50, energy: 0.65, debt: 0.80, accept: 0.00, buffer: 15 },
    P4: { label: '保守留白', short: '不排满', goal: 0.65, change: 0.40, continuity: 0.60, social: 0.50, energy: 0.45, debt: 0.50, accept: 0.05, buffer: 10 },
    P5: { label: '碎片救火', short: '先清冲突', goal: 0.75, change: 0.40, continuity: 0.30, social: 0.50, energy: 0.75, debt: 0.60, accept: 0.00, buffer: 10 },
    P6: { label: '目标囤积', short: '只推进一件', goal: 0.80, change: 0.50, continuity: 0.50, social: 0.70, energy: 0.50, debt: 0.60, accept: -0.05, buffer: 10 },
    P7: { label: '高启动摩擦', short: '短块、可叠加', goal: 0.70, change: 0.55, continuity: 0.60, social: 0.40, energy: 0.50, debt: 0.70, accept: 0.10, buffer: 10 },
    P8: { label: '低记录', short: '保守、不猜', goal: 0.60, change: 0.45, continuity: 0.50, social: 0.50, energy: 0.40, debt: 0.50, accept: 0.00, buffer: 10 }
  };

  var DAYS = ['周一', '周二', '周三', '周四', '周五'];
  var DATES = ['10/30', '10/31', '11/01'];

  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function overlap(a, b) {
    return a.day === b.day && Math.max(a.start, b.start) < Math.min(a.start + a.dur, b.start + b.dur);
  }
  function fmt(min) {
    min = Math.round(min);
    var h = Math.floor(min / 60), m = min % 60;
    return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
  }
  function peak(start) {
    var h = start / 60;
    return (h >= 9 && h < 12) || (h >= 14 && h < 18);
  }
  function sameTitle(a, b) {
    return a === b || (a && b && (a.indexOf(b) !== -1 || b.indexOf(a) !== -1));
  }
  function isAvoided(title, constraints) {
    return (constraints.avoid || []).some(function (x) { return sameTitle(title, x); });
  }
  function isPreferred(title, constraints) {
    return (constraints.prefer || []).some(function (x) { return sameTitle(title, x); });
  }
  function forcedShorten(title, constraints) {
    var map = constraints.shorten || {};
    for (var k in map) if (sameTitle(title, k)) return map[k];
    return 0;
  }

  function conflictsFor(schedule, id) {
    var event = schedule.find(function (e) { return e.id === id; });
    if (!event) return [];
    return schedule.filter(function (e) { return e.id !== id && overlap(e, event); });
  }

  function conflictCount(schedule) {
    var n = 0;
    for (var i = 0; i < schedule.length; i++) {
      for (var j = i + 1; j < schedule.length; j++) {
        if (overlap(schedule[i], schedule[j])) n++;
      }
    }
    return n;
  }

  function busyIntervals(schedule, day, excludeId, buffer) {
    var rows = [];
    schedule.forEach(function (e) {
      if (e.day !== day || e.id === excludeId) return;
      var end = e.start + e.dur;
      if (e.kind === 'meeting' || e.kind === 'class' || e.kind === 'social') end += buffer;
      rows.push([Math.max(DAY_START, e.start), Math.min(DAY_END, end)]);
    });
    rows.sort(function (a, b) { return a[0] - b[0]; });
    var merged = [];
    rows.forEach(function (r) {
      if (!merged.length || r[0] > merged[merged.length - 1][1]) merged.push([r[0], r[1]]);
      else merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], r[1]);
    });
    return merged;
  }

  function freeStarts(schedule, day, dur, excludeId, buffer) {
    var busy = busyIntervals(schedule, day, excludeId, buffer);
    var out = [];
    var cursor = DAY_START;
    function addGap(start, end) {
      start = Math.ceil(start / 15) * 15;
      for (var s = start; s + dur <= end; s += 15) out.push(s);
    }
    busy.forEach(function (b) {
      if (b[0] - cursor >= dur) addGap(cursor, b[0]);
      cursor = Math.max(cursor, b[1]);
    });
    if (DAY_END - cursor >= dur) addGap(cursor, DAY_END);
    return out;
  }

  function gapLength(schedule, day, start, excludeId, buffer) {
    var end = start;
    schedule.forEach(function (e) {
      if (e.day !== day || e.id === excludeId) return;
      var eEnd = e.start + e.dur + buffer;
      if (e.start <= end && eEnd > end) end = eEnd;
    });
    var limit = DAY_END;
    schedule.forEach(function (e) {
      if (e.day !== day || e.id === excludeId) return;
      if (e.start >= end) limit = Math.min(limit, e.start);
    });
    return Math.max(0, limit - end);
  }

  function slotAllowed(day, start, draggedDay, constraints) {
    if (constraints.dayOffset === 1 && day !== draggedDay + 1) return false;
    var part = constraints.part;
    if (part === 'morning' && start >= 12 * 60) return false;
    if (part === 'afternoon' && (start < 12 * 60 || start >= 18 * 60)) return false;
    if (part === 'evening' && start < 18 * 60) return false;
    return true;
  }

  function moveCandidates(event, schedule, persona, constraints, draggedDay, limit) {
    var out = [];
    for (var day = 0; day < 3; day++) {
      var starts = freeStarts(schedule, day, event.dur, event.id, persona.buffer);
      for (var i = 0; i < starts.length; i++) {
        var start = starts[i];
        if (day === event.day && start === event.start) continue;
        if (!slotAllowed(day, start, draggedDay, constraints)) continue;
        var score = Math.abs(day - event.day) * 2 + Math.abs(start - event.start) / 60;
        if (start % 30 !== 0) score += 0.35;
        if (event.energy === 'deep' && !peak(start)) score += 0.6;
        if (event.hardness <= 2) score += 0.5;
        out.push({ day: day, start: start, dur: event.dur, score: score });
      }
    }
    out.sort(function (a, b) { return a.score - b.score; });
    return out.slice(0, limit || 2);
  }

  function shortenCandidate(event, schedule, persona, constraints, draggedDay) {
    if (event.splittable === false || event.dur <= 30) return null;
    var forced = forcedShorten(event.title, constraints);
    var best = null;
    for (var day = 0; day < 3; day++) {
      var starts = freeStarts(schedule, day, MIN_DUR, event.id, persona.buffer);
      for (var i = 0; i < starts.length; i++) {
        var start = starts[i];
        if (!slotAllowed(day, start, draggedDay, constraints)) continue;
        var free = gapLength(schedule, day, start, event.id, persona.buffer);
        var dur;
        if (forced) {
          if (free < forced) continue;
          dur = forced;
        } else {
          dur = Math.min(event.dur - 15, free);
        }
        if (dur < MIN_DUR || dur >= event.dur) continue;
        var score = Math.abs(day - event.day) * 2 + Math.abs(start - event.start) / 60;
        if (start % 30 !== 0) score += 0.35;
        if (event.energy === 'deep' && !peak(start)) score += 0.5;
        var cand = { day: day, start: start, dur: dur, score: score };
        if (!best || score < best.score) best = cand;
      }
    }
    return best;
  }

  function deferCandidate(event, schedule, persona, constraints) {
    if (event.day + 1 >= 3) return null;
    var starts = freeStarts(schedule, event.day + 1, event.dur, event.id, persona.buffer);
    if (starts.indexOf(event.start) === -1) return null;
    return { day: event.day + 1, start: event.start, dur: event.dur };
  }

  function applyUpdates(schedule, updates) {
    var out = clone(schedule);
    Object.keys(updates).forEach(function (id) {
      var ev = out.find(function (e) { return e.id === id; });
      if (!ev) return;
      var u = updates[id];
      if (u.day != null) ev.day = u.day;
      if (u.start != null) ev.start = u.start;
      if (u.dur != null) ev.dur = u.dur;
    });
    return out;
  }

  function diff(base, result) {
    var changes = [];
    base.forEach(function (old) {
      var now = result.find(function (e) { return e.id === old.id; });
      if (!now) return;
      if (now.day === old.day && now.start === old.start && now.dur === old.dur) return;
      var action = now.dur < old.dur ? '缩短' : now.day !== old.day ? '换天' : '移动';
      changes.push({
        id: old.id,
        title: old.title,
        action: action,
        from: { day: old.day, start: old.start, dur: old.dur },
        to: { day: now.day, start: now.start, dur: now.dur }
      });
    });
    return changes;
  }

  function socialOf(event) {
    if (event.hardness <= 1) return 1;
    if (event.hardness === 2) return 0.5;
    return 0;
  }

  function sigmoid(x) {
    if (x >= 0) { var z = Math.exp(-x); return 1 / (1 + z); }
    var z2 = Math.exp(x); return z2 / (1 + z2);
  }

  function draggedOptions(dragged, base, draft, persona, constraints) {
    var original = base.find(function (e) { return e.id === dragged.id; });
    var force = constraints.force;
    if (force && sameTitle(dragged.title, force.title)) {
      var forced = [];
      if (force.action === 'shorten') {
        var fsh = shortenCandidate(dragged, draft, persona, constraints, dragged.day);
        if (fsh) {
          var fu = {}; fu[dragged.id] = { day: fsh.day, start: fsh.start, dur: fsh.dur };
          forced.push({
            kind: 'shorten', updates: fu, intent: 1.5, continuity: 0.5,
            energy: dragged.energy === 'deep' && !peak(fsh.start) ? 0.5 : 0,
            debt: (dragged.dur - fsh.dur) / 60, social: 0
          });
        }
      } else if (force.action === 'move') {
        var fmoves = moveCandidates(dragged, draft, persona, constraints, dragged.day, 2);
        fmoves.forEach(function (m) {
          var u = {}; u[dragged.id] = { day: m.day, start: m.start, dur: dragged.dur };
          forced.push({
            kind: 'move', updates: u, intent: 1.5, continuity: 1,
            energy: dragged.energy === 'deep' && !peak(m.start) ? 0.7 : 0,
            debt: 0, social: 0
          });
        });
      }
      return forced.length ? forced : [{ kind: 'keep', updates: {}, intent: 1.5, continuity: 0, energy: 0, debt: 0, social: 0 }];
    }

    var opts = [{ kind: 'keep', updates: {}, intent: 1.5, continuity: 0, energy: 0, debt: 0, social: 0 }];
    if (!isAvoided(dragged.title, constraints)) {
      if (original) {
        opts.push({
          kind: 'revert', updates: (function () { var u = {}; u[dragged.id] = { day: original.day, start: original.start, dur: original.dur }; return u; })(),
          intent: -1.2, continuity: 1, energy: 0, debt: 0, social: 0
        });
      }
      var moves = moveCandidates(dragged, draft, persona, constraints, dragged.day, 2);
      moves.forEach(function (m) {
        var u = {}; u[dragged.id] = { day: m.day, start: m.start, dur: dragged.dur };
        opts.push({
          kind: 'move', updates: u, intent: -0.4, continuity: 1,
          energy: dragged.energy === 'deep' && !peak(m.start) ? 0.7 : 0,
          debt: 0, social: 0
        });
      });
      var sh = shortenCandidate(dragged, draft, persona, constraints, dragged.day);
      if (sh) {
        var u2 = {}; u2[dragged.id] = { day: sh.day, start: sh.start, dur: sh.dur };
        opts.push({
          kind: 'shorten', updates: u2, intent: 0.2, continuity: 0.5,
          energy: dragged.energy === 'deep' && !peak(sh.start) ? 0.5 : 0,
          debt: (dragged.dur - sh.dur) / 60, social: 0
        });
      }
    } else if (original) {
      opts.push({
        kind: 'revert', updates: (function () { var u = {}; u[dragged.id] = { day: original.day, start: original.start, dur: original.dur }; return u; })(),
        intent: -1.2, continuity: 1, energy: 0, debt: 0, social: 0
      });
    }
    return opts.slice(0, 4);
  }

  function victimOptions(victim, draft, persona, constraints, draggedDay, draggedTitle) {
    var keep = { kind: 'keep', updates: {}, continuity: 0, energy: 0, debt: 0, social: 0, conflict: 1 };
    var force = constraints.force;
    // If the user forced the dragged block, do not move other people's events.
    if (force && sameTitle(force.title, draggedTitle)) return [keep];

    if (force && sameTitle(force.title, victim.title)) {
      var only = [];
      if (force.action === 'shorten') {
        var fsh = shortenCandidate(victim, draft, persona, constraints, draggedDay);
        if (fsh) {
          var fu = {}; fu[victim.id] = { day: fsh.day, start: fsh.start, dur: fsh.dur };
          only.push({
            kind: 'shorten', updates: fu, continuity: 0.5, social: socialOf(victim),
            energy: victim.energy === 'deep' && !peak(fsh.start) ? 0.5 : 0,
            debt: (victim.dur - fsh.dur) / 60, conflict: 0
          });
        }
      } else if (force.action === 'move') {
        moveCandidates(victim, draft, persona, constraints, draggedDay, 2).forEach(function (m) {
          var u = {}; u[victim.id] = { day: m.day, start: m.start, dur: victim.dur };
          only.push({
            kind: 'move', updates: u, continuity: 1, social: socialOf(victim),
            energy: victim.energy === 'deep' && !peak(m.start) ? 0.7 : 0,
            debt: 0, conflict: 0
          });
        });
      }
      return only.length ? only : [keep];
    }

    var opts = [keep];
    var avoided = isAvoided(victim.title, constraints) || sameTitle(victim.title, draggedTitle);
    if (avoided) return opts;

    var moves = moveCandidates(victim, draft, persona, constraints, draggedDay, 2);
    moves.forEach(function (m) {
      var u = {}; u[victim.id] = { day: m.day, start: m.start, dur: victim.dur };
      opts.push({
        kind: 'move', updates: u, continuity: 1, social: socialOf(victim),
        energy: victim.energy === 'deep' && !peak(m.start) ? 0.7 : 0,
        debt: 0, conflict: 0
      });
    });

    var sh = shortenCandidate(victim, draft, persona, constraints, draggedDay);
    if (sh) {
      var u2 = {}; u2[victim.id] = { day: sh.day, start: sh.start, dur: sh.dur };
      opts.push({
        kind: 'shorten', updates: u2, continuity: 0.5, social: socialOf(victim),
        energy: victim.energy === 'deep' && !peak(sh.start) ? 0.5 : 0,
        debt: (victim.dur - sh.dur) / 60, conflict: 0
      });
    }

    var defer = deferCandidate(victim, draft, persona, constraints);
    if (defer) {
      var u3 = {}; u3[victim.id] = { day: defer.day, start: defer.start, dur: defer.dur };
      opts.push({
        kind: 'defer', updates: u3, continuity: 1, social: socialOf(victim),
        energy: 0, debt: 0, conflict: 0
      });
    }
    return opts.slice(0, 3);
  }

  function cartesian(lists, cap) {
    var out = [[]];
    lists.forEach(function (list) {
      var next = [];
      out.forEach(function (prefix) {
        list.forEach(function (item) {
          if (next.length < cap) next.push(prefix.concat([item]));
        });
      });
      out = next;
    });
    return out;
  }

  function scorePlan(plan, persona) {
    var p = persona;
    var tier = plan.social > 0 || plan.crossDay ? 'preview' : (plan.changes >= 2 || plan.debt > 0 ? 'toast' : 'silent');
    var promptPen = tier === 'preview' ? 0.8 : tier === 'toast' ? 0.35 : 0.1;
    var changesNorm = Math.min(1, plan.changes / 4);
    var u = p.goal * plan.goal
      - p.change * changesNorm
      - p.social * plan.social
      - p.continuity * Math.min(1, plan.continuity)
      - p.energy * Math.min(1, plan.energy)
      - p.debt * Math.min(1, plan.debt / 2)
      + plan.intent * 0.9
      + (plan.prefBonus || 0)
      - 0.1 * promptPen;
    var z = 1.0 + p.accept - 0.6 * plan.changes - 1.0 * plan.social
      - 0.5 * Math.min(1, plan.energy) - 0.8 * plan.conflict + 1.2 * plan.goal;
    var pa = Math.max(0.05, Math.min(0.95, sigmoid(z)));
    plan.tier = tier;
    plan.pAccept = pa;
    plan.utility = u;
    plan.expected = pa * u - (1 - pa) * 0.8 * plan.conflict;
    return plan;
  }

  function summarize(plan) {
    if (plan.conflict > 0) return '还有 ' + plan.conflict + ' 处冲突，先给你一个保守方案';
    var bits = ['动 ' + plan.changes + ' 件', '冲突 0', '目标 ' + Math.round(plan.goal * 100) + '%'];
    if (plan.social > 0) bits.push('需通知');
    if (plan.debt > 0) bits.push('欠 ' + Math.round(plan.debt * 60) + ' 分钟');
    return bits.join('，');
  }

  function explain(plan, changes) {
    if (!changes.length) return ['没有需要改动的事件。'];
    return changes.map(function (c) {
      var dur = c.from.dur !== c.to.dur ? '，' + c.from.dur + '→' + c.to.dur + ' 分钟' : '';
      return c.title + '：' + fmt(c.from.start) + ' → ' + fmt(c.to.start) + dur;
    });
  }

  function propose(opts) {
    var base = clone(opts.base || []);
    var draft = clone(opts.draft || base);
    var draggedId = opts.draggedId;
    var persona = PERSONAS[opts.persona] || PERSONAS.P5;
    var constraints = opts.constraints || {};
    var dragged = draft.find(function (e) { return e.id === draggedId; });
    if (!dragged) return { best: noop(draft), alternatives: [] };

    var conflicts = conflictsFor(draft, draggedId);
    if (!conflicts.length) {
      return { best: noop(draft), alternatives: [] };
    }

    var dragOpts = draggedOptions(dragged, base, draft, persona, constraints);
    var victimLists = conflicts.map(function (v) {
      return victimOptions(v, draft, persona, constraints, dragged.day, dragged.title);
    });
    var combos = cartesian([dragOpts].concat(victimLists), 24);
    var plans = [];

    combos.forEach(function (combo) {
      var updates = {};
      var social = 0, continuity = 0, energy = 0, debt = 0, conflictKeep = 0, prefBonus = 0;
      combo.forEach(function (o) {
        Object.keys(o.updates).forEach(function (id) { updates[id] = o.updates[id]; });
        social += o.social || 0;
        continuity += o.continuity || 0;
        energy += o.energy || 0;
        debt += o.debt || 0;
        conflictKeep += o.conflict || 0;
        if (o.kind === 'move' && isPreferred((draft.find(function (e) { return e.id === Object.keys(o.updates)[0]; }) || {}).title, constraints)) prefBonus += 0.5;
      });
      var result = applyUpdates(draft, updates);
      var cc = conflictCount(result);
      var changes = diff(draft, result);
      var shortenPenalty = 0;
      changes.forEach(function (c) {
        if (c.to.dur < c.from.dur) shortenPenalty += (c.from.dur - c.to.dur) / Math.max(1, c.from.dur);
      });
      var goal = Math.max(0, 1 - cc / Math.max(1, conflicts.length) - shortenPenalty / Math.max(1, conflicts.length));
      var crossDay = changes.some(function (c) { return c.from.day !== c.to.day; });
      var plan = {
        kind: (new Set(combo.map(function (o) { return o.kind; })).size > 1) ? 'mixed' : combo[0].kind,
        schedule: result,
        changeList: changes,
        goal: goal,
        social: Math.min(1, social),
        conflict: cc,
        continuity: continuity,
        energy: energy,
        debt: debt,
        changesCount: changes.length,
        crossDay: crossDay,
        intent: combo[0].intent + (combo[0].kind === 'keep' ? 0 : 0),
        prefBonus: prefBonus
      };
      plan.changes = plan.changesCount;
      plan = scorePlan(plan, persona);
      plan.summary = summarize(plan);
      if (changes.length) plan.explanation = explain(plan, changes);
      else if (cc > 0) plan.explanation = ['保留当前的拖放位置，' + cc + ' 处冲突先留着；备选里可以移动对方。'];
      else plan.explanation = ['没有需要改动的事件。'];
      plan.bill = {
        changes: plan.changesCount,
        goal: Math.round(plan.goal * 100),
        conflict: plan.conflict,
        social: plan.social,
        notify: changes.filter(function (c) {
          var ev = draft.find(function (e) { return e.id === c.id; });
          return ev && ev.hardness <= 2;
        }).length,
        debt: Math.round(plan.debt * 60),
        tier: plan.tier
      };
      plans.push(plan);
    });

    plans.sort(function (a, b) { return b.expected - a.expected; });
    var best = plans[0];
    var alts = [];
    var seen = {};
    for (var i = 1; i < plans.length && alts.length < 3; i++) {
      var key = plans[i].changeList.map(function (c) { return c.id + ':' + c.to.start + ':' + c.to.dur; }).join('|');
      if (seen[key]) continue;
      seen[key] = 1;
      alts.push(plans[i]);
    }

    // If the chosen plan leaves a conflict, add a "keep" fallback for the UI.
    return { best: best, alternatives: alts, conflicts: conflicts };
  }

  function noop(schedule) {
    var plan = {
      kind: 'none', schedule: clone(schedule), changeList: [], changes: 0, goal: 1, social: 0,
      conflict: 0, continuity: 0, energy: 0, debt: 0, changesCount: 0,
      crossDay: false, intent: 0, pAccept: 0.9, utility: 1, expected: 1,
      tier: 'silent', summary: '没有冲突，不需要重排。', explanation: ['当前没有冲突。']
    };
    plan.bill = { changes: 0, goal: 100, conflict: 0, social: 0, notify: 0, debt: 0, tier: 'silent' };
    return plan;
  }

  function applyPlan(schedule, plan) {
    return clone(plan.schedule || schedule);
  }

  function suggestSlot(schedule, id, personaKey, constraints) {
    var ev = schedule.find(function (e) { return e.id === id; });
    if (!ev) return null;
    var p = PERSONAS[personaKey] || PERSONAS.P5;
    var rows = moveCandidates(ev, schedule, p, constraints || {}, ev.day, 3);
    return rows[0] || null;
  }

  var api = {
    PERSONAS: PERSONAS,
    DAYS: DAYS,
    DATES: DATES,
    DAY_START: DAY_START,
    DAY_END: DAY_END,
    MIN_DUR: MIN_DUR,
    propose: propose,
    applyPlan: applyPlan,
    suggestSlot: suggestSlot,
    conflictsFor: conflictsFor,
    conflictCount: conflictCount,
    diff: diff,
    fmt: fmt,
    clone: clone
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.Rescheduler = api;
})(typeof window !== 'undefined' ? window : globalThis);
