#!/usr/bin/env python3
"""从 design/tokens.json 生成两端的设计变量（docs/DESIGN_SYSTEM.md 第 5 节、ADR-017）。

- Desktop：desktop/src/styles/tokens.css（--vc-* CSS 变量，浅色 + 两份深色块）
- iPhone：mobile/ios/Packages/MobileCore/Sources/MobileCore/DesignTokens.swift

改变量只改 JSON，然后运行 ``python scripts/design_tokens.py``。
``--check`` 只比对不写入：生成结果和仓库里的文件不一致时退出码为 1（测试与 CI 用）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "design" / "tokens.json"
CSS_OUT = ROOT / "desktop" / "src" / "styles" / "tokens.css"
SWIFT_OUT = ROOT / "mobile" / "ios" / "Packages" / "MobileCore" / "Sources" / "MobileCore" / "DesignTokens.swift"

CSS_HEADER = """/* ── 设计变量（docs/DESIGN_SYSTEM.md 第 5 节）──────────────────────────────
   由 design/tokens.json 生成（python scripts/design_tokens.py），不要手改；iPhone 用同一份生成 DesignTokens.swift。
   新写和迁移过的界面只用这里的 --vc-* 变量，不写死颜色、字号、圆角。
   颜色按用途命名，深色模式按同一组用途重新取值：跟随系统时走 prefers-color-scheme，
   用户手动选了浅色/深色时由 <html data-theme> 决定（见 lib/theme.ts）。
   旧样式里的 --n*、--surface-* 等变量在对应界面迁移完之前保持不动。 */
"""

# Swift 侧按分组输出成枚举；Desktop 专用的布局宽度、字体栈、阴影和渐变不进手机端。
SWIFT_NUMERIC_GROUPS = {
    "字号": ("VCFontSize", "size-"),
    "字重": ("VCFontWeight", "weight-"),
    "间距": ("VCSpace", ""),
    "圆角": ("VCRadius", "radius-"),
    "图标与控件尺寸": ("VCControl", ""),
}

_HEX = re.compile(r"^#([0-9a-fA-F]{6})$")
_RGBA = re.compile(r"^rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9.]+)\s*\)$")


def load(path: Path = SOURCE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("groups"), list):
        raise ValueError("design/tokens.json 版本或结构不对")
    names: set[str] = set()
    for group in data["groups"]:
        for name, value in group["tokens"].items():
            if not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in names:
                raise ValueError(f"变量名不合法或重复：{name}")
            names.add(name)
            if isinstance(value, dict) and set(value) != {"light", "dark"}:
                raise ValueError(f"{name} 的主题值只能有 light 和 dark")
    return data


def _light(value) -> str:
    return value["light"] if isinstance(value, dict) else value


def render_css(data: dict) -> str:
    lines = [CSS_HEADER, ":root {"]
    for index, group in enumerate(data["groups"]):
        if index:
            lines.append("")
        lines.append(f"  /* {group['title']} */")
        for name, value in group["tokens"].items():
            lines.append(f"  --vc-{name}: {_light(value)};")
    lines.append("}")
    dark = [(name, value["dark"]) for group in data["groups"]
            for name, value in group["tokens"].items() if isinstance(value, dict)]
    lines += ["", "@media (prefers-color-scheme: dark) {", '  :root:not([data-theme="light"]) {']
    lines += [f"    --vc-{name}: {value};" for name, value in dark]
    lines += ["  }", "}", "", ':root[data-theme="dark"] {']
    lines += [f"  --vc-{name}: {value};" for name, value in dark]
    lines.append("}")
    return "\n".join(lines) + "\n"


def _rgba(value: str) -> tuple[float, float, float, float] | None:
    if m := _HEX.match(value):
        raw = m.group(1)
        return (int(raw[0:2], 16) / 255, int(raw[2:4], 16) / 255, int(raw[4:6], 16) / 255, 1.0)
    if m := _RGBA.match(value):
        return (int(m.group(1)) / 255, int(m.group(2)) / 255, int(m.group(3)) / 255, float(m.group(4)))
    return None


def _camel(name: str) -> str:
    head, *rest = name.split("-")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _num(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))


def _rgba_swift(c: tuple[float, float, float, float]) -> str:
    return f"RGBA({_num(round(c[0], 4))}, {_num(round(c[1], 4))}, {_num(round(c[2], 4))}, {_num(c[3])})"


def render_swift(data: dict) -> str:
    out = [
        "// 由 design/tokens.json 生成（python scripts/design_tokens.py），不要手改。",
        "// 和 Desktop 的 desktop/src/styles/tokens.css 出自同一份定义（ADR-017）。",
        "// 只有纯色、字号、字重、间距、圆角和控件尺寸；Desktop 专用的布局宽度、字体栈、阴影、渐变不在这里。",
        "",
        "/// 一个颜色在浅色和深色下的取值（sRGB，0–1）。SwiftUI 的 `Color` 见 DesignTokens+SwiftUI.swift。",
        "public struct RGBA: Sendable, Equatable {",
        "    public let red: Double, green: Double, blue: Double, alpha: Double",
        "    public init(_ red: Double, _ green: Double, _ blue: Double, _ alpha: Double = 1) {",
        "        self.red = red; self.green = green; self.blue = blue; self.alpha = alpha",
        "    }",
        "}",
        "",
        "public struct ThemeColor: Sendable, Equatable {",
        "    public let light: RGBA, dark: RGBA",
        "    public init(light: RGBA, dark: RGBA) { self.light = light; self.dark = dark }",
        "}",
        "",
        "public enum VCColor {",
    ]
    for group in data["groups"]:
        colors = []
        for name, value in group["tokens"].items():
            light = _rgba(_light(value))
            dark = _rgba(value["dark"]) if isinstance(value, dict) else light
            if light is not None and dark is not None:
                colors.append((name, light, dark))
        if colors:
            out.append(f"    // {group['title']}")
            for name, light, dark in colors:
                out.append(f"    public static let {_camel(name)} = ThemeColor(light: {_rgba_swift(light)}, dark: {_rgba_swift(dark)})")
    out.append("}")
    for group in data["groups"]:
        key = next((k for k in SWIFT_NUMERIC_GROUPS if group["title"].startswith(k)), None)
        if key is None:
            continue
        enum, prefix = SWIFT_NUMERIC_GROUPS[key]
        out += ["", f"/// {group['title']}", f"public enum {enum} {{"]
        for name, value in group["tokens"].items():
            raw = _light(value)
            m = re.fullmatch(r"(\d+(?:\.\d+)?)(px)?", raw)
            if not m:
                continue
            ident = _camel(name[len(prefix):] if prefix and name.startswith(prefix) else name)
            out.append(f"    public static let {ident}: Double = {_num(float(m.group(1)))}")
        out.append("}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args(argv)
    data = load()
    outputs = {CSS_OUT: render_css(data), SWIFT_OUT: render_swift(data)}
    stale = [path for path, text in outputs.items()
             if not path.is_file() or path.read_text(encoding="utf-8") != text]
    if args.check:
        for path in stale:
            print(f"过期：{path.relative_to(ROOT)}（运行 python scripts/design_tokens.py 重新生成）", file=sys.stderr)
        return 1 if stale else 0
    for path in stale:
        path.write_text(outputs[path], encoding="utf-8")
        print(f"已生成 {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
