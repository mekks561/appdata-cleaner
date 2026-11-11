const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const LEVEL = {
  safe:      { text: "安全缓存", cls: "safe" },
  suspect:   { text: "疑似缓存", cls: "suspect" },
  data:      { text: "用户数据", cls: "data" },
  protected: { text: "系统/程序", cls: "protected" },
};
const MODE_TEXT = {
  recycle: "移入回收站", move: "移动到文件夹", permanent: "永久删除",
  undo: "撤销还原", "trash-empty": "清空回收站", oneclick: "一键清理",
  dup: "重复文件清理",
};
function lv(x) { return LEVEL[x.level || x.cat] || LEVEL.data; }
function isDeletable(x) { return x.deletable !== false && (x.level || x.cat) !== "protected"; }

const state = {
  version: "", build: "", about: null,
  root: "", diskFree: 0, diskTotal: 0,
  hot: [], big: [], errors: [], truncated: null,
  incremental: null, snapshot: null, diff: null, ext: null, extOpen: null,
  totalSize: 0, fileCount: 0, elapsed: 0, scannedAt: "",
  tab: "hot",
  sort: { key: "s", dir: -1 },
  selected: new Map(),
  expanded: new Map(),
  preview: null,
  sessionFreed: 0,
  scanned: false,
  q: "", filter: "all",
  rows: [], cursor: -1,
  history: [], historyLoaded: false,
  lastManifest: "",
  config: null, configLoaded: false,
  oneclick: null,
  dup: { groups: [], loaded: false, chosen: new Set(), keep: {}, partial: false, busy: false },
  bigView: "file",
};

function fmt(n){
  if (n === null || n === undefined) return "—";
  if (n < 1024) return n + " B";
  const u = ["KB","MB","GB","TB"];
  let i = -1, v = n;
  do { v /= 1024; i++; } while (v >= 1024 && i < u.length - 1);
  return (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)) + " " + u[i];
}
function esc(s){ return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function toast(msg, ms){
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("on"), ms || 2800);
}
async function api(path, body){
  const opt = body ? {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)} : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok){
    const err = new Error(j.error || ("HTTP " + r.status));
    if (j.details) err.details = j.details;
    throw err;
  }
  return j;
}

/* ---------- 初始化 ---------- */
async function init(){
  try {
    const info = await api("/api/info");
    state.about = info;
    state.version = info.version || "";
    state.build = info.build || "";
    $("#verBtn").textContent = "v" + state.version;
    document.title = `AppData 磁盘清理助手 v${state.version}`;
    state.root = info.root;
    $("#root").value = info.root;
    state.diskFree = info.disk.free;
    state.diskTotal = info.disk.total;
    renderNet();
    if (!info.admin) $("#adminBar").style.display = "block";
    loadTrashSize();
    loadConfig().catch(() => {});
    loadRoots().catch(() => {});
    const st = await api("/api/scan/status");
    if (st.running){
      // R4 启动预热正在跑：直接接上进度条轮询
      $("#progress").classList.add("on");
      $("#btnScan").disabled = true; $("#btnRescan").disabled = true;
      $("#pPhase").textContent = "后台预热中…";
      polling = setInterval(poll, 600);
    } else if (st.done){
      await loadResult(true);
    }
  } catch(e){
    toast("无法连接本地服务：" + e.message);
  }
}

function renderNet(){
  if (!state.diskTotal) return;
  const used = state.diskTotal - state.diskFree;
  const pct = (used / state.diskTotal * 100).toFixed(1);
  $("#netinfo").innerHTML =
    `C 盘 已用 <b>${fmt(used)}</b> / ${fmt(state.diskTotal)}（${pct}%）<br>可用 <b>${fmt(state.diskFree)}</b>`;
}

/* ---------- 多根目录（1.9）：登记只是快捷方式，不放宽任何校验 ---------- */
let rootsCache = [];

async function loadRoots(){
  try {
    const r = await api("/api/roots");
    rootsCache = r.roots || [];
    renderRoots(r.userHome || "");
  } catch(e){ /* 老服务端无此接口时静默降级，页面其余功能照常 */ }
}

function renderRoots(home){
  $("#rootList").innerHTML = rootsCache.map(x =>
    `<option value="${esc(x.path)}" label="${esc(x.label)}"></option>`).join("");
  if (home) $("#rootsHome").textContent = home;
  $("#rootsList").innerHTML = rootsCache.map(x => `
    <div class="lrow" data-rootpick="${esc(x.path)}" style="cursor:pointer">
      <span class="s" style="width:110px;flex:none">${esc(x.label)}</span>
      <span class="p" title="${esc(x.path)}">${esc(x.path)}</span>
      ${x.default
        ? '<span class="tag protected">内置</span>'
        : `<button class="btn ghost" data-rootremove="${esc(x.path)}">删除</button>`}
    </div>`).join("") || '<div class="lrow">暂无登记的根目录</div>';
}

function openRoots(){
  $("#rootsMsg").textContent = "";
  renderRoots($("#rootsHome").textContent);
  $("#maskRoots").classList.add("on");
}

async function addRoot(){
  const path = $("#newRootPath").value.trim();
  const label = $("#newRootLabel").value.trim();
  if (!path) return toast("请填写根目录绝对路径");
  const btn = $("#btnRootsAdd"); btn.disabled = true;
  try {
    const r = await api("/api/roots/add", { path, label });
    rootsCache = r.roots || [];
    renderRoots($("#rootsHome").textContent);
    $("#newRootPath").value = ""; $("#newRootLabel").value = "";
    $("#rootsMsg").textContent = (r.warnings && r.warnings.length)
      ? "已添加，但有警告：" + r.warnings.join("；") : "已添加：" + path;
  } catch(e){
    $("#rootsMsg").textContent = "添加失败：" + e.message;
  } finally { btn.disabled = false; }
}

async function removeRoot(path, ev){
  ev && ev.stopPropagation();
  try {
    const r = await api("/api/roots/remove", { path });
    rootsCache = r.roots || [];
    renderRoots($("#rootsHome").textContent);
    $("#rootsMsg").textContent = "已删除：" + path;
  } catch(e){ $("#rootsMsg").textContent = "删除失败：" + e.message; }
}

/* ---------- 扫描 ---------- */
let polling = null;
async function startScan(fresh){
  const root = $("#root").value.trim();
  if (!root) return toast("请填写扫描目录");
  $("#btnScan").disabled = true;
  $("#btnRefresh").disabled = true;
  $("#btnRescan").disabled = true;
  $("#progress").classList.add("on");
  $("#pPhase").textContent = "正在枚举目录…";
  try {
    await api("/api/scan", {
      root,
      maxDepth: +$("#depth").value,
      hotMB: +$("#hotmb").value,
      bigMB: +$("#bigmb").value,
      fresh: !!fresh,
    });
  } catch(e){
    toast(e.message);
    $("#btnScan").disabled = false; $("#btnRefresh").disabled = false;
    $("#btnRescan").disabled = false;
    $("#progress").classList.remove("on");
    return;
  }
  if (polling) clearInterval(polling);
  polling = setInterval(poll, 600);
}

async function poll(){
  let st;
  try { st = await api("/api/scan/status"); } catch(e){ return; }
  $("#pPhase").textContent = st.phase || "扫描中";
  $("#pFiles").textContent = st.files.toLocaleString();
  $("#pBytes").textContent = fmt(st.bytes);
  $("#pHot").textContent = st.hotCount;
  $("#pTime").textContent = st.elapsed.toFixed(1) + "s";
  if (!st.running){
    clearInterval(polling); polling = null;
    $("#progress").classList.remove("on");
    $("#btnScan").disabled = false; $("#btnRefresh").disabled = false;
    if (st.done) await loadResult(); else toast("扫描中断，请查看「访问异常」页签");
  }
}

async function loadResult(silent){
  const r = await api("/api/result");
  state.hot = r.hot || [];
  state.big = r.big || [];
  state.errors = r.errors || [];
  state.truncated = r.truncated || null;
  state.incremental = r.incremental || null;
  state.snapshot = r.snapshot || null;
  state.diff = r.diff || null;
  state.ext = r.ext || null;
  state.extOpen = null;
  state.totalSize = r.totalSize || 0;
  state.fileCount = r.fileCount || 0;
  state.elapsed = r.elapsed || 0;
  state.scannedAt = r.scannedAt || "";
  state.root = r.root;
  state.diskFree = r.disk.free; state.diskTotal = r.disk.total;
  state.expanded.clear();
  state.cursor = -1;
  state.scanned = true;
  renderNet(); renderStats(); renderTable(); renderChart(); renderSel(); renderInc();
  $("#btnRescan").disabled = false;
  if (!silent) toast(`扫描完成：${state.hot.length} 个热点目录，耗时 ${state.elapsed}s`);
}

function renderInc(){
  const bar = $("#incBar");
  const inc = state.incremental;
  if (!inc || !inc.used){
    bar.classList.remove("on");
    bar.innerHTML = "";
    return;
  }
  bar.innerHTML = `<span>本次为<b>增量扫描</b>：复用 ${inc.reusedDirs} 个未变动目录（约 ${fmt(inc.reusedBytes)}），
    其余现场重扫。目录内文件被原地改写时体积可能与实际略有出入，
    需要精确值请点右侧按钮。</span>
    <button class="btn" id="btnFullScan">强制全量重扫</button>`;
  bar.classList.add("on");
  $("#btnFullScan").addEventListener("click", () => startScan(true));
}

