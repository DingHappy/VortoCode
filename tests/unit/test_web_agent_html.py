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
