"""日志配置的回归网。

背景（2026-08-01 真机）：全仓**一处日志配置都没有**。根 logger 没 handler，Python 回落到
``logging.lastResort``——只处理 WARNING 及以上。于是全仓 60 处 ``.info()`` 一行都产不出来，
而 ``.env`` 里的 ``LOG_LEVEL`` 没有任何代码读它，纯摆设。

后果不是"少了点日志"，而是**日志里只有失败、没有成功**：排查"点了按钮没反应"时，
加了成功日志照样一片空白，分不清"回调没到"和"到了但没效果"——两者修法完全不同。

两层验法各有原因：
- 端到端那条开**子进程**——pytest 的 logging 插件接管了 logging，在它手里断"有没有真打印
  出来"不可靠（本文件第一版就栽在这：测试红了，功能其实是好的）。
- 级别解析走**纯函数**——``_setup_logging`` 按绝对路径读仓库根的 ``.env``，
  开发机上"未设置 LOG_LEVEL"这个分支根本构造不出来。
"""

import logging
import os
import subprocess
import sys

import pytest

from src.cli import _resolve_log_level, _setup_logging

_PROBE = (
    "import logging, sys;"
    "sys.path.insert(0, {root!r});"
    "from src.cli import _setup_logging;"
    "_setup_logging();"
    "logging.getLogger('vortocode.probe').info('INFO-出来了');"
    "logging.getLogger('vortocode.probe').warning('WARNING-出来了')"
)


def _run_probe(level_env):
    env = {"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8", "LOG_LEVEL": level_env}
    return subprocess.run([sys.executable, "-c", _PROBE.format(root=os.getcwd())],
                          capture_output=True, text=True, env=env, timeout=60).stderr


def test_info_actually_reaches_stderr():
    """LOG_LEVEL=INFO 时 ``.info()`` 必须真的产出——**这正是当初坏掉的那条**。"""
    err = _run_probe("INFO")
    assert "INFO-出来了" in err
    assert "WARNING-出来了" in err


def test_warning_only_when_asked():
    """级别调高时 INFO 就该消失——证明级别是真在起作用，不是"恰好全都打印"。"""
    err = _run_probe("WARNING")
    assert "INFO-出来了" not in err
    assert "WARNING-出来了" in err


@pytest.mark.parametrize("value,expected", [
    ("INFO", logging.INFO), ("debug", logging.DEBUG), ("  Warning  ", logging.WARNING),
    ("ERROR", logging.ERROR), ("CRITICAL", logging.CRITICAL),
    # 没设置 → WARNING：与加这段配置**之前**的实际行为一致（lastResort 也是 WARNING）。
    # 默认刻意不取 INFO——那会让所有没配过的人突然多出一堆输出，属于偷偷改行为。
    (None, logging.WARNING), ("", logging.WARNING), ("   ", logging.WARNING),
    # 写错的一律回落，**不能让进程起不来**：日志级别拼错就炸掉整个服务是荒谬的
    ("verbose", logging.WARNING), ("TRACE", logging.WARNING), ("9", logging.WARNING),
    (123, logging.WARNING),
])
def test_level_resolution(value, expected):
    assert _resolve_log_level(value) == expected


@pytest.fixture
def _clean_logging():
    """basicConfig 只在根 logger 没 handler 时生效；第三方 logger 的级别也会跨用例残留。"""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    noisy = ["aiohttp", "asyncio", "httpx", "httpcore", "openai", "urllib3",
             "websockets", "charset_normalizer"]
    saved_noisy = {n: logging.getLogger(n).level for n in noisy}
    root.handlers.clear()
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for n, lv in saved_noisy.items():
        logging.getLogger(n).setLevel(lv)


def test_noisy_third_parties_are_pinned(monkeypatch, _clean_logging):
    """自己的 INFO 要看得见，但第三方库一到 INFO 就每个 HTTP 请求刷一行，会把自己的淹掉。"""
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    _setup_logging()
    assert logging.getLogger("aiohttp").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING


def test_debug_does_not_gag_third_parties(monkeypatch, _clean_logging):
    """真开 DEBUG 就是想看第三方的细节——那时不该再钉住它们。"""
    for n in ("aiohttp", "httpx"):
        logging.getLogger(n).setLevel(logging.NOTSET)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    _setup_logging()
    assert logging.getLogger("aiohttp").level != logging.WARNING
