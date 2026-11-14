import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { modelConfigurationSummary, ModelConfigurationState, ModelConfigurationView, parseModelConfigurationRead, type ModelConfigurationRead } from "./ModelConfigurationState";

it("does not reuse a success for a different saved revision", () => {
  const read: ModelConfigurationRead = { schemaVersion: 1, savedRevision: "old", runtimes: [{ runtimeId: "a", status: "applied" }] };
  expect(modelConfigurationSummary(read, "new")).toContain("尚未同步");
  expect(modelConfigurationSummary(read)).toContain("正在核对");
  expect(modelConfigurationSummary(null, "new")).toContain("正在核对");
});

it("distinguishes current, old, missing receipts and no engine", () => {
  expect(modelConfigurationSummary({ schemaVersion: 1, savedRevision: "v1", runtimes: [] }, "v1")).toContain("下次启动");
  const summary = modelConfigurationSummary({ schemaVersion: 1, savedRevision: "v1", runtimes: [
    { runtimeId: "a", status: "applied" }, { runtimeId: "b", status: "pending" }, { runtimeId: "c", status: "unknown" },
  ] }, "v1");
  expect(summary).toContain("1 个本机引擎已确认");
  expect(summary).toContain("1 个仍使用旧配置");
  expect(summary).toContain("1 个暂时无法确认");
});

it("shows the evidence boundary without claiming successful inference", () => {
  const html = renderToStaticMarkup(<ModelConfigurationState active={false} revision="v1" />);
  expect(html).toContain("重新核对");
  expect(html).toContain("不代表模型服务可用或任务已完成");
  expect(html).toContain("常驻实例单独配置");
});


it("rejects future schemas, malformed entries and duplicate runtimes instead of implying success", () => {
  const valid = { schemaVersion: 1, savedRevision: "rev-1", runtimes: [{ runtimeId: "a", status: "applied" }] };
  expect(parseModelConfigurationRead(valid)).toEqual(valid);
  for (const bad of [null, {}, { ...valid, schemaVersion: 2 }, { ...valid, savedRevision: "" },
    { ...valid, runtimes: null }, { ...valid, runtimes: [null] },
    { ...valid, runtimes: [{ runtimeId: "a", status: "done" }] },
    { ...valid, runtimes: [valid.runtimes[0], valid.runtimes[0]] }]) {
    expect(parseModelConfigurationRead(bad)).toBeNull();
  }
});

it("shows separate current, restart and unknown counts only for the exact saved revision", () => {
  const read: ModelConfigurationRead = { schemaVersion: 1, savedRevision: "v1", runtimes: [
    { runtimeId: "loaded", status: "applied" }, { runtimeId: "old", status: "pending" }, { runtimeId: "unread", status: "unknown" },
  ] };
  const render = (revision: string, failed = false) => renderToStaticMarkup(<ModelConfigurationView read={read} revision={revision} failed={failed} onRefresh={() => {}} />);
  const current = render("v1");
  for (const text of ["1 个已载入", "1 个需重启", "1 个待核对", "仍使用旧配置，等待重启", "不代表模型服务可用或任务已完成"]) expect(current).toContain(text);
  const stale = render("v2");
  expect(stale).toContain("尚未同步");
  expect(stale).not.toContain("个已载入");
  const failed = render("v1", true);
  expect(failed).toContain("暂时无法核对");
  expect(failed).not.toContain("个已载入");
  expect(failed).not.toContain("loaded");
});
