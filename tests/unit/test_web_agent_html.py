"""web/agent.html 的 Markdown 渲染：安全不变量 + 行为（node 可用时）。

最终回复(agent_emit)在网页端渲染 Markdown，与 TUI 的 Rich Markdown 对齐。渲染器
「先转义全部 <>& 再只产出白名单标签」，故 innerHTML 安全。这里钉住关键不变量，
并在有 node 时实际跑一遍渲染（含 XSS 向量）。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

AGENT_HTML = Path(__file__).resolve().parents[2] / "web" / "agent.html"


def _src() -> str:
    return AGENT_HTML.read_text(encoding="utf-8")


def test_emit_renders_markdown_not_plaintext():
    s = _src()
    assert "function renderMarkdown(" in s
    # 最终回复走 markdown 渲染，而不是纯文本
    assert 'addHTML("emit", renderMarkdown(' in s
    assert 'add("emit"' not in s
    # innerHTML 只在 addHTML 一处（受控、喂的是已转义的渲染输出）
    assert s.count(".innerHTML") == 1
    # 流式增量仍是纯文本，不渲染半成品 markdown
    assert "streamEl.textContent = streamBuf" in s


def test_renderer_escapes_first_and_sanitizes_links():
    s = _src()
    # esc 先把 & < > 转义（XSS 在此消灭）
    for ent in ('"&amp;"', '"&lt;"', '"&gt;"'):
        assert ent in s
    # 链接仅放行 http/https/mailto，且对引号编码防属性逃逸
    assert "/^(https?:|mailto:)/i" in s
    assert '"%22"' in s


_NODE_HARNESS = r"""
import { readFileSync } from "node:fs";
const html = readFileSync("__PATH__", "utf8");
const m = html.match(/function renderMarkdown[\s\S]*?\n\}/);
if (!m) { console.error("renderMarkdown not found"); process.exit(2); }
const renderMarkdown = eval("(" + m[0].replace(/^function renderMarkdown/, "function") + ")");
const NUL = String.fromCharCode(0);
let bad = 0;
const ok = (n, c, g) => { if (!c) { bad++; console.error("FAIL " + n + " :: " + JSON.stringify(g)); } };
let h;
h = renderMarkdown("<script>alert(1)</script>");
ok("script-escaped", !/<script>/.test(h) && h.includes("&lt;script&gt;"), h);
h = renderMarkdown("[x](javascript:alert(1))");
ok("js-link", h.includes('href="#"') && !/javascript:/.test(h), h);
h = renderMarkdown('[x](https://a"onerror=alert(1))');
ok("attr-breakout", h.includes("%22") && !h.includes('"onerror'), h);
h = renderMarkdown(NUL + "B0" + NUL + " x");
ok("nul-strip", !h.includes(NUL) && !h.includes("undefined"), h);
h = renderMarkdown("# T");
ok("h1", h.includes("<h1>T</h1>"), h);
h = renderMarkdown("**b** *i* `c`");
ok("inline", h.includes("<b>b</b>") && h.includes("<i>i</i>") && h.includes("<code>c</code>"), h);
h = renderMarkdown("- a\n- b");
ok("ul", h.includes("<ul><li>a</li><li>b</li></ul>"), h);
h = renderMarkdown("> q\n> r");
ok("blockquote", h.includes("<blockquote>q<br>r</blockquote>"), h);
h = renderMarkdown("```\nx <b>\n```");
ok("fenced-code", h.includes("<pre><code>") && h.includes("x &lt;b&gt;") && !h.includes("<b>"), h);
process.exit(bad ? 1 : 0);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用，跳过行为测试")
def test_renderer_behavior_via_node():
    harness = _NODE_HARNESS.replace("__PATH__", str(AGENT_HTML))
    r = subprocess.run(["node", "--input-type=module"], input=harness,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, (r.stdout + r.stderr)


def test_tool_activity_grouping_wiring():
    s = _src()
    assert "function toolLine(" in s and "function endToolGroup(" in s
    # 工具提示走折叠分组，而不是逐行裸 say（"⏹ 已中断"是唯一允许的一次性 say 行）
    assert "case \"agent_say\":    toolLine(" in s
    assert s.count('add("say"') <= 1
    # 每个回合收尾都定格分组：emit / error / done / 新回合(sendMsg)（断线清理是额外一处）
    assert s.count("endToolGroup();") >= 4
    # 折叠用 <details class="tools">，工具行始终 textContent（纯文本）
    assert 'createElement("details")' in s and ".tools" in s
    assert 'd.className = "say"; d.textContent = text' in s


_GROUP_HARNESS = r"""
import { readFileSync } from "node:fs";
const html = readFileSync("__PATH__", "utf8");
const m = html.match(/let toolGroup = null[\s\S]*?\nfunction endToolGroup[\s\S]*?\n\}/);
if (!m) { console.error("tool-group fns not found"); process.exit(2); }
class El {
  constructor(tag){ this.tagName = tag; this.children = []; this.className = ""; this._text = ""; this.scrollTop = 0; this.scrollHeight = 0; }
  appendChild(c){ this.children.push(c); return c; }
  querySelector(sel){ for (const c of this.children){ if (c.tagName === sel) return c; const f = c.querySelector(sel); if (f) return f; } return null; }
  get textContent(){ return this._text; }
  set textContent(v){ this._text = String(v); }
}
const document = { createElement: (t) => new El(t) };
const log = new El("div");
const api = eval(m[0] + "\n; ({ toolLine, endToolGroup, peek: () => ({ tg: toolGroup, tc: toolCount }) })");
let bad = 0;
const ok = (n, c, g) => { if (!c) { bad++; console.error("FAIL " + n + " :: " + JSON.stringify(g)); } };

api.toolLine("a"); api.toolLine("b");
ok("one group", log.children.length === 1 && log.children[0].tagName === "details", log.children.length);
const g1 = log.children[0];
ok("open while running", g1.open === true, g1.open);
ok("two say lines", g1.children.filter(c => c.tagName === "div").length === 2, g1.children.length);
ok("live count", g1.querySelector("summary").textContent === "🔧 工具活动 · 2", g1.querySelector("summary").textContent);
ok("peek 2 / active", api.peek().tc === 2 && api.peek().tg !== null, api.peek());

api.endToolGroup();
ok("collapsed on end", g1.open === false, g1.open);
ok("final summary", g1.querySelector("summary").textContent === "🔧 2 个工具调用", g1.querySelector("summary").textContent);
ok("reset after end", api.peek().tg === null, api.peek().tg);

api.toolLine("c");
ok("new group next turn", log.children.length === 2, log.children.length);
ok("count reset", api.peek().tc === 1, api.peek().tc);
process.exit(bad ? 1 : 0);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用，跳过行为测试")
def test_tool_activity_grouping_via_node():
    harness = _GROUP_HARNESS.replace("__PATH__", str(AGENT_HTML))
    r = subprocess.run(["node", "--input-type=module"], input=harness,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, (r.stdout + r.stderr)


# ---- 图片输入（附图：📎/拖拽/粘贴）----

def test_image_attach_wiring():
    s = _src()
    # 入口齐全：附件按钮 + 隐藏 file input（图+音）+ 缩略图条
    assert 'id="attach"' in s and 'id="file"' in s and 'id="thumbs"' in s
    assert 'accept="image/*,audio/*"' in s and "multiple" in s
    # 拖拽与粘贴都接了
    assert 'addEventListener("paste"' in s
    assert '"dragenter"' in s and '"drop"' in s and "dataTransfer.files" in s
    # 发送走 buildAgentPayload（带图/音才放对应字段），不再裸发 {type:"agent",text,mode}
    assert "ws.send(JSON.stringify(buildAgentPayload(text, mode, images, audio)))" in s
    assert 'ws.send(JSON.stringify({ type: "agent", text, mode }))' not in s
    # 缩略图与用户气泡都用 createElement/textContent/src，不引入新的 innerHTML
    assert s.count(".innerHTML") == 1
    # 读文件用 FileReader→dataURL
    assert "readAsDataURL" in s


def test_tts_playback_wiring():
    s = _src()
    # 每条回复挂🔊：emit 与回放都 attachSpeak
    assert "attachSpeak(addHTML(\"emit\", renderMarkdown(m.text)), m.text)" in s
    assert "attachSpeak(addHTML(\"emit\", renderMarkdown(it.text)), it.text)" in s
    # 点🔊 → 发 agent_tts；收到音频/错误分别处理
    assert 'type: "agent_tts"' in s
    assert 'case "agent_tts_audio":' in s and 'case "agent_tts_error":' in s
    # 播放用 new Audio（浏览器内播），按钮纯 createElement（不碰 innerHTML）
    assert "new Audio(" in s and s.count(".innerHTML") == 1


_IMG_HARNESS = r"""
import { readFileSync } from "node:fs";
const html = readFileSync("__PATH__", "utf8");
const m = html.match(/function _cap[\s\S]*?\nfunction buildAgentPayload[\s\S]*?\n\}/);
if (!m) { console.error("media fns not found"); process.exit(2); }
const MAX_IMAGES = 6, MAX_IMG_CHARS = 8 * 1024 * 1024;
const { capImages, capAudio, buildAgentPayload } =
  eval(m[0] + "\n; ({ capImages, capAudio, buildAgentPayload })");
