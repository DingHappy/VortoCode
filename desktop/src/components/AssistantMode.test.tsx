import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ASSISTANT_PAGES, AssistantIdentity, AssistantPanel, AssistantSchedule, AssistantSidebar, MemoryProposals, ModeSwitch, PAGE_PANEL, PANEL_PAGE } from "./AssistantMode";

it("模式切换：两个选项，当前项 aria-checked", () => {
  const html = renderToStaticMarkup(<ModeSwitch mode="assistant" onChange={() => {}} />);
  expect(html).toContain('aria-checked="true">助理');
  expect(html).toContain('aria-checked="false">工作台');
});

it("左侧：一级入口不超过 5 个，「任务」角标只计等你处理的件数", () => {
  expect(ASSISTANT_PAGES.length).toBeLessThanOrEqual(5);
  const html = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" page="schedule" waiting={3}
    onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(html).toContain('aria-label="3 件等你处理">3</span>');
  expect(html.match(/aria-current="page"/g)).toHaveLength(1);   // 打开定时时，主要聊天不再同时高亮
  expect(html).toMatch(/aria-current="page"[^>]*>.*?定时/);
  const uncertain = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" waiting={0} waitingUncertain
    onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(uncertain).toContain('class="vc-count vc-count--unknown"');
  expect(uncertain).toContain(">?</span>");
  const calm = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(calm).not.toContain("vc-count");
});

it("每个面板入口都对得上一个标签，审核记录不占一级入口", () => {
  for (const [page, tab] of Object.entries(PAGE_PANEL)) expect(PANEL_PAGE[tab]).toBe(page);
  expect(PANEL_PAGE.audit).toBeNull();
});

it("左侧「最近」：主要聊天置顶在旁聊之前；没有旁聊时给一句说明", () => {
  const withSide = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m", preview: "完成静态审查" }}
    sideChats={[{ sid: "s1", title: "整理周报" }]} current="m" onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(withSide.indexOf("主要聊天")).toBeLessThan(withSide.indexOf("整理周报"));
  expect(withSide).toContain('title="完成静态审查"');
  expect(withSide).toContain("新对话");
  const empty = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(empty).toContain("开个旁聊，不打扰主要聊天");
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
  const resident = panel({ kind: "resident", name: "home-server" });
  expect(resident).toContain("住在常驻电脑「home-server」");
  expect(resident).toContain("合上笔记本它也照常干活");
  expect(resident).not.toContain("连接常驻电脑</button>");
  expect(panel({ kind: "resident", name: "常驻电脑" })).toContain("住在常驻电脑</span>");   // 名字就叫常驻电脑时不重复
  expect(panel({ kind: "resident", name: "home-server", reachable: false })).toContain(">重新连接常驻电脑</button>");
  expect(panel({ kind: "local", canAddResident: true })).toContain(">连接常驻电脑</button>");
  expect(panel({ kind: "local", canAddResident: false })).toContain(">重新连接常驻电脑</button>");
});

it("右侧五个图标标签：动态 / 目标 / 审核记录 / 定时 / 身份，等你处理的件数挂在动态上", () => {
  const html = renderToStaticMarkup(<AssistantPanel name="小V" connected host={{ kind: "local", canAddResident: false }} onAddResident={() => {}} onRetryResident={() => {}} tab="identity" onTab={() => {}} onClose={() => {}} waiting={3}><p /></AssistantPanel>);
  for (const label of ["动态", "目标", "审核记录", "定时", "身份"]) expect(html).toContain(`aria-label="${label}"`);
  expect(html).not.toContain('aria-label="今日"');
  expect(html).toContain('aria-selected="true" aria-label="身份"');
  expect(html).toContain("3 件等你处理");
});

it("离线的动态徽标明确表示历史待处理数量", () => {
  const html = renderToStaticMarkup(<AssistantPanel name="小V" connected={false} host={{ kind: "local", canAddResident: false }}
    onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}} waiting={3}><p /></AssistantPanel>);
  expect(html).toContain("3 条历史待处理");
  expect(html).not.toContain("3 件等你处理");
});

it("来源不完整时，零条已读记录不会伪装成没有待处理事项", () => {
  const html = renderToStaticMarkup(<AssistantPanel name="小V" connected host={{ kind: "local", canAddResident: false }}
    onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}} waiting={0} waitingUncertain><p /></AssistantPanel>);
  expect(html).toContain("部分来源待核对");
  expect(html).toContain('>?<');
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

it("does not point a local-only user at schedules that will never run", () => {
  const local = renderToStaticMarkup(<AssistantSchedule items={[]} connected local />);
  expect(local).toContain("不会按时自动跑"); expect(local).not.toContain("每天早上给我一份简报");
  const resident = renderToStaticMarkup(<AssistantSchedule items={[]} connected />);
  expect(resident).toContain("每天早上给我一份简报");
});

