import { describe, expect, it } from "vitest";

import type { TurnActivity } from "../types";
import { latestTurnSteps, previewReferences, publishedArtifactId } from "./preview";

const tool = (id: string, name: string, args: Record<string, unknown>, status = "succeeded", rid = "r1"): TurnActivity =>
  ({ id, rid, kind: "tool", name, args, status, label: name });

describe("previewReferences", () => {
  it("collects fetched pages and searches, dedupes, and drops non-http urls", () => {
    const refs = previewReferences([
      tool("1", "web_search", { query: "tauri drag drop" }),
      tool("2", "web_fetch", { url: "https://example.com/a" }, "failed"),
      tool("3", "web_fetch", { url: "https://example.com/a" }),
      tool("4", "web_fetch", { url: "file:///etc/passwd" }),
      tool("5", "read_file", { path: "README.md" }),
    ]);
    expect(refs).toEqual([
      { id: "search:tauri drag drop", kind: "search", label: "tauri drag drop", failed: false },
      { id: "page:https://example.com/a", kind: "page", label: "https://example.com/a", url: "https://example.com/a", failed: false },
    ]);
  });
});

describe("publishedArtifactId", () => {
  it("reads the id from a successful publish result", () => {
    expect(publishedArtifactId({
      name: "publish_artifact", status: "succeeded",
      result: "已发布制品「报告」(id=report-1a2b, v2, html)。\n查看：http://x",
    })).toBe("report-1a2b");
  });

  it("ignores running, failed and other tools", () => {
    expect(publishedArtifactId({ name: "publish_artifact", status: "running", args: { id: "a" } })).toBeNull();
    expect(publishedArtifactId({ name: "publish_artifact", status: "failed", result: "(id=a, v1" })).toBeNull();
    expect(publishedArtifactId({ name: "web_fetch", status: "succeeded", result: "(id=a, v1" })).toBeNull();
  });

  it("falls back to a safe id argument", () => {
    expect(publishedArtifactId({ name: "publish_artifact", status: "succeeded", result: "", args: { id: "x-1" } })).toBe("x-1");
    expect(publishedArtifactId({ name: "publish_artifact", status: "succeeded", result: "", args: { id: "../x" } })).toBeNull();
  });
});

describe("latestTurnSteps", () => {
  it("returns tool steps of the most recent turn that used tools", () => {
    const steps = latestTurnSteps([
      tool("1", "read_file", {}, "succeeded", "r1"),
      tool("2", "grep", {}, "succeeded", "r2"),
      { id: "3", rid: "r3", kind: "phase", status: "running", label: "思考" },
    ]);
    expect(steps.map((item) => item.id)).toEqual(["2"]);
  });
});