/* ---------- 1.4 R5：空间预警横幅（只读提醒，不自动执行任何清理） ---------- */
function renderWarn(){
  const bar = $("#warnBar");
  const gb = Number((state.about || {}).warnGB ?? (state.config || {}).warnGB ?? 10);
  const limit = gb > 0 ? gb * 1024 ** 3 : 0;
  if (!state.scanned || !limit || state.totalSize <= limit){
    bar.classList.remove("on");
    bar.innerHTML = "";
    return;
  }
  bar.innerHTML = `<span>AppData 已占用 <b>${fmt(state.totalSize)}</b>，超过预警阈值 <b>${gb} GB</b>（超出 ${fmt(state.totalSize - limit)}）。
    本提示只是提醒，不会自动删除任何东西；阈值可在设置页修改或填 0 关闭。</span>
    <button class="btn" id="btnWarnOneClick">一键清理安全缓存</button>
    <button class="btn" id="btnWarnDup">去查重复文件</button>`;
  bar.classList.add("on");
  $("#btnWarnOneClick").addEventListener("click", openOneClick);
  $("#btnWarnDup").addEventListener("click", () => switchTab("dup"));
}

function renderStats(){
  $("#sTotal").textContent = fmt(state.totalSize);
  $("#sTotalSub").textContent = state.scanned
    ? `${state.scannedAt} · 耗时 ${state.elapsed}s${(state.incremental||{}).used ? " · 增量" : ""}`
    : "尚未扫描";
  $("#sFiles").textContent = state.fileCount.toLocaleString();
  const safe = state.hot.filter(x => (x.level || x.cat) === "safe");
  const susp = state.hot.filter(x => (x.level || x.cat) === "suspect");
  const safeSum = safe.reduce((a, b) => a + b.s, 0);
  $("#sClean").textContent = fmt(safeSum);
  $("#sCleanSub").textContent = state.scanned
    ? `安全缓存 ${safe.length} 个 · 疑似缓存 ${susp.length} 个`
    : "已知安全缓存合计";
  $("#sFreed").textContent = fmt(state.sessionFreed);
  $("#sFreedSub").textContent = state.sessionFreed ? "已累计于本次会话" : "累计于本次会话";
  $("#cHot").textContent = state.hot.length;
  $("#cBig").textContent = state.big.length;
  $("#cErr").textContent = state.errors.length;
  $("#cHist").textContent = state.history.length;
  $("#cDup").textContent = state.dup.groups.length;
  const dt = (state.diff || {}).totals;
  $("#cDiff").textContent = dt ? (dt.new + dt.removed + dt.grown + dt.shrunk) : 0;
  $("#cExt").textContent = (state.ext || {}).types || 0;
  renderWarn();
  const oc = $("#btnOneClick");
  if (oc){
    oc.disabled = !state.scanned;
    oc.title = state.scanned
      ? `共 ${state.hot.filter(x => (x.level || x.cat) === "safe").length} 个安全缓存目录入围`
      : "扫描后可用";
  }
}

/* ---------- 搜索与筛选（R8） ---------- */
function passFilter(r){
  const f = state.filter;
  if (f === "all") return true;
  if (f === "big") return (r.s || 0) >= 1024 * 1024 * 1024;
  return (r.level || r.cat) === f;
}

function passSearch(r){
  const q = state.q.trim().toLowerCase();
  if (!q) return true;
  const hay = String(r.n || "").toLowerCase() + " " + String(r.p || "").toLowerCase();
  if (q.indexOf("*") < 0) return hay.indexOf(q) >= 0;
  // 通配：按 * 切段，依次在字符串里找，等价于前后缀 + 中间包含
  let pos = 0;
  for (const seg of q.split("*")){
    if (!seg) continue;
    const i = hay.indexOf(seg, pos);
    if (i < 0) return false;
    pos = i + seg.length;
  }
  return true;
}

function visible(r){ return passFilter(r) && passSearch(r); }

/* ---------- 列表渲染 ---------- */
function baseRows(){
  if (state.tab === "hot") return state.hot.map(x => ({...x, dir: true, expandable: true}));
  if (state.tab === "big")  return state.big.map(x => ({...x, dir: false, expandable: false, f: undefined}));
  return [];
}

function sortedRows(rows){
  const { key, dir } = state.sort;
  return rows.slice().sort((a, b) => {
    if (key === "n"){
      const va = String(a.n || "").toLowerCase(), vb = String(b.n || "").toLowerCase();
      return va < vb ? dir : va > vb ? -dir : 0;
    }
    const va = a[key] == null ? -1 : a[key];
    const vb = b[key] == null ? -1 : b[key];
    return (va - vb) * dir;
  });
}

function flatRows(){
  const out = [];
  const walk = (rows, level) => {
    for (const r of rows){
      if (!visible(r)) continue;
      out.push({...r, indent: level});
      const kids = state.expanded.get(r.p);
      if (kids) walk(kids, level + 1);
    }
  };
  walk(sortedRows(baseRows()), 0);
  return out;
}

function renderTrunc(){
  const bar = $("#truncBar");
  let html = "";
  if (state.tab === "err" && state.scanned){
    const t = (state.truncated || {}).errors;
    if (t && t.truncated) html = `共 <b>${t.total}</b> 项访问异常，仅显示前 <b>${t.shown}</b> 项`;
  } else if (state.truncated && state.truncated[state.tab] && state.truncated[state.tab].truncated){
    const t = state.truncated[state.tab];
    const name = state.tab === "hot" ? "目录" : "文件";
    html = `共 <b>${t.total}</b> 个符合条件的${name}，仅按体积显示前 <b>${t.shown}</b> 个 —— 调高阈值或缩小扫描目录可减少遗漏`;
  }
  bar.innerHTML = html;
  bar.classList.toggle("on", !!html);
}

/* ---------- 大文件按扩展名聚合（1.3 R7） ---------- */
function extOf(p){
  const name = String(p || "").split(/[\\/]/).pop() || "";
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i).toLowerCase() : "(无后缀)";
}
function extGroups(){
  const map = new Map();
  for (const f of state.big){
    const ext = extOf(f.p);
    let g = map.get(ext);
    if (!g){ g = { ext, s: 0, count: 0, del: 0, sel: 0 }; map.set(ext, g); }
    g.s += f.s || 0;
    g.count += 1;
    if (isDeletable(f)) g.del += 1;
    if (state.selected.has(f.p)) g.sel += 1;
  }
  return Array.from(map.values()).sort((a, b) => b.s - a.s);
}
function extRows(){
  const groups = extGroups();
  const max = Math.max(...groups.map(g => g.s), 1);
  return groups.map(g => ({
    extRow: true, ext: g.ext, s: g.s, f: g.count, del: g.del, sel: g.sel,
    p: `${g.count} 个文件${g.del < g.count ? `（${g.del} 个可清理）` : ""}`,
    n: g.ext, indent: 0, _max: max,
  }));
}
function toggleExt(ext, on){
  for (const f of state.big){
    if (extOf(f.p) !== ext || !isDeletable(f)) continue;
    if (on) state.selected.set(f.p, { s: f.s || 0, level: f.level || f.cat });
    else state.selected.delete(f.p);
  }
  renderSel(); syncExtChecks();
}
function syncExtChecks(){
  const groups = extGroups();
  document.querySelectorAll(".extchk").forEach(c => {
    const g = groups.find(x => x.ext === c.dataset.ext);
    if (g) c.checked = g.sel > 0 && g.sel >= g.del;
  });
}
async function toggleBigView(){
  state.bigView = state.bigView === "file" ? "ext" : "file";
  $("#btnBigView").textContent = state.bigView === "file" ? "按扩展名聚合" : "按文件列表";
  renderTable();
}

/* ---------- 1.4 R3：与上次扫描的变化对比 ---------- */
function renderDiff(){
  const d = state.diff;
  const sum = $("#diffSum"), notice = $("#diffNotice"), body = $("#diffBody");
  if (!d || !d.hasBaseline){
    sum.textContent = "";
    notice.textContent = "";
    body.innerHTML = `<div class="empty-state"><div class="big">还没有对比基准</div>这是本次会话的第一次扫描；再扫一次，就能看到这段时间哪些目录在涨、哪些被清掉了</div>`;
    return;
  }
  const t = d.totals || {};
  sum.textContent = `基准：${d.baselineAt || "上次扫描"} · 净变化 ${fmt(t.deltaBytes || 0)} · 新增 ${t.new||0} / 移除 ${t.removed||0} / 增长 ${t.grown||0} / 缩小 ${t.shrunk||0}`;
  notice.innerHTML = `对比范围是扫描树里可见的目录（层数由扫描设置决定），不额外读盘。` +
    (d.approx
      ? ` <span style="color:var(--warn)">本次为增量扫描：被复用的子树按「无变化」计，增长/缩小可能低估；点右上「强制全量重扫」取精确值。</span>`
      : ` 本次为全量扫描。`);
  const groups = [
    ["新增目录", d.new, "new"],
    ["已移除", d.removed, "removed"],
    ["增长 Top", d.grown, "grown"],
    ["缩小 Top", d.shrunk, "shrunk"],
  ];
  body.innerHTML = groups.map(([title, items, kind]) => {
    const arr = items || [];
    const total = t[kind] || 0;
    if (!arr.length) return `<div class="diff-group"><h4>${title}<span>${total} 项</span></h4><div class="diff-empty">无</div></div>`;
    const rows = arr.map(x => {
      const delta = x.delta || 0;
      const up = kind === "new" || kind === "grown";
      const dtxt = kind === "new" ? `+${fmt(x.s)}`
        : kind === "removed" ? `−${fmt(x.s)}`
        : delta > 0 ? `+${fmt(delta)}` : `−${fmt(-delta)}`;
      return `<tr><td class="nm" title="${esc(x.p)}">${esc(x.n)}</td>` +
             `<td class="pth" title="${esc(x.p)}">${esc(x.p)}</td>` +
             `<td class="num">${fmt(x.s)}</td>` +
             `<td class="num diff-${up ? "up" : "down"}">${dtxt}</td></tr>`;
    }).join("");
    return `<div class="diff-group"><h4>${title}<span>${total} 项${arr.length < total ? `，显示前 ${arr.length}` : ""}</span></h4>` +
           `<table class="diff-table"><tbody>${rows}</tbody></table></div>`;
  }).join("");
}