it("插件：已接上的能检查、能移除；没接的说凭据放哪；检查结果成功/失败分开显示", async () => {
  const { AssistantPlugins } = await import("./AssistantMode");
  const plugins = [
    { name: "calendar", title: "日历（只读）", status: "available" as const, credential_file: "~/.vortocode/connectors/calendar.token", checkable: true },
    { name: "notion", title: "Notion（只读）", status: "missing" as const, credential_file: "~/.vortocode/connectors/notion.token", checkable: true },
    { name: "mail", title: "邮件", status: "invalid" as const, credential_file: "~/.vortocode/connectors/mail.token", checkable: true },
  ];
  const html = renderToStaticMarkup(<AssistantPlugins plugins={plugins} checks={{
    calendar: { ok: true, status: "available", detail: "能读到，共 12 条日程" },
    mail: { ok: false, status: "invalid", detail: "凭据文件不可用" },
  }} />);
  const [calendar, notion, mail] = html.split("<li>").slice(1);
  expect(calendar).toContain("已接上");
  expect(calendar).toContain(">检查</button>");
  expect(calendar).toContain(">移除凭据</button>");
  expect(calendar).toContain("能读到，共 12 条日程");
  expect(notion).toContain("未接入");
  expect(notion).not.toContain("</button>");
  expect(notion).toContain("<code>~/.vortocode/connectors/notion.token</code>");
  expect(mail).not.toContain(">检查</button>");
  expect(mail).toContain('role="alert"');
  expect(renderToStaticMarkup(<AssistantPlugins plugins={[]} />)).toBe("");
  expect(renderToStaticMarkup(<AssistantPlugins plugins={plugins} busy="calendar" />)).toContain("处理中…");
});

it("插件：能在 Desktop 填凭据——没接的给「填写」，接上的给「更换」；不给保存入口时不显示", async () => {
  const { AssistantPlugins } = await import("./AssistantMode");
  const plugins = [
    { name: "calendar", title: "日历（只读）", status: "available" as const, credential_file: "~/.vortocode/connectors/calendar.token", checkable: true },
    { name: "notion", title: "Notion（只读）", status: "missing" as const, credential_file: "~/.vortocode/connectors/notion.token", checkable: true },
  ];
  const html = renderToStaticMarkup(<AssistantPlugins plugins={plugins} onSave={async () => true} />);
  const [calendar, notion] = html.split("<li>").slice(1);
  expect(calendar).toContain(">更换</button>");
  expect(notion).toContain('class="primary">填写</button>');
  expect(notion).toContain("点「填写」把凭据存到助理所在的电脑");
  expect(html).not.toContain("<input");                       // 输入框要点了才出现
  const readOnly = renderToStaticMarkup(<AssistantPlugins plugins={plugins} />);
  expect(readOnly).not.toContain("填写</button>");
  expect(readOnly).not.toContain("更换</button>");
});

it("identity page lists SOUL, about-you and memory without repeating the panel header", () => {
  const html = renderToStaticMarkup(<AssistantIdentity profile={{ name: "VortoCode", soul: "", about_you: "在杭州写代码" }}
    memories={[{ id: "m1", content: "装 VortoCode 的服务器就是助理的电脑", created_at: "2026-10-27T10:00:00" }]}
    proposals={[{ id: "p1", content: "x" } as never]} onAvatar={() => {}} onEdit={() => {}} onDeleteMemory={() => {}} />);
  expect(html).not.toContain("assistant-profile-head");
  for (const text of ["编辑资料", "换头像", "SOUL", "还没写", "在杭州写代码", "记得 1 件事", "1 条待确认", "装 VortoCode 的服务器就是助理的电脑"]) expect(html).toContain(text);
  expect(html.match(/assistant-identity-preview empty/g)).toHaveLength(1);
});

it("schedule section explains read-only items once", () => {
  const items = [{ id: "a", kind: "report", title: "早报", schedule: "每天 09:00", enabled: true }, { id: "b", kind: "report", title: "晚报", schedule: "每天 21:00", enabled: true }] as const;
  const html = renderToStaticMarkup(<AssistantSchedule items={[...items]} connected />);
  expect(html.match(/这里只能查看/g)).toHaveLength(1);
  expect(html.match(/>只读</g)).toHaveLength(2);
});

it("sidebar marks phone and offline chats inline instead of grouping them", () => {
  const html = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" onSelect={() => {}}
    onNewSideChat={() => {}} phoneChats={[{ sid: "p1", title: "📱 买菜" }]}
    offlineChats={[{ sid: "o1", title: "周三开会" }, { sid: "o2", title: "" }]} onOpenOfflineChat={() => {}} />);
  expect(html).not.toContain("本机（离线时）");
  expect(html).toMatch(/买菜<\/span>.*?>手机<\/span>/);
  expect(html).toMatch(/周三开会<\/span>.*?>本机<\/span>/);
  expect(html).toContain("本机对话");
  const none = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(none).not.toContain(">本机<");
});
