"""artifacts 路由：把会话产出的制品渲染成可分享、实时更新的网页。

- GET /artifacts                 制品画廊（自包含页，前端拉 /api/artifacts）
- GET /api/artifacts             制品列表(JSON)
- GET /api/artifacts/{id}        单个制品 meta(JSON，查看页轮询版本号用)
- GET /artifact/{id}             查看页（头部信息条 + 沙箱 iframe 载入 raw + 轮询自动刷新）
- GET /artifact/{id}/raw         制品原始 HTML（限制性 CSP，禁止外联）

复刻 Claude Code「静态、无外部请求」的隔离：raw 带 CSP（default-src none，仅放行内联与
data:），查看页用 iframe sandbox（仅 allow-scripts，不给 same-origin）。两者叠加 → 制品脚本
能跑、但拿不到父页 cookie、也无法联网（挡外联/SSRF）。鉴权沿用全局中间件（设了 token 时按
httpOnly Cookie 「认证可见」——同源子资源自动带 Cookie，token 不进 URL；本地无 token 直接可看）。
"""
import html as _html
import os

from fastapi import Request

from src.web.deps import *  # noqa: F401,F403
from src.web.artifacts import ArtifactStore

router = APIRouter()

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_WEB_DIR = _PROJECT_ROOT / "web"

# 禁止外联：默认 none，仅放行内联样式/脚本与 data: 图片/字体；禁表单提交与 base 改写。
# frame-ancestors 'self'：只允许自家查看页（同源）嵌 raw，禁外站 iframe（防点击劫持/外部嵌套）。
_ARTIFACT_CSP = (
    "default-src 'none'; img-src data: blob:; media-src data: blob:; "
    "style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
)
# 查看页外壳自身不该被任何页面嵌（防被外站框住做 UI redress）。
_VIEW_CSP = "frame-ancestors 'none'"


def _store() -> ArtifactStore:
    return ArtifactStore(os.getcwd())


def _abs_url(request: Request, artifact_id: str) -> str:
    return str(request.base_url).rstrip("/") + f"/artifact/{artifact_id}"


@router.get("/artifacts")
async def artifacts_gallery():
    """制品画廊页。"""
    p = _WEB_DIR / "artifacts.html"
    if p.exists():
        return FileResponse(p, media_type="text/html")
    return {"message": "Artifacts gallery not found"}


@router.get("/api/artifacts")
async def artifacts_list(request: Request):
    """制品列表(JSON)。"""
    items = _store().list()
    for m in items:
        m["url"] = _abs_url(request, m["id"])
    return {"artifacts": items}


@router.get("/api/artifacts/{artifact_id}")
async def artifact_meta(artifact_id: str, request: Request):
    """单个制品 meta（查看页轮询版本号/pin 用）。"""
    m = _store().meta(artifact_id)
    if not m:
        raise HTTPException(status_code=404, detail="制品不存在")
    m["url"] = _abs_url(request, m["id"])
    return m


@router.get("/api/artifacts/{artifact_id}/versions")
async def artifact_versions(artifact_id: str):
    """版本清单 + 当前/被 pin 的版本（查看页的版本选择器用）。"""
    store = _store()
    m = store.meta(artifact_id)
    if not m:
        raise HTTPException(status_code=404, detail="制品不存在")
    return {"id": m["id"], "current": m["version"], "pinned": m.get("pinned"),
            "versions": store.versions(artifact_id)}


@router.post("/api/artifacts/{artifact_id}/pin")
async def artifact_pin(artifact_id: str, version: int | None = None):
    """把"当前"指向某历史版本（CC：选给查看者看哪一版）；不带 version 则取消 pin 回到最新。"""
    store = _store()
    if not store.meta(artifact_id):
        raise HTTPException(status_code=404, detail="制品不存在")
    m = store.pin(artifact_id, version)
    if not m:
        raise HTTPException(status_code=400, detail="版本不存在")
    return {"id": m["id"], "pinned": m.get("pinned"), "current": m["version"]}


@router.delete("/api/artifacts/{artifact_id}")
async def artifact_delete(artifact_id: str):
    """删除一个制品。"""
    if not _store().delete(artifact_id):
        raise HTTPException(status_code=404, detail="制品不存在")
    return {"deleted": artifact_id}


@router.get("/artifact/{artifact_id}/raw")
async def artifact_raw(artifact_id: str, v: int | None = None):
    """制品原始 HTML（限制性 CSP，禁止外联）。?v=N 取某历史版本。"""
    content = _store().html(artifact_id, v)
    if content is None:
        raise HTTPException(status_code=404, detail="制品不存在")
    return HTMLResponse(
        content,
        headers={"Content-Security-Policy": _ARTIFACT_CSP, "X-Content-Type-Options": "nosniff"},
    )


@router.get("/artifact/{artifact_id}")
async def artifact_view(artifact_id: str):
    """查看页：头部信息条 + 沙箱 iframe + 版本轮询自动刷新。"""
    m = _store().meta(artifact_id)
    if not m:
        raise HTTPException(status_code=404, detail="制品不存在")
    page = (
        _VIEW_TEMPLATE
        .replace("__TITLE__", _html.escape(m["title"]))
        .replace("__AID__", _html.escape(m["id"]))
        .replace("__VERSION__", str(int(m["version"])))
    )
    return HTMLResponse(page, headers={"Content-Security-Policy": _VIEW_CSP})