let bad = 0;
const ok = (n, c, g) => { if (!c) { bad++; console.error("FAIL " + n + " :: " + JSON.stringify(g)); } };

const dataPng = "data:image/png;base64,AAAA";
const dataWav = "data:audio/wav;base64,BBBB";
ok("img keeps data+http", capImages([dataPng, "https://x/y.png"]).length === 2, capImages([dataPng]));
ok("img drops non-image", capImages(["ftp://x", "data:audio/wav;base64,x", 42, null]).length === 0, "x");
ok("img caps count", capImages(Array(20).fill(dataPng)).length === MAX_IMAGES, "cap");
ok("aud keeps data:audio only", capAudio([dataWav, dataPng, "https://x/y.mp3"]).length === 1, capAudio([dataWav]));
ok("aud drops oversize", capAudio(["data:audio/wav;base64," + "A".repeat(MAX_IMG_CHARS + 10)]).length === 0, "big");

let p = buildAgentPayload("hi", "plan", [], []);
ok("no fields when none", p.type === "agent" && !("images" in p) && !("audio" in p), p);
p = buildAgentPayload("看图听音", "build", [dataPng, "bad"], [dataWav]);
ok("both fields when present",
   Array.isArray(p.images) && p.images.length === 1 && p.images[0] === dataPng
   && Array.isArray(p.audio) && p.audio.length === 1 && p.audio[0] === dataWav, p);
