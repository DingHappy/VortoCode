import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { AssistantAudit, AssistantIdentity, AssistantPanel, AssistantSchedule, AssistantSidebar, MemoryProposals, BackToAssistant, PAGE_PANEL, PANEL_PAGE } from "./AssistantMode";
import { ConnectionCoordinator } from "../connection/connectionCoordinator";
import { ownerAccessClient } from "../lib/ownerAccess";

it("工作台已撤：没有模式切换，项目界面里只有「回到助理」", () => {
  const html = renderToStaticMarkup(<BackToAssistant onBack={() => {}} />);
  expect(html).toContain("回到助理");
  expect(html).not.toContain("工作台");
});

it("没有提供日常页面处理器时保持会话列表，等你处理仍归主要聊天", () => {
  const html = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" waiting={3}
    onSelect={() => {}} onNewSideChat={() => {}} />);
  for (const label of ["动态", "定时", "目标", "资料库", "定制"]) expect(html).not.toContain(`>${label}</span>`);
  expect(html).toMatch(/主要聊天<\/span><span class="vc-nav-item__meta"><span class="vc-count" aria-label="3 件等你处理">3<\/span>/);
  expect(html.match(/aria-current="page"/g)).toHaveLength(1);
  const onPage = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" page="library"
    onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(onPage).not.toContain('aria-current="page"');          // 在看资料库时，主要聊天不同时高亮
  const uncertain = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" waiting={0} waitingUncertain
    onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(uncertain).toContain('class="vc-count vc-count--unknown"');
  expect(uncertain).toContain(">?</span>");
  const calm = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m", busy: true }} sideChats={[]} current="m" onSelect={() => {}} onNewSideChat={() => {}} />);
  expect(calm).not.toContain("vc-count");
  expect(calm).toContain("vc-live-dot");
});

it("会话分组：主要聊天 → 任务小组 → 旁聊；旁聊默认只列最近 5 条，正在看的那条总会露出来", async () => {
  const { SIDE_CHAT_LIMIT } = await import("./AssistantMode");
  const sides = Array.from({ length: 8 }, (_, index) => ({ sid: `s${index}`, title: `旁聊${index}` }));
  const props = { mainChat: { sid: "m" }, sideChats: sides, onSelect: () => {}, onNewSideChat: () => {},
    teams: [{ id: "t1", title: "比较三家向量数据库", status: "review" }] };
  const html = renderToStaticMarkup(<AssistantSidebar {...props} current="m" />);
  expect(html.indexOf("主要聊天")).toBeLessThan(html.indexOf("任务小组"));
  expect(html.indexOf("任务小组")).toBeLessThan(html.indexOf(">旁聊<"));
  expect(html.match(/>旁聊\d</g)).toHaveLength(SIDE_CHAT_LIMIT);
  expect(html).toContain("显示全部 8 条");
  const deep = renderToStaticMarkup(<AssistantSidebar {...props} current="s6" />);
  expect(deep).toContain(">旁聊6<");
  const searching = renderToStaticMarkup(<AssistantSidebar {...props} current="m" query="旁聊" />);
  expect(searching.match(/>旁聊\d</g)).toHaveLength(8);
});

it("recent-list disclosure describes its list and only offers a collapse that can hide rows", () => {
  const props = { mainChat: { sid: "m" }, sideChats: Array.from({ length: 8 }, (_, index) => ({ sid: `s${index}`, title: `旁聊${index}` })),
    onSelect: () => {}, onNewSideChat: () => {} };
  const html = renderToStaticMarkup(<AssistantSidebar {...props} current="m" />);
  const toggle = html.match(/<button[^>]*class="vc-side-more"[^>]*>/)?.[0];
  expect(toggle).toContain('aria-expanded="false"');
  const controls = toggle?.match(/aria-controls="([^"]+)"/)?.[1];
  expect(controls).toBeTruthy();
  expect(html).toContain(`class="vc-side-chats" id="${controls}"`);
  const lastChat = renderToStaticMarkup(<AssistantSidebar {...props} current="s7" />);
  expect(lastChat.match(/>旁聊\d</g)).toHaveLength(8);
  expect(lastChat).not.toContain('class="vc-side-more"');
  const inTeam = renderToStaticMarkup(<AssistantSidebar {...props} current="s7" currentTeam="t1"
    teams={[{ id: "t1", title: "小组工作", status: "active" }]} />);
  expect(inTeam.match(/>旁聊\d</g)).toHaveLength(5);
  expect(inTeam.match(/aria-current="page"/g)).toHaveLength(1);
});

