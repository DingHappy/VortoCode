// VortoCode Web 控制台逻辑（2026-08 从 agent.html 内联拆出；行为契约见 tests/unit/test_web_agent_html.py，
// 那里的 node harness 直接从本文件正则抽函数真跑——改下面钉住的函数前先看测试）。
const $ = (s) => document.querySelector(s);
const log = $("#log"), streamEl = $("#stream"), stateEl = $("#state");
let mode = "plan", busy = false, ws = null, streamBuf = "";
let reconnectAttempts = 0, reconnectTimer = null;
let pending = [];                          // 待发送的附件：[{name, url(dataURL), kind:"image"|"audio"}]

// 多模态：与后端 _sanitize_images/_sanitize_audio 对齐——限前缀/大小/个数（纯函数，便于测试）
const MAX_IMAGES = 6, MAX_IMG_CHARS = 8 * 1024 * 1024;
function _cap(urls, ok) {
  const out = [];
  for (const u of (urls || [])) {
    if (typeof u !== "string") continue;
    const s = u.trim();
    if (!ok(s) || s.length > MAX_IMG_CHARS) continue;
    out.push(s);
    if (out.length >= MAX_IMAGES) break;
  }
  return out;
}
function capImages(urls) {
  return _cap(urls, (s) => s.startsWith("data:image/") || s.startsWith("http://") || s.startsWith("https://"));
}
function capAudio(urls) {                   // 音频只收 data:audio/（input_audio 要内联数据，不收 http URL）
  return _cap(urls, (s) => s.startsWith("data:audio/"));
}
// 组装发往后端的 agent 消息：带图/音才放对应字段（纯函数，便于测试）
function buildAgentPayload(text, mode, images, audio) {
  const msg = { type: "agent", text, mode };
  const imgs = capImages(images);
  const auds = capAudio(audio);
  if (imgs.length) msg.images = imgs;
  if (auds.length) msg.audio = auds;
  return msg;
}

// 录音 → WAV：浏览器 MediaRecorder 多产出 webm（中转站不支持，只认 mp3/wav/m4a/ogg/flac），
// 所以自己把采到的 PCM 编成 16-bit WAV（全浏览器通用、模型已验证可读）。纯函数，便于测试。
function encodeWAV(samples, sampleRate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const w = (off, s) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE");
  w(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, n * 2, true);
  let off = 44;
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    off += 2;
  }
  return new Uint8Array(buf);
}
function wavDataUrl(samples, sampleRate) {
  const bytes = encodeWAV(samples, sampleRate);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return "data:audio/wav;base64," + btoa(bin);
}

// 断线自动重连：指数退避 0.8s→1.6→3.2→6.4→12.8→封顶15s（纯函数，便于测试）
function backoffDelay(n) { return Math.min(15000, 800 * Math.pow(2, n)); }
function scheduleReconnect() {
  if (reconnectTimer) return;                       // 已排队就不重复排
  const delay = backoffDelay(reconnectAttempts++);
  stateEl.textContent = "重连中…"; stateEl.style.color = "var(--warn)";
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, delay);
}

function add(cls, text) {
  const d = document.createElement("div");
  d.className = "msg " + cls; d.textContent = text;
  log.appendChild(d); log.scrollTop = log.scrollHeight;
}
function addHTML(cls, html) {     // 仅用于已渲染、已转义的安全 HTML（renderMarkdown 的输出）
  const d = document.createElement("div");
  d.className = "msg " + cls; d.innerHTML = html;
  log.appendChild(d); log.scrollTop = log.scrollHeight;
  return d;
}

// 空态首屏：对话区一有内容就让位（MutationObserver 盯 #log，不去挨个改渲染函数）
const heroEl = $("#hero");
function syncHero() { heroEl.classList.toggle("hide", log.children.length > 0); }
new MutationObserver(syncHero).observe(log, { childList: true });
syncHero();
document.querySelectorAll("#hero .chip").forEach((c) => {
  c.onclick = () => { const inp = $("#inp"); inp.value = c.dataset.fill || ""; inp.focus(); };
});

// 语音回复：给一条回复挂「🔊」按钮，点了请求后端 TTS、播放（纯 createElement，不碰 innerHTML）
let ttsSeq = 0; const ttsBtns = {}; let ttsAudio = null;
function attachSpeak(el, text) {
  if (!el || !text || !text.trim()) return;
  const b = document.createElement("button");
  b.className = "speak"; b.textContent = "🔊"; b.title = "朗读这条回复";
  b.onclick = () => requestTTS(text, b);
  el.appendChild(b);
}
function requestTTS(text, btn) {
  if (!ws || ws.readyState !== 1) return;
  const id = "tts" + (++ttsSeq);
  ttsBtns[id] = btn; btn.textContent = "⏳"; btn.disabled = true;
  ws.send(JSON.stringify({ type: "agent_tts", id, text }));
}
function playTTS(id, dataUrl) {
  const btn = ttsBtns[id]; if (btn) { btn.textContent = "🔊"; btn.disabled = false; }
  delete ttsBtns[id];
  try { if (ttsAudio) ttsAudio.pause(); ttsAudio = new Audio(dataUrl); ttsAudio.play(); } catch (e) {}
}
function ttsError(id, msg) {
  const btn = ttsBtns[id]; if (btn) { btn.textContent = "🔊"; btn.disabled = false; }
  delete ttsBtns[id];
  add("err", "语音合成失败：" + (msg || ""));
}
function setBusy(b) {                     // 忙时按钮变「停止」（仍可点，用于中断），闲时「发送」
  busy = b;
  const btn = $("#send");
  btn.textContent = b ? "停止" : "发送";
  btn.classList.toggle("stop", b);
}

// 工具活动折叠：一回合里的多次工具调用收进一个 <details>，运行时展开看实况、出结果后收起。
let toolGroup = null, toolCount = 0;
function toolLine(text) {
  if (!toolGroup) {
    toolGroup = document.createElement("details");
    toolGroup.className = "tools"; toolGroup.open = true;
    toolGroup.appendChild(document.createElement("summary"));
    log.appendChild(toolGroup); toolCount = 0;
  }
  const d = document.createElement("div");
  d.className = "say"; d.textContent = text;     // textContent：工具行始终纯文本
  toolGroup.appendChild(d); toolCount++;
  toolGroup.querySelector("summary").textContent = "🔧 工具活动 · " + toolCount;
  log.scrollTop = log.scrollHeight;
}
function endToolGroup() {     // 回合收尾：收起并定格计数
  if (!toolGroup) return;
  toolGroup.open = false;
  toolGroup.querySelector("summary").textContent = "🔧 " + toolCount + " 个工具调用";
  toolGroup = null;
}

