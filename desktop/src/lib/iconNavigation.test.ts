import { expect, it } from "vitest";
import { commandKeyTarget, hintPosition, tabKeyTarget } from "./iconNavigation";

it("moves through command buttons without treating Enter or Space as selection navigation", () => {
  expect(commandKeyTarget("ArrowDown", -1, 5)).toBe(0);
  expect(commandKeyTarget("ArrowUp", -1, 5)).toBe(4);
  expect(commandKeyTarget("ArrowDown", 4, 5)).toBe(0);
  expect(commandKeyTarget("ArrowUp", 0, 5)).toBe(4);
  expect(commandKeyTarget("Home", 3, 5)).toBe(0);
  expect(commandKeyTarget("End", 1, 5)).toBe(4);
  expect(commandKeyTarget("ArrowDown", -1, 0)).toBeNull();
  for (const key of ["Enter", " ", "Tab"]) expect(commandKeyTarget(key, 1, 5)).toBeNull();
});

it("moves through a horizontal tab bar and wraps at both ends", () => {
  expect(tabKeyTarget("ArrowLeft", 0, 5)).toBe(4);
  expect(tabKeyTarget("ArrowRight", 4, 5)).toBe(0);
  expect(tabKeyTarget("ArrowRight", 1, 5)).toBe(2);
  expect(tabKeyTarget("Home", 3, 5)).toBe(0);
  expect(tabKeyTarget("End", 1, 5)).toBe(4);
  expect(tabKeyTarget("Tab", 1, 5)).toBeNull();
  expect(tabKeyTarget("ArrowDown", 1, 5)).toBeNull();
  expect(tabKeyTarget("ArrowRight", 0, 0)).toBeNull();
  expect(tabKeyTarget("ArrowLeft", -1, 5)).toBeNull();
});

it("keeps a hint in the viewport at either edge and flips above a bottom trigger", () => {
  const viewport = { width: 360, height: 640 }, box = { width: 200, height: 52 };
  expect(hintPosition({ left: 0, right: 32, top: 0, bottom: 32 }, box, viewport)).toEqual({ left: 8, top: 40 });
  expect(hintPosition({ left: 328, right: 360, top: 0, bottom: 32 }, box, viewport)).toEqual({ left: 152, top: 40 });
  const above = hintPosition({ left: 100, right: 132, top: 608, bottom: 640 }, box, viewport);
  expect(above.top + box.height).toBeLessThan(608);
  expect(above.left).toBeGreaterThanOrEqual(8);
  expect(above.left + box.width).toBeLessThanOrEqual(352);
});