it("blank chat titles have searchable names and long titles keep their full hint text", () => {
  const title = "本周交付与下一轮开发安排：核对原有计划和完整成果";
  const props = { mainChat: { sid: "m" }, current: "m", onSelect: () => {}, onNewSideChat: () => {},
    sideChats: [{ sid: "s1", title: " \t " }, { sid: "s2", title: ` ${title} ` }],
    phoneChats: [{ sid: "p1", title: " 📱  " }], offlineChats: [{ sid: "o1", title: "  " }] };
  const html = renderToStaticMarkup(<AssistantSidebar {...props} />);
  expect(html).toContain(`title="${title}"`);
  expect(html).toContain(`>${title}</span>`);
  expect(html).not.toContain("📱");
  for (const query of ["新的旁聊", "手机对话", "本机对话"]) {
    const found = renderToStaticMarkup(<AssistantSidebar {...props} query={query} />);
    expect(found).toContain(`>${query}</span>`);
    expect(found).not.toContain("未找到匹配的会话");
  }
});

it("日常页面复用相同的领域标签，插件仍使用独立页面", () => {
  for (const [page, tab] of Object.entries(PAGE_PANEL)) expect(PANEL_PAGE[tab]).toBe(page);
  expect(PANEL_PAGE.audit).toBe("audit");
  expect(PANEL_PAGE.identity).toBe("identity");
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

it("compact identity keeps status visible while model and computer controls are collapsed", () => {
  const online = renderToStaticMarkup(<AssistantPanel name="VortoOne" connected modelLabel="Qwen3.6" host={{ kind: "local", canAddResident: true }} onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}} waiting={2}><p>内容</p></AssistantPanel>);
  expect(online).toContain("已连接 · 在本机运行");
  expect(online).toContain('<small class="vc-panel-model">Qwen3.6</small>');
  expect(online).toContain('<details class="vc-panel-context"><summary>运行信息与电脑</summary>');
  expect(online).not.toContain('<details class="vc-panel-context" open');
  expect(online).toContain('aria-label="2 件等你处理"');
  const offline = renderToStaticMarkup(<AssistantPanel name="VortoOne" connected={false} host={{ kind: "local", canAddResident: true }} onAddResident={() => {}} onRetryResident={() => {}} tab="audit" onTab={() => {}} onClose={() => {}}><p>内容</p></AssistantPanel>);
  expect(offline).toContain("连接中断");
  expect(offline).not.toContain("vc-panel-model");
  expect(offline).not.toContain("等你处理");
  const noModel = renderToStaticMarkup(<AssistantPanel name="VortoOne" connected modelLabel="未连接模型" host={{ kind: "local", canAddResident: true }} onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}}><p /></AssistantPanel>);
  expect(noModel).toContain(">还没连接模型<");
});

it("名片说明助理住在哪：常驻电脑 / 本机；连不上常驻电脑时说清楚并给「重新连接」", () => {
  const panel = (host: import("./AssistantMode").AssistantHostInfo, connected = true) => renderToStaticMarkup(
    <AssistantPanel name="VortoOne" connected={connected} host={host} onAddResident={() => {}} onRetryResident={() => {}} tab="feed" onTab={() => {}} onClose={() => {}}
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
  for (const label of ["动态", "目标", "审核记录", "定时", "身份"]) expect(html).toContain(`aria-label="${label}"`);
  expect(html).toContain('data-hint="身份 · 资料、性格与记忆"');
  const tabs = [...html.matchAll(/<button[^>]*role="tab"[^>]*>/g)].map(match => match[0]);
  expect(tabs).toHaveLength(5);
  expect(tabs.filter(button => button.includes('tabindex="0"'))).toHaveLength(1);
  expect(tabs.find(button => button.includes('tabindex="0"'))).toContain('aria-label="身份"');
  const panel = html.match(/<div[^>]*role="tabpanel"[^>]*>/)?.[0];
  const id = panel?.match(/id="([^"]+)"/)?.[1];
  expect(id).toBeTruthy();
  for (const button of tabs) expect(button).toContain(`aria-controls="${id}"`);
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
    { id: "1", category: "decision", operation: "下发任务单「修登录」到 VortoOne", decision: "allowed" },
    { id: "2", category: "decision", operation: "发布制品？\n本周周报", decision: "denied", tainted: true },
    { id: "3", category: "tool", tool: "web_fetch" },
  ]} />);
  expect(html).toContain("下发任务单「修登录」到 VortoOne");
  expect(html).toContain("已拒绝 · 读过外部内容后");
  expect(html).not.toContain("web_fetch");
});

