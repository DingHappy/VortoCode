"""Delivery trust boundaries; real device signing is checked separately on macOS."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("ios_delivery", Path(__file__).parents[2] / "scripts/build-ios-app.py")
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def profile():
    certificate = b"synthetic-public-certificate"
    return {"TeamIdentifier": ["TESTTEAM01"], "UUID": "test-profile", "ProvisionedDevices": ["synthetic-device"],
            "ExpirationDate": datetime.now(timezone.utc) + timedelta(days=1), "DeveloperCertificates": [certificate],
            "Entitlements": {"application-identifier": "TESTTEAM01.*", "get-task-allow": True}}, {hashlib.sha1(certificate).hexdigest().upper()}


def metadata():
    return {"CFBundleIdentifier": delivery.BUNDLE_ID, "CFBundlePackageType": "APPL", "CFBundleSupportedPlatforms": ["iPhoneOS"],
            "DTPlatformName": "iphoneos", "CFBundleShortVersionString": "0.2.0", "CFBundleVersion": "1",
            "CFBundleURLTypes": [{"CFBundleURLSchemes": ["vortocode-mobile-auth"]}],
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True}}


@pytest.mark.parametrize("change", ["expired", "distribution", "wrong_app", "no_devices", "wrong_certificate"])
def test_ineligible_local_profiles_are_rejected(change):
    data, identities = profile()
    if change == "expired": data["ExpirationDate"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    if change == "distribution": data["Entitlements"]["get-task-allow"] = False
    if change == "wrong_app": data["Entitlements"]["application-identifier"] = "TESTTEAM01.com.other.app"
    if change == "no_devices": data["ProvisionedDevices"] = []
    if change == "wrong_certificate": identities = {"0" * 40}
    assert delivery.matching_profile(data, identities) is None


def test_matching_development_profile_accepts_wildcard_or_exact_bundle():
    data, identities = profile()
    assert delivery.matching_profile(data, identities)["uuid"] == "test-profile"
    data["Entitlements"]["application-identifier"] = "TESTTEAM01." + delivery.BUNDLE_ID
    assert delivery.matching_profile(data, identities)


@pytest.mark.parametrize("change", ["simulator", "wrong_bundle", "insecure_transport", "wrong_callback"])
def test_invalid_device_metadata_fails(change):
    data = metadata()
    if change == "simulator": data["CFBundleSupportedPlatforms"] = ["iPhoneSimulator"]
    if change == "wrong_bundle": data["CFBundleIdentifier"] = "com.other.app"
    if change == "insecure_transport": data["NSAppTransportSecurity"]["NSAllowsArbitraryLoads"] = True
    if change == "wrong_callback": data["CFBundleURLTypes"][0]["CFBundleURLSchemes"].append("other-app")
    with pytest.raises(ValueError): delivery.validate_metadata(data)


def test_device_metadata_preserves_local_networking_without_global_exception():
    delivery.validate_metadata(metadata())


def receipt_bundle(tmp_path, monkeypatch):
    directory = tmp_path.resolve()
    app = directory / delivery.APP_PATH
    app.mkdir(parents=True)
    (app / "binary").write_bytes(b"synthetic-binary")
    bundle = {"signing": "unsigned", "version": "0.2.0"}
    source = {"sha256": "synthetic-source"}
    receipt = {"schema_version": 1, "app_path": delivery.APP_PATH.as_posix(), "files": delivery.bundle_hashes(app),
               "bundle": bundle, "source": source}
    (directory / "receipt.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(delivery, "inspect_bundle", lambda *args: bundle)
    monkeypatch.setattr(delivery, "source_state", lambda: source)
    return directory, app


@pytest.mark.parametrize("change", ["modified", "added", "deleted"])
def test_receipt_detects_modified_added_and_deleted_resources(tmp_path, monkeypatch, change):
    directory, app = receipt_bundle(tmp_path, monkeypatch)
    assert delivery.verify(directory)["verified"]
    if change == "modified": (app / "binary").write_bytes(b"changed")
    if change == "added": (app / "injected").write_bytes(b"extra")
    if change == "deleted": (app / "binary").unlink()
    with pytest.raises(ValueError, match="changed after"): delivery.verify(directory)


def test_current_source_gate_does_not_claim_device_or_online_acceptance(tmp_path, monkeypatch):
    directory, _ = receipt_bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(delivery, "source_state", lambda: {"sha256": "new-source"})
    result = delivery.verify(directory)
    assert not result["source_matches"]
    assert not result["device_installation_verified"]
    assert not result["online_connection_verified"]
    with pytest.raises(ValueError, match="Current source differs"):
        delivery.verify(directory, current_source=True)


def test_changed_source_during_build_prevents_receipt(tmp_path, monkeypatch):
    states = iter([{"sha256": "before"}, {"sha256": "after"}])
    monkeypatch.setattr(delivery, "source_state", lambda: next(states))
    monkeypatch.setattr(delivery.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    output = tmp_path / "new-build"
    with pytest.raises(ValueError, match="Source changed during"):
        delivery.archive(output, False)
    assert not (output / "receipt.json").exists()


def test_bundle_symlink_cannot_escape_hash_coverage(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    outside = tmp_path / "outside"; outside.write_bytes(b"private")
    (app / "linked").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"): delivery.bundle_hashes(app)
