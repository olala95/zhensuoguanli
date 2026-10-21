/* 诊所管理系统 · 前端（原生 JS，无框架）
 * 接口约定见 src/core/api.py：{ ok:true, data } / { ok:false, error:{code,message,retryable} }
 */
'use strict';

const S = {
  token: localStorage.getItem('token') || '',
  user: JSON.parse(localStorage.getItem('user') || 'null'),
  sys: null,
  route: null,
};

/* ---------- 基础工具 ---------- */
function $(sel) { return document.querySelector(sel); }
function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function toast(msg, kind) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show ' + (kind || '');
  setTimeout(() => { el.className = 'toast'; }, 2600);
}
function modal(title, html) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = html;
  $('#modal').style.display = 'flex';
}
function closeModal() { $('#modal').style.display = 'none'; }

async function api(path, body, method) {
  // GET/HEAD 绝对不能带请求体——fetch 会直接抛
  // "Request with GET/HEAD method cannot have body"，参数只能走 query string
  const m = method || (body ? 'POST' : 'GET');
  const opt = {
    method: m,
    headers: { 'Content-Type': 'application/json', 'X-Token': S.token },
  };
  let url = path;
  if (m === 'GET') {
    if (body) {
      const qs = Object.keys(body)
        .filter(k => body[k] !== undefined && body[k] !== null && body[k] !== '')
        .map(k => encodeURIComponent(k) + '=' + encodeURIComponent(body[k]))
        .join('&');
      if (qs) url += (url.includes('?') ? '&' : '?') + qs;
    }
  } else if (body !== undefined && body !== null) {
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(url, opt);
  const json = await res.json();
  if (!json.ok) {
    const e = json.error || {};
    if (e.code === 'E6001' || e.code === 'E6005') { S.token = ''; localStorage.removeItem('token'); render(); }
    throw new Error((e.code ? e.code + ' ' : '') + (e.message || '请求失败'));
  }
  return json.data;
}
function go(hash) { location.hash = hash; }
function fmtTime(t) { return t ? String(t).slice(5, 16) : '-'; }
function statusTag(s) {
  const map = {
    // 就诊 / 病历
    WAITING: ['muted', '候诊'], IN_PROGRESS: ['ok', '接诊中'], SUSPENDED: ['warn', '挂单'],
    COMPLETED: ['ok', '已完成'], VOID: ['danger', '已作废'],
    DRAFT: ['muted', '草稿'], CONFIRMED: ['ok', '已确认'], ARCHIVED: ['ok', '已归档'],
    SEALED: ['warn', '已封存'], UNSEALED: ['danger', '已解封'],
    CLOSED: ['ok', '已结案'],
    // 检验
    ORDERED: ['warn', '已开单'], SAMPLED: ['warn', '已采样'], DELIVERED: ['warn', '已送检'],
    REPORTED: ['ok', '已回报'], REVIEWED: ['ok', '已阅片'], CANCELLED: ['muted', '已取消'],
    // 处方
    PENDING: ['warn', '待确认'], DISPENSED: ['ok', '已发药'],
    PARTIAL_RETURNED: ['warn', '部分退药'], FULLY_RETURNED: ['danger', '已退药'],
    REJECTED: ['danger', '已驳回'],
    // 账单 / 收款
    UNPAID: ['warn', '待收费'], PAID: ['ok', '已收费'],
    PARTIAL_REFUNDED: ['warn', '部分退款'], FULLY_REFUNDED: ['danger', '已全退'],
    // 打印
    SUCCESS: ['ok', '成功'], FAILED: ['danger', '失败'], RETRYING: ['warn', '重试中'],
    ABANDONED: ['danger', '已放弃'], PRINTING: ['warn', '打印中'],
    // 处置
    EXECUTING: ['ok', '执行中'], FINISHED: ['ok', '已完成'], INTERRUPTED: ['warn', '已中断'],
    // 通用
    OPEN: ['warn', '待处理'], RESOLVED: ['ok', '已处理'], IGNORED: ['muted', '已忽略'],
  };
  const m = map[s] || ['muted', s];
  return `<span class="tag ${m[0]}">${m[1]}</span>`;
}

/* ---------- 初始化 / 登录 ---------- */
async function loadStatus() {
  try { S.sys = await api('/api/system/status'); } catch (e) { S.sys = null; }
}

function viewInit() {
  return `<div class="login-wrap"><div class="card">
    <h2>系统初始化</h2>
    <div class="row"><div style="flex:1"><label>诊所名称</label><input id="clinic" value="我的中医诊所"></div></div>
    <div class="row"><div style="flex:1"><label>管理员账号</label><input id="u" value="admin"></div></div>
    <div class="row"><div style="flex:1"><label>姓名</label><input id="n" value="管理员"></div></div>
    <div class="row"><div style="flex:1"><label>口令（≥8 位，不可纯数字）</label><input id="p" type="password"></div></div>
    <div class="row"><button class="btn" onclick="doInit()">初始化并进入</button></div>
    <div class="hint">初始化只会执行一次，之后引导页不再出现。</div>
  </div></div>`;
}
async function doInit() {
  try {
    await api('/api/system/init', {
      clinic_name: $('#clinic').value, admin_username: $('#u').value,
      admin_name: $('#n').value, admin_password: $('#p').value,
    });
    toast('初始化完成，请登录', 'ok');
    await loadStatus(); render();
  } catch (e) { toast(e.message, 'err'); }
}

function viewLogin() {
  const roles = (S.user && S.user.roles) || null;
  return `<div class="login-wrap"><div class="card">
    <h2>${esc((S.sys && S.sys.clinic_name) || '中医诊所')} 门诊系统</h2>
    <div class="row"><div style="flex:1"><label>用户名</label><input id="u" autofocus></div></div>
    <div class="row"><div style="flex:1"><label>口令</label><input id="p" type="password"></div></div>
    <div class="row"><div style="flex:1"><label>本次以什么身份操作（仅影响审计记录）</label>
      <select id="r"><option value="DOCTOR">医生</option><option value="PHARMACIST">药师</option>
      <option value="CASHIER">收费员</option><option value="ADMIN">管理员</option></select></div></div>
    <div class="row"><button class="btn" style="flex:1" onclick="doLogin()">登录</button></div>
    <div class="hint">单机单用户场景下一人可兼任多角色。<b>能做什么由账号被授予的全部角色决定</b>，
      这里选的只是审计里记录的"以什么身份操作"，不会限制功能。</div>
  </div></div>`;
}
async function doLogin() {
  try {
    const d = await api('/api/auth/login', {
      username: $('#u').value, password: $('#p').value, role_code: $('#r').value,
    });
    S.token = d.token; localStorage.setItem('token', d.token);
    S.user = d; localStorage.setItem('user', JSON.stringify(d));
    toast('登录成功', 'ok');
    go('#/workbench');
  } catch (e) { toast(e.message, 'err'); }
}
function doLogout() {
  api('/api/auth/logout', { token: S.token }).catch(() => {});
  S.token = ''; S.user = null;
  localStorage.removeItem('token'); localStorage.removeItem('user');
  go('#/login');
}

/* ---------- 工作台（候诊队列 + 患者） ---------- */
async function viewWorkbench() {
  let q;
  try {
    q = await api('/api/visit/queue', { date: today() }, 'GET');
  } catch (e) {
    // 候诊队列读不出来时，工作台整个页面会挂。宁可显示空队列 + 明确原因，
    // 也不要让用户面对一个"加载失败"却不知道为什么。
    const items = (q && q.items) || [];
    return renderWorkbench(items, '<div class="err">候诊队列读取失败：' + esc(e.message)
      + '</div>');
  }
  return renderWorkbench(q.items || [], '');
}

let WB = { filter: 'ACTIVE', items: [] };

function wbFilter(list) {
  if (WB.filter === 'ACTIVE') {
    return list.filter(v => ['WAITING', 'IN_PROGRESS', 'SUSPENDED'].includes(v.visit_status));
  }
  if (WB.filter === 'DONE') return list.filter(v => v.visit_status === 'COMPLETED');
  return list;
}

function wbSetFilter(f) { WB.filter = f; renderWorkbench(WB.items, ''); }

function renderWorkbench(items, errHtml) {
  WB.items = items || [];
  const active = WB.items.filter(v => ['WAITING', 'IN_PROGRESS', 'SUSPENDED'].includes(v.visit_status)).length;
  const done = WB.items.filter(v => v.visit_status === 'COMPLETED').length;
  const shown = wbFilter(WB.items).slice(0, 150);
  const rows = shown.map(v => `<tr>
    <td>${v.queue_no}</td>
    <td>${esc(v.patient_name)} ${MODE.chip(v)} <span class="hint">${v.gender === 'F' ? '女' : v.gender === 'M' ? '男' : ''}${v.birth_date ? ' · ' + v.birth_date : ''}</span></td>
    <td>${esc(v.patient_no)}</td>
    <td>${v.is_first_visit ? '<span class="tag muted">初诊</span>' : '<span class="tag muted">复诊</span>'}</td>
    <td>${statusTag(v.visit_status)}</td>
    <td>${fmtTime(v.registered_at)}</td>
    <td>
      ${v.visit_status === 'WAITING'
        ? `<button class="btn sm" onclick="startVisit(${v.id})">接诊</button>` : ''}
      ${v.visit_status === 'IN_PROGRESS' || v.visit_status === 'SUSPENDED'
        ? `<button class="btn sm" onclick="go('#/record/${v.id}')">写病历</button>
           <button class="btn sm ghost" onclick="go('#/prescribe/${v.id}')">开方</button>
           <button class="btn sm ghost" onclick="go('#/lab/${v.id}')">检验</button>
           <button class="btn sm ghost" onclick="go('#/treatment/${v.id}')">处置</button>
           <button class="btn sm ghost" onclick="go('#/billing/${v.id}')">收费</button>
           <button class="btn sm ghost" onclick="go('#/print/${v.id}')">打印</button>
           <button class="btn sm ghost" onclick="completeVisit(${v.id})">完成</button>` : ''}
      ${v.visit_status === 'COMPLETED'
        ? `<button class="btn sm ghost" onclick="go('#/record/${v.id}')">查看病历</button>
           <button class="btn sm ghost" onclick="go('#/billing/${v.id}')">收费/退款</button>` : ''}
      ${v.visit_status === 'VOID' ? '<span class="hint">已作废</span>' : ''}
    </td>
  </tr>`).join('');
  const tab = (k, label, n) => `<button class="btn sm ${WB.filter === k ? '' : 'ghost'}"
      onclick="wbSetFilter('${k}')">${label}（${n}）</button>`;
  return `
  <div class="card">
    <h3>候诊队列 · ${today()}</h3>
    <div class="row">
      <input id="kw" placeholder="搜索患者：姓名 / 患者号 / 手机号 / 身份证" style="flex:1" onkeydown="if(event.key==='Enter')searchPatient()">
      <button class="btn ghost" onclick="searchPatient()">搜索</button>
      <button class="btn" onclick="showNewPatient()">+ 新建患者</button>
    </div>
    <div id="searchResult"></div>
  </div>
  <div class="card">
    <h3>今日就诊</h3>
    ${errHtml}
    <div class="row">
      ${tab('ACTIVE', '待处理', active)}
      ${tab('DONE', '已完成', done)}
      ${tab('ALL', '全部', WB.items.length)}
      <span class="spacer"></span>
      <span class="hint">默认只看还没结束的就诊；历史记录切「全部」</span>
    </div>
    <table><thead><tr><th>号</th><th>患者</th><th>患者号</th><th>诊别</th><th>状态</th><th>登记时间</th><th>操作</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="empty">当前筛选下暂无就诊</td></tr>'}</tbody></table>
    ${wbFilter(WB.items).length > 150 ? '<div class="hint">仅显示最近 150 条，完整记录请用上方搜索或经营统计查询。</div>' : ''}
    <div class="hint" style="margin-top:6px">
      接诊后每一个按钮都会自动带入本次就诊，<b>不需要记 ID</b>。
    </div>
  </div>`;
}
function today() { return new Date().toISOString().slice(0, 10); }

/* ============================================================
   通用选择器
   原则：界面上的每个动作都必须能从"看得见的东西"点出来，
   绝不让操作员手填 visit_id / record_id / 处方 ID 这类内部主键。
   ============================================================ */
const PICK = { visits: [], at: 0, loading: false };
const VST_LABEL = {
  WAITING: '候诊', IN_PROGRESS: '接诊中', SUSPENDED: '挂单',
  COMPLETED: '已完成', VOID: '已作废',
};

async function loadVisitChoices(force) {
  if (!force && PICK.visits.length && Date.now() - PICK.at < 15000) return PICK.visits;
  if (PICK.loading) return PICK.visits;
  PICK.loading = true;
  try {
    const reqs = [
      api('/api/visit/list', { date: today(), limit: 200 }, 'GET'),
      api('/api/visit/list', { status: 'IN_PROGRESS', limit: 100 }, 'GET'),
      api('/api/visit/list', { status: 'SUSPENDED', limit: 50 }, 'GET'),
    ];
    const rs = await Promise.all(reqs.map(p => p.catch(() => ({ items: [] }))));
    const map = new Map();
    rs.forEach(r => (r.items || []).forEach(v => map.set(v.id, v)));
    // 只保留最近 30 天的就诊：超过一个月的"接诊中/挂单"属于异常遗留，
    // 不应该长期堆在下拉里影响日常选择（可在工作台按状态筛选后处理）。
    const cutoff = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
    PICK.visits = Array.from(map.values())
      .filter(v => !v.registered_at || String(v.registered_at).slice(0, 10) >= cutoff)
      .sort((a, b) => b.id - a.id)
      .slice(0, 80);
    PICK.at = Date.now();
  } catch (e) {
    PICK.visits = [];
  } finally {
    PICK.loading = false;
  }
  return PICK.visits;
}

/* 就诊下拉：只列出"今天"和"还没结束"的就诊，避免历史数据淹没当前工作 */
function visitSelectHtml(id, opts) {
  opts = opts || {};
  const list = (PICK.visits || []).filter(v => !opts.status || opts.status.indexOf(v.status) >= 0);
  const cur = opts.selected ? String(opts.selected) : '';
  const head = `<option value="">${opts.placeholder || '— 选择就诊 —'}</option>`;
  const body = list.map(v => {
    const d = String(v.registered_at || '').slice(5, 10);
    const label = [d, v.visit_no, v.patient_name, VST_LABEL[v.status] || v.status]
      .filter(Boolean).join(' · ');
    return `<option value="${v.id}"${String(v.id) === cur ? ' selected' : ''}>${esc(label)}</option>`;
  }).join('');
  const empty = list.length ? '' : '<span class="hint">今天还没有就诊记录，请到「工作台」挂号</span>';
  return `<select id="${id}" class="pick"${opts.onchange ? ` onchange="${opts.onchange}"` : ''}>${head}${body}</select>${empty}`;
}

/* 处方下拉：按就诊联动，用于发药 / 打印 / 采纳建议 */
function rxSelectHtml(id, opts) {
  opts = opts || {};
  return `<select id="${id}" class="pick"${opts.onchange ? ` onchange="${opts.onchange}"` : ''}>
    <option value="">— 先选就诊 —</option></select>`;
}

/* 处方类型归到哪一套流程：饮片/颗粒 = 中医，成药 = 西医 */
function rxKindOf(t) { return (t === 'HERB' || t === 'GRANULE') ? 'TCM' : 'WEST'; }

async function fillRxSelect(selId, visitId, favorKind) {
  const el = document.getElementById(selId);
  if (!el) return;
  el.innerHTML = '<option value="">加载中…</option>';
  if (!visitId) { el.innerHTML = '<option value="">— 先选就诊 —</option>'; return; }
  try {
    let items = (await api('/api/prescription/list', { visit_id: visitId }, 'GET')).items || [];
    if (!items.length) { el.innerHTML = '<option value="">该就诊没有处方</option>'; return; }
    // 中药房只管饮片/颗粒，西药房只管成药：本流程有对应处方时只列它们，
    // 没有就退回显示全部（不给"发药发不了"的死局）
    if (favorKind) {
      const same = items.filter(r => rxKindOf(r.prescription_type) === favorKind);
      if (same.length) items = same;
    }
    // 自动选中可发药的那一张（优先已确认未发药的），省掉「展开下拉再挑」这一步
    const pick = items.find(r => r.status === 'CONFIRMED')
      || items.find(r => ['DISPENSED', 'PARTIAL_RETURNED'].indexOf(r.status) >= 0)
      || items[0];
    el.innerHTML = items.map(r => {
      const label = [r.prescription_no, r.prescription_type === 'HERB' ? '中药' : '成药',
        r.status, yuan(r.total_amount) + '元'].filter(Boolean).join(' · ');
      return `<option value="${r.id}"${r.id === pick.id ? ' selected' : ''}>${esc(label)}</option>`;
    }).join('');
  } catch (e) {
    el.innerHTML = '<option value="">读取失败</option>';
  }
}

/* 账单下拉：按就诊联动 */
async function fillBillSelect(selId, visitId) {
  const el = document.getElementById(selId);
  if (!el) return;
  if (!visitId) { el.innerHTML = '<option value="">— 先选就诊 —</option>'; return; }
  el.innerHTML = '<option value="">加载中…</option>';
  try {
    const items = (await api('/api/finance/bills', { visit_id: visitId, limit: 20 }, 'GET')).items || [];
    if (!items.length) { el.innerHTML = '<option value="">该就诊没有账单</option>'; return; }
    // 自动选中待收的那张（否则下拉停在空值，从工作台点「收费」进来还要再选一次）
    const pick = items.find(b => b.status === 'PENDING' || b.status === 'PARTIAL_PAID') || items[0];
    el.innerHTML = '<option value="">— 选择账单 —</option>' + items.map(b => {
      const label = [b.bill_no, b.status, yuan(b.total_amount_fen) + '元'].filter(Boolean).join(' · ');
      return `<option value="${b.id}"${b.id === pick.id ? ' selected' : ''}>${esc(label)}</option>`;
    }).join('');
  } catch (e) {
    el.innerHTML = '<option value="">读取失败</option>';
  }
}


async function searchPatient() {
  const kw = $('#kw').value.trim();
  if (!kw) { toast('请输入关键词'); return; }
  try {
    await doSearchPatient(kw);
  } catch (e) { toast('搜索失败：' + e.message, 'err'); }
}
async function doSearchPatient(kw) {
  const d = await api('/api/patient/search', { keyword: kw, limit: 20 }, 'GET');
  const rows = d.items.map(p => `<tr>
    <td>${esc(p.name)}</td><td>${p.gender === 'F' ? '女' : '男'}</td><td>${esc(p.birth_date || '')}</td>
    <td>${esc(p.phone || '')}</td><td>${esc(p.patient_no)}</td><td>${esc(p.id_card_no || '')}</td>
    <td><button class="btn sm" onclick="newVisit(${p.id})">建${MODE.def === 'WEST' ? '西医' : '中医'}就诊</button>
        <button class="btn sm ghost" onclick="showPatient(${p.id})">资料</button></td>
  </tr>`).join('');
  $('#searchResult').innerHTML = d.items.length
    ? `<table style="margin-top:8px"><thead><tr><th>姓名</th><th>性别</th><th>出生日期</th><th>手机号</th><th>患者号</th><th>身份证</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<div class="empty">未找到患者</div>';
}

function showNewPatient() {
  modal('新建患者', `
    <div class="grid3">
      <div><label>姓名 *</label><input id="name"></div>
      <div><label>性别</label><select id="gender"><option value="M">男</option><option value="F">女</option><option value="U">未知</option></select></div>
      <div><label>出生日期</label><input id="birth" placeholder="YYYY-MM-DD"></div>
      <div><label>手机号</label><input id="phone"></div>
      <div><label>身份证号</label><input id="idno" placeholder="18 位，系统会校验"></div>
      <div><label>地址</label><input id="addr"></div>
    </div>
    <div class="row" style="margin-top:12px"><label style="flex:1">备注<textarea id="note"></textarea></label></div>
    <div class="row"><button class="btn" onclick="createPatient()">建档</button></div>`);
}
async function createPatient() {
  try {
    const p = await api('/api/patient/create', {
      name: $('#name').value, gender: $('#gender').value, birth_date: $('#birth').value,
      phone: $('#phone').value, id_card_no: $('#idno').value, address: $('#addr').value,
      note: $('#note').value,
    });
    toast('建档成功：' + p.patient_no, 'ok');
    closeModal();
    await newVisit(p.id);
  } catch (e) { toast(e.message, 'err'); }
}
async function showPatient(id) {
  let p;
  try {
    p = await api('/api/patient/get', { id: id }, 'GET');
  } catch (e) {
    toast('读取患者失败：' + e.message, 'err');
    return;
  }
  modal('患者资料 · ' + p.patient_no, `<div class="kv">
    <div class="k">姓名</div><div>${esc(p.name)}（${p.gender === 'F' ? '女' : '男'}）</div>
    <div class="k">出生日期</div><div>${esc(p.birth_date || '')} ${p.age_text || ''}</div>
    <div class="k">手机号</div><div>${esc(p.phone || '')}</div>
    <div class="k">地址</div><div>${esc(p.address || '')}</div>
    <div class="k">证件</div><div>${(p.identifiers || []).map(i => esc(i.id_type) + ':' + esc(i.id_no)).join('，') || '-'}</div>
    <div class="k">就诊记录</div><div>${(p.visits || []).map(v => esc(v.visit_no) + ' ' + statusTag(v.status)).join('<br>') || '无'}</div>
  </div>
  <div class="row" style="margin-top:10px">
    <input id="pt_phone" placeholder="手机号" value="${esc(p.phone || '')}" style="width:150px">
    <input id="pt_addr" placeholder="地址" value="${esc(p.address || '')}" style="flex:1">
    <input id="pt_reason" placeholder="修改原因" style="width:160px">
    <button class="btn sm" onclick="ptUpdate(${id})">保存资料</button>
  </div>
  <div class="row" style="margin-top:6px">
    <button class="btn sm ghost" onclick="ptDuplicates()">查重（F04）</button>
    <button class="btn sm ghost" onclick="ptMerge(${id})">合并到另一患者</button>
    <button class="btn sm warn" onclick="ptDelete(${id})">删除患者</button>
  </div>
  <div class="hint" style="margin-top:6px">有有效就诊或处方的患者不允许删除；合并会把就诊/处方/账单等一并改挂。</div>`);
}
async function ptUpdate(id) {
  const reason = val('pt_reason') || '资料维护';
  try {
    await api('/api/patient/update', { id: id, phone: val('pt_phone'),
      address: val('pt_addr'), reason: reason });
    toast('资料已更新（变更已留痕）', 'ok'); closeModal(); render();
  } catch (e) { toast(e.message, 'err'); }
}
async function ptDuplicates() {
  try {
    const d = await api('/api/patient/duplicates', {}, 'GET');
    const items = d.items || d.groups || [];
    modal('疑似重复患者', items.length
      ? `<table class="tbl"><thead><tr><th>患者</th><th>同名/同手机</th></tr></thead><tbody>${
        items.map(g => `<tr><td>${(g.items || g.patients || []).map(p => esc(p.name) + ' ' + esc(p.patient_no)).join(' / ')}</td>
          <td>${esc(g.reason || (g.match_type || ''))}</td></tr>`).join('')}</tbody></table>`
      : '<div class="hint">未发现疑似重复</div>');
  } catch (e) { toast(e.message, 'err'); }
}
async function ptMerge(id) {
  // 后端要 primary_patient_id（保留方）/ merged_patient_id（被合并方）。
  // 语义必须问清：被合并方的数据会改挂到保留方，选反了就丢数据。
  const target = prompt('保留哪个患者？填**保留方**的患者 ID（被合并方的数据会改挂过来）：');
  if (!target) return;
  const reason = prompt('合并原因（必填）：');
  if (!reason) return;
  try {
    await api('/api/patient/merge', { primary_patient_id: parseInt(target),
      merged_patient_id: id, reason: reason });
    toast('已合并', 'ok'); closeModal(); render();
  } catch (e) { toast(e.message, 'err'); }
}
async function ptDelete(id) {
  const reason = prompt('删除原因（必填）：');
  if (!reason) return;
  try { await api('/api/patient/delete', { id: id, reason: reason }); toast('已删除', 'ok'); closeModal(); render(); }
  catch (e) {
    // patient.update 属一线权限，管理员角色刻意没有：提示用户切到医生角色
    toast(e.message + '（可切换到医生角色操作）', 'err');
  }
}
async function newVisit(patientId) {
  try {
    const v = await api('/api/visit/create',
      { patient_id: patientId, visit_type: MODE.def });
    toast('已建' + (MODE.def === 'WEST' ? '西医' : '中医') + '就诊 ' + v.visit_no, 'ok');
    render();
  } catch (e) { toast(e.message, 'err'); }
}
async function startVisit(id) {
  try {
    const r = await api('/api/visit/start', { visit_id: id });
    toast('已接诊，病历自动建好：v' + (r.record_id || ''), 'ok');
    go('#/record/' + id);
  } catch (e) {
    // 不捕获的话接口一报错就是未捕获的 Promise rejection —— 表现成"点了没反应"
    toast('接诊失败：' + e.message, 'err');
    render();
  }
}

/* ---------- 接诊工作台 / 病历 ---------- */
let LAST_RECORD_ID = 0;

/* 常用舌象 / 脉象 / 证型：点一下填进去，省掉中医门诊里最高频的那几次打字 */
const TONGUE_BODY = ['淡红', '淡白', '淡胖', '红', '绛', '紫暗', '有齿痕', '有瘀点'];
const TONGUE_COAT = ['薄白', '白腻', '白滑', '白厚', '黄腻', '黄厚', '少苔', '无苔', '剥苔'];
const PULSE_OPTS = ['浮', '沉', '迟', '数', '弦', '滑', '涩', '细', '弱', '缓', '结代', '洪'];
const SYNDROMES = ['气血两虚', '心脾两虚', '肝郁脾虚', '肝郁气滞', '脾虚湿盛', '脾胃虚寒',
  '肝肾阴虚', '肺脾气虚', '痰湿内阻', '气滞血瘀', '肝阳上亢', '湿热内蕴',
  '风寒束表', '风热犯肺', '肾阳不足', '心肾不交'];
const TREAT_PRINCIPLE = ['补气养血', '健脾益气', '疏肝健脾', '清热利湿', '温中散寒',
  '滋补肝肾', '活血化瘀', '化痰祛湿', '平肝潜阳', '益气固表', '养心安神', '疏风解表'];

function tagChips(inputId, list) {
  return `<div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px">${
    list.map(x => `<span class="btn sm ghost" style="margin:0" onclick="tagPick('${inputId}','${esc(x)}')">${esc(x)}</span>`).join('')
  }</div>`;
}
function tagPick(inputId, v) {
  const el = document.getElementById(inputId);
  if (!el || el.disabled) return;
  const cur = String(el.value || '').trim();
  if (cur.split(/[,，、;；]/).map(s => s.trim()).filter(Boolean).indexOf(v) >= 0) return;
  el.value = cur ? cur + '、' + v : v;
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

async function viewRecord(visitId) {
  let v, r;
  try {
    v = await api('/api/visit/get', { id: visitId }, 'GET');
  } catch (e) {
    throw new Error('读取就诊失败：' + e.message);
  }
  try {
    r = await api('/api/record/get', { visit_id: visitId }, 'GET');
  } catch (e) {
    throw new Error('读取病历失败：' + e.message);
  }
  // 必须严格判 === false：后端查到病历时返回 exists:true，只有"该就诊还没病历"才返回
  // {"exists":false}。写成 if (!r.exists) 会因字段缺失而永远成立，
  // 导致「建病历 → 递归本函数 → 再建病历」的死循环（点接诊后浏览器疯狂请求、看似没反应）。
  if (r && r.exists === false) {
    const c = await api('/api/record/create', { visit_id: visitId });
    r = await api('/api/record/get', { id: c.id }, 'GET');
  }
  const rid = r.id;
  // 记下当前病历 ID：归档区（arLoad）要的是 record_id，
  // 而路由里的 #/record/<id> 是 visit_id，两者不是一回事，
  // 混用会去查一个不存在的病历（控制台 404、归档信息空白）。
  LAST_RECORD_ID = rid;
  // 本次就诊走中医还是西医：决定默认展开哪一套字段（另一套收起来，点开仍可补录）
  const isTcm = MODE.isTcm(v);
  const cc = r.chief_complaint || {}, pi = r.present_illness || {}, tf = r.tcm_four || {};
  const vit = (r.vitals && r.vitals[0]) || {};
  const locked = r.status !== 'DRAFT';
  const dx = (r.diagnoses || []).map(d => `${d.is_primary ? '【主】' : ''}${d.diag_type === 'TCM' ? '<span class="tcm">中医</span> ' : ''}${esc(d.icd10_name || d.tcm_name)}${d.icd10_code ? ' (' + esc(d.icd10_code) + ')' : ''}`).join('；') || '未填写';
  const syn = (r.syndromes || []).map(s => `${esc(s.syndrome_name)}｜${esc(s.treatment_method || '')}`).join('；') || '未填写';
  const orders = (r.orders || []).map(o => esc(o.content)).join('；') || '无';
  const examRefs = r.exam_refs || [];
  const examRows = examRefs.map(x => {
    const kind = x.ref_type === 'POINT' ? '即时' : (x.ref_type === 'IMAGING' ? '影像' : '检验');
    const name = x.ref_type === 'POINT' ? `${x.exam_name || ''}` : (x.summary || '（引用）');
    const value = x.ref_type === 'POINT'
      ? `${x.exam_value || ''}${x.unit ? ' ' + x.unit : ''}`
      : (x.summary || '');
    const flag = x.source_changed ? ' class="warn"' : (x.expired ? ' class="err"' : '');
    return `<tr>
      <td>${kind}</td><td>${esc(name)}</td><td>${esc(value)}</td>
      <td${flag}>${esc(x.display_status || '')}</td>
      <td>${locked ? '' : `<button class="btn sm ghost" onclick="removeExamRef(${rid},${x.id})">移除</button>`}</td>
    </tr>`;
  }).join('');

  return `
  <div class="card">
    <h3>接诊 · ${esc(v.patient_name)} ${esc(v.patient_no)} · 就诊号 ${esc(v.visit_no)} ${MODE.chip(v)} ${statusTag(r.status)}</h3>
    <div class="row">
      <span class="hint">${v.is_first_visit ? '初诊' : '复诊'} · 登记 ${fmtTime(v.registered_at)}</span>
      <span class="hint">诊疗类型</span>
      <button class="btn sm ${isTcm ? '' : 'ghost'}"
        onclick="changeVisitType(${v.id},'TCM')">中医</button>
      <button class="btn sm ${isTcm ? 'ghost' : ''}"
        onclick="changeVisitType(${v.id},'WEST')">西医</button>
      <span class="spacer"></span>
      ${locked ? '<span class="tag warn">病历已确认，内容锁定</span>' : `<button class="btn ghost" onclick="saveAll(${rid})">保存</button>
        <button class="btn warn" onclick="confirmFlow(${rid})">确认签名（先自动保存）</button>`}
      <button class="btn ghost" onclick="suspendVisit(${v.id})">挂单/留观</button>
      ${v.status === 'SUSPENDED' ? `<button class="btn ghost" onclick="resumeVisit(${v.id})">恢复接诊</button>` : ''}
      <button class="btn ghost" onclick="completeVisit(${v.id})">完成就诊</button>
      <button class="btn ghost warn" onclick="voidVisit(${v.id})">作废就诊</button>
    </div>
  </div>

  <div class="card">
    <details>
    <summary class="section-title" style="cursor:pointer;display:list-item">既往史 / 个人史 / 家族史 / 过敏史（F12/F13）—— 没有可跳过</summary>
    <div class="row" style="margin-top:8px">
      <select id="hs_type" style="width:120px" ${locked ? 'disabled' : ''}>
        <option value="PAST">既往史</option><option value="PERSONAL">个人史</option>
        <option value="FAMILY">家族史</option></select>
      <input id="hs_disease" placeholder="疾病/事项" style="flex:1" ${locked ? 'disabled' : ''}>
      <input id="hs_at" placeholder="诊断时间" style="width:130px" ${locked ? 'disabled' : ''}>
      <input id="hs_treat" placeholder="治疗" style="width:140px" ${locked ? 'disabled' : ''}>
      <input id="hs_outcome" placeholder="转归" style="width:120px" ${locked ? 'disabled' : ''}>
      ${locked ? '' : `<button class="btn sm" onclick="addHistory(${rid})">添加</button>`}
    </div>
    <div class="hint">${(r.histories || []).map(h => esc(h.hist_type) + '：' + esc(h.disease || h.content || '')).join('；') || '无'}</div>

    <div class="section-title" style="margin-top:10px">过敏史（F13，患者级别，全就诊可见）</div>
    <div class="row">
      <input id="al_name" placeholder="过敏原，如：青霉素" style="width:200px" ${locked ? 'disabled' : ''}>
      <input id="al_manifest" placeholder="表现" style="flex:1" ${locked ? 'disabled' : ''}>
      <select id="al_sev" style="width:110px" ${locked ? 'disabled' : ''}>
        <option value="MILD">轻</option><option value="MODERATE">中</option><option value="SEVERE">重</option></select>
      ${locked ? '' : `<button class="btn sm" onclick="addAllergy(${rid})">添加</button>`}
    </div>
    <div class="hint">${(r.allergies || []).map(a => esc(a.allergen) + '（' + esc(a.severity || '') + '）').join('；') || '无'}</div>
    </details>
    ${locked ? '' : `<div class="row" style="margin-top:6px">
      <button class="btn sm ghost" onclick="showRecordTpl(${rid})">套用病历模板</button>
      <button class="btn sm ghost" onclick="saveAsRecordTpl(${rid})">存为模板</button></div>`}
  </div>

  <div class="card">
    <div class="section-title">主诉（F10）</div>
    <div class="row"><input id="cc_content" style="flex:1" placeholder="例：反复头晕 3 天" value="${esc(cc.content || '')}" ${locked ? 'disabled' : ''}></div>
    <div class="row"><input id="cc_duration" placeholder="持续时间" value="${esc(cc.duration || '')}" style="width:220px" ${locked ? 'disabled' : ''}></div>

    <div class="section-title">现病史（F11）</div>
    <textarea id="pi_content" ${locked ? 'disabled' : ''}>${esc(pi.content || '')}</textarea>
    <div class="grid3" style="margin-top:8px">
      <div><label>起病时间</label><input id="pi_onset" value="${esc(pi.onset_time || '')}" ${locked ? 'disabled' : ''}></div>
      <div><label>加重因素</label><input id="pi_agg" value="${esc(pi.aggravate || '')}" ${locked ? 'disabled' : ''}></div>
      <div><label>缓解因素</label><input id="pi_rel" value="${esc(pi.relieve || '')}" ${locked ? 'disabled' : ''}></div>
    </div>

    <details class="grp-tcm" ${isTcm ? 'open' : ''}>
    <summary class="section-title tcm" style="cursor:pointer;display:list-item">中医：四诊 / 辨证论治 / 中医诊断（F108 · F109）${isTcm ? '' : '—— 本次是西医就诊，点开可补录'}</summary>
    <div class="section-title tcm">中医四诊（望闻问切 · F108）</div>
    <div class="grid3">
      <div><label>望（神色形态）</label><textarea id="tf_ins" ${locked ? 'disabled' : ''}>${esc(tf.inspection || '')}</textarea></div>
      <div><label>闻（声息气味）</label><textarea id="tf_aus" ${locked ? 'disabled' : ''}>${esc(tf.auscultation || '')}</textarea></div>
      <div><label>问（寒热汗出饮食二便）</label><textarea id="tf_int" ${locked ? 'disabled' : ''}>${esc(tf.interrogation || '')}</textarea></div>
      <div><label>舌质</label><input id="tf_tb" value="${esc(tf.tongue_body || '')}" ${locked ? 'disabled' : ''}>
        ${locked ? '' : tagChips('tf_tb', TONGUE_BODY)}</div>
      <div><label>舌苔</label><input id="tf_tc" value="${esc(tf.tongue_coating || '')}" ${locked ? 'disabled' : ''}>
        ${locked ? '' : tagChips('tf_tc', TONGUE_COAT)}</div>
      <div><label>脉象</label><input id="tf_pd" value="${esc(tf.pulse_desc || '')}" ${locked ? 'disabled' : ''}>
        ${locked ? '' : tagChips('tf_pd', PULSE_OPTS)}</div>
    </div>

    <div class="section-title tcm">辨证论治（F109）</div>
    <div class="grid3">
      <div><label>证型</label><input id="syn_name" value="${esc((r.syndromes && r.syndromes[0] && r.syndromes[0].syndrome_name) || '')}" ${locked ? 'disabled' : ''}>
        ${locked ? '' : tagChips('syn_name', SYNDROMES)}</div>
      <div><label>治则</label><input id="syn_prin" value="${esc((r.syndromes && r.syndromes[0] && r.syndromes[0].treatment_principle) || '')}" ${locked ? 'disabled' : ''}>
        ${locked ? '' : tagChips('syn_prin', TREAT_PRINCIPLE)}</div>
      <div><label>治法/方剂</label><input id="syn_meth" value="${esc((r.syndromes && r.syndromes[0] && r.syndromes[0].treatment_method) || '')}" ${locked ? 'disabled' : ''}></div>
    </div>

    <div class="grid3">
      <div><label>中医病 / 证</label><input id="dx_tcm" value="${esc((r.diagnoses || []).filter(d => d.diag_type === 'TCM').map(d => d.tcm_name).join('') || '')}" ${locked ? 'disabled' : ''}></div>
    </div>
    </details>

    <details class="grp-west" ${isTcm ? '' : 'open'}>
    <summary class="section-title" style="cursor:pointer;display:list-item">西医：诊断 / 体格检查 / 辅助检查（F14 · F15 · F16 · F17）${isTcm ? '—— 本次是中医就诊，点开可补录' : ''}</summary>
    <div class="section-title">诊断（F17）</div>
    <div class="grid3">
      <div><label>西医诊断（名称）</label><input id="dx_west" value="${esc((r.diagnoses || []).filter(d => d.diag_type === 'WEST').map(d => d.icd10_name).join('') || '')}" ${locked ? 'disabled' : ''}></div>
      <div><label>ICD-10 编码</label><input id="dx_code" value="${esc((r.diagnoses || []).filter(d => d.diag_type === 'WEST').map(d => d.icd10_code).join('') || '')}" ${locked ? 'disabled' : ''}></div>
    </div>
    <div class="hint">当前诊断：${dx}</div>

    <div class="section-title">生命体征 / 查体 / 辅助检查（F14-F16）</div>
    <div class="grid3">
      <div><label>体温 ℃</label><input id="vs_t" value="${esc(vit.temperature_c || '')}" ${locked ? 'disabled' : ''}></div>
      <div><label>脉搏 次/分</label><input id="vs_p" value="${esc(vit.pulse || '')}" ${locked ? 'disabled' : ''}></div>
      <div><label>呼吸 次/分</label><input id="vs_r" value="${esc(vit.respiration || '')}" ${locked ? 'disabled' : ''}></div>
      <div><label>收缩压</label><input id="vs_s" value="${esc(vit.systolic || '')}" ${locked ? 'disabled' : ''}></div>
      <div><label>舒张压</label><input id="vs_d" value="${esc(vit.diastolic || '')}" ${locked ? 'disabled' : ''}></div>
      <div><label>身高 cm / 体重 kg</label><input id="vs_hw" value="${esc(vit.height_cm || '')} / ${esc(vit.weight_kg || '')}" ${locked ? 'disabled' : ''}></div>
    </div>

    <div class="section-title">查体（F15）</div>
    <textarea id="exam" ${locked ? 'disabled' : ''}>${esc((r.exams && r.exams[0] && r.exams[0].content) || '')}</textarea>

    <div class="section-title">辅助检查（F16）</div>
    <div class="hint">外送检验只存<b>引用</b>（不复制结果全文），源数据更新时这里会提示；院内即时检查（血糖、心电图等）直接登记。</div>
    <table class="tbl" style="margin-top:6px">
      <thead><tr><th>类型</th><th>项目 / 摘要</th><th>结果</th><th>状态</th><th>${locked ? '' : '操作'}</th></tr></thead>
      <tbody>${examRows || '<tr><td colspan="5" class="hint">无</td></tr>'}</tbody>
    </table>
    ${locked ? '' : `
    <div class="grid3" style="margin-top:8px">
      <div><label>即时检查项目</label><input id="pt_name" placeholder="如：即时血糖"></div>
      <div><label>结果值</label><input id="pt_value" placeholder="如：8.5"></div>
      <div><label>单位</label><input id="pt_unit" placeholder="如：mmol/L"></div>
    </div>
    <div class="row" style="margin-top:6px">
      <button class="btn ghost sm" onclick="addPointExam(${rid})">登记即时检查</button>
      <button class="btn ghost sm" onclick="showExamRefPicker(${rid},${v.id})">引用本次就诊的检验结果</button>
      <button class="btn ghost sm" onclick="refreshExamRefs(${rid})">刷新辅助检查</button>
    </div>`}
    </details>

    <div class="section-title">医嘱（F18，不含药品）</div>
    <textarea id="orders" placeholder="每行一条，例：针灸 1 次" ${locked ? 'disabled' : ''}>${esc((r.orders || []).map(o => o.content).join('\n'))}</textarea>
    <div class="hint">药品只能开处方（F34/F111），医嘱中写药会被拒绝。</div>
  </div>

  <div class="card">
    <h3>病历流转</h3>
    <div class="kv">
      <div class="k">当前诊断</div><div>${dx}</div>
      <div class="k">辨证</div><div>${syn}</div>
      <div class="k">医嘱</div><div>${orders}</div>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="btn ghost" onclick="showStatusLog(${rid})">查看状态日志</button>
      <button class="btn ghost" onclick="showAudit(${rid})">查看审计记录</button>
    </div>
  </div>

  <div class="card">
    <h3>病历归档（M11 · 版本 / Hash 链 / 封存）</h3>
    <div id="ar_box"><div class="hint">加载中…</div></div>
  </div>`;
}

/* ---------- M11 病历归档 ---------- */
async function arLoad(rid) {
  const box = document.getElementById('ar_box');
  if (!box) return;
  try {
    const st = await api('/api/record/get', { id: rid }, 'GET');
    const vs = await api('/api/record/versions', { record_id: rid }, 'GET');
    const vh = await api('/api/record/hash/verify', { record_id: rid }, 'GET');
    const arcs = (await api('/api/record/archive/list', {}, 'GET')).items || [];
    const mine = arcs.filter(a => a.record_id == rid);
    const arc = mine[mine.length - 1];
    const s = st.status;
    const canEdit = (s === 'CONFIRMED' || s === 'UNSEALED');
    box.innerHTML = `
      <div class="kv">
        <div class="k">当前状态</div><div>${statusTag(s)}</div>
        <div class="k">版本</div><div>v${vs.current_version || 0}（共 ${(vs.items || []).length} 个版本，确认与每次修改留痕各生成一个）</div>
        <div class="k">Hash 链</div><div>${vh.valid
          ? '<span class="tag ok">校验通过（未被篡改）</span>'
          : '<span class="tag danger">异常：' + esc(vh.reason) + '</span>'}</div>
        <div class="k">归档号</div><div>${arc ? esc(arc.archive_no) + '（v' + arc.version_no + '，' + esc(arc.archived_at) + '）' : '未归档'}</div>
      </div>
      <div class="row" style="margin-top:10px">
        ${s === 'CONFIRMED' ? `<button class="btn ghost" onclick="arClose(${rid})">关闭病历</button>` : ''}
        ${canEdit ? `<button class="btn ghost" onclick="arAmend(${rid})">修改留痕</button>` : ''}
        ${canEdit ? `<button class="btn" onclick="arArchive(${rid})">${s === 'UNSEALED' ? '重新归档' : '归档'}</button>` : ''}
        ${s === 'ARCHIVED' ? `<button class="btn warn" onclick="arSeal(${rid})">封存</button>` : ''}
        ${s === 'SEALED' ? `<button class="btn warn" onclick="arUnseal(${rid})">解封</button>` : ''}
        <button class="btn ghost" onclick="arVersions(${rid})">版本历史</button>
        <button class="btn ghost" onclick="arExport(${rid})">导出</button>
        <button class="btn ghost" onclick="arExportList()">导出记录</button>
        <button class="btn ghost" onclick="arVerifyAll()">Hash 链全量巡检</button>
      </div>
      <div class="hint" style="margin-top:6px">已确认病历不能直接编辑：修改会生成新版本并留痕，
        Hash 链逐环校验，任何篡改都会被发现；封存后不可修改，解封属高风险操作需填原因。</div>`;
  } catch (e) {
    box.innerHTML = '<div class="hint">' + esc(e.message) + '</div>';
  }
}

async function arExportList() {
  try {
    const items = (await api('/api/record/export/list', { limit: 50 }, 'GET')).items || [];
    modal('病历导出记录', items.length
      ? `<table class="tbl"><thead><tr><th>时间</th><th>病历</th><th>版本</th><th>格式</th><th>文件</th><th>操作人</th></tr></thead><tbody>${
        items.map(e => `<tr><td>${esc(e.created_at || '')}</td><td>${esc(e.record_id)}</td>
          <td>v${esc(e.version_no)}</td><td>${esc(e.format || '')}</td>
          <td>${esc(e.target_path || '')}</td><td>${esc(e.created_by)}</td></tr>`).join('')}</tbody></table>`
      : '<div class="hint">无导出记录</div>');
  } catch (e) { toast(e.message, 'err'); }
}
async function arVerifyAll() {
  try {
    const r = await api('/api/record/hash/verify_all', { limit: 200 });
    modal('Hash 链全量巡检', `<div class="kv">
      <div class="k">检查条数</div><div>${esc(r.checked || r.total || 0)}</div>
      <div class="k">异常条数</div><div>${esc(r.bad_count ?? (r.bad || []).length ?? 0)}</div></div>
      <pre style="white-space:pre-wrap;font-size:12px;max-height:360px;overflow:auto">${esc(JSON.stringify(r, null, 2))}</pre>`);
  } catch (e) { toast(e.message, 'err'); }
}

async function arClose(rid) {
  const summary = prompt('关闭小结（可留空，如"疗程结束"）：') || '';
  try { await api('/api/record/close', { record_id: rid, summary: summary }); toast('已关闭'); arLoad(rid); }
  catch (e) { toast(e.message, 'err'); }
}

async function arAmend(rid) {
  const reason = prompt('修改原因（必填，说明改了什么 + 为什么）：');
  if (!reason) return;
  const after = prompt('把主诉修改为（留空则只改现病史）：');
  const changes = [];
  if (after) changes.push({ field_path: 'chief_complaint.content', after_value: after });
  const pi = prompt('把现病史修改为（可留空）：');
  if (pi) changes.push({ field_path: 'present_illness.content', after_value: pi });
  if (!changes.length) { toast('未填写任何变更', 'err'); return; }
  try {
    const r = await api('/api/record/amend', { record_id: rid, reason: reason, changes: changes });
    toast('已生成 v' + r.version_no + ' 并留痕');
    arLoad(rid);
  } catch (e) { toast(e.message, 'err'); }
}

async function arArchive(rid) {
  try { const r = await api('/api/record/archive', { record_id: rid }); toast('已归档：' + r.archive_no); arLoad(rid); }
  catch (e) { toast(e.message, 'err'); }
}

async function arSeal(rid) {
  const reason = prompt('封存原因（可选）：') || '';
  try { await api('/api/record/seal', { record_id: rid, reason: reason }); toast('已封存'); arLoad(rid); }
  catch (e) { toast(e.message, 'err'); }
}

async function arUnseal(rid) {
  const reason = prompt('解封原因（必填，属高风险操作）：');
  if (!reason) return;
  try { await api('/api/record/unseal', { record_id: rid, reason: reason }); toast('已解封'); arLoad(rid); }
  catch (e) { toast(e.message, 'err'); }
}

async function arVersions(rid) {
  try {
    const vs = await api('/api/record/versions', { record_id: rid }, 'GET');
    const rows = (vs.items || []).map(v => `<tr>
        <td>v${v.version_no}</td><td>${v.source === 'CONFIRM' ? '确认' : '修改留痕'}</td>
        <td>${esc(v.change_note || '')}</td><td>${esc(v.created_role || '')}</td>
        <td>${fmtTime(v.created_at)}</td>
        <td><button class="btn sm ghost" onclick="arSnapshot(${rid},${v.version_no})">看快照</button></td>
      </tr>`).join('');
    modal('版本历史', `<table class="tbl"><thead><tr><th>版本</th><th>来源</th><th>说明</th><th>角色</th><th>时间</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6">无</td></tr>'}</tbody></table>`);
  } catch (e) { toast(e.message, 'err'); }
}

