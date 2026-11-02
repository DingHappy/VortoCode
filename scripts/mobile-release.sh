#!/usr/bin/env bash
# 更新常驻电脑上的手机网关（vortocode-mobile）。在常驻电脑上运行。
#
# 手机网关**不跑部署目录的代码**：它的 PYTHONPATH 指向一份固定的发布快照
# （~/.local/share/vortocode-mobile/releases/<发布标识>），所以部署目录 git merge + 重启
# 不会更新它（2026-11-02 因此排查了一小时 401）。本脚本从部署目录当前提交生成新快照、
# 切换环境文件、重启并核对；核对不过就自动切回旧快照。
#
# 用法：scripts/mobile-release.sh [--dry-run]
# 重启手机网关不会登出手机：原生会话加密落盘，重启后恢复（gateway/mobile_session_store.py）。
set -euo pipefail

DEPLOY_DIR="${VORTOCODE_DEPLOY_DIR:-$HOME/personal_project/VortoCode}"
ENV_FILE="${VORTOCODE_MOBILE_ENV_FILE:-$HOME/.config/vortocode-mobile/environment}"
RELEASES="${VORTOCODE_MOBILE_RELEASES:-$HOME/.local/share/vortocode-mobile/releases}"
SERVICE="${VORTOCODE_MOBILE_SERVICE:-vortocode-mobile}"
PORT="${VORTOCODE_MOBILE_PORT:-18081}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

say() { printf '%s\n' "$*"; }
fail() { say "✗ $*" >&2; exit 1; }

[[ -f "$ENV_FILE" ]] || fail "找不到手机网关环境文件：$ENV_FILE"
[[ -d "$DEPLOY_DIR/.git" ]] || fail "部署目录不是 Git 仓库：$DEPLOY_DIR"
if [[ -n "$(git -C "$DEPLOY_DIR" status --porcelain --untracked-files=no)" ]]; then
  fail "部署目录有未提交的改动；先恢复干净再发布（快照只从已提交的代码生成）"
fi
commit="$(git -C "$DEPLOY_DIR" rev-parse HEAD)"
current="$(sed -n 's/^VORTOCODE_MOBILE_RELEASE=//p' "$ENV_FILE" | tail -1)"
current_dir="$RELEASES/$current"
[[ -n "$current" && -d "$current_dir/web/mobile" ]] || fail "当前快照缺少 web/mobile（手机网页的构建产物），无法带入新快照：$current_dir"

mkdir -p "$RELEASES"
staging="$(mktemp -d "$RELEASES/.new-XXXXXX")"
trap 'rm -rf "$staging"' EXIT
git -C "$DEPLOY_DIR" archive "$commit" src | tar -x -C "$staging"
mkdir -p "$staging/web"
cp -a "$current_dir/web/mobile" "$staging/web/"

release="$(python3 - "$staging" "$commit" <<'PY'
import hashlib, json, os, sys, time
root, commit = sys.argv[1], sys.argv[2]
files = {}
for base, _, names in os.walk(root):
    for name in names:
        path = os.path.join(base, name)
        rel = os.path.relpath(path, root)
        with open(path, "rb") as handle:
            files[rel] = hashlib.sha256(handle.read()).hexdigest()
files = dict(sorted(files.items()))
content = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
release = "mobile-gateway-" + content[:16]
with open(os.path.join(root, "RELEASE.json"), "w") as handle:
    json.dump({"release": release, "source_commit": commit, "working_tree_dirty": False,
               "content_sha256": content, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "files": files}, handle, indent=2)
print(release)
PY
)"
target="$RELEASES/$release"
say "部署目录提交：${commit:0:12}"
say "当前快照：$current"
say "新快照：  $release"
if [[ "$release" == "$current" ]]; then
  say "✓ 内容没有变化，不用切换"
  exit 0
fi
if [[ "$DRY_RUN" == 1 ]]; then
  say "（--dry-run：不落盘、不切换、不重启）"
  exit 0
fi

if [[ -d "$target" ]]; then
  say "新快照已存在，直接复用"
else
  chmod -R go-rwx "$staging"
  mv "$staging" "$target"
fi
trap - EXIT
rm -rf "$staging"

backup="$ENV_FILE.before-$(date +%Y%m%d%H%M%S)"
cp -p "$ENV_FILE" "$backup"
switch_to() {
  local from="$1" to="$2"
  sed -i.tmp "s#releases/$from\$#releases/$to#; s#^VORTOCODE_MOBILE_RELEASE=$from\$#VORTOCODE_MOBILE_RELEASE=$to#" "$ENV_FILE"
  rm -f "$ENV_FILE.tmp"
}
restarts() { systemctl --user show -p NRestarts --value "$SERVICE"; }

# 新路由必须由路由自己应答；被管理员令牌中间件拦下（{"detail":"Unauthorized"}）说明还在跑旧代码。
probe() {
  local body
  body="$(curl -s -m 5 -X POST -H 'Authorization: Bearer vcn_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' \
    -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$PORT/api/mobile/native/keys" || true)"
  [[ -n "$body" && "$body" != '{"detail":"Unauthorized"}' && "$body" != *'"Not Found"'* ]]
}

switch_to "$current" "$release"
grep -q "^VORTOCODE_MOBILE_RELEASE=$release\$" "$ENV_FILE" || { cp -p "$backup" "$ENV_FILE"; fail "环境文件没有按预期切换，已恢复"; }
systemctl --user restart "$SERVICE"
before="$(restarts)"          # 手动重启后再取基线：之后再涨就是起来又崩了
sleep "${VORTOCODE_MOBILE_SETTLE_SECONDS:-12}"
if [[ "$(systemctl --user is-active "$SERVICE")" == active && "$(restarts)" == "$before" ]] && probe; then
  say "✓ 手机网关已切换到 $release 并通过核对（环境文件备份：${backup}）"
  say "  回滚：把环境文件里的 $release 改回 ${current}，再 systemctl --user restart $SERVICE"
  say "  已登录的手机不用重新登录（会话已加密落盘，见 gateway/mobile_session_store.py）"
  exit 0
fi
say "✗ 新快照没有通过核对，切回 $current" >&2
cp -p "$backup" "$ENV_FILE"
systemctl --user restart "$SERVICE"
exit 1