process.exit(bad ? 1 : 0);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用，跳过行为测试")
def test_media_helpers_via_node():
    harness = _IMG_HARNESS.replace("__PATH__", str(AGENT_HTML))
    r = subprocess.run(["node", "--input-type=module"], input=harness,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, (r.stdout + r.stderr)


def test_auto_reconnect_wiring():
    s = _src()
    assert "function backoffDelay(" in s and "function scheduleReconnect(" in s
    # 断线即排队重连
    assert "scheduleReconnect();" in s
    # 连上重置退避并清掉 pending 定时器
    assert "reconnectAttempts = 0" in s
    # 断线时清掉半截回合 UI（重连后是全新 server 端 agent 上下文）
    assert 'endToolGroup(); streamBuf = ""; streamEl.textContent = "";' in s
    # 退避封顶 15s
    assert "Math.min(15000" in s


_BACKOFF_HARNESS = r"""
import { readFileSync } from "node:fs";
const html = readFileSync("__PATH__", "utf8");
const m = html.match(/function backoffDelay.*\}/);
if (!m) { console.error("backoffDelay not found"); process.exit(2); }
const backoffDelay = eval("(" + m[0].replace(/^function backoffDelay/, "function") + ")");
let bad = 0;
const ok = (n, c, g) => { if (!c) { bad++; console.error("FAIL " + n + " :: " + JSON.stringify(g)); } };
ok("attempt0", backoffDelay(0) === 800, backoffDelay(0));
ok("attempt1", backoffDelay(1) === 1600, backoffDelay(1));
ok("attempt2", backoffDelay(2) === 3200, backoffDelay(2));
ok("attempt4", backoffDelay(4) === 12800, backoffDelay(4));
ok("capped@5", backoffDelay(5) === 15000, backoffDelay(5));
ok("capped@10", backoffDelay(10) === 15000, backoffDelay(10));
ok("monotonic-then-flat", backoffDelay(3) < backoffDelay(4) && backoffDelay(6) === backoffDelay(20), [backoffDelay(3), backoffDelay(4), backoffDelay(6)]);
process.exit(bad ? 1 : 0);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用，跳过行为测试")
def test_backoff_curve_via_node():
    harness = _BACKOFF_HARNESS.replace("__PATH__", str(AGENT_HTML))
    r = subprocess.run(["node", "--input-type=module"], input=harness,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, (r.stdout + r.stderr)


def test_plan_panel_wiring():
    s = _src()
    assert 'case "agent_plan":' in s and "renderPlan(" in s
    assert "function renderPlan(" in s
    assert 'id="plan"' in s and "#plan {" in s
    # 步骤文本走 textContent（模型可控 → 必须无 XSS），不 innerHTML
    assert "row.textContent =" in s


def test_confirm_wiring():
    s = _src()
    assert 'case "agent_confirm":' in s and "renderConfirm(" in s
    assert "function renderConfirm(" in s
    assert 'type: "agent_confirm_response"' in s          # 点按钮把应答发回
    # 消息文本走 textContent（模型/命令可控 → 无 XSS），有允许/拒绝按钮 + 样式
    assert "msg.textContent = " in s and ".confirm {" in s


def test_persistence_wiring():
    s = _src()
    # 每标签页一个 sid，存 sessionStorage（刷新仍在），连接时带上 ?sid=
    assert "function sessionId(" in s
    assert 'sessionStorage.getItem("vorto_sid")' in s and 'sessionStorage.setItem("vorto_sid"' in s
    assert 'qs.set("sid", sessionId())' in s
    # 回放：处理 agent_history，且仅在 log 为空时渲染（避免就地重连重复）
    assert 'case "agent_history":' in s
    assert "log.children.length === 0" in s and "renderHistory(" in s
    assert "function renderHistory(" in s


def test_interrupt_wiring():
    s = _src()
    # 「停止」：cancelTurn 发 agent_cancel；忙时点发送按钮即取消
    assert "function cancelTurn(" in s
    assert 'type: "agent_cancel"' in s
    assert "if (busy) { cancelTurn(); return; }" in s
    # 发送按钮忙时变「停止」红，闲时「发送」
    assert 'b ? "停止" : "发送"' in s
    assert 'classList.toggle("stop"' in s and "#send.stop" in s
    # 后端 agent_cancelled 事件被处理并解忙
    assert 'case "agent_cancelled":' in s
    # 回车忙时不发送；Esc 在忙时中断
    assert "if (!busy) sendMsg()" in s
    assert 'e.key === "Escape" && busy' in s
