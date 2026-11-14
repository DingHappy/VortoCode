/** Real presentation components, synthetic records. Never accesses a runtime or credentials. */
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { StaffWorkspace, AssistantIdentityLink } from "../src/components/StaffWorkspace";
import { StaffView } from "../src/components/settings/StaffPane";
import { ConversationHome } from "../src/components/ConversationHome";
import type { StaffRole, StaffSnapshot } from "../src/lib/staff";
import { IconButton } from "../src/components/ui";
import { ArrowUp, Settings, PanelLeftClose } from "lucide-react";
import { AssistantAudit, AssistantIdentity, AssistantPanel, AssistantSchedule, AssistantSidebar, PAGE_PANEL, type AssistantPage, type AssistantTab } from "../src/components/AssistantMode";
import { AssistantActivity } from "../src/components/AssistantActivity";
import { AssistantGoals } from "../src/components/AssistantGoals";
import { ArtifactCenter } from "../src/components/ArtifactCenter";
import { CustomizePage } from "../src/components/CustomizePage";
import { ChatHistory } from "../src/components/ChatHistory";
import { ComposerCommands } from "../src/components/ComposerCommands";
import { ConversationHeader } from "../src/components/ConversationHeader";
import { PersonalSpacePicker } from "../src/components/PersonalSpacePicker";
import { SidebarSearch } from "../src/components/SidebarChrome";
import { useComposerDraft } from "../src/hooks/useComposerDraft";
import { desktopContext } from "../src/lib/desktopContext";
import type { PersonalGoalClient } from "../src/lib/personalGoals";
import type { ConversationMessage, DesktopProjectProfile, TurnActivity } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./frontend-redesign.css";

const profiles: DesktopProjectProfile[] = [
  { id: "mac", name: "这台 Mac", kind: "remote", repoRoot: "@assistant", baseUrl: "https://local.example", lastOpenedAt: 0 },
  { id: "resident", name: "个人助手", kind: "remote", repoRoot: "@assistant", baseUrl: "https://assistant.example", lastOpenedAt: 0 },
];
const messages: ConversationMessage[] = [
  { id: "question", role: "user", rid: "demo", text: "把本周的资料整理成一份简短摘要。" },
  { id: "answer", role: "assistant", rid: "demo", text: "资料已按主题整理。\n\n- **项目进展**：界面与使用流程的资料已经归在一起。\n- **待你决定**：先看本机运行，还是连接常驻电脑。\n- **下一步**：可从资料库回查完整记录。\n\n这是一段合成示例，用于检查中文排版；没有读取你的资料，也没有执行任务。" },
];
const activities = new Map<string, TurnActivity[]>([["demo", [{ id: "read", rid: "demo", kind: "tool", status: "completed", label: "读取示例资料", tool: "read_file", result: "合成内容" }]]]);
const date = "2026-11-14T09:00:00Z";
const artifacts = [{ id: "brief", title: "本周资料摘要 · 合成示例", kind: "markdown", version: 2, bytes: 2048, created_at: date, updated_at: date, url: "" }];
const goalClient: PersonalGoalClient = {
  listPersonalGoals: async () => [],
  createPersonalGoal: async () => { throw Error("此界面夹具不创建真实目标"); },
  updatePersonalGoal: async () => { throw Error("此界面夹具不更新目标"); },
  confirmPersonalGoal: async () => { throw Error("此界面夹具不批准目标"); },
  configurePersonalGoalFollowUp: async () => { throw Error("此界面夹具不创建提醒"); },
  acknowledgePersonalGoalFollowUp: async () => { throw Error("此界面夹具不确认提醒"); },
};
const roles: StaffRole[] = [
  { id: "public-researcher", title: "资料专员", brief: "核对公开来源，整理资料摘要。", enabled: true, revision: 1, builtin: true,
    grants: { operations: ["read"], tools: ["web_search", "web_fetch"], sees: [], web: true, project: false }, engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 },
  { id: "calendar-reader", title: "日程专员", brief: "整理今日安排，提醒需要你核对的冲突。", enabled: true, revision: 1, builtin: true,
    grants: { operations: ["read"], tools: ["calendar_events"], sees: ["plugin:calendar"], web: false, project: false }, engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 },
  { id: "developer", title: "开发专员", brief: "在登记项目的一次性副本中工作。", enabled: true, revision: 1, builtin: true,
    grants: { operations: ["read", "write"], tools: ["read_file", "edit_file"], sees: [], web: false, project: true }, engine: ["opencode"], max_steps: 40, timeout_seconds: 1800,
    project: "示例项目", project_binding: { name: "示例项目", repo_root: "/example/project", port: 8080 } },
];
const roster: StaffSnapshot = { roles, proposals: [], members: roles.map((role, index) => ({ id: `demo-${index}`, name: role.title,
  role: role.id, engine: role.engine[0], model: "示例模型", revision: 1, status: "active", busy: 0, recent_tasks: [] })) };
