import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ComposerCommands } from "./ComposerCommands";

const noop = () => {};
const base = { prompt: "两行\n草稿", project: false, disabled: false, onPrompt: noop, onAction: noop, onSend: noop };

it("keeps a single input, attachment trigger, caller-owned send action and original context controls", () => {
  const html = renderToStaticMarkup(<ComposerCommands {...base} sendControl={<button aria-label="加入待运行队列" disabled>排队</button>}>
    <div className="composer-footer"><button>授权档位</button><button>模型</button></div>
  </ComposerCommands>);
  expect(html.match(/<textarea/g)).toHaveLength(1);
  expect(html.match(/aria-label="添加与快捷操作"/g)).toHaveLength(1);
  expect(html.match(/aria-label="加入待运行队列"/g)).toHaveLength(1);
  expect(html).toContain('disabled=""');
  for (const text of ["两行\n草稿", "授权档位", "模型"]) expect(html).toContain(text);
});

it("keeps the workbench footer and disables menu actions while the runtime prepares", () => {
  const html = renderToStaticMarkup(<ComposerCommands {...base} project disabled><button>原有发送按钮</button></ComposerCommands>);
  expect(html).toContain("原有发送按钮");
  expect(html).toMatch(/aria-label="添加与快捷操作"[^>]*disabled=""/);
  expect(html).not.toContain('role="dialog"');
});