/* ---------- 1.4 R4：全树扩展名聚合（只读分析，清理仍走大文件页签确认流） ---------- */
function renderExtPage(){
  const d = state.ext;
  const sum = $("#extSum"), notice = $("#extNotice"), body = $("#extBody");
  if (!d || !d.items || !d.items.length){
    sum.textContent = "";
    notice.textContent = "";
    body.innerHTML = `<div class="empty-state"><div class="big">还没有扩展名统计</div>扫描后按全树文件类型聚合展示；点「强制全量重扫」可刷新</div>`;
    return;
  }
  sum.textContent = `${d.types} 种类型 · ${fmt(d.totalBytes)} / ${(d.totalFiles || 0).toLocaleString()} 个文件` +
    (d.shown < d.types ? ` · 显示体积最大的 ${d.shown} 种` : "");
  notice.innerHTML = `统计覆盖本次扫描读到的全部文件（不只是大文件）；点任意一行展开，看该类型主要藏在哪些目录。` +
    (d.partial
      ? ` <span style="color:var(--warn)">本次统计只覆盖实际扫描到的文件（旧快照还没有扩展名数据），可能明显低估；请点右上「强制全量重扫」刷新。</span>`
      : d.approx
      ? ` <span style="color:var(--warn)">本次为增量扫描：复用的子树沿用上次统计，可能低估；点右上「强制全量重扫」取精确值。</span>`
      : ` 本次为全量扫描。`) +
    ` <b>本页只读</b>，不提供清理按钮 —— 要按类型清理请到「大文件 → 按扩展名聚合」，走同一套预览确认流程。`;
  const rows = d.items.map(x => {
    const open = state.extOpen === x.ext;
    const dirs = (x.dirs || []).map(v => `<tr><td class="pth" title="${esc(v.p)}">${esc(v.p)}</td>` +
      `<td class="num">${fmt(v.s)}</td><td class="num">${(v.f || 0).toLocaleString()}</td></tr>`).join("");
    const more = x.otherDirs ? `<div class="ext-more">另有 ${x.otherDirs} 个目录未列出（合计已计入上方总量）</div>` : "";
    return `<tr class="ext-row${open ? " open" : ""}" data-ext="${esc(x.ext)}">
        <td class="nm">${open ? "▾" : "▸"} ${esc(x.ext)}</td>
        <td class="num">${fmt(x.s)}</td>
        <td><div class="bar"><i class="data" style="width:${Math.max(1, x.pct || 0)}%"></i></div></td>
        <td class="num">${(x.pct || 0).toFixed(2)}%</td>
        <td class="num">${(x.f || 0).toLocaleString()}</td>
      </tr>` + (open ? `<tr class="ext-detail"><td colspan="5">
        <table class="ext-dirs"><tbody>${dirs || `<tr><td colspan="3" class="ext-more">没有目录明细</td></tr>`}</tbody></table>
        ${more}</td></tr>` : "");
  }).join("");
  body.innerHTML = `<table class="ext-table"><thead><tr>
      <th style="width:24%">扩展名</th><th class="num" style="width:14%">占用</th>
      <th style="width:34%">分布</th><th class="num" style="width:12%">占比</th>
      <th class="num" style="width:16%">文件数</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  $$("#extBody .ext-row").forEach(tr => tr.addEventListener("click", () => {
    state.extOpen = state.extOpen === tr.dataset.ext ? null : tr.dataset.ext;
    renderExtPage();
  }));
}

function renderTable(){
  const tb = $("#tbody");
  if (state.tab === "diff"){ renderDiff(); return; }
  if (state.tab === "ext"){ renderExtPage(); return; }
  if (state.tab === "history"){ renderHistory(); return; }
  if (state.tab === "big" && state.bigView === "ext"){ renderExtTable(); return; }
  const rows = flatRows();
  state.rows = rows;
  if (state.cursor >= rows.length) state.cursor = rows.length - 1;
  renderTrunc();
  if (state.tab === "err" && state.errors.length){
    tb.innerHTML = state.errors.map(e => `<tr>
      <td></td>
      <td><span class="tag protected">${e.kind === "denied" ? "无权限" : e.kind === "locked" ? "被占用" : "错误"}</span></td>
      <td colspan="5" style="color:var(--muted);font-size:12px">${esc(e.p)}</td>
      <td><button class="btn ghost" data-open="${esc(e.p)}">位置</button></td></tr>`).join("");
    return;
  }
  if (!rows.length){
    const filtered = state.scanned && (state.q || state.filter !== "all");
    tb.innerHTML = `<tr><td colspan="8"><div class="empty-state"><div class="big">${
      filtered ? "当前搜索/筛选没有匹配项" : state.scanned ? "该阈值下没有符合条件的项目" : "还没有数据"
    }</div>${
      filtered ? "试试清空搜索框（Esc）或把筛选切回「全部」"
      : state.scanned ? "试试把「目录入围阈值」调低到 10 MB 再扫描一次" : "点击「开始扫描」统计 AppData 下各目录占用"
    }</div></td></tr>`;
    return;
  }
  const max = Math.max(...state.hot.map(x => x.s || 0), 1);
  const sel = state.selected;
  tb.innerHTML = rows.map((r, i) => {
    const info = lv(r);
    const del = isDeletable(r);
    const isSel = sel.has(r.p);
    const pct = Math.max(1, (r.s || 0) / max * 100);
    const kids = state.expanded.get(r.p);
    const caret = r.expandable
      ? `<span class="caret" data-toggle="${esc(r.p)}">${kids ? "▾" : "▸"}</span>`
      : `<span class="caret empty">·</span>`;
    const noteTitle = r.note ? ` title="${esc(r.note)}"` : "";
    return `<tr class="${isSel ? "sel" : ""}${i === state.cursor ? " cur" : ""}" data-path="${esc(r.p)}" data-i="${i}">
      <td><input type="checkbox" class="rowchk" data-path="${esc(r.p)}" data-size="${r.s || 0}" data-level="${info.cls}" ${isSel ? "checked" : ""} ${del ? "" : "disabled title='系统/程序数据，禁止删除'"}></td>
      <td><div class="namecell" style="padding-left:${(r.indent || 0) * 14}px">${caret}<span class="nm" title="${esc(r.n)}">${esc(r.n)}</span>${
        r.err === "denied" ? '<span class="tag protected" title="部分子项无权限">!</span>' : ""}</div></td>
      <td><div class="pth" title="${esc(r.p)}">${esc(r.p)}</div></td>
      <td class="num">${fmt(r.s)}</td>
      <td><div class="bar"><i class="${info.cls}" style="width:${pct}%"></i></div></td>
      <td><span class="tag ${info.cls}"${noteTitle}>${info.text}</span></td>
      <td class="num">${r.f !== undefined && r.f !== null ? r.f.toLocaleString() : "—"}</td>
      <td><button class="btn ghost" data-open="${esc(r.p)}">位置</button>${del ? "" : `<button class="btn ghost" data-guide="${esc(r.p)}" title="查看该目录的解锁引导与官方说明">引导</button>`}</td>
    </tr>`;
  }).join("");
}

function renderExtTable(){
  const tb = $("#tbody");
  const rows = extRows();
  state.rows = rows;
  renderTrunc();
  if (!rows.length){
    tb.innerHTML = `<tr><td colspan="8"><div class="empty-state"><div class="big">还没有数据</div>扫描后按扩展名聚合展示大文件</div></td></tr>`;
    return;
  }
  tb.innerHTML = rows.map(r => `<tr class="${r.sel > 0 ? "sel" : ""}">
      <td><input type="checkbox" class="extchk" data-ext="${esc(r.ext)}" title="勾选该类型的全部可清理文件"></td>
      <td><div class="namecell"><span class="nm">${esc(r.ext)}</span></div></td>
      <td><div class="pth">${esc(r.p)}</div></td>
      <td class="num">${fmt(r.s)}</td>
      <td><div class="bar"><i class="data" style="width:${Math.max(1, r.s / r._max * 100)}%"></i></div></td>
      <td><span class="tag data">聚合</span></td>
      <td class="num">${r.f.toLocaleString()}</td>
      <td></td>
    </tr>`).join("");
  syncExtChecks();
}

function renderChart(){
  const top = state.hot.slice(0, 12);
  if (!top.length){ $("#chart").innerHTML = '<div class="empty-state" style="padding:24px 0">扫描后显示</div>'; return; }
  const max = top[0].s || 1;
  const colors = {safe:"#22c55e", suspect:"#f59e0b", data:"#2563eb", protected:"#9ca3af"};
  $("#chart").innerHTML = top.map(x => {
    const w = Math.max(2, (x.s || 0) / max * 100);
    const name = x.n.length > 24 ? x.n.slice(0, 23) + "…" : x.n;
    const cls = (x.level || x.cat) || "data";
    return `<div class="row" data-jump="${esc(x.p)}" title="${esc(x.p)}${x.note ? "\n" + esc(x.note) : ""}">
      <div class="lab"><span>${esc(name)}</span><span>${fmt(x.s)}</span></div>
      <div class="track"><div class="fill" style="width:${w}%;background:${colors[cls] || "#2563eb"}"></div></div>
    </div>`;
  }).join("");
}

/* ---------- 展开（R7） ---------- */
async function toggleExpand(path, ev){
  ev && ev.stopPropagation();
  if (state.expanded.has(path)){ state.expanded.delete(path); renderTable(); return; }
  toast("正在统计子项…");
  try {
    const r = await api("/api/list", { path });
    state.expanded.set(path, r.items.map(i => ({
      n: i.n, p: i.p, s: i.s === null ? null : i.s, f: undefined,
      level: i.level || i.cat || "data", note: i.note || "",
      dir: i.dir, err: null, expandable: false
    })));
    renderTable();
    if (r.timedOut) toast("部分子目录统计超时，已显示其余项（显示为 —）");
    else if (r.truncatedCount) toast(`该目录共 ${r.total} 项，仅显示前 ${r.returned} 项`);
  } catch(e){ toast("展开失败：" + e.message); }
}

/* ---------- 选择 ---------- */
function renderSel(){
  const n = state.selected.size;
  let sum = 0;
  state.selected.forEach(v => sum += v.s || 0);
  $("#selCount").textContent = n;
  $("#selSize").textContent = fmt(sum);
  $("#btnPreview").disabled = n === 0;
  $("#btnPreview2").disabled = n === 0;

  // 已选但被当前筛选挡住的项
  const shown = new Set(state.rows.map(r => r.p));
  let hidden = 0;
  state.selected.forEach((_v, p) => { if (!shown.has(p)) hidden++; });
  $("#selOut").textContent = hidden ? `另有 ${hidden} 项已选不在当前筛选内` : "";

  if (!n){
    $("#selSummary").innerHTML = '未选择任何项目。<br>勾选左侧列表可批量清理。';
    return;
  }
  const items = Array.from(state.selected.entries());
  const susp = items.filter(([, v]) => v.level === "suspect").length;
  $("#selSummary").innerHTML = `
    已选 <b style="color:var(--text)">${n}</b> 项，合计 <b style="color:var(--text)">${fmt(sum)}</b>
    ${susp ? `<br><span style="color:var(--warn)">其中 ${susp} 项为「疑似缓存」，预览时请看清用途说明</span>` : ""}
    <div style="margin-top:8px;max-height:130px;overflow:auto;font-size:11.5px;color:var(--muted)">
      ${items.slice(0, 20).map(([p]) => `<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left">${esc(p)}</div>`).join("")}
      ${items.length > 20 ? `<div>…另有 ${items.length - 20} 项</div>` : ""}
    </div>`;
}

/* ---------- 预览 ---------- */
async function openPreview(){
  const paths = Array.from(state.selected.keys());
  if (!paths.length) return;
  const btn = $("#btnPreview"); btn.disabled = true;
  try {
    const r = await api("/api/preview", { paths });
    state.preview = r;
    $("#pvTotal").textContent = fmt(r.total);
    $("#pvFree").textContent = fmt(r.free);
    $("#pvList").innerHTML = r.rows.map(row => {
      const info = lv(row);
      return `
      <div class="lrow ${row.blocked ? "bad" : ""}">
        <span class="s">${fmt(row.s)}</span>
        <span class="p" title="${esc(row.p)}">${esc(row.p)}</span>
        <span class="tag ${info.cls}">${info.text}</span>
        ${row.note ? `<span class="note" title="${esc(row.note)}">${esc(row.note)}</span>` : ""}
        ${row.blocked ? '<span class="errmsg">禁止</span>' : ""}
      </div>`;
    }).join("") || '<div class="lrow">无有效项目</div>';
    $("#pvWarn").innerHTML = r.blockedCount
      ? `清单中有 <b>${r.blockedCount}</b> 项属于系统/程序数据（已标红），执行时会被自动跳过。<br>请确认其余项目确实可以清理：绿色为已知安全缓存，黄色为疑似缓存，请对照说明再决定。<br><a href="#" data-guide="" style="color:var(--accent)">查看解锁引导 →</a>`
      : `请逐项核对路径与用途说明。清理缓存后，对应程序首次启动会变慢或需要重新登录，属于正常现象。`;
    $("#cf1").checked = false; $("#cf2").checked = false;
    $("#permaText").value = "";
    setMode("recycle");
    updateDoBtn();
    $("#maskPreview").classList.add("on");
  } catch(e){
    toast("预览失败：" + e.message);
  } finally { btn.disabled = state.selected.size === 0; }
}

function setMode(mode){
  $$("#modeRow .radio").forEach(el => {
    const on = el.dataset.mode === mode;
    el.classList.toggle("on", on);
    el.querySelector("input").checked = on;
  });
  $("#destBox").style.display = mode === "move" ? "block" : "none";
  $("#permaBox").style.display = mode === "permanent" ? "block" : "none";
  if (mode === "move" && !$("#dest").value){
    $("#dest").value = (state.config && state.config.dest) || "D:\\AppDataBackup";
  }
  updateDoBtn();
}

function currentMode(){
  const el = $$("#modeRow .radio").find(x => x.classList.contains("on"));
  return el ? el.dataset.mode : "recycle";
}

function updateDoBtn(){
  const mode = currentMode();
  const okBase = $("#cf1").checked && $("#cf2").checked;
  let ok = okBase;
  let hint = "";
  if (!okBase) hint = "请先勾选下方两项确认";
  if (mode === "move" && !$("#dest").value.trim()){ ok = false; hint = "请填写目标文件夹"; }
  if (mode === "permanent"){
    if ($("#permaText").value.trim() !== "永久删除"){ ok = false; hint = "请输入「永久删除」以确认"; }
    else if (okBase) hint = "永久删除：无法从回收站恢复";
  }
  $("#btnDoClean").disabled = !ok;
  $("#pvHint").textContent = hint;
  $("#btnDoClean").className = "btn " + (mode === "permanent" ? "danger" : "primary");
}

/* ---------- 执行 ---------- */
async function doClean(){
  const mode = currentMode();
  const paths = Array.from(state.selected.keys());
  $("#btnDoClean").disabled = true;
  $("#btnDoClean").textContent = "执行中…";
  try {
    const r = await api("/api/exec", {
      token: state.preview.token, paths, mode,
      dest: $("#dest").value.trim(),
      confirmText: $("#permaText").value.trim(),
    });
    state.sessionFreed += r.freed;
    for (const item of r.results){
      if (item.ok){
        state.hot = state.hot.filter(x => x.p !== item.p);
        state.big = state.big.filter(x => x.p !== item.p);
        state.selected.delete(item.p);
        state.expanded.delete(item.p);
      }
    }
    closeMask("maskPreview");
    renderStats(); renderTable(); renderChart(); renderSel();

    state.lastManifest = r.manifestId || "";
    $("#btnOpenBin").style.display = r.mode === "recycle" ? "inline-block" : "none";
    $("#btnUndoMove").style.display = r.undoable ? "inline-block" : "none";
    if (r.mode === "permanent"){
      $("#rsFreed").textContent = "+" + fmt(r.freed);
      $("#rsFreed").style.color = "var(--ok)";
      $("#rsSub").textContent = `磁盘可用：${fmt(r.freeBefore)} → ${fmt(r.freeAfter)}　·　所选项目合计 ${fmt(r.freedBySize)}`;
    } else {
      $("#rsFreed").textContent = fmt(r.freedBySize);
      $("#rsFreed").style.color = r.mode === "recycle" ? "var(--accent)" : "var(--purple)";
      $("#rsSub").textContent = r.mode === "recycle"
        ? `已移入回收站 · 清空回收站后空间才会真正释放　·　当前可用 ${fmt(r.freeAfter)}`
        : `已移动到目标文件夹 · 可在下方一键撤销　·　当前可用 ${fmt(r.freeAfter)}`;
    }
    $("#rsOk").textContent = r.okCount;
    $("#rsFail").textContent = r.failCount;
    $("#rsSpace").textContent = fmt(r.freeAfter);
    $("#rsList").innerHTML = r.results.map(x => `
      <div class="lrow ${x.ok ? "" : "bad"}">
        <span class="${x.ok ? "res-ok" : "res-fail"}">${x.ok ? "成功" : "失败"}</span>
        <span class="p" title="${esc(x.p)}">${esc(x.p)}</span>
        <span style="font-size:11.5px;color:${x.ok ? "var(--muted)" : "var(--danger)"};max-width:44%;text-align:right">${esc(x.msg)}</span>
      </div>`).join("");

    const fails = r.results.filter(x => !x.ok);
    const denied = fails.filter(x => /拒绝访问|权限/.test(x.msg)).length;
    const locked = fails.filter(x => /占用|正在被使用/.test(x.msg)).length;
    const tips = [];
    if (denied) tips.push(`有 <b>${denied}</b> 项因权限不足失败：关掉当前黑窗口，双击 <b>以管理员身份启动.bat</b>，UAC 选「是」后重试。`);
    if (locked) tips.push(`有 <b>${locked}</b> 项被程序占用：退出对应软件（浏览器 / 微信 / 游戏 / Docker）后重试。`);
    if (tips.length){ $("#rsHint").innerHTML = tips.join("<br>"); $("#rsHint").style.display = "block"; }
    else $("#rsHint").style.display = "none";
    $("#maskResult").classList.add("on");

    state.historyLoaded = false;
    loadTrashSize();
  } catch(e){
    toast(e.message);
  } finally {
    $("#btnDoClean").disabled = false;
    $("#btnDoClean").textContent = "确认执行";
  }
}

/* ---------- 撤销（R9） ---------- */
async function undoMove(mid){
  if (!mid) return toast("没有可撤销的记录");
  const btn = $("#btnUndoMove");
  if (btn) { btn.disabled = true; btn.textContent = "还原中…"; }
  try {
    const r = await api("/api/undo", { manifestId: mid });
    toast(`已还原 ${r.restoredCount} 项（失败 ${r.failCount}）`);
    await loadResult(true).catch(() => {});
    if (state.historyLoaded) loadHistory();
    state.historyLoaded = false;
  } catch(e){
    toast("撤销失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "撤销本次移动"; }
  }
}

/* ---------- 历史（R11） ---------- */
async function loadHistory(){
  try {
    const r = await api("/api/history?limit=50");
    state.history = r.items || [];
    state.historyLoaded = true;
    $("#cHist").textContent = state.history.length;
    renderTrend().catch(() => {});
    renderHistory();
  } catch(e){
    toast("历史读取失败：" + e.message);
  }
}

/* ---------- 清理趋势图（1.3 R5，纯 SVG 零依赖） ---------- */
async function renderTrend(){
  const r = await api("/api/trend?days=30");
  const days = r.days || [];
  const box = $("#trendChart");
  const sum = $("#trendSum");
  sum.textContent = r.totalOps
    ? `30 天共 ${r.totalOps} 次操作 · 按体积累计释放 ${fmt(r.totalFreed)}`
    : "还没有清理记录";
  const hasData = days.some(d => d.ops > 0);
  if (!hasData){
    box.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:6px 0 2px">清理一次后，这里会出现每天释放空间的折线</div>';
    return;
  }
  const W = 620, H = 96, P = 6;
  const maxFreed = Math.max(...days.map(d => d.freed), 1);
  const n = days.length;
  const px = i => P + i * (W - P * 2) / (n - 1);
  const py = v => H - P - Math.max(0, v) / maxFreed * (H - P * 2 - 12);
  const pts = days.map((d, i) => `${px(i).toFixed(1)},${py(d.freed).toFixed(1)}`).join(" ");
  const area = `${P},${H - P} ` + pts + ` ${W - P},${H - P}`;
  const ticks = [0, 7, 14, 21, 29].map(i =>
    `<text x="${px(i)}" y="${H - 1}" font-size="10" fill="#888" text-anchor="middle">${days[i] ? days[i].date : ""}</text>`).join("");
  const maxDay = days.reduce((a, b) => b.freed >= (a.freed || 0) ? b : a, days[0] || null);
  const dot = maxDay && maxDay.freed > 0
    ? `<circle cx="${px(days.indexOf(maxDay))}" cy="${py(maxDay.freed)}" r="3.5" fill="#185FA5"/>
       <text x="${px(days.indexOf(maxDay))}" y="${py(maxDay.freed) - 7}" font-size="11" fill="#0C447C" text-anchor="middle">${fmt(maxDay.freed)}</text>`
    : "";
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px;display:block" role="img" aria-label="近30天清理趋势">
    <polygon points="${area}" fill="rgba(24,95,165,0.12)"/>
    <polyline points="${pts}" fill="none" stroke="#185FA5" stroke-width="1.8" stroke-linejoin="round"/>
    <line x1="${P}" y1="${H - P}" x2="${W - P}" y2="${H - P}" stroke="#ccc" stroke-width="0.5"/>
    ${ticks}${dot}</svg>`;
}

