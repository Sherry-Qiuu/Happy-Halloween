/**
 * Dependency-free smoke test for web/auth.js.
 *
 * The DSH sandbox cannot start a Chromium process, so this harness runs
 * the real auth.js in a Node VM with a tiny DOM stub. It covers the paths
 * that are easiest to break: boot -> 401 -> login form, successful login,
 * and the account dialog. It does not cover layout or real browser input.
 *
 * Run with: node tests/auth_smoke.mjs
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createContext, runInContext } from 'node:vm';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const authSource = readFileSync(join(root, 'web', 'auth.js'), 'utf8');

const checks = [];
function check(name, condition, detail = '') {
  checks.push({ name, ok: !!condition, detail });
  const flag = condition ? 'ok  ' : 'FAIL';
  console.log(`  ${flag} ${name}${condition ? '' : ` -> ${detail}`}`);
}

// ---------------------------------------------------------------------
// Minimal DOM
// ---------------------------------------------------------------------
const elements = new Map();

function classList() {
  return {
    add() {},
    remove() {},
    toggle() {},
    contains() {
      return false;
    }
  };
}

function makeElement(id = '') {
  const element = {
    id,
    innerHTML: '',
    textContent: '',
    value: '',
    hidden: false,
    disabled: false,
    dataset: {},
    style: {},
    listeners: {},
    classList: classList(),
    addEventListener(type, handler) {
      (this.listeners[type] ||= []).push(handler);
    },
    removeEventListener() {},
    appendChild() {},
    remove() {},
    focus() {},
    close() {},
    showModal() {},
    setAttribute() {},
    getAttribute() {
      return null;
    },
    querySelector(selector) {
      return elementFor(selector);
    },
    querySelectorAll(selector) {
      return elements.get(`all:${selector}`) || [];
    }
  };
  if (id) elements.set(id, element);
  return element;
}

function elementFor(selector) {
  if (!elements.has(selector)) {
    elements.set(selector, makeElement(''));
  }
  return elements.get(selector);
}

const form = elementFor('#authForm');
const submitButton = makeElement('');
submitButton.textContent = '登录';
form.querySelector = (selector) =>
  selector === 'button[type="submit"]' ? submitButton : elementFor(selector);

elements.set('#authLocal', makeElement(''));
elements.set('#authNote', makeElement(''));
elements.set('#authError', makeElement(''));
elements.set('#authLead', makeElement(''));
elements.set('#authForm', form);

const tabs = [makeElement(''), makeElement('')];
tabs[0].getAttribute = () => 'login';
tabs[1].getAttribute = () => 'register';
elements.set('all:[data-auth-tab]', tabs);
const usernameInput = makeElement('');
elements.set('input[name="username"]', usernameInput);

const body = makeElement('body');
const overlay = makeElement('xj-auth');

const documentStub = {
  readyState: 'loading',
  body,
  listeners: {},
  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  },
  createElement() {
    elements.set('#xj-auth', overlay);
    return overlay;
  },
  getElementById(id) {
    if (id === 'xj-auth' && !elements.has('#xj-auth')) return null;
    return elementFor(`#${id}`);
  },
  querySelector(selector) {
    return elementFor(selector);
  },
  querySelectorAll(selector) {
    return elements.get(`all:${selector}`) || [];
  }
};

const storage = new Map();
const localStorageStub = {
  getItem(key) {
    return storage.has(key) ? storage.get(key) : null;
  },
  setItem(key, value) {
    storage.set(key, String(value));
  },
  removeItem(key) {
    storage.delete(key);
  }
};

const locationStub = {
  pathname: '/',
  search: '',
  origin: 'http://127.0.0.1:8010',
  replaced: '',
  replace(target) {
    this.replaced = target;
  }
};

const historyStub = { replaceState() {} };
const toasts = [];
let modalHtml = '';
let renderCalls = 0;

const user = {
  id: 'u_0123456789abcdef',
  username: 'alice',
  display_name: 'Alice',
  created_at: '2026-10-31T10:00:00+00:00'
};

const responses = {
  me: { status: 401, body: { error: '请先登录。', code: 'unauthenticated' } },
  login: { status: 200, body: { user } },
  data: { status: 200, body: { data: null, updated_at: null } },
  save: { status: 200, body: { updated_at: '2026-10-31T10:01:00+00:00' } }
};

function fakeResponse(spec) {
  return {
    ok: spec.status >= 200 && spec.status < 300,
    status: spec.status,
    async text() {
      return JSON.stringify(spec.body);
    }
  };
}

async function fetchStub(url) {
  const path = String(url);
  if (path.endsWith('/api/auth/me')) return fakeResponse(responses.me);
  if (path.endsWith('/api/auth/login')) return fakeResponse(responses.login);
  if (path.endsWith('/api/data')) return fakeResponse(responses.data);
  throw new Error(`unexpected fetch: ${path}`);
}

class FakeFormData {
  constructor() {
    this.fields = { username: 'alice', password: 'correct-horse' };
  }
  get(name) {
    return Object.prototype.hasOwnProperty.call(this.fields, name)
      ? this.fields[name]
      : null;
  }
}

const sandbox = {
  console,
  document: documentStub,
  localStorage: localStorageStub,
  location: locationStub,
  history: historyStub,
  fetch: fetchStub,
  FormData: FakeFormData,
  URLSearchParams,
  AbortController,
  structuredClone,
  setTimeout,
  clearTimeout,
  seed: {
    tasks: [],
    goals: [],
    reviews: {},
    moved: false,
    extra: [],
    name: '明',
    reminder: false
  },
  data: {
    tasks: [],
    goals: [],
    reviews: {},
    moved: false,
    extra: [],
    name: '明',
    reminder: false
  },
  page: 'home',
  render() {
    renderCalls += 1;
  },
  show(html) {
    modalHtml = html;
  },
  toast(message) {
    toasts.push(message);
  },
  esc(value) {
    return String(value);
  },
  confirm() {
    return true;
  },
  addEventListener() {},
  removeEventListener() {}
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const context = createContext(sandbox);
runInContext(authSource, context, { filename: 'auth.js' });

console.log('auth.js boot');
const boot = documentStub.listeners.DOMContentLoaded[0];
if (!boot) throw new Error('DOMContentLoaded handler was not registered');
await boot();
await new Promise((resolve) => setTimeout(resolve, 30));

check(
  'unauthorized shows the login surface',
  overlay.innerHTML.includes('id="authForm"'),
  overlay.innerHTML.slice(0, 120)
);
check('body is locked', body.classList !== null);
check('user is still anonymous', sandbox.XJ.getUser() === null);
check(
  'login form has a username field',
  form.innerHTML.includes('name="username"'),
  form.innerHTML.slice(0, 120)
);

console.log('auth.js login');
const submit = form.listeners.submit[0];
if (!submit) throw new Error('submit handler was not registered');
await submit({ preventDefault() {}, currentTarget: form });
await new Promise((resolve) => setTimeout(resolve, 20));

check('login posts and redirects', locationStub.replaced.includes('signed=1'));
check('current user exposed', sandbox.XJ.getUser().username === 'alice');
check(
  'scope is stored',
  localStorageStub.getItem('xingjian-scope') === user.id
);
check(
  'user cache is scoped',
  sandbox.XJ.dataKey() === `xingjian-v02:${user.id}`
);

console.log('account dialog');
sandbox.XJ.openAccount();
check('dialog opens', modalHtml.includes('修改密码'));
check('dialog has delete', modalHtml.includes('删除账号'));
check('dialog has profile', modalHtml.includes('显示名称'));

const failed = checks.filter((item) => !item.ok);
console.log(
  `\n${checks.length - failed.length}/${checks.length} checks passed`
);
if (failed.length) process.exitCode = 1;
