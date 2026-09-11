"""信号采集：确定性、算增量、去重、一个源挂了不拖垮整轮。

scout 原先靠 web_search 搜"今日科技新闻"，真机产出的"时效证据"全是一年前的事——
搜索返回的是**摘要**不是**事件**，模型没有能力判断"这条是不是当下热的"。
这个模块把"发现"和"解读"拆开：确定性地抓事件，scout 只负责解读。

**不打真网**：所有测试注入假 fetch。
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.gateway.signals import (
    DEDUP_DAYS, Signal, collect, summarize, to_payload,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _src(*signals):
    return lambda _fetch: list(signals)


def _sig(sid, score, source="t"):
    return Signal(id=sid, source=source, title=f"标题 {sid}", score=score)


# ------------------------------------------------------------------ 增量（本模块的立身之本）
def test_first_sighting_has_no_delta(tmp_path):
    r = collect(str(tmp_path), fetch=lambda u: b"", sources={"t": _src(_sig("a", 100))}, now=NOW)
    assert r.signals[0].delta is None


def test_the_second_run_reports_the_increase_not_the_total(tmp_path):
    """**一个 50k star 的仓库不是新闻，这周涨了 2k 的才是。**

    没有 delta 这个模块就退化成另一种"抓一堆东西回来"，那和 web_search 没有本质区别。
    """
    repo = str(tmp_path)
    collect(repo, fetch=lambda u: b"", sources={"t": _src(_sig("a", 100))}, now=NOW)
    later = NOW + timedelta(days=DEDUP_DAYS + 1)          # 越过去重窗口，让它能再次上报
    r = collect(repo, fetch=lambda u: b"", sources={"t": _src(_sig("a", 340))}, now=later)
    assert r.signals[0].delta == 240 and r.signals[0].score == 340


def test_hot_items_come_first_and_new_ones_are_not_buried(tmp_path):
    """有增量的按增量排，**没有增量的按绝对值**——首次见到的条目可能正是今天刚冒出来的那个，
    不该因为 delta 为空就沉底。"""
    repo = str(tmp_path)
    collect(repo, fetch=lambda u: b"", sources={"t": _src(_sig("old", 100))}, now=NOW)
    later = NOW + timedelta(days=DEDUP_DAYS + 1)
    r = collect(repo, fetch=lambda u: b"",
                sources={"t": _src(_sig("old", 150), _sig("fresh", 400))}, now=later)
    assert [s.id for s in r.signals] == ["fresh", "old"]   # 400 > +50


# ------------------------------------------------------------------ 去重
def test_an_item_already_reported_is_not_reported_again(tmp_path):
    """没有"昨天已经报过"的记忆，scout 每天看到同一批东西，选题会反复撞车。"""
    repo = str(tmp_path)
    first = collect(repo, fetch=lambda u: b"", sources={"t": _src(_sig("a", 100))}, now=NOW)
    again = collect(repo, fetch=lambda u: b"", sources={"t": _src(_sig("a", 120))},
                    now=NOW + timedelta(hours=6))
    assert len(first.signals) == 1
    assert again.signals == [] and again.skipped_seen == 1


def test_dedup_expires_so_a_topic_can_come_back(tmp_path):
    """去重是有窗口的——一个话题过了一周重新热起来，那是新事件。"""
    repo = str(tmp_path)
    collect(repo, fetch=lambda u: b"", sources={"t": _src(_sig("a", 100))}, now=NOW)
    r = collect(repo, fetch=lambda u: b"", sources={"t": _src(_sig("a", 900))},
                now=NOW + timedelta(days=DEDUP_DAYS + 1))
    assert len(r.signals) == 1


# ------------------------------------------------------------------ 一个源挂了不拖垮整轮
def test_one_broken_source_does_not_sink_the_others(tmp_path):
    def boom(_f):
        raise RuntimeError("503")

    r = collect(str(tmp_path), fetch=lambda u: b"",
                sources={"good": _src(_sig("a", 10)), "bad": boom}, now=NOW)
    assert [s.id for s in r.signals] == ["a"]
    assert r.failures and "bad" in r.failures[0] and "503" in r.failures[0]


def test_failed_sources_travel_with_the_payload(tmp_path):
    """**scout 得知道自己看到的是全部还是一部分**，否则它会拿半份数据当全景去判断什么在热。"""
    def boom(_f):
        raise RuntimeError("timeout")

    r = collect(str(tmp_path), fetch=lambda u: b"",
                sources={"good": _src(_sig("a", 10)), "bad": boom}, now=NOW)
    payload = to_payload(r)
    assert payload["sources_failed"] and "bad" in payload["sources_failed"][0]
    assert payload["signals"][0]["title"].startswith("标题")


# ------------------------------------------------------------------ 真解析器吃真格式
def test_hacker_news_parsing():
    from src.gateway.signals import _hacker_news

    pages = {
        "https://hacker-news.firebaseio.com/v0/topstories.json": [1, 2],
        "https://hacker-news.firebaseio.com/v0/item/1.json": {
            "type": "story", "title": "某个新东西", "url": "https://x.example",
            "score": 321, "descendants": 88, "time": 1789000000},
        "https://hacker-news.firebaseio.com/v0/item/2.json": {"type": "job", "title": "招聘"},
    }
    out = _hacker_news(lambda u: json.dumps(pages[u]).encode())
    assert len(out) == 1                                   # job 不是 story，跳过
    assert out[0].id == "hn:1" and out[0].score == 321 and out[0].extra["comments"] == 88


def test_github_parsing():
    from src.gateway.signals import _github_new_repos

    body = {"items": [{"full_name": "a/b", "description": "一个新库",
                       "html_url": "https://github.com/a/b", "stargazers_count": 900,
                       "language": "Rust", "created_at": "2026-09-01T00:00:00Z"}]}
    out = _github_new_repos(lambda u: json.dumps(body).encode())
    assert out[0].id == "gh:a/b" and out[0].kind == "release" and "一个新库" in out[0].title


@pytest.mark.parametrize("body, expect", [
    ("""<rss><channel><item><title><![CDATA[带 CDATA 的标题]]></title>
        <link>https://e.example/1</link><pubDate>Wed, 10 Sep 2026</pubDate></item></channel></rss>""",
     "带 CDATA 的标题"),
    ("""<feed><entry><title>Atom 标题</title><link href="https://e.example/2"/>
        <updated>2026-09-10</updated></entry></feed>""", "Atom 标题"),
])
def test_rss_handles_both_rss_and_atom(body, expect):
    """RSS 和 Atom 是两种格式，只认一种的话有一半的源静默产出空列表。"""
    from src.gateway.signals import _rss

    out = _rss(lambda u: body.encode(), "blog", "https://e.example/feed")
    assert out and out[0].title == expect and out[0].url.startswith("https://e.example/")


