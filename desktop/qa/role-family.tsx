/** Real components, synthetic owner records and transport. No runtime, model or private data. */
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowUp } from "lucide-react";
import { TrustChip, ComposerModelPicker, ConfirmCard } from "../src/components/ComposerPanels";
import { RoleNavigation } from "../src/components/RoleNavigation";
import { RoleProfileDialog, type RoleProfileSection } from "../src/components/RoleProfile";
import { NewRoleDialog } from "../src/components/NewRoleDialog";
import { WorkspaceBar } from "../src/components/WorkspaceBar";
import { RoleGrants } from "../src/components/settings/StaffPane";
import { staffCharacter, ROLE_ART } from "../src/components/RoleAvatar";
import { ConversationHome } from "../src/components/ConversationHome";
import { AssistantPanel, AssistantSidebar, AssistantSchedule, AssistantAudit, PAGE_PANEL, type AssistantPage, type AssistantTab } from "../src/components/AssistantMode";
import { AssistantActivity } from "../src/components/AssistantActivity";
import { ArtifactCenter } from "../src/components/ArtifactCenter";
import { CustomizePage } from "../src/components/CustomizePage";
import { ChatHistory } from "../src/components/ChatHistory";
import { ComposerCommands } from "../src/components/ComposerCommands";
import { ConversationHeader } from "../src/components/ConversationHeader";
import { PersonalSpacePicker } from "../src/components/PersonalSpacePicker";
import { SidebarSearch } from "../src/components/SidebarChrome";
import { SidebarFooter } from "../src/components/SidebarFooter";
import { Button, PreferenceGroup, PreferenceRow } from "../src/components/ui";
import { useComposerDraft } from "../src/hooks/useComposerDraft";
import { desktopContext } from "../src/lib/desktopContext";
import { settingsTargetLease, type SettingsTargetLease } from "../src/lib/settingsTarget";
import type { ConversationMessage, DesktopProjectProfile, TrustLevel } from "../src/types";
import type { StaffRole, StaffSnapshot, StaffMember } from "../src/lib/staff";
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
  { id: "user", role: "user", rid: "demo", text: "把本周的资料整理成一份简短摘要。" },
  { id: "assistant", role: "assistant", rid: "demo", text: "资料已按主题整理。\n\n- **项目进展**：界面与使用流程的资料已经归在一起。\n- **待你决定**：先看本机运行，还是连接常驻电脑。\n- **下一步**：可从资料库回查完整记录。\n\n这是一段合成示例，没有读取你的资料或执行任务。" },
];
const date = "2026-11-14T09:00:00Z";
const roles: StaffRole[] = [
  { id: "public-researcher", title: "资料专员", brief: "核对公开来源，整理资料摘要。", enabled: true, revision: 1, builtin: true,
    grants: { operations: ["read"], tools: ["web_search", "web_fetch"], sees: [], web: true, project: false }, engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 },
  { id: "calendar-reader", title: "日程专员", brief: "整理今日安排，提醒需要你核对的冲突。", enabled: true, revision: 1, builtin: true,
    grants: { operations: ["read"], tools: ["calendar_events"], sees: ["plugin:calendar"], web: false, project: false }, engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 },
  { id: "developer", title: "开发专员", brief: "在登记项目的一次性副本中工作。", enabled: true, revision: 1, builtin: true,
    grants: { operations: ["read", "write"], tools: ["read_file", "edit_file"], sees: [], web: false, project: true }, engine: ["opencode"], max_steps: 40, timeout_seconds: 1800,
    project: "示例项目", project_binding: { name: "示例项目", repo_root: "/example/project", port: 8080 } },
];
const roster: StaffSnapshot = { roles, proposals: [], members: roles.map((role, index) => ({ id: `demo-${index}`, name: ({ "public-researcher": "研究助手", "calendar-reader": "日程助手", developer: "开发助手" } as Record<string, string>)[role.id] || role.title,
  role: role.id, engine: role.engine[0], model: "示例模型", revision: 1, status: "active", busy: 0, recent_tasks: [] })) };
