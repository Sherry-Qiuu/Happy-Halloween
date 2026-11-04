/* demo-seed.js — 演示用的虚拟场景数据
 *
 * 目标：演示时每个页面都有内容，且日历上有明显的颜色差异
 * （颜色由标签决定：学习蓝 / 工作金 / 生活绿 / 个人成长紫 / 临时支持粉）。
 *
 * 组织方式：
 *   5 个项目（目标）——秋招冲刺、机器学习课程复习、英语词汇 6000、
 *   作品集上线、生活节奏；另加少量不属于任何项目的当日杂事。
 *   时间块和待办尽量挂到具体项目上，Project 页才有推进感。
 *
 * 触发：
 *   首次打开且没有自己的时间块 → 自动种一次
 *   想重置 → 打开 ?demo=1
 *   已有自己的数据 → 不覆盖
 */
(function () {
  'use strict';

  const FLAG = 'morrow-demo-seeded-v2';
  const WANT_DEMO = 'morrow-want-demo';
  const WANT_BLANK = 'morrow-want-blank';
  const HOME_KEY = 'morrow-home-v05';

  const T = (offset) => window.xjShift(window.XJ_TODAY, offset);
  const D = {
    d2: T(-2),
    d1: T(-1),
    d0: T(0),
    p1: T(1),
    p2: T(2),
    p3: T(3),
  };

  const TAGS = [
    { name: '学习', color: 'blue' },
    { name: '工作', color: 'gold' },
    { name: '生活', color: 'green' },
    { name: '个人成长', color: 'purple' },
    { name: '临时支持', color: 'pink' },
  ];

  const PROJECT = {
    job: '秋招冲刺',
    ml: '机器学习课程复习',
    vocab: '英语词汇 6000',
    portfolio: '作品集上线',
    life: '生活节奏',
  };

  const BLOCKS = [
    { id: 'd2-b1', date: D.d2, title: '背单词 · 新词 30 个', start: 7, end: 7.5, tags: '学习', project: PROJECT.vocab },
    { id: 'd2-b2', date: D.d2, title: '改简历：项目描述', start: 10, end: 11.5, tags: '工作', project: PROJECT.job },
    { id: 'd2-b3', date: D.d2, title: '复习课件 第 3 讲', start: 15, end: 16.5, tags: '学习', project: PROJECT.ml },
    { id: 'd2-b4', date: D.d2, title: '看电影', start: 20, end: 22, tags: '生活', project: PROJECT.life },

    { id: 'd1-b1', date: D.d1, title: '晨跑', start: 7, end: 8, tags: '生活', project: PROJECT.life },
    { id: 'd1-b2', date: D.d1, title: '复习课件 第 4 讲', start: 10, end: 11.5, tags: '学习', project: PROJECT.ml },
    { id: 'd1-b3', date: D.d1, title: '作品集案例（锁定）', start: 15, end: 16.5, tags: '工作', project: PROJECT.portfolio, locked: true },
    { id: 'd1-b4', date: D.d1, title: '线上课程（锁定）', start: 19, end: 20, tags: '学习', project: PROJECT.ml, locked: true },
    { id: 'd1-b5', date: D.d1, title: '帮同学看简历', start: 21, end: 21.5, tags: '临时支持', project: '' },

    { id: 'd0-b1', date: D.d0, title: '背单词 · 复习昨天 20 个', start: 7, end: 7.5, tags: '学习', project: PROJECT.vocab },
    { id: 'd0-b2', date: D.d0, title: '模拟面试 · 项目自述', start: 9, end: 10.5, tags: '工作', project: PROJECT.job },
    { id: 'd0-b3', date: D.d0, title: '做课后题 4–6 讲', start: 11, end: 12, tags: '学习', project: PROJECT.ml },
    { id: 'd0-b4', date: D.d0, title: '写 300 字复盘', start: 13, end: 13.5, tags: '个人成长', project: '' },
    { id: 'd0-b5', date: D.d0, title: '改作品集 · 案例叙事', start: 14, end: 15.5, tags: '工作', project: PROJECT.portfolio },
    { id: 'd0-b6', date: D.d0, title: '帮室友调环境', start: 16, end: 16.5, tags: '临时支持', project: '' },
    { id: 'd0-b7', date: D.d0, title: '看小说', start: 21, end: 22, tags: '生活', project: PROJECT.life },

    { id: 'p1-b1', date: D.p1, title: '背单词 · 新词 30 个', start: 8, end: 8.5, tags: '学习', project: PROJECT.vocab },
    { id: 'p1-b2', date: D.p1, title: '投递 2 家（售前岗）', start: 10, end: 11, tags: '工作', project: PROJECT.job },
    { id: 'p1-b3', date: D.p1, title: '重写项目描述（量化结果）', start: 14, end: 15.5, tags: '工作', project: PROJECT.job },
    { id: 'p1-b4', date: D.p1, title: '读一篇长文', start: 20, end: 20.5, tags: '个人成长', project: '' },

    { id: 'p2-b1', date: D.p2, title: '复习课件 第 5 讲', start: 9, end: 10, tags: '学习', project: PROJECT.ml },
    { id: 'p2-b2', date: D.p2, title: '作品集部署上线', start: 14, end: 16, tags: '工作', project: PROJECT.portfolio },
    { id: 'p2-b3', date: D.p2, title: '和朋友吃饭', start: 18, end: 19.5, tags: '生活', project: PROJECT.life },
    { id: 'p2-b4', date: D.p2, title: '写周复盘', start: 21, end: 21.5, tags: '个人成长', project: '' },

    { id: 'p3-b1', date: D.p3, title: '模拟面试 第二轮', start: 10, end: 11.5, tags: '工作', project: PROJECT.job },
    { id: 'p3-b2', date: D.p3, title: '背单词 · 累计过 3000', start: 19, end: 19.5, tags: '学习', project: PROJECT.vocab },
  ];

  const ACTUAL = [
    { id: 'a1', date: D.d1, title: '复习课件 第 4 讲', start: 10, end: 11.75, tags: '学习', estimated: true },
    { id: 'a2', date: D.d1, title: '改简历：项目描述', start: 16.5, end: 17.25, tags: '工作', estimated: false },
    { id: 'a3', date: D.d0, title: '模拟面试 · 项目自述', start: 9, end: 10.25, tags: '工作', estimated: true },
    { id: 'a4', date: D.d0, title: '帮室友调环境', start: 16, end: 16.75, tags: '临时支持', estimated: false },
    { id: 'a5', date: D.d0, title: '写 300 字复盘', start: 22, end: 22.5, tags: '个人成长', estimated: false },
  ];

  const TASKS = [
    {
      id: 't1', title: '把简历项目描述改写成结果导向', tags: '工作', priority: '高',
      start: D.d2, end: D.p1,
      subtasks: ['挑出 3 个最相关的项目', '每条补一个量化结果', '压缩到一页'],
      subdone: [true, true, false],
      description: '秋招投递前先定稿，改完再批量投。',
      goal: PROJECT.job, minutes: 90, done: false, locked: false,
    },
    {
      id: 't2', title: '复习机器学习第 5–6 讲', tags: '学习', priority: '高',
      start: D.d1, end: T(3),
      subtasks: ['过一遍课件', '手推一遍梯度下降', '做 5 道课后题'],
      subdone: [true, false, false],
      description: '课程进度落后两讲，先补到第 6 讲。',
      goal: PROJECT.ml, minutes: 120, done: false, locked: false,
    },
    {
      id: 't3', title: '背单词 50 个（考研词汇）', tags: '学习', priority: '中',
      start: D.d2, end: D.p3,
      subtasks: ['新词 30 个', '复习昨天 20 个'],
      subdone: [true, true],
      description: '每天早上固定 20 分钟。',
      goal: PROJECT.vocab, minutes: 20, done: true, locked: false,
    },
    {
      id: 't4', title: '写一篇电影短评', tags: '生活', priority: '低',
      start: D.d2, end: T(2),
      subtasks: ['记一句最打动你的台词', '写 300 字'],
      subdone: [false, false],
      description: '看完当天写，别攒着。',
      goal: PROJECT.life, minutes: 30, done: false, locked: false,
    },
    {
      id: 't5', title: '整理作品集里的项目叙事', tags: '工作', priority: '中',
      start: D.d0, end: T(4),
      subtasks: ['挑 3 个项目', '每个写背景 / 动作 / 结果'],
      subdone: [true, false],
      description: '目标：能直接发给面试官。',
      goal: PROJECT.portfolio, minutes: 60, done: false, locked: false,
    },
    {
      id: 't6', title: '投递 2 家售前岗', tags: '工作', priority: '高',
      start: D.p1, end: T(4),
      subtasks: ['筛 2 个匹配度高的 JD', '按岗位改一版简历', '投出并记录'],
      subdone: [false, false, false],
      description: '投递后把链接记在项目备注里。',
      goal: PROJECT.job, minutes: 45, done: false, locked: false,
    },
    {
      id: 't7', title: '帮室友把开发环境配好', tags: '临时支持', priority: '低',
      start: D.d0, end: T(1),
      subtasks: ['装 Python 环境', '跑通一个 demo'],
      subdone: [true, true],
      description: '已经弄好了，记一条实际用时。',
      goal: '', minutes: 30, done: true, locked: false,
    },
    {
      id: 't8', title: '本周复盘：哪些安排真的有用', tags: '个人成长', priority: '中',
      start: D.p2, end: T(5),
      subtasks: ['回看本周时间块', '挑一件下周要继续的', '挑一件要停掉的'],
      subdone: [false, false, false],
      description: '周反思之前先自己写一版。',
      goal: '', minutes: 30, done: false, locked: false,
    },
  ];

  const GOALS = [
    {
      id: 'g1', title: PROJECT.job, category: '工作', date: T(9),
      standard: '一页简历 + 5 家投递 + 一轮模拟面试',
      stages: ['简历定稿', '批量投递 5 家', '模拟面试并复盘'],
      actions: ['挑出 3 个项目 · 30 分钟', '按 JD 改一版简历 · 45 分钟'],
      stage: 1,
    },
    {
      id: 'g2', title: PROJECT.ml, category: '学习', date: T(12),
      standard: '补完第 1–8 讲，课后题做完',
      stages: ['第 1–3 讲', '第 4–6 讲', '第 7–8 讲 + 模拟题'],
      actions: ['过一遍第 5 讲课件 · 40 分钟'],
      stage: 1,
    },
    {
      id: 'g3', title: PROJECT.vocab, category: '学习', date: T(45),
      standard: '累计 6000 词，能认出 90%',
      stages: ['3000 词', '4500 词', '6000 词'],
      actions: ['早上背 30 个新词 · 20 分钟'],
      stage: 0,
    },
    {
      id: 'g4', title: PROJECT.portfolio, category: '工作', date: T(14),
      standard: '3 个案例 + 一页介绍，能公网访问',
      stages: ['挑项目', '写案例叙事', '部署上线'],
      actions: ['写第一个案例的背景与结果 · 45 分钟'],
      stage: 1,
    },
    {
      id: 'g5', title: PROJECT.life, category: '生活', date: T(30),
      standard: '每周运动 2 次 + 看 1 部电影 + 写 1 篇短评',
      stages: ['建立固定运动时段', '每周一部电影', '写 4 篇短评'],
      actions: ['周末排 60 分钟运动 · 60 分钟'],
      stage: 0,
    },
  ];

  const REVIEWS = {
    [D.d2]: {
      text: '上午改简历改得挺顺，下午复习课件有点走神。晚上看了部电影，算是给自己松了个口。',
      tags: ['工作', '学习', '生活'],
      report: '',
    },
    [D.d1]: {
      text: '第 4 讲比预计多花了 20 分钟，晚上线上课程有点困。帮同学看简历打乱了原本的节奏。',
      tags: ['学习', '临时支持'],
      report: '',
    },
    [D.d0]: {
      text: '模拟面试讲项目时还是卡在数据那一段。下午改作品集有点拖，晚上看了会儿小说。',
      tags: ['工作', '生活', '个人成长'],
      report: '',
      insight: {
        facts: [
          '今天 7 个时间块，覆盖 5 个标签：学习、工作、生活、个人成长、临时支持。',
          '实际记录 3 条，共约 2 小时（其中 1 条是估算）。',
          '8 条待办里 2 条完成，3 条进行中。',
        ],
        patterns: [
          '讲项目时卡壳集中在数据部分，看起来素材还没整理成能直接说的版本。',
          '临时支持多落在下午 16 点，容易打断原本的深度工作块。',
        ],
        experiment: '下次面试前先写一段 60 秒的项目自述，把数据结果放到最后一句说。',
        questions: ['数据那段是因为记不清数字，还是不知道该怎么讲？'],
        generated_at: Math.round(Date.now() / 1000) - 1800,
        source: 'demo',
      },
    },
  };

  // 原型自带的默认数据（用来区分“用户自己排的”与“还没动过的模板”）
  const STOCK_TITLES = new Set([
    '晨间阅读', '整理资料', '英语回答练习', '作品集案例', '线上课程',
    '模拟面试', '修改作品集', '看书', '健身', '作品集梳理', '写周报',
  ]);

  function blocksOf(home) {
    return home && Array.isArray(home.blocks) ? home.blocks : [];
  }

  function looksLikeDemo(home) {
    return blocksOf(home).some((block) => {
      const id = String(block.id || '');
      return /^(d\d?-b|p\d-b|d-b|a\d+)/.test(id);
    });
  }

  function dataKeyName() {
    return window.XJ && XJ.dataKey ? XJ.dataKey() : 'morrow-v02';
  }

  /* 空白起点：新账号、或用户主动 ?blank=1 时使用 */
  function seedBlank() {
    localStorage.setItem(
      HOME_KEY,
      JSON.stringify({ blocks: [], tasks: [], actual: [], tags: TAGS })
    );
    localStorage.setItem(
      dataKeyName(),
      JSON.stringify({
        goals: [],
        reviews: {},
        tasks: [],
        extra: [],
        name: '',
        reminder: false,
        moved: true,
      })
    );
    localStorage.setItem(FLAG, '0');
  }

  function seedDemo() {
    localStorage.setItem(
      HOME_KEY,
      JSON.stringify({ blocks: BLOCKS, tasks: TASKS, actual: ACTUAL, tags: TAGS })
    );
    let existing = null;
    try {
      existing = JSON.parse(localStorage.getItem(dataKeyName()) || 'null');
    } catch (_error) {
      existing = null;
    }
    const base = existing && typeof existing === 'object' ? existing : {};
    localStorage.setItem(
      dataKeyName(),
      JSON.stringify({
        ...base,
        goals: GOALS,
        reviews: { ...(base.reviews || {}), ...REVIEWS },
        tasks: TASKS.map((task) => ({
          id: task.id,
          title: task.title,
          minutes: task.minutes,
          goal: task.goal,
          done: task.done,
        })),
        extra: base.extra || [],
        name: base.name || '明',
        reminder: base.reminder || false,
        moved: true,
      })
    );
    localStorage.setItem(FLAG, '1');
  }

  function reloadOnce() {
    const marker = 'morrow-demo-reloading';
    if (sessionStorage.getItem(marker)) return false;
    sessionStorage.setItem(marker, '1');
    location.reload();
    return true;
  }

  /* 等登录流程跑完（auth.js 会先把服务端数据写回本地，
     如果我们在那之前写入演示数据，会被服务端的空数据覆盖） */
  function whenReady(callback) {
    if (window.__xjReady) {
      callback();
      return;
    }
    let tries = 0;
    const timer = setInterval(function () {
      tries += 1;
      if (window.__xjReady) {
        clearInterval(timer);
        callback();
      } else if (tries > 400) {
        // 40 秒还没就绪（冷启动/网络慢）：照常写入本地，别再干等
        clearInterval(timer);
        callback();
      }
    }, 100);
  }

  function pushToServer() {
    const user = window.XJ && XJ.getUser ? XJ.getUser() : null;
    if (!user) return Promise.resolve();
    const key = window.XJ && XJ.dataKey ? XJ.dataKey() : 'morrow-v02';
    let snapshot = null;
    try {
      snapshot = JSON.parse(localStorage.getItem(key) || 'null');
    } catch (_error) {
      snapshot = null;
    }
    if (!snapshot) return Promise.resolve();
    return fetch('/api/data', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ data: snapshot }),
    }).catch(function () {
      /* 离线场景忽略：本地已经写好，下次保存会补上 */
    });
  }

  /* 显式切换模式：等登录就绪 → 写数据 → 推给服务端 → 去掉 URL 参数 → 刷新 */
  function switchMode(write) {
    sessionStorage.removeItem(WANT_DEMO);
    sessionStorage.removeItem(WANT_BLANK);
    whenReady(function () {
      write();
      Promise.resolve(pushToServer()).then(function () {
        try {
          history.replaceState(null, '', location.pathname);
        } catch (_error) {
          /* file:// 下 replaceState 可能不可用，忽略 */
        }
        sessionStorage.removeItem('morrow-demo-reloading');
        location.reload();
      });
    });
  }

  /* 不刷新页面的切换：直接改内存里的 hs / data 再 render，
     避免登录跳转、reload 时序带来的不确定性 */
  function applyToMemory(kind) {
    const demo = kind === 'demo';
    if (typeof hs === 'object' && hs) {
      hs.blocks = demo ? BLOCKS.map((item) => ({ ...item })) : [];
      hs.tasks = demo ? TASKS.map((item) => ({ ...item })) : [];
      hs.actual = demo ? ACTUAL.map((item) => ({ ...item })) : [];
      hs.tags = TAGS.map((item) => ({ ...item }));
      if (typeof hsave === 'function') {
        try { hsave(); } catch (_error) { /* 忽略 */ }
      }
    }
    if (typeof data === 'object' && data) {
      data.goals = demo ? GOALS.map((item) => ({ ...item })) : [];
      data.reviews = demo ? { ...(data.reviews || {}), ...REVIEWS } : {};
      data.tasks = demo
        ? TASKS.map((task) => ({
            id: task.id,
            title: task.title,
            minutes: task.minutes,
            goal: task.goal,
            done: task.done,
          }))
        : [];
      data.examplesV04 = true;
      if (typeof save === 'function') {
        try { save(); } catch (_error) { /* 忽略 */ }
      }
      if (window.XJ && XJ.queueSave) {
        try { XJ.queueSave(); } catch (_error) { /* 忽略 */ }
      }
    }
  }

  function loadDemo() {
    if (sessionStorage) sessionStorage.removeItem(WANT_DEMO);
    localStorage.setItem(FLAG, '1');
    seedDemo();
    applyToMemory('demo');
    if (typeof render === 'function') render();
    if (typeof toast === 'function') toast('已载入演示场景（26 个时间块）');
  }

  function clearAll() {
    if (sessionStorage) sessionStorage.removeItem(WANT_BLANK);
    localStorage.setItem(FLAG, '0');
    seedBlank();
    applyToMemory('blank');
    if (typeof render === 'function') render();
    if (typeof toast === 'function') toast('已清空，从现在开始');
  }

  window.xjDemo = {
    load: loadDemo,
    clear: clearAll,
    isOn: function () {
      return localStorage.getItem(FLAG) === '1';
    },
    toggle: function () {
      if (localStorage.getItem(FLAG) === '1') clearAll();
      else loadDemo();
    },
  };

  try {
    const params = new URLSearchParams(location.search);
    // 登录流程会用 location.replace('?signed=1') 跳一次，参数会丢，
    // 所以显式请求先记到 sessionStorage，跳转后还能接着办。
    if (params.get('demo') === '1') sessionStorage.setItem(WANT_DEMO, '1');
    if (params.get('blank') === '1') sessionStorage.setItem(WANT_BLANK, '1');
    const wantsDemo = sessionStorage.getItem(WANT_DEMO) === '1';
    const wantsBlank = sessionStorage.getItem(WANT_BLANK) === '1';
    const demoKnown = localStorage.getItem(FLAG) === '1';
    const hasHome = localStorage.getItem(HOME_KEY) !== null;

    let current = null;
    try {
      current = JSON.parse(localStorage.getItem(HOME_KEY) || 'null');
    } catch (_error) {
      current = null;
    }

    // 1) 主动要求演示场景：种一份完整数据
    if (wantsDemo) {
      switchMode(seedDemo);
      return;
    }

    // 2) 主动要求清空：回到真正的 0
    if (wantsBlank) {
      switchMode(seedBlank);
      return;
    }

    // 3) 全新浏览器（没有本地数据）：立刻写一份空白，从 0 开始
    //    这里不等登录：时间块（hs）不参与服务端同步，先写不会被打回去；
    //    等登录反而会超时，最后落回原型自带的示例数据。
    if (!hasHome) {
      seedBlank();
      reloadOnce();
      return;
    }

    // 4) 之前用 ?demo=1 打开过：保持演示模式，只在日期过期时按今天重排
    if (demoKnown && looksLikeDemo(current)) {
      const dates = blocksOf(current).map((block) => String(block.date || '')).filter(Boolean).sort();
      const newest = dates[dates.length - 1] || '';
      if (newest < window.XJ_TODAY) {
        seedDemo();
        reloadOnce();
      }
    }

    // 5) 其它情况（用户自己排过内容）：什么都不做，尊重用户数据
  } catch (error) {
    console.warn('[demo] seed failed', error);
  }
})();