function renderHistory(){
  const body = $("#histBody");
  if (!state.history.length){
    body.innerHTML = `<tr><td colspan="7"><div class="empty-state"><div class="big">还没有操作记录</div>清理、移动、撤销都会记在这里</div></td></tr>`;
    return;
  }
  body.innerHTML = state.history.map((h, i) => {
    const mode = MODE_TEXT[h.mode] || h.mode;
    const delta = h.mode === "undo" ? `还原 ${fmt(h.freedBySize || 0)}` : fmt(h.freedBySize || h.freed || 0);
    const detail = (h.items || []).slice(0, 3).map(x => `${x.ok ? "✓" : "✗"} ${x.p}`).join("　");
    const undo = (h.mode === "move" && h.manifestId)
      ? `<button class="btn ghost" data-undo="${esc(h.manifestId)}">撤销</button>` : "";
    return `<tr data-h="${i}">
      <td style="font-size:12px;color:var(--muted);white-space:nowrap">${esc(h.ts || "")}</td>
      <td><span class="tag ${h.mode === "undo" ? "data" : h.mode === "permanent" ? "protected" : "safe"}">${esc(mode)}</span></td>
      <td class="num">${h.count || 0}</td>
      <td class="num"><span class="res-ok">${h.ok || 0}</span> / <span class="${h.fail ? "res-fail" : ""}">${h.fail || 0}</span></td>
      <td class="num">${delta}</td>
      <td><div class="pth" title="${esc((h.items || []).map(x => x.p).join("\n"))}">${esc(detail || "—")}${(h.items || []).length > 3 ? " …" : ""}</div></td>
      <td>${undo}</td>
    </tr>`;
  }).join("");
}

