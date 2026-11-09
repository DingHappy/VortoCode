"""TestFlight 发布脚本（ADR-017 M4）：上传前的硬性核对。只测纯逻辑，不联系 Apple、不跑 xcodebuild。"""
import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path
import struct
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEAM = "ABCDE12345"


def _module():
    spec = importlib.util.spec_from_file_location("ios_testflight", ROOT / "scripts" / "ios-testflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png(color_type: int, extra: bytes = b"") -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + extra + chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b"")


def test_the_committed_app_icon_has_no_alpha():
    tf = _module()
    assert tf.png_is_opaque(tf.ICON.read_bytes())
    assert not tf.png_is_opaque(_png(6))                                   # RGBA
    assert not tf.png_is_opaque(_png(2, b"\0\0\0\x06tRNS\0\0\0\0\0\0" + b"\0\0\0\0"))   # RGB + 透明色
    assert not tf.png_is_opaque(b"not a png")


def test_credentials_are_validated_and_never_from_the_repo(tmp_path):
    tf = _module()
    key = tmp_path / "AuthKey_KEY1234567.p8"
    key.write_text("-----BEGIN PRIVATE KEY-----\n")
    env = {"ASC_KEY_ID": "KEY1234567", "ASC_ISSUER_ID": "69a6de7e-1111-2222-3333-444455556666"}
    with pytest.raises(tf.Refused, match="权限"):
        os.chmod(key, 0o644)
        tf.credentials(env, key_dir=tmp_path)
    os.chmod(key, 0o600)
    assert tf.credentials(env, key_dir=tmp_path)["path"] == key
    for bad in ({**env, "ASC_KEY_ID": "short"}, {**env, "ASC_ISSUER_ID": "nope"}, {}):
        with pytest.raises(tf.Refused):
            tf.credentials(bad, key_dir=tmp_path)
    with pytest.raises(tf.Refused, match="找不到"):
        tf.credentials({**env, "ASC_KEY_ID": "OTHER12345"}, key_dir=tmp_path)


def test_team_comes_from_one_exact_profile():
    tf = _module()
    exact = {"TeamIdentifier": [TEAM], "Entitlements": {"application-identifier": f"{TEAM}.com.vortoone.mobile"}}
    wildcard = {"TeamIdentifier": ["ZZZZZ99999"], "Entitlements": {"application-identifier": "ZZZZZ99999.*"}}
    assert tf.team_from_profiles([exact, wildcard, exact]) == TEAM
    with pytest.raises(tf.Refused):
        tf.team_from_profiles([wildcard])
    with pytest.raises(tf.Refused):
        other = {"TeamIdentifier": ["OTHER00000"], "Entitlements": {"application-identifier": "OTHER00000.com.vortoone.mobile"}}
        tf.team_from_profiles([exact, other])
    assert tf.team_id({"IOS_TEAM_ID": TEAM}) == TEAM


def test_only_a_clean_up_to_date_main_is_released():
    tf = _module()
    good = {"clean": True, "branch": "main", "commit": "abc", "on_main": True}
    assert tf.require_release_source(good) == "abc"
    for bad in ({**good, "clean": False}, {**good, "branch": "vorto/x"}, {**good, "on_main": False}):
        with pytest.raises(tf.Refused):
            tf.require_release_source(bad)


def test_build_numbers_increase_and_export_is_for_app_store():
    tf = _module()
    early = tf.build_number(datetime(2026, 11, 8, 9, 5, tzinfo=timezone.utc))
    late = tf.build_number(datetime(2026, 11, 8, 9, 6, tzinfo=timezone.utc))
    assert early == "202611080905" and int(late) > int(early)
    options = tf.export_options(TEAM)
    assert options["method"] == "app-store-connect" and options["destination"] == "export"
    assert options["manageAppVersionAndBuildNumber"] is False and options["teamID"] == TEAM


def _signed():
    shared = [f"{TEAM}.com.vortoone.mobile.shared"]
    info = {"CFBundleIdentifier": "com.vortoone.mobile", "CFBundleShortVersionString": "0.3.0",
            "CFBundleVersion": "202611080905", "ITSAppUsesNonExemptEncryption": False}
    app = {"application-identifier": f"{TEAM}.com.vortoone.mobile", "aps-environment": "production",
           "beta-reports-active": True, "keychain-access-groups": shared}
    ext = {"application-identifier": f"{TEAM}.com.vortoone.mobile.notification", "beta-reports-active": True,
           "keychain-access-groups": shared}
    profile = {"Entitlements": {"aps-environment": "production"}}
    return info, app, ext, profile


def test_upload_requires_production_push_and_a_store_signature():
    tf = _module()
    info, app, ext, profile = _signed()
    summary = tf.check_signed(info, app, ext, profile, TEAM)
    assert summary == {"bundle_id": "com.vortoone.mobile", "version": "0.3.0", "build": "202611080905",
                       "aps_environment": "production", "debuggable": False, "testflight": True}
    broken = [
        ({**info, "ITSAppUsesNonExemptEncryption": None}, app, ext, profile),
        (info, {**app, "aps-environment": "development"}, ext, profile),
        (info, {**app, "get-task-allow": True}, ext, profile),
        (info, app, {**ext, "get-task-allow": True}, profile),
        (info, {**app, "beta-reports-active": False}, ext, profile),
        (info, app, {**ext, "application-identifier": f"{TEAM}.com.other"}, profile),
        (info, {**app, "application-identifier": "ZZZZZ99999.com.vortoone.mobile"}, ext, profile),
        (info, app, {**ext, "keychain-access-groups": []}, profile),
        (info, app, ext, {**profile, "ProvisionedDevices": ["00008110-0001"]}),
        (info, app, ext, {"Entitlements": {"aps-environment": "development"}}),
        ({**info, "CFBundleVersion": "1.0.abc"}, app, ext, profile),
    ]
    for case in broken:
        with pytest.raises(tf.Refused):
            tf.check_signed(*case, TEAM)
