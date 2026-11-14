"""Offline inventory privacy and explicit-source limits, never execute input commands."""

import json
import os
import plistlib
import stat

import pytest

from src.cli_migration import inventory, verify_report, write_report, MAX_FILE_BYTES


def test_inventory_redacts_arguments_environment_and_prompts_but_keeps_disabled_jobs(tmp_path):
    source = tmp_path / "cron.yaml"
    source.write_text("""jobs:
  - enabled: false
    command: 'vc digest --limit 4'
  - command: 'vc server --host PRIVATE_HOST --port 1234'
  - command: 'vc pipeline review sample --approve --review-token PRIVATE_TOKEN'
  - prompt: 'PRIVATE_BODY ask vc agent to run it'
""")
    before = source.read_bytes()
    report = inventory([source])
    text = json.dumps(report)
    assert report["complete_for_declared_inputs"]
    assert report["legacy_references"] == 4
    assert report["manual_reviews"] == 1
    assert all(value not in text for value in ("PRIVATE_HOST", "PRIVATE_TOKEN", "PRIVATE_BODY"))
    assert report["sources"][0]["references"][0]["replacement_entry"] == "vortoone-runtime"
    assert report["retirement_authorized"] is False
    assert report["runtime_migration_verified"] is False
    assert source.read_bytes() == before


def test_systemd_direct_command_has_a_candidate_but_shell_and_old_agent_require_review(tmp_path):
    source = tmp_path / "assistant.service"
    source.write_text("""[Service]
Environment=API_TOKEN=PRIVATE_TOKEN
ExecStart=/opt/sample/bin/vc server --port 1234
ExecStartPre=/bin/sh -c 'vc digest; echo PRIVATE_TOKEN'
ExecReload=vc agent --yes PRIVATE_PROMPT
""")
    rows = inventory([source])["sources"][0]["references"]
    assert [row["status"] for row in rows] == [
        "entry_replacement_candidate",
        "manual_review",
        "manual_review",
    ]
    assert rows[0]["replacement_entry"] == "/opt/sample/bin/vortoone-runtime"
    assert rows[0]["destination_verified"] is False
    assert "PRIVATE_" not in json.dumps(rows)


@pytest.mark.parametrize("binary", [False, True])
def test_launchd_inventory_reads_program_arguments_and_ignores_environment_values(tmp_path, binary):
    source = tmp_path / "assistant.plist"
    source.write_bytes(
        plistlib.dumps(
            {
                "ProgramArguments": ["/opt/sample/bin/vortocode", "digest", "-n", "3"],
                "EnvironmentVariables": {"TOKEN": "PRIVATE_TOKEN vc agent"},
            },
            fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML,
        )
    )
    report = inventory([source])
    assert report["legacy_references"] == 1
    assert report["sources"][0]["references"][0]["command"] == "digest"
    assert "PRIVATE_TOKEN" not in json.dumps(report)


def test_scripts_modules_and_shell_constructs_are_not_rewritten_or_executed(tmp_path):
    source = tmp_path / "jobs.sh"
    source.write_text(
        "# vc digest\npython -m main server\nvc digest | cat\nvc digest $(touch NEVER_EXECUTED)\ncodex --version\nclaude --version\nopencode --version\n"
    )
    report = inventory([source])
    assert report["legacy_references"] == report["manual_reviews"] == 3
    assert not (tmp_path / "NEVER_EXECUTED").exists()


