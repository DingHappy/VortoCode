"""screenshot_page：给网址截页面图。

真机 2026-07-27 用户问"帮我截一张谷歌首页"，agent 如实答做不到——**这次它没撒谎**：
Playwright 的截图能力在机器上早验通了，却从没接成 agent 工具。这与"天气"那次相反
（那次工具就在手里却否认），区别值得记住：一个是谎报，一个是真缺。

与 browser/verify.py 的运行时验证探针**刻意分开**：那个 loopback-only（只准访问本机 serve），
是给流水线验证用的；混用会把它的网络边界拆掉。
"""
from src.agents.main_agent import build_agent_tools, build_screenshot_tool, make_confirm_gate


def _tool(tmp_path):
    return {t.name: t for t in build_screenshot_tool(str(tmp_path))}["screenshot_page"]


def test_registered_with_web_tier(tmp_path):
    """与联网工具同档：有 web 就有它，无人值守（无真人可问）就没有。"""
    with_web = {t.name for t in build_agent_tools(str(tmp_path), confirm=make_confirm_gate(),
                                                  with_web=True)}
    no_web = {t.name for t in build_agent_tools(str(tmp_path), confirm=make_confirm_gate(),
                                                with_web=False)}
    assert "screenshot_page" in with_web
    assert "screenshot_page" not in no_web


def test_marked_untrusted_source(tmp_path):
    """页面内容（连同图里写的字）是不可信外部输入——摄入必须打污点，否则看图执行可绕过 D0。"""
    t = _tool(tmp_path)
    assert t.untrusted_source is True
    assert t.external_content is True
    assert t.read_only is True          # GET 不改状态，风险靠 SSRF/超时挡，不必逐次确认


async def test_rejects_non_http_scheme(tmp_path):
    out = await _tool(tmp_path).handler({"url": "file:///etc/passwd"})
    assert "只支持 http/https" in out


async def test_rejects_private_address(tmp_path):
    """SSRF：私网/环回一律拒（IP 字面量不查 DNS，直接按段判）。"""
    out = await _tool(tmp_path).handler({"url": "http://127.0.0.1:8080/"})
    assert "拒绝访问" in out


async def test_rejects_link_local_metadata_endpoint(tmp_path):
    """云元数据端点是 SSRF 的经典目标，必须拦住。"""
    out = await _tool(tmp_path).handler({"url": "http://169.254.169.254/latest/meta-data/"})
    assert "拒绝访问" in out


async def test_missing_url_is_reported(tmp_path):
    assert "需要 url" in await _tool(tmp_path).handler({})


async def test_rejection_message_shows_resolved_addresses(tmp_path):
    """被拦时要给出**解析结果**——SSRF 闸分不清"真内网"和"被投毒的解析"，但人能。

    真机：www.google.com 在国内被投毒成 Facebook IP + Teredo 保留段而被拦。
    只说"拒绝私网"会让人以为是自己配置错了，附上解析地址才诊断得动。
    """
    out = await _tool(tmp_path).handler({"url": "http://192.168.10.98:3000/"})
    assert "192.168.10.98" in out
    assert "SSRF" in out
