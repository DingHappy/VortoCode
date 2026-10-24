import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ResidentComputerSummary } from "./ResidentComputerCard";
import { readResidentComputer, type ComputerRead } from "../lib/residentComputer";

const data = readResidentComputer({ schema_version: 1, execution_location: "runtime_host", state: "ready", control_mode: "human",
  viewers: 0, password_store: "unlocked", resident_worker: { connected: false, unattended: false } })!;
const source: ComputerRead = { status: "current", checkedAt: 100, data };
const render = (overrides = {}) => renderToStaticMarkup(<ResidentComputerSummary source={source} connected resident busy={false}
  now={101} onRefresh={() => {}} {...overrides} />);

it("explains resident-local perception without a screen viewer or unattended permission", () => {
  const html = render();
  expect(html).toContain("执行位置：常驻电脑");
  expect(html).toContain("桌面组件就绪");
  expect(html).toContain("密码库：已解锁");
  expect(html).toContain("本地助手默认只接收状态与结果");
  expect(html).toContain("亲自操作与接管");
  expect(html).toContain("无人值守桌面任务尚未接通");
  expect(html).not.toContain("<iframe");
  expect(html).not.toContain("<img");
});

it("stale, failed and disconnected reads do not show current readiness or vault unlock", () => {
  for (const override of [{ now: 60_101 }, { connected: false }, { source: { ...source, status: "unavailable" } }]) {
    const html = render(override);
    expect(html).not.toContain("桌面组件就绪");
    expect(html).not.toContain("密码库：已解锁");
    expect(html).toContain("上次结果已保留");
  }
  expect(render({ resident: false })).toContain("不能代表常驻电脑");
});

it("distinguishes passive observers from controlling viewers", () => {
  const html = render({ source: { ...source, data: { ...data, viewers: 1, observation: { available: true, viewers: 3 } } } });
  expect(html).toContain("1 个接管连接");
  expect(html).toContain("仅观察入口可用 · 3 人观看");
  expect(render()).toContain("仅观察入口：尚未确认");
});
