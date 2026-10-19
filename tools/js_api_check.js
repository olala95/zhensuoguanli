/**
 * 前端 api() 行为检查（Node 执行真实代码，不是静态正则）。
 *
 * 背景：浏览器 fetch 规定 GET/HEAD 不能带请求体，带了一律抛
 *   "Failed to execute 'fetch' on 'Window':
 *    Request with GET/HEAD method cannot have body"
 * 这类错误只有在浏览器里点才会暴露，Python 探针测不到，
 * 所以这里把 web/app.js 里的 api() 抠出来、用**会模拟该限制的 fetch** 真跑一遍。
 *
 * 用法：node tools/js_api_check.js
 */

const fs = require('fs');
const path = require('path');

const APP = path.join(__dirname, '..', 'web', 'app.js');
const src = fs.readFileSync(APP, 'utf8');

// 抠出 api() 函数本体（从 "async function api(" 到与之配对的 "}"）
const start = src.search(/async function api\s*\(/);
if (start < 0) {
  console.error('FAIL  在 web/app.js 里找不到 api() 函数');
  process.exit(1);
}
let depth = 0, i = src.indexOf('{', start), end = -1;
for (let j = i; j < src.length; j++) {
  const ch = src[j];
  if (ch === '{') depth++;
  else if (ch === '}') { depth--; if (depth === 0) { end = j + 1; break; } }
}
if (end < 0) { console.error('FAIL  无法解析 api() 函数体'); process.exit(1); }
const fnSrc = src.slice(start, end);

// 和浏览器一样：GET/HEAD 带 body 直接抛
const calls = [];
async function fakeFetch(url, opt) {
  calls.push({ url, opt });
  const m = (opt.method || 'GET').toUpperCase();
  if ((m === 'GET' || m === 'HEAD') && opt.body !== undefined && opt.body !== null) {
    throw new TypeError(
      "Failed to execute 'fetch' on 'Window': Request with GET/HEAD method cannot have body");
  }
  return { json: async () => ({ ok: true, data: {} }) };
}

const factory = new Function('S', 'fetch', 'localStorage', 'render', 'esc',
  fnSrc + '\nreturn api;');
const api = factory({ token: 'T' }, fakeFetch,
  { removeItem() {} }, function () {}, function (s) { return String(s); });

let failed = 0;
function check(name, cond, extra) {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? '  ' + extra : ''));
  if (!cond) failed++;
}

(async () => {
  console.log('== 前端 api() 行为检查（真实执行）==');

  // 1) 显式 GET + 参数 → 必须走 query string、且不带 body（否则浏览器直接抛）
  calls.length = 0;
  let err = null;
  try { await api('/api/visit/queue', { date: '2026-10-16' }, 'GET'); }
  catch (e) { err = e; }
  check('GET 带参数不抛 fetch 错误', err === null, err ? err.message : '');
  check('GET 不带请求体', calls[0] && calls[0].opt.body === undefined);
  check('GET 参数进了 query string',
    calls[0] && calls[0].url.indexOf('/api/visit/queue?date=2026-10-16') === 0,
    calls[0] ? calls[0].url : '');

  // 2) GET 多个参数
  calls.length = 0;
  err = null;
  try { await api('/api/record/get', { id: 12, limit: 20 }, 'GET'); }
  catch (e) { err = e; }
  check('GET 多参数不抛 fetch 错误', err === null, err ? err.message : '');
  check('GET 多参数拼接正确',
    calls[0] && calls[0].url === '/api/record/get?id=12&limit=20',
    calls[0] ? calls[0].url : '');

  // 3) GET 无参数 → 不该拼出空的 "?"
  calls.length = 0;
  err = null;
  try { await api('/api/system/status', null, 'GET'); }
  catch (e) { err = e; }
  check('GET 无参数不抛错', err === null, err ? err.message : '');
  check('GET 无参数不拼空问号',
    calls[0] && calls[0].url === '/api/system/status', calls[0] ? calls[0].url : '');

  // 4) GET 传空对象 → 不该拼出 "?"
  calls.length = 0;
  err = null;
  try { await api('/api/lab/agency/list', {}, 'GET'); }
  catch (e) { err = e; }
  check('GET 空对象不抛错且不带 ?', err === null && calls[0].url === '/api/lab/agency/list',
    calls[0] ? calls[0].url : String(err));

  // 5) 空值参数应被过滤（否则后端可能收到 id= 空串）
  calls.length = 0;
  await api('/api/patient/get', { id: 5, keyword: '', limit: null }, 'GET');
  check('GET 过滤空值参数',
    calls[0] && calls[0].url === '/api/patient/get?id=5', calls[0] ? calls[0].url : '');

  // 6) POST 必须带 body 且 method 为 POST
  calls.length = 0;
  await api('/api/prescription/create', { visit_id: 3 });
  check('POST（默认）带请求体',
    calls[0] && calls[0].opt.method === 'POST' && calls[0].opt.body === '{"visit_id":3}',
    calls[0] ? calls[0].opt.body : '');

  // 7) 显式 POST
  calls.length = 0;
  await api('/api/print/job/retry', { job_id: 7 }, 'POST');
  check('显式 POST 带请求体',
    calls[0] && calls[0].opt.method === 'POST' && calls[0].opt.body === '{"job_id":7}');

  // 8) 已在 path 里带 ? 的 GET 要用 & 拼接
  calls.length = 0;
  await api('/api/x?a=1', { b: 2 }, 'GET');
  check('已有 query 时用 & 拼接',
    calls[0] && calls[0].url === '/api/x?a=1&b=2', calls[0] ? calls[0].url : '');

  // 9) 全量扫一遍 app.js：GET 调用点应当都显式写了 'GET'（与后端注册方法一致由 api_audit 保证）
  const getCalls = (src.match(/api\(\s*['"][^'"]+['"]\s*,\s*[^)]*,\s*['"]GET['"]\s*\)/g) || []).length;
  check('存在显式 GET 调用点', getCalls > 0, getCalls + ' 处');

  // 10) 反向检查：api() 实现里 GET 分支不得直接赋 opt.body
  const getBranch = fnSrc.slice(fnSrc.indexOf("=== 'GET'"));
  check('GET 分支内没有直接赋 opt.body',
    !/opt\.body\s*=/.test(getBranch.slice(0, getBranch.indexOf('} else'))));

  console.log('\n结果：' + (failed ? failed + ' 项失败' : '全部通过'));
  process.exit(failed ? 1 : 0);
})();
