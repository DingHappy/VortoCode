"""scripts/mobile-release.sh：手机网关跑固定快照，发布要生成新快照、切换环境文件、核对，失败自动切回。

用假的 systemctl / curl 跑真脚本：只验证快照内容、环境文件切换与回滚，不碰真服务。
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mobile-release.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="需要 bash 与 git")


def _setup(tmp_path, probe_body):
    deploy = tmp_path / "deploy"
    (deploy / "src" / "web").mkdir(parents=True)
    (deploy / "src" / "web" / "auth.py").write_text("ROUTES = ['/api/mobile/native/keys']\n")
    git = ["git", "-C", str(deploy), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(deploy)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)

    releases = tmp_path / "releases"
    old = releases / "mobile-gateway-0000000000000000"
    (old / "web" / "mobile").mkdir(parents=True)
    (old / "web" / "mobile" / "index.html").write_text("<html></html>")
    env = tmp_path / "environment"
    env.write_text(f"VORTOCODE_MOBILE_PUBLIC_ORIGIN=https://example.test:18443\n"
                   f"PYTHONPATH={old}\nVORTOCODE_MOBILE_RELEASE={old.name}\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text("#!/bin/sh\ncase \"$*\" in *is-active*) echo active;; *NRestarts*) echo 0;; esac\n"
                                      f"echo \"$*\" >> {tmp_path}/systemctl.log\n")
    (bin_dir / "curl").write_text(f"#!/bin/sh\nprintf '%s' '{probe_body}'\n")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    environ = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "VORTOCODE_DEPLOY_DIR": str(deploy),
               "VORTOCODE_MOBILE_ENV_FILE": str(env), "VORTOCODE_MOBILE_RELEASES": str(releases),
               "VORTOCODE_MOBILE_SETTLE_SECONDS": "0"}
    return deploy, releases, old, env, environ


def _run(environ, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=environ, capture_output=True, text=True)


def test_new_snapshot_is_built_switched_and_verified(tmp_path):
    deploy, releases, old, env, environ = _setup(tmp_path, '{"detail":"登录或设备授权已失效，请重新登录"}')
    result = _run(environ)
    assert result.returncode == 0, result.stderr
    release = next(line.split("=", 1)[1] for line in env.read_text().splitlines()
                   if line.startswith("VORTOCODE_MOBILE_RELEASE="))
    assert release != old.name and f"PYTHONPATH={releases / release}" in env.read_text()
    snapshot = releases / release
    assert (snapshot / "src" / "web" / "auth.py").exists() and (snapshot / "web" / "mobile" / "index.html").exists()
    manifest = json.loads((snapshot / "RELEASE.json").read_text())
    head = subprocess.run(["git", "-C", str(deploy), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert manifest["source_commit"] == head and manifest["release"] == release
    assert "restart" in (tmp_path / "systemctl.log").read_text()
    assert list(tmp_path.glob("environment.before-*"))
    assert _run(environ).stdout.strip().endswith("不用切换")       # 再跑一次：内容没变就不动


def test_old_code_answering_with_middleware_401_rolls_back(tmp_path):
    _, _, old, env, environ = _setup(tmp_path, '{"detail":"Unauthorized"}')
    original = env.read_text()
    result = _run(environ)
    assert result.returncode == 1
    assert env.read_text() == original                                  # 切回旧快照
    assert (tmp_path / "systemctl.log").read_text().count("restart") == 2


def test_dirty_deploy_dir_or_dry_run_changes_nothing(tmp_path):
    deploy, releases, _, env, environ = _setup(tmp_path, "{}")
    original = env.read_text()
    assert _run(environ, "--dry-run").returncode == 0
    assert env.read_text() == original and [p.name for p in releases.iterdir()] == ["mobile-gateway-0000000000000000"]
    (deploy / "src" / "web" / "auth.py").write_text("changed\n")
    result = _run(environ)
    assert result.returncode == 1 and "未提交" in result.stderr and env.read_text() == original
