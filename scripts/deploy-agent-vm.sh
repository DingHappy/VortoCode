#!/usr/bin/env bash
# 把常驻 serve 部署到 agent 专用 VM（默认 vorto-code）——拉代码 → 按需装依赖 → 重启 → 验收。
#
# 为什么要有这个脚本：手动部署是「ssh 进去 / git pull / 可能要重装依赖 / 重启服务 / 看日志」
# 四五步，**最容易漏的是重启**——2026-07-26 真机上就栽过：代码已经 pull 了、服务还跑着旧版本，
# 于是"验证修复"验的是没修的东西，白折腾一轮。所以这个脚本的核心不是省事，是**证明**：
# 收尾会打印远端实际运行的 commit 与服务重启时刻，对不上就非零退出。
#
#   ./scripts/deploy-agent-vm.sh                 # 部署 main
#   ./scripts/deploy-agent-vm.sh vorto/some-work # 部署指定分支（真机验证功能分支用）
#   VORTOCODE_DEPLOY_HOST=myhost ./scripts/deploy-agent-vm.sh
#
# 前置：本机 ssh 能免密登到目标主机；远端已按 docs/OPS.md 装好 venv 与 systemd 用户服务。
#
# 为什么跑在独立 VM 而不是宿主机：裸机上这个进程握着 gh 凭据、SSH 私钥、CI runner 配置和
# 整个家目录——要给 agent 更大权限干活，就得先把爆炸半径关进一台可丢弃的机器（2026-07-26）。
set -uo pipefail

HOST="${VORTOCODE_DEPLOY_HOST:-vorto-code}"
REMOTE_DIR="${VORTOCODE_DEPLOY_DIR:-personal_project/VortoCode}"
SERVICE="${VORTOCODE_DEPLOY_SERVICE:-vortocode-serve.service}"
REF="${1:-main}"

say() { printf '\n──────── %s ────────\n' "$1"; }
die() { printf '\n✗ %s\n' "$1" >&2; exit 1; }

# 远端命令一律带上服务同款 PATH：ssh 的默认 PATH 比 systemd 服务的窄，
# 不对齐就会出现「服务跑得好好的，脚本里却说 gh 没装」这种假警报（真机 2026-07-26 撞到）。
remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "export PATH=\$HOME/.local/bin:/usr/local/bin:\$PATH; $*"; }

# ── 0. 先确认要部署的东西**已经推上去了** ──────────────────────────────
# 远端是从 origin 拉的；本地提交了但没 push，部署出去的就是旧版本——而且看起来一切正常。
say "预检"
LOCAL_SHA="$(git rev-parse --verify --quiet "$REF" || true)"
if [ -n "$LOCAL_SHA" ]; then
  if ! git merge-base --is-ancestor "$LOCAL_SHA" "origin/$REF" 2>/dev/null; then
    die "本地 $REF ($(git rev-parse --short "$LOCAL_SHA")) 还没推到 origin/$REF——先 git push，否则部署的是旧版本"
  fi
fi
remote "test -d ~/$REMOTE_DIR/.git" || die "远端 $HOST:~/$REMOTE_DIR 不是 git 仓库（装机见 docs/OPS.md）"
echo "✓ $HOST:~/$REMOTE_DIR · 部署 $REF"

BEFORE_SHA="$(remote "git -C ~/$REMOTE_DIR rev-parse --short HEAD" 2>/dev/null || echo unknown)"
BEFORE_DEPS="$(remote "md5sum ~/$REMOTE_DIR/pyproject.toml 2>/dev/null | cut -d' ' -f1" || echo x)"

# ── 1. 拉代码 ───────────────────────────────────────────────────────
say "拉取 $REF"
remote "cd ~/$REMOTE_DIR && git fetch --quiet origin && git checkout --quiet '$REF' 2>/dev/null || git checkout --quiet -B '$REF' 'origin/$REF'" \
  || die "checkout $REF 失败"
remote "cd ~/$REMOTE_DIR && git reset --hard --quiet 'origin/$REF' && git rev-parse --short HEAD" \
  || die "同步 origin/$REF 失败"

AFTER_SHA="$(remote "git -C ~/$REMOTE_DIR rev-parse --short HEAD")"
echo "✓ $BEFORE_SHA → $AFTER_SHA"

# ── 2. 依赖：只在 pyproject 变了才重装（省几分钟） ──────────────────────
AFTER_DEPS="$(remote "md5sum ~/$REMOTE_DIR/pyproject.toml | cut -d' ' -f1")"
if [ "$BEFORE_DEPS" != "$AFTER_DEPS" ]; then
  say "pyproject 有变动 → 重装依赖"
  remote "cd ~/$REMOTE_DIR && export PATH=\$HOME/.local/bin:\$PATH && uv pip install --python .venv/bin/python -e '.[all]' 2>&1 | tail -3" \
    || die "依赖安装失败"
else
  echo "✓ 依赖未变，跳过安装"
fi

# ── 3. 重启（最容易漏的一步；记下重启时刻用于验收） ──────────────────────
say "重启 $SERVICE"
remote "systemctl --user restart '$SERVICE'" || die "重启失败"
sleep 8
echo "✓ 已重启"

# ── 4. 验收：不看"命令没报错"，看**远端实际状态** ───────────────────────
say "验收"
ACTIVE="$(remote "systemctl --user is-active '$SERVICE'" || true)"
[ "$ACTIVE" = "active" ] || {
  remote "journalctl --user -u '$SERVICE' -n 20 --no-pager 2>/dev/null || tail -20 ~/.local/share/vortocode-serve.log" || true
  die "服务未在运行（is-active=$ACTIVE）"
}

RUNNING_SHA="$(remote "git -C ~/$REMOTE_DIR rev-parse --short HEAD")"
[ "$RUNNING_SHA" = "$AFTER_SHA" ] || die "远端 HEAD 与预期不符：$RUNNING_SHA ≠ $AFTER_SHA"
echo "✓ 服务 active · 远端 HEAD = $RUNNING_SHA"

SINCE="$(remote "systemctl --user show '$SERVICE' -p ActiveEnterTimestamp --value" || true)"
PORT_OK="$(remote "ss -tln 2>/dev/null | grep -c ':8080'" || echo 0)"
[ "${PORT_OK:-0}" -ge 1 ] || die "8080 未监听——服务起来了但没绑上端口，看日志"
echo "✓ 8080 已监听"

say "doctor（远端自检）"
remote "cd ~/$REMOTE_DIR && .venv/bin/python -m src.cli doctor 2>&1 | tail -12" || true

printf '\n════ 部署完成 ════\n'
printf '  主机     %s:~/%s\n' "$HOST" "$REMOTE_DIR"
printf '  分支     %s\n' "$REF"
printf '  commit   %s（部署前 %s）\n' "$AFTER_SHA" "$BEFORE_SHA"
printf '  服务     active · 重启于 %s\n' "${SINCE:-未知}"
printf '\n注意：钉钉 bot 只有一个——别在别处再起带 --im 的 serve，两个桥会抢同一个 bot。\n'
