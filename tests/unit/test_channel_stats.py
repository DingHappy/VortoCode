"""渠道数据查询：**查不到要说"查不到"，绝不能报 0。**

0 次阅读和查不到是两件完全不同的事。把查不到当成 0 喂给 measure，它会得出"这个选题失败了"，
而那个结论经 metrics 回流给下一轮 scout——假数据进了闭环会自我强化。

各渠道的真实情况是 2026-09-10 实测的，不是查文档：B站/掘金公开接口可查；微信公众号的
阅读数不在页面里（read_num 字段没有、连"阅读"两个字都没有），知乎 403。

**不打真网**：所有测试注入假 fetch。
"""
import json

import pytest

from src.gateway.channel_stats import (
    ChannelStat, collect_stats, fetch_one, load_urls, register_url, summarize, to_payload,
)

BV = "https://www.bilibili.com/video/BV1GJ411x7h7"
WX = "https://mp.weixin.qq.com/s/abcdef"
ZH = "https://zhuanlan.zhihu.com/p/123"


def _bili_ok(url):
    return json.dumps({"code": 0, "data": {"title": "某个视频", "stat": {
        "view": 1234, "like": 56, "reply": 7, "favorite": 8, "coin": 9, "share": 10}}}).encode()


# ------------------------------------------------------------------ 查得到的
def test_bilibili_returns_real_numbers():
    s = fetch_one(BV, _bili_ok)
    assert s.supported and s.channel == "bilibili" and s.title == "某个视频"
    assert s.metrics["view"] == 1234 and s.metrics["like"] == 56


def test_an_api_error_is_reported_not_turned_into_zeros():
    """接口报错时**不能退化成一堆 0**——那会被读成"表现极差"。"""
    def err(url):
        return json.dumps({"code": -404, "message": "啥都木有"}).encode()

    s = fetch_one(BV, err)
    assert s.supported is False and "-404" in s.reason and not s.metrics


# ------------------------------------------------------------------ 查不到的（重点）
@pytest.mark.parametrize("url, must_say", [
    (WX, "微信客户端"),
    (ZH, "403"),
    ("https://www.xiaohongshu.com/explore/x", "没有公开数据接口"),
    ("https://juejin.cn/post/7202990517167030329", "登录态"),
])
def test_channels_without_public_data_say_why(url, must_say):
    """**"不支持"和"还没做"在排查时是两回事。** 写清为什么，而不是一句"不支持"。"""
    s = fetch_one(url, _bili_ok)
    assert s.supported is False and must_say in s.reason
    assert s.metrics == {}                       # 一个数字都不许编


def test_an_unknown_site_still_produces_a_record():
    """**任何情况下都要留一条记录**——查不到的从结果里消失，人就以为它压根没发布过。"""
    s = fetch_one("https://某个没见过的站.example/a", _bili_ok)
    assert s.supported is False and "没有对应的查询适配器" in s.reason


def test_a_transport_failure_names_the_cause():
    def boom(url):
        raise RuntimeError("connection reset")

    s = fetch_one(BV, boom)
    assert s.supported is False and "connection reset" in s.reason


# ------------------------------------------------------------------ 台账
def test_registering_a_url_keeps_only_the_latest_entry(tmp_path):
    repo = str(tmp_path)
    assert register_url(repo, BV, run_id="prun-a")
    assert register_url(repo, BV, run_id="prun-b")
    items = load_urls(repo)
    assert len(items) == 1 and items[0]["run_id"] == "prun-b"


@pytest.mark.parametrize("bad", ["", "不是链接", "javascript:alert(1)", "ftp://x/y"])
def test_a_non_http_url_is_refused(tmp_path, bad):
    assert register_url(str(tmp_path), bad) is False
    assert load_urls(str(tmp_path)) == []


def test_registry_lands_outside_git(tmp_path):
    register_url(str(tmp_path), BV)
    assert (tmp_path / ".vortocode" / "published_urls.json").is_file()


# ------------------------------------------------------------------ 整轮
def test_collect_covers_every_registered_url(tmp_path):
    repo = str(tmp_path)
    register_url(repo, BV, run_id="prun-a")
    register_url(repo, WX, run_id="prun-a")
    stats = collect_stats(repo, fetch=_bili_ok)
    assert len(stats) == 2
    assert {s.supported for s in stats} == {True, False}
    assert all(s.run_id == "prun-a" and s.at for s in stats)


def test_the_payload_warns_measure_not_to_read_unqueryable_as_bad(tmp_path):
    """**这条是本模块的立身之本。** measure 读到 unqueryable 时必须知道那不是"表现差"。"""
    repo = str(tmp_path)
    register_url(repo, WX)
    payload = to_payload(collect_stats(repo, fetch=_bili_ok))
    assert payload["queried"] == 0
    assert payload["unqueryable"] and WX in payload["unqueryable"][0]["url"]
    assert "两件事" in payload["caveat"] and "0 次阅读" in payload["caveat"]


def test_over_budget_urls_are_named_not_dropped(tmp_path, monkeypatch):
    from src.gateway import channel_stats as cs

    clock = {"t": 0.0}
    monkeypatch.setattr(cs.time, "monotonic", lambda: clock["t"])

    def slow(url):
        clock["t"] += 200.0
        return _bili_ok(url)

    repo = str(tmp_path)
    register_url(repo, BV)
    register_url(repo, "https://www.bilibili.com/video/BV1aaaaaaaaaa")
    stats = collect_stats(repo, fetch=slow, budget=90)
    assert len(stats) == 2                       # 两条都留了记录
    assert any("时间预算" in s.reason for s in stats)


def test_summary_says_how_many_could_not_be_queried(tmp_path):
    repo = str(tmp_path)
    register_url(repo, BV)
    register_url(repo, WX)
    assert "查到 1 条" in summarize(collect_stats(repo, fetch=_bili_ok))
    assert "1 条查不到" in summarize(collect_stats(repo, fetch=_bili_ok))


def test_an_empty_registry_says_how_to_add_one(tmp_path):
    assert "/url" in summarize([])


def test_stat_defaults_never_invent_numbers():
    assert ChannelStat(url="u", channel="c", supported=False).metrics == {}


def test_only_verified_adapters_ship():
    """**验不过就不发。** 今天栽过一次：信号采集里写了个 anthropic RSS 地址，14 条单测全绿
    而那地址是 404——单测喂的是自己造的格式，验不了"这个接口真的能用"。

    这条钉住"适配器清单里的每一个都是真机验过的"：新增一个就要同时在这里登记，
    而登记这个动作本身提醒你先去真机跑一遍。
    """
    from src.gateway.channel_stats import ADAPTERS

    verified = {"_bilibili"}
    assert {a.__name__ for a in ADAPTERS} == verified


@pytest.mark.parametrize("url", [
    "https://zhuanlan.zhihu.com/p/1",
    "https://www.zhihu.com/question/1",
    "https://www.xiaohongshu.com/explore/x",
    "https://xiaohongshu.com/explore/x",
])
def test_subdomains_match_the_parent_entry(url):
    """`zhuanlan.zhihu.com` 要能命中 `zhihu.com`。每加一个子域补一条的话，漏掉的那个会得到
    一句"没有对应的查询适配器"——听起来像**还没做**，其实是**根本查不到**。"""
    s = fetch_one(url, _bili_ok)
    assert s.supported is False and "没有对应的查询适配器" not in s.reason
