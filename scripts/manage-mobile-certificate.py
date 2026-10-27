#!/usr/bin/env python3
"""Private DNS-01 runner for the dedicated mobile ingress; no credentials in logs."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import stat
import subprocess
import time

import yaml

STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"
CERTIFICATE_PROXY = "socks5h://127.0.0.1:17890"


class Problem(Exception):
    """Only constant, non-sensitive codes may be returned to the caller."""


def private_file(path: Path) -> Path:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Problem("private_file_permissions")
    return path


def private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Problem("private_directory_permissions")
    return path


def hostname(value: str) -> str:
    if not isinstance(value, str) or len(value) > 253 or value != value.lower():
        raise Problem("invalid_hostname")
    labels = value.split(".")
    if len(labels) < 2 or not any(c.isalpha() for c in value):
        raise Problem("invalid_hostname")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x) for x in labels):
        raise Problem("invalid_hostname")
    return value


def credentials(document: object, host: str) -> tuple[str, str]:
    """Select exactly one configured AliDNS updater for this exact hostname."""
    matches = []

    def visit(value):
        if isinstance(value, dict):
            dns, ipv4 = value.get("dns"), value.get("ipv4")
            if isinstance(dns, dict) and isinstance(ipv4, dict):
                if dns.get("name") == "alidns" and host in ipv4.get("domains", []):
                    matches.append((dns.get("id"), dns.get("secret")))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    if len(matches) != 1:
        raise Problem("credential_source_ambiguous")
    for value in matches[0]:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", value):
            raise Problem("credentials_missing_or_invalid")
    return matches[0]


class Context:
    def __init__(self, home: Path | None = None):
        self.home = home or Path.home()
        self.config = self.home / ".config/vortocode-https"
        self.data = self.home / ".local/share/vortocode-https"
        self.acme = self.home / ".local/lib/vortocode-acme/acme.sh"
        settings = json.loads(private_file(self.config / "certificate.json").read_text())
        if settings.get("schema_version") != 1:
            raise Problem("unsupported_settings")
        self.host = hostname(settings.get("hostname"))
        self.source = Path(settings["credential_source"])
        if not self.source.is_absolute():
            raise Problem("invalid_credential_source")
        self.proxy = settings.get("certificate_proxy", "")
        if self.proxy not in ("", CERTIFICATE_PROXY):
            raise Problem("certificate_proxy_not_allowed")

    def read_credentials(self):
        return credentials(yaml.safe_load(private_file(self.source).read_text()), self.host)

    def acme_paths(self, staging):
        name = "staging" if staging else "production"
        return self.config / "acme" / name, self.data / "acme" / name


def run(command, *, env=None, capture=False, allow_skip=False, timeout=600):
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        output, errors = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise Problem("external_command_timeout") from None
    if process.returncode not in ((0, 2) if allow_skip else (0,)):
        text = output + errors
        for marker, code in ((b"Forbidden.RAM", "dns_permission_denied"),
                             (b"SignatureDoesNotMatch", "dns_signature_rejected"),
                             (b"InvalidAccessKeyId", "dns_key_rejected"),
                             (b"curl-errors.html for error code: 28", "certificate_network_timeout")):
            if marker in text:
                raise Problem(code)
        raise Problem("external_command_failed")
    return output if capture else process.returncode


def scrub_cached_credentials(config: Path):
    path = config / "account.conf"
    if path.exists():
        private_file(path)
        lines = path.read_text().splitlines(keepends=True)
        clean = [line for line in lines if not re.match(r"^(?:SAVED_)?Ali_(?:Key|Secret)=", line)]
        path.write_text("".join(clean))


def certificate_files(ctx: Context, staging: bool):
    _, data = ctx.acme_paths(staging)
    source = data / (ctx.host + "_ecc")
    return source / "fullchain.cer", source / (ctx.host + ".key"), source / (ctx.host + ".cer"), source / "ca.cer"


def validate_certificate(ctx, staging):
    fullchain, key, leaf, chain = certificate_files(ctx, staging)
    for path in (fullchain, key, leaf, chain):
        private_file(path)
    run(["openssl", "x509", "-in", str(leaf), "-noout", "-checkhost", ctx.host])
    run(["openssl", "x509", "-in", str(leaf), "-noout", "-checkend", "604800"])
    public_cert = run(["openssl", "x509", "-in", str(leaf), "-pubkey", "-noout"], capture=True)
    public_key = run(["openssl", "pkey", "-in", str(key), "-pubout"], capture=True)
    if public_cert != public_key:
        raise Problem("certificate_key_mismatch")
    if not staging:
        run(["openssl", "verify", "-purpose", "sslserver", "-verify_hostname", ctx.host,
             "-CApath", "/etc/ssl/certs", "-untrusted", str(chain), str(leaf)])
    return fullchain, key


def local_tls(host, expected_der):
    deadline = time.monotonic() + 10
    while True:
        try:
            with socket.create_connection(("127.0.0.1", 18443), timeout=2) as raw:
                with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as tls:
                    if tls.getpeercert(binary_form=True) != expected_der:
                        raise Problem("https_certificate_not_rotated")
                    return
        except (OSError, Problem):
            if time.monotonic() >= deadline:
                raise Problem("https_certificate_not_served") from None
            time.sleep(0.25)


def wait_certificate_proxy():
    deadline = time.monotonic() + 10
    while True:
        try:
            with socket.create_connection(("127.0.0.1", 17890), timeout=1):
                return
        except OSError:
            if time.monotonic() >= deadline:
                raise Problem("certificate_proxy_unavailable") from None
            time.sleep(0.25)


def activate(ctx, fullchain, key):
    """Atomically rotate a versioned cert pair; restore the old pair on failure."""
    base = private_dir(ctx.data / "certificates")
    releases = private_dir(base / "releases")
    release = private_dir(releases / hashlib.sha256(fullchain.read_bytes()).hexdigest())
    for source, name in ((fullchain, "fullchain.pem"), (key, "privkey.pem")):
        dest = release / name
        if dest.exists():
            if private_file(dest).read_bytes() != source.read_bytes():
                raise Problem("certificate_release_conflict")
        else:
            with dest.open("xb") as stream:
                stream.write(source.read_bytes())
            dest.chmod(0o600)
    current = base / "current"
    if current.exists() and not current.is_symlink():
        raise Problem("certificate_pointer_conflict")
    previous = os.readlink(current) if current.is_symlink() else None
    if previous and not (base / previous).resolve().is_relative_to(releases.resolve()):
        raise Problem("certificate_pointer_outside_releases")
    target = str(release.relative_to(base))
    if previous == target:
        return False
    pending = base / "current.next"
    if pending.exists() or pending.is_symlink():
        raise Problem("certificate_rotation_pending")
    pending.symlink_to(target)
    pending.replace(current)
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "vortocode-https.service"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if active:
        try:
            run(["systemctl", "--user", "restart", "vortocode-https.service"])
            first = fullchain.read_text().split("-----END CERTIFICATE-----", 1)[0] + "-----END CERTIFICATE-----"
            local_tls(ctx.host, ssl.PEM_cert_to_DER_cert(first))
        except Exception:
            if previous:
                pending.symlink_to(previous)
                pending.replace(current)
                subprocess.run(["systemctl", "--user", "restart", "vortocode-https.service"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                current.unlink()
                subprocess.run(["systemctl", "--user", "stop", "vortocode-https.service"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raise Problem("https_rotation_failed") from None
    return True


def issue(ctx, *, staging=False, renew=False, force=False):
    if force and (not staging or not renew):
        raise Problem("force_only_for_staging_renewal")
    private_file(ctx.acme)
    proxy = getattr(ctx, "proxy", "")
    if proxy:
        if proxy != CERTIFICATE_PROXY:
            raise Problem("certificate_proxy_not_allowed")
        wait_certificate_proxy()
    key, secret = ctx.read_credentials()
    config, data = ctx.acme_paths(staging)
    private_dir(config)
    private_dir(data)
    curl_home = private_dir(ctx.config / "curl")
    curl_file = curl_home / ".curlrc"
    curl_settings = "connect-timeout = 8\nmax-time = 30\nretry = 2\nretry-delay = 2\nretry-max-time = 90\n"
    if curl_file.exists():
        if private_file(curl_file).read_text() != curl_settings:
            raise Problem("certificate_curl_settings_changed")
    else:
        curl_file.write_text(curl_settings)
        curl_file.chmod(0o600)
    env = {"HOME": str(ctx.home), "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
           "CURL_HOME": str(curl_home), "Ali_Key": key, "Ali_Secret": secret}
    if proxy:
        env["https_proxy"] = proxy
    args = ["sh", str(ctx.acme), "--home", str(ctx.acme.parent),
            "--config-home", str(config), "--cert-home", str(data),
            "--server", STAGING if staging else PRODUCTION,
            "--renew" if renew else "--issue", "-d", ctx.host]
    if renew:
        args.append("--ecc")
    else:
        args.extend(["--dns", "dns_ali", "--keylength", "ec-256", "--dnssleep", "60"])
    if force:
        args.append("--force")
    try:
        result = run(args, env=env, allow_skip=renew)
    finally:
        scrub_cached_credentials(config)
    fullchain, private_key = validate_certificate(ctx, staging)
    rotated = False if staging else activate(ctx, fullchain, private_key)
    return {"ok": True, "staging": staging, "renewal_not_due": result == 2,
            "certificate_installed": not staging, "certificate_rotated": rotated,
            "public_https_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "staging", "staging-renew", "issue", "renew"))
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        ctx = Context()
        private_dir(ctx.config)
        private_dir(ctx.data)
        lock = ctx.config / "certificate.lock"
        if lock.exists():
            private_file(lock)
        with lock.open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Problem("certificate_operation_in_progress") from None
            if args.action == "inspect":
                ctx.read_credentials()
                result = {"ok": True, "hostname": ctx.host, "credentials_filled": True,
                          "acme_installed": ctx.acme.is_file(), "dns_permissions_verified": False,
                          "restricted_certificate_proxy": bool(ctx.proxy)}
            else:
                result = issue(ctx, staging=args.action in {"staging", "staging-renew"},
                               renew=args.action in {"renew", "staging-renew"},
                               force=args.action == "staging-renew")
        print(json.dumps(result))
        return 0
    except Problem as error:
        print(json.dumps({"ok": False, "error": str(error)}))
    except Exception:
        # Never stringify exceptions from credential parsers or subprocesses.
        print(json.dumps({"ok": False, "error": "certificate_operation_failed"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
