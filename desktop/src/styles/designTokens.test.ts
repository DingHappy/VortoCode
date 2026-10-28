import { describe, expect, it } from "vitest";
import tokensCss from "./tokens.css?raw";
import componentsCss from "./components.css?raw";
import shellCss from "./shell.css?raw";
import taskPillSource from "../components/TaskPill.tsx?raw";
import sidebarFooterSource from "../components/SidebarFooter.tsx?raw";

// 已按 docs/DESIGN_SYSTEM.md 迁移的文件：这里面不许写死颜色、字号、字重和圆角，一律用 --vc-* 变量。
// 每迁完一屏，把它的样式和组件文件加进来，检查范围就跟着扩大。
const MIGRATED: Record<string, string> = {
  "styles/components.css": componentsCss,
  "styles/shell.css": shellCss,
  "components/TaskPill.tsx": taskPillSource,
  "components/SidebarFooter.tsx": sidebarFooterSource,
  ...Object.fromEntries(
    Object.entries(
      import.meta.glob(["../components/ui/*.tsx", "!../components/ui/*.test.tsx"], { query: "?raw", import: "default", eager: true }) as Record<string, string>,
    ).map(([path, source]) => [path.replace("../", ""), source]),
  ),
};

function block(css: string, selector: string): Map<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`tokens.css 里找不到 ${selector}`);
  const end = css.indexOf("}", start);
  const vars = new Map<string, string>();
  for (const m of css.slice(start, end).matchAll(/(--vc-[\w-]+)\s*:\s*([^;]+);/g)) {
    vars.set(m[1], m[2].replace(/\s+/g, " ").trim());
  }
  return vars;
}

const light = block(tokensCss, ":root");
const darkSystem = block(tokensCss, ':root:not([data-theme="light"])');
const darkManual = block(tokensCss, ':root[data-theme="dark"]');

function isColorToken(name: string, value: string): boolean {
  return /#|rgba?\(/.test(value) && !name.startsWith("--vc-font");
}

describe("design tokens", () => {
  it("gives every color role a dark-mode value", () => {
    const colorRoles = [...light].filter(([name, value]) => isColorToken(name, value)).map(([name]) => name);
    expect(colorRoles.length).toBeGreaterThan(20);
    expect([...darkSystem.keys()].sort()).toEqual(colorRoles.sort());
  });

  it("keeps the system-dark and manual-dark blocks identical", () => {
    expect(Object.fromEntries(darkManual)).toEqual(Object.fromEntries(darkSystem));
  });

  it("uses only the 7-step type scale and 3 weights", () => {
    const sizes = [...light].filter(([name]) => name.startsWith("--vc-size-")).map(([, v]) => v);
    expect(sizes).toEqual(["34px", "22px", "18px", "15px", "14px", "13px", "12px"]);
    const weights = [...light].filter(([name]) => name.startsWith("--vc-weight-")).map(([, v]) => v);
    expect(weights).toEqual(["400", "500", "600"]);
  });

  it("only references variables that tokens.css defines", () => {
    for (const [file, source] of Object.entries(MIGRATED)) {
      for (const m of source.matchAll(/var\((--vc-[\w-]+)\)/g)) {
        expect(light.has(m[1]), `${file} 用了未定义的 ${m[1]}`).toBe(true);
      }
    }
  });
});

describe("migrated files", () => {
  const RULES: Array<[string, RegExp]> = [
    ["写死的颜色", /#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/],
    ["写死的字号", /font-size:(?!\s*var\()|fontSize:\s*\d/],
    ["写死的字重", /font-weight:(?!\s*var\()|fontWeight:\s*\d/],
    ["写死的圆角", /border-radius:(?!\s*(?:var\(|0;))|borderRadius:\s*\d/],
  ];

  for (const [file, source] of Object.entries(MIGRATED)) {
    it(`${file} has no hard-coded design values`, () => {
      const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
      for (const [label, pattern] of RULES) {
        const hit = code.split("\n").find((line) => pattern.test(line));
        expect(hit, `${file} 有${label}：${hit?.trim()}`).toBeUndefined();
      }
    });
  }
});
