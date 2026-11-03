/* today.js — 全站唯一的“今天”
 *
 * 以前把 2026-10-31 写死在十几个地方，导致日历高亮、日期列、周反思的
 * 默认锚点都和真实时间对不上。这个文件在其它脚本之前加载，提供：
 *   XJ_TODAY       今天的本地日期（YYYY-MM-DD）
 *   xjShift(d, n)  在某个日期上加减天数
 *   xjWeekStart()  本周一
 *   xjTodayLabel() 今天的中文标签，例如“11 月 1 日 · 周日”
 */
(function () {
  'use strict';

  const pad = (value) => String(value).padStart(2, '0');

  function iso(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function parse(value) {
    const parts = String(value || '').split('-').map(Number);
    return new Date(parts[0], (parts[1] || 1) - 1, parts[2] || 1);
  }

  const today = new Date();
  window.XJ_TODAY = iso(today);
  window.XJ_TODAY_OBJ = today;

  window.xjShift = function xjShift(base, delta) {
    const date = parse(base || window.XJ_TODAY);
    date.setDate(date.getDate() + Number(delta || 0));
    return iso(date);
  };

  window.xjWeekStart = function xjWeekStart(base) {
    const date = parse(base || window.XJ_TODAY);
    const weekday = date.getDay() || 7; // 周一 = 1，周日 = 7
    date.setDate(date.getDate() - (weekday - 1));
    return iso(date);
  };

  window.xjTodayLabel = function xjTodayLabel() {
    const names = ['日', '一', '二', '三', '四', '五', '六'];
    const date = new Date();
    return `${date.getMonth() + 1} 月 ${date.getDate()} 日 · 周${names[date.getDay()]}`;
  };

  window.xjIsToday = function xjIsToday(value) {
    return String(value) === window.XJ_TODAY;
  };
})();
