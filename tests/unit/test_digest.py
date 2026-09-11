"""早报：把 signals 渲染成给人读的一屏。**零 LLM。**

原来这活是个 `prompt:` 作业：让模型 `web_search` 搜"今日科技新闻"这类泛关键词再挑 5 条。
那正是 scout 今天治好的毛病——搜索返回的是**摘要**不是**事件**，模型分不清哪条是真的新。

signals 里已经带着分数、增量、标题、链接，**按分数挑是确定性的**：比让模型转述一遍更准、
零 token、而且编不了。LLM 该留给真正需要判断的地方。
"""

from src.gateway.collect_cli import render_digest
from src.gateway.products import Product


def _product(signals, failed=None):
    return Product(id="p", kind="signals",
                   payload={"signals": signals, "sources_failed": failed or []})


def _sig(**kw):
    base = {"source": "hn", "title": "标题", "url": "https://e.example/1", "score": 10,
            "delta": None}
    base.update(kw)
    return base


def test_it_lists_the_actual_items_with_links():
    out = render_digest(_product([_sig(title="某个新东西", url="https://x.example/a")]))
    assert "某个新东西" in out and "https://x.example/a" in out


def test_growth_and_absolute_score_look_different():
    """**+320 和 320 在"值不值得看"上是两回事**——一个是这周涨的，一个是历史总量。"""
    out = render_digest(_product([
        _sig(title="涨得快的", score=500, delta=320),
        _sig(title="总量高的", score=900, delta=None),
    ]))
    assert "+320 涨得快的" in out
    assert "900 总量高的" in out and "+900" not in out


def test_hot_items_come_first_within_a_source():
    out = render_digest(_product([
        _sig(title="冷的", score=5), _sig(title="热的", score=900),
    ]))
    assert out.index("热的") < out.index("冷的")


def test_sources_are_grouped():
    out = render_digest(_product([
        _sig(source="hn", title="A"), _sig(source="github", title="B"),
    ]))
    assert "【hn】" in out and "【github】" in out


def test_it_respects_the_length_budget():
    """一屏读不完就没人读。"""
    out = render_digest(_product([_sig(title=f"第{i}条", score=i) for i in range(60)]), limit=6)
    assert sum(1 for ln in out.splitlines() if ln.startswith("· ")) <= 6


def test_failed_sources_are_named(): 
    """**少了一块而不说，人会以为今天就这么点事。**"""
    out = render_digest(_product([_sig()], failed=["v2ex：超时", "github：503"]))
    assert "2 个源没取到" in out and "v2ex" in out


def test_an_empty_signal_set_says_so_plainly():
    assert "没有新信号" in render_digest(_product([]))


def test_empty_but_with_failures_says_both():
    """一条都没有 + 有源失败 → 那多半是取数失败，不是今天没新闻。两者要能分辨。"""
    out = render_digest(_product([], failed=["hn：超时"]))
    assert "没有新信号" in out and "1 个源失败" in out


# ------------------------------------------------------------------ CLI
def test_cli_refuses_when_collection_never_ran(tmp_path, monkeypatch, capsys):
    """退出码 1 = 还没采过。cron 据此知道该不该报红——而不是推一份空早报。"""
    from src.gateway.collect_cli import run_digest_cli

    monkeypatch.chdir(tmp_path)
    assert run_digest_cli() == 1
    assert "vc collect" in capsys.readouterr().err


def test_cli_prints_the_digest(tmp_path, monkeypatch, capsys):
    from src.gateway.collect_cli import run_digest_cli
    from src.gateway.products import ProductStore

    monkeypatch.chdir(tmp_path)
    ProductStore(str(tmp_path)).create("signals", payload={
        "signals": [_sig(title="真的有这条")], "sources_failed": []})
    assert run_digest_cli() == 0
    assert "真的有这条" in capsys.readouterr().out


# ------------------------------------------------------------------ 跨源不比分数
def test_each_source_gets_a_fair_share_not_a_score_contest():
    """**这条钉住真机当场暴露的那个 bug。**

        【v2ex】129 如何充值 chatgpt？救救孩子吧     ← 129 是回帖数
        【hackernews】18 Thelio Mira AI Workstation  ← 18 是投票分

    129 > 18 纯粹因为数字大、量纲不同，结果 v2ex 的闲聊吃掉了 10 个位置里的 5 个。
    源内比热度有意义（同一把尺子），跨源只能均分。
    """
    rows = ([_sig(source="v2ex", title=f"闲聊{i}", score=130 - i) for i in range(8)]
            + [_sig(source="hn", title=f"真信号{i}", score=20 - i) for i in range(3)])
    out = render_digest(_product(rows), limit=8)
    assert sum(1 for ln in out.splitlines() if "真信号" in ln) >= 3, "hn 被大数字挤没了"
    assert sum(1 for ln in out.splitlines() if "闲聊" in ln) <= 5


def test_a_single_source_still_fills_the_page():
    """只有一个源时不该因为"均分"而只显示一条。"""
    out = render_digest(_product([_sig(title=f"第{i}条", score=i) for i in range(10)]), limit=6)
    assert sum(1 for ln in out.splitlines() if ln.startswith("· ")) >= 5