function Fixture() {
  const params = new URLSearchParams(location.search);
  const disconnected = params.has("disconnected"), busy = params.has("busy");
  const [trust, setTrust] = useState<TrustLevel>("ask");
  const [host, setHost] = useState(params.has("disconnected") ? profiles[1] : profiles[0]), [sid, setSid] = useState("main"), [generation, setGeneration] = useState(1);
  const [theme, setTheme] = useState(params.get("theme") || "light"), [collapsed, setCollapsed] = useState(false);
  const [search, setSearch] = useState(params.has("search")), [query, setQuery] = useState(""), [rosterState, setRosterState] = useState("ready");
  const [page, setPage] = useState<AssistantPage | null>(null), [tab, setTab] = useState<AssistantTab>("feed"), [panel, setPanel] = useState(params.has("panel"));
  const [notice, setNotice] = useState(""), [localMessages, setLocalMessages] = useState<ConversationMessage[]>([]);
  const context = desktopContext({ registrationId: host.id, assistantId: host.id, scope: "general", repoRoot: "", sid,
    baseUrl: host.baseUrl, runtimeId: "", connection: disconnected ? "disconnected" : "connected", profiles, localResidentId: "mac",
    source: { registrationId: host.id, baseUrl: host.baseUrl, sid, generation, epoch: 1 } });
  const client = useMemo(() => ({ connectionEpoch: generation, desktopSource: { registrationId: host.id, baseUrl: host.baseUrl, sid, generation, epoch: 1 } }), [host, sid, generation]);
  const source = useRef({ context, client }); source.current = { context, client };
  const composer = useComposerDraft(context, setNotice);
  const [selection, setSelection] = useState<{ member?: StaffMember; role?: StaffRole; lease: SettingsTargetLease<typeof client> } | null>(null);
  const [add, setAdd] = useState<SettingsTargetLease<typeof client> | null>(null);
  const [artifact, setArtifact] = useState("");
  const sources = useMemo(() => (["turns", "dispatches", "codexActions", "notices", "tasks"] as const).map(name => ({ name, status: "current" as const, checkedAt: Date.now() })), []);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const openRole = (memberId?: string) => {
    const member = roster.members.find(member => member.id === memberId);
    setSelection({ member, role: roles.find(role => role.id === member?.role), lease: settingsTargetLease(() => source.current) });
  };
  const openPage = (next: AssistantPage) => {
    if (next === "identity") { openRole(); return; }
    setPage(next); setPanel(false);
    if (next !== "library" && next !== "customize") setTab(PAGE_PANEL[next]);
  };
  const current = Boolean(selection?.lease.current()) && (!selection?.member || rosterState === "ready");
  const sections: RoleProfileSection[] = selection ? [
    { id: "settings", label: "设置", content: <><PreferenceGroup title="角色资料">
      <PreferenceRow title="名字">{selection.member?.name || "VortoOne"}</PreferenceRow>
      <PreferenceRow title="权限方案">{selection.role?.title || "总助手"}</PreferenceRow>
      <PreferenceRow title="模型">{selection.member?.model || "示例模型"}</PreferenceRow>
      <PreferenceRow title="状态">{current ? "已核对" : "待核对"}</PreferenceRow></PreferenceGroup>
      <Button onClick={() => setNotice("完整应用中使用原有角色管理与主人批准流程")}>角色与权限</Button>
      {params.has("stress") && <Button onClick={() => setGeneration(value => value + 1)}>模拟来源变化</Button>}</> },
    { id: "access", label: "工具与权限", content: selection.role ? <RoleGrants role={selection.role} />
      : <PreferenceGroup title="操作确认"><PreferenceRow title="工具与连接" description="沿用会话能力与原确认规则">示例，不授予权限</PreferenceRow></PreferenceGroup> },
    selection.member ? { id: "activity", label: "工作记录", content: <p>还没有工作记录。</p> }
      : { id: "memory", label: "记忆", content: <p>还没有记忆。这份界面验收没有读取个人资料。</p> },
  ] : [];
  const send = () => {
    if (!composer.prompt.trim()) return;
    setLocalMessages(items => [...items, { id: `demo-${items.length}`, role: "user", text: composer.prompt }]);
    composer.setPromptState(""); setNotice("仅展示输入效果，没有调用模型或发送到服务。");
  };
  const sendControl = <button className="composer-send" aria-label={busy ? "加入待运行队列" : "发送"} disabled={!composer.prompt.trim()} onClick={send}><ArrowUp aria-hidden="true" /></button>;
  const content = tab === "schedule" ? <AssistantSchedule connected={!disconnected} items={[
    { id: "daily", kind: "report", title: "每日早报", schedule: "每天 09:00", detail: "合成排期，不执行", enabled: true }]} />
    : tab === "audit" ? <AssistantAudit entries={[]} />
    : <AssistantActivity connected={!disconnected} items={[]} sources={sources} onOpen={() => {}} />;
  return <div className="app-shell vc-product-shell qa-reference-shell">
    <div className="qa-role-controls"><header className="qa-redesign-controls" hidden={params.has("capture")}><span>正式组件 · 合成资料 · 不连接服务</span>
      <button onClick={() => setTheme(theme === "light" ? "dark" : "light")}>切换主题</button>
      <button onClick={() => { setSid("empty"); setPage(null); }}>空白对话</button>
      <select aria-label="名录状态" value={rosterState} onChange={event => setRosterState(event.target.value)}><option value="ready">已核对名录</option><option value="empty">空名录</option><option value="error">读取失败</option><option value="stale">旧名录</option></select>
      <a href="/qa/settings-redesign.html?presentation=page&section=account&theme=light">全局设置</a>
      <a href="/qa/runtime-onboarding.html?theme=light">安装引导</a></header></div>
    <WorkspaceBar collapsed={collapsed} onToggle={() => setCollapsed(value => !value)} onSearch={() => { setCollapsed(false); setSearch(true); }} />
    <div className={`workbench vc-mode-assistant ${collapsed ? "vc-sidebar-collapsed" : ""} ${panel && !page ? "inspector-open" : "inspector-closed"}`}>
      <aside id="conversation-sidebar" className="sidebar vc-sidebar">
        <PersonalSpacePicker context={context} profiles={profiles} localResidentId="mac" connection={disconnected ? "disconnected" : "connected"} switching={false}
          onSelect={async next => { setHost(next); setGeneration(value => value + 1); setSid("main"); }} onManage={() => { location.href = "/qa/settings-redesign.html?presentation=page&section=computer"; }} />
        <AssistantSidebar mainChat={{ sid: "main" }} current={sid} sideChats={composer.draftChats.filter(chat => chat.sid !== "main")} page={page}
          onPage={openPage} onSelect={next => { setSid(next); setPage(null); }} onNewSideChat={() => { setSid(`side-${Date.now()}`); setPage(null); }}
          search={search ? <SidebarSearch open={search} query={query} placeholder="搜索聊天" onOpenChange={setSearch} onQuery={setQuery} /> : undefined} query={query}
          channels={[{ sid: "channel-daily", title: "日常", updated: Date.parse(date) / 1000, messages: 0, channel: { version: 1, topic: "日常事项" } },
            { sid: "channel-research", title: "资料研究", updated: Date.parse(date) / 1000, messages: 0, channel: { version: 1, topic: "公开资料" } }]}
          onNewChannel={() => setNotice("频道沿用正式版原创建流程，此夹具不保存记录")}
          roles={<RoleNavigation name="VortoOne" directory={rosterState === "empty" ? { ...roster, members: [] } : roster}
            connected={context.ready && rosterState !== "stale"} error={rosterState === "error" ? "合成读取失败" : ""} onRefresh={() => setRosterState("ready")}
            onAssistant={() => openRole()} onMember={openRole} onAdd={() => setAdd(settingsTargetLease(() => source.current))} />} />
        <SidebarFooter identity={{ initial: "", name: "我的账号", detail: "个人空间", title: "设置与账号" }} update={null}
          resident={{ state: disconnected ? "offline" : "online", name: host.name, onClick: () => openPage("tasks") }}
          onOpenSettings={target => { location.href = `/qa/settings-redesign.html?presentation=page&theme=${theme}&section=${target?.section || "account"}`; }} />
      </aside>
      {page === "library" ? <ArtifactCenter artifacts={[{ id: "sample", title: "本周资料摘要", kind: "markdown", version: 1, bytes: 512, created_at: date, updated_at: date, url: "" }]} loading={false} error="" selectedId={artifact} version={1} versions={null}
        previewLoading={false} previewHtml="<h1>合成摘要</h1><p>没有读取真实资料。</p>" onRefresh={() => {}} onOpen={setArtifact} onVersion={() => {}} onOpenExternal={() => {}} />
        : page === "customize" ? <CustomizePage plugins={[]} checks={{}} busy="" onCheck={() => {}} onRemove={() => {}} onSave={async () => false} connectors={[]} onConnector={() => {}} skills={<p>没有登记技能。</p>} connected />
        : page ? <AssistantPanel name="VortoOne" connected={!disconnected} modelLabel="示例模型" onOpenProfile={() => openRole()} host={host.id === "mac" ? { kind: "local", canAddResident: true } : { kind: "resident", name: host.name }} tab={tab} onTab={setTab} onClose={() => setPage(null)} onAddResident={() => {}} onRetryResident={() => {}} presentation="page">{content}</AssistantPanel>
        : <section className={`conversation ${sid === "empty" && !localMessages.length ? "empty-state" : ""}`}>
          <ConversationHeader title={sid === "main" ? "主要聊天" : sid.startsWith("channel-") ? "频道" : "旁聊"} roleIdentity={{ name: "VortoOne", onOpen: () => openRole() }}
            place={null} panelOpen={panel} waiting={0} plan={{ done: 0, total: 0 }} busy={busy} onTogglePanel={() => setPanel(value => !value)} onStop={() => {}} />
          <div className="conversation-body"><div className="conversation-scroll">
            {sid === "empty" && !localMessages.length && <ConversationHome assistant starting={false} showStarting={false} now={new Date("2026-11-14T09:00:00")} />}
            <ChatHistory messages={[...(sid === "main" ? messages : []), ...(params.has("image") ? [{ id: "image-only", role: "user" as const, text: "", source: "telegram", images: [ROLE_ART.general] }] : []), ...(params.has("stress") ? [{ id: "long", role: "assistant" as const, text: "### 较长的资料示例\n\n```text\n" + "long_file_name_".repeat(40) + "\n```\n\n| 标题 | 进展 |\n| --- | --- |\n| 资料整理 | 等待核对 |\n\nhttps://example.com/" + "longpath".repeat(40) }] : []), ...localMessages]} assistant activityGroups={new Map()} busy={false} activeTurnRid={null} />
            {params.has("confirm") && <ConfirmCard confirm={{ id: "example", text: "合成示例：发送这份摘要？", tainted: true, operation_kind: "deliver" }} trust={null} trustBusy={false} connected={!disconnected} onTrust={() => {}} onCheck={() => {}} onAnswer={() => setNotice("合成确认，不执行操作")} />}
          </div></div>
          <div className="composer-wrap"><div className="composer"><ComposerCommands prompt={composer.prompt} onPrompt={composer.setPromptState} project={false} disabled={false} onSend={send} onAction={command => setNotice(`已选择快捷操作：${command}`)} sendControl={sendControl} placeholder="交代一件事…">
            <div className="composer-footer"><div className="composer-tools"><TrustChip trust={{level:trust,effective:trust,ceiling:"full",levels:["ask","reads","full"],capability_profile:"owner_device",workspace_scope:"general"}} busy={false} onChange={setTrust} /></div><div className="composer-actions"><ComposerModelPicker residentModel="示例模型" onResident checked choices={[]} value="" profile={null} onChoose={() => {}} onManage={() => {}} /></div></div>
          </ComposerCommands></div><p role="status" className="qa-role-notice">{notice}</p></div>
        </section>}
      {!page && panel && <AssistantPanel name="VortoOne" connected={!disconnected} modelLabel="示例模型" onOpenProfile={() => openRole()} host={host.id === "mac" ? { kind: "local", canAddResident: true } : { kind: "resident", name: host.name }} tab={tab} onTab={setTab} onClose={() => setPanel(false)} onAddResident={() => {}} onRetryResident={() => {}}>{content}</AssistantPanel>}
    </div>
    {selection && <RoleProfileDialog name={selection.member?.name || "VortoOne"} description={selection.role?.brief || "总助手 · 由你决定如何行动"}
      character={selection.member ? staffCharacter(selection.role) : "general"} current={current} sections={sections} onClose={() => setSelection(null)} />}
    {add && <NewRoleDialog roles={roles} current={add.current} onClose={() => setAdd(null)} onPrepare={draft => {
      if (!add.current()) return;
      composer.setPromptState(previous => previous.trim() ? `${previous}\n\n${draft}` : draft); setAdd(null); setPage(null); setNotice("已准备草稿，尚未发送或添加角色。");
    }} />}
  </div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
