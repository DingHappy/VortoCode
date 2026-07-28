#!/usr/bin/env bash
# 本地门禁——CI 停用期间的等价替身，跑的是和 .github/workflows/ci.yml 一样的三道关。
#
# 为什么需要它：2026-07-12 起 GitHub Actions 因私有仓库免费额度耗尽而无法启动 job，
# 工作流已 `gh workflow disable CI` 暂停。**关掉 CI 而不给替代品，等于没有门禁**——
# 合并前在本地跑这个，至少保证进 main 的东西过了同样的检查。
#
#   ./scripts/ci-local.sh            # 全跑（含 desktop 段，默认）
#   ./scripts/ci-local.sh quick      # 跳过 integration/live 与 desktop，快一半
#   SKIP_DESKTOP=1 ./scripts/ci-local.sh   # 跳过 desktop 段（逃生口）
#
# 自建 runner 就绪后（装机见 docs/OPS.md）：
#   设仓库变量 CI_RUNNER=self-hosted → gh workflow enable CI → 手动 workflow_dispatch 验一次
# 那之后这个脚本仍然有用（推之前先自查，省一轮往返）。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

MODE="${1:-full}"
FAILED=()
SKIPPED=()

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

# 跳过 ≠ 通过。记进 SKIPPED，收尾时再列一次——一条滚过去的警告在 4 分钟的输出里等于没说。
skip_gate() {
  local name="$1" reason="$2"
  echo ""
  echo "──────── $name ────────"
  echo "⚠️  跳过：$reason"
  SKIPPED+=("$name —— $reason")
}

run_gate "ruff（真错误门禁）" python3 -m ruff check src tests
run_gate "mypy（范围见 pyproject [tool.mypy]）" python3 -m mypy

# CI 刻意不给 API key / shell / browser：保证测试离线、确定性。这里保持一致，
# 否则本地"绿"可能只是因为你的 .env 里有 key，而 CI 上根本不是这个环境。
export OPENAI_API_KEY=""
export VORTOCODE_API_TOKEN=""
export VORTOCODE_ENABLE_SHELL=""
export VORTOCODE_ENABLE_BROWSER=""
# 同理屏蔽**全局 git 身份**：开发机上 user.name/email 是配好的，CI runner 上没有。
# 依赖这份"环境自带身份"的测试在本地永远绿、一上 CI 全红——2026-07-26 就这么被咬了一次
# （新加的流水线预检打挂 8 条存量测试，本地门禁却全绿）。测试要自带身份，不许蹭环境的。
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_SYSTEM=/dev/null

if [ "$MODE" = "quick" ]; then
  run_gate "pytest（unit + tui）" python3 -m pytest tests/unit tests/test_basic.py -q
else
  run_gate "pytest（全量，同 CI 的三个 suite）" \
    python3 -m pytest tests/unit tests/test_basic.py tests/integration tests/live -q
fi

# e2e 交付链路（canary）——**quick 也跑**，只要十几秒。
# 单测证明每一段对，这一段证明接缝没断：2026-07-27 一天里五个真机 bug 全是"每段单测都绿、
# 东西没送到人手机上"（cron_run 漏传 notify、确认门绕过分档函数、三处硬编码教钉钉用户按 Tab）。
# 那类缺陷对"断函数返回值"的测试天然免疫——返回值确实是对的。
run_gate "pytest（e2e 交付链路 canary）" python3 -m pytest tests/e2e -q

# ─────────────────────────────────────────────────────────────
# desktop 段（B6-2）——**刻意只跑 `npm run check` 的离线子集**
#
# 为什么不直接 `npm run check`：那条链里 sidecar:build → ensureSidecarEnvironment
# → ensureManagedPython 会 `curl -L` 一个 python-build-standalone 压缩包
# （见 desktop/scripts/managed-python.mjs:129）。本门禁的立身之本就是离线、确定性
# ——上面刚把 API key 全清掉就是为这个。把一个联网下载塞进来会毁掉这条不变量：
# 本机因为有缓存看不出来，换台机器/冷缓存就变成"门禁要联网拉几百 MB"。
#
# 所以门禁取不联网的部分：tsc（类型）+ vitest（gateway.ts 纯逻辑单测，B6-3）
# + cargo test --lib（40 条 Rust 单测，含路径围栏/项目注册那几条安全测试）
# + cargo check（额外覆盖 main.rs）。约 22s。
# 打包正确性（vite build ~44s、sidecar、bundle 冒烟）属发布前检查，仍走 `npm run check`。
# check-runtime-entry.mjs 不重复跑——它就是 pytest tests/unit/test_desktop_runtime_entry.py，
# 上面的 pytest 段已经覆盖了。
# 同口径的单命令版本：cd desktop && npm run check:ci
# ─────────────────────────────────────────────────────────────
desktop_ts_gate() { ( cd desktop && npx --no-install tsc --noEmit ); }

# `vitest run` 而不是裸 `vitest`——后者是 watch 模式，会让门禁永远挂住不返回。
# 测试本身全程 mock 掉 WebSocket/fetch，不起 dev server、不联网。
desktop_vitest_gate() { ( cd desktop && npx --no-install vitest run ); }

