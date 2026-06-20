"""LLM 用量观测（src/llm/client.py 的进程级累计）单元测试。"""

from src.llm.client import _account, add_usage, estimate_tokens, get_usage, reset_usage


def test_estimate_tokens_cjk_vs_ascii():
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") == 4              # CJK ~1 token/字
    assert estimate_tokens("hello world " * 4) == 12      # ASCII ~1 token/4 字符
    assert estimate_tokens("a") == 1                      # 非空至少 1


def test_add_get_reset_usage():
    reset_usage()
    add_usage(100, 50)
    add_usage(10, 5)
    assert get_usage() == {"calls": 2, "prompt_tokens": 110,
                           "completion_tokens": 55, "total_tokens": 165}
    reset_usage()
    assert get_usage() == {"calls": 0, "prompt_tokens": 0,
                           "completion_tokens": 0, "total_tokens": 0}


def test_account_prefers_exact_usage():
    reset_usage()
    _account([{"content": "嗨"}], "回复内容", {"prompt_tokens": 7, "completion_tokens": 3})
    u = get_usage()
    assert u["total_tokens"] == 10 and u["calls"] == 1     # 用了 API 精确值


def test_account_estimates_when_no_usage():
    reset_usage()
    _account([{"content": "你好世界"}], "你好", None)        # 估算：prompt=4, completion=2
    u = get_usage()
    assert u["prompt_tokens"] == 4 and u["completion_tokens"] == 2 and u["calls"] == 1
