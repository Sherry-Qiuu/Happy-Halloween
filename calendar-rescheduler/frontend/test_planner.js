const assert = require('assert');
const R = require('./planner.js');

const base = [
  { id: 'm1', title: '产品周会', day: 0, start: 600, dur: 60, hardness: 1, kind: 'meeting' },
  { id: 'm2', title: '客户会', day: 0, start: 840, dur: 60, hardness: 1, kind: 'meeting' },
  { id: 'f1', title: '专注块 · 作品集', day: 0, start: 900, dur: 90, hardness: 3, kind: 'focus', energy: 'deep' },
  { id: 't1', title: '高数作业', day: 0, start: 990, dur: 60, hardness: 3, kind: 'task', energy: 'deep' }
];

function drag(start) {
  const draft = R.clone(base);
  draft.find(e => e.id === 'f1').start = start;
  return draft;
}

// no conflict: no proposal
let p = R.propose({ base, draft: R.clone(base), draggedId: 'f1', persona: 'P5' });
assert.strictEqual(p.best.kind, 'none');
assert.strictEqual(p.best.changes, 0);

// P5 moves the client meeting out of the way; no conflict, one change
p = R.propose({ base, draft: drag(840), draggedId: 'f1', persona: 'P5' });
assert.strictEqual(p.best.conflict, 0);
assert.ok(p.best.changes >= 1);
assert.ok(p.best.changeList.some(c => c.id === 'm2'));
assert.strictEqual(R.conflictCount(p.best.schedule), 0);

// P2 prefers stability and leaves the conflict rather than moving the promise
const p2 = R.propose({ base, draft: drag(840), draggedId: 'f1', persona: 'P2' });
assert.strictEqual(p2.best.conflict, 1);
assert.strictEqual(p2.best.changes, 0);

// "别动客户会" must remove the client move, even for P5
const p3 = R.propose({
  base, draft: drag(840), draggedId: 'f1', persona: 'P5',
  constraints: { avoid: ['客户会'], prefer: [], shorten: {}, dayOffset: 0, part: null }
});
assert.ok(!p3.best.changeList.some(c => c.id === 'm2'));

// shortening constraint is respected on the dragged block
const p4 = R.propose({
  base, draft: drag(840), draggedId: 'f1', persona: 'P5',
  constraints: { avoid: [], prefer: [], shorten: { '专注块': 45 }, dayOffset: 0, part: null,
    force: { title: '专注块', action: 'shorten', amount: 45 } }
});
const f1Change = p4.best.changeList.find(c => c.id === 'f1');
assert.ok(f1Change);
assert.strictEqual(f1Change.to.dur, 45);
assert.strictEqual(f1Change.from.dur, 90);

// "把专注块换到明天下午" moves the dragged block, not the meeting
const p5 = R.propose({
  base, draft: drag(840), draggedId: 'f1', persona: 'P5',
  constraints: { avoid: [], prefer: [], shorten: {}, dayOffset: 1, part: 'afternoon',
    force: { title: '专注块', action: 'move' } }
});
const movedFocus = p5.best.changeList.find(c => c.id === 'f1');
assert.ok(movedFocus);
assert.strictEqual(movedFocus.to.day, 1);

// applying a plan produces a conflict-free schedule when the plan says so
p = R.propose({ base, draft: drag(840), draggedId: 'f1', persona: 'P5' });
const applied = R.applyPlan(drag(840), p.best);
assert.strictEqual(R.conflictCount(applied), 0);

// chat move helper finds a slot, and the result is legal
const slot = R.suggestSlot(base, 'f1', 'P5', { avoid: [], prefer: [], shorten: {}, dayOffset: 0, part: 'afternoon' });
assert.ok(slot);
assert.ok(slot.start >= 12 * 60 && slot.start < 18 * 60);

console.log('planner tests passed');
console.log('P5:', p.best.summary, p.best.explanation);
console.log('P2:', p2.best.summary);
