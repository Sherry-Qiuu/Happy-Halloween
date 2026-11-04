/* tag-colors.js — 用标签区分颜色
 *
 * 颜色不属于时间块，属于标签：
 *   hs.tags = [{ name: '学习', color: 'blue' }, ...]
 * 一个时间块可以有多个标签（逗号分隔），取第一个标签的颜色。
 * 标签可以新建、也可以改色，改色后所有用到它的块一起变。
 */
(function () {
  'use strict';

  const PALETTE = ['blue', 'gold', 'green', 'purple', 'pink', 'grey'];
  const COLOR_LABEL = { blue: '蓝', gold: '金', green: '绿', purple: '紫', pink: '粉', grey: '灰' };
  const COLOR_HEX = { blue: '#7d9bc4', gold: '#c9a45f', green: '#8fbf9a', purple: '#9b8fc4', pink: '#c9a9c0', grey: '#b9b6ad' };
  const DEFAULT_TAGS = [
    { name: '学习', color: 'blue' },
    { name: '工作', color: 'gold' },
    { name: '生活', color: 'green' },
    { name: '个人成长', color: 'purple' },
    { name: '临时支持', color: 'pink' },
  ];

  function store() {
    if (typeof hs !== 'object' || !hs) return null;
    return hs;
  }

  function ensure() {
    const doc = store();
    if (!doc) return false;
    let changed = false;
    if (!Array.isArray(doc.tags)) {
      doc.tags = DEFAULT_TAGS.map((tag) => ({ ...tag }));
      changed = true;
    } else {
      // 补齐名字但没颜色的标签
      doc.tags.forEach((tag, index) => {
        if (!tag || !tag.name) return;
        if (!PALETTE.includes(tag.color)) {
          tag.color = DEFAULT_TAGS[index % DEFAULT_TAGS.length].color;
          changed = true;
        }
      });
    }
    // 老数据：块只有 color 没有标签 → 补一个同色标签
    [].concat(doc.blocks || [], doc.actual || []).forEach((block) => {
      if (!block || block.tags) return;
      const tag = tagOfColor(block.color);
      if (tag) {
        block.tags = tag.name;
        changed = true;
      }
    });
    return changed;
  }

  function tagOfColor(color) {
    const doc = store();
    if (!doc || !Array.isArray(doc.tags)) return null;
    return doc.tags.find((tag) => tag.color === color) || null;
  }

  function names(block) {
    return String((block && block.tags) || '')
      .split(/[,\uFF0C]/)
      .map((part) => part.trim())
      .filter(Boolean);
  }

  function tagColor(name) {
    const doc = store();
    if (!doc || !Array.isArray(doc.tags)) return null;
    const hit = doc.tags.find((tag) => tag.name === name);
    return hit ? hit.color : null;
  }

  function colorOf(block) {
    for (const name of names(block)) {
      const color = tagColor(name);
      if (color) return color;
    }
    return 'grey';
  }

  function addTag(name, color) {
    const doc = store();
    if (!doc || !name) return null;
    doc.tags = Array.isArray(doc.tags) ? doc.tags : [];
    const existing = doc.tags.find((tag) => tag.name === name);
    if (existing) {
      if (color) existing.color = color;
      save();
      return existing;
    }
    const tag = { name, color: PALETTE.includes(color) ? color : PALETTE[doc.tags.length % PALETTE.length] };
    doc.tags.push(tag);
    save();
    return tag;
  }

  function setColor(name, color) {
    const doc = store();
    if (!doc || !Array.isArray(doc.tags) || !PALETTE.includes(color)) return false;
    const tag = doc.tags.find((item) => item.name === name);
    if (!tag) return false;
    tag.color = color;
    save();
    return true;
  }

  function save() {
    const fn = window.hsave || (typeof hsave === 'function' ? hsave : null);
    if (fn) {
      try {
        fn();
      } catch (_error) {
        /* 忽略 */
      }
    }
  }

  function chipsHTML(activeNames) {
    const doc = store();
    const tags = (doc && doc.tags) || DEFAULT_TAGS;
    return tags
      .map((tag) => {
        const on = activeNames.includes(tag.name) ? ' on' : '';
        return (
          `<button type="button" class="tagchip c-${tag.color}${on}" data-tagpick="${tag.name}" ` +
          `title="${COLOR_LABEL[tag.color] || ''}色 · 点击切换">` +
          `<i></i>${tag.name}</button>`
        );
      })
      .join('');
  }

  function colorOptions(selected) {
    return PALETTE.map(
      (color) =>
        `<option value="${color}"${color === selected ? ' selected' : ''}>${COLOR_LABEL[color] || color}</option>`
    ).join('');
  }

  /* 打开信息卡时，把标签面板画成带颜色的 chips */
  function paintPanels() {
    const panel = document.getElementById('i-tag');
    const input = document.getElementById('i-tagsvalue');
    if (!panel || !input) return;
    let holder = document.getElementById('i-tagchips');
    if (!holder) {
      holder = document.createElement('div');
      holder.id = 'i-tagchips';
      holder.className = 'tagchips';
      input.parentElement.insertAdjacentElement('afterend', holder);
    }
    const html = chipsHTML(names({ tags: input.value }));
    if (holder.__xjHTML !== html) {
      holder.innerHTML = html;
      holder.__xjHTML = html;
    }

    if (!document.getElementById('i-tagnew')) {
      const form = document.createElement('div');
      form.id = 'i-tagnew';
      form.className = 'tagnew';
      form.innerHTML =
        '<input id="i-newtagname" placeholder="新标签名" aria-label="新标签名">' +
        `<select id="i-newtagcolor" aria-label="新标签颜色">${colorOptions('blue')}</select>` +
        '<button type="button" id="i-addtag">添加</button>';
      holder.insertAdjacentElement('afterend', form);
    }
  }

  function applyTagToInput(name) {
    const input = document.getElementById('i-tagsvalue');
    if (!input) return;
    const current = names({ tags: input.value });
    const next = current.includes(name)
      ? current.filter((item) => item !== name)
      : current.concat(name);
    input.value = next.join(',');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    paintPanels();
  }

  document.addEventListener('click', (event) => {
    const pick = event.target.closest && event.target.closest('[data-tagpick]');
    if (pick) {
      event.preventDefault();
      applyTagToInput(pick.getAttribute('data-tagpick'));
      return;
    }
    const add = event.target.closest && event.target.closest('#i-addtag');
    if (add) {
      event.preventDefault();
      const nameInput = document.getElementById('i-newtagname');
      const colorInput = document.getElementById('i-newtagcolor');
      const name = nameInput ? nameInput.value.trim() : '';
      if (!name) return;
      addTag(name, colorInput ? colorInput.value : 'blue');
      if (nameInput) nameInput.value = '';
      applyTagToInput(name);
      if (typeof render === 'function') render();
      return;
    }
    // 改某个标签的颜色（标签列表里的小圆点）
    const recolor = event.target.closest && event.target.closest('[data-tagrecolor]');
    if (recolor) {
      event.preventDefault();
      const name = recolor.getAttribute('data-tagrecolor');
      const next = recolor.getAttribute('data-nextcolor');
      setColor(name, next);
      if (typeof render === 'function') render();
      paintPanels();
    }
  });

  const wrap = () => {
    ensure();
    paintPanels();
    if (typeof window.render === 'function' && !window.__xjTagWrapped) {
      const base = window.render;
      window.render = function wrappedTagRender() {
        ensure();
        return base.apply(this, arguments);
      };
      window.__xjTagWrapped = true;
    }
    // 打开信息卡时补画（不用 MutationObserver：它会被我们自己的插入触发成死循环）
    document.addEventListener(
      'click',
      (event) => {
        if (event.target.closest && event.target.closest('[data-ipanel], [data-hblock], [data-haddtask]')) {
          setTimeout(paintPanels, 60);
        }
      },
      true
    );
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wrap, { once: true });
  } else {
    wrap();
  }

  window.xjTagColors = {
    ensure,
    names,
    colorOf,
    tagColor,
    addTag,
    setColor,
    chipsHTML,
    colorOptions,
    hex: (name) => COLOR_HEX[name] || COLOR_HEX.grey,
    hexForLabel: (label) => COLOR_HEX[tagColor(label)] || null,
    PALETTE,
    COLOR_LABEL,
  };
})();