desktop_rust_gate() {
  local out status
  out="$( cd desktop \
          && cargo test --manifest-path src-tauri/Cargo.toml --lib --offline --quiet 2>&1 \
          && cargo check --manifest-path src-tauri/Cargo.toml --offline --quiet 2>&1 )"
  status=$?
  printf '%s\n' "$out"
  # 冷缓存（crate 没下过）不是代码问题，别报红骗人——降级成跳过并说清怎么修。
  # cargo 在这件事上有两种说法，都要认：
  #   · 索引已缓存、缺具体 crate 包 → "attempting to make an HTTP request, but `--offline` was specified"
  #   · 索引压根没缓存             → "no matching package named X found" + "note: offline mode (via `--offline`) ..."
  # 只匹配前者会让空 CARGO_HOME 的机器收到一个假的红灯（实测踩过）。
  if [ $status -ne 0 ] \
     && printf '%s' "$out" | grep -qE 'attempting to make an HTTP request|offline mode \(via'; then
    return 111
  fi
  return $status
}

# tauri.conf.json 声明了两个 gitignored 的构建产物，build script 在编译期都要校验存在：
#   externalBin → binaries/vortocode-runtime-<triple>
#   resources   → resources/THIRD_PARTY_NOTICES.txt
# 缺任何一个 cargo 段都编不过。返回缺失项的说明，全齐则返回空串。
# glob 无匹配时原样留下字面量、[ -f ] 自然为假，不需要 shopt/compgen（bash 3.2 也能跑）。
_desktop_build_inputs_missing() {
  local candidate found=""
  for candidate in desktop/src-tauri/binaries/vortocode-runtime-*; do
    [ -f "$candidate" ] && found="yes"
  done
  [ -z "$found" ] && { echo "binaries/vortocode-runtime-<triple>"; return; }
  [ -f desktop/src-tauri/resources/THIRD_PARTY_NOTICES.txt ] \
    || { echo "resources/THIRD_PARTY_NOTICES.txt"; return; }
  echo ""
}

run_desktop_section() {
  if [ -n "${SKIP_DESKTOP:-}" ]; then
    skip_gate "desktop" "SKIP_DESKTOP=1 显式跳过"
    return
  fi
  if [ "$MODE" = "quick" ]; then
    skip_gate "desktop" "quick 模式不含 desktop（单独验：cd desktop && npm run check:ci）"
    return
  fi
  if [ ! -d desktop ]; then
    skip_gate "desktop" "没有 desktop/ 目录"
    return
  fi

  if ! command -v npx >/dev/null 2>&1; then
    skip_gate "desktop · tsc + vitest" "没装 node/npx——装了再跑，或 SKIP_DESKTOP=1"
  elif [ ! -d desktop/node_modules ]; then
    # 新 clone 必然走到这里：npm ci 要联网，门禁不替你装。
    skip_gate "desktop · tsc + vitest" "desktop/node_modules 缺失——先 cd desktop && npm ci（需联网）"
  else
    run_gate "desktop · tsc（类型）" desktop_ts_gate
    run_gate "desktop · vitest（gateway 纯逻辑单测）" desktop_vitest_gate
  fi

  # Tauri 的 build script 在**编译期**校验 tauri.conf.json 声明的 externalBin
  # （binaries/vortocode-runtime-<triple>）真实存在，而 binaries/* 是 gitignored 的构建产物
  # ——只有 `npm run sidecar:build` 会生成它，那一步要联网拉 managed Python。
  # 所以任何全新 clone / git worktree 上 cargo 段都编不过。这是环境没备好，不是代码坏了，
  # 跟 node_modules 缺失同一类：**降级为跳过**，别报一个骗人的红灯。
  # （这正是本门禁最初要防的那种"本机有产物所以看不出来"的陷阱，第一次没防住自己。）
  _missing_input="$(_desktop_build_inputs_missing)"
  if [ -n "$_missing_input" ]; then
    skip_gate "desktop · cargo" \
      "构建产物缺失（${_missing_input}）——先 cd desktop && npm run sidecar:build（需联网）"
    return
  fi
  if ! command -v cargo >/dev/null 2>&1; then
    skip_gate "desktop · cargo" "没装 cargo——装 Rust 工具链再跑，或 SKIP_DESKTOP=1"
    return
  fi
  echo ""
  echo "──────── desktop · cargo（--lib 单测 + check） ────────"
  desktop_rust_gate
  case $? in
    0)   echo "✓ desktop · cargo（--lib 单测 + check）" ;;
    111) SKIPPED+=("desktop · cargo —— crate 依赖未缓存，--offline 取不到；先联网跑一次 cd desktop && cargo fetch --manifest-path src-tauri/Cargo.toml")
         echo "⚠️  跳过：crate 依赖未缓存（--offline 取不到）——先联网 cargo fetch 一次" ;;
    *)   echo "✗ desktop · cargo（--lib 单测 + check）"
         FAILED+=("desktop · cargo（--lib 单测 + check）") ;;
  esac
}

run_desktop_section

echo ""
if [ ${#SKIPPED[@]} -ne 0 ]; then
  echo "⚠️  跳过的检查（**不等于通过**）："
  for item in "${SKIPPED[@]}"; do echo "   · $item"; done
  echo ""
fi
if [ ${#FAILED[@]} -eq 0 ]; then
  if [ ${#SKIPPED[@]} -ne 0 ]; then
    # 有跳过项时不说"全部通过"——那正是 B6-2 要防的假绿。
    echo "════ 已跑的都通过，但有 ${#SKIPPED[@]} 项被跳过（见上）════"
  else
    echo "════ 全部通过 ════"
  fi
  exit 0
fi
echo "════ 失败：${FAILED[*]} ════"
exit 1
