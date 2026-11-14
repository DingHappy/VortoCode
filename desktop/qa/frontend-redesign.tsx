/** Real presentation components, synthetic records. Never accesses a runtime or credentials. */
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowUp, Settings } from "lucide-react";
import { AssistantAudit, AssistantIdentity, AssistantPanel, AssistantSchedule, AssistantSidebar, PAGE_PANEL, type AssistantPage, type AssistantTab } from "../src/components/AssistantMode";
import { AssistantActivity } from "../src/components/AssistantActivity";
import { AssistantGoals } from "../src/components/AssistantGoals";
import { ArtifactCenter } from "../src/components/ArtifactCenter";
import { CustomizePage } from "../src/components/CustomizePage";
import { ChatHistory } from "../src/components/ChatHistory";
import { ComposerCommands } from "../src/components/ComposerCommands";
import { ConversationHeader } from "../src/components/ConversationHeader";
import { PersonalSpacePicker } from "../src/components/PersonalSpacePicker";
import { SidebarBrand, SidebarSearch } from "../src/components/SidebarChrome";
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
function Fixture() {
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
    setPage(next); setPanel(false);
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
      <a href="/qa/runtime-onboarding.html?theme=dark">安装引导</a><a href="/qa/settings-redesign.html?section=computer&theme=light">设置</a>
      <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>切换主题</button>
      {collapsed && <button onClick={() => setCollapsed(false)}>展开侧栏</button>}</header>
    <div className={`workbench vc-mode-assistant ${collapsed ? "vc-sidebar-collapsed" : ""} ${!page && panel ? "inspector-open" : "inspector-closed"}`}>
      <aside className="sidebar vc-sidebar">
        <SidebarBrand onCollapse={() => setCollapsed(true)} />
        <PersonalSpacePicker context={context} profiles={profiles} localResidentId="mac" connection="connected" switching={false}
          onSelect={async profile => { setHost(profile); setSid("main"); setGeneration(value => value + 1); setPage(null); setLocalMessages([]); }} onManage={() => { location.href = "/qa/settings-redesign.html?section=computer"; }} />
        <AssistantSidebar mainChat={{ sid: "main" }} current={sid} sideChats={composer.draftChats.filter(chat => chat.sid !== "main")} page={page}
          onPage={openPage} onManageStaff={() => { location.href = "/qa/settings-redesign.html?section=staff&staff=ready"; }}
          search={<SidebarSearch open={search} query={query} placeholder="搜索聊天" onOpenChange={setSearch} onQuery={setQuery} />} query={query}
          onNewSideChat={() => { setSid(`draft-${++sequence.current}`); setPage(null); setLocalMessages([]); }} onSelect={id => { setSid(id); setPage(null); setPanel(false); setLocalMessages([]); }} />
        <footer className="qa-redesign-footer"><a href="/qa/settings-redesign.html?section=appearance"><Settings aria-hidden="true" />设置</a></footer>
      </aside>
      {page === "library" ? <ArtifactCenter artifacts={artifacts} loading={false} error="" selectedId={selected} version={version} versions={{ id: "brief", current: 2, versions: [{ v: 1, ts: date, bytes: 1024 }, { v: 2, ts: date, bytes: 2048 }] }}
        previewLoading={false} previewHtml={`<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'"><h1>合成资料摘要 v${version}</h1><p>没有读取真实文件。</p>`} onRefresh={() => setNotice("合成资料已刷新")} onOpen={setSelected} onVersion={(_, next) => setVersion(next)} onOpenExternal={() => setNotice("此处不打开外部页面")} />
        : page === "customize" ? <CustomizePage plugins={[]} checks={{}} busy="" onCheck={() => {}} onRemove={() => {}} onSave={async () => false} connectors={[]} onConnector={() => {}} skills={<p>合成环境没有登记技能。</p>} connected />
        : page ? null : <section className="conversation">
          <ConversationHeader title={sid === "main" ? "主要聊天" : "新的旁聊"} place={null} panelOpen={panel} panelId="qa-assistant-panel" waiting={0} plan={{ done: 0, total: 0 }} busy={false} onTogglePanel={() => setPanel(!panel)} onStop={() => {}} />
          <div className="conversation-scroll"><ChatHistory messages={[...(sid === "main" ? messages : []), ...localMessages]} activityGroups={activities} busy={false} activeTurnRid={null} assistant /></div>
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