/* ---------- 导出 CSV（R10） ---------- */
function csvCell(v){
  const s = String(v == null ? "" : v);
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function exportCSV(){
  const rows = state.rows || [];
  if (!rows.length) return toast("当前没有可导出的行");
  const head = ["路径", "名称", "大小字节", "大小可读", "文件数", "级别", "说明", "修改时间"];
  const lines = [head.join(",")];
  for (const r of rows){
    lines.push([
      r.p, r.n, r.s == null ? "" : r.s, fmt(r.s),
      r.f == null ? "" : r.f, lv(r).text, r.note || "", r.mtime || "",
    ].map(csvCell).join(","));
  }
  // UTF-8 BOM：Excel 双击直接打开不乱码
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], {type: "text/csv;charset=utf-8"});
  const d = new Date();
  const pad = n => String(n).padStart(2, "0");
  const name = `appdata-scan-${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.csv`;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  toast(`已导出 ${rows.length} 行到 ${name}`);
}

/* ---------- 回收站 ---------- */
async function loadTrashSize(){
  try {
    const r = await api("/api/trash/size");
    const txt = r.ok ? (r.partial ? `约 ${fmt(r.size)}（估算未完成）` : `约 ${fmt(r.size)}`) : "无法读取";
    $("#trashSize").textContent = txt;
    $("#trashSize2").textContent = txt;
  } catch(e){
    $("#trashSize").textContent = "无法读取";
  }
}

function openTrash(){
  $("#trashText").value = "";
  $("#btnDoTrash").disabled = true;
  loadTrashSize();
  $("#maskTrash").classList.add("on");
}

async function doEmptyTrash(){
  const btn = $("#btnDoTrash");
  btn.disabled = true; btn.textContent = "清空中…";
  try {
    const r = await api("/api/trash/empty", { confirmText: $("#trashText").value.trim() });
    closeMask("maskTrash");
    toast(`${r.msg}　释放 ${fmt(r.freed)}`);
    const i = await api("/api/info");
    state.diskFree = i.disk.free; state.diskTotal = i.disk.total;
    renderNet(); loadTrashSize();
    state.historyLoaded = false;
  } catch(e){
    toast(e.message);
  } finally {
    btn.disabled = false; btn.textContent = "确认清空";
    updateTrashBtn();
  }
}

function updateTrashBtn(){
  $("#btnDoTrash").disabled = $("#trashText").value.trim() !== "清空回收站";
}

/* ---------- 设置中心（1.3 R3） ---------- */
async function loadConfig(){
  const r = await api("/api/config");
  state.config = r.config;
  state.configLoaded = true;
  const rules = r.config.rules || [];
  const lines = rules.map(x => `${x.segments.join("/")} | ${x.level} | ${x.note}`);
  $("#cfgRules").value = lines.join("\n") + (lines.length ? "\n" : "");
  $("#cfgDest").value = r.config.dest || "";
  $("#cfgPort").value = r.config.port || "";
  $("#cfgDupMin").value = (r.config.dup || {}).minSizeMB;
  $("#cfgDupBudget").value = (r.config.dup || {}).hashBudgetSec;
  $("#cfgWarnGB").value = r.config.warnGB ?? 10;
  const ac = r.config.autoClean || {};
  $("#cfgAutoOn").checked = !!ac.enabled;
  $("#cfgAutoInterval").value = ac.interval || "daily";
  if (!$("#cfgAutoHour").options.length){
    for (let h = 0; h < 24; h++)
      $("#cfgAutoHour").add(new Option(`${String(h).padStart(2, "0")}:00`, h));
  }
  $("#cfgAutoHour").value = String(ac.hour ?? 12);
  loadAutoStatus();
  $("#cfgMsg").textContent = `${rules.length} 条自定义规则生效中 · 配置文件：${r.path}`;
}

async function loadAutoStatus(){
  const el = $("#autoStatus");
  try {
    const s = await api("/api/auto/status");
    const fmtT = ts => ts ? new Date(ts * 1000).toLocaleString() : "—";
    const reasonMap = {
      disabled: "未启用", "not-time": "未到今日计划时刻", already: "本周期已执行",
      stale: "扫描结果超 7 天（跳过）", "no-result": "还没有扫描结果",
      "bad-config": "配置非法", due: "已到期待执行",
    };
    const lastTxt = s.lastRun
      ? `${fmtT(s.lastRun.at)}（${s.lastRun.count} 项 / ${fmt(s.lastRun.freed)}` +
        (s.lastRun.fail ? `，失败 ${s.lastRun.fail} 项` : "") +
        (s.lastRun.error ? `，<span style="color:var(--warn)">${esc(s.lastRun.error)}</span>` : "") + `）`
      : "从未执行";
    el.innerHTML = `状态：${s.enabled ? `<b style="color:var(--ok)">已启用</b>` : "未启用"}` +
      ` · 下次检查通过后计划于 ${fmtT(s.nextRun)}` +
      ` · 上次自动清理：${lastTxt}` +
      ` · 扫描数据${s.fresh ? "新鲜（7 天内）" : `<span style="color:var(--warn)">过期（超 7 天将跳过）</span>`}` +
      (s.lastError ? ` · <span style="color:var(--warn)">调度异常：${esc(s.lastError.message)}（${fmtT(s.lastError.at)}）</span>` : "") +
      (s.enabled ? ` · 当前判定：${reasonMap[s.reason] || s.reason}` : "");
  } catch(e){
    el.textContent = "";
  }
}

