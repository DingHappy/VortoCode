import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { TaskPill, taskPillText } from "./TaskPill";

it("keeps routine work quiet while surfacing decisions, exceptions and incomplete coverage", () => {
  expect(taskPillText(0, 0)).toBe("查看摘要");
  expect(taskPillText(0, 2)).toBe("查看摘要");
  expect(taskPillText(1, 2)).toBe("1 件等你");
  expect(taskPillText(1, 0)).toBe("1 件等你");
  expect(taskPillText(2, 1, true)).toBe("2+ 件等你");
  expect(taskPillText(0, 0, true)).toBe("待核对");
  expect(taskPillText(0, 1, true)).toBe("待核对");
  expect(taskPillText(0, 2, false, 1)).toBe("1 项需核对");
  expect(taskPillText(1, 2, true, 3)).toBe("1+ 件等你 · 3+ 项需核对");
});

it("turns amber only when something waits for you", () => {
  const calm = renderToStaticMarkup(<TaskPill waiting={0} busy={2} onOpen={() => {}} />);
  expect(calm).not.toContain("vc-pill--wait");
  expect(calm).not.toContain("vc-pill__dot");
  const idle = renderToStaticMarkup(<TaskPill waiting={0} busy={0} onOpen={() => {}} />);
  expect(idle).not.toContain("vc-pill__dot");
  expect(renderToStaticMarkup(<TaskPill waiting={1} busy={0} onOpen={() => {}} />)).toContain("vc-pill--wait");
  expect(renderToStaticMarkup(<TaskPill waiting={0} busy={0} uncertain onOpen={() => {}} />)).not.toContain("vc-pill--wait");
  expect(renderToStaticMarkup(<TaskPill waiting={0} busy={2} exceptions={1} onOpen={() => {}} />)).toContain("vc-pill--wait");
});
