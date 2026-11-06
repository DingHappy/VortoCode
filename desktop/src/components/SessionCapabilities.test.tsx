import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { SessionCapabilities } from "./SessionCapabilities";
import { emptyResourceView, type ResourceView } from "../lib/sessionResources";

function render(view: ResourceView, connected = true) {
  const action = vi.fn();
  const html = renderToStaticMarkup(<SessionCapabilities view={view} connected={connected} hostLabel="常驻测试端"
    onClose={action} onRefresh={action} onManage={action} onLocalSettings={action} onFocusConfirmation={action} />);
  expect(action).not.toHaveBeenCalled(); return html;
}
const snapshot = { version: 1 as const, session: "sid-one", nonce: "n", observed_at: "2026-11-05T12:00:00Z", status: "observed" as const,
  truncated: false, items: [], tool_inventory: { version: 1 as const, truncated: true, items: [
    { name: "edit_file", read_only: false, required_capabilities: ["host_process", "write_repo"] },
    { name: "save_memory", read_only: false, required_capabilities: [] },
    { name: "mcp__demo__list", read_only: null, required_capabilities: null },
  ] } };

it("shows declarations, truncation and configuration location without granting access", () => {
  const html = render({ snapshot, stale: false, busy: false, error: "" });
  for (const label of ["已装配 3+ 项工具", "未列出的工具保持未知", "可能改动状态", "本机进程", "write_repo", "记忆与目标", "读写属性未确认", "所需能力未确认", "这台 Mac 的权限设置", "具体调用仍需能力和审批检查"]) expect(html).toContain(label);
  expect(html).not.toContain(">已授权<");
});

it("distinguishes old servers, unloaded sessions and retained offline observations", () => {
  const legacy = render({ snapshot: { ...snapshot, tool_inventory: undefined }, busy: false, stale: false, error: "" });
  expect(legacy).toContain("工具清单尚未确认"); expect(legacy).not.toContain("已装配 0");
  expect(render({ ...emptyResourceView(), snapshot: { ...snapshot, status: "not_loaded", tool_inventory: undefined } })).toContain("不会为查看能力创建会话");
  const offline = render({ snapshot, busy: false, stale: true, error: "" }, false);
  expect(offline).toContain("上次记录："); expect(offline).toContain("连接已中断"); expect(offline).toContain('disabled=""');
});

it("groups the actual project and task tool names without treating shell as a built-in command", () => {
  const project = ["glob", "git_status", "find_definition", "find_references", "document_symbols"];
  const names = [...project, "task", "run_command", "shell"];
  const html = render({ snapshot: { ...snapshot, tool_inventory: { version: 1, truncated: false,
    items: names.map(name => ({ name, read_only: null, required_capabilities: null })) } }, stale: false, busy: false, error: "" });
  const section = (label: string) => html.match(new RegExp(`<details><summary>${label}.*?</details>`))?.[0] ?? "";
  for (const name of project) expect(section("文件与项目")).toContain(`<code>${name}</code>`);
  expect(section("任务与协作")).toContain("<code>task</code>");
  expect(section("命令与验证")).toContain("<code>run_command</code>");
  expect(section("命令与验证")).not.toContain("<code>shell</code>");
  expect(section("其他工具与集成")).toContain("<code>shell</code>");
});
