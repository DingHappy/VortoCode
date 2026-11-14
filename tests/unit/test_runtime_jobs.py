"""Internal operators retain existing job behavior, never a second execution authority."""

import json
from types import SimpleNamespace

import pytest

from src import runtime_entry, runtime_jobs


@pytest.mark.parametrize(
    "argv", [["cron", "run"], ["pipeline", "start"], ["pipeline", "review"], ["heartbeat", "start"]]
)
def test_invalid_job_commands_do_not_prepare_state(monkeypatch, argv):
    monkeypatch.setattr(
        runtime_entry, "prepare_runtime", lambda: pytest.fail("prepared invalid job")
    )
    with pytest.raises(SystemExit) as failure:
        runtime_entry.main(argv)
    assert failure.value.code == 2


@pytest.mark.parametrize(
    "command,argv,expected",
    [
        ("digest", ["-n", "7"], {"limit": 7}),
        ("collect", ["--quiet"], {"quiet": True}),
        ("stats", [], {"quiet": False}),
    ],
)
def test_internal_collectors_preserve_arguments_and_exit_codes(
    monkeypatch, command, argv, expected
):
    from src.workflow.pipeline import collect_cli

    calls = []
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: calls.append("prepare"))
    monkeypatch.setattr(collect_cli, f"run_{command}_cli", lambda **kw: calls.append(kw) or 3)
    assert runtime_entry.main([command, *argv]) == 3
    assert calls == ["prepare", expected]


def test_pipeline_preserves_review_identity_flags_and_existing_implementation(monkeypatch):
    from src.workflow.pipeline import pipeline_cli

    calls = []
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: None)

    async def run(*args, **options):
        calls.append((args, options))
        return 1

    monkeypatch.setattr(pipeline_cli, "run_pipeline_cli", run)
    assert (
        runtime_entry.main(
            [
                "pipeline",
                "review",
                "sample",
                "--reject",
                "--review-token",
                "reviewed-version",
                "--rollback-to",
                "draft",
                "--comment",
                "revise",
                "--yes",
                "--max-stages",
                "2",
                "--if-idle",
            ]
        )
        == 1
    )
    assert calls == [
        (
            ("review", "sample"),
            {
                "verdict": "reject",
                "comment": "revise",
                "rollback_to": "draft",
                "max_stages": 2,
                "yes": True,
                "if_idle": True,
                "review_token": "reviewed-version",
            },
        )
    ]


def test_internal_cron_run_uses_the_existing_scheduler_and_validated_name(
    tmp_path, monkeypatch, capsys
):
    from src.scheduling import cron

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: None)
    monkeypatch.setattr(cron, "load_jobs", lambda cwd: [SimpleNamespace(name="daily")])
    calls = []

    async def run(cwd, name):
        calls.append((cwd, name))
        return "existing result"

    monkeypatch.setattr(cron, "run_job_by_name", run)
    assert runtime_entry.main(["cron", "run", "daily"]) == 0
    assert calls == [(str(tmp_path), "daily")]
    assert "existing result" in capsys.readouterr().out


@pytest.mark.parametrize("scope", ["general", "scratch"])
def test_internal_heartbeat_does_not_expand_workspace_permissions(tmp_path, monkeypatch, scope):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", scope)
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: None)
    monkeypatch.setattr(
        runtime_jobs,
        "make_background_development_worker",
        lambda root: pytest.fail("created a worker"),
    )
    with pytest.raises(RuntimeError, match="Project"):
        runtime_entry.main(["heartbeat", "run"])


def test_internal_heartbeat_cannot_claim_work_owned_by_an_existing_service(tmp_path, monkeypatch):
    from src.gateway.process_lease import ProcessLease

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "project")
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: None)
    monkeypatch.setattr(
        runtime_jobs,
        "make_background_development_worker",
        lambda root: pytest.fail("created a worker"),
    )
    state = tmp_path / ".vortoone"
    state.mkdir()
    backlog = state / "BACKLOG.md"
    backlog.write_text("- [ ] untouched\n")
    with ProcessLease(state / "runtime.lock"), pytest.raises(RuntimeError, match="Another Runtime"):
        runtime_entry.main(["heartbeat", "run"])
    assert backlog.read_text() == "- [ ] untouched\n"


def test_inventory_and_verification_never_bootstrap_or_authorize_retirement(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: pytest.fail("prepared inventory"))
    source = tmp_path / "jobs.sh"
    source.write_text("vc digest\n")
    output = tmp_path / "review.json"
    assert (
        runtime_entry.main(["legacy-inventory", "--path", str(source), "--output", str(output)])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["retirement_authorized"] is False
    assert runtime_entry.main(["legacy-verify", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["runtime_migration_verified"] is False
    assert not (tmp_path / ".vortoone").exists()
    assert not (tmp_path / ".vortocode").exists()


def test_incomplete_inventory_is_not_a_successful_preflight_even_when_unchanged(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: pytest.fail("prepared inventory"))
    output = tmp_path / "review.json"
    assert (
        runtime_entry.main(
            ["legacy-inventory", "--path", str(tmp_path / "missing.sh"), "--output", str(output)]
        )
        == 2
    )
    capsys.readouterr()
    assert runtime_entry.main(["legacy-verify", str(output)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["unchanged"] and not result["complete_for_declared_inputs"]
