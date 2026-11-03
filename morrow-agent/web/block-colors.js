/* block-colors.js — 时间块颜色：缺省归类 + 新建时用选民的颜色
 *
 * 背景：新块来自多处（日历空白、AI 建议、信息卡），保存路径不统一，
 * 有的会丢掉 color。这里在渲染前做一次归一：
 *   1. 有合法 color → 不动；
 *   2. 刚在信息卡里选了颜色（window.__xjPendingColor）→ 用这个；
 *   3. 否则按标题/关联待办猜一个类别颜色。
 * 只在真的改了东西时才写回，避免每次渲染都产生脏写。
 */
(function () {
  'use strict';

  const PALETTE = ['blue', 'gold', 'green', 'purple', 'pink'];
  const HINT = [
    [/论文|实验|投稿|课程|学习|阅读|写作|复习|练习/, 'blue'],
    [/面试|投递|会议|汇报|工作|项目|客户|周报/, 'gold'],
    [/生活|运动|健身|做饭|打扫|休息|散步|睡觉/, 'green'],
    [/成长|读书|反思|复盘|日记|冥想/, 'purple'],
    [/临时|帮忙|协作|支持|突发/, 'pink'],
  ];

  function guess(block) {
    const text = `${block.title || ''} ${block.tags || ''}`;
    for (const [pattern, color] of HINT) {
      if (pattern.test(text)) return color;
    }
    return 'blue';
  }

  function list() {
    if (typeof hs !== 'object' || !hs) return [];
    return [].concat(hs.blocks || [], hs.actual || []);
  }

  function normalize() {
    const pending = window.__xjPendingColor;
    if (pending && !PALETTE.includes(pending)) window.__xjPendingColor = null;
    let changed = false;
    const rows = list();
    const missing = rows.filter((block) => !PALETTE.includes(block.color));
    if (!missing.length) return false;

    // 新建的块优先用刚选的色（最近创建的那一个）
    const target = missing[missing.length - 1];
    missing.forEach((block) => {
      if (block === target && window.__xjPendingColor) block.color = window.__xjPendingColor;
      else block.color = guess(block);
      changed = true;
    });
    window.__xjPendingColor = null;
    return changed;
  }

  function apply() {
    const changed = normalize();
    if (changed && typeof (window.hsave || hsave) === 'function') {
      const save = window.hsave || hsave;
      try {
        save();
      } catch (_error) {
        /* 渲染不受影响 */
      }
    }
    return changed;
  }

  const wrap = () => {
    if (typeof window.render === 'function' && !window.__xjColorWrapped) {
      const base = window.render;
      window.render = function wrappedColorRender() {
        apply();
        return base.apply(this, arguments);
      };
      window.__xjColorWrapped = true;
    }
    apply();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wrap, { once: true });
  } else {
    wrap();
  }

  /* 渲染时逐块取色：优先已存颜色 → 新建时选的色 → 按标题猜 */
  function colorOf(block) {
    if (PALETTE.includes(block.color)) return block.color;
    const rows = list();
    const colorless = rows.filter((row) => !PALETTE.includes(row.color));
    const isLatest = colorless.length && colorless[colorless.length - 1] === block;
    if (isLatest && window.__xjPendingColor) {
      block.color = window.__xjPendingColor;
      window.__xjPendingColor = null;
      if (typeof (window.hsave || hsave) === 'function') {
        try {
          (window.hsave || hsave)();
        } catch (_error) {
          /* 忽略 */
        }
      }
      return block.color;
    }
    return guess(block);
  }

  window.xjBlockColors = { apply, guess, colorOf, PALETTE };
})();