async function arSnapshot(rid, vno) {
  try {
    const v = await api('/api/record/version/get', { record_id: rid, version_no: vno }, 'GET');
    const cc = (v.snapshot && v.snapshot.chief_complaint) || {};
    const dxs = ((v.snapshot && v.snapshot.diagnoses) || []).map(d => esc(d.icd10_name || d.tcm_name)).join('；');
    modal('v' + vno + ' 快照', `<div class="kv">
      <div class="k">主诉</div><div>${esc(cc.content || '')} ${esc(cc.duration || '')}</div>
      <div class="k">诊断</div><div>${dxs || '无'}</div>
      <div class="k">prev_hash</div><div class="hint">${esc(v.prev_hash || '(空)')}</div>
      <div class="k">hash</div><div class="hint">${esc(v.hash || '')}</div>
    </div><pre style="white-space:pre-wrap;font-size:12px;max-height:320px;overflow:auto">${esc(JSON.stringify(v.snapshot, null, 2))}</pre>`);
  } catch (e) { toast(e.message, 'err'); }
}

async function arExport(rid) {
  const fmt = prompt('导出格式（PDF / HTML / JSON）：', 'HTML');
  if (!fmt) return;
  try {
    const r = await api('/api/record/export', { record_id: rid, format: fmt.toUpperCase() });
    modal('导出完成', `<div class="kv">
      <div class="k">格式</div><div>${esc(r.format)}</div>
      <div class="k">版本</div><div>v${r.version_no}</div>
      <div class="k">文件</div><div>${esc(r.target_path)}（${r.file_size} 字节）</div>
    </div><div class="hint">导出前已强制校验 Hash 链；系统只记录导出事件，不保存病历内容副本。</div>`);
    arLoad(rid);
  } catch (e) { toast(e.message, 'err'); }
}

function val(id) { const el = document.getElementById(id); return el ? el.value : ''; }

async function saveAll(rid, silent) {
  try {
    await api('/api/record/save/chief', { record_id: rid, content: val('cc_content'), duration: val('cc_duration') });
    await api('/api/record/save/present', { record_id: rid, content: val('pi_content'),
      onset_time: val('pi_onset'), aggravate: val('pi_agg'), relieve: val('pi_rel') });
    await api('/api/record/save/tcm_four', { record_id: rid, inspection: val('tf_ins'),
      auscultation: val('tf_aus'), interrogation: val('tf_int'), tongue_body: val('tf_tb'),
      tongue_coating: val('tf_tc'), pulse_desc: val('tf_pd') });
    const items = [];
    if (val('syn_name')) items.push({ syndrome_name: val('syn_name'),
      treatment_principle: val('syn_prin'), treatment_method: val('syn_meth') });
    await api('/api/record/save/syndrome', { record_id: rid, items: items, replace: true });
    const dxs = [];
    if (val('dx_west')) dxs.push({ diag_type: 'WEST', icd10_name: val('dx_west'), icd10_code: val('dx_code'), is_primary: 1 });
    if (val('dx_tcm')) dxs.push({ diag_type: 'TCM', tcm_name: val('dx_tcm') });
    await api('/api/record/save/diagnosis', { record_id: rid, items: dxs, replace: true });
    const hw = val('vs_hw').split('/');
    await api('/api/record/save/vital', { record_id: rid, temperature_c: parseFloat(val('vs_t')) || null,
      pulse: parseInt(val('vs_p')) || null, respiration: parseInt(val('vs_r')) || null,
      systolic: parseInt(val('vs_s')) || null, diastolic: parseInt(val('vs_d')) || null,
      height_cm: parseFloat(hw[0]) || null, weight_kg: parseFloat(hw[1]) || null });
    await api('/api/record/save/exam', { record_id: rid,
      items: val('exam') ? [{ section: 'GENERAL', content: val('exam') }] : [], replace: true });
    await api('/api/record/save/order', { record_id: rid,
      items: val('orders').split('\n').filter(s => s.trim()).map(s => ({ order_type: 'ADVICE', content: s.trim() })) });
    if (!silent) { toast('已保存', 'ok'); render(); }
    return true;
  } catch (e) { toast(e.message, 'err'); return false; }
}

/* 确认签名前自动先保存：少点一次按钮，也避免「改了内容没保存就签名」这种丢数据的坑 */
/* ---------- 签名口令：会话内免重复输入 ----------
   单人诊所一个医生一天要签几十次字，每次手输口令是纯粹的重复劳动。
   口令只存在当前浏览器标签页内存里（sessionStorage），关掉页面或超过 30 分钟即失效，
   后端审计会记录本次签名是「手输」还是「会话内已验证」，事后可追溯。
   想每次都输：在系统设置里把「记住签名口令」关掉即可。 */
const SIGN_REMEMBER_MS = 30 * 60 * 1000;
function signRememberOn() { return localStorage.getItem('clinic_sign_remember') !== '0'; }
function signPwdGet() {
  if (!signRememberOn()) return null;
  try {
    const at = parseInt(sessionStorage.getItem('clinic_sign_at') || '0', 10);
    if (!at || Date.now() - at > SIGN_REMEMBER_MS) { signPwdClear(); return null; }
    return sessionStorage.getItem('clinic_sign_pwd') || null;
  } catch (e) { return null; }
}
function signPwdSet(p) {
  if (!signRememberOn() || !p) return;
  try {
    sessionStorage.setItem('clinic_sign_pwd', p);
    sessionStorage.setItem('clinic_sign_at', String(Date.now()));
  } catch (e) { }
}
function signPwdClear() {
  try {
    sessionStorage.removeItem('clinic_sign_pwd');
    sessionStorage.removeItem('clinic_sign_at');
  } catch (e) { }
}

async function confirmFlow(rid) {
  const ok = await saveAll(rid, true);
  if (!ok) return;
  const cached = signPwdGet();
  if (cached) { await doConfirmRecord(rid, cached, 'SESSION'); return; }
  showConfirm(rid);
}

// ---------- F16 辅助检查 ----------
async function addPointExam(rid) {
  const name = val('pt_name'), value = val('pt_value');
  if (!name || !value) { toast('请填写即时检查的项目名称与结果值', 'err'); return; }
  try {
    await api('/api/record/save/exam_ref', { record_id: rid, items: [{
      ref_type: 'POINT', exam_name: name, exam_value: value, unit: val('pt_unit') || null }],
      reason: '院内即时检查登记' });
    toast('即时检查已登记', 'ok'); render();
  } catch (e) { toast(e.message, 'err'); }
}

async function showExamRefPicker(rid, visitId) {
  try {
    const r = await api('/api/lab/order/list', { visit_id: visitId }, 'GET');
    const items = r.items || [];
    if (!items.length) { toast('本次就诊还没有检查申请，请先开单', 'err'); return; }
    const opts = items.map(o => `<option value="${o.id}">${esc(o.order_no)} · ${esc(o.status)}</option>`).join('');
    modal('引用检验结果（F16）', `
      <div class="hint">外送结果只存引用与一句摘要，不复制结果全文；未回报的申请单会显示为「已开单待回报」。</div>
      <div class="row" style="margin-top:8px"><label style="width:96px">检查申请</label>
        <select id="exr_order" style="flex:1" onchange="loadExamResults()">${opts}</select></div>
      <div class="row"><label style="width:96px">具体结果</label>
        <select id="exr_result" style="flex:1"><option value="">（整单引用·待回报）</option></select></div>
      <div class="row"><label style="width:96px">摘要</label>
        <input id="exr_sum" style="flex:1" placeholder="如：血常规示白细胞升高"></div>
      <div class="row" style="margin-top:10px">
        <button class="btn" onclick="doExamRef(${rid})">引用</button></div>`);
    loadExamResults();
  } catch (e) { toast(e.message, 'err'); }
}

async function loadExamResults() {
  const oid = val('exr_order');
  const sel = document.getElementById('exr_result');
  if (!oid || !sel) return;
  try {
    const o = await api('/api/lab/order/get', { lab_order_id: oid }, 'GET');
    sel.innerHTML = '<option value="">（整单引用·待回报）</option>' +
      (o.results || []).map(x => `<option value="${x.id}">${esc(x.item_name)} ${esc(x.result_text || '')}${x.unit ? ' ' + esc(x.unit) : ''}${x.abnormal_flag && x.abnormal_flag !== 'NORMAL' ? ' ⚠' : ''}</option>`).join('');
  } catch (e) { toast(e.message, 'err'); }
}

async function doExamRef(rid) {
  const oid = parseInt(val('exr_order'));
  const resultId = val('exr_result');
  try {
    await api('/api/record/save/exam_ref', { record_id: rid, items: [{
      ref_type: 'LAB', lab_order_id: oid,
      lab_result_id: resultId ? parseInt(resultId) : null,
      summary: val('exr_sum') || null }], reason: '引用检验结果' });
    toast('已引用', 'ok'); closeModal(); render();
  } catch (e) { toast(e.message, 'err'); }
}

async function refreshExamRefs(rid) {
  try {
    const d = await api('/api/record/exam_ref/list', { record_id: rid }, 'GET');
    const rows = (d.items || []).map(x => `<span class="btn sm ghost" style="margin:2px"
      >${esc(x.display_status || '')}：${esc(x.display_text || '')}</span>`).join('');
    toast('辅助检查已刷新：' + (d.items || []).length + ' 条', 'ok');
    const el = document.getElementById('rx_msg');
    if (el) el.innerHTML = rows || '无';
    render();
  } catch (e) { toast(e.message, 'err'); }
}

async function removeExamRef(rid, id) {
  const reason = prompt('移除引用的原因（留痕用）：');
  if (!reason) return;
  try {
    await api('/api/record/save/exam_ref', { record_id: rid, items: [{ id: id, deleted: true }], reason: reason });
    toast('引用已移除（M05 原始结果不受影响）', 'ok'); render();
  } catch (e) { toast(e.message, 'err'); }
}

