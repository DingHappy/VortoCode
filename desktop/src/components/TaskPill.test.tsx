import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { TaskPill, taskPillText } from "./TaskPill";

it("says how many things wait for you before how many are busy", () => {
  expect(taskPillText(0, 0)).toBe("任务");
  expect(taskPillText(0, 2)).toBe("2 件在忙");
  expect(taskPillText(1, 2)).toBe("1 件等你 · 2 件在忙");
  expect(taskPillText(1, 0)).toBe("1 件等你");
  expect(taskPillText(2, 1, true)).toBe("2+ 件等你 · 1 件在忙");
  expect(taskPillText(0, 0, true)).toBe("待核对");
  expect(taskPillText(0, 1, true)).toBe("1 件在忙 · 待核对");
});

it("turns amber only when something waits for you", () => {
  const calm = renderToStaticMarkup(<TaskPill waiting={0} busy={2} onOpen={() => {}} />);
  expect(calm).not.toContain("vc-pill--wait");
  expect(calm).toContain("vc-pill__dot");
  const idle = renderToStaticMarkup(<TaskPill waiting={0} busy={0} onOpen={() => {}} />);
  expect(idle).not.toContain("vc-pill__dot");
  expect(renderToStaticMarkup(<TaskPill waiting={1} busy={0} onOpen={() => {}} />)).toContain("vc-pill--wait");
  expect(renderToStaticMarkup(<TaskPill waiting={0} busy={0} uncertain onOpen={() => {}} />)).not.toContain("vc-pill--wait");
});
