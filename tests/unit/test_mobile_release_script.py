"""scripts/mobile-release.sh：手机网关跑固定快照，发布要生成新快照、切换环境文件、核对，失败自动切回。

用假的 systemctl / curl 跑真脚本：只验证快照内容、环境文件切换与回滚，不碰真服务。
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mobile-release.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="需要 bash 与 git")


def _setup(tmp_path, probe_body):
    deploy = tmp_path / "deploy"
    (deploy / "src" / "web").mkdir(parents=True)
    (deploy / "src" / "web" / "auth.py").write_text(
        "MOBILE_SELF_AUTHENTICATED = frozenset({('POST', '/api/mobile/native/keys'), "
        "('GET', '/api/mobile/native/timeline'), ('POST', '/api/mobile/native/goal-advance/resume')})\n")
    shutil.copyfile(SCRIPT.parent.parent / 'src/web/mobile_rate_limit.py', deploy / 'src/web/mobile_rate_limit.py')
    git = ["git", "-C", str(deploy), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(deploy)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)

    releases = tmp_path / "releases"
    old = releases / "mobile-gateway-0000000000000000"
    (old / "web" / "mobile").mkdir(parents=True)
    (old / "web" / "mobile" / "index.html").write_text("<html></html>")
    env = tmp_path / "environment"
    env.write_text(f"VORTOONE_MOBILE_PUBLIC_ORIGIN=https://example.test:18443\n"
                   f"PYTHONPATH={old}\nVORTOONE_MOBILE_RELEASE={old.name}\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / 'python3').write_text(f'#!{sys.executable}\n' +
        'import importlib.util, os, pathlib, sys\n' +
        'if len(sys.argv)>1 and pathlib.Path(sys.argv[1]).name == "mobile-route-check.py":\n' +
        '    spec=importlib.util.spec_from_file_location("route_check", sys.argv[1])\n' +
        '    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n' +
        '    clock=[0.0]\n' +
        '    def sleep(seconds): clock[0]+=seconds\n' +
        '    sys.exit(module.main(sys.argv[2:], clock=lambda:clock[0], sleep=sleep))\n' +
        'os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n')
    (bin_dir / "systemctl").write_text("#!/usr/bin/env python3\n" +
        "import os, pathlib, sys\n" +
        f"log=pathlib.Path({str(tmp_path / 'systemctl.log')!r})\n" +
        "old=log.read_text() if log.exists() else ''\n" +
        "with log.open('a') as f: f.write(' '.join(sys.argv[1:])+'\\n')\n" +
        "if 'restart' in sys.argv and os.getenv('RESTART_FAIL') and 'restart' not in old: sys.exit(1)\n" +
        "if 'is-active' in sys.argv: print('active')\n" +
        "if 'NRestarts' in ' '.join(sys.argv) and os.getenv('COUNTER_FAIL'): sys.exit(1)\n" +
        "if 'NRestarts' in ' '.join(sys.argv): print('0')\n")
    (bin_dir / "curl").write_text("#!/usr/bin/env python3\n" +
        "import json, os, pathlib, sys\n" +
        "args=sys.argv[1:]; url=args[-1]\n" +
        f"log=pathlib.Path({str(tmp_path / 'curl.log')!r})\n" +
        "with log.open('a') as f: f.write(url+'\\n')\n" +
        f"body={probe_body!r}; code='401'\n" +
        "if '/api/assistant/' in url: body='Not found'; code='404'\n" +
        "if os.getenv('PROBE_FAIL_PUBLIC') and url.startswith('https:') and '/native/timeline' in url: body='Not found'; code='404'\n" +
        "if os.getenv('PROBE_EXPOSE_ADMIN') and '/api/assistant/' in url: body='{}'; code='401'\n" +
        "pathlib.Path(args[args.index('--output')+1]).write_text(body)\n" +
        "sys.stdout.write(code)\n")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    environ = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "VORTOONE_DEPLOY_DIR": str(deploy),
               "VORTOONE_MOBILE_ENV_FILE": str(env), "VORTOONE_MOBILE_RELEASES": str(releases),
               "VORTOONE_MOBILE_SETTLE_SECONDS": "0", "VORTOONE_MOBILE_CADDYFILE": str(tmp_path / "no-caddy") }
    return deploy, releases, old, env, environ


def _run(environ, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=environ, capture_output=True, text=True)


def test_new_snapshot_is_built_switched_and_verified(tmp_path):
    deploy, releases, old, env, environ = _setup(tmp_path, '{"detail":"登录或设备授权已失效，请重新登录"}')
    result = _run(environ)
    assert result.returncode == 0, result.stderr
    release = next(line.split("=", 1)[1] for line in env.read_text().splitlines()
                   if line.startswith("VORTOONE_MOBILE_RELEASE="))
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
    assert not (tmp_path / "curl.log").exists() and not (tmp_path / "systemctl.log").exists()
    (deploy / "src" / "web" / "auth.py").write_text("changed\n")
    result = _run(environ)
    assert result.returncode == 1 and "未提交" in result.stderr and env.read_text() == original


def test_public_route_missing_rolls_back_even_when_loopback_passes(tmp_path):
    _, _, _, env, environ = _setup(tmp_path, '{"detail":"登录或设备授权已失效，请重新登录"}')
    original = env.read_text()
    environ["PROBE_FAIL_PUBLIC"] = "1"
    result = _run(environ)
    assert result.returncode == 1 and "公网入口" in result.stderr
    assert env.read_text() == original
    assert (tmp_path / "systemctl.log").read_text().count("restart") == 2


def test_missing_caddy_path_fails_before_switch_or_restart(tmp_path):
    _, _, _, env, environ = _setup(tmp_path, '{"detail":"登录或设备授权已失效，请重新登录"}')
    original = env.read_text()
    caddy = tmp_path / "Caddyfile"
    caddy.write_text("@mobile_get {\n method GET\n path /api/mobile/native/timeline\n}\n"
                     "@mobile_post {\n method POST\n path /api/mobile/native/keys\n}\n")
    environ["VORTOONE_MOBILE_CADDYFILE"] = str(caddy)
    result = _run(environ)
    assert result.returncode == 1 and "goal-advance/resume" in result.stderr
    assert env.read_text() == original
    assert not (tmp_path / "systemctl.log").exists()
    assert not (tmp_path / "curl.log").exists()


def test_unchanged_release_still_checks_public_routes_without_restart(tmp_path):
    _, _, _, env, environ = _setup(tmp_path, '{"detail":"登录或设备授权已失效，请重新登录"}')
    assert _run(environ).returncode == 0
    original = env.read_text()
    restarts = (tmp_path / "systemctl.log").read_text().count("restart")
    environ["PROBE_FAIL_PUBLIC"] = "1"
    result = _run(environ)
    assert result.returncode == 1 and "未重启" in result.stderr
    assert env.read_text() == original
    assert (tmp_path / "systemctl.log").read_text().count("restart") == restarts


def test_exposed_admin_route_fails_and_rolls_back(tmp_path):
    _, _, _, env, environ = _setup(tmp_path, '{"detail":"登录或设备授权已失效，请重新登录"}')
    original = env.read_text()
    environ["PROBE_EXPOSE_ADMIN"] = "1"
    assert _run(environ).returncode == 1
    assert env.read_text() == original


@pytest.mark.parametrize("failure_var", ["RESTART_FAIL", "COUNTER_FAIL"])
def test_failed_restart_restores_old_environment_and_restarts_it(tmp_path, failure_var):
    _, _, _, env, environ = _setup(tmp_path, '{"detail":"登录或设备授权已失效，请重新登录"}')
    original = env.read_text()
    environ[failure_var] = "1"
    result = _run(environ)
    assert result.returncode == 1 and env.read_text() == original
    assert (tmp_path / "systemctl.log").read_text().count("restart") == 2


def test_environment_written_before_the_rename_keeps_its_legacy_keys(tmp_path):
    """97 上的环境文件是改名前写的（VORTOCODE_*，ADR-018 批次 B）：照样能发，切换后键名不变。"""
    _, releases, old, env, environ = _setup(tmp_path, '{"detail":"登录或设备授权已失效，请重新登录"}')
    env.write_text(env.read_text().replace("VORTOONE_", "VORTOCODE_"))
    for key in [k for k in environ if k.startswith("VORTOONE_")]:       # 调用方也还在用旧名字
        environ["VORTOCODE_" + key[len("VORTOONE_"):]] = environ.pop(key)
    result = _run(environ)
    assert result.returncode == 0, result.stderr
    text = env.read_text()
    release = next(line.split("=", 1)[1] for line in text.splitlines()
                   if line.startswith("VORTOCODE_MOBILE_RELEASE="))
    assert release != old.name and f"PYTHONPATH={releases / release}" in text
    assert "VORTOONE_" not in text
