"""Workflow configuration fails closed before any browser or application is launched."""
import pytest

from src.browser.verify import run_browser_probe
from src.browser.workflow import normalize_steps
from src.agents.verify_profiles import load_verify_profiles


@pytest.mark.parametrize("steps", [
    [], {}, ["click"], [{"action": {}}], [{"action": "evaluate", "value": "alert(1)"}],
    [{"action": "click", "target": {"role": "button"}}],
    [{"action": "assert_text", "target": {"text": "a"}}],
    [{"action": "assert_visible", "target": {"role": "button", "text": "a"}}],
    [{"action": "assert_visible", "target": {"css": "#a"}}],
    [{"action": "assert_count", "target": {"role": "button"}, "value": True}],
    [{"action": "assert_visible", "target": {"text": "a"}, "typo": True}],
])
def test_invalid_workflows_do_not_degrade_to_page_health(steps, tmp_path):
    with pytest.raises(ValueError):
        normalize_steps(steps)
    result = run_browser_probe({"url": "http://127.0.0.1:1234", "steps": steps}, tmp_path / "x.png")
    assert not result["ok"] and "browser.steps" in result["output"]
    assert result["screenshot_path"] == result["trace_path"] == ""


def test_profile_preserves_steps_and_rejects_an_action_without_assertions(tmp_path):
    import yaml
    path = tmp_path / ".vortocode" / "verify.yaml"
    path.parent.mkdir()
    steps = [{"action": "fill", "target": {"label": "标题"}, "value": "任务"},
             {"action": "click", "target": {"role": "button", "name": "保存"}},
             {"action": "reload"},
             {"action": "assert_visible", "target": {"text": "任务"}}]
    profile = {"serve": "python server.py", "browser": {"url": "http://127.0.0.1:3000", "steps": steps}}
    path.write_text(yaml.safe_dump({"profiles": {"save": profile}}))
    result = load_verify_profiles(str(tmp_path))
    assert result["ok"] and result["profiles"]["save"]["browser"]["steps"] == steps
    profile["browser"]["steps"] = steps[:-1]
    path.write_text(yaml.safe_dump({"profiles": {"save": profile}}))
    assert not load_verify_profiles(str(tmp_path))["ok"]