# 查看页外壳（用 .replace 注入，避开 .format 的花括号转义）。制品本体在沙箱 iframe 内。
_VIEW_TEMPLATE = """<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · VortoCode 制品</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
         background: #0f1115; color: #d7dae0; display: flex; flex-direction: column; }
  header { padding: 8px 14px; border-bottom: 1px solid #2a2e37; display: flex; gap: 12px;
           align-items: center; font-size: 13px; background: #14161b; }
  header b { color: #fff; }
  #title { color: #e8eaed; }
  .sp { flex: 1; }
  #v { color: #7f8896; }
  .badge { display: inline-flex; align-items: center; gap: 6px; color: #7fce9a; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #7fce9a; animation: pulse 1.6s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }
  a.btn, button.btn { color: #8ab4f8; background: #1b1f27; border: 1px solid #3a4150;
        border-radius: 6px; padding: 4px 10px; cursor: pointer; text-decoration: none; font: inherit; }
  select { background: #1b1f27; color: #d7dae0; border: 1px solid #3a4150; border-radius: 6px;
           padding: 3px 6px; font: inherit; }
  label { color: #7f8896; }
  iframe { flex: 1; width: 100%; border: 0; background: #fff; }
</style></head>
<body>
  <header>
    <b>VortoCode</b> · <span id="title">__TITLE__</span>
    <label>版本 <select id="vsel"></select></label>
    <button class="btn" id="pin">设为默认</button>
    <span class="sp"></span>
    <span class="badge"><span class="dot"></span>live</span>
    <a class="btn" id="raw" target="_blank" rel="noopener">新窗口打开</a>
    <button class="btn" id="copy">复制链接</button>
    <a class="btn" href="/artifacts">全部制品</a>
  </header>
  <iframe id="frame" sandbox="allow-scripts" title="__TITLE__"></iframe>
<script>
  var AID = "__AID__", curVer = __VERSION__, pinned = null;
  // token 不再进 URL（审计 P0#4）：同源请求（含此沙箱 iframe 的子资源）自动带 httpOnly Cookie 鉴权，
  // allow-scripts 脚本既读不到 httpOnly Cookie、也无从 location 里捞 token。
  var sel = document.getElementById("vsel"), frame = document.getElementById("frame");
  var pinBtn = document.getElementById("pin");

  function show(v) {   // 在 iframe 里载入指定版本（沙箱 + CSP 不变；Cookie 同源自动带）
    var u = "/artifact/" + encodeURIComponent(AID) + "/raw?v=" + v;
    frame.src = u; document.getElementById("raw").href = u;
  }
  function syncPinBtn() {
    pinBtn.textContent = (parseInt(sel.value, 10) === pinned) ? "已是默认" : "设为默认";
  }
  async function loadVersions(initial) {
    try {
      var r = await fetch("/api/artifacts/" + encodeURIComponent(AID) + "/versions",
                          { cache: "no-store" });
      if (!r.ok) return;
      var d = await r.json(); curVer = d.current; pinned = d.pinned;
      var keep = sel.value;
      sel.innerHTML = "";
      d.versions.slice().reverse().forEach(function (it) {
        var o = document.createElement("option"); o.value = it.v;
        o.textContent = "v" + it.v + (it.v === d.current ? " · 当前" : "")
                        + (it.v === d.pinned ? " · 📌默认" : "");
        sel.appendChild(o);
      });
      sel.value = initial ? (pinned || curVer) : (keep || pinned || curVer);
      syncPinBtn();
      if (initial) show(sel.value);
    } catch (e) {}
  }
  sel.onchange = function () { show(sel.value); syncPinBtn(); };
  pinBtn.onclick = async function () {
    try {
      var r = await fetch("/api/artifacts/" + encodeURIComponent(AID) + "/pin?version="
                          + sel.value, { method: "POST" });
      if (r.ok) await loadVersions(false);
    } catch (e) {}
  };
  document.getElementById("copy").onclick = function () {
    try { navigator.clipboard.writeText(location.href); } catch (e) {}
    var b = this; b.textContent = "已复制"; setTimeout(function () { b.textContent = "复制链接"; }, 1200);
  };
  // 轮询：出现更高版本就刷新版本列表；若用户正看着"当前"，顺带载入新版本（实时更新不变）。
  setInterval(async function () {
    try {
      var r = await fetch("/api/artifacts/" + encodeURIComponent(AID), { cache: "no-store" });
      if (!r.ok) return;
      var m = await r.json();
      if (m.version !== curVer || m.pinned !== pinned) {
        var atLatest = (parseInt(sel.value, 10) === curVer);
        await loadVersions(false);
        if (atLatest) { sel.value = curVer; show(sel.value); syncPinBtn(); }
      }
    } catch (e) {}
  }, 2000);
  loadVersions(true);
</script>
</body></html>"""