async function previewAutoScope(){
  const out = $("#autoPreviewOut");
  out.textContent = "读取中…";
  try {
    const r = await api("/api/oneclick/preview", {});
    out.innerHTML = r.count
      ? `将清理 <b>${r.count}</b> 个目录 / 共 <b>${fmt(r.total)}</b>（与一键清理清单一致，只含 safe 级）`
      : esc(r.msg || "当前没有可清理项");
  } catch(e){
    out.textContent = e.message;
  }
}

function parseRuleLines(text){
  const rules = [];
  const errors = [];
  text.split("\n").forEach((line, i) => {
    const t = line.trim();
    if (!t) return;
    const segs = t.split("|").map(s => s.trim());
    if (segs.length !== 3){
      errors.push(`第 ${i + 1} 行：格式应为 段/段 | 级别 | 说明（实际 ${segs.length} 段）`);
      return;
    }
    const [path, level, note] = segs;
    const parts = path.split(/[\\/]+/).map(s => s.trim()).filter(Boolean);
    if (!parts.length){ errors.push(`第 ${i + 1} 行：路径段为空`); return; }
    if (!["safe", "suspect", "protected"].includes(level)){
      errors.push(`第 ${i + 1} 行：级别必须是 safe / suspect / protected`);
      return;
    }
    if (!note){ errors.push(`第 ${i + 1} 行：说明必填`); return; }
    rules.push({ segments: parts, level, note });
  });
  return { rules, errors };
}

async function saveConfig(){
  const { rules, errors } = parseRuleLines($("#cfgRules").value);
  const msg = $("#cfgMsg");
  if (errors.length){
    msg.innerHTML = `<span style="color:var(--danger)">${errors.length} 行有误（未保存）：</span><br>` +
      errors.map(esc).join("<br>");
    return;
  }
  const port = parseInt($("#cfgPort").value, 10);
  const dupMin = parseInt($("#cfgDupMin").value, 10);
  const dupBudget = parseInt($("#cfgDupBudget").value, 10);
  const warnGB = parseInt($("#cfgWarnGB").value, 10);
  const btn = $("#btnCfgSave");
  btn.disabled = true; btn.textContent = "保存中…";
  try {
    const r = await api("/api/config", {
      port: isNaN(port) ? $("#cfgPort").value : port,
      dest: $("#cfgDest").value.trim(),
      dup: {
        minSizeMB: isNaN(dupMin) ? 10 : dupMin,
        hashBudgetSec: isNaN(dupBudget) ? 30 : dupBudget,
      },
      autoClean: {
        enabled: $("#cfgAutoOn").checked,
        interval: $("#cfgAutoInterval").value,
        hour: parseInt($("#cfgAutoHour").value, 10),
      },
      warnGB: isNaN(warnGB) ? 10 : warnGB,
      rules,
    });
    state.config = r.config;
    if (state.about) state.about.warnGB = r.config.warnGB;
    renderWarn();
    msg.innerHTML = `<span style="color:var(--ok)">已保存 · ${r.customRules} 条自定义规则已生效</span>` +
      (r.warnings && r.warnings.length ? `<br><span style="color:var(--warn)">部分条目被丢弃：</span><br>` + r.warnings.map(esc).join("<br>") : "") +
      (r.restartHint ? `<br><span style="color:var(--warn)">${esc(r.restartHint)}</span>` : "");
    loadAutoStatus();
    toast("设置已保存");
  } catch(e){
    msg.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>` +
      (e.details && e.details.length ? `<br>${e.details.map(esc).join("<br>")}` : "");
  } finally {
    btn.disabled = false; btn.textContent = "保存设置";
  }
}

/* ---------- 一键清理安全缓存（1.3 R2） ---------- */
async function openOneClick(){
  if (!state.scanned) return toast("请先扫描");
  try {
    const r = await api("/api/oneclick/preview", {});
    if (!r.count){
      toast(r.msg || "当前扫描结果里没有「安全缓存」级目录");
      return;
    }
    state.oneclick = r;
    $("#ocCount").textContent = r.count;
    $("#ocTotal").textContent = fmt(r.total);
    $("#ocPartial").style.display = r.partial ? "inline" : "none";
    $("#ocList").innerHTML = r.items.map(x => `
      <div class="lrow">
        <span class="s">${fmt(x.s)}</span>
        <span class="p" title="${esc(x.p)}">${esc(x.p)}</span>
        ${x.note ? `<span class="note" title="${esc(x.note)}">${esc(x.note)}</span>` : ""}
      </div>`).join("");
    $("#ocCf").checked = false;
    $("#ocText").value = "";
    $("#btnDoOneClick").disabled = true;
    $("#maskOneClick").classList.add("on");
  } catch(e){
    toast(e.message);
  }
}

function updateOneClickBtn(){
  $("#btnDoOneClick").disabled =
    !($("#ocCf").checked && $("#ocText").value.trim() === "一键清理");
}

async function doOneClick(){
  const btn = $("#btnDoOneClick");
  btn.disabled = true; btn.textContent = "执行中…";
  try {
    const r = await api("/api/oneclick", { confirmText: $("#ocText").value.trim() });
    closeMask("maskOneClick");
    state.sessionFreed += r.freed;
    for (const item of r.results){
      if (item.ok){
        state.hot = state.hot.filter(x => x.p !== item.p);
        state.selected.delete(item.p);
        state.expanded.delete(item.p);
      }
    }
    renderStats(); renderTable(); renderChart(); renderSel();

    $("#btnOpenBin").style.display = "inline-block";
    $("#btnUndoMove").style.display = "none";
    $("#rsFreed").textContent = fmt(r.freedBySize);
    $("#rsFreed").style.color = "var(--accent)";
    $("#rsSub").textContent = `一键清理完成 · 已移入回收站，可随时还原　·　当前可用 ${fmt(r.freeAfter)}`;
    $("#rsOk").textContent = r.okCount;
    $("#rsFail").textContent = r.failCount;
    $("#rsSpace").textContent = fmt(r.freeAfter);
    $("#rsList").innerHTML = r.results.map(x => `
      <div class="lrow ${x.ok ? "" : "bad"}">
        <span class="${x.ok ? "res-ok" : "res-fail"}">${x.ok ? "成功" : "失败"}</span>
        <span class="p" title="${esc(x.p)}">${esc(x.p)}</span>
        <span style="font-size:11.5px;color:${x.ok ? "var(--muted)" : "var(--danger)"};max-width:44%;text-align:right">${esc(x.msg)}</span>
      </div>`).join("");
    $("#rsHint").style.display = "none";
    $("#maskResult").classList.add("on");
    state.historyLoaded = false;
    loadTrashSize();
  } catch(e){
    toast(e.message);
  } finally {
    btn.disabled = false; btn.textContent = "确认执行";
    updateOneClickBtn();
  }
}

/* ---------- 重复文件（R1） ---------- */
function fmtDay(sec){
  const d = new Date(sec * 1000);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
}
function dupKeepOf(g){ return state.dup.keep[g.hash] || g.keepSuggestion; }
function dupTargets(){
  const out = [];
  state.dup.groups.forEach(g => {
    if (!state.dup.chosen.has(g.hash)) return;
    const keep = dupKeepOf(g);
    (g.copies || []).forEach(cp => {
      if (cp.p !== keep) out.push({ p: cp.p, s: g.size });
    });
  });
  return out;
}
function updateDupBar(){
  const tg = dupTargets();
  const bytes = tg.reduce((a, x) => a + x.s, 0);
  const n = state.dup.chosen.size;
  $("#dupSelInfo").textContent = n
    ? `已选 ${n} 组 · 将删除 ${tg.length} 个文件 · 释放 ${fmt(bytes)}`
    : "未选择任何重复组";
  $("#btnDupClean").disabled = !tg.length || state.dup.busy;
}
function renderDup(){
  const list = $("#dupList");
  const groups = state.dup.groups || [];
  if (!groups.length){
    list.innerHTML = `<div class="empty-state"><div class="big">没有发现重复文件</div>${
      state.dup.loaded ? "当前条件下没有内容完全一致的文件" : "点上方「扫描重复文件」开始"}</div>`;
    $("#dupSummary").textContent = "";
    updateDupBar();
    return;
  }
  list.innerHTML = groups.map(g => {
    const on = state.dup.chosen.has(g.hash);
    const keep = dupKeepOf(g);
    const rows = (g.copies || []).map(cp => `
      <div style="display:flex;gap:8px;align-items:center;padding:3px 0;font-size:12.5px">
        <label class="chk" style="margin:0;white-space:nowrap;min-width:74px">
          <input type="radio" name="k-${esc(g.hash)}" class="dup-k" data-h="${esc(g.hash)}" data-p="${esc(cp.p)}" ${cp.p === keep ? "checked" : ""}>
          <span style="color:${cp.p === keep ? "var(--ok)" : "var(--muted)"}">${cp.p === keep ? "保留" : "删除"}</span>
        </label>
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(cp.p)}">${esc(cp.p)}</span>
        <span style="color:var(--muted);white-space:nowrap">${fmtDay(cp.m)}</span>
      </div>`).join("");
    return `<div class="panel" style="padding:10px 12px;margin-bottom:10px">
      <label class="chk" style="margin:0">
        <input type="checkbox" class="dup-g" data-h="${esc(g.hash)}" ${on ? "checked" : ""}>
        <span><b>${fmt(g.size)}</b> × ${g.count} 份 · 可释放 <b style="color:var(--ok)">${fmt(g.reclaimable)}</b></span>
      </label>
      <div style="margin-top:6px;padding-left:24px">${rows}</div>
    </div>`;
  }).join("");
  const total = groups.reduce((a, g) => a + g.reclaimable, 0);
  $("#dupSummary").innerHTML = `共 <b>${groups.length}</b> 组 · 可释放 <b>${fmt(total)}</b>` +
    (state.dup.partial ? ` <span style="color:var(--warn)">（已达时间预算，结果为部分结果）</span>` : "");
  updateDupBar();
}
async function loadDup(){
  try {
    const r = await api("/api/dup/result");
    state.dup.groups = r.groups || [];
    state.dup.partial = !!r.partial;
    state.dup.loaded = true;
    state.dup.keep = {};
    state.dup.chosen = new Set();
    (r.groups || []).forEach(g => {
      state.dup.keep[g.hash] = g.keepSuggestion;
      state.dup.chosen.add(g.hash);
    });
    const sk = r.skipped || {};
    $("#dupStatus").textContent =
      `扫描 ${r.scannedFiles || 0} 个文件 · 用时 ${((r.costMs || 0) / 1000).toFixed(1)}s` +
      ` · 跳过硬链接 ${sk.hardlink || 0} / 云盘占位 ${sk.cloud || 0}`;
    renderDup();
    renderStats();
  } catch(e){
    state.dup.groups = [];
    state.dup.loaded = false;
    renderDup();
  }
}
async function dupScan(){
  if (state.dup.busy) return;
  const min = parseFloat($("#dupMin").value) || 10;
  state.dup.busy = true;
  $("#btnDupScan").disabled = true;
  $("#dupStatus").textContent = "正在枚举候选文件…";
  try {
    await api("/api/dup/scan", { minSizeMB: min, fresh: true });
    const t0 = Date.now();
    while (Date.now() - t0 < 180000){
      const st = await api("/api/dup/status");
      if (st.phase) $("#dupStatus").textContent = st.phase + "…";
      if (!st.running){
        if (st.error) throw new Error(st.error);
        break;
      }
      await new Promise(r => setTimeout(r, 500));
    }
    await loadDup();
    state.historyLoaded = false;
  } catch(e){
    $("#dupStatus").textContent = "扫描失败：" + e.message;
    toast("重复文件扫描失败：" + e.message, 5000);
  } finally {
    state.dup.busy = false;
    $("#btnDupScan").disabled = false;
    updateDupBar();
  }
}
function openDupModal(){
  const tg = dupTargets();
  if (!tg.length) return toast("没有可清理的项");
  state.dup.pending = tg;
  const bytes = tg.reduce((a, x) => a + x.s, 0);
  $("#dupDelCount").textContent = tg.length;
  $("#dupDelSize").textContent = fmt(bytes);
  $("#dupDelList").innerHTML = tg.map(x => `
    <div class="lrow"><span class="s">${fmt(x.s)}</span>
      <span class="p" title="${esc(x.p)}">${esc(x.p)}</span></div>`).join("");
  $("#dupCf").checked = false;
  $("#btnDoDup").disabled = true;
  $("#maskDup").classList.add("on");
}
async function doDupClean(){
  if (!state.dup.pending || !state.dup.pending.length) return;
  const keep = state.dup.groups
    .filter(g => state.dup.chosen.has(g.hash))
    .map(g => dupKeepOf(g));
  state.dup.busy = true;
  $("#btnDoDup").disabled = true;
  $("#btnDoDup").textContent = "清理中…";
  try {
    const r = await api("/api/dup/clean", {
      groupHashes: Array.from(state.dup.chosen),
      keepPolicy: "manual",
      keepPaths: keep,
    });
    $("#maskDup").classList.remove("on");
    state.sessionFreed += r.freed || 0;
    $("#sFreed").textContent = fmt(state.sessionFreed);
    toast(`已清理 ${r.okCount} 项${r.failCount ? "，失败 " + r.failCount + " 项" : ""}，释放 ${fmt(r.freed)}`, 5000);
    const bad = (r.results || []).filter(x => !x.ok);
    if (bad.length) console.warn("部分项未清理成功", bad);
    state.dup.busy = false;
    state.dup.loaded = false;
    await dupScan();
  } catch(e){
    toast("清理失败：" + e.message, 5000);
  } finally {
    state.dup.busy = false;
    $("#btnDoDup").disabled = false;
    $("#btnDoDup").textContent = "确认清理";
    updateDupBar();
  }
}

/* ---------- 关于 ---------- */
function openAbout(){
  const a = state.about || {};
  const rows = [
    ["版本", `v${a.version || "?"}　(build ${a.build || "?"})`],
    ["Python", a.python || "—"],
    ["运行权限", a.admin ? "管理员" : "普通用户"],
    ["扫描根目录", a.root || "—"],
    ["日志目录", a.logDir || "—"],
    ["数据目录", a.dataDir || "—"],
    ["服务端口", String(a.port || "")],
  ];
  $("#aboutBody").innerHTML = rows.map(([k, v]) =>
    `<div class="row"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("") +
    `<div class="row"><div class="k">判定规则</div><div class="v">安全缓存 / 疑似缓存 / 用户数据 / 系统程序 四级，保护项禁止删除</div></div>` +
    `<div class="row"><div class="k">增量扫描</div><div class="v">仅复用「目录 mtime 未变且近 10 分钟无写入」的最深一层子树，其余现场重扫；可随时强制全量</div></div>`;
  $("#maskAbout").classList.add("on");
}

