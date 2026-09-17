"""Small declarative browser workflows: user-facing locators and explicit assertions."""
from __future__ import annotations

import time

_ACTIONS = {"click", "fill", "press", "reload", "assert_visible", "assert_text", "assert_value",
            "assert_count", "assert_url"}
_LOCATORS = {"role", "label", "text", "test_id", "placeholder"}


def normalize_steps(raw) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not 1 <= len(raw) <= 40:
        raise ValueError("browser.steps 必须包含 1–40 个步骤")
    steps = []
    for index, value in enumerate(raw, 1):
        if (not isinstance(value, dict) or not isinstance(value.get("action"), str)
                or value["action"] not in _ACTIONS):
            raise ValueError(f"browser.steps 第 {index} 步 action 无效")
        action = value["action"]
        keys = {"action", "name"}
        if action not in {"reload", "assert_url"}:
            keys.add("target")
            target = value.get("target")
            if not isinstance(target, dict) or len(set(target) & _LOCATORS) != 1:
                raise ValueError(f"browser.steps 第 {index} 步必须指定唯一定位方式")
            method = next(iter(set(target) & _LOCATORS))
            allowed = {method, "name"} if method == "role" else {method}
            if set(target) - allowed or any(not isinstance(v, str) or not v for v in target.values()):
                raise ValueError(f"browser.steps 第 {index} 步 target 无效")
        if action in {"fill", "press", "assert_text", "assert_value", "assert_url", "assert_count"}:
            keys.add("value")
            expected = value.get("value")
            if action == "assert_count":
                valid = type(expected) is int and expected >= 0
            else:
                valid = isinstance(expected, str)
            if not valid:
                raise ValueError(f"browser.steps 第 {index} 步 value 无效")
        if set(value) - keys or ("name" in value and not isinstance(value["name"], str)):
            raise ValueError(f"browser.steps 第 {index} 步存在无效字段")
        steps.append(dict(value))
    if not any(s["action"].startswith("assert_") for s in steps):
        raise ValueError("browser.steps 至少需要一个明确断言，操作成功不能代表业务验收通过")
    return steps


def _locator(page, target: dict):
    if "role" in target:
        kwargs = {"name": target["name"], "exact": True} if "name" in target else {}
        return page.get_by_role(target["role"], **kwargs)
    if "test_id" in target:
        return page.get_by_test_id(target["test_id"])
    for method in ("label", "text", "placeholder"):
        if method in target:
            return getattr(page, f"get_by_{method}")(target[method], exact=True)
    raise ValueError("缺少定位方式")


def run_steps(page, steps: list[dict], deadline: float, results: list[dict]) -> str:
    """Stop at the first failure; return its reason while preserving step evidence."""
    from playwright.sync_api import expect

    for index, step in enumerate(steps, 1):
        started = time.monotonic()
        action = step["action"]
        row = {"index": index, "name": step.get("name") or action,
               "action": action, "ok": False, "error": ""}
        results.append(row)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("浏览器验收总时间已用尽")
            timeout = max(1, int(remaining * 1000))
            if action == "reload":
                response = page.reload(wait_until="load", timeout=timeout)
                if response is not None and response.status >= 400:
                    raise RuntimeError(f"刷新返回 HTTP {response.status}")
            elif action == "assert_url":
                expect(page).to_have_url(step["value"], timeout=timeout)
            else:
                target = _locator(page, step["target"])
                if action == "click":
                    target.click(timeout=timeout)
                elif action in {"fill", "press"}:
                    getattr(target, action)(step["value"], timeout=timeout)
                elif action == "assert_visible":
                    expect(target).to_be_visible(timeout=timeout)
                else:
                    method = {"assert_text": "to_have_text", "assert_value": "to_have_value",
                              "assert_count": "to_have_count"}[action]
                    getattr(expect(target), method)(step["value"], timeout=timeout)
            row["ok"] = True
        except Exception as exc:  # noqa: BLE001 - preserve assertion/timeout evidence
            row["error"] = str(exc)[:2000]
            return f"第 {index} 步「{row['name']}」失败: {row['error']}"
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return ""