def test_html_entities_and_tags_are_stripped():
    from src.gateway.signals import _clean

    assert _clean("<b>A &amp; B</b>") == "A & B"


# ------------------------------------------------------------------ 摘要说真话
def test_summary_names_failures_instead_of_hiding_them(tmp_path):
    def boom(_f):
        raise RuntimeError("x")

    r = collect(str(tmp_path), fetch=lambda u: b"",
                sources={"good": _src(_sig("a", 1)), "bad": boom}, now=NOW)
    assert "1 条新信号" in summarize(r) and "1 个源失败" in summarize(r)


def test_state_file_lands_outside_git(tmp_path):
    collect(str(tmp_path), fetch=lambda u: b"", sources={"t": _src(_sig("a", 1))}, now=NOW)
    assert (tmp_path / ".vortocode" / "signals_state.json").is_file()


# ------------------------------------------------------------------ 抖一次要重试
def test_a_transient_failure_is_retried(monkeypatch):
    """真机 2026-09-10：同一轮里 4 个源同时 SSL handshake timed out，十分钟前还都是好的。

    **单次尝试的后果是整个源今天没了**——而 scout 看到的"什么在热"就少了一整块，
    它还不知道自己少看了什么。
    """
    import urllib.error

    from src.gateway import signals as sg

    calls = []

    class _Resp:
        def read(self, n):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def flaky(req, timeout=0):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("handshake timed out")
        return _Resp()

    monkeypatch.setattr(sg.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(sg.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sg, "_host_is_safe", lambda h: True, raising=False)
    from src.agents import web_fetch
    monkeypatch.setattr(web_fetch, "_host_is_safe", lambda h: True)

    assert sg._fetch("https://e.example/x") == b"ok"
    assert len(calls) == 3


def test_an_http_error_is_not_retried(monkeypatch):
    """对方明确答了 404/500 就别重试——重试三次得到的还是同一个答案，纯属浪费一次窗口。"""
    import urllib.error

    from src.agents import web_fetch
    from src.gateway import signals as sg

    calls = []

    def refuse(req, timeout=0):
        calls.append(1)
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(sg.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(sg.time, "sleep", lambda _s: None)
    monkeypatch.setattr(web_fetch, "_host_is_safe", lambda h: True)

    with pytest.raises(urllib.error.HTTPError):
        sg._fetch("https://e.example/x")
    assert len(calls) == 1


def test_giving_up_reports_the_last_transport_error(monkeypatch):
    """重试用尽要把**最后一次的真实死因**抛出去，别换成一个笼统的"失败"。"""
    import urllib.error

    from src.agents import web_fetch
    from src.gateway import signals as sg

    def always(req, timeout=0):
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(sg.urllib.request, "urlopen", always)
    monkeypatch.setattr(sg.time, "sleep", lambda _s: None)
    monkeypatch.setattr(web_fetch, "_host_is_safe", lambda h: True)

    with pytest.raises(urllib.error.URLError, match="connection reset"):
        sg._fetch("https://e.example/x")


# ------------------------------------------------------------------ 时间预算
def test_sources_beyond_the_budget_are_named_not_silently_dropped(tmp_path, monkeypatch):
    """真机 2026-09-10：一轮跑了十几分钟（失败源各重试 3×15s + HN 的 31 次往返）。

    **采集是定时作业，不能无限拖。** 但超预算时必须**点名**说哪些源没跑到——静默少一块，
    scout 会拿残缺的数据当全景去判断"什么在热"。
    """
    from src.gateway import signals as sg

    clock = {"t": 0.0}
    monkeypatch.setattr(sg.time, "monotonic", lambda: clock["t"])

    def slow(_f):
        clock["t"] += 200.0                      # 一个源就吃光预算
        return [_sig("a", 10)]

    r = collect(str(tmp_path), fetch=lambda u: b"",
                sources={"first": slow, "second": _src(_sig("b", 20))},
                now=NOW, budget=180)
    assert [s.id for s in r.signals] == ["a"]
    assert r.failures and "second" in r.failures[0] and "时间预算" in r.failures[0]


def test_a_fast_round_touches_every_source(tmp_path):
    r = collect(str(tmp_path), fetch=lambda u: b"",
                sources={"a": _src(_sig("x", 1)), "b": _src(_sig("y", 2))}, now=NOW, budget=180)
    assert len(r.signals) == 2 and not r.failures


def test_hacker_news_stops_instead_of_eating_the_whole_round(monkeypatch):
    """一个源最多吃掉半个预算——31 次往返卡在第 5 次时，该停下把位置让给别的源。"""
    import json as _json

    from src.gateway import signals as sg

    clock = {"t": 0.0}
    monkeypatch.setattr(sg.time, "monotonic", lambda: clock["t"])

    pages = {"https://hacker-news.firebaseio.com/v0/topstories.json": list(range(1, 16))}

    def fetch(url):
        if url in pages:
            return _json.dumps(pages[url]).encode()
        clock["t"] += 40.0                       # 每条详情都很慢
        hid = url.rsplit("/", 1)[-1].split(".")[0]
        return _json.dumps({"type": "story", "title": f"t{hid}", "score": 1}).encode()

    out = sg._hacker_news(fetch)
    assert 0 < len(out) < 15                     # 拿到多少算多少，没跑完 15 条


# ------------------------------------------------------------------ 跨源不比分数
def test_sources_are_interleaved_not_ranked_against_each_other(tmp_path):
    """**量纲不同的分数不能直接比。** v2ex 的 129 是回帖数、hn 的 18 是投票分——
    129 > 18 纯粹因为数字大，结果闲聊把真信号挤下去。

    这不只影响早报好不好看：scout 拿到的也是这个顺序，payload 触到体积上限时，
    被截掉的就是排在后面的那些。
    """
    chatty = [Signal(id=f"v{i}", source="v2ex", title=f"闲聊{i}", score=130 - i) for i in range(6)]
    real = [Signal(id=f"h{i}", source="hn", title=f"真信号{i}", score=20 - i) for i in range(3)]
    r = collect(str(tmp_path), fetch=lambda u: b"",
                sources={"a": lambda _f: chatty + real}, now=NOW)
    top = [s.source for s in r.signals[:4]]
    assert "hn" in top, "hn 被大数字挤出了前四"


def test_within_a_source_the_hottest_still_comes_first(tmp_path):
    items = [Signal(id="a", source="hn", title="冷", score=5),
             Signal(id="b", source="hn", title="热", score=900)]
    r = collect(str(tmp_path), fetch=lambda u: b"", sources={"s": lambda _f: items}, now=NOW)
    assert [s.title for s in r.signals] == ["热", "冷"]


def test_v2ex_ad_nodes_are_dropped():
    """真机抓到的热门里，"推广"节点那条是「注册就送 $11」的广告。"""
    import json as _json

    from src.gateway.signals import _v2ex_hot

    body = [
        {"id": 1, "title": "一条广告", "replies": 99, "node": {"title": "推广"}},
        {"id": 2, "title": "一条正常帖", "replies": 5, "node": {"title": "程序员"}},
    ]
    out = _v2ex_hot(lambda u: _json.dumps(body).encode())
    assert [s.title for s in out] == ["一条正常帖"]
