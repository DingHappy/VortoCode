import { expect, it } from "vitest";
import { dialogTabTarget } from "./dialogFocus";

function element(order: number) {
  return { order, compareDocumentPosition: (other: { order: number }) => other.order > order ? 4 : other.order < order ? 2 : 0 } as unknown as HTMLElement;
}
it("wraps both directions when the modal has only one control", () => {
  const close = element(1);
  expect(dialogTabTarget([close], close, false)).toBe(close);
  expect(dialogTabTarget([close], close, true)).toBe(close);
});
it("cycles within the modal rather than falling into background controls", () => {
  const first = element(1), middle = element(2), last = element(3);
  expect(dialogTabTarget([first, middle, last], last, false)).toBe(first);
  expect(dialogTabTarget([first, middle, last], first, true)).toBe(last);
  expect(dialogTabTarget([first, middle, last], middle, false)).toBe(last);
});
it("finds the next control after programmatic focus on an old message", () => {
  const close = element(1), message = element(2), link = element(3);
  expect(dialogTabTarget([close, link], message, false)).toBe(link);
  expect(dialogTabTarget([close, link], message, true)).toBe(close);
  expect(dialogTabTarget([close], message, false)).toBe(close);
});
it("handles the initial focus and an empty visible-control list", () => {
  const close = element(1);
  expect(dialogTabTarget([close], null, false)).toBe(close);
  expect(dialogTabTarget([], null, false)).toBeUndefined();
});
