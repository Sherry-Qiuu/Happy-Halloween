/* calendar-dnd.js — 时间块拖动与拉伸
 *
 * 交互：
 *   按住块中间 → 拖动（上下移动时间，左右拖到别的日期列）
 *   按住块上缘 / 下缘 → 拉伸起止时间
 * 粒度：5 分钟（1/12 小时），最短 5 分钟。
 * 落点如果和别的块重叠，或者碰到锁定块，撤销并提示，不写数据。
 */
(function () {
  'use strict';

  const SNAP = 1 / 12;
  const MIN_DURATION = SNAP;
  const EDGE = 7; // 上下边缘可拉的像素宽度

  let drag = null;

  function snap(value) {
    return Math.round(value / SNAP) * SNAP;
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function store() {
    return typeof hs === 'object' && hs ? hs : null;
  }

  function list() {
    if (typeof hview !== 'undefined' && hview === 'actual') return store().actual || [];
    return store().blocks || [];
  }

  function minutesLabel(hours) {
    const whole = Math.floor(hours);
    const mins = Math.round((hours - whole) * 60);
    return `${String(whole).padStart(2, '0')}:${String(mins).padStart(2, '0')}`;
  }

  function conflicts(block, other) {
    return (
      other.id !== block.id &&
      other.date === block.date &&
      block.start < other.end &&
      block.end > other.start
    );
  }

  function paint(node, block) {
    const geom = window.xjCalGeom();
    const span = Math.max(1, geom.range.end - geom.range.start);
    node.style.top = `${((block.start - geom.range.start) / span) * 100}%`;
    node.style.height = `${Math.max(0.6, ((block.end - block.start) / span) * 100)}%`;
    const title = node.getAttribute('data-title-base') || node.textContent.trim();
    node.textContent = `${title} ${minutesLabel(block.start)}–${minutesLabel(block.end)}`;
  }

  function onPointerDown(event) {
    const node = event.target.closest && event.target.closest('[data-hblock]');
    if (!node) return;
    const rows = list();
    const block = rows.find((row) => row.id === node.getAttribute('data-hblock'));
    if (!block) return;
    if (block.locked) {
      if (typeof toast === 'function') toast('这块是锁定项，先解锁再拖动');
      return;
    }

    const rect = node.getBoundingClientRect();
    const offset = event.clientY - rect.top;
    let mode = 'move';
    if (offset <= EDGE) mode = 'resize-start';
    else if (offset >= rect.height - EDGE) mode = 'resize-end';

    const geom = window.xjCalGeom();
    node.setAttribute('data-title-base', node.textContent.replace(/\s+\d{2}:\d{2}–\d{2}:\d{2}$/, '').trim());
    drag = {
      node,
      block,
      mode,
      startY: event.clientY,
      startX: event.clientX,
      origin: { start: block.start, end: block.end, date: block.date },
      geom,
      moved: false,
    };
    node.classList.add('dragging');
    node.setPointerCapture && node.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function columnDateAt(clientX, clientY) {
    const columns = Array.from(document.querySelectorAll('[data-hcanvas]'));
    for (const column of columns) {
      const rect = column.getBoundingClientRect();
      if (clientX >= rect.left && clientX <= rect.right) return column.getAttribute('data-hcanvas');
    }
    // 落到同一列但指针偏出时，按最近的列算
    let best = null;
    columns.forEach((column) => {
      const rect = column.getBoundingClientRect();
      const distance = Math.abs(clientX - (rect.left + rect.width / 2));
      if (!best || distance < best.distance) best = { distance, date: column.getAttribute('data-hcanvas') };
    });
    return best ? best.date : null;
  }

  function onPointerMove(event) {
    if (!drag) return;
    const dy = event.clientY - drag.startY;
    const dx = event.clientX - drag.startX;
    if (Math.abs(dy) > 2 || Math.abs(dx) > 2) drag.moved = true;
    const geom = drag.geom;
    const delta = snap(dy / geom.pxPerHour);
    const block = drag.block;
    const range = geom.range;

    if (drag.mode === 'move') {
      const duration = drag.origin.end - drag.origin.start;
      let nextStart = snap(drag.origin.start + delta);
      nextStart = clamp(nextStart, range.start, range.end - duration);
      block.start = nextStart;
      block.end = nextStart + duration;
      const targetDate = columnDateAt(event.clientX, event.clientY);
      if (targetDate) block.date = targetDate;
    } else if (drag.mode === 'resize-start') {
      let nextStart = snap(drag.origin.start + delta);
      nextStart = clamp(nextStart, range.start, drag.origin.end - MIN_DURATION);
      block.start = nextStart;
    } else {
      let nextEnd = snap(drag.origin.end + delta);
      nextEnd = clamp(nextEnd, drag.origin.start + MIN_DURATION, range.end);
      block.end = nextEnd;
    }

    paint(drag.node, block);
  }

  function onPointerUp() {
    if (!drag) return;
    const { block, node, origin, moved } = drag;
    node.classList.remove('dragging');
    drag = null;

    if (!moved) {
      if (typeof blockEditor === 'function') blockEditor(block.id);
      return;
    }

    const rows = list();
    const hit = rows.some((row) => conflicts(block, row));
    if (hit) {
      block.start = origin.start;
      block.end = origin.end;
      block.date = origin.date;
      if (typeof render === 'function') render();
      if (typeof toast === 'function') toast('这个位置和已有安排重叠，已撤回');
      return;
    }

    if (typeof hsave === 'function') hsave();
    window.__xjSuppressClick = true;
    if (typeof render === 'function') render();
    if (typeof toast === 'function') {
      toast(`${block.title} → ${minutesLabel(block.start)}–${minutesLabel(block.end)}`);
    }
  }

  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('pointermove', onPointerMove, true);
  document.addEventListener('pointerup', onPointerUp, true);
  document.addEventListener('pointercancel', onPointerUp, true);

  // 拖动结束后吞掉那一次 click，避免又弹出编辑器
  document.addEventListener(
    'click',
    (event) => {
      if (!window.__xjSuppressClick) return;
      window.__xjSuppressClick = false;
      event.stopPropagation();
      event.preventDefault();
    },
    true
  );

  window.xjBlockDrag = { snap, minutesLabel };
})();
