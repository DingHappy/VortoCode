import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DepartmentView, NotesForm } from "./DepartmentPage";
import { StaffBudgets } from "./settings/StaffBudgets";
import { AssistantSidebar } from "./AssistantMode";
import { departmentFixture } from "../../qa/department-fixture";
import type { GatewayClient } from "../gateway";
const noop = () => {};
it("shows original task evidence, member grants, quota and a draft-only dispatch entry", () => {
  const html = renderToStaticMarkup(<DepartmentView detail={departmentFixture()} disabled={false} error="" refresh={noop} onDraft={noop} onOpenTask={noop} />);
  for (const text of ["资料专员", "web_search", "查看交付原文", "今天已记录 300", "交给助理派活", "不会直接派发", "第 1 版"]) expect(html).toContain(text);
  expect(html).not.toContain("主人已验收");
});
it("suggestions are unchecked and public disclosure is explicit before save", () => {
  const detail = departmentFixture(), client = {} as GatewayClient;
  const html = renderToStaticMarkup(<NotesForm detail={detail} client={client} getClient={() => client} disabled={false} onUpdated={noop} />);
  expect(html).toContain("外部内容的条目默认不勾选"); expect(html).toContain("可供联网专员读取");
  expect(html).not.toContain('checked=""'); expect(html).not.toContain("<script>"); expect(html).toContain("&lt;script&gt;");
  expect(html.match(/<button[^>]*>保存所选<\/button>/)?.[0]).toContain("disabled");
});
it("incomplete history and stale reads remain explicit", () => {
  const detail = departmentFixture(); detail.tasks = []; detail.tasks_complete = false;
  const html = renderToStaticMarkup(<DepartmentView detail={detail} disabled error="读取失败" refresh={noop} onDraft={noop} onOpenTask={noop} />);
  expect(html).toContain("旧任务尚未全部读取"); expect(html).not.toContain("还没有部门任务");
  expect(html).toContain("决定已停用");
});
it("sidebar departments are conversations and only show a dot for their pending notes", () => {
  const html = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "main" }} sideChats={[]} current="main" onSelect={noop} onNewSideChat={noop}
    departments={[{ id: "dept-public", title: "资料专员", status: "active", pending: 2 }]} currentDepartment="dept-public" onOpenDepartment={noop} />);
  expect(html).toContain("资料专员"); expect(html).toContain("有笔记等主人核对"); expect(html).not.toContain("把尚未核实");
});
it("budget controls disclose the call-between semantics and default unlimited values", () => {
  const html = renderToStaticMarkup(<StaffBudgets value={{ revision: 1, daily_tokens: 0, task_tokens: 0 }} disabled={false} onSave={noop} />);
  expect(html).toContain("0 不限"); expect(html).toContain("已在途请求可能超出剩余额度"); expect(html).toContain("旧定时作业");
});
