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
  undo: "撤销还原", "trash-empty": "清空回收站",
};
function lv(x) { return LEVEL[x.level || x.cat] || LEVEL.data; }
function isDeletable(x) { return x.deletable !== false && (x.level || x.cat) !== "protected"; }

const state = {
  version: "", build: "", about: null,
  root: "", diskFree: 0, diskTotal: 0,
  hot: [], big: [], errors: [], truncated: null,
  incremental: null, snapshot: null,
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
function toast(msg){
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("on"), 2800);
}
async function api(path, body){
  const opt = body ? {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)} : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
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
    const st = await api("/api/scan/status");
    if (st.done && !st.running) await loadResult(true);
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

function renderTable(){
  const tb = $("#tbody");
  if (state.tab === "history"){ renderHistory(); return; }
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
      <td><button class="btn ghost" data-open="${esc(r.p)}">位置</button></td>
    </tr>`;
  }).join("");
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
      ? `清单中有 <b>${r.blockedCount}</b> 项属于系统/程序数据（已标红），执行时会被自动跳过。<br>请确认其余项目确实可以清理：绿色为已知安全缓存，黄色为疑似缓存，请对照说明再决定。`
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
    $("#dest").value = "D:\\AppDataBackup";
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
    renderHistory();
  } catch(e){
    toast("历史读取失败：" + e.message);
  }
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

document.addEventListener("click", e => {
  const t = e.target.closest("[data-close]");
  if (t) closeMask(t.dataset.close);

  const caret = e.target.closest("[data-toggle]");
  if (caret) { toggleExpand(caret.dataset.toggle, e); return; }

  const open = e.target.closest("[data-open]");
  if (open){ e.stopPropagation(); api("/api/open", { path: open.dataset.open }).catch(err => toast(err.message)); return; }

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
  if (e.target.classList.contains("rowchk")){
    const p = e.target.dataset.path;
    if (e.target.checked) state.selected.set(p, { s: +e.target.dataset.size, level: e.target.dataset.level });
    else state.selected.delete(p);
    e.target.closest("tr").classList.toggle("sel", e.target.checked);
    renderSel();
  }
  if (["cf1","cf2"].includes(e.target.id)) updateDoBtn();
});

document.addEventListener("input", e => {
  if (["permaText","dest"].includes(e.target.id)) updateDoBtn();
  if (e.target.id === "trashText") updateTrashBtn();
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
  $("#listWrap").style.display = isHist ? "none" : "block";
  $("#histWrap").style.display = isHist ? "block" : "none";
  $("#filterBar").style.display = isHist ? "none" : "flex";
  state.expanded.clear();
  state.cursor = -1;
  if (isHist){
    $("#truncBar").classList.remove("on");
    $("#truncBar").innerHTML = "";
    if (!state.historyLoaded) loadHistory();
    else renderHistory();
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
$("#btnRescan").addEventListener("click", () => startScan(false));
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