def test_report_is_stable_and_any_source_or_directory_membership_change_invalidates_it(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    source = sources / "jobs.sh"
    source.write_text("vc digest\n")
    first = inventory([sources])
    assert inventory([sources]) == first
    report = tmp_path / "review.json"
    write_report(report, first)
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert verify_report(report)["unchanged"] is True
    with pytest.raises(FileExistsError):
        write_report(report, first)
    source.write_text("vc collect\n")
    assert verify_report(report)["unchanged"] is False
    source.write_text("vc digest\n")
    (sources / "another.sh").write_text("vc stats\n")
    assert verify_report(report)["unchanged"] is False


def test_missing_unparseable_large_symlink_and_fifo_sources_never_report_a_clean_inventory(
    tmp_path,
):
    missing = tmp_path / "missing.service"
    broken = tmp_path / "broken.plist"
    broken.write_text("PRIVATE_TOKEN <not-valid")
    large = tmp_path / "large.sh"
    large.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    link = tmp_path / "link.sh"
    link.symlink_to(large)
    fifo = tmp_path / "pipe.sh"
    os.mkfifo(fifo)
    report = inventory([missing, broken, large, link, fifo])
    assert report["complete_for_declared_inputs"] is False
    assert report["problems"]
    assert "PRIVATE_TOKEN" not in json.dumps(report)


def test_tampered_report_and_invalid_inputs_are_rejected(tmp_path):
    source = tmp_path / "jobs.sh"
    source.write_text("vc digest\n")
    saved = inventory([source])
    saved["retirement_authorized"] = True
    path = tmp_path / "review.json"
    write_report(path, saved)
    with pytest.raises(ValueError, match="invalid_report"):
        verify_report(path)
    with pytest.raises(ValueError):
        inventory([])


def test_product_directory_names_do_not_turn_internal_or_third_party_executors_into_legacy_cli(
    tmp_path,
):
    source = tmp_path / "assistant.plist"
    for argv in (
        ["/opt/vortocode/bin/python3", "-m", "src.runtime_entry", "server"],
        [
            "/opt/vortocode/vortocode-runtime",
            "resident-server",
            "--config",
            "/private/example.json",
        ],
        ["codex", "--cd", "/opt/vortocode/project"],
    ):
        source.write_bytes(plistlib.dumps({"ProgramArguments": argv}))
        assert inventory([source])["legacy_references"] == 0
    source.write_bytes(
        plistlib.dumps({"ProgramArguments": ["/opt/vortocode/bin/python3", "-m", "main", "server"]})
    )
    row = inventory([source])["sources"][0]["references"][0]
    assert row["status"] == "manual_review" and row["command"] == "server"


def test_inventory_remains_useful_before_the_internal_runner_is_installed(tmp_path, monkeypatch):
    import sys

    source = tmp_path / "jobs.sh"
    source.write_text("vc digest\n")
    monkeypatch.setitem(sys.modules, "src.runtime_entry", None)
    row = inventory([source])["sources"][0]["references"][0]
    assert row["command"] == "digest"
    assert row["reason"] == "internal_entry_unavailable"


def test_reference_and_file_limits_are_explicitly_incomplete(tmp_path, monkeypatch):
    import src.cli_migration as migration

    source = tmp_path / "jobs.sh"
    source.write_text("vc digest\n" * 129)
    assert inventory([source])["complete_for_declared_inputs"] is False
    source.write_text("vc digest\n")
    (tmp_path / "second.sh").write_text("vc stats\n")
    monkeypatch.setattr(migration, "MAX_FILES", 1)
    report = inventory([tmp_path])
    assert report["complete_for_declared_inputs"] is False
    assert len(report["sources"]) == 1


@pytest.mark.parametrize("arguments", ["vc digest", 123, ["vc", 123]])
def test_malformed_launch_arguments_are_incomplete_not_a_clean_inventory(tmp_path, arguments):
    source = tmp_path / "assistant.plist"
    source.write_bytes(plistlib.dumps({"ProgramArguments": arguments}))
    assert inventory([source])["complete_for_declared_inputs"] is False


def test_directory_inventory_stops_reading_after_its_entry_limit(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import src.cli_migration as migration

    reads = []

    @contextmanager
    def entries(path):
        def stream():
            for index in range(1000):
                reads.append(index)
                assert len(reads) <= migration.MAX_FILES * 4 + 1
                yield type("Entry", (), {"name": f"unused-{index}.txt"})()

        yield stream()

    monkeypatch.setattr(migration.os, "scandir", entries)
    report = inventory([tmp_path])
    assert report["complete_for_declared_inputs"] is False
    assert len(reads) == migration.MAX_FILES * 4 + 1
