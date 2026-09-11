"""路径围栏全仓**只有一份**，而且三个入口判定一致。

2026-09-11 的依赖图体检发现它写了**三份，且三份不等价**：

    web/auth.resolve_within           空串 → 返回 base 本身；**不捕 OSError**
    agents/tools/files._resolve_within  空串 → None；额外 lstrip("@")；捕 OSError
    memory/rewind._resolve_within       空串 → 返回 base；捕 OSError

而 rewind 那份的注释写着"契约与 src/web/auth.resolve_within 相同"——**那句话是假的**。
两份各自演化，谁也不知道自己和另一份已经不一样了。

CLAUDE.md：「安全规则**上收到内核一处**，别在每个端各写一遍（那正是「加一端漏一端」的
历史病根）」。这些测试钉住收口之后的状态。
"""
import pytest

from src.agents.tools.files import _resolve_within as agents_fence
from src.memory.rewind import _resolve_within as memory_fence
from src.utils.paths import resolve_within as core
from src.web.auth import resolve_within as web_fence

FENCES = {"core": core, "web": web_fence, "memory": memory_fence, "agents": agents_fence}


@pytest.mark.parametrize("name, fence", FENCES.items())
@pytest.mark.parametrize("rel", ["", "   ", "../x", "../../etc/passwd", "/etc/passwd",
                                 "a/../../b", "\t"])
def test_every_entrance_refuses_the_same_escapes(tmp_path, name, fence, rel):
    """**所有入口对越界的判定必须一致。** 不一致的那个就是下一个被绕过的地方。"""
    assert fence(str(tmp_path), rel) is None, f"{name} 放行了 {rel!r}"


@pytest.mark.parametrize("name, fence", FENCES.items())
def test_every_entrance_allows_a_normal_path(tmp_path, name, fence):
    got = fence(str(tmp_path), "sub/a.txt")
    assert got is not None and str(tmp_path) in str(got)


@pytest.mark.parametrize("name, fence", FENCES.items())
def test_no_entrance_raises_on_a_broken_path(tmp_path, name, fence):
    """**安全判定函数抛异常是 fail-open 的形状。** 软链环（ELOOP）、超长路径都会让
    Path.resolve() 抛 OSError——web 那份原先不捕，异常直接穿出围栏。"""
    monster = "x/" * 2000 + "\x00bad"
    try:
        assert fence(str(tmp_path), monster) is None
    except (ValueError, OSError) as e:      # noqa: PT011 —— 就是要断它不该抛
        pytest.fail(f"{name} 抛了 {type(e).__name__} 而不是拒绝")


def test_a_prefix_lookalike_directory_is_not_inside(tmp_path):
    """组件级判断，不是字符串 startswith——`/x/proj-secrets` 不在 `/x/proj` 里。"""
    base = tmp_path / "proj"
    base.mkdir()
    (tmp_path / "proj-secrets").mkdir()
    assert core(str(base), "../proj-secrets/k.pem") is None


def test_the_at_prefix_is_an_agents_convention_not_part_of_the_fence():
    """`@src/x.py` 是 agents 层的书写约定。塞进围栏就等于让 web/memory 也悄悄接受它——
    围栏只管边界，不管各层怎么写路径。"""
    import inspect

    from src.utils import paths

    assert "@" not in inspect.getsource(paths.resolve_within)


def test_the_three_entrances_delegate_rather_than_reimplement(tmp_path):
    """断的是**行为等价**（同一批输入给同一批判定），不是"源码里有没有 import"——
    仓库明令禁止拿 getsource 查子串来证明防护存在。

    这里换一个真实现的探针：把核心那份临时换掉，三个入口必须**一起**改变行为。
    若某个入口还留着自己的实现，它不会跟着变——这条就红。
    """
    import src.utils.paths as core_mod

    original = core_mod.resolve_within
    try:
        core_mod.resolve_within = lambda base, rel: None      # 核心一律拒绝
        for name, fence in FENCES.items():
            if name == "core":
                continue
            assert fence(str(tmp_path), "a.txt") is None, f"{name} 没有走核心那份"
    finally:
        core_mod.resolve_within = original