/* ---------- 事件 ---------- */
function closeMask(id){ $("#" + id).classList.remove("on"); }

/* ---------- 解锁引导（v1.8）---------- */
async function openGuide(path){
  try {
    const g = await api("/api/unlock-guide", path ? { path } : {});
    const note = $("#guideNote");
    if (g.protected){
      note.style.display = "";
      note.innerHTML = `<b>命中规则：</b>${esc(g.rule || "")}${g.vendor ? "（" + esc(g.vendor) + "）" : ""}<br>${esc(g.note || "")}`;
    } else {
      note.style.display = "none";
    }
    $("#guideSteps").innerHTML = (g.steps || []).map(s => `<li>${esc(s)}</li>`).join("");
    $("#guideLinks").innerHTML = (g.links || []).map(l =>
      `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer" class="btn ghost" style="text-align:left">${esc(l.title)}</a>`
    ).join("");
    $("#guideDisclaimer").textContent = g.disclaimer || "";
    $("#maskGuide").classList.add("on");
  } catch(e){ toast("获取引导失败：" + e.message); }
}

document.addEventListener("click", e => {
  const t = e.target.closest("[data-close]");
  if (t) closeMask(t.dataset.close);

  const rm = e.target.closest("[data-rootremove]");
  if (rm){ removeRoot(rm.dataset.rootremove, e); return; }

  const pick = e.target.closest("[data-rootpick]");
  if (pick){ $("#root").value = pick.dataset.rootpick; closeMask("maskRoots"); return; }

  const caret = e.target.closest("[data-toggle]");
  if (caret) { toggleExpand(caret.dataset.toggle, e); return; }

  const open = e.target.closest("[data-open]");
  if (open){ e.stopPropagation(); api("/api/open", { path: open.dataset.open }).catch(err => toast(err.message)); return; }

  const guideBtn = e.target.closest("[data-guide]");
  if (guideBtn){
    e.preventDefault(); e.stopPropagation();
    openGuide(guideBtn.dataset.guide || null);
    return;
  }

  const undo = e.target.closest("[data-undo]");
  if (undo){ e.stopPropagation(); undoMove(undo.dataset.undo); return; }

  const jump = e.target.closest("[data-jump]");
  if (jump){
    const p = jump.dataset.jump;
    switchTab("hot");
    const row = document.querySelector(`tr[data-path="${CSS.escape(p)}"]`);
    if (row){ row.scrollIntoView({block:"center", behavior:"smooth"}); row.style.outline = "2px solid #2563eb"; setTimeout(() => row.style.outline = "", 1200); }
    return;
  }

  const radio = e.target.closest("#modeRow .radio");
  if (radio){ setMode(radio.dataset.mode); return; }

  const chip = e.target.closest("#chips .chip");
  if (chip){
    $$("#chips .chip").forEach(c => c.classList.remove("on"));
    chip.classList.add("on");
    state.filter = chip.dataset.f;
    state.cursor = -1;
    renderTable(); renderSel();
    return;
  }
});

document.addEventListener("change", e => {
  if (e.target.classList.contains("extchk")){
    toggleExt(e.target.dataset.ext, e.target.checked);
    return;
  }
  if (e.target.classList.contains("rowchk")){
    const p = e.target.dataset.path;
    if (e.target.checked) state.selected.set(p, { s: +e.target.dataset.size, level: e.target.dataset.level });
    else state.selected.delete(p);
    e.target.closest("tr").classList.toggle("sel", e.target.checked);
    renderSel();
  }
  if (["cf1","cf2"].includes(e.target.id)) updateDoBtn();
  if (["ocCf"].includes(e.target.id) || e.target.id === "ocText") updateOneClickBtn();
});

document.addEventListener("input", e => {
  if (["permaText","dest"].includes(e.target.id)) updateDoBtn();
  if (e.target.id === "trashText") updateTrashBtn();
  if (e.target.id === "ocText") updateOneClickBtn();
  if (e.target.id === "q"){
    state.q = e.target.value;
    state.cursor = -1;
    renderTable(); renderSel();
  }
});

$("#chkAll").addEventListener("change", e => {
  const on = e.target.checked;
  document.querySelectorAll(".rowchk").forEach(c => {
    if (c.disabled) return;
    c.checked = on;
    const p = c.dataset.path;
    if (on) state.selected.set(p, { s: +c.dataset.size, level: c.dataset.level });
    else state.selected.delete(p);
    c.closest("tr").classList.toggle("sel", on);
  });
  renderSel();
});

