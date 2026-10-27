import { beforeEach, expect, it, vi } from "vitest";

const invoke = vi.fn(async () => undefined);
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args as []) }));

import { diag, installGlobalErrorLogging } from "./diagnostics";

beforeEach(() => invoke.mockClear());

it("passes a bounded detail to the native logger and never throws", () => {
  diag("connect.failed", "x".repeat(900), "warn");
  expect(invoke).toHaveBeenCalledWith("diag_log", { level: "warn", event: "connect.failed", detail: "x".repeat(600) });
  invoke.mockImplementationOnce(() => { throw new Error("no tauri"); });
  expect(() => diag("app.start")).not.toThrow();
});

it("records uncaught UI errors and rejections", () => {
  const listeners = new Map<string, (event: unknown) => void>();
  installGlobalErrorLogging({ addEventListener: (type: string, listener: (event: unknown) => void) => { listeners.set(type, listener); } } as never);
  listeners.get("error")!({ message: "boom", filename: "App.tsx", lineno: 42 });
  listeners.get("unhandledrejection")!({ reason: new TypeError("bad") });
  expect(invoke).toHaveBeenCalledWith("diag_log", { level: "error", event: "ui.error", detail: "boom @ App.tsx:42" });
  expect(invoke).toHaveBeenCalledWith("diag_log", { level: "error", event: "ui.unhandled_rejection", detail: "TypeError: bad" });
});
