"""Pytest 共享夹具（全套件）。"""
import pytest


@pytest.fixture(autouse=True)
def _default_prompt_protocol(monkeypatch):
    """把测试套件默认钉在**提示式**工具协议（VORTOCODE_NATIVE_TOOLS=0）。

    2026-07 起 native_default() 默认**开**（真机对照 dogfood：native 3/3 正确落地 vs 提示式 1/3，
    对 mimo 明显更可靠）。但大量 loop-mechanics 测试用脚本化 LLM 输出**提示式 JSON** 工具调用
    （`{"tool":...}`）——若默认走 native，这些 JSON 不会被当工具调用解析、测试全崩。故此处把套件
    默认钉在提示式，保证确定性、匹配这些 stub。

    需要另一种行为的用例自行覆盖：显式测 native 的传 `native=True` 或 `setenv(...,"1")`；测
    `native_default()` 真实默认的用例先 `delenv`（去掉这里设的 0 → 读到默认开）。
    """
    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "0")


@pytest.fixture(autouse=True)
def _default_dev_review_off(monkeypatch):
    """把测试套件默认钉在**关闭** dev_auto 的 PR 前审查段（VORTOCODE_DEV_REVIEW=0）。

    生产默认**开**，但审查段会另起 reviewer 子 agent（需 LLM），且给 dev_auto 输出追加审查注记——
    会干扰无关的 dev_auto 单测。审查段有自己的专门测试（test_review.py 直接测 run_gate）。测真实默认
    的用例先 `delenv`（去掉这里设的 0 → 读到默认开）。
    """
    monkeypatch.setenv("VORTOCODE_DEV_REVIEW", "0")


@pytest.fixture(autouse=True)
def _explicit_test_sandbox_off(monkeypatch):
    """Unit fixtures execute tiny local commands, so authorize host execution explicitly.

    Production defaults to ``auto`` and unattended generated-code paths fail closed when
    no Seatbelt/bubblewrap backend is available. The sandbox policy and argv construction
    have dedicated tests which delete/override this variable as needed; unrelated unit
    tests should not depend on the CI runner having bubblewrap installed.
    """
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")


@pytest.fixture(autouse=True)
def _hermetic_ambient_credentials(monkeypatch):
    """把套件与**机器自带的凭据/开关**隔开：`.env` 里有什么都不该改变测试结论。

    `scripts/ci-local.sh` 与 CI 都把这四个设成空串，这本身是对的。但**保护来自调用方**，
    而 CLAUDE.md 教人敲的是裸命令 `python -m pytest tests/ -q`——在 `.env` 里配了
    `VORTOCODE_API_TOKEN` 的机器上（生产机 192.168.10.97 就是），那条命令会红 55 条，
    报错还长得像代码坏了（`KeyError: 'artifacts'`——其实是 401 的响应体里没有那个键）。

    **空串而不是 delenv，这个区别是要命的**：`load_dotenv(override=False)` 的判据是
    "键在不在 `os.environ` 里"，空串也算在。所以
        `VORTOCODE_API_TOKEN=""`  → dotenv 不覆盖 → 真的空
        `env -u VORTOCODE_API_TOKEN` → 键没了 → dotenv 从 `.env` 读回来 → 反而有值
    两种"清掉"的写法效果完全相反。2026-09-10 我就是用后者去验生产机，把 55 条环境失败
    误判成回归，查了半天。

    真要这些值的用例自行 `monkeypatch.setenv(...)` 覆盖——与本文件其他夹具同一约定。
    """
    for name in ("OPENAI_API_KEY", "VORTOCODE_API_TOKEN",
                 "VORTOCODE_ENABLE_SHELL", "VORTOCODE_ENABLE_BROWSER"):
        monkeypatch.setenv(name, "")
