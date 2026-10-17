import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AssistantPanel, AssistantSidebar, MemoryProposals, ModeSwitch } from "./AssistantMode";

it("模式切换：两个选项，当前项 aria-checked", () => {
  const html = renderToStaticMarkup(<ModeSwitch mode="assistant" onChange={() => {}} />);
  expect(html).toContain('aria-checked="true" class="active">助理');
  expect(html).toContain('aria-checked="false" class="">工作台');
});

it("左侧：主要聊天置顶在旁聊之前；没有旁聊时给一句说明", () => {
  const withSide = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m", preview: "完成静态审查" }}
    sideChats={[{ sid: "s1", title: "整理周报" }]} current="m" onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(withSide.indexOf("主要聊天")).toBeLessThan(withSide.indexOf("整理周报"));
  expect(withSide).toContain("完成静态审查");
  expect(withSide).toContain("新建旁聊");
  const empty = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(empty).toContain("岔开的话题可以开一个旁聊");
});

it("右侧：身份卡显示连接状态与模型；等你处理的件数跟在「动态」后面", () => {
  const online = renderToStaticMarkup(<AssistantPanel name="VortoCode" connected modelLabel="Qwen3.6" host={{ kind: "local", canAddResident: true }} onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}} waiting={2}><p>内容</p></AssistantPanel>);
  expect(online).toContain("已连接 · Qwen3.6");
  expect(online).toContain("2 件等你处理");
  const offline = renderToStaticMarkup(<AssistantPanel name="VortoCode" connected={false} host={{ kind: "local", canAddResident: true }} onAddResident={() => {}} onRetryResident={() => {}} tab="audit" onTab={() => {}} onClose={() => {}}><p>内容</p></AssistantPanel>);
  expect(offline).toContain("连接中断");
  expect(offline).not.toContain("等你处理");
});

it("身份卡说明助理住在哪：常驻电脑 / 本机；本机时给出连接或重连常驻电脑的入口", () => {
  const panel = (host: import("./AssistantMode").AssistantHostInfo) => renderToStaticMarkup(
    <AssistantPanel name="VortoCode" connected host={host} onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}}><p /></AssistantPanel>);
  const resident = panel({ kind: "resident", name: "vorto-code" });
  expect(resident).toContain("住在常驻电脑「vorto-code」");
  expect(resident).toContain("合上笔记本它也照常干活");
  expect(resident).not.toContain("连接常驻电脑</button>");
  expect(panel({ kind: "resident", name: "常驻电脑" })).toContain("住在常驻电脑</span>");   // 名字就叫常驻电脑时不重复
  expect(panel({ kind: "resident", name: "vorto-code", reachable: false })).toContain(">重新连接常驻电脑</button>");
  expect(panel({ kind: "local", canAddResident: true })).toContain(">连接常驻电脑</button>");
  expect(panel({ kind: "local", canAddResident: false })).toContain(">重新连接常驻电脑</button>");
});

it("右侧五个图标标签：动态 / 目标 / 审核记录 / 定时 / 身份，等你处理的件数挂在动态上", () => {
  const html = renderToStaticMarkup(<AssistantPanel name="小V" connected host={{ kind: "local", canAddResident: false }} onAddResident={() => {}} onRetryResident={() => {}} tab="identity" onTab={() => {}} onClose={() => {}} waiting={3}><p /></AssistantPanel>);
  for (const label of ["动态", "目标", "审核记录", "定时", "身份"]) expect(html).toContain(`aria-label="${label}"`);
  expect(html).toContain('aria-selected="true" aria-label="身份"');
  expect(html).toContain("3 件等你处理");
});

it("审核记录只列决策，写明允许/拒绝与是否读过外部内容", async () => {
  const { AssistantAudit } = await import("./AssistantMode");
  const html = renderToStaticMarkup(<AssistantAudit entries={[
    { id: "1", category: "decision", operation: "下发任务单「修登录」到 VortoCode", decision: "allowed" },
    { id: "2", category: "decision", operation: "发布制品？\n本周周报", decision: "denied", tainted: true },
    { id: "3", category: "tool", tool: "web_fetch" },
  ]} />);
  expect(html).toContain("下发任务单「修登录」到 VortoCode");
  expect(html).toContain("已拒绝 · 读过外部内容后");
  expect(html).not.toContain("web_fetch");
});

it("待确认记忆：可能更新了旧记忆的才多一个「记住并替换旧的」，并写出旧的是哪条", () => {
  const html = renderToStaticMarkup(<MemoryProposals proposals={[
    { id: "p1", content: "搬到杭州了", created_at: "", tainted: false, related: [{ id: "m1", content: "住在北京朝阳" }] },
    { id: "p2", content: "周末想去爬山", created_at: "", tainted: false, related: [] },
  ]} />);
  expect(html).toContain("可能更新了：住在北京朝阳");
  expect(html.match(/aria-label="记住并替换旧的"/g)).toHaveLength(1);
  expect(html).toContain('title="记住（旧的也保留）"');
  expect(renderToStaticMarkup(<MemoryProposals proposals={[]} />)).toBe("");
});
