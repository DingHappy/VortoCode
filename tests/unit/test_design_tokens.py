"""设计变量只有一个出处（ADR-017）：design/tokens.json 生成 Desktop 的 tokens.css 和 iPhone 的 DesignTokens.swift。"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _module():
    spec = importlib.util.spec_from_file_location("design_tokens", ROOT / "scripts" / "design_tokens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_files_are_up_to_date():
    # 有人手改了 tokens.css / DesignTokens.swift，或改了 JSON 没重新生成，这里会红
    assert _module().main(["--check"]) == 0


def test_css_and_swift_carry_the_same_colors():
    tokens = _module()
    data = tokens.load()
    css = tokens.render_css(data)
    swift = tokens.render_swift(data)
    root = css[css.index(":root {"):css.index("}")]
    for group in data["groups"]:
        for name, value in group["tokens"].items():
            light = value["light"] if isinstance(value, dict) else value
            assert f"--vc-{name}: {light};" in root
            rgba = tokens._rgba(light)
            if rgba is not None:
                assert re.search(rf"static let {tokens._camel(name)} = ThemeColor\(light: RGBA\(", swift), name
    # 两份深色块完全一样：跟随系统和手动选深色不能出现两套值
    dark_system = css[css.index(':root:not([data-theme="light"]) {'):]
    dark_system = dark_system[:dark_system.index("}")]
    dark_manual = css[css.index(':root[data-theme="dark"] {'):]
    dark_manual = dark_manual[:dark_manual.index("}")]
    assert dark_system.split("{", 1)[1].split() == dark_manual.split("{", 1)[1].split()


@pytest.mark.parametrize("patch", [{"version": 2}, {"groups": "x"}])
def test_bad_source_is_rejected(tmp_path, patch):
    data = json.loads((ROOT / "design" / "tokens.json").read_text(encoding="utf-8"))
    data.update(patch)
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        _module().load(path)


def test_duplicate_or_partial_theme_values_are_rejected(tmp_path):
    data = json.loads((ROOT / "design" / "tokens.json").read_text(encoding="utf-8"))
    data["groups"][0]["tokens"]["bg"] = {"light": "#ffffff"}
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        _module().load(path)
