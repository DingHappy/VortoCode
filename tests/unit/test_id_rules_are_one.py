"""id 的两条边界规则全仓各只有一份——**而且是两条，不能合成一条**。

`.vortocode/` 下大量状态按 id 落成 `<id>.json`，所以 **id 直接进路径**：一个没清洗的 id
就是一次路径穿越。2026-09-11 的体检发现这条规则长出了 **8 处实现、5 种变体**，
和 `resolve_within`（3 份、已漂移）是同一类。

**但清洗型和校验型是两件事**：

    safe_id            坏字符换 _，任何字符串都能变成合法文件名
    typed_id(…, "run") 必须完整匹配 run-[A-Za-z0-9_-]+，否则拒绝

把校验型合进清洗型，等于把"拒绝未知"降级成"清洗一切"——**那是削弱安全，不是消除重复**。
这些测试同时钉住"只有一份"和"那两份不许合并"。
"""
import pytest

from src.utils.ids import safe_id, typed_id


# ------------------------------------------------------------------ 清洗型
@pytest.mark.parametrize("raw", ["../../etc/passwd", "a/b", "x\\y", "a b", ";rm -rf /", "\x00"])
def test_sanitize_strips_everything_that_could_leave_the_directory(raw):
    out = safe_id(raw)
    assert out is None or all(c.isalnum() or c in "_-" for c in out)


@pytest.mark.parametrize("empty", ["", None, "///", "___", "   "])
def test_sanitize_returns_none_not_an_empty_string(empty):
    """**None 而不是空串。** 空串是 falsy 但仍是字符串，拼进路径会得到一个目录而不是文件；
    调用方写的是 `if safe_id(x) is None: 拒绝`。"""
    assert safe_id(empty) is None


def test_sanitize_keeps_a_normal_id_untouched():
    assert safe_id("prun-content-ops-0fe2b177") == "prun-content-ops-0fe2b177"


# ------------------------------------------------------------------ 校验型
@pytest.mark.parametrize("bad", ["run-a/../b", "run-a b", "runx-1", "abc", "", "run-", "RUN-1"])
def test_validate_refuses_instead_of_transforming(bad):
    """**不做任何转换。** `run-x/../y` 不会被洗成 `run-x_.._y` 然后当成一个合法 id 用下去——
    调用方据此知道"这个 id 我不认识"，而不是拿着一个被悄悄改过的 id 往下走。"""
    assert typed_id(bad, "run") == ""


def test_validate_accepts_a_well_formed_id():
    assert typed_id("run-abc_123-x", "run") == "run-abc_123-x"


def test_the_prefix_is_not_a_regex_injection_point():
    """前缀是代码给的，但仍然转义——今天已经见过"配置里的字符串渗进判定"那类坑。"""
    assert typed_id("a.c-1", "a.c") == "a.c-1"
    assert typed_id("abc-1", "a.c") == ""          # `.` 不该被当成通配


def test_the_two_rules_are_not_interchangeable():
    """**这条是本文件的立身之本。** 哪天有人"顺手统一"成一个函数，这条就红。"""
    hostile = "run-a/../b"
    assert typed_id(hostile, "run") == ""           # 校验型：拒绝
    assert safe_id(hostile) == "run-a____b"         # 清洗型：转换后放行
    assert typed_id(hostile, "run") != safe_id(hostile)


# ------------------------------------------------------------------ 各调用点真的走了这一份
@pytest.mark.parametrize("module, attr, kind", [
    ("src.gateway.pipeline", "_clean_id", "safe"),
    ("src.gateway.products", "_clean_id", "safe"),
    ("src.gateway.goals", "_clean_id", "safe"),
    ("src.gateway.tasks", "_clean_id", "safe"),
    ("src.agents.dev_plan", "_clean_id", "safe"),
    ("src.web.session_store", "_clean_sid", "safe"),
])
def test_sanitize_callers_delegate_rather_than_reimplement(monkeypatch, module, attr, kind):
    """把核心那份临时换掉，调用点必须**跟着**改变行为——还留着自己实现的就不会变。

    断行为不断源码：仓库明令禁止用 getsource 查子串来"证明"某个防护存在。
    """
    import importlib

    mod = importlib.import_module(module)
    fn = getattr(mod, attr)
    assert fn("abc") == "abc"
    # 调用点是**模块级 import**（持有直接引用），所以换的是调用方命名空间里那个名字。
    # 还留着自己实现的模块根本没有 `safe_id` 这个名字，setattr 会 AttributeError——同样是红。
    monkeypatch.setattr(mod, "safe_id", lambda v: "被换掉了")
    assert fn("abc") == "被换掉了", f"{module}.{attr} 没有走核心那份"


@pytest.mark.parametrize("module, cls, prefix", [
    ("src.gateway.runs", "RunLedger", "run"),
    ("src.gateway.terminals", None, "term"),
    ("src.gateway.review_threads", None, "review"),
])
def test_validate_callers_delegate(monkeypatch, module, cls, prefix):
    import importlib

    mod = importlib.import_module(module)
    target = None
    for obj in vars(mod).values():
        if isinstance(obj, type) and hasattr(obj, "_clean_id"):
            target = obj._clean_id
            break
    assert target is not None, f"{module} 里找不到 _clean_id"
    assert target(f"{prefix}-ok") == f"{prefix}-ok"
    monkeypatch.setattr(mod, "typed_id", lambda v, p: "被换掉了")
    assert target(f"{prefix}-ok") == "被换掉了", f"{module} 没有走核心那份"
