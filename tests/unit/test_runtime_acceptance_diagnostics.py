"""Sampling coverage and recovery evidence cannot be inferred from totals alone."""
import json

import pytest

from src.gateway.runtime_acceptance import read_report


def records(count=4):
    start = {"type": "start", "duration_seconds": count, "interval_seconds": 1,
             "targets": ["assistant", "code"]}
    samples = [{"type": "sample", "elapsed_seconds": index, "ok": True,
                "targets": {name: {"healthy": True, "problems": []} for name in start["targets"]}}
               for index in range(count)]
    summary = {"type": "summary", "elapsed_seconds": count, "completed": True,
               "coverage_complete": True, "samples": count, "unhealthy_samples": 0}
    return [start, *samples, summary]


def report(tmp_path, rows):
    path = tmp_path / "synthetic.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return read_report(path)


def fail(rows, index, target="assistant", problem="im_disconnected"):
    rows[index + 1]["ok"] = False
    rows[index + 1]["targets"][target]["problems"] = [problem]
    if problem == "health_unhealthy":
        rows[index + 1]["targets"][target]["healthy"] = False
    elif problem.startswith("health_"):
        rows[index + 1]["targets"][target].pop("healthy", None)
    rows[-1]["unhealthy_samples"] = sum(row.get("ok") is False for row in rows[1:-1])


def test_consecutive_bad_samples_form_one_recovered_incident(tmp_path):
    rows = records()
    fail(rows, 1)
    fail(rows, 2)
    result = report(tmp_path, rows)
    assert result["state"] == "failed"  # Recovery never erases the earlier outage.
    assert result["unhealthy_samples"] == 2 and result["incident_count"] == 1
    assert result["recovered_incidents"] == 1 and result["open_incidents"] == 0
    assert result["problem_counts"] == {"assistant": {"im_disconnected": 2}}
    assert result["incidents"] == [{"target": "assistant", "problems": ["im_disconnected"],
        "first_bad_elapsed_seconds": 1, "last_bad_elapsed_seconds": 2,
        "next_healthy_elapsed_seconds": 3, "affected_samples": 2}]
    assert result["full_task_loop_verified"] is False


def test_recovery_is_specific_to_each_target(tmp_path):
    rows = records()
    fail(rows, 0)
    fail(rows, 1, "code", "health_http_503")
    fail(rows, 2)
    fail(rows, 3)
    result = report(tmp_path, rows)
    assert result["incident_count"] == 3 and result["recovered_incidents"] == 2
    assert result["open_incidents"] == 1
    assert result["incidents"][-1]["next_healthy_elapsed_seconds"] is None


def test_error_changes_are_not_mistaken_for_recovery(tmp_path):
    rows = records()
    fail(rows, 0)
    fail(rows, 1, problem="im_TimeoutError")
    fail(rows, 2, problem="im_delivery_pending")
    result = report(tmp_path, rows)
    assert result["incident_count"] == result["recovered_incidents"] == 1
    assert result["incidents"][0]["next_healthy_elapsed_seconds"] == 3
    assert result["incidents"][0]["affected_samples"] == 3
    assert result["incidents"][0]["problems"] == ["im_TimeoutError", "im_delivery_pending", "im_disconnected"]


@pytest.mark.parametrize("mutation", [
    "duplicate_slot", "missing_target", "extra_target", "backwards", "nan_sample",
    "infinite_sample", "out_of_plan", "contradictory_ok", "infinite_summary", "nan_summary",
    "wrong_unhealthy_count", "bool_count", "unknown_record", "extra_sample",
    "false_health_flag", "missing_health_flag",
])
def test_invalid_coverage_cannot_pass_even_with_a_complete_summary(tmp_path, mutation):
    rows = records()
    if mutation == "duplicate_slot":
        rows[2]["elapsed_seconds"] = 0.5
    elif mutation == "missing_target":
        del rows[2]["targets"]["code"]
    elif mutation == "extra_target":
        rows[2]["targets"]["private-target"] = {"problems": []}
    elif mutation == "backwards":
        rows[2], rows[3] = rows[3], rows[2]
    elif mutation in {"nan_sample", "infinite_sample"}:
        rows[2]["elapsed_seconds"] = float("nan" if mutation == "nan_sample" else "inf")
    elif mutation == "out_of_plan":
        rows[4]["elapsed_seconds"] = 10
    elif mutation == "contradictory_ok":
        rows[2]["targets"]["assistant"]["problems"] = ["im_disconnected"]
    elif mutation == "false_health_flag":
        rows[2]["targets"]["assistant"]["healthy"] = False
    elif mutation == "missing_health_flag":
        del rows[2]["targets"]["assistant"]["healthy"]
    elif mutation in {"infinite_summary", "nan_summary"}:
        rows[-1]["elapsed_seconds"] = float("inf" if mutation == "infinite_summary" else "nan")
    elif mutation == "wrong_unhealthy_count":
        rows[-1]["unhealthy_samples"] = 1
    elif mutation == "bool_count":
        rows[-1]["samples"] = True
    elif mutation == "unknown_record":
        rows.insert(-1, {"type": "unexpected"})
    elif mutation == "extra_sample":
        rows.insert(-1, rows[1])
        rows[-1]["samples"] += 1
    assert report(tmp_path, rows)["state"] == "incomplete"


def test_incomplete_and_truncated_reports_still_explain_observed_recovery(tmp_path):
    rows = records()
    fail(rows, 1)
    result = report(tmp_path, rows[:-1])
    assert result["state"] == "incomplete" and result["recovered_incidents"] == 1
    path = tmp_path / "synthetic.jsonl"
    with path.open("a") as handle:
        handle.write('{"type":')
    result = read_report(path)
    assert result["state"] == "incomplete" and result["full_task_loop_verified"] is False
    assert result["recovered_incidents"] == 1


def test_pending_future_slots_are_distinct_from_gaps_in_observed_samples(tmp_path):
    rows = records()
    result = report(tmp_path, rows[:3])
    assert result["state"] == "incomplete"
    assert result["missing_sample_slots"] == 0 and result["unobserved_sample_slots"] == 2
    result = report(tmp_path, [rows[0], rows[1], rows[3]])
    assert result["state"] == "incomplete"
    assert result["missing_sample_slots"] == result["unobserved_sample_slots"] == 1


def test_unknown_error_payload_is_not_echoed_in_diagnostics(tmp_path):
    rows = records()
    fail(rows, 1, problem="synthetic-private-token https://example.invalid/private")
    result = report(tmp_path, rows)
    assert result["state"] == "failed"
    assert "synthetic-private-token" not in json.dumps(result)
    assert "example.invalid" not in json.dumps(result)
    assert result["problem_counts"] == {"assistant": {"probe_error": 1}}


def test_incident_details_are_bounded_without_losing_total_counts(tmp_path):
    rows = records(50)
    for index in range(0, 50, 2):
        fail(rows, index)
    result = report(tmp_path, rows)
    assert result["incident_count"] == result["recovered_incidents"] == 25
    assert len(result["incidents"]) == 20 and result["incidents_truncated"] is True
