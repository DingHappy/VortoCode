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
  expect(PANEL_PAGE.identity).toBeNull();                       // 「定制」是独立页面，不再打开身份标签
  expect("customize" in PAGE_PANEL).toBe(false);
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

it("右侧名片：一行状态写连接和住在哪，模型单独一行；等你处理的件数挂在「动态」的圆点上", () => {
  const online = renderToStaticMarkup(<AssistantPanel name="VortoCode" connected modelLabel="Qwen3.6" host={{ kind: "local", canAddResident: true }} onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}} waiting={2}><p>内容</p></AssistantPanel>);
  expect(online).toContain("已连接 · 在本机运行");
  expect(online).toContain('<small class="vc-panel-model">Qwen3.6</small>');
  expect(online).toContain('aria-label="2 件等你处理"');
  const offline = renderToStaticMarkup(<AssistantPanel name="VortoCode" connected={false} host={{ kind: "local", canAddResident: true }} onAddResident={() => {}} onRetryResident={() => {}} tab="audit" onTab={() => {}} onClose={() => {}}><p>内容</p></AssistantPanel>);
  expect(offline).toContain("连接中断");
  expect(offline).not.toContain("vc-panel-model");
  expect(offline).not.toContain("等你处理");
  const noModel = renderToStaticMarkup(<AssistantPanel name="VortoCode" connected modelLabel="未连接模型" host={{ kind: "local", canAddResident: true }} onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}}><p /></AssistantPanel>);
  expect(noModel).toContain(">还没连接模型<");
});

it("名片说明助理住在哪：常驻电脑 / 本机；连不上常驻电脑时说清楚并给「重新连接」", () => {
  const panel = (host: import("./AssistantMode").AssistantHostInfo, connected = true) => renderToStaticMarkup(
    <AssistantPanel name="VortoCode" connected={connected} host={host} onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}}
      computer={<button>打开电脑看</button>}><p /></AssistantPanel>);
  const resident = panel({ kind: "resident", name: "home-server" });
  expect(resident).toContain("已连接 · 住在常驻电脑「home-server」");
  expect(resident).toContain(">打开电脑看</button>");
  expect(resident).not.toContain("连接常驻电脑</button>");
  expect(panel({ kind: "resident", name: "常驻电脑" })).toContain("已连接 · 住在常驻电脑</span>");   // 名字就叫常驻电脑时不重复
  const down = panel({ kind: "resident", name: "home-server", reachable: false }, false);
  expect(down).toContain('class="vc-panel-status is-down">连不上常驻电脑<');
  expect(down).toContain("它可能还在干活，只是这台电脑看不到");
  expect(down).toContain(">重新连接</button>");
  expect(down).not.toContain("打开电脑看");
  expect(panel({ kind: "local", canAddResident: true })).toContain(">连接常驻电脑</button>");
  expect(panel({ kind: "local", canAddResident: false })).toContain(">重新连接常驻电脑</button>");
});

it("右侧五个标签只显示图标，名字在 aria-label 和悬停提示里；等你处理的件数挂在动态上", () => {
  const html = renderToStaticMarkup(<AssistantPanel name="小V" connected host={{ kind: "local", canAddResident: false }} onAddResident={() => {}} onRetryResident={() => {}} tab="identity" onTab={() => {}} onClose={() => {}} waiting={3}><p /></AssistantPanel>);
  for (const label of ["动态", "目标", "审核记录", "定时", "身份"]) expect(html).toContain(`aria-label="${label}" title="${label}"`);
  expect(html).not.toContain('aria-label="今日"');
  expect(html).toContain('aria-selected="true" aria-label="身份"');
  expect(html).toContain('class="vc-panel-tab-dot" role="img" aria-label="3 件等你处理"');
  expect(html).toContain('aria-label="收起面板"');
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
  expect(html).toContain('class="vc-panel-tab-dot is-unknown" role="img" aria-label="已读取 0 件待处理，部分来源待核对"');
  const none = renderToStaticMarkup(<AssistantPanel name="小V" connected host={{ kind: "local", canAddResident: false }}
    onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}} waiting={0}><p /></AssistantPanel>);
  expect(none).not.toContain("vc-panel-tab-dot");
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

it("identity page lists SOUL, about-you and memory without repeating the panel header", () => {
  const html = renderToStaticMarkup(<AssistantIdentity profile={{ name: "VortoCode", soul: "", about_you: "在杭州写代码" }}
    memories={[{ id: "m1", content: "装 VortoCode 的服务器就是助理的电脑", created_at: "2026-10-27T10:00:00" }]}
    proposals={[{ id: "p1", content: "x" } as never]} onAvatar={() => {}} onEdit={() => {}} onDeleteMemory={() => {}} />);
  expect(html).not.toContain("assistant-profile-head");
  for (const text of ["编辑资料", "换头像", "SOUL", "还没写", "在杭州写代码", "记得 1 件事", "1 条待确认", "装 VortoCode 的服务器就是助理的电脑"]) expect(html).toContain(text);
  // 名片卡在前，SOUL 和记忆是两张卡
  expect(html.indexOf("vc-identity-card")).toBeLessThan(html.indexOf("vc-identity-tile soul"));
  expect(html).toContain('class="vc-identity-tile memory"');
  expect(html).not.toContain("vc-identity-about empty");
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
  expect(html).toMatch(/买菜<\/span><span class="vc-nav-item__meta">手机<\/span>/);
  expect(html).toMatch(/周三开会<\/span><span class="vc-nav-item__meta">本机<\/span>/);
  expect(html).toContain("本机对话");
  const none = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(none).not.toContain(">本机<");
});

it("search narrows only the recent list", () => {
  const html = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} query="周报"
    sideChats={[{ sid: "s1", title: "整理周报" }, { sid: "s2", title: "签到脚本" }]} current="m" onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(html).toContain("整理周报");
  expect(html).not.toContain("签到脚本");
  expect(html).not.toContain("主要聊天</span>");
  expect(html).toContain("定时");   // 一级入口不受影响
});

it("首页建议是输入框下面的一排小按钮：显示短名字，整句话放在悬停提示里", async () => {
  const { AssistantStarterTips } = await import("./AssistantMode");
  const html = renderToStaticMarkup(<AssistantStarterTips tips={[{ id: "t1", label: "看看这周的日程", prompt: "帮我看看这周的日程，有冲突提醒我" }]}
    onPick={() => {}} onDismiss={() => {}} />);
  expect(html).toContain('role="group" aria-label="可以这样用"');
  expect(html).toContain('<button class="vc-starter-chip" title="帮我看看这周的日程，有冲突提醒我">看看这周的日程</button>');
  expect(html).toContain(">不再显示</button>");
  expect(renderToStaticMarkup(<AssistantStarterTips tips={[]} onPick={() => {}} onDismiss={() => {}} />)).toBe("");
});
