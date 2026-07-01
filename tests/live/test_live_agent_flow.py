"""真模型 live 验证层：确认主 agent 对话流程在**真实 LLM** 上真站得住（不只是 mock 绿）。

设计原则（对齐"框架要通用、跨模型"）：
- **按模型参数化**：同一批行为测试对 VORTOCODE_LIVE_MODELS 里的**每个模型**各跑一遍，
  验证框架不是只为某个模型调好的。默认取 DEFAULT_MODEL；可 `VORTOCODE_LIVE_MODELS=a,b,c` 指定多个。
- **断言看结果、不看格式**：只断言"用了工具/答案落地到文件内容/分支名来自 env"这类与模型无关的产出，
  不依赖某模型的措辞或输出风格。
- **默认跳过**：CI/普通 pytest 不跑（不触网、不烧 token）。要跑：

      VORTOCODE_LIVE_TESTS=1 OPENAI_API_KEY=... pytest tests/live -q
      # 跨多个模型：
      VORTOCODE_LIVE_TESTS=1 VORTOCODE_LIVE_MODELS=mimo-v2.5,mimo-v2.5-pro pytest tests/live -q

真机曾据此发现真 bug（如系统提示没放开并行 → 任何模型都不批量）。
"""
import os
import subprocess

import pytest

_LIVE = os.getenv("VORTOCODE_LIVE_TESTS") == "1" and bool(os.getenv("OPENAI_API_KEY"))
_MODELS = [m.strip() for m in
           (os.getenv("VORTOCODE_LIVE_MODELS") or os.getenv("DEFAULT_MODEL") or "mimo-v2.5").split(",")
           if m.strip()]

pytestmark = pytest.mark.skipif(
    not _LIVE, reason="live 测试：需 VORTOCODE_LIVE_TESTS=1 + OPENAI_API_KEY（可选 VORTOCODE_LIVE_MODELS）")


@pytest.fixture
def scratch(tmp_path):
    """一次性 git 仓库 + 两个内容已知的文件，供工具/env 探测。"""
    (tmp_path / "alpha.txt").write_text("ALPHA 模块：负责用户登录与鉴权。\n", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("BETA 模块：负责订单结算与支付。\n", encoding="utf-8")
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
              ["add", "-A"], ["commit", "-q", "-m", "baseline"]):
        subprocess.run(["git", "-C", str(tmp_path), *c], check=True, capture_output=True)
    return tmp_path


def _agent(repo, model, **kw):
    from src.agents.main_agent import MainAgent, build_read_tools
    a = MainAgent(build_read_tools(str(repo)), max_steps=8, **kw)
    a.set_model(model)                         # 用 set_model 把这条测试切到目标模型（参数化的核心）
    return a


_ALPHA = ("登录", "鉴权", "认证", "ALPHA")           # a.txt 概念的多种说法（避免因措辞误判）
_BETA = ("结算", "支付", "订单", "交易", "BETA")     # b.txt 概念的多种说法


async def _passes(make_run, check, attempts=3):
    """真模型是非确定的：多试几次，区分"这个模型做不到"和"偶尔翻车一次"。
    make_run() 每次返回一个新的 run_turn 协程（全新 agent）；check(答案)->bool。返回 (是否通过, 最后答案)。

    注意：本层是**门控的非确定诊断**（不是 CI 硬门）——用来观察"框架在某模型上到底行不行"，
    偶发失败重跑即可；系统性失败（多次都不过）才说明该模型与框架配合有问题。"""
    ans = ""
    for _ in range(attempts):
        ans = await make_run()
        if check(ans):
            return True, ans
    return False, ans


@pytest.mark.parametrize("model", _MODELS)
@pytest.mark.asyncio
async def test_live_basic_tool_loop(scratch, model):
    # 核心循环：模型该用只读工具读文件、答案落地到文件内容（whatever 措辞）
    ok, ans = await _passes(
        lambda: _agent(scratch, model).run_turn("读取 alpha.txt，说说它讲的是什么模块。", mode="plan"),
        lambda a: any(k in a for k in _ALPHA))
    assert ok, f"[{model}] 答案没落地到文件内容: {ans[:200]}"


@pytest.mark.parametrize("model", _MODELS)
@pytest.mark.asyncio
async def test_live_env_grounding(scratch, model, monkeypatch):
    # #97 env 上下文：分支名来自 <env>，模型不该凭空知道 → 出现在回答里即证明 env 真进了上下文
    monkeypatch.chdir(scratch)                 # 让 _env_block 的 cwd/git 反映 scratch（monkeypatch 自动还原）
    branch = subprocess.run(["git", "-C", str(scratch), "branch", "--show-current"],
                            capture_output=True, text=True).stdout.strip()
    ok, ans = await _passes(
        lambda: _agent(scratch, model, env_context=True).run_turn(
            "不要调用任何工具，直接回答：当前 git 分支叫什么名字？", mode="plan"),
        lambda a: bool(branch) and branch in a)
    assert ok, f"[{model}] 分支名 {branch!r} 没出现在回答里（env 未生效？）: {ans[:200]}"


@pytest.mark.parametrize("model", _MODELS)
@pytest.mark.asyncio
async def test_live_reads_multiple_files(scratch, model):
    # #97 多工具流程：请求读两个文件 → 两个内容都该反映在回答里（批量或顺序都算过，结果为准）
    ok, ans = await _passes(
        lambda: _agent(scratch, model).run_turn(
            "读取 alpha.txt 和 beta.txt，各用一句话概括其内容。", mode="plan"),
        lambda a: any(k in a for k in _ALPHA) and any(k in a for k in _BETA))
    assert ok, f"[{model}] 两文件没都读到: {ans[:200]}"