// 轻量 Markdown 渲染：先转义全部 <>& 再只产出白名单标签 —— 模型文本里的任何原始 HTML
// 都被转义，绝不进 DOM，因此 innerHTML 安全（与 TUI 的 Rich Markdown 对齐）。
function renderMarkdown(src) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const safeUrl = (u) => {           // 仅放行 http/https/mailto，并编码引号尖括号防属性逃逸
    u = u.trim();
    if (!/^(https?:|mailto:)/i.test(u)) return "#";
    return u.replace(/"/g, "%22").replace(/'/g, "%27").replace(/</g, "%3C").replace(/>/g, "%3E");
  };
  const inline = (t) => {            // 先转义本段（XSS 在此消灭），再做行内：代码抽占位免被加粗/斜体污染
    t = esc(t);
    const codes = [];
    t = t.replace(/`([^`]+)`/g, (_, c) => { codes.push("<code>" + c + "</code>"); return "\u0000C" + (codes.length - 1) + "\u0000"; });
    t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
         .replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, "$1<i>$2</i>")
         .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
                  (_, txt, url) => '<a href="' + safeUrl(url) + '" target="_blank" rel="noopener noreferrer">' + txt + "</a>");
    return t.replace(/\u0000C(\d+)\u0000/g, (_, n) => codes[Number(n)]);
  };

  src = String(src).replace(/\u0000/g, "");          // 去掉占位用的控制符，杜绝注入
  const blocks = [];                                 // 1) 抽出围栏代码块，内部不做任何变换
  src = src.replace(/```[ \t]*[\w+-]*[ \t]*\n([\s\S]*?)```/g, (_, code) => {
    blocks.push("<pre><code>" + esc(code.replace(/\n+$/, "")) + "</code></pre>");
    return "\u0000B" + (blocks.length - 1) + "\u0000";
  });

  const lines = src.split("\n"), out = [];           // 2) 块级解析在「原文」上判定结构（> # - 不被转义）；转义在 inline 内逐段做
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (/^\u0000B\d+\u0000$/.test(line.trim())) { out.push(line.trim()); i++; continue; }
    if (!line.trim()) { i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { const n = h[1].length; out.push("<h" + n + ">" + inline(h[2].trim()) + "</h" + n + ">"); i++; continue; }
    if (/^\s*>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) { buf.push(inline(lines[i].replace(/^\s*>\s?/, ""))); i++; }
      out.push("<blockquote>" + buf.join("<br>") + "</blockquote>"); continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) { buf.push("<li>" + inline(lines[i].replace(/^\s*[-*+]\s+/, "")) + "</li>"); i++; }
      out.push("<ul>" + buf.join("") + "</ul>"); continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) { buf.push("<li>" + inline(lines[i].replace(/^\s*\d+[.)]\s+/, "")) + "</li>"); i++; }
      out.push("<ol>" + buf.join("") + "</ol>"); continue;
    }
    const buf = [];                                  // 段落：聚合到空行或下一个块起始
    while (i < lines.length && lines[i].trim()
           && !/^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s?)/.test(lines[i])
           && !/^\s*([-*_])(\s*\1){2,}\s*$/.test(lines[i])
           && !/^\u0000B\d+\u0000$/.test(lines[i].trim())) { buf.push(inline(lines[i])); i++; }
    out.push("<p>" + buf.join("<br>") + "</p>");
  }
  return out.join("\n").replace(/\u0000B(\d+)\u0000/g, (_, n) => blocks[Number(n)]);  // 还原代码块
}

function sessionId() {       // 稳定 sid（localStorage，跨刷新/关标签/浏览器重启）→ 服务端按它把
  let sid = localStorage.getItem("vorto_sid");   // 会话落盘，连服务器重启都不丢对话
  if (!sid) {
    sid = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
        : "s" + Date.now().toString(36) + Math.random().toString(36).slice(2);
    localStorage.setItem("vorto_sid", sid);
  }
  return sid;
}

// ---- 多会话管理：列表/切换/新建/删除/重命名（后端 /api/agent/sessions）----
function apiUrl(path) {                     // token 不再进 URL（审计 P0#4）：同源 fetch 自动带 httpOnly Cookie
  return path;
}

async function loadSessions() {
  let list = [];
  try {
    const r = await fetch(apiUrl("/api/agent/sessions"), { cache: "no-store" });
    if (r.ok) list = (await r.json()).sessions || [];
  } catch { /* 离线/未鉴权：静默降级，不影响对话 */ }
  renderSessions(list);
}

function renderSessions(list) {
  const box = $("#sessions"); box.replaceChildren();
  if (!list.length) {
    const e = document.createElement("div"); e.className = "empty";
    e.textContent = "还没有已保存的对话"; box.appendChild(e); return;
  }
  const cur = sessionId();
  list.forEach((s) => {
    const row = document.createElement("div");
    row.className = "sess" + (s.sid === cur ? " active" : "");
    const t = document.createElement("span"); t.className = "t";
    t.textContent = s.title || "新对话"; t.title = s.title || "";   // textContent → 标题安全、无 XSS
    const n = document.createElement("span"); n.className = "n"; n.textContent = s.messages || 0;
    const del = document.createElement("button"); del.className = "del"; del.textContent = "✕"; del.title = "删除";
    row.appendChild(t); row.appendChild(n); row.appendChild(del);
    row.onclick = () => { if (s.sid !== sessionId()) switchSession(s.sid); };
    del.onclick = (e) => { e.stopPropagation(); deleteSession(s.sid); };
    t.ondblclick = (e) => { e.stopPropagation(); renameSession(s.sid, s.title || ""); };
    box.appendChild(row);
  });
}

function _resetView() {                     // 切换会话前清空当前视图（服务端会回放新会话的 agent_history）
  endToolGroup(); log.replaceChildren(); streamBuf = ""; streamEl.textContent = "";
  const p = $("#plan"); p.classList.remove("show"); p.replaceChildren();
}

function switchSession(sid) {
  localStorage.setItem("vorto_sid", sid);
  try { if (ws) { ws.onclose = null; ws.close(); } } catch {}   // 断旧连接、不触发重连
  _resetView(); setBusy(false);
  connect();                                // 重连新 sid → 服务端回放该会话历史
  loadSessions();
}

function newSession() {
  const sid = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
      : "s" + Date.now().toString(36) + Math.random().toString(36).slice(2);
  switchSession(sid);                       // 新 sid：空会话，发首条消息后才落盘、才进列表
}

async function deleteSession(sid) {
  if (!confirm("删除这个对话？不可撤销。")) return;
  try { await fetch(apiUrl("/api/agent/sessions/" + encodeURIComponent(sid)), { method: "DELETE" }); } catch {}
  if (sid === sessionId()) { newSession(); return; }   // 删的是当前会话 → 开个新的
  loadSessions();
}

async function renameSession(sid, old) {
  const title = prompt("重命名对话：", old || "");
  if (title == null || !title.trim()) return;
  try {
    await fetch(apiUrl("/api/agent/sessions/" + encodeURIComponent(sid)),
      { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title.trim() }) });
  } catch {}
  loadSessions();
}

function connect() {
  const qs = new URLSearchParams();          // token 不进 URL：同源 WS 握手自动带 httpOnly Cookie 鉴权
  qs.set("sid", sessionId());
  const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws?" + qs.toString();
  ws = new WebSocket(url);
  ws.onopen = () => {
    reconnectAttempts = 0;                           // 连上即重置退避
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    stateEl.textContent = "已连接"; stateEl.style.color = "var(--ok)";
    loadSessions();                                  // 连上即刷新会话列表（高亮当前）
    try { ws.send(JSON.stringify({ type: "task_list" })); } catch {}   // hydrate 后台任务面板
    loadNotices();                                   // 顺手刷新通知台账
  };
  ws.onclose = () => {
    setBusy(false);
    endToolGroup(); streamBuf = ""; streamEl.textContent = "";   // 清掉半截回合（断线已中断在跑的回合）
    scheduleReconnect();
  };
  ws.onerror = () => { stateEl.textContent = "连接错误"; stateEl.style.color = "var(--err)"; };
  ws.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch { return; }
    switch (m.type) {
      case "agent_history": if (log.children.length === 0) renderHistory(m.items); break;  // 刷新/重连回放（空 log 才放，避免重复）
      case "agent_plan":   renderPlan(m.items); break;   // 任务清单：原地替换面板（幂等，重连也恢复）
      case "agent_say":    toolLine(m.text); break;
      case "agent_stream": streamBuf = m.text; streamEl.textContent = streamBuf; log.scrollTop = log.scrollHeight; break;
      case "agent_emit":   endToolGroup(); streamBuf = ""; streamEl.textContent = ""; attachSpeak(addHTML("emit", renderMarkdown(m.text)), m.text); break;
      case "agent_error":  endToolGroup(); add("err", "出错: " + m.text); setBusy(false); break;
      case "agent_done":   endToolGroup(); streamBuf = ""; streamEl.textContent = ""; setBusy(false); loadSessions(); break;   // 回合结束刷新列表（新会话此时才落盘、标题/条数更新）
      case "agent_cancelled": endToolGroup(); streamBuf = ""; streamEl.textContent = ""; add("say", "⏹ 已中断"); setBusy(false); break;
      case "agent_confirm": renderConfirm(m.id, m.text); break;   // 工具请求确认：弹允许/拒绝
      case "agent_tts_audio": playTTS(m.id, m.data); break;       // 语音回复就绪 → 播放
      case "agent_tts_error": ttsError(m.id, m.text); break;
      case "task_snapshot": taskMap = new Map((m.data || []).map((t) => [t.id, t])); renderTasks(); break;  // 连上/刷新的全量任务
      case "task_update":   upsertTask(m.data); break;            // 后台任务状态实时推送
      case "notice":        addNotice({ ts: new Date().toISOString(), source: "后台",
                                        text: (m.data && m.data.text) || "" }); break;   // cron/heartbeat 通知
    }
  };
}

function renderConfirm(id, text) {       // 确认块：消息 + 允许/拒绝；点一下即回应、按钮失效
    const box = document.createElement("div");
    box.className = "msg confirm";
    const msg = document.createElement("div");
    msg.className = "cmsg"; msg.textContent = "需要确认：" + text;   // textContent → 安全
    box.appendChild(msg);
    const row = document.createElement("div"); row.className = "crow";
    const reply = (ok) => {
      if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "agent_confirm_response", id, ok }));
      row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      msg.textContent += ok ? "  → 已允许" : "  → 已拒绝";
    };
    const yes = document.createElement("button"); yes.className = "cyes"; yes.textContent = "允许";
    const no = document.createElement("button"); no.className = "cno"; no.textContent = "拒绝";
    yes.onclick = () => reply(true); no.onclick = () => reply(false);
    row.appendChild(yes); row.appendChild(no); box.appendChild(row);
    log.appendChild(box); log.scrollTop = log.scrollHeight;
}

function renderHistory(items) {          // 回放之前的对话：用户行 + 最终回复（markdown，带🔊）
  (items || []).forEach((it) => {
    if (it.role === "user") add("user", it.text);
    else attachSpeak(addHTML("emit", renderMarkdown(it.text)), it.text);
  });
}

function renderPlan(items) {             // 任务清单面板：原地重渲；步骤文本用 textContent，绝无 XSS
  const el = $("#plan");
  el.textContent = "";
  if (!items || !items.length) { el.className = ""; return; }
  const done = items.filter((p) => p.status === "completed").length;
  const head = document.createElement("div");
  head.className = "head"; head.textContent = "📋 计划 · " + done + "/" + items.length;
  el.appendChild(head);
  const glyph = { completed: "✓", in_progress: "▸", pending: "○" };
  const cls = { completed: "done", in_progress: "doing", pending: "" };
  items.forEach((p) => {
    const row = document.createElement("div");
    row.className = "row " + (cls[p.status] || "");
    row.textContent = (glyph[p.status] || "○") + " " + p.step;
    el.appendChild(row);
  });
  el.className = "show";
}

function cancelTurn() {                  // 「停止」：请求中断正在跑的回合
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "agent_cancel" }));
}
// 把用户这轮的输入（文字 + 图缩略图 + 音频条）渲染进对话区
function addUserMsg(text, images, audio) {
  const d = document.createElement("div");
  d.className = "msg user";
  if (text) d.textContent = text;
  if (images && images.length) {
    const row = document.createElement("div");
    row.className = "shots";
    for (const u of images) { const im = document.createElement("img"); im.src = u; row.appendChild(im); }
    d.appendChild(row);
  }
  for (const u of (audio || [])) {
    const a = document.createElement("audio"); a.controls = true; a.src = u; a.className = "clip";
    d.appendChild(a);
  }
  log.appendChild(d); log.scrollTop = log.scrollHeight;
}

function renderThumbs() {
  const box = $("#thumbs");
  while (box.firstChild) box.removeChild(box.firstChild);
  pending.forEach((p, i) => {
    const rm = document.createElement("button"); rm.className = "x"; rm.textContent = "×";
    rm.title = "移除"; rm.onclick = () => { pending.splice(i, 1); renderThumbs(); };
    let t;
    if (p.kind === "audio") {              // 音频用一枚带🎵的小条（不渲染图）
      t = document.createElement("div"); t.className = "thumb audio";
      const lab = document.createElement("span"); lab.className = "alab"; lab.textContent = "🎵";
      lab.title = p.name || "audio"; t.appendChild(lab);
    } else {
      t = document.createElement("div"); t.className = "thumb";
      const im = document.createElement("img"); im.src = p.url; im.title = p.name || ""; t.appendChild(im);
    }
    t.appendChild(rm); box.appendChild(t);
  });
  box.classList.toggle("show", pending.length > 0);
}

// 读入图片/音频文件 → dataURL 进 pending（超量/超大/不支持都跳过并提示）
function addFiles(files) {
  for (const f of (files || [])) {
    if (!f || !f.type) continue;
    const kind = f.type.startsWith("image/") ? "image" : (f.type.startsWith("audio/") ? "audio" : null);
    if (!kind) { add("err", `不支持的文件类型：${f.name}`); continue; }
    if (pending.length >= MAX_IMAGES) { add("err", `最多附 ${MAX_IMAGES} 个文件`); break; }
    if (f.size > MAX_IMG_CHARS) { add("err", `「${f.name}」太大（>${Math.round(MAX_IMG_CHARS/1048576)}MB）`); continue; }
    const r = new FileReader();
    r.onload = () => { pending.push({ name: f.name, url: String(r.result), kind }); renderThumbs(); };
    r.readAsDataURL(f);
  }
}

function sendMsg() {
  if (busy) { cancelTurn(); return; }    // 忙时点按钮 = 停止
  const inp = $("#inp"), text = inp.value.trim();
  const images = capImages(pending.filter((p) => p.kind !== "audio").map((p) => p.url));
  const audio = capAudio(pending.filter((p) => p.kind === "audio").map((p) => p.url));
  if ((!text && !images.length && !audio.length) || !ws || ws.readyState !== 1) return;
  endToolGroup();                        // 新回合前先定格上一组工具活动
  addUserMsg(text, images, audio); inp.value = ""; pending = []; renderThumbs(); setBusy(true);
  ws.send(JSON.stringify(buildAgentPayload(text, mode, images, audio)));
}

$("#send").onclick = sendMsg;
$("#newchat").onclick = newSession;                 // 新建对话
$("#sbtoggle").onclick = () => $("#sidebar").classList.toggle("hidden");   // 显示/隐藏会话侧栏
$("#attach").onclick = () => $("#file").click();
$("#file").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });

// 录音：Web Audio 采 PCM → 自编 WAV（避开 MediaRecorder 的 webm 不被支持的坑）。
let recCtx = null, recProc = null, recStream = null, recBuf = [], recRate = 16000, recording = false;
const _canRecord = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia &&
                      (window.AudioContext || window.webkitAudioContext));
async function startRec() {
  try { recStream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (e) { add("err", "麦克风不可用或被拒绝：" + (e && e.message || e)); return; }
  recCtx = new (window.AudioContext || window.webkitAudioContext)();
  recRate = recCtx.sampleRate; recBuf = [];
  const src = recCtx.createMediaStreamSource(recStream);
  recProc = recCtx.createScriptProcessor(4096, 1, 1);
  recProc.onaudioprocess = (e) => { recBuf.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
  src.connect(recProc); recProc.connect(recCtx.destination);
  recording = true; const b = $("#rec"); b.classList.add("recording"); b.textContent = "⏹";
}
function stopRec() {
  recording = false; const b = $("#rec"); b.classList.remove("recording"); b.textContent = "🎙";
  if (recProc) { recProc.disconnect(); recProc.onaudioprocess = null; }
  if (recStream) recStream.getTracks().forEach((t) => t.stop());
  let len = 0; for (const c of recBuf) len += c.length;
  const all = new Float32Array(len); let o = 0; for (const c of recBuf) { all.set(c, o); o += c.length; }
  if (recCtx) recCtx.close();
  if (len === 0) { add("err", "没录到音频"); return; }
  if (pending.length >= MAX_IMAGES) { add("err", `最多附 ${MAX_IMAGES} 个文件`); return; }
  const url = wavDataUrl(all, recRate);
  if (url.length > MAX_IMG_CHARS) { add("err", "录音太长（超 8MB）"); return; }
  pending.push({ name: "录音.wav", url, kind: "audio" }); renderThumbs();
}
if (_canRecord) { $("#rec").hidden = false; $("#rec").onclick = () => (recording ? stopRec() : startRec()); }
$("#inp").addEventListener("paste", (e) => {
  const items = (e.clipboardData && e.clipboardData.items) || [];
  const imgs = [];
  for (const it of items) { if (it.kind === "file" && it.type.startsWith("image/")) { const f = it.getAsFile(); if (f) imgs.push(f); } }
  if (imgs.length) { e.preventDefault(); addFiles(imgs); }   // 粘贴的是图就拦下、当附件处理
});
const foot = $("footer");
["dragenter", "dragover"].forEach((ev) => foot.addEventListener(ev, (e) => { e.preventDefault(); foot.classList.add("drop"); }));
["dragleave", "drop"].forEach((ev) => foot.addEventListener(ev, (e) => { e.preventDefault(); if (ev === "drop") addFiles(e.dataTransfer.files); foot.classList.remove("drop"); }));
$("#inp").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!busy) sendMsg(); }
  else if (e.key === "Escape" && busy) { e.preventDefault(); cancelTurn(); }   // Esc 停止
});
$("#mode").onclick = () => {
  mode = mode === "plan" ? "build" : "plan";
  $("#mode").textContent = mode;
  $("#mode").classList.toggle("build", mode === "build");
};

// ---- 右侧控制台：后台任务（gateway TaskRunner 台账）+ 通知（cron/heartbeat 台账）----
// 数据三来源：WS task_snapshot（连上 hydrate）/ task_update（实时增量）/ GET /api/notices（持久台账）。
const tasksEl = $("#tasks"), noticesEl = $("#notices");
let taskMap = new Map();               // id → 任务 dict
const openTaskIds = new Set();         // 展开中的任务卡（重渲染保持）
let noticeList = [], noticeUnread = 0, consoleTab = "tasks";

function fmtTime(s) {
  const d = new Date(s);
  if (isNaN(d)) return String(s || "").slice(0, 16);
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}

const ST_LABEL = { queued: "○ 排队", running: "▸ 运行中", done: "✓ 完成", failed: "✗ 失败", cancelled: "⏹ 已取消" };

function renderTasks() {
  tasksEl.replaceChildren();
  const list = [...taskMap.values()].sort((a, b) => String(b.updated || "").localeCompare(String(a.updated || "")));
  if (!list.length) {
    const e = document.createElement("div"); e.className = "cs-empty";
    e.textContent = "还没有后台任务。上面描述一个开发任务点「后台跑」：自动分解 → 隔离 worktree 并行实现 → 自测自修复 → 落 vorto/* 分支，完成后可一键开 draft PR，全程不碰 main。";
    tasksEl.appendChild(e);
  }
  list.forEach((t) => tasksEl.appendChild(taskCard(t)));
  updateBadges();
}

function taskCard(t) {                 // 全部 textContent 渲染，任务文本/日志绝不进 innerHTML
  const card = document.createElement("div");
  card.className = "task" + (openTaskIds.has(t.id) ? " open" : "");
  const row = document.createElement("div"); row.className = "trow";
  const st = document.createElement("span"); st.className = "st " + (t.status || "");
  st.textContent = ST_LABEL[t.status] || (t.status || "?");
  const tp = document.createElement("span"); tp.className = "tp"; tp.textContent = t.prompt || "";
  row.append(st, tp);
  const meta = document.createElement("div"); meta.className = "meta";
  meta.textContent = [t.id, t.branch, fmtTime(t.updated)].filter(Boolean).join(" · ");
  meta.title = meta.textContent;
  const detail = document.createElement("div"); detail.className = "detail";
  if (t.log && t.log.length) {
    const lg = document.createElement("div"); lg.className = "tlog";
    lg.textContent = t.log.slice(-30).join("\n");
    detail.appendChild(lg);
    requestAnimationFrame(() => { lg.scrollTop = lg.scrollHeight; });   // 日志自动滚到最新
  }
  if (t.result) { const r = document.createElement("div"); r.className = "tres"; r.textContent = String(t.result).slice(-600); detail.appendChild(r); }
  if (t.error)  { const r = document.createElement("div"); r.className = "terr"; r.textContent = t.error; detail.appendChild(r); }
  const btns = document.createElement("div"); btns.className = "tbtns";
  if (t.status === "queued" || t.status === "running") {
    const b = document.createElement("button"); b.className = "bcancel"; b.textContent = "取消";
    b.onclick = (e) => { e.stopPropagation(); cancelBgTask(t.id); };
    btns.appendChild(b);
  }
  if (t.status === "done" && !t.error && t.branch && t.branch.startsWith("vorto/")) {
    const b = document.createElement("button"); b.className = "bpr"; b.textContent = "开 Draft PR";
    b.onclick = (e) => { e.stopPropagation(); openTaskPr(t.id, b); };   // 人在合并口：点这下=确认
    btns.appendChild(b);
  }
  if (btns.children.length) detail.appendChild(btns);
  card.append(row, meta, detail);
  card.onclick = (e) => {
    if (detail.contains(e.target)) return;          // 详情区内点击（选中日志等）不折叠
    if (openTaskIds.has(t.id)) openTaskIds.delete(t.id); else openTaskIds.add(t.id);
    card.classList.toggle("open");
  };
  return card;
}

function upsertTask(t) { if (t && t.id) { taskMap.set(t.id, t); renderTasks(); } }

function updateBadges() {              // 头部「控制台」角标 = 排队+运行中数；通知 tab 角标 = 未读数
  const act = [...taskMap.values()].filter((t) => t.status === "running" || t.status === "queued").length;
  const hb = $("#hbadge");
  hb.textContent = act; hb.classList.toggle("hide", act === 0);
  const nb = $("#nbadge");
  nb.textContent = noticeUnread; nb.classList.toggle("hide", noticeUnread === 0);
}

async function submitBgTask() {
  const inp = $("#taskinp"), prompt = inp.value.trim();
  if (!prompt) return;
  $("#taskgo").disabled = true;
  try {
    const r = await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" },
                                          body: JSON.stringify({ prompt }) });
    if (r.ok) inp.value = "";                       // 提交即返回；列表靠 task_update 推送刷新
    else add("err", "提交后台任务失败：HTTP " + r.status);
  } catch (e) { add("err", "提交后台任务失败：" + e); }
  $("#taskgo").disabled = false;
}

async function cancelBgTask(id) {
  try { await fetch("/api/tasks/" + encodeURIComponent(id) + "/cancel", { method: "POST" }); } catch {}
}

async function openTaskPr(id, btn) {
  if (!confirm("对该任务落的 vorto/* 分支开一个 draft PR？")) return;
  btn.disabled = true; btn.textContent = "开 PR 中…";
  try {
    const r = await (await fetch("/api/tasks/" + encodeURIComponent(id) + "/open_pr", { method: "POST" })).json();
    if (r.ok && r.url) {
      addNotice({ ts: new Date().toISOString(), source: "pr", text: "已开 draft PR：" + r.url });
      window.open(r.url, "_blank");
    } else add("err", "开 PR 失败：" + (r.error || "未知错误"));
  } catch (e) { add("err", "开 PR 失败：" + e); }
  btn.disabled = false; btn.textContent = "开 Draft PR";
}

async function loadNotices() {
  try {
    const r = await fetch("/api/notices?limit=50", { cache: "no-store" });
    if (r.ok) { noticeList = (await r.json()).notices || []; renderNotices(); }
  } catch { /* 离线/未鉴权：静默降级 */ }
}

function renderNotices() {
  noticesEl.replaceChildren();
  if (!noticeList.length) {
    const e = document.createElement("div"); e.className = "cs-empty";
    e.textContent = "暂无后台通知。cron 作业结果、heartbeat 值班发现、开 PR 记录会出现在这里。";
    noticesEl.appendChild(e); return;
  }
  noticeList.forEach((n) => {
    const d = document.createElement("div"); d.className = "notice";
    const t = document.createElement("div"); t.className = "nt";
    t.textContent = fmtTime(n.ts) + (n.source ? " · " + n.source : "");
    const x = document.createElement("div"); x.className = "nx"; x.textContent = n.text || "";
    d.append(t, x); noticesEl.appendChild(d);
  });
}

function addNotice(n) {
  noticeList.unshift(n); noticeList = noticeList.slice(0, 100); renderNotices();
  if (consoleTab !== "notices" || $("#console").classList.contains("hidden")) { noticeUnread++; updateBadges(); }
}

function showConsoleTab(name) {
  consoleTab = name;
  $("#tab-tasks").classList.toggle("active", name === "tasks");
  $("#tab-pipes").classList.toggle("active", name === "pipes");
  $("#tab-notices").classList.toggle("active", name === "notices");
  $("#tab-cron").classList.toggle("active", name === "cron");
  $("#tab-board").classList.toggle("active", name === "board");
  $("#tasknew").style.display = name === "tasks" ? "flex" : "none";
  tasksEl.style.display = name === "tasks" ? "" : "none";
  $("#pipes").style.display = name === "pipes" ? "" : "none";
  noticesEl.style.display = name === "notices" ? "" : "none";
  $("#cron").style.display = name === "cron" ? "" : "none";
  $("#board").style.display = name === "board" ? "" : "none";
  if (name === "notices") { noticeUnread = 0; updateBadges(); loadNotices(); }
  if (name === "pipes") loadPipes();
  if (name === "cron") loadCron();
  if (name === "board") loadBoard();
}
$("#tab-tasks").onclick = () => showConsoleTab("tasks");
$("#tab-pipes").onclick = () => showConsoleTab("pipes");
$("#tab-notices").onclick = () => showConsoleTab("notices");
$("#tab-cron").onclick = () => showConsoleTab("cron");
$("#tab-board").onclick = () => showConsoleTab("board");

// ---- 流水线（P1 可见性）：dev_auto/dev_resume 的 write-ahead 计划台账，只读投影 ----
// 数据源 GET /api/dev-plans（列表）+ /api/dev-plans/{id}/graph（服务端已算好拓扑 layers，
// 前端不需要图算法）。全部 textContent 渲染，计划文本/失败原因绝不进 innerHTML。
const pipesEl = $("#pipes");
const openPipeIds = new Set();         // 展开中的计划卡（轮询重渲染保持）
const _PIPE_LABEL = { running: "▸ 运行中", failed: "✗ 失败", done: "✓ 完成", integrated: "✓ 已集成",
                      landed: "✓ 已落地", pending: "○ 待跑", skipped: "– 已跳过" };
function pipeStClass(st) { return "st-" + String(st || "pending").replace(/[^a-z_-]/gi, ""); }
function fmtTokens(n) {                // 61379 → "61.4k"；199 → "199"；120万 → "1200k"
  n = Number(n) || 0;
  if (n < 1000) return String(n);
  const k = n / 1000;
  return (k >= 100 ? String(Math.round(k)) : k.toFixed(1)) + "k";
}

async function loadPipes() {
  if (consoleTab !== "pipes" || $("#console").classList.contains("hidden")) return;
  try {
    const r = await fetch(apiUrl("/api/dev-plans"), { cache: "no-store" });
    if (!r.ok) { pipesEl.textContent = "读取流水线失败：HTTP " + r.status; return; }
    renderPipes(await r.json());
  } catch (e) { pipesEl.textContent = "读取流水线失败：" + e; }
}
setInterval(loadPipes, 5000);          // 可见时 5s 一刷；不可见时函数首行直接返回

function renderPipes(list) {
  pipesEl.replaceChildren();
  if (!list || !list.length) {
    const e = document.createElement("div"); e.className = "cs-empty";
    e.textContent = "还没有流水线记录。dev_auto 跑过的每个计划（拆解 → 并行实现 → 集成 → PR）都会在这里留一张依赖图：每块的状态、token 花费、失败原因。";
    pipesEl.appendChild(e); return;
  }
  if (_autoOpenLatestPipe && list[0] && list[0].plan_id) {   // 深链进来：最新计划直接摊开
    openPipeIds.add(list[0].plan_id); _autoOpenLatestPipe = false;
  }
  list.forEach((p) => pipesEl.appendChild(pipeCard(p)));
}

function pipeCard(p) {
  const card = document.createElement("div");
  card.className = "pipe" + (openPipeIds.has(p.plan_id) ? " open" : "");
  const row = document.createElement("div"); row.className = "trow";
  const st = document.createElement("span"); st.className = "st " + pipeStClass(p.status);
  st.textContent = _PIPE_LABEL[p.status] || (p.status || "?");
  const tp = document.createElement("span"); tp.className = "tp"; tp.textContent = p.task || "";
  row.append(st, tp);
  const meta = document.createElement("div"); meta.className = "meta";
  meta.textContent = [p.plan_id, p.branch, p.summary, fmtTime(p.updated * 1000)].filter(Boolean).join(" · ");
  meta.title = meta.textContent;
  const detail = document.createElement("div"); detail.className = "detail";
  card.append(row, meta, detail);
  if (openPipeIds.has(p.plan_id)) loadPipeGraph(p.plan_id, detail);   // 轮询重渲染时恢复展开内容
  card.onclick = (e) => {
    if (detail.contains(e.target)) return;          // 详情区内点击（选中失败原因等）不折叠
    if (openPipeIds.has(p.plan_id)) { openPipeIds.delete(p.plan_id); card.classList.remove("open"); }
    else { openPipeIds.add(p.plan_id); card.classList.add("open"); loadPipeGraph(p.plan_id, detail); }
  };
  return card;
}

async function loadPipeGraph(id, box) {
  try {
    const r = await fetch(apiUrl("/api/dev-plans/" + encodeURIComponent(id) + "/graph"), { cache: "no-store" });
    if (!r.ok) { box.textContent = "读取依赖图失败：HTTP " + r.status; return; }
    renderPipeGraph(box, await r.json());
  } catch (e) { box.textContent = "读取依赖图失败：" + e; }
}

function renderPipeGraph(box, g) {
  box.replaceChildren();
  if (g.cycle) {                       // 计划被手改出了环：分层不完整，必须摆出来而不是画个残图装正常
    const w = document.createElement("div"); w.className = "cyc";
    w.textContent = "⚠ 计划里有依赖环（" + (g.cyclic_ids || []).join(", ") + "）——下面的分层不完整，多半是计划文件被手改坏了。";
    box.appendChild(w);
  }
  const byId = new Map((g.nodes || []).map((n) => [n.id, n]));
  const dag = document.createElement("div"); dag.className = "dag";
  (g.layers || []).forEach((layer, i) => {
    const col = document.createElement("div"); col.className = "dagcol";
    const h = document.createElement("div"); h.className = "colhead";
    h.textContent = "批 " + (i + 1) + (layer.length > 1 ? " · " + layer.length + " 并行" : "");
    col.appendChild(h);
    layer.forEach((id) => { const n = byId.get(id); if (n) col.appendChild(dagNode(n)); });
    dag.appendChild(col);
  });
  box.appendChild(dag);
  // 尾行：总花费 + 集成结果 + PR 去向。pr/integration 是结构化对象（note/url、ok/output），
  // 直接 String() 会变成 [object Object]——真机截图当场撞到过。
  const foot = document.createElement("div"); foot.className = "pfoot";
  const bits = ["合计 " + fmtTokens(g.tokens) + " tokens"];
  const integ = g.integration;
  if (integ && typeof integ === "object") bits.push("集成 " + (integ.ok ? "✓" : "✗"));
  else if (integ) bits.push("集成 " + String(integ).slice(0, 60));
  const pr = g.pr;
  const prText = (pr && typeof pr === "object") ? (pr.url || pr.note || "") : (pr || "");
  if (prText && !/^https?:\/\//.test(prText)) bits.push(String(prText).slice(0, 90));
  foot.textContent = bits.join(" · ");
  foot.title = [prText, integ && integ.output].filter(Boolean).join("\n").slice(0, 600);
  if (/^https?:\/\//.test(prText)) {           // PR 已开成：给个能点的链接（href 只放行 http/https）
    const a = document.createElement("a");
    a.href = prText; a.target = "_blank"; a.rel = "noopener noreferrer";
    a.textContent = " 打开 PR ↗"; a.style.color = "var(--acc2)";
    foot.appendChild(a);
  }
  box.appendChild(foot);
}

function dagNode(n) {
  const el = document.createElement("div"); el.className = "dagnode " + pipeStClass(n.status);
  const t = document.createElement("div"); t.className = "nt";
  t.textContent = (_PIPE_LABEL[n.status] || "○").slice(0, 1) + " " + (n.title || n.id);
  t.title = n.desc || n.title || "";
  el.appendChild(t);
  const m = document.createElement("div"); m.className = "nm";
  const bits = [fmtTokens(n.tokens) + " tok"];
  if (n.attempts > 1) bits.push("重试×" + (n.attempts - 1));
  m.textContent = bits.join(" · ");
  el.appendChild(m);
  if (n.deps && n.deps.length) {
    const d = document.createElement("div"); d.className = "nd";
    d.textContent = "← " + n.deps.join(", ");
    d.title = "依赖：" + n.deps.join(", ");
    el.appendChild(d);
  }
  if (n.status === "failed" && n.note) {   // 失败原因就地可读——别让人去翻 .vortocode 的 JSON
    const w = document.createElement("div"); w.className = "nnote";
    w.textContent = String(n.note).slice(0, 400);
    w.title = n.note;
    el.appendChild(w);
  }
  return el;
}

// ---- 员工看板（B9-①）：各 agent 在干什么 + 运行中 + 定时摘要，一屏聚合 ----
// 纯前端聚合既有 API（sessions 端点本就是状态优先级排序的 dashboard 快照），不加新路由。
const _BOARD_STATUS = {
  needs_input: ["待你输入", "var(--err)"], failed: ["需处理", "var(--err)"],
  working: ["工作中", "var(--ok)"], queued: ["有排队输入", "var(--warn)"],
  idle: ["空闲", "var(--tx2)"], inactive: ["历史会话", "var(--tx3)"], completed: ["已完成", "var(--tx3)"],
};

async function loadBoard() {
  if (consoleTab !== "board" || $("#console").classList.contains("hidden")) return;
  const box = $("#board");
  try {
    const [sessR, runsR, cronR] = await Promise.all([
      fetch(apiUrl("/api/agent/sessions"), { cache: "no-store" }),
      fetch(apiUrl("/api/runs"), { cache: "no-store" }),
      fetch(apiUrl("/api/cron"), { cache: "no-store" }),
    ]);
    // 非 2xx 不许画成"全空=健康"的假盘面（对抗审查 F8）：失败的路点名置顶
    const problems = [];
    if (!sessR.ok) problems.push("会话 HTTP " + sessR.status);
    if (!runsR.ok) problems.push("运行 HTTP " + runsR.status);
    if (!cronR.ok) problems.push("定时 HTTP " + cronR.status);
    renderBoard(
      sessR.ok ? (await sessR.json()).sessions || [] : [],
      runsR.ok ? (await runsR.json()).runs || [] : [],
      cronR.ok ? await cronR.json() : { enabled: false, jobs: [] },
      problems,
    );
  } catch (e) { box.textContent = "看板读取失败：" + e; }
}
setInterval(loadBoard, 5000);              // 看板可见时 5s 一刷；不可见时函数首行直接返回

function _boardHead(text) {
  const el = document.createElement("div");
  el.style.cssText = "padding:8px 10px 2px;font-size:12px;color:var(--tx3);letter-spacing:.5px";
  el.textContent = text;
  return el;
}

function renderBoard(sessions, runs, cron, problems = []) {
  const box = $("#board");
  box.replaceChildren();          // 同 cron tab：不新增 innerHTML 面（仓库安全契约）
  if (problems.length) {
    const warn = document.createElement("div");
    warn.style.cssText = "margin:6px 8px;padding:6px 10px;border:1px solid var(--err);border-radius:8px;color:var(--err);font-size:12px";
    warn.textContent = "部分数据读取失败（下方对应区不可信）：" + problems.join("，");
    box.appendChild(warn);
  }
  // ① 会话：谁在干什么（API 已按 需处理>工作中>排队>空闲 排序，历史会话折进计数）
  box.appendChild(_boardHead("会话"));
  const live = sessions.filter((s) => s.status !== "inactive" && s.status !== "completed");
  if (!live.length) {
    const empty = document.createElement("div");
    empty.style.cssText = "padding:4px 10px;color:var(--tx3);font-size:13px";
    empty.textContent = "没有活跃会话";
    box.appendChild(empty);
  }
  for (const s of live) {
    const [label, color] = _BOARD_STATUS[s.status] || [s.status, "var(--tx2)"];
    const row = document.createElement("div");
    row.style.cssText = "margin:4px 8px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;cursor:pointer;background:var(--raise)";
    const top = document.createElement("div");
    top.style.cssText = "display:flex;gap:7px;align-items:center;font-size:13px";
    const dot = document.createElement("span");
    dot.style.cssText = "width:8px;height:8px;border-radius:50%;flex:none;background:" + color;
    const title = document.createElement("b");
    title.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1";
    title.textContent = s.title || s.sid;
    const st = document.createElement("span");
    st.style.cssText = "font-size:11px;color:" + color;
    st.textContent = label + (s.pending_input_count ? " ×" + s.pending_input_count : "");
    top.append(dot, title, st);
    row.appendChild(top);
    const doing = s.activity || s.running_prompt
      || (s.background_tasks && s.background_tasks.active ? s.background_tasks.latest_prompt : "");
    if (doing) {
      const act = document.createElement("div");
      act.style.cssText = "margin-top:3px;font-size:12px;color:var(--tx1);overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
      act.textContent = doing;
      act.title = doing;
      row.appendChild(act);
    }
    const bits = [];
    if (s.queue_count) bits.push("排队输入 " + s.queue_count);
    if (s.background_tasks && s.background_tasks.active) bits.push("后台任务 " + s.background_tasks.active + " 跑着");
    if (s.background_tasks && s.background_tasks.attention) bits.push("任务待处理 " + s.background_tasks.attention);
    if (s.hook_issues && s.hook_issues.count) bits.push("Hook 提示 " + s.hook_issues.count);
    if (bits.length) {
      const meta = document.createElement("div");
      meta.style.cssText = "margin-top:2px;font-size:12px;color:var(--tx3)";
      meta.textContent = bits.join(" · ");
      row.appendChild(meta);
    }
    row.onclick = () => switchSession(s.sid);
    box.appendChild(row);
  }
  const rest = sessions.length - live.length;
  if (rest > 0) {
    const more = document.createElement("div");
    more.style.cssText = "padding:2px 10px 6px;color:var(--tx3);font-size:12px";
    more.textContent = "另有 " + rest + " 个历史会话（左侧列表可切换）";
    box.appendChild(more);
  }
  // ② 运行：命令/测试/预览 run 的活跃与待处理失败
  const activeRuns = runs.filter((r) => ["queued", "running", "cancelling"].includes(r.status));
  const failedRuns = runs.filter((r) => r.status === "failed" && !r.dismissed);
  box.appendChild(_boardHead("运行"));
  const runLine = document.createElement("div");
  runLine.style.cssText = "padding:2px 10px 6px;font-size:13px;color:" + (failedRuns.length ? "var(--err)" : "var(--tx1)");
  runLine.textContent = activeRuns.length + " 个在跑 · " + failedRuns.length + " 个失败待处理";
  box.appendChild(runLine);
  for (const r of activeRuns.slice(0, 5)) {
    const line = document.createElement("div");
    line.style.cssText = "padding:1px 10px;font-size:12px;color:var(--tx1);overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    line.textContent = "▸ " + (r.command || r.kind || r.id);
    line.title = r.command || "";
    box.appendChild(line);
  }
  // ③ 定时：总开关 + 最近的下次应跑 + 连败点名（详情在「定时」tab）
  box.appendChild(_boardHead("定时"));
  const dueTimes = cron.jobs.filter((j) => j.next_due).map((j) => j.next_due).sort();
  const sick = cron.jobs.filter((j) => j.failures > 0);
  const cronLine = document.createElement("div");
  cronLine.style.cssText = "padding:2px 10px 8px;font-size:13px;color:" + (sick.length ? "var(--err)" : "var(--tx1)") + ";cursor:pointer";
  const cronBits = [cron.enabled ? "调度已开" : "调度未开"];
  if (dueTimes.length) cronBits.push("最近应跑 " + dueTimes[0].replace("T", " "));
  if (sick.length) cronBits.push("连败：" + sick.map((j) => j.name + "×" + j.failures).join("，"));
  cronLine.textContent = cronBits.join(" · ");
  cronLine.onclick = () => showConsoleTab("cron");
  box.appendChild(cronLine);
}

// ---- 定时作业观察面（B9-②，只读 + 手动触发；数据源 GET /api/cron）----
async function loadCron() {
  const box = $("#cron");
  try {
    const r = await fetch(apiUrl("/api/cron"), { cache: "no-store" });
    if (!r.ok) { box.textContent = "读取定时作业失败：HTTP " + r.status; return; }
    renderCron(await r.json());
  } catch (e) { box.textContent = "读取定时作业失败：" + e; }
}

function renderCron(data) {
  const box = $("#cron");
  box.replaceChildren();          // 清空用 replaceChildren：不新增 innerHTML 面（仓库安全契约，见 test_web_agent_html）
  const head = document.createElement("div");
  head.style.cssText = "padding:8px 10px;font-size:12px;color:" + (data.enabled ? "var(--ok)" : "var(--warn)");
  head.textContent = data.enabled
    ? "调度循环已开（VORTOCODE_CRON=1）"
    : "调度循环未开——下面的作业不会自动跑，手动触发不受影响";
  box.appendChild(head);
  if (!data.jobs.length) {
    const empty = document.createElement("div");
    empty.style.cssText = "padding:10px;color:var(--tx3);font-size:13px";
    empty.textContent = "没有定时作业。样例：config/cron.example.yaml → .vortocode/cron.yaml";
    box.appendChild(empty);
    return;
  }
  for (const job of data.jobs) box.appendChild(cronCard(job, data.next_due_horizon_days || 62));
}

function cronCard(job, horizonDays) {
  const card = document.createElement("div");
  card.style.cssText = "margin:6px 8px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--raise);"
    + (job.enabled ? "" : "opacity:.55");
  const row = document.createElement("div");
  row.style.cssText = "display:flex;gap:8px;align-items:center;font-size:13px";
  const name = document.createElement("b"); name.textContent = job.name;
  const kind = document.createElement("span");
  kind.style.cssText = "font-size:11px;color:var(--tx3);border:1px solid var(--line2);border-radius:4px;padding:0 5px";
  kind.textContent = job.kind === "command" ? "命令" : "LLM";
  const sched = document.createElement("span");
  sched.style.cssText = "color:var(--tx3);font-size:12px";
  sched.textContent = job.schedule + (job.enabled ? "" : " · 已停用");
  const sp = document.createElement("span"); sp.style.flex = "1";
  row.append(name, kind, sched, sp);
  if (job.enabled) {                       // 停用作业不给触发入口（服务端同样 409 兜底）
    const go = document.createElement("button");
    go.textContent = "触发"; go.title = "立即手动跑一次（结果进通知/运行台账）";
    go.style.cssText = "background:var(--hover);color:var(--tx1);border:1px solid var(--line2);border-radius:6px;padding:2px 10px;cursor:pointer";
    go.onclick = async () => {
      go.disabled = true;
      try {
        const r = await fetch(apiUrl("/api/cron/" + encodeURIComponent(job.name) + "/run"), { method: "POST" });
        if (r.ok) addNotice({ ts: new Date().toISOString(), source: "cron", text: "已手动触发 [" + job.name + "]，结果看通知/运行台账" });
        else add("err", "触发失败：HTTP " + r.status + " " + (await r.text()).slice(0, 200));
      } catch (e) { add("err", "触发失败：" + e); }
      go.disabled = false;
    };
    row.append(go);
  }
  const meta = document.createElement("div");
  meta.style.cssText = "margin-top:4px;font-size:12px;color:var(--tx3)";
  const bits = [];
  bits.push(job.last_run ? "上次 " + job.last_run.replace("T", " ") : "从未跑过");
  if (job.next_due) bits.push("下次应跑 " + job.next_due.replace("T", " "));
  else if (job.enabled) bits.push(horizonDays + " 天内无排期");
  if (job.budget) bits.push("预算 " + job.budget + " tokens");
  meta.textContent = bits.join(" · ");
  const detail = document.createElement("div");
  detail.style.cssText = "margin-top:4px;font-size:12px;color:var(--tx1);white-space:pre-wrap;word-break:break-all";
  detail.textContent = (job.detail || "").slice(0, 200) + ((job.detail || "").length > 200 ? "…" : "");
  detail.title = job.detail || "";
  card.append(row, meta, detail);
  if (job.failures > 0) {
    const warn = document.createElement("div");
    warn.style.cssText = "margin-top:4px;font-size:12px;color:var(--err)";
    warn.textContent = "已连续失败 " + job.failures + " 次";
    card.appendChild(warn);
  }
  return card;
}
$("#taskgo").onclick = submitBgTask;
$("#taskinp").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitBgTask(); }
});
$("#ctoggle").onclick = () => {
  const el = $("#console"), want = el.classList.contains("hidden");
  el.classList.toggle("hidden", !want);
  localStorage.setItem("vorto_console", want ? "1" : "0");
  if (want && consoleTab === "notices") { noticeUnread = 0; updateBadges(); }
  if (want && consoleTab === "pipes") loadPipes();
};
// 初始：记住上次开合；没记录时宽屏默认开（让后台能力第一眼可见）、窄屏默认收
if (localStorage.getItem("vorto_console") === "1" ||
    (localStorage.getItem("vorto_console") == null && window.innerWidth >= 1180)) {
  $("#console").classList.remove("hidden");
}
// 手机（P3）：会话侧栏是 fixed 覆盖层，默认展开会把对话整个盖死——窄屏首帧就收起。
// 只做首帧判断、不监听 resize：旋转屏时替人开关面板比不动更烦。
if (window.innerWidth < 760) {
  $("#sidebar").classList.add("hidden");
  $("#inp").placeholder = "问点什么…";   // 长占位文案在 180px 宽的输入框里会折行被裁
}

// 哈希深链：/agent#pipes 等直接打开对应控制台 tab——IM 通知里的「看进度」链接就指这里。
// #pipes 额外自动展开最新计划：深链的场景就是"看这次跑到哪了"，进来还要再点一下是折磨。
let _autoOpenLatestPipe = false;
const _hashTab = (location.hash || "").slice(1);
if (["tasks", "pipes", "notices", "cron", "board"].includes(_hashTab)) {
  $("#console").classList.remove("hidden");
  if (_hashTab === "pipes") _autoOpenLatestPipe = true;
  showConsoleTab(_hashTab);
}

// ---- 登录门（审计 P0#4）：设了 token 且未登录 → 弹框收 token → POST 种 httpOnly Cookie → 再连 ----
function showLogin() {
  return new Promise((resolve) => {
    const ov = document.createElement("div");
    ov.style.cssText = "position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:9999";
    const card = document.createElement("div");
    card.style.cssText = "background:var(--panel);border:1px solid var(--line2);border-radius:12px;padding:24px;width:320px;color:var(--tx);font:14px var(--sans)";
    const h = document.createElement("div"); h.textContent = "🔒 需要访问令牌"; h.style.cssText = "font-weight:600;margin-bottom:12px";
    const inp = document.createElement("input"); inp.type = "password"; inp.placeholder = "API Token";
    inp.style.cssText = "width:100%;box-sizing:border-box;padding:9px;background:var(--bg);border:1px solid var(--line2);border-radius:8px;color:var(--tx);margin-bottom:10px";
    const err = document.createElement("div"); err.style.cssText = "color:var(--err);font-size:12px;height:16px;margin-bottom:8px";
    const go = document.createElement("button"); go.textContent = "登录";
    go.style.cssText = "width:100%;padding:9px;background:var(--acc);color:#fff;border:0;border-radius:8px;cursor:pointer;font-weight:600";
    card.append(h, inp, err, go); ov.appendChild(card); document.body.appendChild(ov); inp.focus();
    async function submit() {
      err.textContent = "";
      try {
        const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: inp.value }) });
        if (r.ok) { ov.remove(); resolve(); } else { err.textContent = "令牌不正确"; inp.select(); }
      } catch { err.textContent = "登录失败，请重试"; }
    }
    go.onclick = submit;
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  });
}
async function boot() {
  try {
    const st = await (await fetch("/api/auth/status", { cache: "no-store" })).json();
    if (st.auth_required && !st.authed) await showLogin();   // 需鉴权且未登录 → 先登录
  } catch { /* 状态查不到：按无鉴权处理，直接连 */ }
  connect();
}
boot();
