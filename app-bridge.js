/* app-bridge.js — 把旧版账号层接到 V0.13 的侧栏账号菜单上。
 *
 * 只做三件小事，不改任何布局：
 *   1. 给新的账号按钮内部节点补上 auth.js 认识的 id（昵称 / 头像首字）
 *   2. 在账号菜单里加一条「账号与同步」，打开 auth.js 的账号弹窗
 *   3. 补一个同步状态行，替换菜单顶部那句话
 */
(function () {
  'use strict';

  function decorate() {
    const account = document.querySelector('.workspace-account');
    if (account) {
      const avatar = account.querySelector('.avatar');
      if (avatar && !avatar.id) avatar.id = 'accountAvatar';
      const label = account.querySelector('.account-label');
      if (label && !label.id) label.id = 'accountName';
    }

    const menu = document.getElementById('accountMenu');
    if (menu && !menu.querySelector('[data-account]')) {
      const status = document.createElement('small');
      status.id = 'syncState';
      status.className = 'sync-state';
      status.textContent = '本地演示';
      menu.insertBefore(status, menu.firstChild);

      const item = document.createElement('button');
      item.type = 'button';
      item.setAttribute('data-account', '');
      item.textContent = '账号与同步';
      item.addEventListener('click', function (event) {
        event.stopPropagation();
        menu.classList.add('hidden');
        const toggle = document.getElementById('workspaceAccount');
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
        if (window.XJ && typeof window.XJ.openAccount === 'function') {
          window.XJ.openAccount();
        }
      });
      menu.appendChild(item);
    }

    // auth.js 会写这两个节点；没有就补一个隐藏占位，避免任何未判空的地方报错。
    ['sidebarName', 'sidebarAvatar', 'agentState'].forEach(function (id) {
      if (document.getElementById(id)) return;
      const span = document.createElement('span');
      span.id = id;
      span.hidden = true;
      document.body.appendChild(span);
    });
  }

  decorate();

  // 页面重绘后再兜一次（账号区本身是常驻节点，通常不需要重建）。
  const priorRender = window.render;
  if (typeof priorRender === 'function' && !priorRender.__bridgeWrapped) {
    const wrapped = function () {
      const result = priorRender.apply(this, arguments);
      decorate();
      return result;
    };
    wrapped.__bridgeWrapped = true;
    window.render = wrapped;
  }
})();
