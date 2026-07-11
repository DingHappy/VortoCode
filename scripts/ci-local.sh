#!/usr/bin/env bash
# 本地门禁——CI 停用期间的等价替身，跑的是和 .github/workflows/ci.yml 一样的三道关。
#
# 为什么需要它：2026-07-12 起 GitHub Actions 因私有仓库免费额度耗尽而无法启动 job，
# 工作流已 `gh workflow disable CI` 暂停。**关掉 CI 而不给替代品，等于没有门禁**——
# 合并前在本地跑这个，至少保证进 main 的东西过了同样的检查。
#
#   ./scripts/ci-local.sh            # 三道关全跑（默认）
#   ./scripts/ci-local.sh quick      # 跳过 integration/live，快一半
#
# 自建 runner 就绪后（装机见 docs/OPS.md）：
#   设仓库变量 CI_RUNNER=self-hosted → gh workflow enable CI → 手动 workflow_dispatch 验一次
# 那之后这个脚本仍然有用（推之前先自查，省一轮往返）。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

MODE="${1:-full}"
FAILED=()

run_gate() {
  local name="$1"; shift
  echo ""
  echo "──────── $name ────────"
  if "$@"; then
    echo "✓ $name"
  else
    echo "✗ $name"
    FAILED+=("$name")
  fi
}

run_gate "ruff（真错误门禁）" python3 -m ruff check src tests
run_gate "mypy（范围见 pyproject [tool.mypy]）" python3 -m mypy

# CI 刻意不给 API key / shell / browser：保证测试离线、确定性。这里保持一致，
# 否则本地"绿"可能只是因为你的 .env 里有 key，而 CI 上根本不是这个环境。
export OPENAI_API_KEY=""
export VORTOCODE_API_TOKEN=""
export VORTOCODE_ENABLE_SHELL=""
export VORTOCODE_ENABLE_BROWSER=""

if [ "$MODE" = "quick" ]; then
  run_gate "pytest（unit + tui）" python3 -m pytest tests/unit tests/test_basic.py -q
else
  run_gate "pytest（全量，同 CI 的三个 suite）" \
    python3 -m pytest tests/unit tests/test_basic.py tests/integration tests/live -q
fi

echo ""
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "════ 全部通过 ════"
  exit 0
fi
echo "════ 失败：${FAILED[*]} ════"
exit 1
