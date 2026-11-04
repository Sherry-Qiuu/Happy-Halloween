/**
 * Dependency-free DOM shim smoke test for web/agent.js.
 *
 * A real browser (or jsdom) is not available in every build box, so this
 * harness stubs just enough of the DOM to run the prototype's own script
 * plus the bridge, then drives the send -> proposal -> apply flow through
 * the same code paths the browser uses.
 *
 * What it covers: global wiring, SSE frame parsing, proposal rendering,
 * schedule application, and the reflection report path.
 * What it does not cover: layout, styling, and real user input.
 *
 * Run with: node tests/dom_smoke.mjs
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createContext, runInContext } from 'node:vm';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const html = readFileSync(join(root, 'web', 'index.html'), 'utf8');
const bridge = readFileSync(join(root, 'web', 'agent.js'), 'utf8');

const inlineScripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(
  (match) => match[1],
);
const mainScript = inlineScripts.find((code) =>
  code.includes('function render()')
);
if (!mainScript) throw new Error('prototype script not found');

function makeElement(tag = 'div') {
  return {
    tagName: tag.toUpperCase(),
    className: '',
    textContent: '',
    innerHTML: '',
    value: '',
    content: '',
    style: {},
    dataset: {},
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() {
        return false;
      },
    },
    setAttribute() {},
    getAttribute() {
      return null;
    },
    hasAttribute() {
      return false;
    },
    removeAttribute() {},
    addEventListener() {},
    removeEventListener() {},
    appendChild() {},
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    closest() {
      return null;
    },
    focus() {},
    blur() {},
    click() {},
    scrollTo() {},
    showModal() {},
    close() {},
  };
}

const listeners = {};
const storage = new Map();

const documentStub = {
  querySelector() {
    return makeElement();
  },
  querySelectorAll() {
    return [];
  },
  addEventListener(name, handler) {
    listeners[name] = handler;
  },
  createElement(tag) {
    return makeElement(tag);
  },
  body: makeElement('body'),
  documentElement: makeElement('html'),
};

const localStorageStub = {
  getItem(key) {
    return storage.has(key) ? storage.get(key) : null;
  },
  setItem(key, value) {
    storage.set(key, String(value));
  },
  removeItem(key) {
    storage.delete(key);
  },
};

function sseFrames(events) {
  return events
    .map(([name, payload]) => `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`)
    .join('');
}

const CHAT_FRAMES = sseFrames([
  ['delta', { text: '14:00 到 16:00 被会议占掉，' }],
  ['delta', { text: '英语练习先挪到 16:30。' }],
  [
    'proposal',
    {
      summary: '把英语练习挪到 16:30，并把临时会议写进 14:00–16:00',
      changes: [
        {
          type: 'move_event',
          date: '2026-10-31',
          title: '英语面试 · 自我介绍',
          start: '16:30',
          duration: 30,
        },
        {
          type: 'add_event',
          date: '2026-10-31',
          title: '临时会议',
          start: '14:00',
          duration: 120,
        },
      ],
    },
  ],
  ['done', {}],
]);

async function stubFetch(url, options = {}) {
  const path = String(url);
  if (path.endsWith('/api/agent/health')) {
    return new Response(
      JSON.stringify({ configured: true, model: 'deepseek-chat', mock: true }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }
  if (path.endsWith('/api/agent/chat')) {
    const chunks = CHAT_FRAMES.match(/[\s\S]{1,60}/g) || [];
    const stream = new ReadableStream({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(new TextEncoder().encode(chunk));
        }
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    });
  }
  if (path.endsWith('/api/agent/report')) {
    return new Response(
      JSON.stringify({ report: '记录到的事实\n会议占用了两个小时。' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }
  throw new Error(`unexpected fetch: ${path}`);
}

const sandbox = {
  console,
  document: documentStub,
  localStorage: localStorageStub,
  location: {
    protocol: 'http:',
    search: '',
    origin: 'http://127.0.0.1:8010',
    href: 'http://127.0.0.1:8010/',
  },
  navigator: { userAgent: 'smoke-test' },
  fetch: stubFetch,
  Response,
  ReadableStream,
  TextEncoder,
  TextDecoder,
  AbortController,
  URLSearchParams,
  structuredClone,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const context = createContext(sandbox);

const checks = [];
function check(name, condition, detail = '') {
  checks.push({ name, ok: !!condition, detail });
  const flag = condition ? 'ok  ' : 'FAIL';
  console.log(`  ${flag} ${name}${condition ? '' : ` -> ${detail}`}`);
}

runInContext(mainScript, context, { filename: 'index.prototype.js' });
runInContext(bridge, context, { filename: 'agent.js' });
runInContext('render();', context);

await new Promise((resolve) => setTimeout(resolve, 50));

const evaluate = (code) => runInContext(code, context);

console.log('agent.js bridge');
check('health resolved', evaluate('__xingjianAgent.reachable') === true);
check('configured flag', evaluate('__xingjianAgent.configured') === true);
check('model exposed', evaluate('__xingjianAgent.model') === 'deepseek-chat');

const homePanel = evaluate("ai('home')");
check('panel header', homePanel.includes('行间 AI'));
check('status dot ok', homePanel.includes('agentdot ok'));
check('empty state hint', homePanel.includes('今天有变化'));

const seedEvents = evaluate("events('2026-10-31')");
check('seed event one', seedEvents.includes('英语面试 · 自我介绍'));
check('seed event two', seedEvents.includes('整理作品集素材'));
check('seed event time', seedEvents.includes('14:00–14:30'));

const reviewPanel = evaluate("go('daily'); ai('review')");
check('review panel renders', reviewPanel.includes('结束并生成观察'));

evaluate("go('home'); expanded = false;");
await evaluate("send('下午两点到四点临时开会，帮我调整')");

const chat = JSON.parse(evaluate('JSON.stringify(chats["home"] || [])'));
check('user message stored', chat[0] && chat[0].text.includes('临时开会'));
check(
  'assistant text streamed',
  chat[1] && chat[1].text.includes('16:30'),
  chat[1] && chat[1].text,
);
check('proposal attached', !!(chat[1] && chat[1].proposal));
check(
  'proposal has two changes',
  chat[1] && chat[1].proposal && chat[1].proposal.changes.length === 2,
);

const withCard = evaluate("ai('home')");
check('proposal card rendered', withCard.includes('class="proposal"'));
check('apply button rendered', withCard.includes('data-agent-apply'));
check('summary rendered', withCard.includes('把英语练习挪到 16:30'));

const applyIndex = withCard.indexOf('data-agent-apply="');
const applyId = withCard.slice(applyIndex + 18).split('"')[0];
listeners.click({
  preventDefault() {},
  target: {
    hasAttribute(name) {
      return name === 'data-agent-apply';
    },
    getAttribute(name) {
      return name === 'data-agent-apply' ? applyId : null;
    },
    closest() {
      return this;
    },
  },
});

const schedule = JSON.parse(
  evaluate('JSON.stringify((data.schedule || []).map(e => [e.title, e.start, e.note]))')
);
const moved = schedule.find((row) => row[0] === '英语面试 · 自我介绍');
check('event moved to 16:30', moved && moved[1] === 16.5, JSON.stringify(moved));
check('meeting added', !!schedule.find((row) => row[0] === '临时会议'));
check(
  'note recalculated',
  moved && moved[2] === '16:30–17:00',
  moved && moved[2],
);

const afterApply = evaluate("ai('home')");
check('card marked applied', afterApply.includes('已应用到你今天的计划'));

await evaluate('generate()');
const report = evaluate('(data.reviews["2026-10-31"] || {}).report || ""');
check('report stored', report.includes('记录到的事实'), report);

const failed = checks.filter((item) => !item.ok);
console.log(
  `\n${checks.length - failed.length}/${checks.length} checks passed`,
);
if (failed.length) process.exitCode = 1;