function showConfirm(rid) {
  modal('病历确认签名（F19）', `
    <div class="hint">确认后病历内容锁定，修改需走病历修改留痕流程。请输入登录口令作为签名凭据。</div>
    <div class="row" style="margin-top:10px"><input id="sign_pwd" type="password" placeholder="登录口令"
      onkeydown="if(event.key==='Enter')confirmRecord(${rid})"></div>
    <div class="row"><button class="btn" onclick="confirmRecord(${rid})">确认签名</button>
      <span class="hint">${signRememberOn() ? '本次签名后 30 分钟内免再输（仅存于本标签页）' : ''}</span></div>`);
}
async function confirmRecord(rid) {
  const p = val('sign_pwd');
  if (!p) { toast('请输入登录口令', 'err'); return; }
  await doConfirmRecord(rid, p, 'INPUT');
}
async function doConfirmRecord(rid, pwd, src) {
  try {
    await api('/api/record/confirm', { record_id: rid, password: pwd, pw_source: src });
    signPwdSet(pwd);
    toast('病历已确认并签名', 'ok'); closeModal(); render();
  } catch (e) {
    // 会话缓存的口令失效（改过密码）→ 清掉，退回手输，不要死循环
    if (src === 'SESSION') { signPwdClear(); toast(e.message, 'err'); showConfirm(rid); return; }
    toast(e.message, 'err');
  }
}
async function suspendVisit(id) {
  const reason = prompt('挂单/留观原因（必填）：');
  if (!reason) return;
  try { await api('/api/visit/suspend', { visit_id: id, suspend_type: 'OBSERVATION', reason: reason }); toast('已挂单', 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}
async function completeVisit(id) {
  try { await api('/api/visit/complete', { visit_id: id }); toast('就诊已完成', 'ok'); go('#/workbench'); }
  catch (e) { toast(e.message, 'err'); }
}
/* 从工作台点「收费」进来：自动找/建该就诊的账单，不用再手填账单 ID */
async function blAuto(vid) {
  const vsel = document.getElementById('bl_visit');
  if (vsel) {
    if (!Array.from(vsel.options).some(o => String(o.value) === String(vid))) {
      vsel.insertAdjacentHTML('beforeend', `<option value="${vid}">就诊 #${vid}（来自工作台）</option>`);
    }
    vsel.value = String(vid);
  }
  P3.visitId = vid;
  await fillBillSelect('bl_bill', vid);
  const bsel = document.getElementById('bl_bill');
  if (bsel && bsel.value) {
    P3.billId = bsel.value;
    await blLoad();
    toast('已载入该就诊的账单', 'ok');
  } else {
    // 该就诊还没建账单，不阻断页面，提示用户点「生成账单」
    toast('该就诊还没有账单，点「生成账单」即可', 'ok');
  }
}

/* 从工作台点「打印」进来：自动带出处方与账单 ID */
async function tryFillPrint(vid) {
  try {
    const vsel = document.getElementById('pr_visit');
    if (vsel) {
      if (!Array.from(vsel.options).some(o => String(o.value) === String(vid))) {
        vsel.insertAdjacentHTML('beforeend', `<option value="${vid}">就诊 #${vid}（来自工作台）</option>`);
      }
      vsel.value = String(vid);
    }
    await fillRxSelect('pr_rx', vid);
    await fillBillSelect('pr_bill', vid);
    const v = await api('/api/visit/get', { id: vid }, 'GET');
    const msg = document.getElementById('pr_msg');
    if (msg) msg.innerHTML = `当前就诊：<b>${esc(v.visit_no)}</b> · ${esc(v.patient_name || '')}
      （处方与账单已自动列出，直接在上面选好点打印）`;
  } catch (e) { console.warn('tryFillPrint:', e.message); }
}

/* ---- 病历既往史 / 过敏史 / 模板（F12 / F13 / F126）---- */
async function addHistory(rid) {
  const d = val('hs_disease');
  if (!d) { toast('请填写疾病/事项', 'err'); return; }
  try {
    await api('/api/record/save/history', { record_id: rid, items: [{
      hist_type: val('hs_type'), disease: d, diagnosed_at: val('hs_at') || null,
      treatment: val('hs_treat') || null, outcome: val('hs_outcome') || null }] });
    toast('已添加', 'ok'); render();
  } catch (e) { toast(e.message, 'err'); }
}
async function addAllergy(rid) {
  const a = val('al_name');
  if (!a) { toast('请填写过敏原', 'err'); return; }
  try {
    await api('/api/record/save/allergy', { record_id: rid, items: [{
      allergen: a, manifestation: val('al_manifest') || null, severity: val('al_sev') }] });
    toast('已添加（患者级别，全就诊可见）', 'ok'); render();
  } catch (e) { toast(e.message, 'err'); }
}
async function showRecordTpl(rid) {
  try {
    const t = (await api('/api/record/templates', {}, 'GET')).items || [];
    if (!t.length) { toast('暂无模板，可先「存为模板」', 'err'); return; }
    modal('套用病历模板', t.map(x => `<div class="row">
      <span style="flex:1">${esc(x.name || x.template_name)}（${esc(x.category || '')}）</span>
      <button class="btn sm" onclick="useRecordTpl(${rid},${x.id})">套用</button></div>`).join(''));
  } catch (e) { toast(e.message, 'err'); }
}
async function useRecordTpl(rid, id) {
  try { await api('/api/record/template/use', { record_id: rid, template_id: id }); toast('已套用', 'ok'); closeModal(); render(); }
  catch (e) { toast(e.message, 'err'); }
}
async function saveAsRecordTpl(rid) {
  const name = prompt('模板名称：'); if (!name) return;
  const cat = prompt('分类（如 感冒/咳嗽）：') || '通用';
  try { await api('/api/record/template/create', { record_id: rid, name: name, category: cat }); toast('已存为模板', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}

async function callVisit(id) {
  try { const r = await api('/api/visit/call', { visit_id: id }); toast('已叫号：' + (r.queue_no || ''), 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}
async function resumeVisit(id) {
  try { await api('/api/visit/resume', { visit_id: id }); toast('已恢复接诊', 'ok'); go('#/record/' + id); }
  catch (e) { toast(e.message, 'err'); }
}
async function voidVisit(id) {
  const reason = prompt('作废原因（必填，将联动取消未送检的检验申请）：');
  if (!reason) return;
  try { await api('/api/visit/void', { visit_id: id, reason: reason }); toast('就诊已作废', 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}
/* ============================================================
   M06 开方（F32-F39 / F110-F113）
   ============================================================ */
/* ============================================================
   M06 开方 —— 连续录入模式（2026-10-20 易用性改造）

   改造前的开方流程（实测 12 味方要 51 步）：
     选就诊 → 建方 → 输关键词 → 点搜索 → 点药名 → prompt 弹窗填剂量
     →（每味重复 4 步）→ 点「保存明细」→ 计价 → 预检 → 提交 → 确认

   现在：目录一次拉全量放本地，输入即出候选，回车直接入库。
     「黄芪 15」回车 = 1 步；「hq15」回车 = 1 步；整段方子粘贴 = 1 步。
   没有 prompt，没有「保存明细」二次按钮 —— 加了就是存了。
   ============================================================ */
/* 默认饮片方：这是中医诊所，绝大多数处方是中药，不该每次都先切成「饮片方」 */
const RX_STATE = { visitId: 0, rxId: 0, rx: null, lines: [],
                   cand: [], candIdx: -1,
                   rxType: 'HERB', doses: '7', decoction: '水煎',
                   admin: '每日一剂，分两次温服',
                   last: null, tpls: [], tplOpen: false };
const CAT = { herbs: [], drugs: [], freqHerb: [], freqDrug: [], at: 0, loading: false, err: '' };

async function loadCatalog(force) {
  if (!force && CAT.herbs.length && Date.now() - CAT.at < 600000) return CAT;
  if (CAT.loading) return CAT;
  CAT.loading = true;
  try {
    const [h, d, fh, fd] = await Promise.all([
      api('/api/catalog/herbs', {}, 'GET').catch(() => ({ items: [] })),
      api('/api/catalog/drugs', {}, 'GET').catch(() => ({ items: [] })),
      api('/api/catalog/frequent', { kind: 'herb', limit: 36 }, 'GET').catch(() => ({ items: [] })),
      api('/api/catalog/frequent', { kind: 'drug', limit: 18 }, 'GET').catch(() => ({ items: [] })),
    ]);
    CAT.herbs = h.items || []; CAT.drugs = d.items || [];
    CAT.freqHerb = fh.items || []; CAT.freqDrug = fd.items || [];
    CAT.at = Date.now();
  } catch (e) { CAT.err = e.message || ''; } finally { CAT.loading = false; }
  return CAT;
}

/* 匹配打分：正名 > 首字母 > 全拼 > 前缀 > 包含 */
function catScore(item, kw, isHerb) {
  const name = (isHerb ? item.name : item.generic_name) || '';
  const py = (item.pinyin || '').toLowerCase();
  const ini = (item.pinyin_initials || '').toLowerCase();
  const k = String(kw || '').toLowerCase();
  if (!k) return 0;
  const n = name.toLowerCase();
  if (n === k) return 100;
  if (ini === k) return 92;
  if (py === k) return 88;
  if (n.indexOf(k) === 0) return 70;
  if (ini.indexOf(k) === 0) return 62;
  if (py.indexOf(k) === 0) return 55;
  if (n.indexOf(k) > 0) return 40;
  if (ini.indexOf(k) > 0) return 30;
  if (py.indexOf(k) > 0) return 22;
  return 0;
}

/* 解析一行录入："黄芪 15" / "黄芪15" / "hq 15" / "黄芪" */
function rxParse(raw) {
  const s = String(raw == null ? '' : raw).replace(/[，,、]/g, ' ').trim();
  if (!s) return null;
  const m = s.match(/^(.*?)[\s]*(\d+(?:\.\d+)?)$/);
  if (m && m[1].trim()) return { name: m[1].trim(), dose: parseFloat(m[2]) };
  return { name: s, dose: null };
}

function rxIsHerb() {
  return !!(RX_STATE.rx && RX_STATE.rx.prescription_type !== 'WEST');
}

function rxFind(raw) {
  const p = rxParse(raw);
  if (!p) return null;
  const isHerb = rxIsHerb();
  const pool = isHerb ? CAT.herbs : CAT.drugs;
  const scored = [];
  for (const x of pool) {
    const s = catScore(x, p.name, isHerb);
    if (s > 0) scored.push({ x: x, s: s });
  }
  scored.sort((a, b) => b.s - a.s);
  if (!scored.length) return null;
  const hit = scored[0].x;
  const dose = p.dose || (isHerb ? (hit.usual_dose_g || 15) : 1);
  return { hit: hit, dose: dose, name: isHerb ? hit.name : hit.generic_name,
           ambiguous: scored.length > 1 && scored[1].s === scored[0].s };
}

function rxPush(hit, dose) {
  const isHerb = rxIsHerb();
  RX_STATE.lines.push(isHerb
    ? { herb_id: hit.id, dose_g: Number(dose) || hit.usual_dose_g || 15, foot_note: 'NORMAL' }
    : { drug_id: hit.id, quantity: Number(dose) || 1, dosage: '按医嘱',
        frequency: 'tid', route: 'PO', days: 3 });
}

/* 立即落库：后端 save/herbs 与 save/items 都是整方覆盖，
   所以每次增删改都要把完整 lines 发一遍 —— 前端 lines 就是唯一事实来源。 */
async function rxCommit(msg) {
  const rx = RX_STATE.rx;
  if (!rx) return false;
  const isHerb = rxIsHerb();
  try {
    if (isHerb) {
      await api('/api/prescription/save/herbs', {
        prescription_id: rx.id, items: RX_STATE.lines,
        doses: parseInt(RX_STATE.doses, 10) || 7,
        decoction_method: RX_STATE.decoction || '水煎',
        administration: RX_STATE.admin || '每日一剂，分两次温服',
      });
    } else {
      await api('/api/prescription/save/items', {
        prescription_id: rx.id, items: RX_STATE.lines });
    }
    if (msg) toast(msg, 'ok');
    await rxRender();
    return true;
  } catch (e) { toast(e.message, 'err'); return false; }
}

async function rxAddOne(raw) {
  await loadCatalog();
  const f = rxFind(raw);
  if (!f) { toast('目录里找不到「' + String(raw || '').trim() + '」', 'err'); return false; }
  rxPush(f.hit, f.dose);
  return await rxCommit('已加入 ' + f.name + ' ' + f.dose + (rxIsHerb() ? ' g' : ''));
}

async function rxBatchAdd() {
  const el = document.getElementById('rx_batch');
  if (!el) return;
  const rows = String(el.value || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if (!rows.length) { toast('请先粘贴方子内容', 'err'); return; }
  await loadCatalog();
  let ok = 0; const miss = [];
  for (const r of rows) {
    const f = rxFind(r);
    if (!f) { miss.push(r); continue; }
    rxPush(f.hit, f.dose); ok++;
  }
  el.value = '';
  if (ok) await rxCommit('批量加入 ' + ok + ' 味' + (miss.length ? '，' + miss.length + ' 味未找到' : ''));
  else toast('一行都没匹配上：' + miss.slice(0, 5).join('、'), 'err');
}

async function rxDose(i, v) {
  const n = parseFloat(v);
  if (!n || n <= 0) { toast('剂量必须大于 0', 'err'); await rxRender(); return; }
  const isHerb = rxIsHerb();
  if (isHerb) RX_STATE.lines[i].dose_g = n; else RX_STATE.lines[i].quantity = n;
  await rxCommit('');
}

async function rxDel(i) {
  RX_STATE.lines.splice(i, 1);
  await rxCommit('已删除');
}

async function rxMove(i, d) {
  const j = i + d;
  if (j < 0 || j >= RX_STATE.lines.length) return;
  const t = RX_STATE.lines[i]; RX_STATE.lines[i] = RX_STATE.lines[j]; RX_STATE.lines[j] = t;
  await rxCommit('');
}

function rxFieldChanged() {
  RX_STATE.doses = val('rx_doses');
  RX_STATE.decoction = val('rx_decoction');
  RX_STATE.admin = val('rx_admin');
}

/* ---------- 候选下拉 ---------- */
function rxCandRender() {
  const box = document.getElementById('rx_cand');
  if (!box) return;
  if (!RX_STATE.cand.length) { box.innerHTML = ''; box.style.display = 'none'; return; }
  const isHerb = rxIsHerb();
  box.style.display = 'block';
  box.innerHTML = RX_STATE.cand.map((c, i) => {
    const nm = isHerb ? c.x.name : c.x.generic_name;
    const extra = isHerb
      ? ((c.x.usual_dose_g ? '常用 ' + c.x.usual_dose_g + 'g' : '') +
         (c.x.is_toxic ? ' <b class="warn">毒</b>' : ''))
      : ((c.x.spec || '') + ' ' + ((c.x.price_fen || 0) / 100).toFixed(2) + '元');
    return `<div class="cand${i === RX_STATE.candIdx ? ' on' : ''}"
      onmousedown="rxCandPick(${i})">${esc(nm)}
      <span class="hint">${esc(String(extra || ''))}</span></div>`;
  }).join('');
}

async function rxCandPick(i) {
  const c = RX_STATE.cand[i];
  if (!c) return;
  const isHerb = rxIsHerb();
  const dose = isHerb ? (c.x.usual_dose_g || 15) : 1;
  rxPush(c.x, dose);
  const el = document.getElementById('rx_fast');
  if (el) el.value = '';
  RX_STATE.cand = []; RX_STATE.candIdx = -1;
  await rxCommit('已加入 ' + (isHerb ? c.x.name : c.x.generic_name) + ' ' + dose);
  const e2 = document.getElementById('rx_fast');
  if (e2) e2.focus();
}

async function rxFastInput(v) {
  await loadCatalog();
  const p = rxParse(v);
  if (!p || !p.name) { RX_STATE.cand = []; rxCandRender(); return; }
  const isHerb = rxIsHerb();
  const pool = isHerb ? CAT.herbs : CAT.drugs;
  const scored = [];
  for (const x of pool) {
    const s = catScore(x, p.name, isHerb);
    if (s > 0) scored.push({ x: x, s: s });
  }
  scored.sort((a, b) => b.s - a.s);
  RX_STATE.cand = scored.slice(0, 12);
  RX_STATE.candIdx = RX_STATE.cand.length ? 0 : -1;
  rxCandRender();
}

function rxFastKey(ev) {
  if (ev.key === 'ArrowDown') {
    if (RX_STATE.cand.length) {
      RX_STATE.candIdx = (RX_STATE.candIdx + 1) % RX_STATE.cand.length; rxCandRender();
    }
    ev.preventDefault(); return false;
  }
  if (ev.key === 'ArrowUp') {
    if (RX_STATE.cand.length) {
      RX_STATE.candIdx = (RX_STATE.candIdx - 1 + RX_STATE.cand.length) % RX_STATE.cand.length;
      rxCandRender();
    }
    ev.preventDefault(); return false;
  }
  if (ev.key === 'Enter') {
    const raw = val('rx_fast');
    if (RX_STATE.cand.length && RX_STATE.candIdx >= 0 && !rxParse(raw).dose) {
      rxCandPick(RX_STATE.candIdx);
    } else {
      rxAddOne(raw);
      const el = document.getElementById('rx_fast');
      if (el) el.value = '';
      RX_STATE.cand = []; rxCandRender();
    }
    ev.preventDefault(); return false;
  }
  if (ev.key === 'Escape') { RX_STATE.cand = []; rxCandRender(); return false; }
  return true;
}

/* ---------- 页面骨架 ---------- */
async function viewPrescribe() {
  try { await loadVisitChoices(); } catch (e) { }
  return `
  <div class="card">
    <h3>开方（M06）</h3>
    <div class="row">
      ${visitSelectHtml('rx_visit', {
        selected: RX_STATE.visitId,
        placeholder: '— 选择本次就诊 —',
        status: ['WAITING', 'IN_PROGRESS', 'SUSPENDED'],
        onchange: 'rxPickVisit()',
      })}
      <button class="btn ghost sm" onclick="rxLoad()">载入</button>
    </div>
    <div id="rx_body"><div class="hint">选择就诊后载入该就诊的处方。</div></div>
  </div>`;
}

async function rxPickVisit() {
  RX_STATE.visitId = parseInt(val('rx_visit')) || 0;
  if (!RX_STATE.visitId) { $('#rx_body').innerHTML = '<div class="hint">选择就诊后载入该就诊的处方。</div>'; return; }
  await rxLoad();
}

function rxSeedBanner() {
  const real = CAT.herbs.filter(h => !/^M\d/.test(h.name || '')).length;
  if (real >= 20) return '';
  return `<div class="row" style="margin-top:8px;padding:8px;border:1px solid #e0b34a;border-radius:6px;background:#fff8e6">
    <span style="flex:1">目录里只有 ${real} 味饮片，开方搜不到药。
      点「一键载入」写入 ${'228'} 味常用中药（含拼音首字母、常用量、参考价）。</span>
    <button class="btn sm" onclick="rxSeed()">一键载入常用中药</button>
    <button class="btn sm ghost" onclick="rxCleanupTest()">清理测试品目</button>
  </div>`;
}

async function rxSeed() {
  try {
    const r = await api('/api/catalog/seed', { kind: 'all' });
    toast('已载入：饮片 ' + (r.herb ? r.herb.added : 0) + ' 味，成药 ' + (r.drug ? r.drug.added : 0) + ' 种', 'ok');
    await loadCatalog(true);
    await rxLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function rxCleanupTest() {
  try {
    const r = await api('/api/catalog/cleanup_test', {});
    toast('已停用测试品目：饮片 ' + (r.herb || 0) + ' 味，成药 ' + (r.drug || 0) + ' 种（可再启用）', 'ok');
    await loadCatalog(true);
    await rxLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function rxLoad() {
  RX_STATE.visitId = parseInt(val('rx_visit')) || RX_STATE.visitId || 0;
  if (!RX_STATE.visitId) { toast('请选择就诊', 'err'); return; }
  await loadCatalog();
  try {
    const v = await api('/api/visit/get', { id: RX_STATE.visitId }, 'GET');
    // 处方类型跟着就诊类型走：中医就诊只给饮片/颗粒，西医就诊只给成药
    const isTcmVisit = MODE.isTcm(v);
    if (isTcmVisit && RX_STATE.rxType === 'WEST') RX_STATE.rxType = 'HERB';
    if (!isTcmVisit) RX_STATE.rxType = 'WEST';
    const list = (await api('/api/prescription/list', { visit_id: RX_STATE.visitId }, 'GET')).items || [];
    const rows = list.map(r => `<tr>
      <td>${esc(r.prescription_no)}</td>
      <td>${r.prescription_type === 'HERB' ? '饮片' : (r.prescription_type === 'GRANULE' ? '颗粒' : '成药')}</td>
      <td>${statusTag(r.status)}</td>
      <td>${r.total_amount != null ? (r.total_amount / 100).toFixed(2) + ' 元' : '-'}</td>
      <td>
        <button class="btn sm" onclick="rxOpen(${r.id})">编辑</button>
        <button class="btn sm ghost" onclick="rxCheck(${r.id})">规则预检</button>
        <button class="btn sm ghost" onclick="rxPrint(${r.id})">打印处方笺</button>
        ${['DRAFT', 'PENDING', 'CONFIRMED'].includes(r.status)
          ? `<button class="btn sm warn" onclick="rxVoid(${r.id})">作废</button>` : ''}
      </td></tr>`).join('');
    $('#rx_body').innerHTML = `
      <div class="kv" style="margin-top:10px">
        <div class="k">患者</div><div>${esc(v.patient_name || '')} ${esc(v.patient_no || '')}</div>
        <div class="k">就诊</div><div>${esc(v.visit_no)} ${statusTag(v.status)}</div>
      </div>
      ${rxSeedBanner()}
      <div class="section-title" style="margin-top:12px">新建处方 ${MODE.chip(v)}</div>
      <div class="row">
        <select id="rx_type" style="width:170px">
          ${isTcmVisit
            ? `<option value="HERB"${RX_STATE.rxType !== 'GRANULE' ? ' selected' : ''}>饮片方（中药）</option>
               <option value="GRANULE"${RX_STATE.rxType === 'GRANULE' ? ' selected' : ''}>颗粒方</option>`
            : `<option value="WEST" selected>成药方（西药 / 中成药）</option>`}
        </select>
        <input id="rx_doses" placeholder="付数" style="width:80px" value="${esc(RX_STATE.doses || '7')}" onchange="rxFieldChanged()">
        <input id="rx_decoction" placeholder="煎法：水煎" style="flex:1" value="${esc(RX_STATE.decoction || '水煎')}" onchange="rxFieldChanged()">
        <input id="rx_admin" placeholder="服法：每日一剂分两次温服" style="flex:1" value="${esc(RX_STATE.admin || '')}" onchange="rxFieldChanged()">
        <button class="btn" onclick="rxCreate()">建方</button>
      </div>
      ${rxCatalogForm()}
      <div class="section-title" style="margin-top:12px">本就诊处方（${list.length}）</div>
      <table class="tbl"><thead><tr><th>处方号</th><th>类型</th><th>状态</th><th>金额</th><th>操作</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="hint">无</td></tr>'}</tbody></table>
      <div id="rx_editor"></div>`;
    // 建方后不重建整块，避免把刚选的类型冲掉
    const ts = document.getElementById('rx_type');
    if (ts) ts.onchange = function () { rxTypeChanged(this.value); };
  } catch (e) { toast(e.message, 'err'); }
}

function rxTypeChanged(v) {
  RX_STATE.rxType = v;
  const el = document.getElementById('rx_type_hint');
  if (el) {
    el.innerHTML = '将创建：' + (v === 'HERB' ? '<b>饮片方（中药）</b>'
      : v === 'GRANULE' ? '<b>颗粒方</b>' : '<b>成药方</b>');
  }
}

async function rxCreate() {
  const t = val('rx_type') || RX_STATE.rxType || 'WEST';
  RX_STATE.rxType = t;
  RX_STATE.doses = val('rx_doses') || RX_STATE.doses;
  RX_STATE.decoction = val('rx_decoction') || RX_STATE.decoction;
  RX_STATE.admin = val('rx_admin') || RX_STATE.admin;
  try {
    const r = await api('/api/prescription/create', {
      visit_id: RX_STATE.visitId, prescription_type: t,
      doses: t === 'WEST' ? null : (parseInt(RX_STATE.doses, 10) || 7),
      decoction_method: RX_STATE.decoction || null,
      administration: RX_STATE.admin || null });
    toast('已建方 ' + r.prescription_no, 'ok');
    await rxLoad();
    await rxOpen(r.id);
  } catch (e) { toast(e.message, 'err'); }
}

async function rxOpen(id) {
  RX_STATE.rxId = id;
  await loadCatalog();
  await rxLoadLast();
  await rxLoadTpls();
  try {
    const rx = await api('/api/prescription/get', { id: id }, 'GET');
    RX_STATE.rx = rx;
    const isHerb = rx.prescription_type !== 'WEST';
    const src = isHerb ? (rx.herbs || []) : (rx.items || []);
    RX_STATE.lines = src.map(it => isHerb
      ? { herb_id: it.herb_id, dose_g: it.dose_g, foot_note: it.foot_note || 'NORMAL' }
      : { drug_id: it.drug_id, quantity: it.quantity, dosage: it.dosage || '按医嘱',
          frequency: it.frequency || 'tid', route: it.route || 'PO', days: it.days || 3 });
    if (rx.doses) RX_STATE.doses = String(rx.doses);
    if (rx.decoction_method) RX_STATE.decoction = rx.decoction_method;
    if (rx.administration) RX_STATE.admin = rx.administration;
    await rxRender();
  } catch (e) { toast(e.message, 'err'); }
}

function rxNameOf(line, isHerb) {
  const pool = isHerb ? CAT.herbs : CAT.drugs;
  const id = isHerb ? line.herb_id : line.drug_id;
  const hit = pool.find(x => x.id === id);
  return hit ? (isHerb ? hit.name : hit.generic_name) : ('#' + id);
}

async function rxRender() {
  const box = document.getElementById('rx_editor');
  if (!box) return;
  const rx = RX_STATE.rx;
  if (!rx) { box.innerHTML = ''; return; }
  const isHerb = rxIsHerb();
  const locked = !['DRAFT', 'PENDING'].includes(rx.status);
  const rows = RX_STATE.lines.map((it, i) => {
    const nm = rxNameOf(it, isHerb);
    const hit = (isHerb ? CAT.herbs : CAT.drugs).find(x => x.id === (isHerb ? it.herb_id : it.drug_id));
    const tox = isHerb && hit && hit.is_toxic;
    return `<tr>
      <td>${i + 1}</td>
      <td>${esc(nm)}${tox ? ' <b class="warn" title="毒性饮片">毒</b>' : ''}</td>
      <td><input class="dose" style="width:74px" value="${isHerb ? it.dose_g : it.quantity}"
           onchange="rxDose(${i}, this.value)" ${locked ? 'disabled' : ''}>
           <span class="hint">${isHerb ? 'g' : (hit && hit.unit) || ''}</span></td>
      <td class="hint">${isHerb ? (hit && hit.usual_dose_g ? '常用 ' + hit.usual_dose_g + 'g' : '') : (hit && hit.spec) || ''}</td>
      <td>${locked ? '' : `<button class="btn sm ghost" onclick="rxMove(${i},-1)" title="上移">↑</button>
        <button class="btn sm ghost" onclick="rxMove(${i},1)" title="下移">↓</button>
        <button class="btn sm warn" onclick="rxDel(${i})">删</button>`}</td>
    </tr>`;
  }).join('');

  const freq = isHerb ? CAT.freqHerb : CAT.freqDrug;
  const freqHtml = freq.length ? freq.map(f =>
    `<span class="btn sm ghost" style="margin:2px" onclick="rxFreqAdd(${f.id},${isHerb ? (f.usual || 15) : 1})">${esc(f.name)}</span>`
  ).join('') : '<span class="hint">还没有历史处方，开过方后这里会自动长出常用药</span>';

  box.innerHTML = `
    <div class="section-title">编辑 ${esc(rx.prescription_no)} ${statusTag(rx.status)}
      ${isHerb ? '（饮片方）' : '（成药方）'}</div>

    ${locked ? '' : `
    <div class="row" style="margin-top:6px">
      ${RX_STATE.last ? `<button class="btn" onclick="rxCopyLast()">照上次方（${esc(RX_STATE.last.prescription_no)} · ${RX_STATE.last.count} 味）</button>` : ''}
      ${RX_STATE.tpls.length
        ? `<button class="btn ghost" onclick="rxTplToggle()">${isHerb ? '经典方' : '常用方'}（${RX_STATE.tpls.length}）</button>`
        : `<button class="btn sm ghost" onclick="rxSeedTpls()">载入经典方</button>`}
      <span class="hint">${RX_STATE.last ? '复诊：先照抄再加减，比一味味重打快得多' : '首诊：从经典方改，比空手起方快'}</span>
    </div>
    <div id="rx_tpls" class="chipbox" style="display:${RX_STATE.tplOpen ? 'block' : 'none'}">
      ${RX_STATE.tpls.map(t => `<span class="chip" onclick="rxTplApplyOne(${t.id})">${esc(t.name)}${t.indication ? ' <span class="hint">' + esc(t.indication) + '</span>' : ''}</span>`).join('')
      || '<span class="hint">还没有模板，点「载入经典方」一键预置 14 张常用方</span>'}
    </div>

    <div class="row" style="margin-top:6px;position:relative">
      <input id="rx_fast" style="flex:1" autocomplete="off" placeholder="输入药名或拼音首字母，回车直接加入。例：黄芪 15 / 当归12 / hq15"
             oninput="rxFastInput(this.value)" onkeydown="return rxFastKey(event)">
      <button class="btn" onclick="rxAddOne(val('rx_fast'));document.getElementById('rx_fast').value=''">加入</button>
    </div>
    <div id="rx_cand" class="cand-box" style="display:none"></div>
    <div class="hint">支持：中文名 / 拼音全拼 / 首字母，带不带剂量都行（不写剂量自动取常用量）。↑↓ 选候选，回车确认。</div>

    <details style="margin-top:6px"><summary class="hint">整段粘贴方子（一行一味）</summary>
      <textarea id="rx_batch" style="width:100%;height:110px;margin-top:6px"
        placeholder="黄芪 30&#10;当归 15&#10;党参 15&#10;白术 12"></textarea>
      <div class="row" style="margin-top:6px">
        <button class="btn sm" onclick="rxBatchAdd()">批量加入</button>
        <span class="hint">适合把纸质方子 / 旧系统里的方子一次粘进来</span>
      </div>
    </details>

    <div class="section-title" style="margin-top:10px">常用${isHerb ? '饮片' : '药品'}（点一下加入）</div>
    <div>${freqHtml}</div>`}

    <table class="tbl" style="margin-top:10px">
      <thead><tr><th>#</th><th>名称</th><th>用量</th><th>参考</th><th>${locked ? '' : '操作'}</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="hint">还没有明细，在上面的输入框里直接打字</td></tr>'}</tbody>
    </table>

    ${isHerb ? `<div class="row" style="margin-top:8px">
      <span class="hint">付数</span>
      <input id="rx_doses" style="width:70px" value="${esc(RX_STATE.doses || '7')}" onchange="rxFieldChanged();rxCommit('付数已保存')" ${locked ? 'disabled' : ''}>
      <input id="rx_decoction" placeholder="煎法" style="flex:1" value="${esc(RX_STATE.decoction || '')}" onchange="rxFieldChanged();rxCommit('煎法已保存')" ${locked ? 'disabled' : ''}>
      <input id="rx_admin" placeholder="服法" style="flex:1" value="${esc(RX_STATE.admin || '')}" onchange="rxFieldChanged();rxCommit('服法已保存')" ${locked ? 'disabled' : ''}>
    </div>` : ''}

    <div class="row" style="margin-top:10px">
      ${rx.status === 'DRAFT' ? `<button class="btn" onclick="rxSubmit(${rx.id})">提交（自动计价+预检）</button>` : ''}
      ${rx.status === 'PENDING' ? `<button class="btn warn" onclick="rxConfirm(${rx.id})">确认${signPwdGet() ? '（已记住口令）' : '（需口令）'}</button>` : ''}
      <button class="btn sm ghost" onclick="rxCheck(${rx.id})">规则预检</button>
      <button class="btn sm ghost" onclick="rxSaveAsTpl(${rx.id})">存为模板</button>
      <button class="btn sm ghost" onclick="rxGuide(${rx.id})">用药指导</button>
    </div>
    <div id="rx_msg" class="hint"></div>`;
}

/* ---------- 开方提速：照上次方 / 经典方 ---------- */
async function rxLoadLast() {
  RX_STATE.last = null;
  try {
    const r = await api('/api/prescription/last', { visit_id: RX_STATE.visitId }, 'GET');
    RX_STATE.last = (r && r.has_last) ? r : null;
  } catch (e) { RX_STATE.last = null; }
}

async function rxLoadTpls() {
  RX_STATE.tpls = [];
  try {
    const r = await api('/api/prescription/templates', {}, 'GET');
    RX_STATE.tpls = r.items || [];
  } catch (e) { RX_STATE.tpls = []; }
}

async function rxCopyLast() {
  if (!RX_STATE.rxId) return;
  try {
    const r = await api('/api/prescription/copy_last', { prescription_id: RX_STATE.rxId });
    const bits = [];
    if (r.herbs) bits.push(r.herbs + ' 味饮片');
    if (r.drugs) bits.push(r.drugs + ' 种成药');
    toast('已照抄 ' + r.source_prescription_no + '：' + bits.join(' + '), 'ok');
    if (r.skipped && r.skipped.length) toast('已停用、没照抄：' + r.skipped.join('、'), 'err');
    await rxOpen(RX_STATE.rxId);
  } catch (e) { toast(e.message, 'err'); }
}

function rxTplToggle() {
  RX_STATE.tplOpen = !RX_STATE.tplOpen;
  const el = document.getElementById('rx_tpls');
  if (el) el.style.display = RX_STATE.tplOpen ? 'block' : 'none';
}

async function rxTplApplyOne(tplId) {
  if (!RX_STATE.rxId) return;
  try {
    await api('/api/prescription/template/apply',
      { prescription_id: RX_STATE.rxId, template_id: tplId });
    toast('已套用，可继续加减味', 'ok');
    await rxOpen(RX_STATE.rxId);
  } catch (e) { toast(e.message, 'err'); }
}

async function rxSeedTpls() {
  try {
    const r = await api('/api/prescription/template/seed_classic', {});
    toast('已预置 ' + r.total + ' 张经典方', 'ok');
    if (r.missing && r.missing.length) {
      toast('目录缺药未建：' + r.missing.map(m => m.name).join('、'), 'err');
    }
    await rxLoadTpls();
    RX_STATE.tplOpen = true;
    await rxRender();
  } catch (e) { toast(e.message, 'err'); }
}

async function rxFreqAdd(id, dose) {
  const isHerb = rxIsHerb();
  const hit = (isHerb ? CAT.herbs : CAT.drugs).find(x => x.id === id);
  if (!hit) { toast('品目不在目录中', 'err'); return; }
  rxPush(hit, dose);
  await rxCommit('已加入 ' + (isHerb ? hit.name : hit.generic_name) + ' ' + dose);
}

async function rxPrice(id) {
  try {
    const r = await api('/api/prescription/price', { prescription_id: id });
    const el = document.getElementById('rx_msg');
    if (el) el.innerHTML = '<span class="ok">计价完成</span> ' + esc(JSON.stringify(r.pricing || r).slice(0, 200));
    await rxOpen(id);
  } catch (e) { toast(e.message, 'err'); }
}

async function rxCheck(id) {
  try {
    const r = await api('/api/prescription/validate', { prescription_id: id });
    const blocks = (r.blocks || []).map(b => `<div class="err">阻断：${esc(b.message || b.rule || JSON.stringify(b))}</div>`).join('');
    const warns = (r.warns || []).map(b => `<div class="warn">提醒：${esc(b.message || b.rule || JSON.stringify(b))}</div>`).join('');
    const el = document.getElementById('rx_msg');
    if (el) el.innerHTML = (r.passed ? '<div class="ok">规则预检通过</div>' : '<div class="err">存在阻断项</div>') + blocks + warns;
    else toast(r.passed ? '规则预检通过' : '存在阻断项，见提示', r.passed ? 'ok' : 'err');
  } catch (e) { toast(e.message, 'err'); }
}

async function rxSubmit(id) {
  try { await api('/api/prescription/submit', { prescription_id: id }); toast('已提交', 'ok'); await rxLoad(); await rxOpen(id); }
  catch (e) { toast(e.message, 'err'); }
}

async function rxConfirm(id) {
  const cached = signPwdGet();
  const pwd = cached || prompt('确认处方需要签名口令：');
  if (!pwd) return;
  try {
    await api('/api/prescription/confirm',
      { prescription_id: id, password: pwd, pw_source: cached ? 'SESSION' : 'INPUT' });
    signPwdSet(pwd);
    toast('处方已确认', 'ok'); await rxLoad(); await rxOpen(id);
  } catch (e) {
    if (cached) signPwdClear();
    toast(e.message, 'err');
  }
}

async function rxVoid(id) {
  const reason = prompt('作废原因（必填）：');
  if (!reason) return;
  try { await api('/api/prescription/void', { prescription_id: id, reason: reason }); toast('已作废', 'ok'); await rxLoad(); }
  catch (e) { toast(e.message, 'err'); }
}

/* 药品 / 饮片目录建档（F32 / F110）——收进折叠区，日常开方不该被它挡住 */
function rxCatalogForm() {
  return `
  <details style="margin-top:10px"><summary class="hint">药品 / 饮片目录维护（缺药时才用）</summary>
  <div class="row" style="margin-top:6px">
    <select id="cat_kind" style="width:110px">
      <option value="drug">成药</option><option value="herb">饮片</option></select>
    <input id="cat_code" placeholder="编码" style="width:110px">
    <input id="cat_name" placeholder="名称" style="width:150px">
    <input id="cat_price" placeholder="单价：成药元/盒，饮片元/克" style="width:190px">
    <input id="cat_unit" placeholder="单位，如 盒" style="width:90px">
    <input id="cat_spec" placeholder="规格" style="width:120px">
    <button class="btn sm" onclick="rxCatCreate()">建档</button>
  </div>
  <div class="hint">价格都按「元」填：成药填一盒多少钱（如 13.5），饮片填一克多少钱（如 0.05）。</div>
  <div class="row">
    <input id="cat_id" placeholder="品目 ID（改资料用）" style="width:130px">
    <input id="cat_price2" placeholder="新单价（元）" style="width:150px">
    <button class="btn sm ghost" onclick="rxCatUpdate()">修改品目</button>
  </div>
  </details>`;
}

async function rxCatUpdate() {
  const id = parseInt(val('cat_id'));
  if (!id) { toast('填品目 ID', 'err'); return; }
  const price = parseFloat(val('cat_price2') || 0);
  try {
    if (val('cat_kind') === 'herb') {
      await api('/api/herb/update', { id: id, price_yuan_per_g: price });
    } else {
      await api('/api/drug/update', { id: id, price_fen: Math.round(price * 100) });
    }
    toast('品目已更新（改价不影响已开处方）', 'ok');
    await loadCatalog(true);
  } catch (e) { toast(e.message, 'err'); }
}

async function rxCatCreate() {
  const kind = val('cat_kind'), code = val('cat_code'), name = val('cat_name');
  if (!code || !name) { toast('编码与名称必填', 'err'); return; }
  const price = parseFloat(val('cat_price') || 0);
  try {
    if (kind === 'herb') {
      await api('/api/herb/create', { herb_code: code, name: name,
        price_yuan_per_g: price, unit: val('cat_unit') || 'g',
        spec: val('cat_spec') || null });
    } else {
      await api('/api/drug/create', { drug_code: code, generic_name: name,
        price_fen: Math.round(price * 100), unit: val('cat_unit') || '盒',
        spec: val('cat_spec') || null });
    }
    toast('已建档：' + name, 'ok');
    await loadCatalog(true);
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- 处方模板（F127） ---------- */
async function rxSaveAsTpl(id) {
  const name = prompt('模板名称：');
  if (!name) return;
  try { await api('/api/prescription/template/save_from', { prescription_id: id, name: name }); toast('已存为模板', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}

async function rxTplApply(id) {
  try {
    const tpls = (await api('/api/prescription/templates', {}, 'GET')).items || [];
    if (!tpls.length) { toast('暂无模板，可先「存为模板」', 'err'); return; }
    modal('套用处方模板', tpls.map(t => `<div class="row">
      <span style="flex:1">${esc(t.name)}（${esc(t.prescription_type || '')}）</span>
      <button class="btn sm" onclick="rxDoApply(${id},${t.id})">套用</button></div>`).join(''));
  } catch (e) { toast(e.message, 'err'); }
}

async function rxDoApply(rxId, tplId) {
  try { await api('/api/prescription/template/apply', { prescription_id: rxId, template_id: tplId }); toast('已套用模板', 'ok'); closeModal(); await rxOpen(rxId); }
  catch (e) { toast(e.message, 'err'); }
}

async function rxGuide(id) {
  const content = prompt('录入用药指导内容（如：饭后服用，忌生冷，疗程 7 天）：');
  if (!content) return;
  try {
    const r = await api('/api/prescription/guide', { prescription_id: id, content: content });
    toast('用药指导已保存（id=' + (r.id || '') + '）', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

async function rxPrint(id) {
  try {
    const j = await api('/api/print/prescription', { prescription_id: id });
    await api('/api/print/job/execute', { job_id: j.id });
    toast('处方笺已打印：' + j.print_job_no, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* ============================================================
   M05 检验检查（F27-F31 / F117-F118）全部外送
   ============================================================ */
const LB = { visitId: 0, orderId: 0, kw: '', items: [], agencies: [] };

async function viewLab() {
  let agencies = [], items = [];
  try {
    agencies = (await api('/api/lab/agency/list', {}, 'GET')).items || [];
  } catch (e) { /* 权限不足时降级为只读提示 */ }
  try {
    items = (await api('/api/lab/item/list', {}, 'GET')).items || [];
  } catch (e) {
    try { items = (await api('/api/lab/item/search', { keyword: '' }, 'GET')).items || []; }
    catch (e2) { }
  }
  try { await loadVisitChoices(); } catch (e) { }
  LB.items = items;
  LB.agencies = agencies;
  const SPEC_OPTS = ['血', '尿', '便', '痰', '分泌物', '组织', '其他'];
  const CAT_OPTS = ['检验', '病理', '影像', '功能'];
  const itemRows = items.map(x => `<tr>
    <td>${esc(x.name)}${x.short_name ? ' <span class="hint">' + esc(x.short_name) + '</span>' : ''}</td>
    <td>${esc(x.item_code)}</td>
    <td>${esc(x.category || '')}</td>
    <td>${esc(x.specimen_type || '')}</td>
    <td>${esc(yuan(x.price_fen))}</td>
    <td class="hint">${esc(yuan(x.settle_price_fen))}</td>
    <td>${x.report_days != null ? esc(x.report_days) + ' 天' : '-'}</td>
    <td>${x.is_active ? '<span class="tag ok">启用</span>' : '<span class="tag muted">停用</span>'}</td>
    <td><button class="btn sm ghost" onclick="lbItemEdit(${x.id})">修改</button></td>
  </tr>`).join('');
  return `
  <div class="card">
    <h3>检验检查（全部外送）</h3>
    <div class="row">
      ${visitSelectHtml('lb_visit', { selected: LB.visitId, onchange: 'lbLoad()' })}
      <button class="btn sm" onclick="lbLoad()">载入该就诊检验单</button>
      <span class="spacer"></span>
      <button class="btn ghost sm" onclick="lbRecList()">机构对账（F118）</button>
      <button class="btn ghost sm" onclick="lbReconcile()">生成对账单</button>
    </div>
    <div class="hint" style="margin-top:6px">
      已登记机构 ${agencies.length} 家 / 外送项目 ${items.length} 项。
      流程：开单 → 采样 → 送检 → 回报 → 录入结果 → 医生阅片。
      <span class="hint">检验项目本身通用，不按中西医区分。</span>
    </div>
    <div id="lb_mode" style="margin-top:4px"></div>
    <div id="lb_body"><div class="hint">先在上方选择本次就诊，再点「载入」。</div></div>
  </div>

  <div class="card">
    <h3>检验项目目录（F27）· 共 ${items.length} 项</h3>
    <details style="margin-bottom:8px">
      <summary class="hint">＋ 新增检验项目（点开填写，一次填完再提交）</summary>
      <div class="row" style="margin-top:8px">
        <input id="li_code" placeholder="项目编码，如 CRP" style="width:160px">
        <input id="li_name" placeholder="项目名称，如 C反应蛋白" style="width:220px">
        <select id="li_cat" style="width:110px">${CAT_OPTS.map(c => `<option>${c}</option>`).join('')}</select>
        <select id="li_spec" style="width:110px">${SPEC_OPTS.map(c => `<option>${c}</option>`).join('')}</select>
        <input id="li_short" placeholder="简称（可选）" style="width:130px">
      </div>
      <div class="row" style="margin-top:6px">
        <input id="li_price" placeholder="向患者收费 元" style="width:150px">
        <input id="li_settle" placeholder="与机构结算 元" style="width:150px">
        <input id="li_days" placeholder="出报告天数" style="width:130px">
        <input id="li_remark" placeholder="备注（可选）" style="flex:1">
        <button class="btn sm" onclick="lbItemCreate2()">添加项目</button>
      </div>
      <div class="hint">收费价是向患者收的，结算价是付给检验机构的成本，两者可以不同；差价就是这块的收入。</div>
    </details>
    <table class="tbl">
      <thead><tr><th>项目</th><th>编码</th><th>类别</th><th>标本</th><th>收费(元)</th>
        <th>结算(元)</th><th>报告</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${itemRows || '<tr><td colspan="9" class="hint">还没有检验项目，点上面「新增检验项目」添加</td></tr>'}</tbody>
    </table>

    <h3 style="margin-top:14px">外送机构 · 共 ${agencies.length} 家</h3>
    <div class="row">
      <input id="la_code" placeholder="机构编码" style="width:150px">
      <input id="la_name" placeholder="机构名称" style="width:220px">
      <input id="la_contact" placeholder="联系人" style="width:130px">
      <input id="la_phone" placeholder="电话" style="width:150px">
      <button class="btn sm ghost" onclick="lbAgencyCreate2()">新增机构</button>
    </div>
    <table class="tbl" style="margin-top:6px">
      <thead><tr><th>机构</th><th>编码</th><th>联系人</th><th>电话</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${agencies.map(a => `<tr>
        <td>${esc(a.name)}</td><td>${esc(a.agency_code)}</td>
        <td>${esc(a.contact || '')}</td><td>${esc(a.phone || '')}</td>
        <td>${a.is_active ? '<span class="tag ok">启用</span>' : '<span class="tag muted">停用</span>'}</td>
        <td><button class="btn sm ghost" onclick="lbAgencyEdit(${a.id})">修改</button></td>
      </tr>`).join('') || '<tr><td colspan="6" class="hint">还没有外送机构</td></tr>'}</tbody>
    </table>
  </div>`;
}

async function lbLoad() {
  LB.visitId = parseInt(val('lb_visit')) || 0;
  if (!LB.visitId) { toast('请先选择就诊', 'err'); return; }
  // 顶部标明本次就诊走的是中医还是西医，避免在错的流程里点点点
  api('/api/visit/get', { id: LB.visitId }, 'GET').then(v => {
    const el = document.getElementById('lb_mode');
    if (el) el.innerHTML = '<span class="hint">本次就诊：</span>' + MODE.chip(v);
  }).catch(() => { });
  try {
    const list = (await api('/api/lab/order/list', { visit_id: LB.visitId }, 'GET')).items || [];
    const catItems = (await api('/api/lab/item/search', { keyword: '' }, 'GET')).items || [];
    const rows = list.map(o => `<tr>
      <td>${esc(o.order_no)}</td><td>${statusTag(o.status)}</td>
      <td>${o.total_amount != null ? (o.total_amount / 100).toFixed(2) + ' 元' : '-'}</td>
      <td>${esc(o.created_at || '')}</td>
      <td>
        <button class="btn sm ghost" onclick="lbOpen(${o.id})">详情</button>
        ${['ORDERED'].includes(o.status) ? `<button class="btn sm" onclick="lbCollect(${o.id})">采样</button>` : ''}
        ${['SAMPLED'].includes(o.status) ? `<button class="btn sm" onclick="lbDeliver(${o.id})">送检</button>` : ''}
        ${['DELIVERED', 'REPORTED'].includes(o.status) ? `<button class="btn sm" onclick="lbResult(${o.id})">录入结果</button>` : ''}
        ${['REPORTED'].includes(o.status) ? `<button class="btn sm" onclick="lbReview(${o.id})">阅片</button>` : ''}
        ${['DRAFT', 'ORDERED', 'SAMPLED'].includes(o.status) ? `<button class="btn sm warn" onclick="lbCancel(${o.id})">作废</button>` : ''}
      </td></tr>`).join('');
    const itemBoxes = catItems.map(i => `<label class="chk"><input type="checkbox" class="lb_item_chk" value="${i.id}">
        ${esc(i.name)} <span class="hint">${yuan(i.price_fen)} 元</span></label>`).join('');
    $('#lb_body').innerHTML = `
      <div class="section-title" style="margin-top:12px">新建检验申请</div>
      <div class="row">
        <input id="lb_note" style="flex:1" placeholder="临床诊断 / 送检目的">
      </div>
      <div class="hint" style="margin:4px 0 6px">勾选检验项目（可多选）：</div>
      <div class="row" id="lb_items" style="gap:6px 16px">
        ${itemBoxes || '<span class="hint">还没有检验项目，先点右上「新增项目」</span>'}
      </div>
      <div class="row"><button class="btn" onclick="lbCreate()">开单并提交</button></div>
      <div class="section-title" style="margin-top:12px">本就诊检验单（${list.length}）</div>
      <table class="tbl"><thead><tr><th>申请号</th><th>状态</th><th>金额</th><th>开单时间</th><th>操作</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="hint">无</td></tr>'}</tbody></table>
      <div id="lb_detail"></div>`;
  } catch (e) { toast(e.message, 'err'); }
}

async function lbCreate() {
  const ids = Array.from(document.querySelectorAll('.lb_item_chk:checked'))
    .map(el => parseInt(el.value)).filter(Boolean);
  if (!ids.length) { toast('请至少勾选一个检验项目', 'err'); return; }
  try {
    const o = await api('/api/lab/order/create', { visit_id: LB.visitId, clinical_note: val('lb_note') });
    if (ids.length) await api('/api/lab/order/save/items', { lab_order_id: o.id, items: ids.map(i => ({ lab_item_id: i })) });
    await api('/api/lab/order/submit', { lab_order_id: o.id });
    toast('已开单 ' + o.order_no, 'ok'); await lbLoad();
  } catch (e) { toast(e.message, 'err'); }
}
async function lbOpen(id) {
  LB.orderId = id;
  try {
    const o = await api('/api/lab/order/get', { lab_order_id: id }, 'GET');
    const res = (o.results || []).map(r => `<tr><td>${esc(r.item_name)}</td><td>${esc(r.result_text || '')}</td>
      <td>${esc(r.unit || '')}</td><td>${esc(r.abnormal_flag || '')}</td><td>${esc(r.reported_at || '')}</td></tr>`).join('');
    $('#lb_detail').innerHTML = `
      <div class="section-title">${esc(o.order_no)} ${statusTag(o.status)}</div>
      <div class="kv">
        <div class="k">项目</div><div>${(o.items || []).map(i => esc(i.item_name_snap)).join('、') || '-'}</div>
        <div class="k">标本</div><div>${(o.specimens || []).map(s => esc(s.barcode) + '·' + esc(s.status)).join('，') || '-'}</div>
        <div class="k">送检</div><div>${o.delivery ? esc(o.delivery.status) : '未送检'}</div>
      </div>
      <table class="tbl" style="margin-top:8px"><thead><tr><th>项目</th><th>结果</th><th>单位</th><th>异常</th><th>回报时间</th></tr></thead>
      <tbody>${res || '<tr><td colspan="5" class="hint">尚无结果</td></tr>'}</tbody></table>
      <div class="row" style="margin-top:8px">
        <input id="lb_dvid" placeholder="送检记录 ID" style="width:130px" value="${o.delivery ? o.delivery.id : ''}">
        <button class="btn sm ghost" onclick="lbDeliverReturn()">登记机构回报</button>
        <input id="lb_att" placeholder="报告文件路径" style="flex:1">
        <button class="btn sm ghost" onclick="lbAttach(${id})">登记报告附件</button>
        <button class="btn sm ghost" onclick="lbSpecimens(${id})">标本列表</button>
      </div>
      <div id="lb_more" class="hint"></div>`;
  } catch (e) { toast(e.message, 'err'); }
}
async function lbDeliverReturn() {
  const id = parseInt(val('lb_dvid')); if (!id) { toast('填送检记录 ID', 'err'); return; }
  try { await api('/api/lab/delivery/return', { delivery_id: id }); toast('已登记回报', 'ok'); await lbLoad(); }
  catch (e) { toast(e.message, 'err'); }
}
async function lbAttach(oid) {
  const p = val('lb_att'); if (!p) { toast('填报告文件路径', 'err'); return; }
  try { await api('/api/lab/result/attach', { lab_order_id: oid, file_path: p }); toast('附件已登记', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function lbSpecimens(oid) {
  try {
    const items = (await api('/api/lab/specimen/list', { lab_order_id: oid }, 'GET')).items || [];
    $('#lb_more').innerHTML = items.length
      ? items.map(s => `<span class="btn sm ghost" style="margin:2px">${esc(s.barcode)} · ${esc(s.status)}</span>`).join('')
      : '无标本';
  } catch (e) { toast(e.message, 'err'); }
}
async function lbCollect(id) { try { await api('/api/lab/specimen/collect', { lab_order_id: id }); toast('已采样', 'ok'); await lbLoad(); } catch (e) { toast(e.message, 'err'); } }
async function lbDeliver(id) { try { await api('/api/lab/delivery/create', { lab_order_id: id }); toast('已送检', 'ok'); await lbLoad(); } catch (e) { toast(e.message, 'err'); } }
async function lbReview(id) { try { await api('/api/lab/result/review', { lab_order_id: id, note: '医生已阅' }); toast('已阅片', 'ok'); await lbLoad(); } catch (e) { toast(e.message, 'err'); } }
async function lbCancel(id) {
  const reason = prompt('作废原因（必填）：'); if (!reason) return;
  try { await api('/api/lab/order/cancel', { lab_order_id: id, reason: reason }); toast('已作废', 'ok'); await lbLoad(); } catch (e) { toast(e.message, 'err'); }
}
async function lbResult(id) {
  const name = prompt('结果项目名称：', '白细胞'); if (!name) return;
  const text = prompt('结果值：', ''); if (text === null) return;
  const unit = prompt('单位（可空）：', '') || null;
  const summary = prompt('结论/提示（可空）：', '') || null;
  try { await api('/api/lab/result/save', { lab_order_id: id, items: [{ item_name: name, result_text: text, unit: unit, result_summary: summary }] }); toast('结果已录入', 'ok'); await lbLoad(); await lbOpen(id); }
  catch (e) { toast(e.message, 'err'); }
}
/* 机构对账（F118） */
async function lbReconcile() {
  const ag = prompt('对账机构 ID：'); if (!ag) return;
  const from = prompt('对账起始日（YYYY-MM-DD）：'); if (!from) return;
  const to = prompt('对账截止日（YYYY-MM-DD）：'); if (!to) return;
  try { const r = await api('/api/lab/reconcile', { agency_id: parseInt(ag), period_from: from, period_to: to });
    toast('对账单已生成：' + (r.reconcile_no || r.id || ''), 'ok'); lbRecList(); }
  catch (e) { toast(e.message, 'err'); }
}
async function lbRecList() {
  try {
    const items = (await api('/api/lab/reconcile/list', {}, 'GET')).items || [];
    $('#lb_more').innerHTML = items.length
      ? `<table class="tbl"><thead><tr><th>对账单</th><th>机构</th><th>期间</th><th>金额</th><th>状态</th><th></th></tr></thead><tbody>${
        items.map(r => `<tr><td>${esc(r.reconcile_no || r.id)}</td><td>${esc(r.agency_id)}</td>
          <td>${esc(r.period_from || '')}~${esc(r.period_to || '')}</td>
          <td>${((r.amount_fen || 0) / 100).toFixed(2)}</td><td>${esc(r.status)}</td>
          <td>${r.status !== 'SETTLED' ? `<button class="btn sm ghost" onclick="lbRecSettle(${r.id})">结清</button>` : ''}</td></tr>`).join('')}</tbody></table>`
      : '无对账单';
  } catch (e) { toast(e.message, 'err'); }
}
async function lbRecSettle(id) {
  try { await api('/api/lab/reconcile/settle', { id: id }); toast('已结清', 'ok'); lbRecList(); }
  catch (e) { toast(e.message, 'err'); }
}

async function lbNewAgency() {
  const code = prompt('机构编码：'); if (!code) return;
  const name = prompt('机构名称：'); if (!name) return;
  try { await api('/api/lab/agency/create', { agency_code: code, name: name }); toast('已新增机构', 'ok'); render(); } catch (e) { toast(e.message, 'err'); }
}
async function lbNewItem() {
  const code = prompt('项目编码：'); if (!code) return;
  const name = prompt('项目名称：'); if (!name) return;
  const price = prompt('收费价（元）：', '30');
  const settle = prompt('结算价（元）：', '18');
  const days = prompt('出报告天数：', '1');
  try {
    await api('/api/lab/item/create', { item_code: code, name: name,
      price_fen: Math.round(parseFloat(price || 0) * 100),
      settle_price_fen: Math.round(parseFloat(settle || 0) * 100),
      report_days: parseInt(days) || 1, specimen_type: 'BLOOD' });
    toast('已新增项目', 'ok'); render();
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- 检验项目 / 机构维护（F27）：列表 + 表单，不再连环弹 prompt ---------- */
async function lbItemCreate2() {
  const code = (val('li_code') || '').trim();
  const name = (val('li_name') || '').trim();
  if (!code || !name) { toast('项目编码与名称为必填', 'err'); return; }
  try {
    await api('/api/lab/item/create', {
      item_code: code, name: name,
      short_name: (val('li_short') || '').trim() || null,
      category: val('li_cat'), specimen_type: val('li_spec'),
      price_fen: Math.round(parseFloat(val('li_price') || 0) * 100),
      settle_price_fen: Math.round(parseFloat(val('li_settle') || 0) * 100),
      report_days: parseInt(val('li_days'), 10) || null,
      remark: (val('li_remark') || '').trim() || null,
    });
    toast('已添加检验项目：' + name, 'ok');
    render();
  } catch (e) { toast(e.message, 'err'); }
}

function lbItemEdit(id) {
  const it = (LB.items || []).find(x => x.id === id);
  if (!it) { toast('项目不存在，请刷新', 'err'); return; }
  modal('修改检验项目 · ' + it.name, `
    <div class="grid3">
      <div><label>名称</label><input id="ei_name" value="${esc(it.name)}"></div>
      <div><label>类别</label><input id="ei_cat" value="${esc(it.category || '')}"></div>
      <div><label>标本类型</label><input id="ei_spec" value="${esc(it.specimen_type || '')}"></div>
      <div><label>向患者收费（元）</label><input id="ei_price" value="${((it.price_fen || 0) / 100).toFixed(2)}"></div>
      <div><label>与机构结算（元）</label><input id="ei_settle" value="${((it.settle_price_fen || 0) / 100).toFixed(2)}"></div>
      <div><label>出报告天数</label><input id="ei_days" value="${it.report_days != null ? it.report_days : ''}"></div>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="btn" onclick="lbItemSave(${id})">保存</button>
      <button class="btn ${it.is_active ? 'warn' : 'ghost'}"
        onclick="lbItemToggle(${id},${it.is_active ? 0 : 1})">${it.is_active ? '停用此项目' : '重新启用'}</button>
    </div>
    <div class="hint">改价只影响此后新开的检验单；已开单按下单时的价格计费，不会追溯改账。</div>`);
}

async function lbItemSave(id) {
  try {
    await api('/api/lab/item/update', {
      id: id,
      name: (val('ei_name') || '').trim() || null,
      category: val('ei_cat'), specimen_type: val('ei_spec'),
      price_yuan: parseFloat(val('ei_price') || 0),
      settle_price_yuan: parseFloat(val('ei_settle') || 0),
      report_days: parseInt(val('ei_days'), 10) || null,
    });
    toast('已保存', 'ok');
    closeModal();
    render();
  } catch (e) { toast(e.message, 'err'); }
}

async function lbItemToggle(id, active) {
  try {
    await api('/api/lab/item/update', { id: id, is_active: active });
    toast(active ? '已重新启用' : '已停用（历史检验单不受影响）', 'ok');
    closeModal();
    render();
  } catch (e) { toast(e.message, 'err'); }
}

async function lbAgencyCreate2() {
  const code = (val('la_code') || '').trim();
  const name = (val('la_name') || '').trim();
  if (!code || !name) { toast('机构编码与名称为必填', 'err'); return; }
  try {
    await api('/api/lab/agency/create', {
      agency_code: code, name: name,
      contact: (val('la_contact') || '').trim() || null,
      phone: (val('la_phone') || '').trim() || null,
    });
    toast('已新增机构：' + name, 'ok');
    render();
  } catch (e) { toast(e.message, 'err'); }
}

function lbAgencyEdit(id) {
  const a = (LB.agencies || []).find(x => x.id === id);
  if (!a) { toast('机构不存在，请刷新', 'err'); return; }
  modal('修改外送机构 · ' + a.name, `
    <div class="grid3">
      <div><label>名称</label><input id="ea_name" value="${esc(a.name)}"></div>
      <div><label>联系人</label><input id="ea_contact" value="${esc(a.contact || '')}"></div>
      <div><label>电话</label><input id="ea_phone" value="${esc(a.phone || '')}"></div>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="btn" onclick="lbAgencySave(${id})">保存</button>
      <button class="btn ${a.is_active ? 'warn' : 'ghost'}"
        onclick="lbAgencyToggle(${id},${a.is_active ? 0 : 1})">${a.is_active ? '停用此机构' : '重新启用'}</button>
    </div>`);
}

async function lbAgencySave(id) {
  try {
    await api('/api/lab/agency/update', {
      id: id,
      name: (val('ea_name') || '').trim() || null,
      contact: val('ea_contact'),
      phone: val('ea_phone'),
    });
    toast('已保存', 'ok');
    closeModal();
    render();
  } catch (e) { toast(e.message, 'err'); }
}

async function lbAgencyToggle(id, active) {
  try {
    await api('/api/lab/agency/update', { id: id, is_active: active });
    toast(active ? '已启用' : '已停用', 'ok');
    closeModal();
    render();
  } catch (e) { toast(e.message, 'err'); }
}

/* ============================================================
   M10 打印与硬件（F66-F75）
   ============================================================ */
async function viewPrint() {
  let jobs = [], devs = [], tpls = [], fails = [];
  try { jobs = (await api('/api/print/job/list', { limit: 30 }, 'GET')).items || []; } catch (e) { }
  try { devs = (await api('/api/hardware/devices', {}, 'GET')).items || []; } catch (e) { }
  try { tpls = (await api('/api/print/templates', {}, 'GET')).items || []; } catch (e) { }
  try { fails = (await api('/api/print/failures', {}, 'GET')).items || []; } catch (e) { }
  try { await loadVisitChoices(); } catch (e) { }
  const st = { PENDING: '待打印', PRINTING: '打印中', SUCCESS: '成功', FAILED: '失败', RETRYING: '重试中', ABANDONED: '已放弃' };
  const rows = jobs.map(j => `<tr>
    <td>${esc(j.print_job_no)}</td><td>${esc(j.biz_type || '')}</td>
    <td>${esc(st[j.status] || j.status)}</td><td>${j.reprint_count || 0}</td>
    <td>${esc(j.printed_at || '')}</td>
    <td>
      ${['PENDING', 'FAILED'].includes(j.status) ? `<button class="btn sm" onclick="prExec(${j.id})">执行</button>` : ''}
      ${['SUCCESS'].includes(j.status) ? `<button class="btn sm ghost" onclick="prReprint(${j.id})">重打</button>` : ''}
      ${['FAILED'].includes(j.status) ? `<button class="btn sm ghost" onclick="prRetry(${j.id})">重试</button>` : ''}
      ${['FAILED', 'PENDING'].includes(j.status) ? `<button class="btn sm warn" onclick="prAbandon(${j.id})">放弃</button>` : ''}
      <button class="btn sm ghost" onclick="prDetail(${j.id})">详情</button>
      <button class="btn sm ghost" onclick="prBrowserPrint(${j.id})">打印</button>
    </td></tr>`).join('');
  const devRows = devs.map(d => `<tr><td>${esc(d.device_name || d.driver_code)}</td><td>${esc(d.driver_code)}</td>
    <td>${statusTag(d.status)}</td><td>${esc(d.last_seen_at || '')}</td></tr>`).join('');
  return `
  <div class="card">
    <h3>打印与硬件（M10）</h3>
    <div class="row">
      ${visitSelectHtml('pr_visit', { selected: 0, onchange: 'prVisitChanged()' })}
      <select id="pr_rx" class="pick"><option value="">— 处方 —</option></select>
      <button class="btn ghost sm" onclick="prRx()">打处方笺</button>
      <button class="btn ghost sm" onclick="prLabel()">打药袋标签</button>
    </div>
    <div class="row">
      <select id="pr_bill" class="pick"><option value="">— 账单 —</option></select>
      <button class="btn ghost sm" onclick="prBill()">打收费凭条</button>
      <span class="spacer"></span>
      <button class="btn ghost sm" onclick="prHealth()">硬件自检</button>
      <button class="btn ghost sm" onclick="prScan()">扫码</button>
      <button class="btn ghost sm" onclick="prIdCard()">读身份证</button>
    </div>
    <div class="section-title" style="margin-top:12px">打印任务（最近 ${jobs.length}）</div>
    <table class="tbl"><thead><tr><th>任务号</th><th>类型</th><th>状态</th><th>重打</th><th>打印时间</th><th>操作</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="hint">无</td></tr>'}</tbody></table>
    <div class="section-title" style="margin-top:12px">设备（${devs.length}）</div>
    <table class="tbl"><thead><tr><th>设备</th><th>驱动</th><th>状态</th><th>最后在线</th></tr></thead>
    <tbody>${devRows || '<tr><td colspan="4" class="hint">无</td></tr>'}</tbody></table>
    <div class="section-title" style="margin-top:12px">模板（${tpls.length}）· 失败记录（${fails.length}）</div>
    <div class="hint">${tpls.map(t => esc(t.template_code) + '(' + esc(t.biz_type) + ')').join('、') || '无模板'}
      ${fails.length ? '　最近失败：' + esc(String(fails[0].message || fails[0].error_code || '')) : ''}</div>
    <div class="section-title" style="margin-top:12px">硬件事件与设备登记</div>
    <div class="row">
      <input id="hw_code" placeholder="驱动码，如 mock.printer" style="width:200px">
      <input id="hw_name" placeholder="设备名称" style="width:160px">
      <button class="btn sm ghost" onclick="prDevRegister()">登记设备</button>
      <button class="btn sm ghost" onclick="prEvents()">硬件事件</button>
      <button class="btn sm ghost" onclick="prCheckRx()">扫码核对处方</button>
    </div>
    <div id="pr_more" class="hint"></div>
    <div id="pr_msg" class="hint" style="margin-top:8px"></div>
  </div>`;
}
async function prVisitChanged() {
  const vid = val('pr_visit');
  await fillRxSelect('pr_rx', vid);
  await fillBillSelect('pr_bill', vid);
}

async function prDevRegister() {
  const code = val('hw_code'); if (!code) { toast('填驱动码', 'err'); return; }
  try { await api('/api/hardware/device/register', { driver_code: code,
    device_name: val('hw_name') || code }); toast('设备已登记', 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}
async function prEvents() {
  try {
    const items = (await api('/api/hardware/events', { limit: 50 }, 'GET')).items || [];
    $('#pr_more').innerHTML = items.length
      ? `<table class="tbl"><thead><tr><th>时间</th><th>驱动</th><th>事件</th><th>结果</th><th>说明</th></tr></thead><tbody>${
        items.map(e => `<tr><td>${esc(e.occurred_at || '')}</td><td>${esc(e.driver_code)}</td>
          <td>${esc(e.event_type)}</td><td>${esc(e.result)}</td><td>${esc(e.summary || '')}</td></tr>`).join('')}</tbody></table>`
      : '无硬件事件';
  } catch (e) { toast(e.message, 'err'); }
}
async function prCheckRx() {
  const id = parseInt(val('pr_rx')); if (!id) { toast('请先在上方选择处方', 'err'); return; }
  // 后端要求 prescription_id + code（扫到的条码/编码），只传处方号会被拒
  const code = prompt('扫描/输入药品或饮片条码：');
  if (!code) return;
  try {
    const r = await api('/api/hardware/check_prescription',
      { prescription_id: id, code: code });
    prMsg('核对结果：匹配=' + (r.matched ? '是' : '否') + ' ' + esc(JSON.stringify(r)));
  } catch (e) { toast(e.message, 'err'); }
}
function prMsg(t) { const el = document.getElementById('pr_msg'); if (el) el.innerHTML = t; }
async function prRx() { const id = parseInt(val('pr_rx')); if (!id) return toast('请先选择处方', 'err'); try { const j = await api('/api/print/prescription', { prescription_id: id }); await api('/api/print/job/execute', { job_id: j.id }); toast('已打印 ' + j.print_job_no, 'ok'); render(); } catch (e) { toast(e.message, 'err'); } }
async function prBill() { const id = parseInt(val('pr_bill')); if (!id) return toast('请先选择账单', 'err'); try { const j = await api('/api/print/receipt', { bill_id: id }); await api('/api/print/job/execute', { job_id: j.id }); toast('已打印 ' + j.print_job_no, 'ok'); render(); } catch (e) { toast(e.message, 'err'); } }
async function prLabel() { const id = parseInt(val('pr_rx')); if (!id) return toast('请先选择处方', 'err'); try { const j = await api('/api/print/label', { prescription_id: id }); await api('/api/print/job/execute', { job_id: j.id }); toast('已打印 ' + j.print_job_no, 'ok'); render(); } catch (e) { toast(e.message, 'err'); } }
async function prExec(id) { try { await api('/api/print/job/execute', { job_id: id }); toast('已执行', 'ok'); render(); } catch (e) { toast(e.message, 'err'); } }
async function prReprint(id) { const r = prompt('重打原因：', '补打'); if (r === null) return; try { const x = await api('/api/print/job/reprint', { job_id: id, reason: r }); toast('已重打，单号不变：' + x.print_job_no, 'ok'); render(); } catch (e) { toast(e.message, 'err'); } }
async function prRetry(id) { try { const x = await api('/api/print/job/retry', { job_id: id }); toast(x.abandoned ? '重试超限已放弃' : '重试成功', x.abandoned ? 'err' : 'ok'); render(); } catch (e) { toast(e.message, 'err'); } }
async function prAbandon(id) { const r = prompt('放弃原因：', ' '); if (r === null) return; try { await api('/api/print/job/abandon', { job_id: id, reason: r }); toast('已放弃', 'ok'); render(); } catch (e) { toast(e.message, 'err'); } }
async function prDetail(id) {
  try {
    const j = await api('/api/print/job/get', { id: id }, 'GET');
    modal('打印任务 ' + j.print_job_no,
      `<pre style="white-space:pre-wrap;font-size:12px;max-height:340px;overflow:auto">${esc(j.rendered_text || '')}</pre>
       <div class="row" style="margin-top:8px">
         <button class="btn sm" onclick="prBrowserPrint(${id})">用浏览器打印这一张</button>
       </div>`);
  } catch (e) { toast(e.message, 'err'); }
}

/* 真实打印通道：把任务内容渲染成 A5 打印页，交给系统打印对话框。
   硬件驱动目前只有 mock（落盘），这条路径不依赖任何特殊驱动，普通打印机即可。 */
async function prBrowserPrint(id) {
  try {
    const j = await api('/api/print/job/get', { id: id }, 'GET');
    const text = j.rendered_text || '';
    if (!text) { toast('该任务没有可打印内容', 'err'); return; }
    const w = window.open('', '_blank', 'width=820,height=920');
    if (!w) { toast('浏览器拦截了弹窗，请允许弹窗后重试', 'err'); return; }
    w.document.write('<!doctype html><html><head><meta charset="utf-8">'
      + '<title>' + esc(j.print_job_no) + '</title>'
      + '<style>body{font-family:"Microsoft YaHei","SimSun",monospace;padding:12mm;'
      + 'font-size:13px;line-height:1.65;color:#000}'
      + 'pre{white-space:pre-wrap;word-break:break-all;font-family:inherit;margin:0}'
      + '@page{size:A5;margin:8mm}@media print{body{padding:0}}</style></head>'
      + '<body><pre id="pp"></pre></body></html>');
    w.document.getElementById('pp').textContent = text;
    w.document.close();
    w.focus();
    setTimeout(() => { try { w.print(); } catch (e) { } }, 350);
  } catch (e) { toast(e.message, 'err'); }
}
async function prHealth() { try { const r = await api('/api/hardware/health', {}, 'GET'); prMsg('自检：' + (r.items || []).map(i => esc(i.driver) + '=' + (i.online ? '在线' : '离线')).join('，')); } catch (e) { toast(e.message, 'err'); } }
async function prScan() { try { const r = await api('/api/hardware/scan', {}); prMsg('扫码结果：' + esc(r.code || JSON.stringify(r))); } catch (e) { toast(e.message, 'err'); } }
async function prIdCard() { try { const r = await api('/api/hardware/id_card/read', {}); prMsg('身份证：' + esc(r.name || '') + ' ' + esc(r.id_card_no || '')); } catch (e) { toast(e.message, 'err'); } }

/* ============================================================
   M04 AI 与语音（F20-F26）
   ============================================================ */
const AI = { visitId: 0, taskId: 0 };
async function viewAi() {
  let tasks = [], drafts = [];
  try { tasks = (await api('/api/ai/task/list', { limit: 20 }, 'GET')).items || []; } catch (e) { }
  try { drafts = (await api('/api/ai/draft/list', { limit: 20 }, 'GET')).items || []; } catch (e) { }
  try { await loadVisitChoices(); } catch (e) { }
  const rows = tasks.map(t => `<tr><td>${esc(t.task_no)}</td><td>${esc(t.task_type)}</td>
    <td>${statusTag(t.status)}</td><td>${esc(t.created_at || '')}</td>
    <td>${['FAILED', 'ABANDONED'].includes(t.status) ? `<button class="btn sm ghost" onclick="aiRetry(${t.id})">重试</button>` : ''}
        <button class="btn sm ghost" onclick="aiTaskGet(${t.id})">详情</button></td></tr>`).join('');
  const drows = drafts.map(d => `<tr><td>${esc(d.draft_no || d.id)}</td><td>${esc(d.source || '')}</td>
    <td>${esc(d.status || '')}</td>
    <td><button class="btn sm ghost" onclick="aiDraft(${d.id})">查看</button>
        <button class="btn sm" onclick="aiApply(${d.id})">采纳入病历</button>
        <button class="btn sm warn" onclick="aiReject(${d.id})">驳回</button></td></tr>`).join('');
  return `
  <div class="card">
    <h3>AI 与语音（M04）</h3>
    <div class="hint">AI 只生成草稿，**医生确认后才写入病历**，落库时标记来源。</div>
    <div class="row" style="margin-top:8px">
      ${visitSelectHtml('ai_visit', { selected: AI.visitId, onchange: 'aiVisitChanged()' })}
      <button class="btn" onclick="aiAsr()">语音转写</button>
      <button class="btn ghost" onclick="aiExtract()">要素抽取</button>
      <button class="btn ghost" onclick="aiDraftCreate()">生成病历草稿</button>
      <button class="btn ghost" onclick="aiRisk()">风险提醒</button>
      <button class="btn ghost" onclick="aiSuggest()">用药建议</button>
    </div>
    <div class="row" style="margin-top:6px">
      <input id="ai_audio" placeholder="录音文件路径" style="flex:1">
      <button class="btn sm ghost" onclick="aiAudio()">登记录音（F20）</button>
      <input id="ai_alert" placeholder="风险提醒 ID" style="width:120px">
      <button class="btn sm ghost" onclick="aiRiskAck()">确认风险已处置</button>
    </div>
    <div class="section-title" style="margin-top:12px">任务（${tasks.length}）</div>
    <table class="tbl"><thead><tr><th>任务号</th><th>类型</th><th>状态</th><th>时间</th><th>操作</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="5" class="hint">无</td></tr>'}</tbody></table>
    <div class="section-title" style="margin-top:12px">病历草稿（${drafts.length}）</div>
    <table class="tbl"><thead><tr><th>草稿</th><th>来源</th><th>状态</th><th>操作</th></tr></thead>
    <tbody>${drows || '<tr><td colspan="4" class="hint">无</td></tr>'}</tbody></table>
    <div id="ai_msg" class="hint" style="margin-top:8px"></div>
  </div>`;
}
function aiVisitChanged() { AI.visitId = parseInt(val('ai_visit')) || 0; }

function aiMsg(t) { const el = document.getElementById('ai_msg'); if (el) el.innerHTML = t; else toast(t, 'ok'); }
async function aiTask(type) {
  AI.visitId = parseInt(val('ai_visit')) || 0;
  if (!AI.visitId) { toast('请先选择就诊', 'err'); return null; }
  const t = await api('/api/ai/task/create', { task_type: type, visit_id: AI.visitId });
  AI.taskId = t.id; return t.id;
}
async function aiAsr() {
  const text = prompt('口述内容（本地规则引擎转写）：'); if (text === null) return;
  try { const id = await aiTask('ASR'); if (!id) return;
    const r = await api('/api/ai/transcript/save', { ai_task_id: id, text: text });
    aiMsg('转写完成：' + esc(String(r.text || text))); render();
  } catch (e) { toast(e.message, 'err'); }
}
async function aiExtract() { try { const id = await aiTask('EXTRACT'); if (!id) return; const r = await api('/api/ai/extract', { ai_task_id: id }); aiMsg('抽出要素 ' + ((r.facts || []).length) + ' 条'); render(); } catch (e) { toast(e.message, 'err'); } }
async function aiDraftCreate() { try { const id = await aiTask('DRAFT'); if (!id) return; const r = await api('/api/ai/draft/create', { ai_task_id: id }); aiMsg('草稿已生成 #' + r.id); render(); } catch (e) { toast(e.message, 'err'); } }
async function aiRisk() { try { const id = await aiTask('RISK'); if (!id) return; const r = await api('/api/ai/risk/check', { ai_task_id: id }); aiMsg('风险项 ' + ((r.alerts || r.items || []).length) + ' 条'); render(); } catch (e) { toast(e.message, 'err'); } }
/* ---- 锁屏 / 解锁（F100 隐私合规）---- */
async function doLock() {
  try { await api('/api/auth/lock', {}); toast('已锁屏', 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}
async function doUnlock() {
  const pwd = prompt('解锁请输入登录口令：'); if (!pwd) return;
  try { await api('/api/auth/unlock', { password: pwd }); toast('已解锁', 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}

async function aiAudio() {
  const p = val('ai_audio'); if (!p) { toast('填录音文件路径', 'err'); return; }
  AI.visitId = parseInt(val('ai_visit')) || 0;
  try { await api('/api/ai/audio/register', { file_path: p, visit_id: AI.visitId || null }); toast('录音已登记（只存路径，不存音频本体）', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function aiSuggest() {
  try { const id = await aiTask('SUGGEST'); if (!id) return;
    const r = await api('/api/ai/suggest/create', { ai_task_id: id });
    aiMsg('用药建议已生成；采纳会走正常开方流程，规则照跑'); render();
  } catch (e) { toast(e.message, 'err'); }
}
async function aiRiskAck() {
  const id = parseInt(val('ai_alert')); if (!id) { toast('填风险提醒 ID', 'err'); return; }
  const note = prompt('处置说明：') || '已知悉';
  try { await api('/api/ai/risk/ack', { alert_id: id, note: note }); toast('已确认', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function aiSuggestAdopt(id) {
  const rx = prompt('采纳到处方 ID（会走正常开方流程与规则校验）：');
  if (!rx) return;
  try { await api('/api/ai/suggest/adopt', { suggestion_id: id, prescription_id: parseInt(rx) }); toast('已采纳', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function aiRetry(id) { try { await api('/api/ai/task/retry', { ai_task_id: id }); toast('已重新入队', 'ok'); render(); } catch (e) { toast(e.message, 'err'); } }
async function aiTaskGet(id) { try { const t = await api('/api/ai/task/get', { id: id }, 'GET'); modal('AI 任务 ' + t.task_no, `<pre style="white-space:pre-wrap;font-size:12px;max-height:400px;overflow:auto">${esc(JSON.stringify(t, null, 2))}</pre>`); } catch (e) { toast(e.message, 'err'); } }
async function aiDraft(id) {
  // 后端没有单独的 draft/get，从列表里取（草稿必须先看得见，才谈得上采纳或驳回）
  try {
    const d = (await api('/api/ai/draft/list', { limit: 50 }, 'GET')).items.find(x => x.id == id);
    if (!d) { toast('草稿不存在或已被处理', 'err'); return; }
    modal('病历草稿 #' + id, `<pre style="white-space:pre-wrap;font-size:12px;max-height:400px;overflow:auto">${esc(JSON.stringify(d, null, 2))}</pre>`);
  } catch (e) { toast(e.message, 'err'); }
}
async function aiApply(id) {
  const vid = parseInt(val('ai_visit')) || AI.visitId || 0;
  if (!vid) { toast('请先选择就诊', 'err'); return; }
  try {
    const r = await api('/api/record/get', { visit_id: vid }, 'GET');
    if (!r || r.exists === false) { toast('该就诊还没有病历，请先到病历页接诊', 'err'); return; }
    await api('/api/ai/draft/apply', { draft_id: id, record_id: r.id });
    toast('已采纳入病历（来源标记为 AI 已确认）', 'ok'); render();
  } catch (e) { toast(e.message, 'err'); }
}
async function aiReject(id) { const r = prompt('驳回原因：'); if (!r) return; try { await api('/api/ai/draft/reject', { draft_id: id, reason: r }); toast('已驳回', 'ok'); render(); } catch (e) { toast(e.message, 'err'); } }

async function showStatusLog(rid) {
  try {
    const r = await api('/api/record/get', { id: rid }, 'GET');
    const rows = (r.status_log || []).map(l => `<tr><td>${esc(l.from_status || '—')}</td><td>${esc(l.to_status)}</td><td>${esc(l.occurred_at)}</td><td>${esc(l.reason || '')}</td></tr>`).join('');
    modal('病历状态流转日志', `<table><thead><tr><th>从</th><th>到</th><th>时间</th><th>原因</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="empty">无记录</td></tr>'}</tbody></table>`);
  } catch (e) { toast('读取状态日志失败：' + e.message, 'err'); }
}
async function showAudit(rid) {
  try {
    const d = await api('/api/audit/data_changes', { record_id: String(rid), limit: 100 }, 'GET');
    const rows = d.items.map(a => `<tr><td>${esc(a.table_name)}</td><td>${esc(a.field_name)}</td><td>${esc(a.before_value || '—')}</td><td>${esc(a.after_value || '—')}</td><td>${esc(a.occurred_at)}</td></tr>`).join('');
    modal('字段级修改审计（F85）', `<table><thead><tr><th>表</th><th>字段</th><th>修改前</th><th>修改后</th><th>时间</th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="empty">无记录</td></tr>'}</tbody></table>`);
  } catch (e) { toast('读取审计失败：' + e.message, 'err'); }
}

/* ---------- 系统管理 ---------- */
function toggleSignRemember(on) {
  localStorage.setItem('clinic_sign_remember', on ? '1' : '0');
  if (!on) signPwdClear();
  const el = document.getElementById('sign_remember_state');
  if (el) el.textContent = on ? '已开启：签名不再重复输口令' : '已关闭：每次签名都要输口令';
}

async function viewSystem() {
  let html = `
  <div class="card">
    <h3>系统状态</h3>
    <div class="kv">
      <div class="k">版本</div><div>${esc(S.sys.version)}</div>
      <div class="k">数据库</div><div>${esc(S.sys.db_path)}</div>
      <div class="k">迁移</div><div>已应用 ${S.sys.migrations_applied} 个${S.sys.migrations_pending.length ? '，待执行 ' + S.sys.migrations_pending.join(',') : ''}</div>
      <div class="k">安全模式</div><div>${S.sys.safe_mode ? '<span class="tag danger">只读</span>' : '<span class="tag ok">正常</span>'}</div>
      <div class="k">最近备份</div><div>${esc((S.sys.last_backup && S.sys.last_backup.started_at) || '无')}</div>
      <div class="k">签名口令</div><div>
        <label style="cursor:pointer"><input type="checkbox" id="sign_remember"
          ${signRememberOn() ? 'checked' : ''} onchange="toggleSignRemember(this.checked)">
        记住 30 分钟（仅本机当前标签页，关闭页面即失效）</label>
        <span class="hint" id="sign_remember_state"></span></div>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="btn ghost" onclick="runDiagnose()">系统诊断</button>
      <button class="btn ghost" onclick="runIntegrityFull()">完整性检查</button>
      <button class="btn ghost" onclick="runBackup()">立即备份</button>
      <button class="btn ghost" onclick="runIncremental()">增量备份</button>
      <button class="btn ghost" onclick="showBackups()">备份列表</button>
      <button class="btn ghost" onclick="showRestores()">恢复记录</button>
      <button class="btn ghost" onclick="runWatchdog()">跑一次看门狗</button>
      <button class="btn ghost" onclick="showWatchdogLog()">看门狗日志</button>
      <button class="btn ghost" onclick="runCrashRecovery()">崩溃恢复扫描</button>
      <button class="btn ghost" onclick="showLogs()">系统日志</button>
    </div>
    <div class="row" style="margin-top:8px">
      <button class="btn ghost sm" onclick="sysHealth()">健康检查</button>
      <button class="btn ghost sm" onclick="sysIntegrity()">页结构检查</button>
      <button class="btn ghost sm" onclick="sysImport()">基础数据导入</button>
      <button class="btn ghost sm" onclick="sysImportStatus()">导入进度</button>
      <button class="btn ghost sm" onclick="sysDataPackage()">数据包版本</button>
      <button class="btn ghost sm" onclick="sysImportTemplate()">下载导入模板</button>
      <button class="btn ghost sm" onclick="sysCheckUpdate()">检查更新</button>
      <button class="btn ghost sm warn" onclick="sysRestore()">从备份恢复</button>
      <button class="btn ghost sm" onclick="sysReleaseReadonly()">解除只读</button>
      <button class="btn ghost sm" onclick="sysSalvage()">数据库抢救</button>
      <button class="btn ghost sm warn" onclick="sysShutdown()">安全退出服务</button>
    </div>
  </div>
  <div class="card"><h3>审计概览（M12）</h3><div id="auditSum">加载中…</div>
    <div class="row"><button class="btn ghost" onclick="showAuditList('high_risk')">高风险操作</button>
    <button class="btn ghost" onclick="showAuditList('logins')">登录审计</button>
    <button class="btn ghost" onclick="showAuditList('permission')">权限拒绝</button>
    <button class="btn ghost" onclick="showAuditList('operations')">操作审计</button>
    <button class="btn ghost" onclick="showAuditList('time_anomaly')">时间异常（F88）</button></div>
  </div>`;
  const role = (S.user || {}).role_code;
  if (role === 'ADMIN') {
    html += `<div class="card"><h3>用户与角色（F102）</h3><div id="userList">加载中…</div>
      <div class="row"><button class="btn ghost" onclick="showNewUser()">+ 新建用户</button></div></div>
      <div class="card"><h3>系统配置（F101）</h3><div id="cfgList">加载中…</div></div>`;
  }
  return html;
}
async function loadSystemExtras() {
  try {
    const s = await api('/api/audit/summary');
    $('#auditSum').innerHTML = `<div class="kv">${Object.keys(s.counts).map(k => `<div class="k">${k}</div><div>${s.counts[k]} 条</div>`).join('')}
      <div class="k">今日操作</div><div>${s.today_operations} 条</div></div>`;
  } catch (e) { $('#auditSum').textContent = '需要管理员角色'; }
  if ((S.user || {}).role_code !== 'ADMIN') return;
  try {
    const u = await api('/api/system/users');
    const roles = (await api('/api/system/roles', {}, 'GET')).items
      || (await api('/api/system/roles', {}, 'GET')).roles || [];
    const roleOpts = roles.map(r => typeof r === 'string' ? r : (r.role_code || r.code))
      .filter(Boolean).map(r => `<option value="${esc(r)}">${esc(r)}</option>`).join('');
    const active = u.items.filter(x => x.is_active);
    const disabled = u.items.filter(x => !x.is_active);
    const userRow = x => `<tr><td>${esc(x.username)}</td><td>${esc(x.display_name)}</td>
      <td>${esc(x.roles || '')}</td>
      <td>${x.is_active ? '<span class="tag ok">启用</span>' : '<span class="tag danger">停用</span>'}</td>
      <td>${esc(x.last_login_at || '-')}</td>
      <td><button class="btn sm ghost" onclick="sysUserRoles(${x.id})">改角色</button>
          <button class="btn sm ghost" onclick="sysResetPwd(${x.id})">改口令</button>
          ${x.is_active ? `<button class="btn sm warn" onclick="sysDisableUser(${x.id})">停用</button>` : ''}</td></tr>`;
    $('#userList').innerHTML = `<table><thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead>
      <tbody>${active.map(userRow).join('') || '<tr><td colspan="6" class="empty">暂无启用账号</td></tr>'}</tbody></table>
      ${disabled.length ? `<details style="margin-top:8px"><summary class="hint">已停用账号 ${disabled.length} 个（点击展开）</summary>
        <table><thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead>
        <tbody>${disabled.map(userRow).join('')}</tbody></table></details>` : ''}
      <div class="hint" style="margin-top:6px">可用角色：${roles.map(r => esc(typeof r === 'string' ? r : (r.role_code || r.code))).join('、') || '-'}</div>
      <div class="hint" id="roleHint"></div>`;
    window.__roleOptsCache = roleOpts;
  } catch (e) { $('#userList').textContent = e.message; }
  try {
    const c = await api('/api/system/config');
    $('#cfgList').innerHTML = `<table><thead><tr><th>配置键</th><th>值</th><th>模块</th><th>说明</th><th></th></tr></thead><tbody>${
      c.items.map(x => `<tr><td>${esc(x.cfg_key)}</td><td><input id="cfg_${esc(x.cfg_key)}" value="${esc(x.cfg_value)}"></td>
      <td>${esc(x.module || '')}</td><td>${esc(x.description || '')}</td>
      <td><button class="btn sm" onclick="setCfg('${esc(x.cfg_key)}')">保存</button></td></tr>`).join('')}</tbody></table>`;
  } catch (e) { $('#cfgList').textContent = e.message; }
}
async function setCfg(k) {
  try { await api('/api/system/config/set', { key: k, value: $('#cfg_' + k).value, reason: '界面修改' }); toast('已保存（已写高风险审计）', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}

/* ---- 用户停用 / 改口令 / 改角色 ---- */
async function sysDisableUser(id) {
  const r = prompt('停用原因（必填）：'); if (!r) return;
  try { await api('/api/system/user/disable', { user_id: id, reason: r }); toast('已停用', 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}
async function sysResetPwd(id) {
  const p = prompt('新口令（至少 8 位，含大小写与数字）：'); if (!p) return;
  try { await api('/api/system/user/reset_password', { user_id: id, new_password: p }); toast('口令已重置', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function sysUserRoles(id) {
  const opts = window.__roleOptsCache || '<option value="DOCTOR">医生</option><option value="PHARMACIST">药师</option><option value="CASHIER">收费员</option><option value="ADMIN">管理员</option>';
  modal('修改角色', `
    <div class="hint">按住 Ctrl 可多选。</div>
    <select id="ur_sel" multiple size="5" style="width:100%;margin-top:8px">${opts}</select>
    <div class="row" style="margin-top:10px"><button class="btn" onclick="doUserRoles(${id})">保存</button></div>`);
}
async function doUserRoles(id) {
  const sel = document.getElementById('ur_sel');
  const roles = Array.from(sel.selectedOptions).map(o => o.value);
  if (!roles.length) { toast('至少选一个角色', 'err'); return; }
  try { await api('/api/system/user/roles', { user_id: id, roles: roles }); toast('角色已更新', 'ok'); closeModal(); render(); }
  catch (e) { toast(e.message, 'err'); }
}

/* ---- 系统健康 / 完整性 / 基础数据导入 / 恢复 ---- */
async function sysHealth() {
  try {
    const r = await api('/api/system/health', {}, 'GET');
    modal('系统健康检查', `<pre style="white-space:pre-wrap;font-size:12px;max-height:420px;overflow:auto">${esc(JSON.stringify(r, null, 2))}</pre>`);
  } catch (e) { toast(e.message, 'err'); }
}
async function sysIntegrity() {
  try {
    const r = await api('/api/system/integrity', {});
    toast('页结构检查：' + (r.integrity_check || r.result || ''), r.ok ? 'ok' : 'err');
  } catch (e) { toast(e.message, 'err'); }
}
async function sysImport() {
  const kind = prompt('导入类型（DRUG / HERB / CHARGE_ITEM）：', 'DRUG'); if (!kind) return;
  const path = prompt('数据文件路径（CSV）：'); if (!path) return;
  try {
    const pv = await api('/api/system/import/preview', { data_type: kind.toUpperCase(), file_path: path });
    modal('导入预校验（零写入）', `<pre style="white-space:pre-wrap;font-size:12px;max-height:300px;overflow:auto">${esc(JSON.stringify(pv, null, 2))}</pre>
      <div class="row" style="margin-top:10px">
        <button class="btn" onclick="doImport('${esc(kind.toUpperCase())}','${esc(path)}')">确认导入</button></div>`);
  } catch (e) { toast(e.message, 'err'); }
}
async function doImport(kind, path) {
  try {
    const r = await api('/api/system/import', { data_type: kind, file_path: path });
    toast('导入完成：成功 ' + (r.success_count || 0) + ' 行，失败 ' + (r.error_count || 0) + ' 行', 'ok');
    closeModal();
  } catch (e) { toast(e.message, 'err'); }
}
async function sysImportStatus() {
  try {
    const r = await api('/api/system/import/status', {}, 'GET');
    modal('导入进度', `<pre style="white-space:pre-wrap;font-size:12px">${esc(JSON.stringify(r, null, 2))}</pre>`);
  } catch (e) { toast(e.message, 'err'); }
}
async function sysDataPackage() {
  try {
    const r = await api('/api/system/datapackage/status', {}, 'GET');
    modal('基础数据包版本', `<pre style="white-space:pre-wrap;font-size:12px">${esc(JSON.stringify(r, null, 2))}</pre>
      <div class="row" style="margin-top:10px"><button class="btn ghost" onclick="sysDpCheck()">检查更新</button></div>`);
  } catch (e) { toast(e.message, 'err'); }
}
async function sysDpCheck() {
  try {
    const r = await api('/api/system/datapackage/check', {});
    modal('数据包检查', `<pre style="white-space:pre-wrap;font-size:12px;max-height:360px;overflow:auto">${esc(JSON.stringify(r, null, 2))}</pre>
      <div class="row" style="margin-top:10px"><button class="btn" onclick="sysDpApply()">应用增量数据包</button></div>`);
  } catch (e) { toast(e.message, 'err'); }
}
async function sysDpApply() {
  if (!confirm('应用增量数据包会写入基础数据。继续？')) return;
  try { const r = await api('/api/system/datapackage/apply', {}); toast('已应用', 'ok'); closeModal(); }
  catch (e) { toast(e.message, 'err'); }
}
async function sysImportTemplate() {
  try {
    const r = await api('/api/system/import/template', { data_type: 'DRUG' }, 'GET');
    modal('导入模板', `<pre style="white-space:pre-wrap;font-size:12px">${esc(r.template || r.content || JSON.stringify(r, null, 2))}</pre>`);
  } catch (e) { toast(e.message, 'err'); }
}
async function sysCheckUpdate() {
  try { const r = await api('/api/system/update', {}, 'GET');
    modal('版本信息', `<pre style="white-space:pre-wrap;font-size:12px">${esc(JSON.stringify(r, null, 2))}</pre>`); }
  catch (e) { toast(e.message, 'err'); }
}
async function sysRestore() {
  const ref = prompt('备份标识（job_no 或文件路径）：'); if (!ref) return;
  if (!confirm('恢复会用备份整库覆盖当前数据（恢复前会自动备份现状）。确定继续？')) return;
  try { const r = await api('/api/system/restore', { backup_ref: ref, confirm: true });
    toast('恢复成功：' + (r.restore_no || ''), 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}
async function sysReleaseReadonly() {
  const r = prompt('解除只读的原因（必须说明已确认数据安全）：'); if (!r) return;
  try { await api('/api/system/readonly/release', { reason: r }); toast('已解除只读', 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}
async function sysSalvage() {
  if (!confirm('抢救会把当前库尽力导出到另一个文件（原库不改动），用于损坏后人工核对。继续？')) return;
  try { const r = await api('/api/system/salvage', { confirm: true }); modal('抢救结果', `<pre style="white-space:pre-wrap;font-size:12px">${esc(JSON.stringify(r, null, 2))}</pre>`); }
  catch (e) { toast(e.message, 'err'); }
}
async function sysShutdown() {
  if (!confirm('确定安全退出服务？退出前会自动 checkpoint 并备份。')) return;
  try { await api('/api/system/shutdown', {}); toast('服务已请求退出', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
function showNewUser() {
  modal('新建用户', `
    <div class="grid2">
      <div><label>账号 *</label><input id="nu_u"></div><div><label>姓名 *</label><input id="nu_n"></div>
      <div><label>口令 *</label><input id="nu_p" type="password"></div>
      <div><label>角色（可多选）</label><select id="nu_r" multiple size="4">
        <option value="DOCTOR">医生</option><option value="PHARMACIST">药师</option>
        <option value="CASHIER">收费员</option><option value="ADMIN">管理员</option></select></div>
    </div>
    <div class="row" style="margin-top:10px"><button class="btn" onclick="createUser()">创建</button></div>`);
}
async function createUser() {
  const sel = Array.from(document.getElementById('nu_r').selectedOptions).map(o => o.value);
  try {
    await api('/api/system/user/create', { username: val('nu_u'), display_name: val('nu_n'),
      password: val('nu_p'), roles: sel });
    toast('已创建', 'ok'); closeModal(); render();
  } catch (e) { toast(e.message, 'err'); }
}
async function runDiagnose() {
  try {
    const d = await api('/api/system/diagnose');
    modal('系统诊断（F104）', `<table><thead><tr><th>项目</th><th>结果</th><th>说明</th></tr></thead><tbody>${
      d.items.map(i => `<tr><td>${esc(i.diag_type)}</td><td><span class="tag ${i.result === 'OK' ? 'ok' : i.result === 'WARN' ? 'warn' : 'danger'}">${i.result}</span></td><td>${esc(JSON.stringify(i.detail))}</td></tr>`).join('')
      || '<tr><td colspan="3" class="empty">无诊断项</td></tr>'}</tbody></table>`);
  } catch (e) { toast('诊断失败：' + e.message, 'err'); }
}
async function runBackup() {
  try {
    const r = await api('/api/system/backup', { reason: 'MANUAL' });
    toast('备份完成：' + r.file + (r.valid ? '（已校验）' : '（校验失败！）'), r.valid ? 'ok' : 'err');
  } catch (e) { toast('备份失败：' + e.message, 'err'); }
}
async function showBackups() {
  try {
    const d = await api('/api/system/backups', { limit: 30 }, 'GET');
    modal('备份列表（F90/F92）', `<table><thead><tr><th>任务号</th><th>时间</th><th>大小</th><th>状态</th><th>原因</th></tr></thead><tbody>${
      d.items.map(b => `<tr><td>${esc(b.job_no)}</td><td>${esc(b.started_at)}</td><td>${b.file_size || '-'}</td><td>${b.status}</td><td>${esc(b.reason || '')}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">无</td></tr>'}</tbody></table>`);
  } catch (e) { toast('读取备份列表失败：' + e.message, 'err'); }
}
/* ---------- 第五阶段：M13 数据安全与恢复 ---------- */
async function runIntegrityFull() {
  try {
    const d = await api('/api/system/integrity/full', {});
    const rows = [
      ['页结构 integrity_check', d.integrity_check],
      ['外键违规', d.foreign_key_violations.length + ' 条'],
      ['业务不变量', d.invariants.length ? d.invariants.join('；') : '一致'],
    ];
    modal('完整性检查（F94）', `<table><thead><tr><th>层级</th><th>结果</th></tr></thead><tbody>${
      rows.map(r => `<tr><td>${esc(r[0])}</td><td>${esc(String(r[1]))}</td></tr>`).join('')
    }</tbody></table>`);
    toast(d.ok ? '完整库检查通过' : '发现不一致，请查看明细', d.ok ? 'ok' : 'err');
  } catch (e) { toast(e.message, 'err'); }
}
async function runIncremental() {
  try {
    const r = await api('/api/system/backup/incremental', { force: true, reason: 'MANUAL' });
    toast(r.skipped ? ('跳过：' + r.reason) : ('增量备份完成：' + r.file), r.skipped ? 'ok' : (r.valid ? 'ok' : 'err'));
  } catch (e) { toast(e.message, 'err'); }
}
async function showRestores() {
  try {
    const d = await api('/api/system/restores', { limit: 30 }, 'GET');
    modal('恢复记录（F93）', `<table><thead><tr><th>恢复单号</th><th>时间</th><th>来源文件</th><th>结果</th><th>恢复前备份</th></tr></thead><tbody>${
      d.items.map(r => `<tr><td>${esc(r.restore_no || '-')}</td><td>${esc(r.restored_at)}</td><td>${esc(r.source_file)}</td><td>${r.result}</td><td>${esc(r.before_backup || '-')}</td></tr>`).join('')
      || '<tr><td colspan="5" class="empty">无</td></tr>'}</tbody></table>`);
  } catch (e) { toast(e.message, 'err'); }
}
async function runWatchdog() {
  try {
    const d = await api('/api/system/watchdog', { kind: 'all' });
    toast(`看门狗完成：AI 重试 ${d.ai.retried} / 放弃 ${d.ai.abandoned}；硬件重启 ${d.hardware.restarted} / 降级 ${d.hardware.degraded}`, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}
async function showWatchdogLog() {
  try {
    const d = await api('/api/system/watchdog/log', { limit: 100 }, 'GET');
    modal('看门狗日志（F97/F98）', `<table><thead><tr><th>类型</th><th>目标</th><th>动作</th><th>结果</th><th>时间</th></tr></thead><tbody>${
      d.items.map(r => `<tr><td>${esc(r.kind)}</td><td>${esc(r.target || '-')}</td><td>${esc(r.action)}</td><td>${r.result}</td><td>${esc(r.occurred_at)}</td></tr>`).join('')
      || '<tr><td colspan="5" class="empty">无</td></tr>'}</tbody></table>`);
  } catch (e) { toast(e.message, 'err'); }
}
async function runCrashRecovery() {
  try {
    const d = await api('/api/system/recovery/crash', {});
    const bits = [`导入 ${d.imports_recovered}`, `打印 ${d.prints_recovered}`,
      `AI 重试 ${d.ai.retried}/放弃 ${d.ai.abandoned}`];
    if (d.manual_review.length) bits.push(`需人工复核 ${d.manual_review.length} 项`);
    toast('崩溃恢复：' + bits.join('，'), d.manual_review.length ? 'err' : 'ok');
  } catch (e) { toast(e.message, 'err'); }
}
async function showLogs() {
  try {
    const d = await api('/api/system/logs', { type: 'app', lines: 200 }, 'GET');
    modal('系统日志（F103，最近 200 行）', `<pre class="log">${esc((d.items || []).join('\n')) || '(空)'}</pre>`);
  } catch (e) { toast('读取日志失败：' + e.message, 'err'); }
}
async function showAuditList(kind) {
  const map = { high_risk: '/api/audit/high_risk', logins: '/api/audit/logins',
    permission: '/api/audit/permission', operations: '/api/audit/operations',
    time_anomaly: '/api/audit/time_anomaly' };
  try {
    // 审计查询接口都是 GET——这里传了 body，不显式写 'GET' 会变成 POST 而报 E2005
    const d = await api(map[kind], { limit: 100 }, 'GET');
    const keys = d.items.length ? Object.keys(d.items[0]).filter(k => k !== 'detail_json' && k !== 'client_info') : [];
    modal('审计 · ' + kind, `<table><thead><tr>${keys.map(k => `<th>${k}</th>`).join('')}</tr></thead><tbody>${
      d.items.map(r => `<tr>${keys.map(k => `<td>${esc(r[k])}</td>`).join('')}</tr>`).join('') || '<tr><td class="empty">无记录</td></tr>'}</tbody></table>`);
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- 第三阶段：药房 / 处置 / 收费 ---------- */
const P3 = {
  itemKind: 'DRUG',
  picked: null,        // 入库选中的品目
  inLines: [],         // 待提交入库明细
  rxId: null,          // 发药/退药选中的处方
  visitId: '',         // 处置/收费的就诊
  billId: null,
  execId: null,
};

function yuan(fen) { return (Number(fen || 0) / 100).toFixed(2); }
function num(v, d) { const n = parseFloat(v); return isNaN(n) ? (d || 0) : n; }

/* ===== 药房 ===== */
async function viewPharmacy() {
  let alerts = [];
  try { alerts = (await api('/api/stock/alerts', { status: 'OPEN' }, 'GET')).items || []; } catch (e) { }
  try { await loadVisitChoices(); } catch (e) { }
  return `
  <div class="card">
    <h3>库存查询</h3>
    <div class="row">
      <select id="ph_kind" onchange="P3.itemKind=this.value">
        <option value="DRUG">成药</option><option value="HERB">中药饮片</option>
      </select>
      <input id="ph_kw" placeholder="品目名称/拼音" style="flex:1">
      <button class="btn sm" onclick="phSearch()">搜索</button>
    </div>
    <div id="ph_result" class="hint">输入关键词搜索品目后查看库存与批次</div>
  </div>

  <div class="card">
    <h3>入库（F40/F41）</h3>
    <div class="row">
      <input id="in_supplier" placeholder="供应商名称（留空则新建为「默认供应商」）" style="flex:1">
      <input id="in_invoice" placeholder="发票号" style="width:140px">
    </div>
    <div class="row">
      <input id="in_qty" placeholder="数量" style="width:100px">
      <span class="hint" id="in_unit_hint">单位</span>
      <input id="in_cost" placeholder="成本 元/单位" style="width:140px">
      <input id="in_expiry" type="date" style="width:150px">
      <input id="in_batchno" placeholder="供应商批号" style="width:140px">
      <button class="btn sm ghost" onclick="phAddLine()">加入明细</button>
    </div>
    <table><thead><tr><th>品目</th><th>数量</th><th>成本(元)</th><th>效期</th><th>供应商批号</th><th></th></tr></thead>
      <tbody id="in_lines"><tr><td class="empty" colspan="6">暂无明细</td></tr></tbody></table>
    <div class="row" style="margin-top:8px">
      <button class="btn" onclick="phSubmitIn()">生成入库单并确认</button>
      <span class="hint">确认后批次、库存与流水同时生效（同一事务）</span>
    </div>
  </div>

  <div class="card">
    <h3>发药 / 退药（F51 / F115 / F46）· 当前药房：
      <span class="tag ${MODE.def === 'WEST' ? 'west' : 'tcm'}">${MODE.def === 'WEST' ? '西药 / 成药' : '中药饮片'}</span>
      <button class="btn sm ghost" onclick="setMode('${MODE.def === 'WEST' ? 'TCM' : 'WEST'}')">切到${MODE.def === 'WEST' ? '中药房' : '西药房'}</button>
    </h3>
    <div class="row">
      ${visitSelectHtml('ph_visit', { selected: P3.visitId, onchange: 'phVisitChanged()' })}
      <select id="rx_id" class="pick" onchange="phLoadRx()"><option value="">— 先选就诊 —</option></select>
    </div>
    <div class="row">
      <button class="btn" onclick="phDispenseAuto()">发药</button>
      <label class="hint"><input type="checkbox" id="ph_daijian"> 代煎</label>
      <span class="hint">按处方类型自动走成药 / 中药调剂，不用先判断再选按钮</span>
      <details style="margin-left:8px"><summary class="hint">手动指定</summary>
        <div class="row" style="margin-top:6px">
          <button class="btn sm ghost" onclick="phLoadRx()">读取处方</button>
          <button class="btn sm ghost" onclick="phDispense('WEST')">成药发药</button>
          <button class="btn sm ghost" onclick="phDispense('HERB')">中药发药</button>
        </div>
      </details>
    </div>
    <div id="rx_info" class="hint">先选就诊 → 选处方 → 读取，再执行发药或退药</div>
    <div class="row" style="margin-top:6px">
      <input id="rt_qty" placeholder="退药数量" style="width:120px">
      <input id="rt_reason" placeholder="退药原因（必填）" style="flex:1">
      <button class="btn sm ghost" onclick="phReturn()">退药</button>
    </div>
  </div>

  <div class="card">
    <h3>效期与库存预警（F49/F50）</h3>
    <div class="row">
      <button class="btn sm" onclick="phScan()">扫描预警</button>
      <button class="btn sm ghost" onclick="phCheckCreate()">新建盘点（全品种）</button>
      <button class="btn sm ghost" onclick="phCheckApprove()">录入并审批最近盘点</button>
    </div>
    <table><thead><tr><th>类型</th><th>对象</th><th>可用量</th><th>效期</th><th>剩余天数</th></tr></thead>
      <tbody>${alerts.map(a => `<tr><td>${esc(a.alert_type)}</td><td>${esc(a.drug_id || a.herb_id || '')}</td>
        <td>${esc(a.available_qty)}</td><td>${esc(a.expiry_date || '')}</td><td>${esc(a.days_left)}</td></tr>`).join('')
    || '<tr><td class="empty" colspan="5">暂无预警</td></tr>'}</tbody></table>
  </div>

  <div class="card">
    <h3>最近库存流水（F43）</h3>
    <div class="row"><button class="btn sm ghost" onclick="phTxns()">刷新</button></div>
    <div id="ph_txns" class="hint">点击刷新查看最近 30 条流水（流水只允许新增，不可修改/删除）</div>
  </div>

  <div class="card">
    <h3>批次 / 入库单 / 报损 / 拆零 / 退药记录</h3>
    <div class="row">
      <input id="bt_kind" value="DRUG" style="width:90px" readonly>
      <input id="bt_item" placeholder="品目 ID" style="width:110px">
      <button class="btn sm ghost" onclick="phBatches()">查批次</button>
      <button class="btn sm ghost" onclick="phInList()">入库单列表</button>
      <input id="iv_id" placeholder="入库单 ID" style="width:110px">
      <button class="btn sm warn" onclick="phInVoid()">作废入库单</button>
    </div>
    <div class="row">
      <input id="ls_item" placeholder="品目 ID" style="width:110px">
      <input id="ls_batch" placeholder="批次 ID" style="width:110px">
      <input id="ls_qty" placeholder="报损数量" style="width:110px">
      <input id="ls_reason" placeholder="报损原因（必填）" style="flex:1">
      <button class="btn sm ghost" onclick="phLoss()">报损</button>
    </div>
    <div class="row">
      <input id="sp_batch" placeholder="拆出批次 ID" style="width:120px">
      <input id="sp_qty" placeholder="拆出数量" style="width:110px">
      <button class="btn sm ghost" onclick="phSplit()">拆零</button>
      <input id="ck_id" placeholder="盘点单 ID" style="width:110px">
      <button class="btn sm ghost" onclick="phCheckCancel()">取消盘点</button>
      <button class="btn sm ghost" onclick="phReturnList()">退药记录</button>
    </div>
    <div class="row">
      <input id="ar_id" placeholder="预警 ID" style="width:110px">
      <select id="ar_act"><option value="RESOLVED">已处理</option><option value="IGNORED">忽略</option></select>
      <input id="ar_reason" placeholder="处理说明（忽略必填）" style="flex:1">
      <button class="btn sm ghost" onclick="phAlertResolve()">处理预警</button>
    </div>
    <div id="ph_more" class="hint"></div>
  </div>`;
}

/* ---- 批次 / 入库单 / 报损 / 拆零 / 盘点 / 预警 / 退药记录 ---- */
async function phBatches() {
  try {
    const items = (await api('/api/stock/batches', { item_kind: val('bt_kind'),
      item_id: parseInt(val('bt_item')) }, 'GET')).items || [];
    $('#ph_more').innerHTML = items.length
      ? `<table class="tbl"><thead><tr><th>批号</th><th>剩余</th><th>效期</th><th>状态</th></tr></thead><tbody>${
        items.map(b => `<tr><td>${esc(b.batch_no)}</td><td>${esc(b.qty_remain)}</td>
          <td>${esc(b.expiry_date || '')}</td><td>${esc(b.status)}</td></tr>`).join('')}</tbody></table>`
      : '无批次';
  } catch (e) { toast(e.message, 'err'); }
}
async function phInList() {
  try {
    const items = (await api('/api/stock/in/list', { limit: 30 }, 'GET')).items || [];
    $('#ph_more').innerHTML = items.length
      ? `<table class="tbl"><thead><tr><th>入库单号</th><th>状态</th><th>金额</th><th>时间</th></tr></thead><tbody>${
        items.map(s => `<tr><td>${esc(s.stock_in_no)}</td><td>${esc(s.status)}</td>
          <td>${((s.total_amount_fen || 0) / 100).toFixed(2)}</td><td>${esc(s.created_at || '')}</td></tr>`).join('')}</tbody></table>`
      : '无入库单';
  } catch (e) { toast(e.message, 'err'); }
}
async function phInVoid() {
  const id = parseInt(val('iv_id')); if (!id) { toast('填入库单 ID', 'err'); return; }
  const r = prompt('作废原因（必填）：'); if (!r) return;
  try { await api('/api/stock/in/void', { id: id, reason: r }); toast('已作废', 'ok'); phInList(); }
  catch (e) { toast(e.message, 'err'); }
}
async function phLoss() {
  const reason = val('ls_reason');
  if (!reason) { toast('报损原因必填', 'err'); return; }
  try {
    await api('/api/stock/loss/create', { items: [{
      item_kind: 'DRUG', item_id: parseInt(val('ls_item')), batch_id: parseInt(val('ls_batch')),
      qty: parseFloat(val('ls_qty')) }], reason: reason });
    toast('报损已登记', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}
async function phSplit() {
  try { await api('/api/stock/split', { batch_id: parseInt(val('sp_batch')),
    qty: parseFloat(val('sp_qty')) }); toast('已拆零', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function phCheckCancel() {
  const id = parseInt(val('ck_id')); if (!id) { toast('填盘点单 ID', 'err'); return; }
  const r = prompt('取消原因（必填）：'); if (!r) return;
  try { await api('/api/stock/check/cancel', { id: id, reason: r }); toast('盘点已取消', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function phAlertResolve() {
  const id = parseInt(val('ar_id')); if (!id) { toast('填预警 ID', 'err'); return; }
  try { await api('/api/stock/alert/resolve', { id: id, action: val('ar_act'),
    reason: val('ar_reason') || '已处理' }); toast('预警已处理', 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}
async function phReturnList() {
  try {
    const items = (await api('/api/pharmacy/return/list', { limit: 30 }, 'GET')).items || [];
    $('#ph_more').innerHTML = items.length
      ? `<table class="tbl"><thead><tr><th>退药单号</th><th>处方</th><th>状态</th><th>金额</th><th>原因</th></tr></thead><tbody>${
        items.map(r => `<tr><td>${esc(r.return_no || r.id)}</td><td>${esc(r.prescription_id)}</td>
          <td>${esc(r.status)}</td><td>${((r.amount_fen || 0) / 100).toFixed(2)}</td>
          <td>${esc(r.reason || '')}</td></tr>`).join('')}</tbody></table>`
      : '无退药记录';
  } catch (e) { toast(e.message, 'err'); }
}

async function phSearch() {
  const kw = val('ph_kw');
  const kind = P3.itemKind;
  try {
    const d = kind === 'DRUG' ? await api('/api/drug/search', { keyword: kw }, 'GET')
                              : await api('/api/herb/search', { keyword: kw }, 'GET');
    const items = (d.items || []).slice(0, 10);
    P3.search = items;
    $('#ph_result').innerHTML = `<table><thead><tr><th>名称</th><th>编码</th><th>价格</th><th></th></tr></thead><tbody>${
      items.map(x => `<tr><td>${esc(x.generic_name || x.name)}</td><td>${esc(x.drug_code || x.herb_code)}</td>
        <td>${esc(x.price_fen ? yuan(x.price_fen) : (x.price_milli_per_g ? (x.price_milli_per_g / 1000).toFixed(3) + '元/g' : '-'))}</td>
        <td><button class="btn sm" onclick="phPick(${x.id})">选用 / 查库存</button></td></tr>`).join('')
    || '<tr><td class="empty" colspan="4">无匹配品目</td></tr>'}</tbody></table>`;
  } catch (e) { toast(e.message, 'err'); }
}

async function phPick(id) {
  const kind = P3.itemKind;
  const x = (P3.search || []).find(i => i.id === id) || {};
  P3.picked = { id: id, kind: kind, name: x.generic_name || x.name };
  $('#in_unit_hint').textContent = '单位：' + (kind === 'HERB' ? 'kg（饮片按 g 存储）' : (x.unit || '盒'));
  try {
    const s = await api('/api/stock/query', kind === 'DRUG' ? { item_kind: 'DRUG', drug_id: id }
                                                            : { item_kind: 'HERB', herb_id: id }, 'GET');
    modal('库存 · ' + P3.picked.name, `<div class="hint">可用量：${esc(s.available)}${kind === 'HERB' ? ' g' : ''}</div>
      <table><thead><tr><th>批次</th><th>剩余</th><th>效期</th><th>状态</th></tr></thead><tbody>${
      (s.batches || []).map(b => `<tr><td>${esc(b.batch_no)}</td><td>${esc(b.qty_remain)}</td>
        <td>${esc(b.expiry_date)}</td><td>${esc(b.status)}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="4">无批次</td></tr>'}</tbody></table>`);
  } catch (e) { toast(e.message, 'err'); }
}

function phAddLine() {
  if (!P3.picked) { toast('请先搜索并选用品目', 'err'); return; }
  const qty = num(val('in_qty'), 0);
  if (qty <= 0) { toast('数量必须大于 0', 'err'); return; }
  if (!val('in_expiry')) { toast('效期必填', 'err'); return; }
  P3.inLines.push({
    item_kind: P3.picked.kind, drug_id: P3.picked.kind === 'DRUG' ? P3.picked.id : null,
    herb_id: P3.picked.kind === 'HERB' ? P3.picked.id : null,
    name: P3.picked.name, qty: qty,
    input_unit: P3.picked.kind === 'HERB' ? 'KG' : null,
    cost_yuan_per_unit: num(val('in_cost'), 0), expiry_date: val('in_expiry'),
    supplier_batch_no: val('in_batchno'),
  });
  phRenderLines();
}

function phRenderLines() {
  $('#in_lines').innerHTML = P3.inLines.map((l, i) => `<tr><td>${esc(l.name)}</td><td>${esc(l.qty)}${l.input_unit === 'KG' ? ' kg' : ''}</td>
    <td>${esc(l.cost_yuan_per_unit)}</td><td>${esc(l.expiry_date)}</td><td>${esc(l.supplier_batch_no || '')}</td>
    <td><button class="btn sm ghost" onclick="P3.inLines.splice(${i},1);phRenderLines()">移除</button></td></tr>`).join('')
    || '<tr><td class="empty" colspan="6">暂无明细</td></tr>';
}

async function phSubmitIn() {
  if (!P3.inLines.length) { toast('请先加入入库明细', 'err'); return; }
  try {
    let sid = null;
    const name = val('in_supplier').trim();
    if (name) {
      const list = await api('/api/stock/supplier/list', {}, 'GET');
      const hit = (list.items || []).find(s => s.name === name);
      sid = hit ? hit.id : (await api('/api/stock/supplier/create', { name: name })).id;
    }
    const r = await api('/api/stock/in/create', {
      supplier_id: sid, invoice_no: val('in_invoice'), items: P3.inLines,
    });
    const c = await api('/api/stock/in/confirm', { id: r.id });
    toast('入库已确认：' + r.stock_in_no, 'ok');
    P3.inLines = []; phRenderLines();
    modal('入库完成', `<div>单号：${esc(r.stock_in_no)}</div><div>明细 ${esc(c.lines)} 行</div>
      <div class="hint">已生成批次、库存与流水（同一事务）</div>`);
  } catch (e) { toast(e.message, 'err'); }
}

async function phVisitChanged() {
  const vid = val('ph_visit');
  P3.visitId = vid;
  P3.rxId = 0;
  // 中药房默认只列中药方，西药房只列成药方（药房页顶部可切模式）
  await fillRxSelect('rx_id', vid, MODE.def === 'WEST' ? 'WEST' : 'TCM');
  const sel = document.getElementById('rx_id');
  if (sel && sel.value) { await phLoadRx(); }   // 处方带出来后直接读，不用再点「读取处方」
  else if (vid) { document.getElementById('rx_info').innerHTML = '该就诊还没有处方，请医生先开方'; }
}

async function phLoadRx() {
  try {
    const id = val('rx_id');
    if (!id) { toast('请先选择处方', 'err'); return; }
    const r = await api('/api/prescription/get', { id: id }, 'GET');
    P3.rxId = r.id;
    P3.rxType = r.prescription_type || 'WEST';
    $('#rx_info').innerHTML = `<div>处方 ${esc(r.prescription_no)} · 类型 ${esc(r.prescription_type)}
      · 状态 ${esc(r.status)} · 金额 ${esc(yuan(r.total_amount))} 元
      · 付数 ${esc(r.doses || '-')}</div>
      <div class="hint">成药明细 ${esc((r.items || []).length)} 行 / 饮片明细 ${esc((r.herbs || []).length)} 行</div>`;
  } catch (e) { toast(e.message, 'err'); }
}

async function phDispenseAuto() {
  if (!P3.rxId) { toast('请先选择就诊，系统会自动带出处方', 'err'); return; }
  return phDispense(P3.rxType === 'WEST' ? 'WEST' : 'HERB');
}

async function phDispense(kind) {
  if (!P3.rxId) { toast('请先读取处方', 'err'); return; }
  try {
    const r = kind === 'WEST' ? await api('/api/pharmacy/dispense', { prescription_id: P3.rxId })
      : await api('/api/pharmacy/herb_dispense', { prescription_id: P3.rxId,
          decoction_type: (document.getElementById('ph_daijian') || {}).checked ? 'DAIJIAN' : 'ZIJIAN' });
    toast('发药完成，处方状态 ' + r.status, 'ok');
    phLoadRx();
  } catch (e) { toast(e.message, 'err'); }
}

async function phReturn() {
  if (!P3.rxId) { toast('请先读取处方', 'err'); return; }
  try {
    const r = await api('/api/prescription/get', { id: P3.rxId }, 'GET');
    const items = (r.items || []).map(i => ({ prescription_item_id: i.id }));
    const herbs = (r.herbs || []).map(i => ({ herb_prescription_item_id: i.id }));
    const list = items.length ? items : herbs;
    if (!list.length) { toast('该处方没有明细', 'err'); return; }
    const qty = num(val('rt_qty'), 0);
    list.forEach(x => { x.qty = qty; });
    const rt = await api('/api/pharmacy/return/create', {
      prescription_id: P3.rxId, reason: val('rt_reason'), items: list,
    });
    toast('退药成功：' + rt.return_no + '，金额 ' + yuan(rt.total_amount_fen) + ' 元', 'ok');
    phLoadRx();
  } catch (e) { toast(e.message, 'err'); }
}

async function phScan() {
  try { const s = await api('/api/stock/alert/scan', {}); toast('扫描完成：' + JSON.stringify(s), 'ok'); render(); }
  catch (e) { toast(e.message, 'err'); }
}

async function phCheckCreate() {
  try {
    const c = await api('/api/stock/check/create', { scope: 'ALL' });
    const d = await api('/api/stock/check/get', { id: c.id }, 'GET');
    const inputs = (d.items || []).map(i => ({ batch_id: i.batch_id, counted_qty: Number(i.book_qty) }));
    await api('/api/stock/check/input', { id: c.id, items: inputs });
    const a = await api('/api/stock/check/approve', { id: c.id });
    toast('盘点完成：' + c.check_no + '，调整 ' + a.adjusted_rows + ' 行', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

async function phCheckApprove() { toast('请使用「新建盘点」按钮，流程已内置录入与审批', 'ok'); }

async function phTxns() {
  try {
    const d = await api('/api/stock/transactions', { limit: 30 }, 'GET');
    $('#ph_txns').innerHTML = `<table><thead><tr><th>时间</th><th>类型</th><th>变动</th><th>结存</th><th>单据</th><th>金额</th></tr></thead><tbody>${
      (d.items || []).map(t => `<tr><td>${fmtTime(t.occurred_at)}</td><td>${esc(t.trans_type)}</td>
        <td>${esc(t.qty_change)}</td><td>${esc(t.balance_after)}</td>
        <td>${esc(t.ref_type)}#${esc(t.ref_id)}</td><td>${esc(yuan(t.amount_fen))}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="6">暂无流水</td></tr>'}</tbody></table>`;
  } catch (e) { toast(e.message, 'err'); }
}

/* ===== 处置 ===== */
async function viewTreatment() {
  try { await loadVisitChoices(); } catch (e) { }
  return `
  <div class="card">
    <h3>开立处置（F52/F54）</h3>
    <div class="row">
      ${visitSelectHtml('tr_visit', { selected: P3.visitId, onchange: 'trLoad()' })}
      <input id="tr_kw" placeholder="处置项目关键词" style="flex:1">
      <button class="btn sm" onclick="trSearch()">搜索项目</button>
      <button class="btn sm ghost" onclick="trLoad()">刷新本就诊处置</button>
    </div>
    <div id="tr_result" class="hint">搜索项目后可开立（支持组套）</div>
    <div id="tr_list"></div>
  </div>

  <div class="card">
    <h3>中医治疗记录 / 过程记录（F116 / F56）</h3>
    <div class="row">
      <select id="tr_exec" class="pick"><option value="">— 先在上方列表里点「记录」 —</option></select>
      <button class="btn sm" onclick="trLoadExec()">读取</button>
    </div>
    <div id="tr_exec_info" class="hint">读取后可填写中医治疗记录与过程记录</div>
    <div class="row">
      <input id="tcm_points" placeholder="穴位（逗号分隔）" style="flex:1">
      <input id="tcm_manip" placeholder="手法" style="width:120px">
      <input id="tcm_retain" placeholder="留针分钟" style="width:100px">
      <button class="btn sm ghost" onclick="trTcmSave()">保存中医记录</button>
    </div>
    <div class="row">
      <input id="proc_content" placeholder="过程记录内容" style="flex:1">
      <button class="btn sm ghost" onclick="trProcAdd()">追加过程</button>
    </div>
  </div>

  <div class="card">
    <h3>处置项目与套餐维护（F52）</h3>
    <div class="row">
      <input id="ti_code" placeholder="编码" style="width:110px">
      <input id="ti_name" placeholder="名称" style="width:160px">
      <input id="ti_price" placeholder="单价（元）" style="width:110px">
      <select id="ti_cat" style="width:120px">
        <option value="GENERAL">通用</option><option value="TCM">中医</option>
        <option value="NURSING">护理</option></select>
      <button class="btn sm" onclick="trItemCreate()">新增项目</button>
    </div>
    <div class="row">
      <input id="ti_id" placeholder="项目 ID（改价用）" style="width:130px">
      <input id="ti_price2" placeholder="新单价（元）" style="width:120px">
      <button class="btn sm ghost" onclick="trItemUpdate()">修改项目</button>
    </div>
    <div class="row">
      <input id="tp_name" placeholder="套餐名称" style="width:160px">
      <input id="tp_items" placeholder="项目 ID 列表，逗号分隔" style="flex:1">
      <button class="btn sm ghost" onclick="trPkgCreate()">新建套餐</button>
      <button class="btn sm ghost" onclick="trPkgList()">套餐列表</button>
    </div>
    <div id="tr_maint" class="hint"></div>
  </div>

  <div class="card">
    <h3>皮试（F45）</h3>
    <div class="row">
      <input id="sk_pid" placeholder="患者 ID（必填）" style="width:140px">
      <input id="sk_visit" placeholder="就诊 ID（可选）" style="width:130px">
      <input id="sk_drug" placeholder="药品 ID（必填）" style="width:120px">
      <select id="sk_result" style="width:110px">
        <option value="NEGATIVE">阴性</option><option value="POSITIVE">阳性</option>
        <option value="PENDING">待观察</option></select>
      <input id="sk_note" placeholder="备注" style="flex:1">
      <button class="btn sm" onclick="trSkinRecord()">记录皮试结果</button>
      <button class="btn sm ghost" onclick="trSkinList()">该患者皮试记录</button>
    </div>
    <div class="hint">皮试按<b>患者</b>记录（patient_id 必填），开方时规则会自动校验是否需要皮试。</div>
    <div id="tr_skin" class="hint"></div>
  </div>`;
}

async function trItemCreate() {
  const code = val('ti_code'), name = val('ti_name');
  if (!code || !name) { toast('编码与名称必填', 'err'); return; }
  try { await api('/api/treatment/item/create', { item_code: code, name: name,
    price_fen: Math.round(parseFloat(val('ti_price') || 0) * 100), category: val('ti_cat') });
    toast('已新增处置项目', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}
async function trItemUpdate() {
  const id = parseInt(val('ti_id')); if (!id) { toast('填项目 ID', 'err'); return; }
  try { await api('/api/treatment/item/update', { id: id,
    price_fen: Math.round(parseFloat(val('ti_price2') || 0) * 100) }); toast('已更新', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function trPkgCreate() {
  const name = val('tp_name'); if (!name) { toast('填套餐名称', 'err'); return; }
  const ids = val('tp_items').split(',').map(s => parseInt(s.trim())).filter(Boolean);
  if (!ids.length) { toast('至少包含一个项目', 'err'); return; }
  try { await api('/api/treatment/package/create', { name: name,
    items: ids.map(i => ({ item_id: i, quantity: 1 })) }); toast('套餐已创建', 'ok'); trPkgList(); }
  catch (e) { toast(e.message, 'err'); }
}
async function trPkgList() {
  try {
    const items = (await api('/api/treatment/package/list', {}, 'GET')).items || [];
    $('#tr_maint').innerHTML = items.length
      ? `<table class="tbl"><thead><tr><th>套餐</th><th>项目数</th><th>金额</th></tr></thead><tbody>${
        items.map(p => `<tr><td>${esc(p.name)}</td><td>${(p.items || []).length}</td>
          <td>${((p.total_amount_fen || 0) / 100).toFixed(2)}</td></tr>`).join('')}</tbody></table>`
      : '无套餐';
  } catch (e) { toast(e.message, 'err'); }
}
async function trResume(id) {
  try { await api('/api/treatment/resume', { id: id }); toast('已恢复执行', 'ok'); trLoad(); }
  catch (e) { toast(e.message, 'err'); }
}
async function trSkinRecord() {
  const pid = parseInt(val('sk_pid')), did = parseInt(val('sk_drug'));
  if (!pid || !did) { toast('患者 ID 与药品 ID 必填', 'err'); return; }
  const vid = parseInt(val('sk_visit')) || null;
  try { await api('/api/skin_test/record', { patient_id: pid, drug_id: did,
    visit_id: vid, result: val('sk_result'), note: val('sk_note') || null });
    toast('皮试结果已记录', 'ok'); trSkinList(); }
  catch (e) { toast(e.message, 'err'); }
}
async function trSkinList() {
  const pid = parseInt(val('sk_pid'));
  if (!pid) { toast('请填患者 ID', 'err'); return; }
  try {
    const items = (await api('/api/skin_test/list', { patient_id: pid }, 'GET')).items || [];
    $('#tr_skin').innerHTML = items.length
      ? `<table class="tbl"><thead><tr><th>药品</th><th>结果</th><th>执行时间</th><th>观察(分)</th><th>备注</th></tr></thead><tbody>${
        items.map(s => `<tr><td>${esc(s.drug_name || s.drug_id)}</td><td>${esc(s.result)}</td>
          <td>${esc(s.performed_at || '')}</td><td>${esc(s.observe_min || '')}</td>
          <td>${esc(s.note || '')}</td></tr>`).join('')}</tbody></table>`
      : '该患者无皮试记录';
  } catch (e) { toast(e.message, 'err'); }
}

async function trSearch() {
  try {
    // 处置项目表本身带 is_tcm：中医就诊优先只看中医操作（针灸推拿等），
    // 西医就诊只看非中医操作；本类目没有项目时退回显示全部，不给死局
    const vid = val('tr_visit') || P3.visitId;
    let isTcm = MODE.def === 'TCM';
    if (vid) {
      try {
        const v = await api('/api/visit/get', { id: vid }, 'GET');
        isTcm = MODE.isTcm(v);
      } catch (e) { /* 取不到就按当前模式 */ }
    }
    const d = await api('/api/treatment/item/search', { keyword: val('tr_kw') }, 'GET');
    let list = d.items || [];
    const matched = list.filter(x => (Number(x.is_tcm) === 1) === isTcm);
    const filtered = matched.length > 0;
    if (filtered) list = matched;
    const note = filtered
      ? `<div class="hint">已按${isTcm ? '中医' : '西医'}就诊过滤：只显示${isTcm ? '中医操作（针灸/推拿等）' : '西医操作'}。
         <a href="javascript:void(0)" onclick="trShowAll()">看全部项目</a></div>`
      : `<div class="hint">本类目暂无项目，已显示全部（共 ${list.length} 项）。</div>`;
    $('#tr_result').innerHTML = note
      + `<table><thead><tr><th>项目</th><th>类别</th><th>单价</th><th></th></tr></thead><tbody>${
        list.map(x => `<tr><td>${esc(x.name)}${Number(x.is_tcm) === 1 ? ' <span class="tag tcm">中医</span>' : ''}</td>
          <td>${esc(x.category)}</td><td>${esc(yuan(x.price_fen))}</td>
          <td><button class="btn sm" onclick="trOrder(${x.id})">开立 ×1</button></td></tr>`).join('')
        || '<tr><td class="empty" colspan="4">无匹配项目</td></tr>'}</tbody></table>`;
  } catch (e) { toast(e.message, 'err'); }
}

async function trShowAll() {
  TR_ALL = true;
  try {
    const d = await api('/api/treatment/item/search', { keyword: val('tr_kw') }, 'GET');
    $('#tr_result').innerHTML = `<div class="hint">已显示全部项目</div>
      <table><thead><tr><th>项目</th><th>类别</th><th>单价</th><th></th></tr></thead><tbody>${
      (d.items || []).map(x => `<tr><td>${esc(x.name)}${Number(x.is_tcm) === 1 ? ' <span class="tag tcm">中医</span>' : ''}</td>
        <td>${esc(x.category)}</td><td>${esc(yuan(x.price_fen))}</td>
        <td><button class="btn sm" onclick="trOrder(${x.id})">开立 ×1</button></td></tr>`).join('')
      || '<tr><td class="empty" colspan="4">无匹配项目</td></tr>'}</tbody></table>`;
  } catch (e) { toast(e.message, 'err'); }
}

async function trOrder(itemId) {
  const vid = val('tr_visit');
  if (!vid) { toast('请填写就诊 ID', 'err'); return; }
  try {
    const r = await api('/api/treatment/order', { visit_id: Number(vid), items: [{ item_id: itemId, quantity: 1 }] });
    P3.visitId = vid;
    toast('已开立 ' + r.count + ' 条处置，合计 ' + yuan(r.total_amount_fen) + ' 元', 'ok');
    trLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function trLoad() {
  const vid = val('tr_visit') || P3.visitId;
  if (!vid) { toast('请先选择就诊', 'err'); return; }
  P3.visitId = vid;
  try {
    const d = await api('/api/treatment/list', { visit_id: vid }, 'GET');
    const items = d.items || [];
    $('#tr_list').innerHTML = `<table><thead><tr><th>执行号</th><th>项目</th><th>金额</th><th>状态</th><th>操作</th></tr></thead><tbody>${
      items.map(t => `<tr><td>${esc(t.exec_no)}</td><td>${esc(t.item_name_snap)}</td>
        <td>${esc(yuan(t.amount_fen))}</td><td>${esc(t.status)}</td><td>
        <button class="btn sm ghost" onclick="trAct(${t.id},'start')">开始</button>
        <button class="btn sm ghost" onclick="trAct(${t.id},'complete')">完成</button>
        <button class="btn sm ghost" onclick="trPickExec(${t.id})">记录</button>
        <button class="btn sm ghost" onclick="trInterrupt(${t.id})">中断</button>
        <button class="btn sm ghost" onclick="trCancel(${t.id})">取消</button></td></tr>`).join('')
      || '<tr><td class="empty" colspan="5">该就诊暂无处置</td></tr>'}</tbody></table>`;
    const sel = document.getElementById('tr_exec');
    if (sel) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">— 选择已开立的处置 —</option>' + items.map(t =>
        `<option value="${t.id}">${esc(t.exec_no)} · ${esc(t.item_name_snap)} · ${esc(t.status)}</option>`).join('');
      if (cur) sel.value = cur;
    }
  } catch (e) { toast(e.message, 'err'); }
}

function trPickExec(id) {
  const sel = document.getElementById('tr_exec');
  if (sel) { sel.value = String(id); }
  trLoadExec();
}

async function trAct(id, act) {
  const map = { start: '/api/treatment/start', complete: '/api/treatment/complete' };
  try { await api(map[act], { id: id }); toast('操作成功', 'ok'); trLoad(); }
  catch (e) { toast(e.message, 'err'); }
}
async function trInterrupt(id) {
  const r = prompt('中断原因（必填）'); if (!r) return;
  try { await api('/api/treatment/interrupt', { id: id, reason: r }); toast('已中断', 'ok'); trLoad(); }
  catch (e) { toast(e.message, 'err'); }
}
async function trCancel(id) {
  const r = prompt('取消原因（必填）'); if (!r) return;
  try { await api('/api/treatment/cancel', { id: id, reason: r }); toast('已取消', 'ok'); trLoad(); }
  catch (e) { toast(e.message, 'err'); }
}

async function trLoadExec() {
  P3.execId = val('tr_exec');
  try {
    const t = await api('/api/treatment/tcm/list', { exec_id: P3.execId }, 'GET');
    const p = await api('/api/treatment/process/list', { exec_id: P3.execId }, 'GET');
    $('#tr_exec_info').innerHTML = `<div class="hint">中医记录 ${esc((t.items || []).length)} 条 / 过程记录 ${esc((p.items || []).length)} 条</div>`;
  } catch (e) { toast(e.message, 'err'); }
}

async function trTcmSave() {
  if (!P3.execId) { toast('请先读取处置执行 ID', 'err'); return; }
  try {
    await api('/api/treatment/tcm/save', {
      exec_id: Number(P3.execId), therapy_type: 'ACUP', points: val('tcm_points'),
      manipulation: val('tcm_manip'), retain_minutes: num(val('tcm_retain'), 0),
    });
    toast('中医治疗记录已保存', 'ok'); trLoadExec();
  } catch (e) { toast(e.message, 'err'); }
}

async function trProcAdd() {
  if (!P3.execId) { toast('请先读取处置执行 ID', 'err'); return; }
  try {
    await api('/api/treatment/process/add', { exec_id: Number(P3.execId), phase: 'STEP', content: val('proc_content') });
    toast('过程记录已追加', 'ok'); trLoadExec();
  } catch (e) { toast(e.message, 'err'); }
}

/* ===== 收费 ===== */
async function viewBilling() {
  let settles = [];
  try { settles = (await api('/api/settlement/list', {}, 'GET')).items || []; } catch (e) { }
  try { await loadVisitChoices(); } catch (e) { }
  return `
  <div class="card">
    <h3>账单（F58/F59/F60）</h3>
    <div class="row">
      ${visitSelectHtml('bl_visit', { selected: P3.visitId, onchange: 'blVisitChanged()' })}
      <button class="btn sm" onclick="blCreate()">生成账单</button>
      <select id="bl_bill" class="pick" onchange="blLoad()"><option value="">— 选择账单 —</option></select>
      <button class="btn sm ghost" onclick="blLoad()">查询账单</button>
    </div>
    <div id="bl_info" class="hint">选择就诊后可生成账单，再在此查看明细、收费、退款与冲正</div>
    <div id="bl_due" style="margin-top:6px"></div>
  </div>

  <div class="card">
    <h3>收费 / 退款 / 冲正（F59/F61/F62）</h3>
    <div class="row">
      <button class="btn" onclick="blPayFast('CASH')">现金收讫</button>
      <button class="btn ghost" onclick="blPayFast('WECHAT')">微信收讫</button>
      <button class="btn ghost" onclick="blPayFast('ALIPAY')">支付宝收讫</button>
      <button class="btn ghost" onclick="blPayFast('CARD')">银行卡收讫</button>
      <span class="hint">点一下就按应收金额收齐，不用手打金额（现金会问实收、自动算找零）</span>
    </div>
    <details><summary class="hint">手工填金额收费（拆多笔时用）</summary>
    <div class="row" style="margin-top:6px">
      <select id="pay_channel"><option value="CASH">现金</option><option value="WECHAT">微信</option>
        <option value="ALIPAY">支付宝</option><option value="CARD">银行卡</option></select>
      <input id="pay_amount" placeholder="金额（元）" style="width:120px">
      <button class="btn sm" onclick="blPay()">收费</button>
      <span class="hint">无部分支付：多笔合计必须等于应收</span>
    </div>
    </details>
    <div class="hint">日常退款 / 冲正：直接在上方账单明细对应行点「退款」或「冲正」。</div>
    <details style="margin-top:6px"><summary class="hint">高级：按明细行操作（需知道行 ID）</summary>
    <div class="row">
      <input id="rf_item" placeholder="账单明细行 ID" style="width:160px">
      <input id="rf_amount" placeholder="退款金额（元）" style="width:140px">
      <input id="rf_reason" placeholder="退款原因（必填）" style="flex:1">
      <button class="btn sm ghost" onclick="blRefund()">退款</button>
    </div>
    <div class="row">
      <input id="rv_item" placeholder="账单明细行 ID" style="width:160px">
      <input id="rv_amount" placeholder="冲正金额（元）" style="width:140px">
      <input id="rv_reason" placeholder="冲正原因（必填）" style="flex:1">
      <button class="btn sm ghost" onclick="blReversal()">红字冲正</button>
    </div>
    </details>
  </div>

  <div class="card">
    <h3>日结（F64）</h3>
    <div id="st_state" class="hint">读取今日日结状态…</div>
    <div class="row" style="margin-top:6px">
      <input id="st_date" type="date" style="width:160px">
      <button class="btn sm" onclick="blSettle()">执行日结</button>
      <button class="btn sm ghost" onclick="blReopen()">撤销今日日结</button>
    </div>
    <div class="hint">日结后当日账单锁定，只能红字冲正。<b>误点日结可用「撤销今日日结」恢复当天营业</b>（需填原因，写高风险审计）。</div>
    <table><thead><tr><th>日结号</th><th>营业日</th><th>实收</th><th>退款</th><th>净收</th><th>未收费</th></tr></thead>
      <tbody>${settles.map(s => `<tr><td>${esc(s.settlement_no)}${s.voided_at ? '<span class="tag danger" style="margin-left:6px">已撤销</span>' : ''}</td>
        <td>${esc(s.biz_date)}</td>
        <td>${esc(yuan(s.paid_amount_fen))}</td><td>${esc(yuan(s.refund_amount_fen))}</td>
        <td>${esc(yuan(s.net_amount_fen))}</td><td>${esc(s.pending_count)}</td></tr>`).join('')
    || '<tr><td class="empty" colspan="6">暂无日结记录</td></tr>'}</tbody></table>
  </div>

  <div class="card">
    <h3>财务查询（F65）</h3>
    <div class="row">
      <input id="fq_date" type="date" style="width:160px">
      <button class="btn sm ghost" onclick="blQuery()">按营业日查询账单</button>
    </div>
    <div id="fq_result" class="hint"></div>
  </div>

  <div class="card">
    <h3>收费项目维护（F57）</h3>
    <div class="row">
      <input id="ci_code" placeholder="编码" style="width:120px">
      <input id="ci_name" placeholder="名称" style="width:160px">
      <input id="ci_price" placeholder="单价（元）" style="width:110px">
      <label class="hint"><input type="checkbox" id="ci_auto"> 就诊自动加</label>
      <button class="btn sm" onclick="blChargeCreate()">新增项目</button>
    </div>
    <div class="row">
      <input id="ci_kw" placeholder="搜索收费项目" style="width:200px">
      <button class="btn sm ghost" onclick="blChargeSearch()">搜索</button>
    </div>
    <div id="ci_list" class="hint"></div>
  </div>

  <div class="card">
    <h3>账单作废 / 退款记录 / 支付流水</h3>
    <div class="row">
      <input id="bv_bill" placeholder="账单 ID" style="width:120px">
      <input id="bv_reason" placeholder="作废原因（必填）" style="flex:1">
      <button class="btn sm warn" onclick="blVoid()">作废账单</button>
    </div>
    <div class="row">
      <input id="rf_return" placeholder="退药单 ID" style="width:140px">
      <button class="btn sm ghost" onclick="blRefundByReturn()">按退药单退款（F63）</button>
      <span class="hint">仅已确认的退药单可退，且不可重复退</span>
    </div>
    <div class="row">
      <input id="pm_date" type="date" style="width:160px">
      <select id="pm_channel"><option value="">全部渠道</option><option value="CASH">现金</option>
        <option value="WECHAT">微信</option><option value="ALIPAY">支付宝</option>
        <option value="CARD">银行卡</option></select>
      <button class="btn sm ghost" onclick="blPayments()">查支付流水</button>
      <button class="btn sm ghost" onclick="blRefundList()">查退款记录</button>
    </div>
    <div id="pm_list" class="hint"></div>
  </div>`;
}

async function blChargeCreate() {
  const code = val('ci_code'), name = val('ci_name');
  if (!code || !name) { toast('编码与名称必填', 'err'); return; }
  try {
    await api('/api/charge_item/create', { item_code: code, name: name,
      price_fen: Math.round(parseFloat(val('ci_price') || 0) * 100),
      auto_add_on_visit: document.getElementById('ci_auto').checked ? 1 : 0 });
    toast('已新增收费项目', 'ok'); await blChargeSearch();
  } catch (e) { toast(e.message, 'err'); }
}
async function blChargeSearch() {
  try {
    const items = (await api('/api/charge_item/search', { keyword: val('ci_kw') || '' }, 'GET')).items || [];
    $('#ci_list').innerHTML = items.length
      ? `<table class="tbl"><thead><tr><th>编码</th><th>名称</th><th>单价</th><th>自动加</th></tr></thead><tbody>${
        items.map(i => `<tr><td>${esc(i.item_code)}</td><td>${esc(i.name)}</td>
          <td>${(i.price_fen / 100).toFixed(2)} 元</td><td>${i.auto_add_on_visit ? '是' : '否'}</td></tr>`).join('')
        }</tbody></table>`
      : '无';
  } catch (e) { toast(e.message, 'err'); }
}
async function blVoid() {
  const id = parseInt(val('bv_bill')), reason = val('bv_reason');
  if (!id || !reason) { toast('账单 ID 与原因必填', 'err'); return; }
  try { await api('/api/bill/void', { id: id, reason: reason }); toast('账单已作废', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function blRefundByReturn() {
  const id = parseInt(val('rf_return'));
  if (!id) { toast('请填退药单 ID', 'err'); return; }
  try { const r = await api('/api/refund/by_drug_return', { drug_return_id: id }); toast('已退款 ' + r.refund_no, 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function blPayments() {
  const b = {};
  if (val('pm_date')) { b.date_from = val('pm_date'); b.date_to = val('pm_date'); }
  if (val('pm_channel')) b.channel = val('pm_channel');
  try {
    const items = (await api('/api/finance/payments', b, 'GET')).items || [];
    const sum = items.reduce((s, p) => s + (p.amount_fen || 0), 0);
    $('#pm_list').innerHTML = items.length
      ? `<div class="hint">${items.length} 笔，合计 ${(sum / 100).toFixed(2)} 元</div>
         <table class="tbl"><thead><tr><th>时间</th><th>渠道</th><th>金额</th><th>找零</th><th>状态</th></tr></thead><tbody>${
        items.map(p => `<tr><td>${esc(p.paid_at || '')}</td><td>${esc(p.channel)}</td>
          <td>${(p.amount_fen / 100).toFixed(2)}</td><td>${((p.change_amount_fen || 0) / 100).toFixed(2)}</td>
          <td>${esc(p.status)}</td></tr>`).join('')}</tbody></table>`
      : '无记录';
  } catch (e) { toast(e.message, 'err'); }
}
async function blRefundList() {
  try {
    const items = (await api('/api/refund/list', { limit: 50 }, 'GET')).items || [];
    $('#pm_list').innerHTML = items.length
      ? `<div class="hint">退款 ${items.length} 笔</div>
         <table class="tbl"><thead><tr><th>退款单号</th><th>账单</th><th>金额</th><th>原因</th><th>状态</th></tr></thead><tbody>${
        items.map(r => `<tr><td>${esc(r.refund_no)}</td><td>${esc(r.bill_id)}</td>
          <td>${((r.amount_fen || 0) / 100).toFixed(2)}</td><td>${esc(r.reason || '')}</td>
          <td>${esc(r.status)}</td></tr>`).join('')}</tbody></table>`
      : '无退款记录';
  } catch (e) { toast(e.message, 'err'); }
}

async function blVisitChanged() {
  const vid = val('bl_visit');
  P3.visitId = vid;
  await fillBillSelect('bl_bill', vid);
  const sel = document.getElementById('bl_bill');
  if (sel && sel.value) { P3.billId = sel.value; await blLoad(); }
  else if (vid) { toast('该就诊还没有账单，点「生成账单」即可', 'ok'); }
}

async function blCreate() {
  const vid = val('bl_visit');
  if (!vid) { toast('请先选择就诊', 'err'); return; }
  try {
    const b = await api('/api/bill/create', { visit_id: Number(vid) });
    toast('账单已生成：' + b.bill_no, 'ok');
    await fillBillSelect('bl_bill', vid);
    const sel = document.getElementById('bl_bill');
    if (sel) sel.value = String(b.id);
    P3.billId = b.id; blLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function blLoad() {
  const id = val('bl_bill') || P3.billId;
  if (!id) { toast('请先选择账单', 'err'); return; }
  P3.billId = id;
  try {
    const b = await api('/api/bill/get', { id: id }, 'GET');
    // 账单行的 source_type 直接标明来源：HERB 中药 / DRUG 西药成药 /
    // TREATMENT 处置 / LAB 检验 / CHARGE 收费项目，不必再反查处方
    const srcTag = (i) => {
      switch (i.source_type) {
        case 'HERB': return '<span class="tag tcm">中药</span>';
        case 'DRUG': return '<span class="tag west">西药/成药</span>';
        case 'TREATMENT': return '<span class="tag muted">处置</span>';
        case 'LAB': return '<span class="tag muted">检验</span>';
        case 'CHARGE': return '<span class="tag muted">收费项目</span>';
        default: return esc(i.source_type || '');
      }
    };
    // 中药费 / 西药费 / 其它 分开小计，账目一眼可辨
    let herbFee = 0, westFee = 0, otherFee = 0;
    (b.items || []).forEach(i => {
      const amt = Number(i.amount_fen) || 0;
      if (i.source_type === 'HERB') herbFee += amt;
      else if (i.source_type === 'DRUG') westFee += amt;
      else otherFee += amt;
    });
    $('#bl_info').innerHTML = `<div>账单 ${esc(b.bill_no)} · 状态 ${esc(b.status)}
      · 应收 ${esc(yuan(b.total_amount_fen))} 元 · 已收 ${esc(yuan(b.paid_amount_fen))} 元
      · 已退 ${esc(yuan(b.refunded_amount_fen))} 元</div>
      <div class="row" style="margin-top:4px">
        <span class="tag tcm">中药费 ${esc(yuan(herbFee))} 元</span>
        <span class="tag west">西药/成药 ${esc(yuan(westFee))} 元</span>
        <span class="tag muted">其它 ${esc(yuan(otherFee))} 元</span>
      </div>
      <table><thead><tr><th>行</th><th>项目</th><th>归类</th><th>金额</th><th>操作</th></tr></thead><tbody>${
      (b.items || []).map(i => `<tr><td>${esc(i.line_no)}</td><td>${esc(i.name_snap)}</td>
        <td>${srcTag(i)}</td><td>${esc(yuan(i.amount_fen))}</td>
        <td><button class="btn sm ghost" onclick="blItemRefund(${i.id}, this)">退款</button>
            <button class="btn sm ghost" onclick="blItemReversal(${i.id}, this)">冲正</button></td></tr>`).join('')
      || '<tr><td class="empty" colspan="5">无明细</td></tr>'}</tbody></table>
      <div class="hint">支付 ${esc((b.payments || []).length)} 笔 / 退款 ${esc((b.refunds || []).length)} 笔
        ${b.settlement_id ? ' · 已日结（锁定）' : ''}</div>`;
    // 待收金额自动带出来：手工收费时不用再照着屏幕上方的数字敲一遍
    const el = document.getElementById('pay_amount');
    if (el) {
      const paid = (b.payments || []).filter(p => p.status === 'SUCCESS')
        .reduce((s, p) => s + p.amount_fen, 0);
      const rest = (b.total_amount_fen || 0) - paid;
      el.value = rest > 0 ? (rest / 100).toFixed(2) : '';
    }
    const box = document.getElementById('bl_due');
    if (box) {
      const paid = (b.payments || []).filter(p => p.status === 'SUCCESS')
        .reduce((s, p) => s + p.amount_fen, 0);
      const rest = (b.total_amount_fen || 0) - paid;
      box.innerHTML = rest > 0
        ? `<span class="tag warn">待收 ${esc(yuan(rest))} 元</span> 点上面「现金/微信/支付宝收讫」直接收齐`
        : '<span class="tag ok">已收讫</span>';
    }
  } catch (e) { toast(e.message, 'err'); }
}

/* 一键收讫：按应收金额收齐，不用手打金额。
   现金场景问一句实收，后端自动算找零（change_amount_fen）。 */
async function blPayFast(channel) {
  if (!P3.billId) { toast('请先生成或选择账单', 'err'); return; }
  try {
    const b = await api('/api/bill/get', { id: P3.billId }, 'GET');
    const paid = (b.payments || []).filter(p => p.status === 'SUCCESS')
      .reduce((s, p) => s + p.amount_fen, 0);
    const rest = b.total_amount_fen - paid;
    if (rest <= 0) { toast('这张账单已经收讫了', 'ok'); return; }
    let received = rest;
    if (channel === 'CASH') {
      const got = prompt('实收现金（元）：', (rest / 100).toFixed(2));
      if (got === null) return;
      received = Math.round(parseFloat(got) * 100);
      if (!received || received < rest) { toast('实收不能少于应收 ' + (rest / 100).toFixed(2) + ' 元', 'err'); return; }
    }
    const r = await api('/api/bill/pay', {
      bill_id: Number(P3.billId),
      payments: [{ channel: channel, amount_fen: rest, received_amount_fen: received }],
    });
    const change = received - rest;
    toast('收费成功：' + yuan(r.paid_amount_fen) + ' 元' +
      (change > 0 ? '，找零 ' + yuan(change) + ' 元' : ''), 'ok');
    await blLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function blPay() {
  if (!P3.billId) { toast('请先查询账单', 'err'); return; }
  try {
    const b = await api('/api/bill/get', { id: P3.billId }, 'GET');
    const amt = Math.round(num(val('pay_amount'), 0) * 100);
    if (!amt) { toast('请填写金额', 'err'); return; }
    const paid = (b.payments || []).filter(p => p.status === 'SUCCESS')
      .reduce((s, p) => s + p.amount_fen, 0);
    const rest = b.total_amount_fen - paid;
    const r = await api('/api/bill/pay', {
      bill_id: P3.billId,
      payments: [{ channel: $('#pay_channel').value, amount_fen: amt === rest ? amt : rest }],
    });
    toast('收费成功：' + yuan(r.paid_amount_fen) + ' 元', 'ok');
    blLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function blItemRefund(itemId, btn) {
  const name = btn ? btn.closest('tr').children[1].innerText : '本行';
  const amt = prompt(`对「${name}」退款金额（元）：`, '');
  if (amt === null) return;
  const fen = Math.round(parseFloat(amt) * 100);
  if (!fen || fen <= 0) { toast('金额不正确', 'err'); return; }
  const reason = prompt('退款原因（必填）：', '患者要求退费');
  if (!reason) { toast('必须填写退款原因', 'err'); return; }
  try {
    const r = await api('/api/refund/create', {
      bill_id: Number(P3.billId), reason: reason,
      items: [{ bill_item_id: itemId, amount_fen: fen }],
    });
    toast('退款成功：' + r.refund_no, 'ok'); blLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function blItemReversal(itemId, btn) {
  const name = btn ? btn.closest('tr').children[1].innerText : '本行';
  const amt = prompt(`对「${name}」冲正金额（元）：`, '');
  if (amt === null) return;
  const fen = Math.round(parseFloat(amt) * 100);
  if (!fen || fen <= 0) { toast('金额不正确', 'err'); return; }
  const reason = prompt('冲正原因（必填）：', '');
  if (!reason) { toast('必须填写冲正原因', 'err'); return; }
  try {
    const r = await api('/api/reversal/create', {
      bill_id: Number(P3.billId), reason: reason,
      items: [{ bill_item_id: itemId, amount_fen: fen }],
    });
    toast('冲正成功：' + r.reversal_no, 'ok'); blLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function blRefund() {
  if (!P3.billId) { toast('请先查询账单', 'err'); return; }
  try {
    const r = await api('/api/refund/create', {
      bill_id: P3.billId, reason: val('rf_reason'),
      items: [{ bill_item_id: Number(val('rf_item')), amount_fen: Math.round(num(val('rf_amount'), 0) * 100) }],
    });
    toast('退款成功：' + r.refund_no, 'ok'); blLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function blReversal() {
  if (!P3.billId) { toast('请先查询账单', 'err'); return; }
  try {
    const r = await api('/api/reversal/create', {
      bill_id: P3.billId, reason: val('rv_reason'),
      items: [{ bill_item_id: Number(val('rv_item')), amount_fen: Math.round(num(val('rv_amount'), 0) * 100) }],
    });
    toast('冲正成功：' + r.reversal_no, 'ok'); blLoad();
  } catch (e) { toast(e.message, 'err'); }
}

async function blSettle() {
  const d = val('st_date') || today();
  if (!confirm('确认对营业日 ' + d + ' 执行日结？\n\n'
    + '日结后当天将不能再生成账单和收费，只能做红字冲正。\n'
    + '如果点错了，可以用「撤销今日日结」恢复（仅限当天）。')) return;
  try {
    const body = {};
    if (val('st_date')) body.biz_date = val('st_date');
    const s = await api('/api/settlement/close', body);
    toast('日结完成：' + s.settlement_no + '，净收 ' + yuan(s.net_amount_fen) + ' 元', 'ok');
    render();
  } catch (e) { toast(e.message, 'err'); }
}

async function blReopen() {
  const reason = prompt('撤销今日日结的原因（至少 4 个字，会写高风险审计）：', '日结误操作，恢复当天收费');
  if (reason === null) return;
  if (reason.trim().length < 4) { toast('原因至少 4 个字', 'err'); return; }
  try {
    const r = await api('/api/settlement/reopen', { reason: reason.trim() });
    toast('已撤销 ' + r.settlement_no + '，解锁 ' + r.unlocked_bills + ' 张账单', 'ok');
    render();
  } catch (e) { toast(e.message, 'err'); }
}

async function blLoadSettleState() {
  const el = document.getElementById('st_state');
  if (!el) return;
  try {
    const s = await api('/api/settlement/today', {}, 'GET');
    el.innerHTML = s.settled
      ? `<span class="tag warn">今日已日结</span> ${esc(s.settlement.settlement_no)}
         · 净收 ${esc(yuan(s.settlement.net_amount_fen))} 元
         —— 当日账单已锁定，不能再建账单/收费；如需继续营业请点「撤销今日日结」`
      : '<span class="tag ok">今日未日结</span> —— 可以正常建账单与收费';
  } catch (e) { el.textContent = ''; }
}

async function blQuery() {
  try {
    const d = await api('/api/finance/bills',
      val('fq_date') ? { date_from: val('fq_date'), date_to: val('fq_date'), limit: 100 } : { limit: 50 }, 'GET');
    $('#fq_result').innerHTML = `<table><thead><tr><th>账单号</th><th>状态</th><th>应收</th><th>已收</th><th>已退</th></tr></thead><tbody>${
      (d.items || []).map(b => `<tr><td>${esc(b.bill_no)}</td><td>${esc(b.status)}</td>
        <td>${esc(yuan(b.total_amount_fen))}</td><td>${esc(yuan(b.paid_amount_fen))}</td>
        <td>${esc(yuan(b.refunded_amount_fen))}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="5">无账单</td></tr>'}</tbody></table>`;
  } catch (e) { toast(e.message, 'err'); }
}

/* ===== 经营统计（M15）===== */
const RPT = { kind: 'revenue', data: null };

function viewReport() {
  return `
  <div class="card">
    <h3>经营统计（M15 · 只读视图）</h3>
    <div class="row">
      <select id="rp_kind" onchange="RPT.kind=this.value">
        <option value="revenue">日营收与班结</option>
        <option value="visit">门诊量</option>
        <option value="doctor">医生工作量</option>
        <option value="stock">药品进销存</option>
        <option value="prescription">处方统计</option>
        <option value="patient">患者与疾病谱</option>
      </select>
      <input id="rp_from" type="date" style="width:150px">
      <input id="rp_to" type="date" style="width:150px">
      <select id="rp_type" style="width:120px" onchange="rpTypeChanged()">
        <option value="">全部诊疗</option>
        <option value="TCM">只看中医</option>
        <option value="WEST">只看西医</option>
      </select>
      <button class="btn sm" onclick="rpLoad()">查询</button>
      <button class="btn sm ghost" onclick="rpExport()">导出 CSV</button>
      <button class="btn sm ghost" onclick="rpPrint()">打印预览</button>
      <button class="btn sm ghost" onclick="rpDefs()">报表口径定义</button>
    </div>
    <div id="rp_body" class="hint">选择报表类型与区间后查询（默认最近 30 天）</div>
  </div>`;
}

async function rpDefs() {
  try {
    const d = await api('/api/report/definitions', {}, 'GET');
    const items = d.items || d.definitions || [];
    modal('报表口径定义', items.length
      ? `<table class="tbl"><thead><tr><th>报表</th><th>口径</th><th>来源</th></tr></thead><tbody>${
        items.map(x => `<tr><td>${esc(x.name || x.report_code)}</td>
          <td>${esc(x.basis || x.description || '')}</td><td>${esc(x.source || '')}</td></tr>`).join('')}</tbody></table>`
      : '<div class="hint">无定义</div>');
  } catch (e) { toast(e.message, 'err'); }
}

function rpRange() {
  const b = {};
  if (val('rp_from')) b.from = val('rp_from');
  if (val('rp_to')) b.to = val('rp_to');
  const t = val('rp_type');
  if (t) b.visit_type = t;
  return b;
}

/* 诊疗类型筛选目前只对「门诊量」生效（其它报表无 visit_type 维度），
   切换时说清楚，避免以为全都过滤了 */
function rpTypeChanged() {
  const t = val('rp_type');
  if (!t) return;
  if (RPT.kind !== 'visit') {
    toast('诊疗类型筛选只对「门诊量」生效，已为你切过去', 'ok');
    RPT.kind = 'visit';
    const sel = document.getElementById('rp_kind');
    if (sel) sel.value = 'visit';
    rpLoad();
  }
}

async function rpLoad() {
  const kind = RPT.kind;
  const paths = {
    revenue: '/api/report/revenue_daily', visit: '/api/report/visit_stats',
    doctor: '/api/report/doctor_workload', stock: '/api/report/stock_ledger',
    prescription: '/api/report/prescription_stats', patient: '/api/report/patient_stats',
  };
  try {
    const d = await api(paths[kind], rpRange(), 'GET');
    RPT.data = d;
    const vt = (d.criteria && d.criteria.visit_type) || 'ALL';
    const vtLabel = vt === 'TCM' ? '　<b>仅中医</b>' : (vt === 'WEST' ? '　<b>仅西医</b>' : '');
    $('#rp_body').innerHTML = `<div class="hint">口径：${esc(d.criteria.basis)}　区间 ${esc(d.criteria.from)} ~ ${esc(d.criteria.to)}${vtLabel}</div>`
      + rpRender(kind, d);
  } catch (e) { toast(e.message, 'err'); }
}

function rpRender(kind, d) {
  const y = f => yuan(f);
  if (kind === 'revenue') {
    const s = d.summary;
    return `<table><tbody>
      <tr><th>实收</th><td>${esc(s.paid_amount_yuan)} 元</td><th>退款</th><td>${esc(s.refund_amount_yuan)} 元</td></tr>
      <tr><th>净营收</th><td><b>${esc(s.net_amount_yuan)} 元</b></td><th>门诊量</th><td>${esc(s.visit_count)}</td></tr>
      <tr><th>客单价</th><td>${esc(s.avg_ticket_yuan || '-')} 元</td><th>未结账</th><td>${esc(s.pending_count)} 笔 / ${esc(y(s.pending_amount_fen))} 元</td></tr>
      </tbody></table>
      <h4 style="margin-top:10px">分渠道</h4>
      <table><thead><tr><th>渠道</th><th>金额(元)</th><th>笔数</th></tr></thead><tbody>${
      (d.by_channel || []).map(c => `<tr><td>${esc(c.channel)}</td><td>${esc(c.amount_yuan)}</td><td>${esc(c.count)}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="3">无数据</td></tr>'}</tbody></table>
      <h4 style="margin-top:10px">按日</h4>
      <table><thead><tr><th>营业日</th><th>实收(元)</th><th>退款(元)</th><th>净营收(元)</th></tr></thead><tbody>${
      (d.by_day || []).map(r => `<tr><td>${esc(r.biz_date)}</td><td>${esc(y(r.paid_amount_fen))}</td>
        <td>${esc(y(r.refund_amount_fen))}</td><td>${esc(y(r.net_amount_fen))}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="4">无数据</td></tr>'}</tbody></table>`;
  }
  if (kind === 'visit') {
    const typeName = t => t === 'TCM' ? '中医' : (t === 'WEST' ? '西医' : '未分类（旧数据）');
    return `<table><tbody><tr><th>就诊人次</th><td><b>${esc(d.summary.visit_count)}</b></td>
      <th>初诊</th><td>${esc(d.summary.first_visit)}</td>
      <th>复诊率</th><td>${esc(d.summary.revisit_rate)}%</td></tr></tbody></table>
      <h4 style="margin-top:10px">按诊疗类型</h4>
      <table><thead><tr><th>类型</th><th>人次</th></tr></thead><tbody>${
      (d.by_type || []).map(t => `<tr><td>${esc(typeName(t.visit_type))}</td>
        <td>${esc(t.count)}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="2">无数据</td></tr>'}</tbody></table>
      <h4 style="margin-top:10px">趋势</h4>
      <table><thead><tr><th>期间</th><th>人次</th><th>初诊</th><th>复诊</th></tr></thead><tbody>${
      (d.trend || []).map(r => `<tr><td>${esc(r.period)}</td><td>${esc(r.total)}</td>
        <td>${esc(r.first_visit)}</td><td>${esc(r.revisit)}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="4">无数据</td></tr>'}</tbody></table>`;
  }
  if (kind === 'doctor') {
    return `<table><thead><tr><th>医生</th><th>接诊</th><th>开方</th><th>处置</th><th>营收(元)</th></tr></thead><tbody>${
      (d.items || []).map(r => `<tr><td>${esc(r.doctor_name)}</td><td>${esc(r.visit_count)}</td>
        <td>${esc(r.rx_count)}</td><td>${esc(r.treatment_count)}</td>
        <td>${esc(r.revenue_yuan)}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="5">无数据</td></tr>'}</tbody></table>`;
  }
  if (kind === 'stock') {
    return `<h4>品目汇总</h4>
      <table><thead><tr><th>品目</th><th>单位</th><th>期初</th><th>入库</th><th>出库</th>
        <th>退药</th><th>报损</th><th>盘点</th><th>期末</th><th>金额(元)</th></tr></thead><tbody>${
      (d.summary || []).map(r => `<tr><td>${esc(r.item_name)}</td><td>${esc(r.unit)}</td>
        <td>${esc(r.opening_qty)}</td><td>${esc(r.in_qty)}</td><td>${esc(r.out_qty)}</td>
        <td>${esc(r.return_qty)}</td><td>${esc(r.loss_qty)}</td><td>${esc(r.check_qty)}</td>
        <td><b>${esc(r.ending_qty)}</b></td><td>${esc(y(r.amount_fen))}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="10">无数据</td></tr>'}</tbody></table>
      <h4 style="margin-top:10px">批次明细</h4>
      <table><thead><tr><th>批次</th><th>效期</th><th>期末</th><th>金额(元)</th></tr></thead><tbody>${
      (d.batches || []).map(r => `<tr><td>${esc(r.batch_no)}</td><td>${esc(r.expiry_date)}</td>
        <td>${esc(r.ending_qty)}${r.negative_alert ? ' ⚠ 负值' : ''}</td>
        <td>${esc(r.amount_yuan)}</td></tr>`).join('')
      || '<tr><td class="empty" colspan="4">无数据</td></tr>'}</tbody></table>`;
  }
  if (kind === 'prescription') {
    const s = d.summary;
    return `<table><tbody>
      <tr><th>有效处方</th><td><b>${esc(s.valid_count)}</b></td><th>作废</th><td>${esc(s.void_count)}</td></tr>
      <tr><th>处方金额</th><td>${esc(s.amount_yuan)} 元</td><th>平均处方</th><td>${esc(y(s.avg_amount_fen))} 元</td></tr>
      <tr><th>抗生素使用率</th><td>${esc(s.antibiotic_rate)}%</td><th>注射剂使用率</th><td>${esc(s.injection_rate)}%</td></tr>
      <tr><th>平均味数</th><td>${esc(s.avg_herb_lines)}</td><th>平均付数</th><td>${esc(s.avg_doses)}</td></tr>
      </tbody></table>
      <table><thead><tr><th>类型</th><th>数量</th><th>占比</th><th>金额(元)</th></tr></thead><tbody>${
      (d.by_type || []).map(r => {
        const cn = { HERB: '饮片方（中医）', GRANULE: '颗粒方（中医）', WEST: '成药方（西医）' }[r.prescription_type]
          || r.prescription_type;
        return `<tr><td>${esc(cn)}</td><td>${esc(r.count)}</td>
        <td>${esc(r.count_pct)}%</td><td>${esc(r.amount_yuan)}</td></tr>`;
      }).join('')
      || '<tr><td class="empty" colspan="4">无数据</td></tr>'}</tbody></table>`;
  }
  const s = d.summary;
  return `<table><tbody>
    <tr><th>新增患者</th><td>${esc(s.new_patient_count)}</td><th>活跃患者</th><td>${esc(s.active_patient_count)}</td></tr>
    <tr><th>就诊人次</th><td>${esc(s.visit_count)}</td><th>复诊率</th><td>${esc(s.revisit_rate)}%</td></tr>
    </tbody></table>
    <h4 style="margin-top:10px">西医疾病谱 TOP</h4>
    <table><thead><tr><th>编码</th><th>名称</th><th>就诊人次</th></tr></thead><tbody>${
    (d.disease_top_west || []).map(r => `<tr><td>${esc(r.code)}</td><td>${esc(r.name)}</td>
      <td>${esc(r.patient_visits)}</td></tr>`).join('')
    || '<tr><td class="empty" colspan="3">无数据</td></tr>'}</tbody></table>
    <h4 style="margin-top:10px">中医证型 TOP</h4>
    <table><thead><tr><th>名称</th><th>就诊人次</th></tr></thead><tbody>${
    (d.syndrome_top_tcm || []).map(r => `<tr><td>${esc(r.name)}</td>
      <td>${esc(r.patient_visits)}</td></tr>`).join('')
    || '<tr><td class="empty" colspan="2">无数据</td></tr>'}</tbody></table>`;
}

async function rpExport() {
  try {
    const r = await api('/api/report/export', Object.assign({ kind: RPT.kind }, rpRange()));
    toast('已导出 ' + r.rows + ' 行：' + r.file_name, 'ok');
    modal('导出完成', `<div>文件：${esc(r.file)}</div>
      <div class="hint">导出为 UTF-8 带 BOM 的 CSV，Excel 可直接打开；已写高风险审计。</div>`);
  } catch (e) { toast(e.message, 'err'); }
}

async function rpPrint() {
  try {
    const d = await api('/api/report/print_data',
      Object.assign({ kind: RPT.kind }, rpRange()), 'GET');
    modal(d.title, `<table><thead><tr>${d.header.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${(d.rows || []).slice(0, 100).map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')
    || '<tr><td class="empty">无数据</td></tr>'}</tbody></table>
      <div class="hint">${esc(d.footer)}</div>
      <div class="row" style="margin-top:8px"><button class="btn sm" onclick="window.print()">打印</button></div>`);
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- 路由与渲染 ---------- */
/* 菜单按权限过滤：没权限的入口不显示，避免"菜单都在、点了报无权限" */
function can(perm) {
  const p = (S.user && S.user.permissions) || [];
  if (!p.length) return true;      // 老会话没有 permissions 字段，放行由后端兜底
  return p.indexOf(perm) >= 0;
}

/* ---------- 中西医双模式 ----------
   中医和西医的字段、处方类型、发药队列本来就不是一套东西，挤在一屏最容易点错。
   模式由就诊类型（visit.visit_type）驱动：TCM 中医 / WEST 西医 / NORMAL 旧数据按西医通用。
   还没挂号时（没有就诊上下文）用上次选的默认模式。 */
const MODE = {
  key: 'clinic_visit_mode',
  get def() { return localStorage.getItem(MODE.key) || 'TCM'; },
  setDef(m) { localStorage.setItem(MODE.key, m); },
  of(v) {
    const t = v && v.visit_type;
    return (t === 'WEST' || t === 'TCM') ? t : (t === 'NORMAL' ? 'WEST' : MODE.def);
  },
  isTcm(v) { return MODE.of(v) === 'TCM'; },
  label(v) { return MODE.of(v) === 'WEST' ? '西医' : '中医'; },
  chip(v) {
    const m = MODE.of(v);
    return m === 'TCM'
      ? '<span class="tag tcm">中医</span>'
      : '<span class="tag west">西医</span>';
  }
};

function setMode(m) {
  MODE.setDef(m);
  toast('已切换到「' + (m === 'WEST' ? '西医' : '中医') + '」模式', 'ok');
  render();
}

/* 病历页/开方页顶部：显示并允许纠正这次就诊的诊疗类型 */
async function changeVisitType(visitId, to) {
  try {
    await api('/api/visit/set_type', { visit_id: visitId, visit_type: to });
    toast('已切换为「' + (to === 'WEST' ? '西医' : '中医') + '」诊疗', 'ok');
    render();
  } catch (e) { toast(e.message, 'err'); }
}

function layout(inner) {
  const u = S.user || {};
  const all = [
    ['#/workbench', '工作台', 'visit.view'],
    ['#/prescribe', '开方', 'prescription.view'],
    ['#/lab', '检验检查', 'lab.view'],
    ['#/pharmacy', '药房库存', 'stock.view'],
    ['#/treatment', '处置执行', 'treatment.view'],
    ['#/billing', '收费财务', 'finance.view'],
    ['#/print', '打印硬件', 'print.view'],
    ['#/ai', 'AI 语音', 'ai.view'],
    ['#/report', '经营统计', 'report.view'],
    ['#/system', '系统管理', 'system.config'],
  ];
  const navs = all.filter(n => can(n[2]));
  const roles = (u.roles && u.roles.length) ? u.roles.join('/') : (u.role_code || '');
  return `
  <div class="topbar">
    <span class="clinic">${esc((S.sys && S.sys.clinic_name) || '中医诊所')}</span>
    <span class="hint">门诊管理系统</span>
    <span class="modesw" title="决定新挂号走中医还是西医流程">
      <button class="btn sm ${MODE.def === 'TCM' ? '' : 'ghost'}"
        onclick="setMode('TCM')">中医</button>
      <button class="btn sm ${MODE.def === 'WEST' ? '' : 'ghost'}"
        onclick="setMode('WEST')">西医</button>
    </span>
    <span class="spacer"></span>
    <span class="who" title="账号被授予的全部角色">${esc(u.display_name || '')} · ${esc(roles)}</span>
    <button class="btn sm ghost" onclick="doLock()">锁屏</button>
    <button class="btn sm ghost" onclick="doLogout()">退出</button>
  </div>
  <div class="layout">
    <div class="sidebar">${navs.map(n => `<a href="${n[0]}" class="${location.hash.startsWith(n[0]) ? 'active' : ''}">${n[1]}</a>`).join('')}</div>
    <div class="main">${inner}</div>
  </div>`;
}

async function render() {
  const app = $('#app');
  await loadStatus();
  if (!S.sys || S.sys.error) { app.innerHTML = '<div class="empty">无法连接服务</div>'; return; }
  if (!S.sys.initialized) { app.innerHTML = viewInit(); return; }
  if (!S.token) { app.innerHTML = viewLogin(); return; }

  const hash = location.hash || '#/workbench';
  try {
    if (hash === '#/system') {
      app.innerHTML = layout(await viewSystem());
      await loadSystemExtras();
    } else if (hash.startsWith('#/prescribe')) {
      // 支持 #/prescribe/<visitId>：从工作台点进来时自动带入就诊，不用手填 ID
      const vid = parseInt((hash.split('/')[2] || '').split('?')[0]) || 0;
      if (vid) RX_STATE.visitId = vid;
      app.innerHTML = layout(await viewPrescribe());
      if (RX_STATE.visitId) await rxLoad();
    } else if (hash.startsWith('#/lab')) {
      const vid = parseInt((hash.split('/')[2] || '').split('?')[0]) || 0;
      if (vid) LB.visitId = vid;
      app.innerHTML = layout(await viewLab());
      if (LB.visitId) { const el = document.getElementById('lb_visit'); if (el) el.value = LB.visitId; await lbLoad(); }
    } else if (hash.startsWith('#/print')) {
      const vid = parseInt((hash.split('/')[2] || '').split('?')[0]) || 0;
      app.innerHTML = layout(await viewPrint());
      if (vid) tryFillPrint(vid);
    } else if (hash.startsWith('#/ai')) {
      const vid = parseInt((hash.split('/')[2] || '').split('?')[0]) || 0;
      if (vid) AI.visitId = vid;
      app.innerHTML = layout(await viewAi());
    } else if (hash === '#/pharmacy') {
      app.innerHTML = layout(await viewPharmacy());
    } else if (hash.startsWith('#/treatment')) {
      const vid = parseInt((hash.split('/')[2] || '').split('?')[0]) || 0;
      if (vid) P3.visitId = vid;
      app.innerHTML = layout(await viewTreatment());
      if (vid) { const el = document.getElementById('tr_visit'); if (el) el.value = vid; await trLoad(); }
    } else if (hash.startsWith('#/billing')) {
      const vid = parseInt((hash.split('/')[2] || '').split('?')[0]) || 0;
      app.innerHTML = layout(await viewBilling());
      await blLoadSettleState();
      if (vid) { const el = document.getElementById('bl_visit'); if (el) el.value = vid; await blAuto(vid); }
    } else if (hash === '#/report') {
      app.innerHTML = layout(viewReport());
      await rpLoad();
    } else if (hash.startsWith('#/record/')) {
      const id = hash.split('/')[2];
      app.innerHTML = layout(await viewRecord(id));
      // 用病历 ID（不是路由里的就诊 ID）加载归档区
      if (LAST_RECORD_ID) await arLoad(LAST_RECORD_ID);
    } else {
      app.innerHTML = layout(await viewWorkbench());
    }
  } catch (e) {
    app.innerHTML = layout(`<div class="card"><h3>加载失败</h3><div class="hint">${esc(e.message)}</div></div>`);
  }
}

window.onhashchange = render;
window.addEventListener('load', render);
