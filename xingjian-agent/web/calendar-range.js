/* calendar-range.js — 日历显示天数与时间区间
 *
 * 天数：默认 3 天，可在标题栏下拉里改成 1/2/3/5/7 天，记在本机。
 * 区间：不再固定 06:00–23:00，而是按这批天里时间块的最早/最晚自动收放，
 *       上下各留 1 小时；空日历回落到 07:00–23:00，最少 8 小时。
 */
(function () {
  'use strict';

  const DAYS_KEY = 'xingjian-cal-days';
  const DEFAULT_DAYS = 3;
  const FALLBACK = { start: 7, end: 23 };
  const PAD_HOURS = 1;
  const MIN_SPAN = 8;

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function days() {
    const raw = Number(localStorage.getItem(DAYS_KEY));
    return [1, 2, 3, 5, 7].includes(raw) ? raw : DEFAULT_DAYS;
  }

  function setDays(value) {
    const next = [1, 2, 3, 5, 7].includes(Number(value)) ? Number(value) : DEFAULT_DAYS;
    localStorage.setItem(DAYS_KEY, String(next));
    window.xjCalDays = days;
    return next;
  }

  function shiftDate(iso, delta) {
    const day = new Date(iso + 'T12:00:00');
    day.setDate(day.getDate() + delta);
    const pad = (n) => String(n).padStart(2, '0');
    return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
  }

  function anchorDate() {
    try {
      return typeof anchor !== 'undefined' ? anchor : (typeof date !== 'undefined' ? date : null);
    } catch (_error) {
      return null;
    }
  }

  function range(dayIndexes) {
    const base = anchorDate();
    const doc = typeof hs === 'object' && hs ? hs : null;
    const rows = doc ? [].concat(doc.blocks || [], doc.actual || []) : [];
    const wanted = new Set(
      (dayIndexes || []).map((index) => (base ? shiftDate(base, index) : null)).filter(Boolean)
    );
    const picks = rows.filter((row) => row && wanted.has(row.date));
    if (!picks.length) return { ...FALLBACK };

    let start = Math.min(...picks.map((row) => Number(row.start) || 0));
    let end = Math.max(...picks.map((row) => Number(row.end) || 0));
    start = Math.floor(start - PAD_HOURS);
    end = Math.ceil(end + PAD_HOURS);
    if (end - start < MIN_SPAN) {
      const mid = (start + end) / 2;
      start = Math.floor(mid - MIN_SPAN / 2);
      end = Math.ceil(mid + MIN_SPAN / 2);
    }
    // 0–24 内收口，且保证足够长
    if (start < 0) {
      end = Math.min(24, end - start);
      start = 0;
    }
    if (end > 24) {
      start = Math.max(0, start - (end - 24));
      end = 24;
    }
    return { start: clamp(start, 0, 23), end: clamp(end, 2, 24) };
  }

  function geom() {
    const day = document.querySelector('.hday');
    const rangeNow = typeof window.__xjCalRangeNow === 'object' && window.__xjCalRangeNow
      ? window.__xjCalRangeNow
      : FALLBACK;
    if (!day) return { pxPerHour: 40, range: rangeNow };
    const span = Math.max(1, rangeNow.end - rangeNow.start);
    return { pxPerHour: day.clientHeight / span, range: rangeNow };
  }

  window.xjCalDays = days;
  window.xjCalSetDays = setDays;
  window.xjCalRange = range;
  window.xjCalShift = shiftDate;
  window.xjCalGeom = geom;
})();
