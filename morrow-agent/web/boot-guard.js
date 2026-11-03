/* boot-guard.js — 兜底：页面骨架出来了但内容空白时，强制渲染一次
 *
 * 场景：会话校验/健康检查卡住（比如后端刚重启、file:// 打开、代理拦截），
 * 结果侧边栏在、右边一片空白。这里在启动后检查一次 #view，
 * 如果还是空的就自己调 render()，并把启动遮罩收起来。
 *
 * 登录页（body.morrow-locked）交给 auth.js，这里不抢渲染。
 */
(function () {
  'use strict';

  function viewEmpty() {
    const view = document.getElementById('view');
    return !view || !view.innerHTML.trim();
  }

  function locked() {
    return document.body.classList.contains('morrow-locked');
  }

  function unblock() {
    document.body.classList.remove('morrow-booting');
  }

  function rescue() {
    if (locked()) return;
    if (!viewEmpty()) {
      unblock();
      return;
    }
    try {
      if (typeof window.render === 'function') window.render();
    } catch (error) {
      console.warn('[morrow] render fallback failed', error);
    }
    if (viewEmpty()) {
      // 再退一步：至少让首页骨架出来，避免整屏空白
      try {
        if (typeof home === 'function') {
          const view = document.getElementById('view');
          if (view) view.innerHTML = home();
        }
      } catch (_error) {
        /* 什么都不做也比白屏好，控制台会留下原因 */
      }
    }
    unblock();
  }

  function schedule() {
    setTimeout(rescue, 1200);
    setTimeout(rescue, 3000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', schedule, { once: true });
  } else {
    schedule();
  }
})();