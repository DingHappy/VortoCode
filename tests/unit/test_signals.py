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
