import { beforeEach, expect, it, vi } from "vitest";

const invoke = vi.fn(async (..._args: unknown[]) => ({ errorsEnabled: false, usageEnabled: true }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));

import { loadTelemetrySettings, reportUiError, saveTelemetrySettings } from "./telemetry";

beforeEach(() => invoke.mockClear());

it("reads and saves the two switches through the native layer", async () => {
  await expect(loadTelemetrySettings()).resolves.toEqual({ errorsEnabled: false, usageEnabled: true });
  expect(invoke).toHaveBeenLastCalledWith("telemetry_settings");
  await saveTelemetrySettings({ errorsEnabled: true, usageEnabled: false });
  expect(invoke).toHaveBeenLastCalledWith("set_telemetry_settings", { errorsEnabled: true, usageEnabled: false });
});

it("hands uncaught UI errors to the native error report, bounded and never throwing", () => {
  reportUiError("ui.error", "x".repeat(900));
  expect(invoke).toHaveBeenLastCalledWith("report_ui_error", { kind: "ui.error", message: "x".repeat(600) });
  invoke.mockImplementationOnce(() => { throw new Error("no tauri"); });
  expect(() => reportUiError("ui.error", "boom")).not.toThrow();
});

it("counts a feature use by event name only and never throws", async () => {
  const { countUsage } = await import("./telemetry");
  countUsage("chat.send");
  expect(invoke).toHaveBeenLastCalledWith("count_usage", { event: "chat.send" });
  invoke.mockImplementationOnce(() => { throw new Error("no tauri"); });
  expect(() => countUsage("settings.open")).not.toThrow();
});
