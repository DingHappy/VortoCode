"""SSRF 校验遇上代理的 fake-IP：放行占位符，但四道防线一条不松。

背景（2026-09-09 真机）：Clash/mihomo 的 TUN/fake-ip 模式下本地 DNS 把**所有**域名解析到
198.18.0.0/15，而 Python 认为该段 `is_private=True`——于是 SSRF 校验把每个外网域名都判成
内网，web_search/web_fetch 全废，报错还说"域名解析异常"（解析明明好好的）。
同一进程同一代理实测：`_host_is_safe` 拒绝，直接发请求却是 HTTP 202 / 0.77s。

**闸门按一个不会被使用的 IP 做判断，然后拒绝了自己。** 这些测试钉的就是修完之后
"该放的放、该拦的一个不少"。
"""
import ipaddress

import pytest

from src.agents import web_fetch

FAKE = "198.18.0.10"          # Clash/mihomo 默认 fake-ip-range
PUBLIC_LOOKALIKE = "28.0.0.7"  # 真实可路由的公网地址（第一版补丁误当成 fake-IP 段收了进去）
REAL_INTERNAL = "10.0.0.5"
PUBLIC = "93.184.216.34"


def _resolves_to(monkeypatch, *ips):
    monkeypatch.setattr(web_fetch.socket, "getaddrinfo",
                        lambda host, port, *a, **k: [(2, 1, 6, "", (ip, 0)) for ip in ips])


def _proxy(monkeypatch, on: bool):
    monkeypatch.setattr(web_fetch, "_proxy_in_effect", lambda host: on)


# ------------------------------------------------------------------ 该放的放
def test_fake_ip_with_proxy_is_allowed(monkeypatch):
    """fake-IP 不是目的地，是给代理做路由的号码牌——这次解析等同于"没给出真实地址"。"""
    _resolves_to(monkeypatch, FAKE)
    _proxy(monkeypatch, True)
    assert web_fetch._host_is_safe("html.duckduckgo.com") is True


def test_real_public_ip_still_allowed(monkeypatch):
    _resolves_to(monkeypatch, PUBLIC)
    _proxy(monkeypatch, False)
    assert web_fetch._host_is_safe("example.com") is True


# ------------------------------------------------------------------ 该拦的一个不少
def test_fake_ip_without_proxy_is_refused(monkeypatch):
    """没配代理却解析到 fake-IP 段：那才是真该拦的异常，别顺手一起放了。"""
    _resolves_to(monkeypatch, FAKE)
    _proxy(monkeypatch, False)
    assert web_fetch._host_is_safe("html.duckduckgo.com") is False


def test_real_internal_ip_is_refused_even_behind_a_proxy(monkeypatch):
    """内网主机名照拦——代理放行只针对"解析结果是占位符"，不是"有代理就什么都放"。"""
    _resolves_to(monkeypatch, REAL_INTERNAL)
    _proxy(monkeypatch, True)
    assert web_fetch._host_is_safe("internal.corp") is False


def test_one_fake_ip_cannot_launder_a_real_internal_one(monkeypatch):
    """**all 而不是 any**：混一个 fake-IP 进来不能把真实内网地址的判定绕过去。

    用 any 的话，攻击者只要让域名同时解析出一个 fake-IP 和一个 10.x，就能读内网——
    那才是真开后门。
    """
    _resolves_to(monkeypatch, FAKE, REAL_INTERNAL)
    _proxy(monkeypatch, True)
    assert web_fetch._host_is_safe("mixed.example") is False


@pytest.mark.parametrize("literal", ["127.0.0.1", "192.168.1.1", "10.0.0.1", "169.254.169.254",
                                     "198.18.0.10"])
def test_ip_literals_are_never_affected(monkeypatch, literal):
    """IP 字面量压根不走 DNS 分支——包括 fake-IP 段本身，直接写它也不给读。"""
    _proxy(monkeypatch, True)
    assert web_fetch._host_is_safe(literal) is False


def test_unresolvable_host_keeps_its_old_behaviour(monkeypatch):
    def boom(*a, **k):
        raise OSError("no dns")

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", boom)
    _proxy(monkeypatch, True)
    assert web_fetch._host_is_safe("only-via-proxy.example") is True
    _proxy(monkeypatch, False)
    assert web_fetch._host_is_safe("only-via-proxy.example") is False


def test_fake_ip_predicate_covers_the_declared_ranges():
    for net in web_fetch._FAKE_IP_NETS:
        assert web_fetch._is_fake_ip(ipaddress.ip_address(net[1]))
    assert not web_fetch._is_fake_ip(ipaddress.ip_address(PUBLIC))
    assert not web_fetch._is_fake_ip(ipaddress.ip_address(REAL_INTERNAL))


@pytest.mark.parametrize("proxy_on", [True, False])
def test_the_exemption_can_only_relax_never_tighten(monkeypatch, proxy_on):
    """**改之前放行的，改之后一定还放行。**

    第一版补丁把一个真实可路由的公网段（28.0.0.0/8）当成 fake-IP 收进了清单，判据又漏了
    "本来就该拦"这个前提——结果是"正常解析到该段的网站在没配代理时反而被拦"，
    一条本该只放松的豁免被写成了双向，凭空造出新拦截。
    现在判据是 `_blocked(a) and _is_fake_ip(a)`：任何一个正常公网地址都让 all() 不成立，
    直接落回原来的放行路径。
    """
    _resolves_to(monkeypatch, PUBLIC_LOOKALIKE)
    _proxy(monkeypatch, proxy_on)
    assert web_fetch._host_is_safe("normal-site.example") is True

    # 清单里只该有"本来就会被拦的保留段"——收了公网段就会重演上面那个回归
    for net in web_fetch._FAKE_IP_NETS:
        assert web_fetch._blocked(ipaddress.ip_address(net[1])), f"{net} 不是保留段，不该进清单"


# ------------------------------------------------------------------ 报错要说真话
def test_refusal_reason_tells_the_actual_cause(monkeypatch):
    """原来所有拒绝都统一说"域名解析异常"，把排查往"是不是网断了"上带（真机被带偏过一次）。

    判定一个字不改，只让人看得懂——与 taint.py"来源分档、判定不分档"同一手法。
    """
    _proxy(monkeypatch, False)
    assert "IP 字面量" in web_fetch.refusal_reason("192.168.1.1")

    _resolves_to(monkeypatch, REAL_INTERNAL)
    assert "内网/保留地址" in web_fetch.refusal_reason("internal.corp")

    def boom(*a, **k):
        raise OSError("no dns")

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", boom)
    assert "未配置代理" in web_fetch.refusal_reason("nowhere.example")


def test_refusal_reason_is_empty_when_allowed(monkeypatch):
    _resolves_to(monkeypatch, PUBLIC)
    _proxy(monkeypatch, False)
    assert web_fetch.refusal_reason("example.com") == ""


def test_search_surfaces_the_real_reason(monkeypatch):
    """web_search 复用同一份说明，别自己再编一句笼统的。"""
    from src.agents import web_search

    monkeypatch.setattr(web_fetch, "refusal_reason", lambda host: f"{host} 就是不让")
    raw, err = web_search._fetch_html("https://html.duckduckgo.com/html/?q=x")
    assert raw is None and "就是不让" in err