it.each([
  { name: "本机通用引擎", scope: "general", remote: null, visible: false },
  { name: "其它远端的通用引擎", scope: "general", remote: "other", visible: false },
  { name: "常驻电脑的项目引擎", scope: "project", remote: "resident", visible: false },
  { name: "常驻助理通用引擎", scope: "general", remote: "resident", visible: true },
] as const)("$name 只展示所属运行端的审核记录", ({ scope, remote, visible }) => {
  const coordinator = new ConnectionCoordinator();
  coordinator.assistantHost.current = "resident";
  coordinator.remote.current = remote;
  const client = { ownerAccess: vi.fn(async () => { throw new Error("render must not fetch permissions"); }) };
  const html = renderToStaticMarkup(<AssistantAudit connected
    accessClient={ownerAccessClient(scope, coordinator.onAssistantHost, client)}
    entries={[{ id: "local-decision", category: "decision", operation: "当前运行端的普通决定", decision: "allowed" }]} />);
  expect(html.includes('aria-label="手机权限与审批记录"')).toBe(visible);
  expect(html.includes("核对权限与记录")).toBe(visible);
  expect(html).toContain("当前运行端的普通决定");
  expect(client.ownerAccess).not.toHaveBeenCalled();
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
  const html = renderToStaticMarkup(<AssistantIdentity profile={{ name: "VortoOne", soul: "", about_you: "在杭州写代码" }}
    memories={[{ id: "m1", content: "装 VortoOne 的服务器就是助理的电脑", created_at: "2026-10-27T10:00:00" }]}
    proposals={[{ id: "p1", content: "x" } as never]} onAvatar={() => {}} onEdit={() => {}} onDeleteMemory={() => {}} />);
  expect(html).not.toContain("assistant-profile-head");
  for (const text of ["编辑资料", "换头像", "SOUL", "还没写", "在杭州写代码", "记得 1 件事", "1 条待确认", "装 VortoOne 的服务器就是助理的电脑"]) expect(html).toContain(text);
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
  expect(html).toContain("新对话");   // 新对话、搜索不受影响
});

it("search reports no matches only when main, team, phone, local and side chats all miss", () => {
  const props = { mainChat: { sid: "m" }, current: "m", onSelect: () => {}, onNewSideChat: () => {},
    sideChats: [{ sid: "s", title: "周报草稿" }], phoneChats: [{ sid: "p", title: "📱 买菜" }],
    offlineChats: [{ sid: "o", title: "旅行安排" }], teams: [{ id: "t", title: "修复登录", status: "active" }] };
  const missing = renderToStaticMarkup(<AssistantSidebar {...props} query="找不到的会话" />);
  expect(missing).toContain('role="status">未找到匹配的会话');
  expect(missing).toContain("标题关键词");
  expect(missing).toContain("新对话");
  for (const [query, label] of [["主要", "主要聊天"], ["周报", "周报草稿"], ["买菜", "买菜"], ["旅行", "旅行安排"], ["登录", "修复登录"]]) {
    const found = renderToStaticMarkup(<AssistantSidebar {...props} query={query} />);
    expect(found).not.toContain("未找到匹配的会话");
    expect(found).toContain(label + "</span>");
  }
});

it("whitespace keeps the normal recent-chat limit and empty guidance counts all chat sources", () => {
  const props = { mainChat: { sid: "m" }, current: "m", onSelect: () => {}, onNewSideChat: () => {},
    sideChats: Array.from({ length: 8 }, (_, index) => ({ sid: `s${index}`, title: `旁聊${index}` })) };
  const blank = renderToStaticMarkup(<AssistantSidebar {...props} query={" \t "} />);
  expect(blank.match(/>旁聊\d</g)).toHaveLength(5);
  expect(blank).toContain("显示全部 8 条");
  expect(blank).not.toContain("未找到匹配的会话");
  const found = renderToStaticMarkup(<AssistantSidebar {...props} query=" 旁聊 " />);
  expect(found.match(/>旁聊\d</g)).toHaveLength(8);
  expect(found).not.toContain("显示全部");
  const phoneOnly = renderToStaticMarkup(<AssistantSidebar {...props} sideChats={[]} phoneChats={[{ sid: "p", title: "手机记录" }]} />);
  expect(phoneOnly).not.toContain("开个旁聊");
  expect(phoneOnly).toContain("手机记录");
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

it("an open team is the only current item; the main chat is not also marked", () => {
  // 真机 2026-11-05：打开小组时「主要聊天」和小组两条同时显示选中
  const props = { mainChat: { sid: "m" }, sideChats: [{ sid: "s1", title: "旁聊" }], onSelect: () => {}, onNewSideChat: () => {},
    teams: [{ id: "t1", title: "比较三家向量数据库", status: "review" }] };
  const inTeam = renderToStaticMarkup(<AssistantSidebar {...props} current="m" currentTeam="t1" />);
  expect(inTeam.match(/aria-current="page"/g)).toHaveLength(1);
  expect(inTeam).toMatch(/aria-current="page"[^>]*>.*?比较三家向量数据库/);
  const inChat = renderToStaticMarkup(<AssistantSidebar {...props} current="m" />);
  expect(inChat.match(/aria-current="page"/g)).toHaveLength(1);
  expect(inChat).toMatch(/aria-current="page"[^>]*>.*?主要聊天/);
});