function switchTab(name){
  state.tab = name;
  $$(".tab").forEach(x => x.classList.toggle("active", x.dataset.tab === name));
  const isHist = name === "history";
  const isSet = name === "settings";
  const isDup = name === "dup";
  const isDiff = name === "diff";
  const isExt = name === "ext";
  $("#listWrap").style.display = (isHist || isSet || isDup || isDiff || isExt) ? "none" : "block";
  $("#histWrap").style.display = isHist ? "block" : "none";
  $("#setWrap").style.display = isSet ? "block" : "none";
  $("#dupWrap").style.display = isDup ? "block" : "none";
  $("#diffWrap").style.display = isDiff ? "block" : "none";
  $("#extWrap").style.display = isExt ? "block" : "none";
  $("#filterBar").style.display = (isHist || isSet || isDup || isDiff || isExt) ? "none" : "flex";
  $("#btnBigView").style.display = name === "big" ? "inline-block" : "none";
  state.expanded.clear();
  state.cursor = -1;
  if (isHist){
    $("#truncBar").classList.remove("on");
    $("#truncBar").innerHTML = "";
    if (!state.historyLoaded) loadHistory();
    else renderHistory();
  } else if (isSet){
    $("#truncBar").classList.remove("on");
    $("#truncBar").innerHTML = "";
    loadConfig().catch(e => toast(e.message));
  } else if (isDup){
    $("#truncBar").classList.remove("on");
    $("#truncBar").innerHTML = "";
    if (!state.dup.loaded) loadDup().catch(() => renderDup());
    else renderDup();
  } else if (isDiff){
    $("#truncBar").classList.remove("on");
    $("#truncBar").innerHTML = "";
    renderDiff();
  } else if (isExt){
    $("#truncBar").classList.remove("on");
    $("#truncBar").innerHTML = "";
    renderExtPage();
  } else {
    renderTable();
  }
}

$$(".tab").forEach(tab => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));

$$("thead th.sortable").forEach(th => th.addEventListener("click", () => {
  const k = th.dataset.sort;
  if (state.sort.key === k) state.sort.dir *= -1;
  else { state.sort.key = k; state.sort.dir = k === "n" ? 1 : -1; }
  $$("thead th.sortable").forEach(x => x.classList.toggle("on", x.dataset.sort === k));
  $$("thead th.sortable .arrow").forEach(a => a.textContent = "↕");
  th.querySelector(".arrow").textContent = state.sort.dir === -1 ? "↓" : "↑";
  renderTable();
}));

$("#btnScan").addEventListener("click", () => startScan(false));
$("#btnRoots").addEventListener("click", openRoots);
$("#btnRootsAdd").addEventListener("click", addRoot);
$("#btnRescan").addEventListener("click", () => startScan(false));
$("#btnDiffRescan").addEventListener("click", () => startScan(true));
$("#btnExtRescan").addEventListener("click", () => startScan(true));
$("#btnRescan2").addEventListener("click", () => { closeMask("maskResult"); startScan(false); });
$("#btnRefresh").addEventListener("click", async () => {
  try {
    const i = await api("/api/info");
    state.diskFree = i.disk.free; state.diskTotal = i.disk.total; renderNet(); toast("磁盘信息已刷新");
  } catch(e){ toast(e.message); }
});
$("#verBtn").addEventListener("click", openAbout);
$("#btnPreview").addEventListener("click", openPreview);
$("#btnPreview2").addEventListener("click", openPreview);
$("#btnExport").addEventListener("click", exportCSV);
$("#btnBigView").addEventListener("click", toggleBigView);
$("#btnClearSel").addEventListener("click", () => {
  state.selected.clear();
  document.querySelectorAll(".rowchk").forEach(c => { c.checked = false; c.closest("tr").classList.remove("sel"); });
  $("#chkAll").checked = false;
  renderSel();
});
$("#btnDoClean").addEventListener("click", doClean);
$("#btnUndoMove").addEventListener("click", () => undoMove(state.lastManifest));
$("#btnOpenBin").addEventListener("click", () => {
  api("/api/openrecyclebin", {}).then(() => toast("已打开回收站，可在此还原或彻底清空")).catch(e => toast(e.message));
});
$("#btnOpenBin2").addEventListener("click", () => {
  api("/api/openrecyclebin", {}).then(() => toast("已打开回收站")).catch(e => toast(e.message));
});
$("#btnTrash").addEventListener("click", openTrash);
$("#btnDoTrash").addEventListener("click", doEmptyTrash);
$("#btnOneClick").addEventListener("click", openOneClick);
$("#btnDoOneClick").addEventListener("click", doOneClick);
$("#btnCfgSave").addEventListener("click", saveConfig);
$("#btnCfgReload").addEventListener("click", () => loadConfig().catch(e => toast(e.message)));

/* ---------- 规则包导入（1.7）：导出是 <a download> 直链，无需 JS ---------- */
$("#btnRulesImport").addEventListener("click", () => $("#rulesFile").click());
$("#rulesFile").addEventListener("change", async e => {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";                       // 允许重复选择同一文件
  if (!file) return;
  const msg = $("#cfgMsg");
  if (file.size > 64 * 1024){
    msg.innerHTML = `<span style="color:var(--danger)">规则包超过 64 KB，不像本工具导出的文件</span>`;
    return;
  }
  let pkg;
  try { pkg = JSON.parse(await file.text()); }
  catch(_){
    msg.innerHTML = `<span style="color:var(--danger)">不是有效的 JSON 文件</span>`;
    return;
  }
  if (!window.confirm(`导入将「替换」当前全部自定义规则（文件内 ${Array.isArray(pkg.rules) ? pkg.rules.length : "?"} 条）。继续？`))
    return;
  const btn = $("#btnRulesImport");
  btn.disabled = true;
  try {
    const r = await api("/api/rules/import", pkg);
    await loadConfig();                      // 重新拉取配置刷新 textarea 与计数
    const warn = (r.warnings && r.warnings.length)
      ? `<br><span style="color:var(--warn)">丢弃 ${r.dropped} 条：</span>` + r.warnings.map(esc).join("<br>")
      : "";
    msg.innerHTML = `<span style="color:var(--ok)">已导入 ${r.applied} 条规则</span>` + warn;
    toast("规则包已导入");
  } catch(err){
    msg.innerHTML = `<span style="color:var(--danger)">${esc(err.message)}</span>` +
      (err.details && err.details.length ? `<br>${err.details.map(esc).join("<br>")}` : "");
  } finally {
    btn.disabled = false;
  }
});
$("#btnAutoPreview").addEventListener("click", previewAutoScope);

/* ---------- 以管理员身份重启 ---------- */
async function restartAdmin(){
  const btn = $("#btnRestartAdmin");
  btn.disabled = true; btn.textContent = "等待 UAC 确认…";
  try {
    const r = await api("/api/restart-admin", {});
    if (!r.ok){ toast(r.error || "无法重启"); btn.disabled = false; btn.textContent = "以管理员身份重启"; return; }
    toast(r.message || "请在 UAC 弹窗点「是」", 6000);
    // 轮询等待新实例接管（服务短暂掉线属正常），恢复后刷新页面
    const deadline = Date.now() + 45000;
    const t = setInterval(async () => {
      try {
        const info = await api("/api/info");
        if (info && info.admin){
          clearInterval(t);
          toast("已以管理员身份重启 ✔", 3000);
          setTimeout(() => location.reload(), 800);
        }
      } catch(_){ /* 旧实例退出中，继续等 */ }
      if (Date.now() > deadline){
        clearInterval(t);
        btn.disabled = false; btn.textContent = "以管理员身份重启";
        toast("未检测到提权重启（可能 UAC 被取消），服务保持原样");
      }
    }, 1200);
  } catch(e){
    toast(e.message);
    btn.disabled = false; btn.textContent = "以管理员身份重启";
  }
}
$("#btnRestartAdmin").addEventListener("click", restartAdmin);

/* ---------- 重复文件事件 ---------- */
$("#btnDupScan").addEventListener("click", dupScan);
$("#btnDupClean").addEventListener("click", openDupModal);
$("#btnDoDup").addEventListener("click", doDupClean);
$("#dupCf").addEventListener("change", e => { $("#btnDoDup").disabled = !e.target.checked; });
$("#dupList").addEventListener("change", e => {
  const t = e.target;
  if (t.classList.contains("dup-g")){
    const h = t.dataset.h;
    if (t.checked) state.dup.chosen.add(h);
    else state.dup.chosen.delete(h);
    updateDupBar();
  } else if (t.classList.contains("dup-k")){
    state.dup.keep[t.dataset.h] = t.dataset.p;
    renderDup();
  }
});

/* ---------- 键盘（R8） ---------- */
document.addEventListener("keydown", e => {
  const inField = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);

  if (e.key === "/" && !inField){
    e.preventDefault();
    $("#q").focus();
    return;
  }
  if (e.key === "Escape"){
    const openMasks = $$(".mask.on");
    if (openMasks.length){ openMasks.forEach(m => m.classList.remove("on")); return; }
    if (document.activeElement === $("#q")){
      $("#q").value = ""; state.q = ""; renderTable(); renderSel(); $("#q").blur();
    }
    return;
  }
  if (inField || state.tab === "history") return;

  if (e.key === "ArrowDown" || e.key === "ArrowUp"){
    if (!state.rows.length) return;
    e.preventDefault();
    const d = e.key === "ArrowDown" ? 1 : -1;
    state.cursor = Math.min(state.rows.length - 1, Math.max(0, state.cursor + d));
    renderTable();
    const el = document.querySelector(`tr[data-i="${state.cursor}"]`);
    if (el) el.scrollIntoView({block:"nearest"});
    return;
  }
  if (e.key === " "){
    if (state.cursor < 0 || state.cursor >= state.rows.length) return;
    const row = state.rows[state.cursor];
    if (!isDeletable(row)) return;
    e.preventDefault();
    if (state.selected.has(row.p)) state.selected.delete(row.p);
    else state.selected.set(row.p, { s: row.s || 0, level: row.level || row.cat });
    renderTable(); renderSel();
  }
});

init();