function Fixture() {
  const [staff, setStaff] = useState(new URLSearchParams(location.search).get("screen") === "staff");
  const [rosterState, setRosterState] = useState("ready");

  const [host, setHost] = useState(profiles[1]), [sid, setSid] = useState("main"), [generation, setGeneration] = useState(1);
  const [page, setPage] = useState<AssistantPage | null>(null), [tab, setTab] = useState<AssistantTab>("feed"), [panel, setPanel] = useState(false);
  const [theme, setTheme] = useState(new URLSearchParams(location.search).get("theme") === "light" ? "light" : "dark");
  const [search, setSearch] = useState(false), [query, setQuery] = useState(""), [notice, setNotice] = useState("");
  const [selected, setSelected] = useState(""), [version, setVersion] = useState(2), [collapsed, setCollapsed] = useState(false);
  const [localMessages, setLocalMessages] = useState<ConversationMessage[]>([]);
  const trigger = useRef<HTMLElement | null>(null), sequence = useRef(0);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const context = desktopContext({ registrationId: host.id, assistantId: host.id, scope: "general", repoRoot: "", sid,
    baseUrl: host.baseUrl, runtimeId: "", connection: "connected", profiles, localResidentId: "mac",
    source: { registrationId: host.id, baseUrl: host.baseUrl, sid, generation, epoch: 1 } });
  const composer = useComposerDraft(context, setNotice);
  const sources = useMemo(() => (["turns", "dispatches", "codexActions", "notices"] as const).map(name => ({ name, status: "current" as const, checkedAt: Date.now() })), []);
  const openPage = (next: AssistantPage) => {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setPage(next); setPanel(false); setStaff(false);
    if (next !== "library" && next !== "customize") setTab(PAGE_PANEL[next]);
  };
  const closePage = () => { setPage(null); requestAnimationFrame(() => trigger.current?.focus()); };
  const send = () => {
    if (!composer.prompt.trim()) return;
    setLocalMessages(items => [...items, { id: `sent-${++sequence.current}`, role: "user", text: composer.prompt },
      { id: `receipt-${sequence.current}`, role: "system", text: "仅展示输入效果，没有调用模型或发送到服务。" }]);
    composer.setPromptState("");
  };
  const content = tab === "schedule" ? <AssistantSchedule connected items={[
    { id: "brief", kind: "report", title: "每日早报", schedule: "每天 09:00", detail: "示例排期；此处不会执行", enabled: true },
    { id: "weekly", kind: "cron", title: "本周资料跟踪", schedule: "每周五 17:00", detail: "合成例行工作", enabled: false },
  ]} /> : tab === "audit" ? <AssistantAudit entries={[{ id: "audit-demo", category: "decision", operation: "外发示例草稿", decision: "denied", ts: date }]} />
    : tab === "identity" ? <AssistantIdentity profile={{ name: "小 V", soul: "简洁、清楚，有依据地汇报。", about_you: "个人空间的合成示例" }} memories={[]} onEdit={() => setNotice("在完整应用中打开原有资料编辑器")} onDeleteMemory={() => {}} />
    : tab === "goals" ? <AssistantGoals client={goalClient} connected />
    : <AssistantActivity connected items={[{ id: "record", title: "本周资料摘要 · 合成示例", summary: "仅用于界面验收，没有实际运行任务", status: "done", sourceLabel: "示例记录", at: date }]} sources={sources} onOpen={() => setNotice("示例记录，不对应真实任务")} />;
  return <div className="app-shell qa-redesign-shell">
    <header className="qa-redesign-controls"><span>真实组件 · 合成界面验收 · 不连接服务</span>
      <button onClick={() => { setSid("empty"); setStaff(false); setPage(null); setLocalMessages([]); }}>空白对话</button>
      <select aria-label="名录状态" value={rosterState} onChange={event => setRosterState(event.target.value)}><option value="ready">已核对名录</option><option value="empty">空名录</option><option value="error">读取失败</option><option value="stale">旧名录</option></select>
      <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>切换主题</button>
      {collapsed && <button onClick={() => setCollapsed(false)}>展开侧栏</button>}</header>
    <div className={`workbench vc-mode-assistant ${collapsed ? "vc-sidebar-collapsed" : ""} ${!page && panel ? "inspector-open" : "inspector-closed"}`}>
      <aside className="sidebar vc-sidebar"><div className="vc-personal-window-drag" data-tauri-drag-region="deep" aria-hidden="true" />
        <PersonalSpacePicker context={context} profiles={profiles} localResidentId="mac" connection="connected" switching={false}
          collapseControl={<IconButton label="收起侧栏" onClick={() => setCollapsed(true)} icon={<PanelLeftClose />} />}
          onSelect={async profile => { setHost(profile); setSid("main"); setGeneration(value => value + 1); setPage(null); setLocalMessages([]); }} onManage={() => { location.href = "/qa/settings-redesign.html?section=computer"; }} />
        <AssistantSidebar mainChat={{ sid: "main" }} current={sid} sideChats={composer.draftChats.filter(chat => chat.sid !== "main")} page={page}
          channels={[{ sid: "channel:daily", title: "日常", updated: Date.parse(date) / 1000, messages: 0, channel: { version: 1, topic: "日常讨论" } }, { sid: "channel:research", title: "资料研究", updated: Date.parse(date) / 1000, messages: 0, channel: { version: 1, topic: "公开资料" } }]}
          onNewChannel={() => setNotice("此处仅展示角色界面；频道使用正式版既有创建流程")} onPage={openPage} staffOpen={staff} onManageStaff={() => { setStaff(true); setPage(null); setPanel(false); }}
          search={<SidebarSearch open={search} query={query} placeholder="搜索聊天" onOpenChange={setSearch} onQuery={setQuery} />} query={query}
          onNewSideChat={() => { setSid(`draft-${++sequence.current}`); setPage(null); setLocalMessages([]); }} onSelect={id => { setStaff(false); setSid(id); setPage(null); setPanel(false); setLocalMessages([]); }} />
        <AssistantIdentityLink name="VortoOne" onOpen={() => openPage("identity")} /><footer className="qa-redesign-footer"><a href="/qa/settings-redesign.html?section=appearance"><Settings aria-hidden="true" />设置</a></footer>
      </aside>
      {staff ? <StaffWorkspace onBack={() => setStaff(false)} onSettings={() => setNotice("完整应用中打开电脑与连接设置")}><StaffView
        state={{ value: rosterState === "empty" ? { ...roster, members: [] } : roster, busy: false, error: rosterState === "error" ? "合成读取失败" : "", checkedAt: rosterState === "stale" ? 0 : Date.now() }}
        onRefresh={() => setRosterState("ready")} onStatus={() => setNotice("仅供界面验收，没有修改真实专员")} onDecide={() => setNotice("仅供界面验收，没有批准真实登记")} /></StaffWorkspace>
        : page === "library" ? <ArtifactCenter artifacts={artifacts} loading={false} error="" selectedId={selected} version={version} versions={{ id: "brief", current: 2, versions: [{ v: 1, ts: date, bytes: 1024 }, { v: 2, ts: date, bytes: 2048 }] }}
        previewLoading={false} previewHtml={`<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'"><h1>合成资料摘要 v${version}</h1><p>没有读取真实文件。</p>`} onRefresh={() => setNotice("合成资料已刷新")} onOpen={setSelected} onVersion={(_, next) => setVersion(next)} onOpenExternal={() => setNotice("此处不打开外部页面")} />
        : page === "customize" ? <CustomizePage plugins={[]} checks={{}} busy="" onCheck={() => {}} onRemove={() => {}} onSave={async () => false} connectors={[]} onConnector={() => {}} skills={<p>合成环境没有登记技能。</p>} connected />
        : page ? null : <section className={`conversation ${sid === "empty" && !localMessages.length ? "empty-state" : ""}`}>
          <ConversationHeader title={sid === "main" ? "主要聊天" : "新的旁聊"} place={null} panelOpen={panel} panelId="qa-assistant-panel" waiting={0} plan={{ done: 0, total: 0 }} busy={false} onTogglePanel={() => setPanel(!panel)} onStop={() => {}} />
          <div className="conversation-body"><div className="conversation-scroll">{sid === "empty" && !localMessages.length && <ConversationHome starting={false} showStarting={false} assistant now={new Date("2026-11-14T09:00:00")} />}<ChatHistory messages={[...(sid === "main" ? messages : []), ...localMessages]} activityGroups={activities} busy={false} activeTurnRid={null} assistant /></div></div>
          <div className="composer-wrap"><div className="composer"><ComposerCommands prompt={composer.prompt} onPrompt={text => { composer.markComposerEdited(); composer.setPromptState(text); }} project={false} disabled={false} onSend={send} onAction={action => setNotice(`示例操作：${action}，没有调用服务`)} placeholder="交代一件事…">
            <div className="composer-footer"><span>示例模型 · 无请求</span><button className="composer-send" aria-label="发送" disabled={!composer.prompt.trim()} onClick={send}><ArrowUp aria-hidden="true" /></button></div>
          </ComposerCommands></div><p role="status">{notice}</p></div>
        </section>}
      {(page && page !== "library" && page !== "customize" || !page && panel) && <AssistantPanel id="qa-assistant-panel" presentation={page ? "page" : "panel"} name="小 V" connected host={{ kind: "resident", name: host.name }}
        modelLabel="示例模型" onAddResident={() => {}} onRetryResident={() => {}} tab={tab} onTab={setTab} onClose={page ? closePage : () => setPanel(false)}>{content}</AssistantPanel>}
    </div>
  </div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
