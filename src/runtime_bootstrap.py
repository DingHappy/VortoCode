"""Shared process preparation for internal services and the temporary public CLI wrapper."""

from pathlib import Path
from src.env_compat import env_compat


def resolve_log_level(raw) -> int:
    """把 ``LOG_LEVEL`` 的原始值解析成 logging 级别；**认不出一律 WARNING**。

    抽成纯函数是为了能确定性地测"没设置""拼错了"这些分支——``setup_logging`` 会去读
    仓库根的 ``.env``，在开发机上"未设置"这个分支根本构造不出来。

    写错不能让进程起不来：日志级别拼错就炸掉整个服务是荒谬的。
    """
    import logging

    level = getattr(logging, str(raw or "WARNING").strip().upper(), None)
    return level if isinstance(level, int) else logging.WARNING


def setup_logging(env_file: Path | None = None) -> int:
    """按 ``LOG_LEVEL`` 配日志。**不设则 WARNING**——与今天的实际行为逐字节一致。

    为什么非有这段不可：全仓**一处日志配置都没有**。根 logger 没 handler，Python 就回落到
    ``logging.lastResort``，而那个只处理 WARNING 及以上。后果是全仓 60 处 ``.info()``
    一行都产不出来，同时 ``.env`` 里那个 ``LOG_LEVEL`` **没有任何代码读它**，纯摆设。

    于是日志里**只有失败、没有成功**。2026-08-01 真机排查"点了按钮没反应"时就卡死在这：
    加了成功日志照样一片空白，分不清是"回调没到"还是"到了但没效果"——而这两者修法完全不同。

    返回实际生效的级别（int），好让测试断解析结果而不必依赖全局 logging 状态。
    """
    import logging
    import os

    # LOG_LEVEL 写在 .env 里，而 .env 要等 src.llm.client 被导入才加载——这里显式再读一次。
    # （load_dotenv 默认不覆盖已存在的环境变量，重复调用无害。）
    from src.env_compat import load_env_file

    load_env_file(env_file if env_file is not None else Path(__file__).parent.parent / ".env")

    level = resolve_log_level(os.getenv("LOG_LEVEL"))
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if level > logging.DEBUG:
        # 第三方库一到 INFO 就每个 HTTP 请求刷一行，会把自己的日志淹得找不着
        for noisy in (
            "aiohttp",
            "asyncio",
            "httpx",
            "httpcore",
            "openai",
            "urllib3",
            "websockets",
            "charset_normalizer",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    return level


def prepare_runtime(*, log_setup=None) -> None:
    """Preserve logging, environment compatibility and state migration before dispatch."""
    if env_compat("VORTOONE_JSON_LOGS", "AUTODEV_JSON_LOGS") or env_compat(
        "VORTOONE_LOG_FILE", "AUTODEV_LOG_FILE"
    ):
        from src.core.tracing import setup_structured_logging

        setup_structured_logging(
            log_file=env_compat("VORTOONE_LOG_FILE", "AUTODEV_LOG_FILE") or None
        )
    else:
        (log_setup or setup_logging)()
    from src.utils.state_dir import home_state_dir, state_dir

    state_dir(Path.cwd())
    home_state_dir()
