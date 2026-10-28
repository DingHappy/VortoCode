// 工作台侧栏 + 任务页验收：合成数据，不连引擎。?theme=dark 看深色，?tab=goals 看目标页。
import { createRoot } from "react-dom/client";
import { ModeSwitch } from "../src/components/AssistantMode";
import { ManagementHead, ManagementNav } from "../src/components/ManagementNav";
import { RuntimeInboxPanel } from "../src/components/RuntimeInboxPanel";
import { SidebarBrand, SidebarSectionTitle, WorkNav } from "../src/components/SidebarChrome";
import { SidebarFooter } from "../src/components/SidebarFooter";
import { SidebarProjects } from "../src/components/SidebarProjects";
import { SidebarSessions } from "../src/components/SidebarSessions";
import type { InspectorTab } from "../src/lib/inspector";
import type { DesktopProjectProfile, DesktopRuntimeInbox, SessionSummary } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";

const params = new URLSearchParams(location.search);
if (params.get("theme") === "dark") document.documentElement.dataset.theme = "dark";
const tab = (params.get("tab") || "inbox") as InspectorTab;
const noop = () => {};
const now = Math.floor(Date.now() / 1000);
const iso = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString();

const projects: DesktopProjectProfile[] = [
  { id: "p1", name: "示例项目", kind: "local", repoRoot: "/demo/app", baseUrl: "", lastOpenedAt: now },
  { id: "p2", name: "个人博客", kind: "local", repoRoot: "/demo/blog", baseUrl: "", lastOpenedAt: now - 3600 },
];
const sessions: SessionSummary[] = [
  { sid: "s1", title: "修登录超时", messages: 8, updated: now - 60, status: "working" } as SessionSummary,
  { sid: "s2", title: "加边界单测", messages: 12, updated: now - 86400 } as SessionSummary,
];
const snapshot = (tasks: unknown[], decisions: unknown[], liveSessions: unknown[]) => ({
  version: 1, scope: "project", generated_at: iso(1), counts: {} as never, goals: [], sessions: liveSessions, decisions, tasks,
}) as never;
const sources: DesktopRuntimeInbox[] = [
  { runtimeId: "r1", label: "示例项目", scope: "project", baseUrl: "", checkedAt: Date.now(), error: "",
    snapshot: snapshot(
      [{ id: "t1", status: "running", prompt: "修登录超时", detail: "跑测试中", owner_session: "s1", goal_id: "", plan_id: "", branch: "vorto/login-timeout", updated: iso(2) },
       { id: "t2", status: "done", prompt: "整理发布脚本", detail: "PR 已合并", owner_session: "s2", goal_id: "", plan_id: "", branch: "", updated: iso(90) }],
      [{ id: "d1", kind: "task", severity: "warn", title: "合并前确认：加边界单测", detail: "测试都过了，PR 已开，等你看一眼", target_id: "t3", action: "open_task", can_dismiss: false }],
      []) },
  { runtimeId: "r2", label: "个人博客", scope: "project", baseUrl: "", checkedAt: Date.now(), error: "",
    snapshot: snapshot([{ id: "t4", status: "queued", prompt: "迁移图片到对象存储", detail: "排在下一个", owner_session: "", goal_id: "", plan_id: "", branch: "", updated: iso(5) }], [], []) },
];
const summary = { runtimes: 2, unreachable: 0, starting: 0, actionable: 1, working: 1, queued: 1, goals: 0, tasks: 3 } as never;

createRoot(document.getElementById("root")!).render(
  <div className="app-shell"><div className="workbench vc-mode-work inspector-closed">
    <aside className="sidebar vc-sidebar">
      <SidebarBrand onCollapse={noop} />
      <ModeSwitch mode="work" onChange={noop} />
      <WorkNav page={tab === "goals" ? "goals" : "tasks"} connected waiting={1} onNewChat={noop} onPage={noop} />
      <SidebarSectionTitle label="项目" className="project-section-title sidebar-first-section" open onToggle={noop} />
      <SidebarProjects activeScope="project" runtimeProcesses={[]} runtimeRecoveries={[]} projects={projects} query=""
        projectSessions={[{ projectId: "p2", sid: "b1", title: "换评论系统", updatedAt: now }]} sessions={sessions} residentSessions={{}}
        remoteProjectId={null} repoRoot="/demo/app" connection="connected" connectionText="已连接" activeSid="s1"
        onSwitchScratch={noop} onSwitchProject={noop} onSwitchSession={noop} onForgetProject={noop} onBanner={noop} />
      <SidebarSectionTitle label="最近" className="work-items-heading" open onToggle={noop} />
      <SidebarSessions sessions={sessions} activeSid="s1" busy query="" decisions={[]} goals={[]} tasks={[]}
        onSelect={noop} onFocusTask={noop} onOpenHooks={noop} onRename={noop} onDelete={noop} onOpenDecision={noop} onOpenGoals={noop} onOpenTasks={noop} />
      <SidebarFooter identity={{ initial: "S", name: "someone@example.com", detail: "中度会员", title: "" }} update={null} onOpenSettings={noop} />
    </aside>
    <aside className="inspector management-page">
      <ManagementHead current={tab} onClose={noop} />
      {tab !== "goals" && <ManagementNav current={tab} onSelect={noop} />}
      <div className="inspector-body">
        {tab === "inbox" ? <RuntimeInboxPanel runtimeInboxes={sources} summary={summary} currentRuntimeId="r1"
          onRefresh={noop} onActivate={noop} onOpenSession={noop} onOpenPanel={noop} /> : <p style={{ padding: 32 }}>（{tab} 的内容沿用原面板）</p>}
      </div>
    </aside>
  </div></div>);
