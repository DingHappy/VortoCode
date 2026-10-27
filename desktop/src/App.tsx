import { useLocalRuntimeControls } from "./hooks/useLocalRuntimeControls";
import { ManagementNav } from "./components/ManagementNav";
import { isManagementTab } from "./lib/inspector";
import { WorkbenchLauncher, WorkbenchTabs, type WorkbenchTool } from "./components/WorkbenchTabs";
import { ActivityFeed } from "./components/ActivityFeed";
import { AssistantTaskCard } from "./components/AssistantTaskCard";
import { CodexActionCard } from "./components/CodexActionCard";
import { AssistantWorkday } from "./components/AssistantWorkday";
import { activityCoverage, type ActivitySource } from "./lib/activitySources";
import { prepareAvatar } from "./lib/avatar";
import { resolveMainChat } from "./lib/mainChat";
import { ChatHistory } from "./components/ChatHistory";
import { ConversationLocation } from "./components/ConversationLocation";
import { useConversationLocation } from "./hooks/useConversationLocation";
import { startPolling } from "./lib/polling";
import { AssistantActionGate, type AssistantTaskAction } from "./lib/assistantTask";
import { openAssistantTask } from "./lib/assistantNavigation";
import { AssistantAudit, AssistantAvatarImage, AssistantStarterTips, AssistantIdentity, AssistantPanel, AssistantPlugins, AssistantProfileDialog, AssistantSchedule, AssistantSidebar, ModeSwitch, PhoneChatDialog, type AppMode, type AssistantTab } from "./components/AssistantMode";
import { AssistantGoals } from "./components/AssistantGoals";
import { assistantFeed, isPhoneSession, taskFeedItem } from "./lib/activityFeed";
import { AssistantActivity, AssistantActivityDetail } from "./components/AssistantActivity";
import { ComputerLauncher, ComputerWorkspacePanel } from "./components/ComputerWorkspace";
import { useComputerWorkspace } from "./hooks/useComputerWorkspace";
import { dispatchDetailSteps } from "./components/ActivityRecordDialog";
import { createConfirmationReply } from "./lib/confirm";
import { ComposerCommands } from "./components/ComposerCommands";
import { ContextStatus, SessionInfo } from "./components/SessionInfo";
import { invoke } from "@tauri-apps/api/core";
import { isPermissionGranted, sendNotification } from "@tauri-apps/plugin-notification";
import { openUrl } from "@tauri-apps/plugin-opener";
import {
  ArrowUp,
  Eye,
  FileCode2,
  GitCompareArrows,
  Inbox,
  Library,
  ListChecks,
  Server,
  FolderPlus,
  Plus,
  SquareTerminal,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import "./App.css";
import "./daily.css";
import {
  createSessionId,
  DESKTOP_PROTOCOL_VERSION,
  GatewayClient,
  normalizeLocalBaseUrl,
} from "./gateway";
import type {
  AssistantDispatch,
  AuditEntry,
  CommandRunItem,
  CommandRunKind,
  ConnectionState,
  ContextItem,
  ConversationMessage,
  DecisionItem,
  DesktopProjectProfile,
  EngineWorkersView,
  DiffPayload,
  GatewayProcessStatus,
  GoalItem,
  JournalNextAction,
  NoticeItem,
  PendingConfirmation,
  PlanItem,
  PromptQueueItem,
  ProtocolEvent,
  PrDeliveryCheck,
  RuntimeSnapshot,
  DesktopRuntimeInbox,
  SessionSummary,
  TaskItem,
  TurnActivity,
  WorkspaceScope,
  DesktopLlmProfileStatus,
  ProjectSessionSummary, CodexAction, PhoneChat} from "./types";
import { DecisionsPanel } from "./components/DecisionsPanel";
import { ConnectionCoordinator, placeAssistant, residentRetryDelay, retryResident } from "./connection/connectionCoordinator";
import { SessionConnection } from "./connection/sessionConnection";
import { TasksPanel } from "./tasks/TasksPanel";
import { useTasks } from "./hooks/useTasks";
import { useTaskDispatch } from "./hooks/useTaskDispatch";
import { taskCollaborationDraft, type CollaborationAction } from "./lib/taskCollaboration";
import { McpConnectionCard } from "./components/McpConnectionCard";
import { McpServersCard } from "./components/McpServersCard";
import { ProjectSkillsCard } from "./components/ProjectSkillsCard";
import { CatalogCard } from "./components/CatalogCard";
import { autoCheckForUpdate, type UpdateCheck } from "./lib/updateCheck";
import { SidebarFooter } from "./components/SidebarFooter";
import { AttachmentChips, ComposerModelPicker, ConfirmCard, ContextChips, PromptQueuePanel, TrustChip, WorkspaceRequestCard } from "./components/ComposerPanels";
import { ConversationHome, FirstRunCard } from "./components/ConversationHome";
import { SidebarBrand, SidebarSectionTitle, WorkNav } from "./components/SidebarChrome";
import { ConversationHeader } from "./components/ConversationHeader";
import { assistantPanelActions } from "./lib/assistantPanelActions";
import { SidebarProjects } from "./components/SidebarProjects";
import { SidebarSessions } from "./components/SidebarSessions";
import {
  ACCOUNT_CHANGED_EVENT, getAccountStatus, getMembership, sidebarIdentity, type VortoAccountStatus, type VortoMembership,
} from "./lib/vortoAccount";
import { ExtensionsInspector } from "./components/ExtensionsInspector";
import { FilesPanel } from "./components/FilesPanel";
import { GitReviewPanel } from "./components/GitReviewPanel";
import { GoalsPanel } from "./components/GoalsPanel";
import { JournalCard } from "./components/JournalCard";
import { MarkdownMessage } from "./components/MarkdownMessage";
import { ProjectAssetsPanel } from "./components/ProjectAssetsPanel";
import { RunsPanel } from "./components/RunsPanel";
import { RuntimeInboxPanel } from "./components/RuntimeInboxPanel";
import { SettingsModal, type SettingsSection } from "./components/SettingsModal";
import { localGeneral, readLocalCodexObserver } from "./lib/codexObserverHealth";
import { checkAttachment } from "./lib/attachments";
import { dismissOfflineChat, listOfflineChats, offlineChatAttachment, recordOfflineChat } from "./lib/offlineChats";
import { BuildVersion } from "./components/BuildVersion";
import { ArtifactCenter } from "./components/ArtifactCenter";
import { PluginsPage } from "./components/PluginsPage";
import { PersonalSkillsCard } from "./components/PersonalSkillsCard";
import { ProjectPicker } from "./components/ProjectPicker";
import { RemoteProjectDialog } from "./components/RemoteProjectDialog";
import { findAssistantHost, isAssistantHost, mainChatKey, probeAssistantHost, workProjects } from "./lib/assistantHost";
import { dismissStarterTips, recordAssistantSend, shouldShowStarterTips, starterTips } from "./lib/starterTips";
import { listedProjects, localTwin, residentEntry, residentTwin, type ResidentProject, type WorkPlace } from "./lib/residentPlace";
import { TurnTimeline } from "./components/TurnTimeline";
import { useChangeReview } from "./hooks/useChangeReview";
import { useExtensionsStatus } from "./hooks/useExtensionsStatus";
import { useGoals } from "./hooks/useGoals";
import { useIsolatedDeliveries } from "./hooks/useIsolatedDeliveries";
import { useProjectAssets } from "./hooks/useProjectAssets";
import { useTrustLevel } from "./hooks/useTrustLevel";
import { useJournal } from "./hooks/useJournal";
import { useLlmProfile } from "./hooks/useLlmProfile";
import { EMPTY_PROCESS } from "./lib/runtimeSupervision";
import { inboxTarget } from "./lib/workspaceRouting";
import { useManagedRuntimes, useRuntimeSupervision } from "./hooks/useManagedRuntimes";
import { agentFrame, outgoingMessage } from "./protocol/outgoingPrompt";
import { usePlugins } from "./hooks/usePlugins";
import { useDecisionNotifications } from "./hooks/useDecisionNotifications";
import { useAssistantSnapshot } from "./hooks/useAssistantSnapshot";
import { useWorkspaceFiles } from "./hooks/useWorkspaceFiles";
import { useWorkspacePolling } from "./hooks/useWorkspacePolling";
import { useRuntimeInboxes } from "./hooks/useRuntimeInboxes";
import { contextItemKey } from "./lib/sourceContext";
import { withLocalDecisions } from "./lib/decisionItems";
import { gitCommentsPrompt, prFeedbackPrompt } from "./lib/agentPrompts";
import { autoFocusDecision, closeWorkbenchTab, openWorkbenchTab, type InspectorTab } from "./lib/inspector";
import { projectSessionKey, projectToRestore,
  STORAGE_KEYS } from "./lib/storage";
import { localDay } from "./lib/time";
import { finishRunningActivities, groupActivitiesByTurn, hydrateActivities, protocolActivity, upsertActivity } from "./protocol/activities";
import {
  historyMessages, promptQueueFromEvent, systemNotice, thinkingActivity, withAssistantReply, withRunningPrompt, withSayActivity,
} from "./protocol/transcript";
import { errorText } from "./lib/errorText";
import { confirmAction } from "./lib/confirm";
import { useAttachments } from "./hooks/useAttachments";
import { latestTurnSteps, previewReferences, publishedArtifactId, type PreviewView } from "./lib/preview";
import { PreviewPanel } from "./components/PreviewPanel";
import { useDelayedFlag } from "./hooks/useDelayedFlag";
import {
  loadModelChoice, loadModelPicks, modelChoices, persistModelChoice, persistModelPicks, resolveModelChoice,
} from "./lib/modelChoice";


type RuntimeConnectionOptions = { baseUrl?: string; repoRoot?: string; token?: string; scope?: WorkspaceScope; remoteProjectId?: string };
type StartWorkspaceOptions = RuntimeConnectionOptions & { baseUrl: string; sid: string; announce?: boolean; workspaceId?: string };
type WorkspaceRequest = { scope: Exclude<WorkspaceScope, "general">; reason: string; task: string };

function inspectorTabLabel(tab: InspectorTab): string {
  return {
    feed: "任务动态",
    info: "任务步骤",
    inbox: "工作看板",
    preview: "预览",
    files: "代码",
    diff: "变更",
    runs: "命令控制台",
    goals: "完成标准",
    tasks: "后台工作",
    decisions: "待处理",
    project: "上下文",
  }[tab];
}

function App() {
  const clientRef = useRef<GatewayClient | null>(null);
  const creatingSessionRef = useRef(false);
  const sessionConnectionRef = useRef<SessionConnection<GatewayClient> | null>(null);
  if (!sessionConnectionRef.current) sessionConnectionRef.current = new SessionConnection(clientRef);
  const goalRunSyncRef = useRef("");
  const initializationStartedRef = useRef(false);
  const projectRestoreDoneRef = useRef(false);
  // 谁在切换、本机引擎是否在启动、现在连在哪：规则见 connection/connectionCoordinator.ts。
  const coordinatorRef = useRef<ConnectionCoordinator | null>(null);
  if (!coordinatorRef.current) coordinatorRef.current = new ConnectionCoordinator();
  const coordinator = coordinatorRef.current;
  const remoteProjectRef = coordinator.remote;
  const assistantHostRef = coordinator.assistantHost;
  // 进助理模式前工作台停在哪个项目：切回工作台时回到那里，而不是丢在通用工作区。
  const workReturnRef = useRef<string | null>(null);
  const [remoteDialogOpen, setRemoteDialogOpen] = useState<false | "project" | "assistant">(false);
  const conversationScrollRef = useRef<HTMLDivElement | null>(null);
  const autoScrollRef = useRef(true);
  const activeTurnRidRef = useRef<string | null>(null);
  const [baseUrl, setBaseUrl] = useState(() => localStorage.getItem(STORAGE_KEYS.baseUrl) ?? "http://127.0.0.1:8080");
  const [repoRoot, setRepoRoot] = useState("");
  const [token, setToken] = useState("");
  const [activeSid, setActiveSid] = useState(() => localStorage.getItem(STORAGE_KEYS.sid) ?? createSessionId());
  const [connection, setConnection] = useState<ConnectionState>("disconnected");
  const [connectionNote, setConnectionNote] = useState("随时可以打开项目或开始一个任务");
  const [protocolVersion, setProtocolVersion] = useState<number | null>(null);
  const [runtime, setRuntime] = useState<RuntimeSnapshot>({});
  // Desktop 托管的 runtime 进程（启动/停止/恢复记录/令牌/监督代次）；WebSocket 会话仍在下面的连接代码里。
  const { runtimes, runtimeTokensRef, processStatus, runtimeProcesses, runtimeRecoveries, recoveryRecord } = useManagedRuntimes();
  const [runtimeStarting, setRuntimeStarting] = useState(false);
  // 冷启动的初始化流程还没走完：这期间首页按「正在启动」处理，不先闪一下「有什么可以帮你？」。
  const [booting, setBooting] = useState(true);
  const homeStarting = connection !== "connected" && (booting || runtimeStarting || connection === "connecting");
  const showHomeStarting = useDelayedFlag(homeStarting, 400);
  const [projects, setProjects] = useState<DesktopProjectProfile[]>([]);
  const [projectSwitching, setProjectSwitching] = useState(false);
  coordinator.listen({ switching: setProjectSwitching, starting: setRuntimeStarting });
  const [settingsOpen, setSettingsOpen] = useState(false);

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const conversationLocation = useConversationLocation({ client: clientRef.current, sid: activeSid,
    messages, container: conversationScrollRef, autoScroll: autoScrollRef });
  const [plan, setPlan] = useState<PlanItem[]>([]);
  const [activities, setActivities] = useState<TurnActivity[]>([]);
  const [previewView, setPreviewView] = useState<PreviewView>("plan");
  // 主区域：对话，或侧边栏打开的「插件」「产物中心」页面。切换会话时回到对话。
  const [mainView, setMainView] = useState<"chat" | "plugins" | "artifacts" | "management">("chat");
  const mainViewRef = useRef(mainView);
  mainViewRef.current = mainView;
  const [settingsSection, setSettingsSection] = useState<SettingsSection | undefined>(undefined);
  const [projectsOpen, setProjectsOpen] = useState(true);
  const [projectSessions, setProjectSessions] = useState<ProjectSessionSummary[]>([]);
  const [sidebarQuery, setSidebarQuery] = useState("");
  // 当前工作区的会话有变化（新开、发了消息）时，顺带刷新各项目下列出的最近会话。
  const sessionsSignature = sessions.map((item) => `${item.sid}:${item.messages}`).join("|");
  useEffect(() => {
    void invoke<ProjectSessionSummary[]>("list_project_sessions").then(setProjectSessions).catch(() => undefined);
  }, [sessionsSignature]);
  const [recentOpen, setRecentOpen] = useState(true);
  // 刚发布的制品：协议回调里只记下来，由 effect 带着当前作用域去刷新并定位（避免回调闭包里的旧 scope）。
  const [publishedArtifact, setPublishedArtifact] = useState<{ id: string; seq: number } | null>(null);
  const previewReferenceItems = useMemo(() => previewReferences(activities), [activities]);
  const previewSteps = useMemo(() => latestTurnSteps(activities), [activities]);
  const [streaming, setStreaming] = useState("");
  const [streamingRid, setStreamingRid] = useState<string | undefined>();
  const [activeTurnRid, setActiveTurnRid] = useState<string | null>(null);
  const updateActiveTurnRid = useCallback((rid: string | null) => {
    activeTurnRidRef.current = rid;
    setActiveTurnRid(rid);
  }, []);
  const [busy, setBusy] = useState(false);
  const [promptQueue, setPromptQueue] = useState<PromptQueueItem[]>([]);
  const [promptQueuePaused, setPromptQueuePaused] = useState(false);
  // 不再区分 Plan / Build：每轮都按可修改的模式运行，写盘、执行、外发照旧逐次过确认门
  //（授权档位决定问不问，污点回合一律问人）。只读规划交给模型自己判断，不再让用户先选模式。
  const mode = "build" as const;
  const [prompt, setPrompt] = useState("");
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirmation | null>(null);
  const confirmationViewRef = useRef({client:clientRef.current,scope:activeSid,id:"",state:pendingConfirm?.responseState});
  confirmationViewRef.current = {client:clientRef.current,scope:activeSid,id:pendingConfirm?.id ?? "",state:pendingConfirm?.responseState};
  const confirmationReplyRef = useRef<ReturnType<typeof createConfirmationReply<GatewayClient>> | null>(null);
  if (!confirmationReplyRef.current) confirmationReplyRef.current = createConfirmationReply(
    () => ({...confirmationViewRef.current,client:clientRef.current}),
    (client,id,ok) => client.send({type:"agent_confirm_response",id,ok}),
    (id,responseState,responseError) => setPendingConfirm(current => current?.id===id ? {...current,responseState,responseError} : current));
  const confirmationCheckRef = useRef<{id:string;nonce:string;client:GatewayClient;scope:string}|null>(null);
  const [workspaceRequest, setWorkspaceRequest] = useState<WorkspaceRequest | null>(null);

  const [inspectorTab, setInspectorTabState] = useState<InspectorTab>("project");
  // 助理模式右侧（助理身份 + 动态）默认打开；工作台默认收起。
  const [inspectorOpen, setInspectorOpen] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEYS.appMode) === "assistant"; } catch { return false; }
  });
  // 会话侧栏的标签栏：打开过的工具；launcher = 正在看工具列表（点了「＋」或标签全关了）。
  const [workbenchTabs, setWorkbenchTabs] = useState<InspectorTab[]>([]);
  const [workbenchLauncher, setWorkbenchLauncher] = useState(false);
  const [feedScope, setFeedScope] = useState<"all" | "session">("all");
  // 助理模式（参考 Muse 个人助理）：一个常驻「主要聊天」+ 旁聊；右侧是助理本人与它做过的事。
  const [appMode, setAppMode] = useState<AppMode>(() => {
    try { return localStorage.getItem(STORAGE_KEYS.appMode) === "assistant" ? "assistant" : "work"; } catch { return "work"; }
  });
  const [mainChatSid, setMainChatSid] = useState(() => { try { return localStorage.getItem(STORAGE_KEYS.mainChatSid) ?? ""; } catch { return ""; } });
  const [assistantTab, setAssistantTab] = useState<AssistantTab>("feed");
  const computerWorkspace = useComputerWorkspace(appMode === "assistant" && mainView === "chat" && inspectorOpen);
  const [assistantGoalFocus, setAssistantGoalFocus] = useState<{ client: GatewayClient | null; scope: string; id: string } | null>(null);
  const [profileDialogOpen, setProfileDialogOpen] = useState(false);
  const [phoneChatSid, setPhoneChatSid] = useState<string | null>(null);
  // 常驻电脑连不上时在本机聊过的会话：连回常驻电脑后列在「本机（离线时）」，见 lib/offlineChats.ts
  const [offlineChatIds, setOfflineChatIds] = useState(() => listOfflineChats());
  const [offlineChats, setOfflineChats] = useState<PhoneChat[]>([]);
  const [offlineChatSid, setOfflineChatSid] = useState<string | null>(null);
  const [phoneChatRid, setPhoneChatRid] = useState<string | undefined>();
  // Snapshots are bound to the loaded host before they can expose actions.
  const assistantScope = `${baseUrl}:${repoRoot}:${processStatus.runtimeId}:${connection}`;
  const assistantScopeRef = useRef(assistantScope);
  assistantScopeRef.current = assistantScope;
  const {
    assistantProfile, assistantAvatar, setAssistantAvatar, scheduleReadEpoch, assistantSchedule, setAssistantSchedule,
    assistantMemories, setAssistantMemories, memoryProposals, setMemoryProposals,
    connectors, connectorReads, refreshConnectorStatus, phoneChats, agentTurns, activityReads, publishActivityRead,
    dispatches, dispatchSnapshotScope, codexActions, refreshAgentTurns, setAssistantProfile, invalidateAssistantReads,
  } = useAssistantSnapshot({ clientRef, assistantScope, assistantScopeRef, connection, assistantTab });
  // 手机上和助理的聊天：只读查看（不接管——两个进程同时写一段对话会互相覆盖）
  const openPhoneChat = (sid: string, rid?: string) => {
    if (!phoneChats.some(chat => chat.sid === sid)) {
      setBanner("当前尚未取得这段手机聊天记录，请刷新动态后再试；这不表示记录已删除。");
      return;
    }
    setPhoneChatSid(sid); setPhoneChatRid(rid);
  };
  const [selectedDispatchId, setSelectedDispatchId] = useState<string | null>(null);
  const [selectedCodexAction, setSelectedCodexAction] = useState<{ client: GatewayClient | null; scope: string; id: string } | null>(null);
  const assistantActionGate = useRef(new AssistantActionGate());
  // 换了助理 scope：数据由 useAssistantSnapshot 清空，这里清掉指向旧数据的选中与弹窗。
  useEffect(() => {
    setPhoneChatSid(null); setPhoneChatRid(undefined);
    setSelectedDispatchId(null); setSelectedCodexAction(null); setAssistantGoalFocus(null); setProfileDialogOpen(false);
  }, [assistantScope]);
  const [tabsNeedingAttention, setTabsNeedingAttention] = useState<ReadonlySet<InspectorTab>>(
    () => new Set());
  const openInspector = useCallback((tab: InspectorTab) => {
    setInspectorTabState(tab);
    setMainView(isManagementTab(tab) ? "management" : "chat");
    setInspectorOpen(!isManagementTab(tab));
    if (!isManagementTab(tab)) {
      setWorkbenchTabs((previous) => openWorkbenchTab(previous, tab));
      setWorkbenchLauncher(false);
    }
    setTabsNeedingAttention((previous) => {
      if (!previous.has(tab)) return previous;
      const next = new Set(previous);
      next.delete(tab);
      return next;
    });
  }, []);
  // Agent 那边发生的事（新 diff、保存结果…）**不许抢走你正在看的标签**：面板没开就照常打开，
  // 开着且你在看别的，就只在目标标签上点一个提示点，由你决定什么时候过去。
  // 真机 2026-09-17：正在看「代码」，面板自己跳到了「收件箱」。
  const inspectorOpenRef = useRef(inspectorOpen);
  const inspectorTabRef = useRef<InspectorTab>("inbox");
  const autoOpenInspector = useCallback((tab: InspectorTab) => {
    const decision = autoFocusDecision({
      open: inspectorOpenRef.current || mainViewRef.current !== "chat" || isManagementTab(tab),
      current: inspectorTabRef.current,
      target: tab,
    });
    if (decision === "open") {
      openInspector(tab);
      return;
    }
    if (decision === "ignore") return;
    setTabsNeedingAttention((previous) => {
      if (previous.has(tab)) return previous;
      const next = new Set(previous);
      next.add(tab);
      return next;
    });
  }, [openInspector]);
  useEffect(() => { inspectorOpenRef.current = inspectorOpen; }, [inspectorOpen]);
  useEffect(() => { inspectorTabRef.current = inspectorTab; }, [inspectorTab]);
  const [diffPayload, setDiffPayload] = useState<DiffPayload | null>(null);
  // 对话式隔离交付的审查视图已收进 useIsolatedDeliveries（hooks/useIsolatedDeliveries.ts）。
  const {
    isolatedDeliveries, isolatedDelivery, isolatedDeliveryDiff, isolatedDeliveryPath,
    isolatedDeliveryError, isolatedDeliveryLoading,
    refreshIsolatedDeliveries, openIsolatedDelivery, loadIsolatedDeliveryDiff, resetIsolatedDeliveries,
  } = useIsolatedDeliveries(clientRef);
  const [runs, setRuns] = useState<CommandRunItem[]>([]);
  const [notices, setNotices] = useState<NoticeItem[]>([]);
  const noticeReadRef = useRef(0), noticeRevisionRef = useRef(0);
  // Hook 信任 + 扩展清单已收进 useExtensionsStatus；跨域的 toggleHookTrust 留在 App。
  const {
    hookStatus, setHookStatus, hookTrustBusy, setHookTrustBusy,
    extensionsInspect, extensionsInspectBusy,
    refreshHookStatus, refreshExtensionsInspect,
  } = useExtensionsStatus(clientRef);
  const [decisions, setDecisions] = useState<DecisionItem[]>([]);
  const decisionScopeRef = useRef(activeSid);
  decisionScopeRef.current = activeSid;
  const decisionReadRef = useRef(0);
  const auditReadRef = useRef(0);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  // auditFilter（审计类别筛选）是纯本地 UI 态，已下移到 <DecisionsPanel> 自持。
  // journal 域 8 个 state + 全部读写回调已收进 useJournal（hook 试点，hooks/useJournal.ts）；
  // 实例在 banner 声明之后挂载——保存快照/添加记录的结果提示要注入 setBanner。
  const [backgroundPrompt, setBackgroundPrompt] = useState("");
  const [banner, setBanner] = useState<string | null>(null);
  const { attachments, setAttachments, addFiles, removeAttachment } = useAttachments(setBanner);
  const { plugins, pluginBusy, pluginChecks, savePlugin, pluginAction } = usePlugins(
    clientRef, assistantScopeRef, assistantScope, connection, assistantTab, setBanner);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const { tasks, taskRead, focusedTaskId, worktreeWorkspace, setFocusedTaskId, refreshTasks,
    refreshWorktrees, resetTasks, upsertTask, acceptTaskEvent, captureTaskScope, cancelTask, pauseTask,
    resumeTask } = useTasks(clientRef, setBanner);
  const {
    journal,
    journalDays,
    weeklyJournal,
    journalContinuation,
    journalView,
    journalDate,
    journalNote,
    journalBusy,
    setJournalView,
    setJournalNote,
    refreshJournal,
    refreshWeeklyJournal,
    snapshotTodayJournal,
    selectJournalDay,
    continueFromYesterday,
    saveJournalSnapshot,
    addJournalNote,
    resetJournal,
  } = useJournal(clientRef, setBanner);
  // goal 域状态与只写自身的回调已收进 useGoals（hooks/useGoals.ts）；跨域的 runGoal /
  // runGoalVerifiers / adoptRunEvidence 留在 App。
  const {
    goals, setGoals, refreshGoals, goalFocus, focusGoal, resetGoalNavigation,
    goalObjective, setGoalObjective, goalCriteria, setGoalCriteria,
    goalConstraints, setGoalConstraints, goalNonGoals, setGoalNonGoals,
    goalEvidenceDrafts, setGoalEvidenceDrafts, goalVerifierDrafts, setGoalVerifierDrafts,
    editingGoalId, goalSubmitting, setGoalSubmitting,
    resetGoalForm, saveGoalDraft, editGoalDraft, deleteGoalDraft, recordGoalEvidence, saveGoalVerifier,
  } = useGoals(clientRef, setBanner, () => openInspector("goals"));
  // 仓库记忆 + 制品预览已收进 useProjectAssets；跨域的 addRepoMemoryFact /
  // attachSelectedArtifact / openSelectedArtifact 与按连接加载预览的 effect 留在 App。
  const {
    projectAssetView, setProjectAssetView, repoMemory, setRepoMemory, repoMemoryDraft, setRepoMemoryDraft,
    projectAssetsLoading, setProjectAssetsLoading, projectAssetsError, setProjectAssetsError,
    artifacts, selectedArtifactId, setSelectedArtifactId, selectedArtifact,
    artifactVersions, artifactVersion, artifactPreviewLoading, securedArtifactHtml,
    refreshProjectAssets, loadArtifactPreview, resetProjectAssets,
  } = useProjectAssets(clientRef);
  // 授权档位与模型服务配置已收进 useTrustLevel / useLlmProfile；重启 runtime 经注入回调。
  const { trust, trustBusy, changeTrustLevel } = useTrustLevel(clientRef, connection, repoRoot, settingsOpen, setBanner);
  const {
    llmProfile, applyLlmProfile, llmProfileChecked, llmBaseInput, setLlmBaseInput, llmModelInput, setLlmModelInput,
    llmFastInput, setLlmFastInput, llmStrongInput, setLlmStrongInput,
    llmRolesInput, setLlmRolesInput,
    llmKeyInput, setLlmKeyInput, llmProfileBusy, llmSaveNote, llmSaveKind, llmProfileLoading, llmProfileError, llmProfileConflict, reloadLlmProfile, saveLlmProfile, clearLlmProfile,
  } = useLlmProfile(settingsOpen, setBanner, () => restartCurrentRuntimeForLlmProfile());
  const [savedModelChoice, setSavedModelChoice] = useState<string | null>(() => loadModelChoice());
  const [modelPicks, setModelPicks] = useState<string[] | null>(() => loadModelPicks());
  const changeModelPicks = useCallback((picks: string[] | null) => {
    setModelPicks(picks);
    persistModelPicks(picks);
  }, []);
  // 没配模型时 Desktop 填的占位默认模型不是真的可用：不列出来，按钮显示「未连接模型」
  const composerModelChoices = useMemo(() => llmProfile?.configured ? modelChoices(llmProfile, modelPicks) : [], [llmProfile, modelPicks]);
  const modelChoice = resolveModelChoice(savedModelChoice, composerModelChoices);
  // 「变更」面板域（Git 审查 / 任务分支审查 / 评论 / PR 交付）已收进 useChangeReview；
  // 发给 Agent 的两个回调与协议事件对 setGitReviewRevision 的写入留在 App。
  const {
    gitReview, gitReviewDiff, gitSelectedPath, gitReviewScope, gitReviewLoading, gitReviewError,
    gitReviewRevision, setGitReviewRevision, gitActionBusy, taskReviewTask,
    taskBranchReview, taskBranchDiff,
    taskBranchSelectedPath, taskReviewVerifying, gitComments,
    setGitComments, pendingGitComment, setPendingGitComment, gitCommentDraft, setGitCommentDraft,
    gitCommentSaving, gitCommitMessage, setGitCommitMessage, gitPrTitle, setGitPrTitle, gitPrBase,
    setGitPrBase, gitDeliveryBusy, prDelivery, prDeliveryLoading, prCheckLogs, openGitComments,
    sentGitComments, refreshGitReview, refreshPrDelivery, loadTaskBranchReviewDiff,
    openTaskBranchReview, closeTaskBranchReview, applyTaskBranchAction, verifyReviewedTaskBranch,
    openGitReviewFile, applyGitAction, startGitComment, addGitComment, updateGitCommentStatus,
    deleteGitComment, loadPrCheckLog, commitGitReview, openGitReviewPr, resetChangeReview,
  } = useChangeReview(clientRef, setBanner, () => openInspector("diff"), async () => {
    await refreshTasks();
    await refreshWorktrees();
  });
  const {
    workspaceRootRef, workspaceFiles, workspaceTruncated, workspaceLoading, workspaceError,
    fileQuery, setFileQuery, selectedFile, filePreview, contextItems, setContextItems,
    sourceSelection, setSourceSelection, editorMode, setEditorMode, editorContent, setEditorContent, editorEol, savingFile,
    filteredWorkspaceFiles, editorDirty, workspaceMatchesRuntime, previewContextItem, previewContextAttached,
    refreshWorkspaceFiles, openWorkspaceFile, settleWorkspaceSave, abandonWorkspaceSave, resetWorkspaceFiles,
    addFileToContext, selectSourceLine, launchExternalEditor, saveWorkspaceFile, discardEditorChanges, closeWorkspaceFile,
  } = useWorkspaceFiles({ clientRef, remoteProjectRef, repoRoot, runtimeWorkdir: runtime.workdir, connection, protocolVersion, onBanner: setBanner });

  const currentSession = sessions.find((session) => session.sid === activeSid);
  const activeScope: WorkspaceScope = runtime.scope ?? processStatus.scope ?? (repoRoot.trim() ? "project" : "general");
  /** 精选目录装完东西后递增，让插件页的 MCP 服务 / 项目技能卡重新加载。 */
  const [pluginsRevision, setPluginsRevision] = useState(0);
  /** 启动时自动检查更新（每天最多一次，设置里可关）；有新版才非空。 */
  const [availableUpdate, setAvailableUpdate] = useState<UpdateCheck | null>(null);
  // 左下角显示 VortoCode 账号本人；账号卡片里登录/退出/换会员后会广播 ACCOUNT_CHANGED_EVENT
  const [vortoAccount, setVortoAccount] = useState<{ status: VortoAccountStatus | null; membership: VortoMembership | null }>({ status: null, membership: null });
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const status = await getAccountStatus();
        const membership = status.signedIn && !status.offline ? await getMembership().catch(() => null) : null;
        if (alive) setVortoAccount({ status, membership });
        // 读会员信息时会顺手刷新模型列表里的会员模型，界面跟着拿一次最新配置
        if (alive && membership?.listed) applyLlmProfile(await invoke<DesktopLlmProfileStatus>("get_llm_profile"));
      } catch {
        if (alive) setVortoAccount({ status: null, membership: null });
      }
    };
    void refresh();
    window.addEventListener(ACCOUNT_CHANGED_EVENT, refresh);
    return () => { alive = false; window.removeEventListener(ACCOUNT_CHANGED_EVENT, refresh); };
  }, []);
  const identity = sidebarIdentity(vortoAccount.status, vortoAccount.membership);
  useEffect(() => { void autoCheckForUpdate().then(setAvailableUpdate); }, []);
  // 切换会话或工作区时回到对话视图。
  useEffect(() => { setMainView("chat"); }, [activeSid, activeScope]);
  // llmInputIsLocal 派生只服务设置弹窗，已随 <SettingsModal> 搬入组件内计算。
  const connectionText = {
    disconnected: "未启动",
    connecting: "正在准备",
    connected: activeScope === "general" ? "通用会话就绪" : activeScope === "scratch" ? "Scratch 就绪" : "项目就绪",
    error: "需要处理",
  }[connection];
  // 派生的可见文件切片 / 预览行 / 行数 / 裁剪标记只服务 <FilesPanel>，已随面板搬入组件内计算。
  // selectedGitFile 与 displayedGit* 族派生只服务「变更」面板，已随 <GitReviewPanel> 搬入组件内计算。
  const decisionItems = useMemo(() => withLocalDecisions(decisions, sessions, prDelivery), [decisions, prDelivery, sessions]);
  // 审计筛选派生随 <DecisionsPanel> 搬入；journalActions 派生随 <JournalCard> 搬入（均只服务各自组件）。
  const activeTasks = useMemo(
    () => tasks.filter((task) => ["queued", "running", "blocked", "paused", "failed", "interrupted"].includes(task.status)
      || (task.kind === "delegation" && ["pending", "rework_requested"].includes(task.collaboration?.review ?? ""))),
    [tasks],
  );
  const activeGoals = useMemo(
    () => goals.filter((goal) => ["draft", "active", "blocked", "failed"].includes(goal.status)),
    [goals],
  );
  const activeRuns = useMemo(
    () => runs.filter((run) => ["queued", "running", "cancelling", "failed"].includes(run.status)),
    [runs],
  );
  const { runtimeInboxes, runtimeInboxSummary, refreshRuntimeInboxes } = useRuntimeInboxes({
    runtimeProcesses, projects, decisionItems, runtimeTokensRef });
  const {
    notificationsEnabled, toggleSystemNotifications,
    beginNotificationSync, endNotificationSync, stopNotificationSync, resetNotificationBaseline,
  } = useDecisionNotifications({ runtimeInboxes, decisionItems, onBanner: setBanner,
    currentRuntimeId: processStatus.runtimeId || `${activeScope}:${repoRoot || "general"}` });

  const sessionRefreshAttemptRef = useRef(0);
  const refreshSessions = useCallback(async () => {
    const attempt = ++sessionRefreshAttemptRef.current;
    const client = clientRef.current;
    if (!client) return false;
    try {
      const result = await client.listSessions();
      if (client !== clientRef.current || attempt !== sessionRefreshAttemptRef.current) return false;
      setSessions(result);
      return true;
    } catch (error) {
      if (client === clientRef.current && attempt === sessionRefreshAttemptRef.current) setConnectionNote(errorText(error, "读取会话失败"));
      return false;
    }
  }, []);

  const taskDispatch = useTaskDispatch({
    tasks,
    value: backgroundPrompt, onChange: setBackgroundPrompt,
    getClient: () => clientRef.current,
    scopeKey: `${processStatus.runtimeId || baseUrl}:${activeSid}`,
    session: activeSid, enabled: connection === "connected" && activeScope !== "general",
    onSubmitted: (id) => {
      setFocusedTaskId(id);
      setBanner("任务已下派，可在任务列表查看进度与结果。");
      void refreshTasks();
    },
  });

  const focusTask = useCallback((taskId: string) => {
    if (!taskId) return;
    setFocusedTaskId(taskId);
    openInspector("tasks");
    void refreshTasks();
    void refreshWorktrees();
  }, [openInspector, refreshTasks, refreshWorktrees]);

  const runRefreshAttemptRef = useRef(0);
  const refreshRuns = useCallback(async () => {
    const attempt = ++runRefreshAttemptRef.current;
    const client = clientRef.current;
    if (!client) return false;
    const current = () => client === clientRef.current && attempt === runRefreshAttemptRef.current;
    try {
      const nextRuns = await client.listRuns();
      if (!current()) return false;
      setRuns(nextRuns);
      const linkedFingerprint = nextRuns
        .filter((run) => run.goal_id)
        .map((run) => `${run.id}:${run.status}:${run.updated ?? ""}`)
        .join("|");
      if (linkedFingerprint !== goalRunSyncRef.current) {
        if (linkedFingerprint) {
          const nextGoals = await client.listGoals();
          if (!current()) return false;
          setGoals(nextGoals);
        }
        goalRunSyncRef.current = linkedFingerprint;
      }
      return true;
    } catch {
      // 运行控制台是 Desktop 增量能力；旧 runtime 不支持时保持空态。
      return false;
    }
  }, []);

  const refreshNotices = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    const scope = assistantScopeRef.current, serial = ++noticeReadRef.current, revision = noticeRevisionRef.current;
    const current = () => clientRef.current === client && assistantScopeRef.current === scope && noticeReadRef.current === serial;
    publishActivityRead(client, scope, "notices", { status: "loading" });
    try {
      const next = await client.listNotices();
      if (!current()) return;
      if (!Array.isArray(next) || noticeRevisionRef.current !== revision) {
        publishActivityRead(client, scope, "notices", { status: "unavailable" }); return;
      }
      setNotices(next);
      publishActivityRead(client, scope, "notices", { status: "current", checkedAt: Date.now() });
    } catch {
      if (current()) publishActivityRead(client, scope, "notices", { status: "unavailable" });
    }
  }, [publishActivityRead]);

  const refreshDecisions = useCallback(async (sid = activeSid) => {
    const attempt = ++decisionReadRef.current;
    const client = clientRef.current;
    if (!client) return false;
    try {
      const next = await client.listDecisions(`sid-${sid}`);
      if (client !== clientRef.current || attempt !== decisionReadRef.current || sid !== decisionScopeRef.current) return false;
      setDecisions(next);
      return true;
    } catch {
      // 旧 runtime 没有决策队列时，PR/CI 决策仍由 Desktop 本地聚合。
      return false;
    }
  }, [activeSid]);

  const refreshAudit = useCallback(async () => {
    const attempt = ++auditReadRef.current;
    const client = clientRef.current;
    if (!client) return false;
    try {
      const next = await client.listAudit(120);
      if (client !== clientRef.current || attempt !== auditReadRef.current) return false;
      setAuditEntries(next);
      return true;
    } catch {
      // 审计时间线不是对话链路的硬依赖。
      return false;
    }
  }, []);

  // refreshJournal* 四件已随 journal 域收进 useJournal（S6b）。

  // snapshotTodayJournal 已随 journal 域收进 useJournal（S6b）；总线与 connect 照旧调用。

  const refreshProjectRegistry = useCallback(async () => {
    const registered = await invoke<DesktopProjectProfile[]>("list_desktop_projects");
    setProjects(registered);
    void invoke<ProjectSessionSummary[]>("list_project_sessions").then(setProjectSessions).catch(() => undefined);
    return registered;
  }, []);

  const rememberProject = useCallback(async (root: string, url: string) => {
    const profile = await invoke<DesktopProjectProfile>("remember_desktop_project", {
      repoRoot: root,
      baseUrl: url,
    });
    setProjects((previous) => [profile, ...previous.filter((item) => item.id !== profile.id)]);
    return profile;
  }, []);

  const handleProtocolEvent = useCallback(
    (event: ProtocolEvent) => {
      // init 与 status 都带 runtime 快照：项目工作区跟着 runtime 的 workdir 走，文件列表换目录才重读。
      const applyRuntimeSnapshot = (snapshot: RuntimeSnapshot) => {
        setRuntime(snapshot);
        if (snapshot.scope === "project" && snapshot.workdir) {
          if (snapshot.workdir !== repoRoot.trim()) {
            setRepoRoot(snapshot.workdir);
            localStorage.setItem(STORAGE_KEYS.repoRoot, snapshot.workdir);
          }
          if (snapshot.workdir !== workspaceRootRef.current) {
            void refreshWorkspaceFiles(snapshot.workdir);
          }
        } else if (snapshot.scope === "scratch" && snapshot.workdir
          && snapshot.workdir !== workspaceRootRef.current) {
          void refreshWorkspaceFiles(snapshot.workdir);
        }
      };
      switch (event.type) {
        case "runtime_disconnected":
          setConnection("error");
          setConnectionNote("远端连接已断开；服务器任务继续运行，点击项目可重新连接");
          setBusy(false);
          break;
        case "init": {
          const version = typeof event.v === "number" ? event.v : null;
          const snapshot = (event.data ?? {}) as RuntimeSnapshot;
          setProtocolVersion(version);
          applyRuntimeSnapshot(snapshot);
          if (version !== null && version !== DESKTOP_PROTOCOL_VERSION) {
            setBanner(`协议版本不一致：Desktop=${DESKTOP_PROTOCOL_VERSION}，runtime=${version}`);
          }
          break;
        }
        case "status":
          if (typeof event.v === "number") {
            setProtocolVersion(event.v);
            if (event.v !== DESKTOP_PROTOCOL_VERSION) {
              setBanner(`协议版本不一致：Desktop=${DESKTOP_PROTOCOL_VERSION}，runtime=${event.v}`);
            }
          }
          applyRuntimeSnapshot((event.data ?? {}) as RuntimeSnapshot);
          break;
        case "agent_history":
          setMessages(historyMessages(event, activeSid));
          break;
        case "agent_activity_history":
          setActivities(hydrateActivities(event.items ?? [], { missingStatus: "unknown" }));
          break;
        case "agent_plan":
          setPlan((event.items ?? []) as PlanItem[]);
          break;
        case "agent_queue": {
          const { queued, running, paused } = promptQueueFromEvent(event);
          setPromptQueue(queued);
          setPromptQueuePaused(paused);
          if (running) {
            setBusy(true);
            updateActiveTurnRid(running.id);
            setActivities((previous) => upsertActivity(previous, thinkingActivity(running.id)));
            setMessages((previous) => withRunningPrompt(previous, running));
          } else {
            setBusy(false);
            updateActiveTurnRid(null);
          }
          void refreshSessions();
          break;
        }
        case "agent_phase":
        case "agent_tool":
        case "agent_hook": {
          const activity = protocolActivity(event);
          if (activity) setActivities((previous) => upsertActivity(previous, activity));
          const publishedId = event.type === "agent_tool" ? publishedArtifactId(event) : null;
          if (publishedId) {
            setPreviewView("artifacts");
            setPublishedArtifact((previous) => ({ id: publishedId, seq: (previous?.seq ?? 0) + 1 }));
            autoOpenInspector("preview");
          }
          if (event.type === "agent_tool" && event.name === "dev_isolated" && event.status === "succeeded") {
            autoOpenInspector("diff");
            void refreshIsolatedDeliveries();
          }
          if (event.type === "agent_hook" && ["failed", "timed_out", "blocked"].includes(String(event.status ?? ""))) {
            void refreshSessions();
            void refreshDecisions();
          }
          break;
        }
        case "agent_say": {
          const label = String(event.text ?? "").trim();
          if (!label) break;
          setActivities((previous) => withSayActivity(previous, event.rid, label));
          break;
        }
        case "agent_reasoning":
          // 不展示原始推理链；老 runtime 若发 reasoning，只映射成安全的阶段状态。
          setActivities((previous) => upsertActivity(previous, thinkingActivity(event.rid)));
          break;
        case "agent_stream":
          setStreaming(String(event.text ?? ""));
          setStreamingRid(event.rid);
          break;
        case "agent_emit":
          setStreaming("");
          setStreamingRid(undefined);
          setMessages((previous) => withAssistantReply(previous, event));
          break;
        case "agent_error":
          setStreaming("");
          setStreamingRid(undefined);
          if (!event.rid || !activeTurnRidRef.current || event.rid === activeTurnRidRef.current) {
            setBusy(false);
            updateActiveTurnRid(null);
            setActivities((previous) => finishRunningActivities(previous, event.rid, "failed"));
          }
          setMessages((previous) => [...previous, systemNotice("error", event)]);
          break;
        case "agent_done":
          setStreaming("");
          setStreamingRid(undefined);
          setBusy(false);
          updateActiveTurnRid(null);
          setActivities((previous) => finishRunningActivities(previous, event.rid, "completed"));
          void refreshSessions();
          if (activeScope !== "general") {
            void refreshGitReview();
            void refreshPrDelivery();
          }
          void refreshDecisions();
          void refreshAudit();
          void snapshotTodayJournal();
          break;
        case "agent_cancelled":
          setStreaming("");
          setStreamingRid(undefined);
          setBusy(false);
          updateActiveTurnRid(null);
          setActivities((previous) => finishRunningActivities(previous, event.rid, "cancelled"));
          setMessages((previous) => [...previous, systemNotice("cancelled", event)]);
          void refreshDecisions();
          void refreshAudit();
          void snapshotTodayJournal();
          break;
        case "agent_confirm":
          if (event.id) {
            setPendingConfirm({
              id: event.id,
              text: String(event.text ?? "需要确认"),
              tainted: event.tainted !== false,
            });
            void refreshDecisions();
            void refreshSessions();
          }
          break;
        case "agent_confirm_state": {
          const check = confirmationCheckRef.current;
          if (!check || check.id !== event.id || check.nonce !== event.nonce || check.client !== clientRef.current || check.scope !== confirmationViewRef.current.scope || typeof event.pending !== "boolean") break;
          confirmationCheckRef.current = null;
          confirmationReplyRef.current?.close(check.id);
          setPendingConfirm(current => current?.id !== check.id ? current : event.pending === true
            ? {...current,responseState:"idle",responseError:"运行端仍在等待，可重新选择本次批准。"} : null);
          if (event.pending === false) setBanner("运行端已不再等待此确认；请查看活动记录核对处理结果");
          break;
        }
        case "agent_confirm_closed":
          confirmationReplyRef.current?.close(String(event.id ?? ""));
          // 后端不再等这条确认了（超时按拒绝、回合取消、或别的客户端已应答）。
          // 没有这一支时，卡片会一直挂着且按钮可点，但点了什么都不会发生。
          setPendingConfirm((current) => (current && current.id === event.id ? null : current));
          if (event.reason === "timeout") setBanner("确认已超时，本次操作按拒绝处理");
          break;
        case "workspace_required": {
          const requested = event.scope === "scratch" ? "scratch" : "project";
          setWorkspaceRequest({
            scope: requested,
            reason: String(event.reason ?? "这个任务需要文件工作区"),
            task: String(event.task ?? ""),
          });
          break;
        }
        case "agent_diff":
          setDiffPayload({ title: String(event.title ?? "待审查改动"), diff: String(event.diff ?? "") });
          autoOpenInspector("diff");
          break;
        case "git_review_changed":
          setGitReviewRevision({
            baseline: String(event.baseline ?? ""),
            files: Number(event.files ?? 0),
            reason: String(event.reason ?? "content"),
            receivedAt: Date.now(),
          });
          if (activeScope !== "general") void refreshGitReview();
          break;
        case "workspace_edit_result": {
          const settled = settleWorkspaceSave(event);
          if (settled.stale) break;
          setPendingConfirm(null);
          if (settled.pending) autoOpenInspector("files");
          setBanner(String(event.message ?? (event.ok ? "源码已保存" : "源码保存失败")));
          void refreshDecisions();
          void refreshAudit();
          void snapshotTodayJournal();
          break;
        }
        case "task_snapshot":
          if (!acceptTaskEvent(event, clientRef.current)) break;
          void refreshSessions();
          void refreshWorktrees();
          break;
        case "task_update": {
          const task = event.data as TaskItem;
          if (!task?.id) break;
          if (!acceptTaskEvent(event, clientRef.current)) break;
          void refreshSessions();
          void refreshWorktrees();
          if (task.goal_id && ["done", "failed", "cancelled", "interrupted", "paused"].includes(task.status)) {
            void refreshGoals();
          }
          if (["failed", "cancelled", "interrupted", "paused", "done", "blocked"].includes(task.status)
              || (task.collaboration?.questions?.length ?? 0) > 0) {
            void refreshDecisions();
            void snapshotTodayJournal();
          }
          break;
        }
        case "task_handoff": {
          const task = event.data as TaskItem;
          if (!task?.id) break;
          if (!acceptTaskEvent(event, clientRef.current)) break;
          const handoffId = `task-handoff-${task.id}-${task.status}`;
          const label = task.status === "done" ? "后台任务已完成" : `后台任务已${task.status}`;
          const detail = task.handoff?.next_action || task.error || "打开工作台查看交接详情";
          setMessages((previous) => previous.some((message) => message.id === handoffId)
            ? previous
            : [...previous, { id: handoffId, role: "system", text: `${label}：${task.prompt ?? task.id}\n下一步：${detail}` }]);
          setBanner(`${label}，已交接回当前会话`);
          void refreshSessions();
          void refreshWorktrees();
          void refreshDecisions();
          void snapshotTodayJournal();
          if (notificationsEnabled) {
            void isPermissionGranted().then((granted) => {
              if (granted) sendNotification({ title: label, body: String(task.prompt ?? task.id).slice(0, 160) });
            }).catch(() => undefined);
          }
          break;
        }
        case "notice": {
          const notice = event.data as NoticeItem;
          noticeRevisionRef.current++;
          setNotices((previous) => [{ ...notice, text: String(notice?.text ?? "") }, ...previous].slice(0, 100));
          break;
        }
        default:
          break;
      }
    },
    [acceptTaskEvent, activeScope, activeSid, notificationsEnabled, openInspector, refreshAudit, refreshDecisions, refreshGitReview, refreshGoals, refreshIsolatedDeliveries, refreshPrDelivery, refreshSessions, refreshWorkspaceFiles, refreshWorktrees, repoRoot, settleWorkspaceSave, snapshotTodayJournal, updateActiveTurnRid],
  );

  const disconnect = useCallback(async () => {
    stopNotificationSync();
    if (!await sessionConnectionRef.current!.disconnect()) return;
    resetIsolatedDeliveries();
    setConnection("disconnected");
    setConnectionNote("已离开工作区；后台 runtime 与任务继续运行");
    setBusy(false);
    abandonWorkspaceSave();
  }, [abandonWorkspaceSave, resetIsolatedDeliveries, stopNotificationSync]);

  // 连接域编排注册表（B8-④c S10）：连接成功后的全量数据装填收敛到这一处。
  // 各域 refresh 按「通用 / 工作区专属」两档显式注册——hook 化的域把归还的回调挂到
  // 这里即可（journal 的 snapshotTodayJournal/refreshWeeklyJournal 已是 useJournal 归还），
  // connect 本体不再罗列域细节。allSettled：任一域拉取失败不阻断连接（各域自行降级）。
  const refreshAllForScope = useCallback(async (scope: WorkspaceScope, sid: string) => {
    const common = [
      refreshSessions(), refreshNotices(), refreshDecisions(sid), refreshAudit(),
      snapshotTodayJournal(), refreshWeeklyJournal(localDay()),
      refreshProjectAssets(scope !== "general"),
    ];
    const workspace = scope === "general" ? [] : [
      refreshTasks(), refreshWorktrees(), refreshRuns(), refreshGoals(),
      refreshGitReview(), refreshIsolatedDeliveries(), refreshPrDelivery(), refreshHookStatus(), refreshExtensionsInspect(),
    ];
    await Promise.allSettled([...common, ...workspace]);
  }, [refreshSessions, refreshNotices, refreshDecisions, refreshAudit, snapshotTodayJournal, refreshWeeklyJournal, refreshProjectAssets, refreshTasks, refreshWorktrees, refreshRuns, refreshGoals, refreshGitReview, refreshIsolatedDeliveries, refreshPrDelivery, refreshHookStatus, refreshExtensionsInspect]);

  const connectToRuntime = useCallback(
    async (sid = activeSid, options: RuntimeConnectionOptions = {}): Promise<boolean> => {
      const attempt = sessionConnectionRef.current!.begin();
      coordinator.connectBegan();
      const requestedBaseUrl = options.baseUrl ?? baseUrl;
      const requestedRepoRoot = options.repoRoot ?? repoRoot;
      const requestedToken = options.token ?? token;
      const remoteAssistant = Boolean(options.remoteProjectId ?? remoteProjectRef.current)
        && assistantHostRef.current === (options.remoteProjectId ?? remoteProjectRef.current);
      const requestedScope = remoteAssistant ? "general"
        : options.scope ?? runtime.scope ?? (requestedRepoRoot.trim() ? "project" : "general");
      setBanner(null);
      setContextItems([]);
      beginNotificationSync();
      setConnection("connecting");
      setConnectionNote("正在建立安全协议连接…");
      try {
        const remoteId = options.remoteProjectId ?? remoteProjectRef.current ?? undefined;
        const normalized = remoteId ? requestedBaseUrl : normalizeLocalBaseUrl(requestedBaseUrl);
        remoteProjectRef.current = remoteId ?? null;
        if (!remoteId) localStorage.setItem(STORAGE_KEYS.baseUrl, normalized);
        if (requestedRepoRoot.trim()) localStorage.setItem(STORAGE_KEYS.repoRoot, requestedRepoRoot.trim());
        localStorage.setItem(STORAGE_KEYS.sid, sid);
        const client = await attempt.attach(
          () => new GatewayClient({ baseUrl: normalized, token: remoteId ? "" : requestedToken, remoteProjectId: remoteId,
            expectedWorkdir: remoteId && !remoteAssistant ? requestedRepoRoot : undefined,
            expectedScope: remoteAssistant ? "general" : undefined }), sid, handleProtocolEvent,
        );
        if (!client) return false;
        setConnection("connected");
        setConnectionNote(remoteAssistant ? "常驻电脑已连接；合上笔记本它也照常干活" : remoteId ? "远端工作区已连接；离开 Desktop 后服务器任务继续运行" : requestedScope === "general" ? "通用会话已就绪" : requestedScope === "scratch" ? "隔离 Scratch 已就绪" : "项目工作区已就绪");
        if (remoteId) localStorage.setItem(projectSessionKey(remoteId), sid);
        if (!remoteId && requestedScope === "project" && requestedRepoRoot.trim()) {
          try {
            const profile = await rememberProject(requestedRepoRoot.trim(), normalized);
            if (!attempt.current()) return false;
            setRepoRoot(profile.repoRoot);
            setBaseUrl(profile.baseUrl);
            localStorage.setItem(STORAGE_KEYS.repoRoot, profile.repoRoot);
            localStorage.setItem(projectSessionKey(profile.id), sid);
          } catch (error) {
            if (!attempt.current()) return false;
            setBanner(`runtime 已连接，但项目记录未保存：${error instanceof Error ? error.message : String(error)}`);
          }
        }
        await client.send({ type: "task_list" });
        if (!attempt.current()) return false;
        await refreshAllForScope(requestedScope, sid);
        if (!attempt.current()) return false;
        endNotificationSync();
        setSettingsOpen(false);
        return true;
      } catch (error) {
        if (!attempt.current()) return false;
        coordinator.connectFailed(errorText(error, "连接失败"));
        endNotificationSync();
        clientRef.current = null;
        setConnection("error");
        setConnectionNote(errorText(error, "连接失败"));
        return false;
      }
    }, [activeSid, baseUrl, beginNotificationSync, coordinator, endNotificationSync, handleProtocolEvent, refreshAllForScope, rememberProject, repoRoot, runtime.scope, token],
  );

  const startWorkspace = useCallback(async (options: StartWorkspaceOptions): Promise<boolean> => {
    if (coordinator.starting) return false;
    const scope = options.scope ?? "project";
    const root = options.repoRoot?.trim() ?? "";
    if (scope === "project" && !root) {
      setBanner("请先选择一个 Git 项目");
      return false;
    }

    return coordinator.exclusiveStart(async () => {
      setConnection("connecting");
      setConnectionNote(scope === "general" ? "正在启动通用会话…" : scope === "scratch" ? "正在创建隔离 Scratch…" : "正在启动项目引擎…");
      if (options.announce !== false) setBanner(scope === "general" ? "正在准备通用会话…" : scope === "scratch" ? "正在准备隔离 Scratch…" : "正在准备项目工作区；首次启动可能需要约 20 秒…");
      runtimes.invalidate();
      try {
        const preferredUrl = normalizeLocalBaseUrl(remoteProjectRef.current ? "http://127.0.0.1:8080" : options.baseUrl);
        remoteProjectRef.current = null;
        const parsed = new URL(preferredUrl);
        const preferredPort = Number(parsed.port || "8080");
        const status = await runtimes.launch({
          repoRoot: scope === "project" ? root : null,
          scope,
          workspaceId: options.workspaceId ?? options.sid,
          port: preferredPort,
          token: options.token?.trim() || null,
        });
        const actualUrl = normalizeLocalBaseUrl(status.baseUrl || preferredUrl);
        setBaseUrl(actualUrl);
        if (scope === "project") localStorage.setItem(STORAGE_KEYS.repoRoot, root);
        else localStorage.removeItem(STORAGE_KEYS.repoRoot);
        localStorage.setItem(STORAGE_KEYS.baseUrl, actualUrl);
        const probe = new GatewayClient({ baseUrl: actualUrl, token: options.token ?? "" });
        await probe.waitUntilReady(750, 100);
        const connected = await connectToRuntime(options.sid, {
          baseUrl: actualUrl,
          repoRoot: scope === "project" ? root : "",
          token: options.token ?? "",
          scope,
        });
        if (!connected) {
          // 被更新的连接取代（比如启动时紧接着切到常驻电脑）不是失败，不提示
          if (coordinator.lastConnectFailed) setBanner("本地引擎已经启动，但工作区连接失败；可在 设置 → 高级 里查看连接参数");
          return false;
        }
        setBanner(null);
        return true;
      } catch (error) {
        runtimes.invalidate();
        setConnection("error");
        setConnectionNote(errorText(error, "工作区启动失败"));
        setBanner(errorText(error, "工作区启动失败"));
        await runtimes.reloadAfterFailure(processStatus.runtimeId);
        return false;
      }
    });
  }, [connectToRuntime, coordinator, processStatus.runtimeId, runtimes]);

  const localRuntimeControls = useLocalRuntimeControls({
    processStatus, scope: activeScope, runtime, repoRoot, baseUrl, sid: activeSid, token, runtimes, disconnect, startWorkspace,
    remote: () => Boolean(remoteProjectRef.current), starting: () => coordinator.starting,
    switching: () => coordinator.switching, client: () => clientRef.current,
    readActivity: (status, checkedToken) => new GatewayClient({ baseUrl: status.baseUrl!, token: checkedToken }).getRuntimeActivity(),
    onBanner: setBanner,
  });
  const { target: runtimeControlStatus, busy: runtimeActionBusy, note: runtimeActionNote,
    start: startRuntime, stop: stopRuntime, reload: reloadRuntime, restartIfRunning: restartCurrentRuntimeForLlmProfile } = localRuntimeControls;

  useEffect(() => {
    if (initializationStartedRef.current) return;
    initializationStartedRef.current = true;
    void (async () => {
      const smokeMode = await invoke<boolean>("desktop_smoke_ready").catch(() => false);
      if (smokeMode) {
        setBooting(false);
        return;
      }

      try {
        const [initial] = await Promise.all([runtimes.readInitial(), refreshProjectRegistry()]);
        runtimes.adoptInitial(initial);
        const [status] = initial;

        // 老配置里还没有默认服务的模型清单：在启动引擎**之前**补拉一次，引擎一起来就认识这些模型。
        // 不能等连上后再拉再重启——刚启动就重启会和启动流程撞车（真机 2026-10-06：引擎没能重新拉起）。
        try {
          const profile = await invoke<DesktopLlmProfileStatus>("get_llm_profile");
          // 只有模型名、没有分类信息的旧清单也补一次，选择器才能按服务端分类分组。
          if (profile.configured && (profile.modelInfo?.length ?? 0) === 0) {
            applyLlmProfile(await invoke<DesktopLlmProfileStatus>("refresh_llm_providers"));
          }
        } catch {
          // 拉不到清单不影响启动：输入框只列已配置的模型。
        }

        // 每次打开都从一个新对话开始；之前的会话都在左侧「最近」里，点一下就能回去。
        // 空会话不会落盘，所以不会在列表里越积越多。
        const sid = createSessionId();
        localStorage.setItem(STORAGE_KEYS.generalSid, sid);
        localStorage.setItem(STORAGE_KEYS.sid, sid);
        localStorage.removeItem(STORAGE_KEYS.repoRoot);
        setActiveSid(sid);
        setRepoRoot("");
        setSettingsOpen(false);
        await startWorkspace({
          scope: "general",
          baseUrl: status.scope === "general" && status.running ? status.baseUrl || baseUrl : baseUrl,
          sid,
          token: "",
          announce: false,
        });
      } catch (error) {
        setConnection("error");
        setConnectionNote("通用会话未能启动");
        setBanner(errorText(error, "无法启动通用会话"));
      } finally {
        setBooting(false);
      }
    })();
  }, [activeSid, baseUrl, refreshProjectRegistry, runtimes, startWorkspace]);

  // 当前 runtime 意外退出：还连着它（同一工作区范围）就断开，恢复记录与提示由监督器处理。
  const onRuntimeStopped = useCallback(async (status: GatewayProcessStatus) => {
    if (status.scope === activeScope) await disconnect();
  }, [activeScope, disconnect]);
  useRuntimeSupervision({ runtimes, runtimeId: processStatus.runtimeId, onUnexpectedStop: onRuntimeStopped, onBanner: setBanner });

  const readCodexObserver = useCallback(() => readLocalCodexObserver({
    runtimes: () => invoke<GatewayProcessStatus[]>("list_gateway_processes"),
    read: source => new GatewayClient({ baseUrl: source.baseUrl!,
      token: runtimes.tokenFor(source.runtimeId!) }).getCodexObserverStatus(),
  }), [runtimes]);

  useWorkspacePolling({ connection, activeScope, runtimeId: processStatus.runtimeId, refreshRuns, refreshSessions,
    refreshDecisions, refreshAudit, refreshPrDelivery, snapshotTodayJournal });

  const handledPublishRef = useRef(0);
  useEffect(() => {
    if (!publishedArtifact || connection !== "connected" || publishedArtifact.seq === handledPublishRef.current) return;
    handledPublishRef.current = publishedArtifact.seq;
    const { id } = publishedArtifact;
    void refreshProjectAssets(activeScope !== "general").then(() => {
      setSelectedArtifactId(id);
      // 同一 id 的新版本不会改变 selectedArtifactId，这里显式重载预览到最新版。
      void loadArtifactPreview(id);
    });
  }, [activeScope, connection, loadArtifactPreview, publishedArtifact, refreshProjectAssets, setSelectedArtifactId]);

  useEffect(() => {
    if (connection !== "connected" || !selectedArtifactId) return;
    void loadArtifactPreview(selectedArtifactId);
  }, [connection, loadArtifactPreview, selectedArtifactId]);

  useEffect(() => {
    if (!settingsOpen || connection !== "connected" || activeScope !== "project") return;
    void refreshHookStatus();
    void refreshExtensionsInspect();
  }, [activeScope, connection, refreshExtensionsInspect, refreshHookStatus, settingsOpen]);


  const clearSessionView = () => {
    conversationLocation.reset();
    autoScrollRef.current = true;
    confirmationReplyRef.current?.close();
    confirmationCheckRef.current = null;
    setMessages([]);
    setPlan([]);
    setActivities([]);
    setStreaming("");
    setStreamingRid(undefined);
    updateActiveTurnRid(null);
    setPromptQueue([]);
    setPromptQueuePaused(false);
    setPendingConfirm(null);
    setWorkspaceRequest(null);
    setDiffPayload(null);
    setContextItems([]);
    abandonWorkspaceSave();
    setBusy(false);
  };

  const clearProjectView = () => {
    clearSessionView();
    setSessions([]);
    setProtocolVersion(null);
    setRuntime({});
    resetChangeReview();
    setRuns([]);
    resetTasks();
    resetGoalNavigation();
    setGoals([]);
    setNotices([]);
    noticeReadRef.current++; noticeRevisionRef.current++;
    setDecisions([]);
    setAuditEntries([]);
    resetJournal();
    resetProjectAssets();
    resetWorkspaceFiles();
    setToken("");
    resetNotificationBaseline();
  };

  /**
   * 切项目 / 范围 / 常驻电脑的共同骨架：同一时刻只允许一个切换（进门时由协调器再查一次，
   * 调用方前面的确认框期间另一次切换抢先进来也不会叠上）；前后各作废一次监督器的旧轮次；
   * 先断开、清视图，再由 body 连到新的地方。
   */
  const runWorkspaceSwitch = (failure: string, body: () => Promise<boolean>): Promise<boolean> =>
    coordinator.exclusiveSwitch(async () => {
      runtimes.invalidate();
      try {
        await disconnect();
        clearProjectView();
        return await body();
      } catch (error) {
        setBanner(errorText(error, failure));
        return false;
      } finally {
        runtimes.invalidate();
      }
    });

  const switchProject = async (project: DesktopProjectProfile, options: { fresh?: boolean; sid?: string } = {}): Promise<boolean> => {
    if (coordinator.switching) return false;
    if (project.kind !== "remote" && !remoteProjectRef.current && project.repoRoot === repoRoot.trim()) {
      setBaseUrl(project.baseUrl);
      localStorage.setItem(STORAGE_KEYS.baseUrl, project.baseUrl);
      localStorage.setItem(STORAGE_KEYS.lastProjectId, project.id);
      if (connection !== "connected") {
        const sid = localStorage.getItem(projectSessionKey(project.id)) ?? activeSid;
        return startWorkspace({ repoRoot: project.repoRoot, baseUrl: project.baseUrl, sid, token: "" });
      }
      return true;
    }
    if (savingFile) {
      setBanner("请先完成或拒绝当前源码保存确认");
      return false;
    }
    if (coordinator.starting) {
      setBanner("本地引擎正在启动，请稍候再切换项目");
      return false;
    }
    if (connection === "connecting") {
      setBanner("runtime 正在连接，请等待连接完成后再切换项目");
      return false;
    }
    if (editorDirty && !await confirmAction("当前文件有未保存修改，切换项目会放弃这些修改。确定继续吗？")) {
      return false;
    }
    return runWorkspaceSwitch("切换项目失败", async () => {
      // fresh：启动时回到项目也从新对话开始（上次的会话仍在「最近」里）。
      const sid = options.sid
        ?? (options.fresh ? null : localStorage.getItem(projectSessionKey(project.id)))
        ?? createSessionId();
      setActiveSid(sid);
      setRepoRoot(project.repoRoot);
      setBaseUrl(project.baseUrl);
      if (project.kind === "remote") {
        remoteProjectRef.current = project.id;
        runtimes.markRemote(project, false);
        localStorage.setItem(STORAGE_KEYS.lastProjectId, project.id);
        await refreshWorkspaceFiles(project.repoRoot);
        openInspector("inbox");
        return await connectToRuntime(sid, { baseUrl: project.baseUrl, repoRoot: project.repoRoot,
          scope: "project", remoteProjectId: project.id, token: "" });
      }
      remoteProjectRef.current = null;
      localStorage.setItem(STORAGE_KEYS.sid, sid);
      localStorage.setItem(STORAGE_KEYS.repoRoot, project.repoRoot);
      localStorage.setItem(STORAGE_KEYS.baseUrl, project.baseUrl);
      localStorage.setItem(STORAGE_KEYS.lastProjectId, project.id);
      await refreshWorkspaceFiles(project.repoRoot);
      openInspector("files");
      setSettingsOpen(false);
      const recovery = runtimeRecoveries.find((item) => item.projectId === project.id);
      if (recovery?.status === "running" && !runtimeProcesses.some((item) => item.projectId === project.id)) {
        try {
          const probe = new GatewayClient({ baseUrl: recovery.baseUrl, token: "" });
          await probe.waitUntilReady(4, 200);
          const connected = await connectToRuntime(sid, {
            baseUrl: recovery.baseUrl,
            repoRoot: project.repoRoot,
            token: "",
            scope: "project",
          });
          if (connected) {
            runtimes.markAttached({
              ...EMPTY_PROCESS,
              projectId: project.id,
              scope: "project",
              workspaceRoot: project.repoRoot,
              repoRoot: project.repoRoot,
              baseUrl: recovery.baseUrl,
              message: "已重新附着到上次的 runtime；当前进程不由本次 Desktop 生命周期接管",
            });
            setBanner("已重新附着到上次仍在运行的项目 runtime");
            return true;
          }
        } catch {
          // 上次进程已不存在或端口已被复用；下面启动新的受托管 runtime。
        }
      }
      return await startWorkspace({
        repoRoot: project.repoRoot,
        baseUrl: project.baseUrl,
        sid,
        token: "",
      });
    });
  };

  // 重启后回到上次停的项目：通用会话先起来（快、且是失败时的落点），连上后再切回去。
  // 真机 2026-09-17：关掉再开永远落在通用会话，用户得自己重新点一次项目。
  // 只认注册表里还在的项目；人主动切回通用/Scratch 时那条记录会被清掉，不会硬把人拽回去。
  useEffect(() => {
    if (projectRestoreDoneRef.current) return;
    if (connection !== "connected" || activeScope !== "general") return;
    const lastProjectId = localStorage.getItem(STORAGE_KEYS.lastProjectId);
    const target = projectToRestore(projects, lastProjectId);
    if (!target) {
      // 记着的项目目录已不存在：清掉这条记录，以后启动不再白白尝试。
      if (lastProjectId && projects.some((project) => project.id === lastProjectId && project.missing)) {
        try { localStorage.removeItem(STORAGE_KEYS.lastProjectId); } catch { /* 存储不可用时下次再清 */ }
      }
      return;
    }
    projectRestoreDoneRef.current = true;
    void (async () => {
      const restored = await switchProject(target, { fresh: true }).catch(() => false);
      if (!restored) {
        // 只提示一次：恢复失败就不再记着它，下次启动直接留在通用会话。
        try { localStorage.removeItem(STORAGE_KEYS.lastProjectId); } catch { /* 同上 */ }
        setBanner(`未能回到上次的项目「${target.name}」，已留在通用会话`);
      }
    })();
  }, [connection, activeScope, projects, runtimeRecoveries, switchProject]);

  const switchManagedScope = async (
    scope: "general" | "scratch",
    managedRuntime?: GatewayProcessStatus,
  ): Promise<boolean> => {
    if (coordinator.switching) return false;
    if (activeScope === scope && connection === "connected" && !remoteProjectRef.current
      && (!managedRuntime || managedRuntime.runtimeId === processStatus.runtimeId)) return true;
    if (savingFile) {
      setBanner("请先完成或拒绝当前源码保存确认，再切换工作区范围");
      return false;
    }
    // 先拒绝再断开：startWorkspace 在引擎启动中会直接返回 false，断开之后才发现就回不去了。
    if (coordinator.starting) {
      setBanner("本地引擎正在启动，请稍候再切换工作区范围");
      return false;
    }
    if (editorDirty && !await confirmAction("当前文件有未保存修改，切换范围会放弃这些修改。确定继续吗？")) {
      return false;
    }
    return runWorkspaceSwitch("切换工作区范围失败", async () => {
      const sid = scope === "general"
        ? localStorage.getItem(STORAGE_KEYS.generalSid) ?? createSessionId()
        : managedRuntime?.workspaceId ?? createSessionId();
      if (scope === "general") localStorage.setItem(STORAGE_KEYS.generalSid, sid);
      else localStorage.setItem(STORAGE_KEYS.scratchSid, sid);
      setActiveSid(sid);
      setRepoRoot("");
      localStorage.setItem(STORAGE_KEYS.sid, sid);
      localStorage.removeItem(STORAGE_KEYS.repoRoot);
      localStorage.removeItem(STORAGE_KEYS.lastProjectId);   // 人主动离开项目 → 下次别再自动回去
      if (scope === "scratch") openInspector("files");
      else {
        setInspectorTabState("project");
        setInspectorOpen(false);
      }
      setSettingsOpen(false);
      return await startWorkspace({
        scope,
        baseUrl: managedRuntime?.baseUrl || baseUrl,
        sid,
        workspaceId: sid,
        token: "",
      });
    });
  };

  const forgetProject = async (project: DesktopProjectProfile) => {
    if (coordinator.switching) return;
    if (project.kind === "remote" ? remoteProjectRef.current === project.id : !remoteProjectRef.current && project.repoRoot === repoRoot.trim()) {
      setBanner("当前项目不能从最近项目中移除，请先切换到其他项目");
      return;
    }
    const managedRuntime = runtimeProcesses.find((item) => item.projectId === project.id && item.running);
    const effect = managedRuntime
      ? "该项目仍在后台运行；移除会同时停止它，但不会删除项目文件。"
      : "项目文件不会被删除。";
    if (!await confirmAction(`从最近项目中移除“${project.name}”？${effect}`)) return;
    try {
      if (managedRuntime?.runtimeId) {
        await runtimes.stopBackground(managedRuntime.runtimeId);
      }
      setProjects(await invoke<DesktopProjectProfile[]>("forget_desktop_project", { projectId: project.id }));
      localStorage.removeItem(projectSessionKey(project.id));
    } catch (error) {
      setBanner(errorText(error, "移除最近项目失败"));
    }
  };

  const switchSession = async (sid: string) => {
    if (sid === activeSid) return connection === "connected";
    if (savingFile) {
      setBanner("请先完成或拒绝当前源码保存确认");
      return false;
    }
    clearSessionView();
    setActiveSid(sid);
    return connectToRuntime(sid);
  };

  const activateRuntimeInbox = async (runtimeInbox: DesktopRuntimeInbox): Promise<boolean> => {
    const target = inboxTarget(runtimeInbox, { currentRuntimeId: processStatus.runtimeId, connected: connection === "connected",
      projects, runtimeProcesses });
    switch (target.kind) {
      case "current": return true;
      case "assistant":
        await changeAppMode("assistant");
        return remoteProjectRef.current === target.host.id;
      case "scope": return switchManagedScope(target.scope, target.runtime);
      case "project": return switchProject(target.project);
      case "unavailable":
        setBanner(target.message);
        return false;
    }
  };

  const openRuntimeInboxSession = async (runtimeInbox: DesktopRuntimeInbox, sid: string, needsInput = false) => {
    if (!await activateRuntimeInbox(runtimeInbox)) return;
    if(!await switchSession(sid))return;
    setMainView("chat");
    if (needsInput) openInspector("info");
    else setInspectorOpen(false);
  };

  const inboxNavigationRef = useRef(0);
  const openRuntimeInboxPanel = async (
    runtimeInbox: DesktopRuntimeInbox,
    tab: Extract<InspectorTab, "inbox" | "decisions" | "goals" | "tasks" | "runs">,
    sid = "",
    taskId = "",
    goalId = "",
  ) => {
    const attempt = ++inboxNavigationRef.current;
    if (!await activateRuntimeInbox(runtimeInbox) || attempt !== inboxNavigationRef.current) return;
    const client = clientRef.current;
    if (sid && tab !== "goals") await switchSession(sid);
    if (attempt !== inboxNavigationRef.current || client !== clientRef.current) return;
    if (tab === "tasks" && taskId) focusTask(taskId);
    else {
      openInspector(tab);
      if (tab === "goals") {
        resetGoalNavigation();
        if (goalId) void focusGoal(goalId);
        else void refreshGoals();
      }
    }
  };

  const newSession = async () => {
    if (creatingSessionRef.current) return;
    if (savingFile) {
      setBanner("请先完成或拒绝当前源码保存确认");
      return;
    }
    // Already on an unused draft: clear attachments without creating another server slot.
    if (!currentSession && !busy && !messages.length && !streaming && !plan.length
        && !activities.length && !pendingConfirm && !promptQueue.length) {
      clearSessionView();
      return;
    }
    const sid = createSessionId();
    creatingSessionRef.current = true;
    clearSessionView();
    setActiveSid(sid);
    try {
      await connectToRuntime(sid);
    } finally {
      creatingSessionRef.current = false;
    }
  };

  // ── 助理模式 ─────────────────────────────────────────────────────────────
  // 常驻电脑上有哪些项目（它的登记表）：工作台「在哪做」据此判断能不能切到常驻电脑。
  const residentHost = findAssistantHost(projects);
  const [residentProjects, setResidentProjects] = useState<ResidentProject[]>([]);
  // 空白对话里的上手示例（用熟了或点了「不再显示」就收起）
  const [showStarterTips, setShowStarterTips] = useState(() => shouldShowStarterTips());
  useEffect(() => {
    if (!residentHost) { setResidentProjects([]); return undefined; }
    let cancelled = false;
    new GatewayClient({ baseUrl: residentHost.baseUrl, token: "", remoteProjectId: residentHost.id })
      .listResidentProjects()
      .then((items) => { if (!cancelled) setResidentProjects(items); })
      .catch(() => { if (!cancelled) setResidentProjects([]); });   // 连不上常驻电脑：只是不提供「常驻电脑」选项
    return () => { cancelled = true; };
  }, [residentHost?.id, residentHost?.baseUrl]);  // eslint-disable-line react-hooks/exhaustive-deps

  // 常驻电脑上各项目的会话（侧栏里与本机会话合在一起显示）。在常驻电脑那份上时直接用当前会话列表。
  const [residentSessions, setResidentSessions] = useState<Record<string, SessionSummary[]>>({});
  const residentTwinIds = residentHost
    ? projects.filter((item) => item.kind === "remote" && !isAssistantHost(item) && localTwin(item, projects) && residentTwin(item, projects, residentHost)?.id === item.id).map((item) => item.id)
    : [];
  const residentTwinKey = residentTwinIds.join("|");
  useEffect(() => {
    let cancelled = false;
    void Promise.all(residentTwinIds.map(async (id) => {
      const project = projects.find((item) => item.id === id);
      if (!project) return [id, []] as const;
      const list = await new GatewayClient({ baseUrl: project.baseUrl, token: "", remoteProjectId: id }).listSessions().catch(() => []);
      return [id, list] as const;
    })).then((entries) => { if (!cancelled) setResidentSessions(Object.fromEntries(entries)); });
    return () => { cancelled = true; };
  }, [residentTwinKey, activeSid]);  // eslint-disable-line react-hooks/exhaustive-deps

  /** 当前工作台项目（本机那份或常驻电脑那份）。 */
  const currentWorkProject = (): DesktopProjectProfile | undefined => remoteProjectRef.current
    ? projects.find((item) => item.id === remoteProjectRef.current && !isAssistantHost(item))
    : activeScope === "project" ? projects.find((item) => item.kind !== "remote" && item.repoRoot === repoRoot.trim()) : undefined;

  // 设置页的外部执行者状态：问「当前连着的那个引擎」自己的 /api/delegations/capabilities。
  // 本机引擎与常驻电脑走同一个接口、同一份内核实现；本机设置从不改常驻电脑的配置，两边不混。
  const [engineWorkers, setEngineWorkers] = useState<EngineWorkersView | null>(null);
  const remoteProjectKey = remoteProjectRef.current;
  useEffect(() => {
    const project = currentWorkProject();
    const client = clientRef.current;
    if (!settingsOpen) { setEngineWorkers(null); return undefined; }
    const place = project?.kind === "remote" ? "resident" as const : "local" as const;
    const name = place === "resident" ? project?.name ?? "常驻电脑" : "本机";
    if (!client) { setEngineWorkers({ place, name, summary: null, error: "引擎未运行，启动后可检查" }); return undefined; }
    let alive = true;
    client.dispatchCapabilities()
      .then((value) => { if (alive) setEngineWorkers({ place, name, summary: value.external_workers ?? null }); })
      .catch(() => { if (alive) setEngineWorkers({ place, name, summary: null, error: "暂时读不到引擎的执行者状态" }); });
    return () => { alive = false; };
  }, [settingsOpen, remoteProjectKey, connection]);  // eslint-disable-line react-hooks/exhaustive-deps

  /** 「在哪做」切换：换到另一边的同名项目；常驻电脑那份还没登记就按它的登记表登记（令牌沿用常驻电脑）。 */
  const changeWorkPlace = async (target: WorkPlace) => {
    const current = currentWorkProject();
    if (!current || !residentHost) return;
    if (target === "local") {
      const twin = localTwin(current, projects);
      if (twin) await switchProject(twin);
      else setBanner("本机还没有这个项目：先在本机添加它的目录");
      return;
    }
    if (current.kind !== "remote" && clientRef.current) {
      // 本机没提交的改动不会跟过去：常驻电脑上那份 clone 看不到它们。
      const review = await clientRef.current.getGitReview().catch(() => null);
      const changed = review?.files.length ?? 0;
      if (changed > 0 && !await confirmAction(
        `本机有 ${changed} 个文件的改动还没提交${review?.branch ? `（分支 ${review.branch}）` : ""}。\n`
        + "常驻电脑上那份项目看不到这些改动；需要的话先提交并推送，再到常驻电脑上拉取。\n\n仍然切到常驻电脑吗？")) return;
    }
    let twin = residentTwin(current, projects, residentHost);
    if (!twin) {
      const entry = residentEntry(current, residentProjects);
      if (!entry) {
        setBanner("常驻电脑上还没有这个项目");
        return;
      }
      try {
        const profile = await invoke<DesktopProjectProfile>("remember_resident_project", {
          hostProjectId: residentHost.id, port: entry.port, remoteRepoRoot: entry.repo_root, name: entry.name,
        });
        twin = profile;
        setProjects((previous) => [profile, ...previous.filter((item) => item.id !== profile.id)]);
      } catch (error) {
        setBanner(errorText(error, "登记常驻电脑上的项目失败"));
        return;
      }
    }
    await switchProject(twin);
  };

  /** 打开「主要聊天」：每台电脑第一次进助理模式时生成一个固定会话并记住，之后一直用它。 */
  const openMainChat = async (hostId: string | null = remoteProjectRef.current && remoteProjectRef.current === assistantHostRef.current ? remoteProjectRef.current : null) => {
    const key = mainChatKey(hostId);
    let sid = "";
    try { sid = localStorage.getItem(key) ?? ""; } catch { /* 读不到就新开一个 */ }
    if (!sid) {
      sid = createSessionId();
      try { localStorage.setItem(key, sid); } catch { /* 存不下也照常使用本次会话 */ }
    }
    setMainChatSid(sid);
    setMainView("chat");
    await switchSession(sid);
  };

  /** 连到常驻电脑上的助理（通用范围）。已经连着就直接返回；连不上返回原因。 */
  const connectAssistantHost = async (host: DesktopProjectProfile): Promise<true | string> => {
    if (remoteProjectRef.current === host.id && assistantHostRef.current === host.id && connection === "connected") return true;
    // 启动时本机引擎还在拉起（正在切换，或启动流程还在装填数据）：等它走完再连常驻电脑。
    // 否则两次连接交错：本机那次被顶掉，常驻电脑又连不上时，回落会撞上「引擎正在启动」被丢弃，
    // 结果两头都没连上（启动时常驻电脑离线即复现）。
    if (!await coordinator.waitUntilIdle()) return "本机引擎还在启动或切换，稍后会自动再试";
    if (savingFile) {
      setBanner("请先完成或拒绝当前源码保存确认");
      return "有一个源码保存确认还没处理";
    }
    // 先只测通不通：连不上就别断开本机——断开再回落会清掉当前对话，而且真实原因会被回落提示盖掉
    const unreachable = await probeAssistantHost(host.id);
    if (unreachable) return unreachable;
    const switched = await runWorkspaceSwitch("连接常驻电脑失败", async () => {
      const key = mainChatKey(host.id);
      let sid = "";
      try { sid = localStorage.getItem(key) ?? ""; } catch { /* 读不到就新开一个 */ }
      if (!sid) sid = createSessionId();
      // 主要聊天由常驻电脑统一登记（手机、电脑共用一段）：已登记就用那段，没登记就把本机这段报上去
      sid = await resolveMainChat(new GatewayClient({ baseUrl: host.baseUrl, token: "", remoteProjectId: host.id }), sid);
      try { localStorage.setItem(key, sid); } catch { /* 存不下也照常使用本次会话 */ }
      setMainChatSid(sid);
      setActiveSid(sid);
      setRepoRoot("");
      setBaseUrl(host.baseUrl);
      remoteProjectRef.current = host.id;
      assistantHostRef.current = host.id;
      runtimes.markRemote(host, true);
      return await connectToRuntime(sid, { baseUrl: host.baseUrl, repoRoot: "", scope: "general",
        remoteProjectId: host.id, token: "" });
    });
    return switched || (coordinator.lastConnectError ?? "连接常驻电脑失败");
  };

  /** 新建旁聊。从主要聊天出发时必须换新会话——newSession 对空草稿只清界面不换 id，会把旁聊并进主要聊天。 */
  const newSideChat = async () => {
    setMainView("chat");
    if (activeSid !== mainChatSid) {
      await newSession();
      return;
    }
    const sid = createSessionId();
    clearSessionView();
    setActiveSid(sid);
    await connectToRuntime(sid);
  };

  const onAssistantHost = () => coordinator.onAssistantHost;

  // 连回常驻电脑后，从本机引擎只读取出离线时聊过的会话（本机引擎一直在跑，不用切换过去）
  const onResidentNow = appMode === "assistant" && connection === "connected" && onAssistantHost();
  useEffect(() => {
    if (!onResidentNow || offlineChatIds.length === 0) { setOfflineChats([]); return; }
    let current = true;
    void (async () => {
      const source = localGeneral(await invoke<GatewayProcessStatus[]>("list_gateway_processes").catch(() => []));
      if (!source) return;
      const local = new GatewayClient({ baseUrl: source.baseUrl!, token: runtimes.tokenFor(source.runtimeId!) });
      const chats = await Promise.all(offlineChatIds.map((item) => local.getAssistantTranscript(item.sid).catch(() => null)));
      if (current) setOfflineChats(chats.filter((chat): chat is PhoneChat => Boolean(chat && chat.transcript.length)));
    })();
    return () => { current = false; };
  }, [onResidentNow, offlineChatIds, runtimes]);

  /** 「带回常驻电脑」：把本机这段对话作为文本附件放进输入框，用户补一句话再发；不自动发送、不合并。 */
  const bringOfflineChat = (chat: PhoneChat) => {
    const attachment = offlineChatAttachment(chat);
    const others = attachments.filter((item) => item.id !== attachment.id);
    const check = checkAttachment(attachment, others);
    if (!check.ok) { setBanner(check.reason); return; }
    setAttachments([...others, attachment]);
    setOfflineChatIds(dismissOfflineChat(chat.sid));
    setOfflineChatSid(null);
    setMainView("chat");
    setBanner("已把这段本机对话放进输入框，补一句话再发给常驻助理");
  };

  // 登记了常驻电脑、在助理模式、却连在本机：明着提示，并在后台自动重连（正在回复时不切）
  const residentMissed = appMode === "assistant" && Boolean(findAssistantHost(projects)) && !onAssistantHost()
    && connection === "connected" && !projectSwitching;
  const residentRetryRef = useRef(0);
  // 每次重试结束都 +1，定时器据此重排：以前只按状态变化排，失败一次之后就再也不试了
  const [residentRetryRound, setResidentRetryRound] = useState(0);
  /** 常驻电脑最近一次连不上的原因：显示在提示条里（横幅会被随后的本机连接清掉） */
  const [residentFailure, setResidentFailure] = useState<string | null>(null);
  useEffect(() => {
    if (!residentMissed) {
      if (onAssistantHost()) { residentRetryRef.current = 0; setResidentFailure(null); }
      return;
    }
    if (busy) return;
    const timer = window.setTimeout(() => {
      residentRetryRef.current += 1;
      const host = findAssistantHost(projects);
      if (!host) return;
      void retryResident({
        reachable: () => probeAssistantHost(host.id),
        connect: async () => { await changeAppMode("assistant"); return onAssistantHost(); },
        onUnreachable: setResidentFailure,
      }).finally(() => setResidentRetryRound((round) => round + 1));
    }, residentRetryDelay(residentRetryRef.current));
    return () => window.clearTimeout(timer);
    // changeAppMode 每次渲染都是新函数；只按状态变化重排定时器
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [residentMissed, busy, residentRetryRound]);

  const changeAppMode = async (mode: AppMode, known: DesktopProjectProfile[] = projects) => {
    if (localRuntimeControls.isOperating()) { setBanner("正在核对本机引擎，请等待操作完成后再切换"); return; }
    setAppMode(mode);
    try { localStorage.setItem(STORAGE_KEYS.appMode, mode); } catch { /* 只影响下次启动记不住 */ }
    if (mode !== "assistant") {
      // 回到进助理模式前的那个项目；没有就回本机通用工作区（工作台不在常驻电脑上干活）。
      const back = workProjects(known).find((project) => project.id === workReturnRef.current && !project.missing);
      workReturnRef.current = null;
      if (back) await switchProject(back);
      else if (onAssistantHost()) await switchManagedScope("general");
      return;
    }
    if (!onAssistantHost()) {
      try { workReturnRef.current = activeScope === "project" ? localStorage.getItem(STORAGE_KEYS.lastProjectId) : null; } catch { workReturnRef.current = null; }
    }
    // 助理默认住在常驻电脑上（合上笔记本也照常干活）；没登记或连不上，才用本机通用工作区兜底。
    const hostId = await placeAssistant({
      host: findAssistantHost(known), connectHost: connectAssistantHost,
      onHostFailure: (host, reason) => {
        setResidentFailure(reason);
        setBanner(`常驻电脑「${host.name}」暂时连不上（${reason}），先用本机助理；它不在时你交代的事不会继续做。`);
      },
      awayFromLocalGeneral: () => Boolean(remoteProjectRef.current) || activeScope !== "general",
      switchToLocalGeneral: () => switchManagedScope("general"),
    });
    if (hostId) setResidentFailure(null);
    if (hostId === undefined) return;
    setInspectorOpen(true);
    setAssistantTab("feed");
    await openMainChat(hostId);
  };

  const renameSession = async (session: SessionSummary) => {
    const title = window.prompt("重命名会话", session.title)?.trim();
    if (!title || !clientRef.current) return;
    try {
      await clientRef.current.renameSession(session.sid, title);
      await refreshSessions();
    } catch (error) {
      setBanner(errorText(error, "重命名失败"));
    }
  };

  const deleteSession = async (session: SessionSummary) => {
    if (!await confirmAction(`删除会话“${session.title}”？此操作不可撤销。`) || !clientRef.current) return;
    try {
      await clientRef.current.deleteSession(session.sid);
      if (session.sid === activeSid) await newSession();
      else await refreshSessions();
    } catch (error) {
      setBanner(errorText(error, "删除失败"));
    }
  };


  const sendPrompt = async (
    override?: string,
    requestedMode: "plan" | "build" = mode,
    requestedContext?: ContextItem[],
  ): Promise<boolean> => {
    const text = (override ?? prompt).trim();
    const selectedContext = [...(requestedContext ?? contextItems)].slice(0, 8);
    // 附件只跟随输入框发送；面板一键派发（override）不夹带输入框里的附件。
    const selectedAttachments = override === undefined ? attachments : [];
    if ((!text && selectedContext.length === 0 && selectedAttachments.length === 0) || savingFile) return false;

    if (connection !== "connected" || !clientRef.current) {
      if (coordinator.starting || coordinator.switching) {
        setBanner("工作区正在准备，请稍候发送");
        return false;
      }
      const scope = repoRoot.trim() ? "project" : activeScope;
      const ready = await startWorkspace({
        scope,
        repoRoot: scope === "project" ? repoRoot.trim() : undefined,
        baseUrl,
        sid: activeSid,
        workspaceId: activeSid,
        token,
      });
      if (!ready || !clientRef.current) return false;
    }

    const client = clientRef.current;
    const willQueue = busy;
    const outgoing = { text, context: selectedContext, attachments: selectedAttachments, rid: createSessionId().slice(0, 64) };
    const { rid } = outgoing;
    setPrompt("");
    setContextItems([]);
    if (selectedAttachments.length > 0) setAttachments([]);
    if (appMode === "assistant" && showStarterTips) {
      recordAssistantSend();
      setShowStarterTips(shouldShowStarterTips());
    }
    conversationLocation.reset();
    autoScrollRef.current = true;
    if (!willQueue) {
      setBusy(true);
      updateActiveTurnRid(rid);
      setActivities((previous) => upsertActivity(previous, thinkingActivity(rid)));
      setMessages((previous) => [...previous, outgoingMessage(outgoing)]);
    }
    try {
      await client.send(agentFrame(outgoing, requestedMode, modelChoice && !onAssistantHost() ? modelChoice : null));
      if (appMode === "assistant" && !onAssistantHost() && findAssistantHost(projects)) setOfflineChatIds(recordOfflineChat(activeSid));
      return true;
    } catch (error) {
      if (!willQueue) {
        setBusy(false);
        updateActiveTurnRid(null);
        setActivities((previous) => upsertActivity(previous, {
          id: `${rid}:thinking`, rid, kind: "phase", phase: "thinking",
          status: "failed", label: "发送任务失败",
        }));
      }
      setPrompt(text);
      setContextItems(selectedContext);
      if (selectedAttachments.length > 0) setAttachments(selectedAttachments);
      setBanner(errorText(error, "发送失败"));
      return false;
    }
  };

  // Cmd/Ctrl+S 保存快捷键 effect 随 <FilesPanel> 搬入组件内（调用注入的 onSave）。

  const cancelTurn = async () => {
    await clientRef.current?.send({ type: "agent_cancel" }).catch(() => undefined);
  };

  const removeQueuedPrompt = async (id: string) => {
    await clientRef.current?.send({ type: "agent_queue_remove", id }).catch((error) => {
      setBanner(errorText(error, "删除排队任务失败"));
    });
  };

  const sendQueuedPromptNow = async (id: string) => {
    await clientRef.current?.send({ type: "agent_queue_send_now", id }).catch((error) => {
      setBanner(errorText(error, "切换排队任务失败"));
    });
  };

  const answerConfirmationById = async (id: string, ok: boolean) => {
    await confirmationReplyRef.current?.submit(id,ok);
    void refreshDecisions();
    void refreshAudit();
  };
  const checkConfirmation = async () => {
    const client=clientRef.current;
    if(!client || !pendingConfirm || connection!=="connected")return;
    const check={client,scope:activeSid,id:pendingConfirm.id,nonce:createSessionId()};
    confirmationCheckRef.current=check;
    try { await client.send({type:"agent_confirm_check",id:check.id,nonce:check.nonce}); }
    catch { if(confirmationCheckRef.current===check && clientRef.current===client && confirmationViewRef.current.scope===check.scope){confirmationCheckRef.current=null;setBanner("核对发送失败，请恢复连接后重试");} }
    window.setTimeout(()=>{
      if(confirmationCheckRef.current!==check || clientRef.current!==client || confirmationViewRef.current.scope!==check.scope)return;
      confirmationCheckRef.current=null;
      setBanner("未收到核对回执，请检查连接或更新运行端后再核对；不会自动重发批准。");
    },8000);
  };

  const answerConfirmation = async (ok: boolean) => {
    if (!pendingConfirm) return;
    await answerConfirmationById(pendingConfirm.id, ok);
  };

  // selectJournalDay / continueFromYesterday 已随 journal 域收进 useJournal（S6b）。

  const toggleHookTrust = async () => {
    if (!clientRef.current || !hookStatus?.configured || hookTrustBusy) return;
    setHookTrustBusy(true);
    try {
      const next = await clientRef.current.setHookTrust(!hookStatus.trusted);
      setHookStatus(next);
      void refreshAudit();
      void refreshExtensionsInspect();
      setBanner(next.trusted
        ? `已信任并启用 ${next.hooks.length} 个项目 Hook；当前会话已热重载`
        : "已撤销项目 Hook 信任；当前会话已停止执行仓库 Hook");
    } catch (error) {
      setBanner(errorText(error, "Hook 信任状态更新失败"));
    } finally {
      setHookTrustBusy(false);
    }
  };

  // saveJournalSnapshot / addJournalNote 已随 journal 域收进 useJournal（S6b，banner 经注入回调）。

  const addRepoMemoryFact = async () => {
    const content = repoMemoryDraft.trim();
    if (!clientRef.current || !content) return;
    if (!await confirmAction(
      `把这条事实写入仓库记忆？\n\n${content.slice(0, 280)}\n\n下个新会话及其子 Agent 会自动带上它。`,
    )) return;
    setProjectAssetsLoading(true);
    setProjectAssetsError("");
    try {
      const snapshot = await clientRef.current.addRepoMemory(content);
      setRepoMemory(snapshot);
      setRepoMemoryDraft("");
      setBanner(snapshot.message ?? "仓库记忆已保存");
      await refreshAudit();
    } catch (error) {
      setProjectAssetsError(errorText(error, "仓库记忆写入失败"));
    } finally {
      setProjectAssetsLoading(false);
    }
  };

  const attachSelectedArtifact = () => {
    if (!selectedArtifact) return;
    const reference = `@artifact:${selectedArtifact.id}`;
    setPrompt((current) => current.includes(reference) ? current : `${current.trim()}${current.trim() ? " " : ""}${reference} `);
    setBanner(`已把制品“${selectedArtifact.title}”加入输入框；发送后 Agent 可基于当前版本继续迭代`);
  };

  const openSelectedArtifact = async () => {
    if (!selectedArtifact) return;
    if (token.trim()) {
      setBanner("当前 runtime 使用 token；为避免把凭据放进 URL，请使用 Desktop 内置沙箱预览");
      return;
    }
    try {
      await openUrl(selectedArtifact.url);
    } catch (error) {
      setBanner(errorText(error, "无法打开制品页面"));
    }
  };

  const chooseRepo = async () => {
    // 目录选择与注册都在后端（pick_desktop_project）：webview 拿不到命名任意路径的通道
    try {
      const profile = await invoke<DesktopProjectProfile | null>("pick_desktop_project", {
        baseUrl: normalizeLocalBaseUrl(remoteProjectRef.current ? "http://127.0.0.1:8080" : baseUrl),
      });
      if (!profile) return false;
      setProjects((previous) => [profile, ...previous.filter((item) => item.id !== profile.id)]);
      return switchProject(profile);
    } catch (error) {
      setBanner(errorText(error, "项目目录不可用"));
      return false;
    }
  };

  const acceptWorkspaceRequest = async () => {
    const request = workspaceRequest;
    if (!request) return;
    const ready = request.scope === "scratch"
      ? await switchManagedScope("scratch")
      : await chooseRepo();
    if (!ready) return;
    setWorkspaceRequest(null);
    if (request.task.trim()) setPrompt(request.task.trim());
    setBanner(
      request.task.trim()
        ? "已进入新范围；请先检查输入框中的任务，再发送给新会话"
        : "已进入新范围；可以继续描述要完成的任务",
    );
  };

  const restoreRecoveryConfig = async () => {
    const record = recoveryRecord;
    if (!record) return;
    if (busy || savingFile || connection === "connecting" || coordinator.starting) {
      setBanner("当前仍有操作进行中，暂时不能切换到恢复配置");
      return;
    }
    if (editorDirty && !await confirmAction("当前文件有未保存修改，载入上次 runtime 配置会放弃这些修改。确定继续吗？")) {
      return;
    }
    try {
      await disconnect();
      clearProjectView();
      const project = projects.find((item) => item.repoRoot === record.repoRoot);
      const sid = project
        ? localStorage.getItem(projectSessionKey(project.id)) ?? createSessionId()
        : createSessionId();
      setActiveSid(sid);
      setRepoRoot(record.repoRoot);
      setBaseUrl(record.baseUrl);
      localStorage.setItem(STORAGE_KEYS.sid, sid);
      localStorage.setItem(STORAGE_KEYS.repoRoot, record.repoRoot);
      localStorage.setItem(STORAGE_KEYS.baseUrl, record.baseUrl);
      await refreshWorkspaceFiles(record.repoRoot);
      openInspector("files");
      setSettingsOpen(false);
      const restored = await connectToRuntime(sid, {
        baseUrl: record.baseUrl,
        repoRoot: record.repoRoot,
        token: "",
      });
      if (!restored) {
        await startWorkspace({
          repoRoot: record.repoRoot,
          baseUrl: record.baseUrl,
          sid,
          token: "",
        });
      }
    } catch (error) {
      setBanner(errorText(error, "载入 runtime 恢复配置失败"));
    }
  };

  const dismissRecoveryRecord = async () => {
    try {
      await runtimes.dismissRecovery(recoveryRecord?.runtimeId);
      setBanner("已忽略上次 runtime 恢复记录");
    } catch (error) {
      setBanner(errorText(error, "清除 runtime 恢复记录失败"));
    }
  };



  const runGoal = async (goal: GoalItem, resume = false) => {
    const client = clientRef.current;
    if (!client) return;
    const taskScope = captureTaskScope();
    setGoalSubmitting(true);
    try {
      const result = await client.runGoal(goal.id, resume);
      setGoals((previous) => [result.goal, ...previous.filter((item) => item.id !== goal.id)]);
      if (taskScope.current()) upsertTask(result.task, client);
      setBanner(resume ? "已从持久计划断点续跑" : goal.status === "draft" ? "目标合同已确认，隔离开发任务开始执行" : "已按目标合同开始新一轮执行");
    } catch (error) {
      setBanner(errorText(error, "目标执行失败"));
    } finally {
      setGoalSubmitting(false);
    }
  };

  const runGoalVerifiers = async (goal: GoalItem) => {
    if (!clientRef.current) return;
    setGoalSubmitting(true);
    try {
      const result = await clientRef.current.runGoalVerifiers(goal.id);
      setGoals((previous) => [result.goal, ...previous.filter((item) => item.id !== goal.id)]);
      if (result.runs.length > 0) {
        setRuns((previous) => [
          ...result.runs,
          ...previous.filter((item) => !result.runs.some((run) => run.id === item.id)),
        ]);
        openInspector("runs");
        setBanner(`已启动 ${result.runs.length} 项隔离自动验收；结果会直接回写 Goal 证据`);
      } else if (result.goal.status === "achieved") {
        setBanner("文件检查已通过，目标达到全部验收标准");
      } else {
        setBanner(result.skipped_active ? "自动验收已在执行中" : "没有可运行的自动验收器；其余标准需要人工确认");
      }
    } catch (error) {
      setBanner(errorText(error, "运行自动验收失败"));
    } finally {
      setGoalSubmitting(false);
    }
  };

  const draftTaskCollaboration = async (task: TaskItem, action: CollaborationAction) => {
    const draft = taskCollaborationDraft(task, action);
    if (!draft) return;
    if (savingFile || prompt.trim()) {
      setBanner("请先处理当前保存确认或输入框中的草稿，再准备任务交互。");
      return;
    }
    const generation = runtimes.currentGeneration();
    if (!await switchSession(draft.sid) || generation !== runtimes.currentGeneration()) return;
    setPrompt(draft.text);
    setBanner("任务交互已放入发起会话输入框；补充要求后发送。");
  };

  const copyTaskHandoff = async (task: TaskItem) => {
    const text = task.handoff?.text;
    if (!text) {
      setBanner("这个任务还没有可交接的上下文");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setBanner("任务交接摘要已复制");
    } catch {
      setBanner("系统剪贴板不可用；可展开交接摘要后手动复制");
    }
  };

  const openTaskPr = async (id: string) => {
    if (!clientRef.current) return;
    try {
      const result = await clientRef.current.openTaskPr(id);
      setBanner(result.ok ? `Draft PR 已创建：${result.url ?? ""}` : result.error || "开 PR 失败");
    } catch (error) {
      setBanner(errorText(error, "开 PR 失败"));
    }
  };

  const startWorkspaceRun = async (
    command: string,
    kind: CommandRunKind,
    previewUrl: string,
  ): Promise<boolean> => {
    const text = command.trim();
    if (!text || !clientRef.current) return false;
    try {
      const run = await clientRef.current.startRun(
        text,
        kind,
        kind === "preview" ? previewUrl.trim() : "",
      );
      setRuns((previous) => [run, ...previous.filter((item) => item.id !== run.id)]);
      openInspector("runs");
      setBanner(kind === "test" ? "测试已开始，退出码会形成结构化结果" : kind === "preview" ? "预览进程已开始" : "命令已开始");
      return true;
    } catch (error) {
      setBanner(errorText(error, "命令启动失败"));
      return false;
    }
  };

  const cancelWorkspaceRun = async (run: CommandRunItem) => {
    if (!clientRef.current) return;
    try {
      const updated = await clientRef.current.cancelRun(run.id);
      setRuns((previous) => [updated, ...previous.filter((item) => item.id !== run.id)]);
    } catch (error) {
      setBanner(errorText(error, "停止命令失败"));
    }
  };

  const adoptRunEvidence = async (run: CommandRunItem, target: string) => {
    const separator = target.indexOf("|");
    if (!clientRef.current || separator < 1 || run.code == null) {
      setBanner("请选择要验收的目标标准");
      return;
    }
    const goalId = target.slice(0, separator);
    const criterionId = target.slice(separator + 1);
    const goal = goals.find((item) => item.id === goalId);
    if (!goal) return;
    try {
      const updated = await clientRef.current.recordGoalEvidence(goalId, criterionId, {
        passed: run.code === 0,
        kind: "test",
        summary: `Desktop 测试：${run.command}（退出码 ${run.code}）`,
        run_id: run.id,
      });
      setGoals((previous) => [updated, ...previous.filter((item) => item.id !== goalId)]);
      const accepted = updated.acceptance_criteria.find((item) => item.id === criterionId)?.status;
      setBanner(accepted === "pending" ? "测试证据未对应当前目标版本，请重新运行验收" : run.code === 0 ? "测试通过结果已采纳为 Goal 证据" : "测试失败结果已记录，目标进入阻塞状态");
    } catch (error) {
      setBanner(errorText(error, "采纳测试证据失败"));
    }
  };

  const sendGitCommentsToAgent = async () => {
    const client = clientRef.current;
    if (!client || openGitComments.length === 0) return;
    const { text, contexts } = gitCommentsPrompt(openGitComments);
    const sent = await sendPrompt(text, "build", contexts);
    if (sent) {
      setPendingGitComment(null);
      setGitCommentDraft("");
      try {
        const updated = await Promise.all(
          openGitComments.map((comment) => client.updateGitReviewComment(comment.id, "sent")),
        );
        const byId = new Map(updated.map((comment) => [comment.id, comment]));
        setGitComments((previous) => previous.map((comment) => byId.get(comment.id) ?? comment));
        setBanner("行级审查评论已交给 Agent，修复后可逐条确认解决");
      } catch (error) {
        setBanner(`评论已交给 Agent，但状态保存失败：${error instanceof Error ? error.message : String(error)}`);
        setGitComments(await client.listGitReviewComments().catch(() => gitComments));
      }
    }
  };

  const sendPrFeedbackToAgent = async (check?: PrDeliveryCheck) => {
    if (!prDelivery?.ok || busy || connection !== "connected") return;
    const logResult = check ? (prCheckLogs[check.id] ?? await loadPrCheckLog(check)) : null;
    const { text, contexts } = prFeedbackPrompt(prDelivery, check, logResult?.log);
    const sent = await sendPrompt(text, "build", contexts);
    if (sent) setBanner(check ? "CI 失败证据已交给 Agent 修复" : "PR Review 已交给 Agent 修复");
  };

  const openDecision = async (decision: DecisionItem) => {
    if(decision.kind === "confirmation") {
      if(decision.session_id && decision.session_id !== activeSid)await switchSession(decision.session_id);
      openInspector("info");
      return;
    }
    if (decision.kind === "hook") {
      if (decision.session_id && decision.session_id !== activeSid) {
        await switchSession(decision.session_id);
      }
      return;
    }
    if (decision.kind === "goal") {
      openInspector("goals");
      return;
    }
    if (decision.kind === "task") {
      openInspector("tasks");
      const task = tasks.find((item) => item.id === decision.target_id);
      if (decision.action === "resume_task" && task) await resumeTask(task);
      return;
    }
    if (decision.kind === "run") {
      openInspector("runs");
      return;
    }
    if (decision.kind === "pr_check") {
      openInspector("diff");
      const check = prDelivery?.failing_checks.find((item) => item.id === decision.target_id);
      if (check) await loadPrCheckLog(check);
      return;
    }
    if (decision.kind === "pr_review") openInspector("diff");
    if (decision.kind === "followup" && clientRef.current) {
      try {
        await clientRef.current.startFollowup(decision.target_id);
        setDecisions((previous) => previous.filter((item) => item.id !== decision.id));
        openInspector("tasks");
        setBanner(`已开始：${decision.title}`);
      } catch (error) {
        setBanner(errorText(error, "启动跟进建议失败"));
      }
    }
  };

  const openJournalAction = async (action: JournalNextAction) => {
    if (action.kind === "goal") {
      openInspector("goals");
      return;
    }
    if (action.kind === "run") {
      openInspector("runs");
      return;
    }
    openInspector("tasks");
    if (action.action === "resume_task") {
      const task = tasks.find((item) => item.id === action.target_id);
      if (task) await resumeTask(task);
    }
  };

  const dismissDecision = async (decision: DecisionItem) => {
    if (!clientRef.current || !decision.can_dismiss) return;
    try {
      if (decision.kind === "hook" && decision.session_id) {
        await clientRef.current.acknowledgeHookIssue(decision.session_id, decision.target_id);
        await refreshSessions();
        setBanner("Hook 失败已确认；记录仍保留在会话时间线中");
        return;
      }
      await clientRef.current.dismissDecision(decision.id);
      setDecisions((previous) => previous.filter((item) => item.id !== decision.id));
    } catch (error) {
      setBanner(errorText(error, "忽略待决策事项失败"));
    }
  };

  const { activityGroups, orphanActivities } = useMemo(() => groupActivitiesByTurn(messages, activities), [activities, messages]);

  useEffect(() => {
    if (conversationLocation.rid || !autoScrollRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const container = conversationScrollRef.current;
      if (container) container.scrollTop = container.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activities, busy, messages, pendingConfirm, streaming, conversationLocation.rid]);

  // 会话侧栏的工具：标签栏与工具列表共用。点击动作沿用原标签按钮的（含变更/上下文的刷新）。
  const planDone = plan.filter((item) => item.status === "completed").length;
  const scoped = activeScope !== "general";
  // 任务动态：后台任务台账的投影（useTasks 已随 task_update 推送实时更新）。
  const feedItems = tasks.map(taskFeedItem);
  const feedWaiting = feedItems.filter(item => item.status === "waiting").length;
  const assistantFeedItems = assistantFeed({ turns: agentTurns, dispatches, notices, taskItems: feedItems, codexActions,
    confirmation: pendingConfirm });
  const assistantWaiting = assistantFeedItems.filter(item => item.status === "waiting").length;
  const readSources = activityReads?.client === clientRef.current && activityReads?.scope === assistantScope ? activityReads.sources : {};
  const activitySources: ActivitySource[] = (["turns", "dispatches", "codexActions", "notices"] as const)
    .map(name => ({ name, ...(readSources[name] ?? { status: "loading" as const }) }));
  if (activeScope !== "general") activitySources.push({ name: "tasks", ...taskRead });
  const activityComplete = activityCoverage(activitySources, connection === "connected", Date.now()).complete;
  const refreshActivity = useCallback(async () => {
    await Promise.allSettled([refreshAgentTurns(), refreshNotices(), ...(activeScope !== "general" ? [refreshTasks()] : [])]);
  }, [refreshAgentTurns, refreshNotices, refreshTasks, activeScope]);
  const activityCodexAction = selectedCodexAction?.client === clientRef.current && selectedCodexAction?.scope === assistantScope
    ? codexActions.find(item => item.id === selectedCodexAction.id && item.status !== "cancelled") : null;
  const focusConfirmation = (id?: string) => {
    if (!pendingConfirm || (id && pendingConfirm.id !== id)) return;
    const card = document.getElementById("active-confirmation");
    card?.scrollIntoView({ block: "nearest", behavior: "smooth" }); card?.focus({ preventScroll: true });
  };

  const decideCodexAction = async (action: CodexAction, decision: "approve" | "cancel") => {
    const client = clientRef.current, scope = assistantScope;
    const current = () => clientRef.current === client && assistantScopeRef.current === scope;
    if (!client || !current()) throw new Error("请先恢复连接");
    await assistantActionGate.current.run(client, scope, `codex:${action.id}`, current, async () => {
      await client.decideCodexAction(action.id, decision);
      if (current()) await refreshAgentTurns();
    });
  };

  const openDispatch = async (id: string) => {
    setSelectedCodexAction(null);
    setSelectedDispatchId(id);
    setAssistantTab("feed"); setInspectorOpen(true);
  };

  const actOnDispatch = async (item: AssistantDispatch, action: AssistantTaskAction, answer?: string) => {
    const client = clientRef.current;
    const scope = assistantScope;
    const current = () => clientRef.current === client && assistantScopeRef.current === scope;
    if (!client || connection !== "connected") throw new Error("请先恢复连接");
    await assistantActionGate.current.run(client, scope, item.id, current, async () => {
      if (action === "refresh") { await refreshAgentTurns(item.id); return; }
      if (action === "approve" || action === "cancel") {
        // 卡片本身就是确认：目标、验收标准和外部内容提醒都摆在卡片上，点卡片上的按钮即生效，不再弹系统框
        if (!current()) return;
        try { await client.decideDispatch(item.id, action); }
        finally { if (current()) await refreshAgentTurns(); }
        return;
      }
      // 回答 / 开 PR：按钮在卡片上，人亲手点；后端记审计
      if (action === "answer" || action === "open_pr" || action === "confirm_goal") {
        if (!current()) return;
        try {
          if (action === "answer") await client.answerDispatch(item.id, answer ?? "");
          else if (action === "confirm_goal") await client.confirmDispatchGoal(item.id);
          else await client.openDispatchPr(item.id);
        } finally { if (current()) await refreshAgentTurns(); }
        return;
      }
      const host = projects.find(project => project.id === remoteProjectRef.current && isAssistantHost(project));
      if (!host) throw new Error("请连接这张任务单所属的常驻助理，再打开项目任务");
      await openAssistantTask(client, item, host.id, {
        current,
        register: async (hostId, entry) => {
          const profile = await invoke<DesktopProjectProfile>("remember_resident_project", {
            hostProjectId: hostId, port: entry.port, remoteRepoRoot: entry.repo_root, name: entry.name,
          });
          if (current()) setProjects(previous => [profile, ...previous.filter(project => project.id !== profile.id)]);
          return profile;
        },
        switchProject, getProjectClient: () => clientRef.current,
        onConnected: (_profile, id) => {
          workReturnRef.current = null;
          setAppMode("work");
          try { localStorage.setItem(STORAGE_KEYS.appMode, "work"); } catch { /* The current navigation still works. */ }
          focusTask(id);
        },
        onReview: openTaskBranchReview,
        onError: error => setBanner(errorText(error, "读取项目任务失败，请核对状态")),
      });
    });
  };
  const workbenchTools: WorkbenchTool[] = ([
    { id: "feed", label: "任务动态", icon: <Inbox size={15} />, badge: feedWaiting ? String(feedWaiting) : undefined,
      open: () => { openInspector("feed"); void refreshTasks(); } },
    { id: "info", label: "任务步骤", icon: <ListChecks size={15} />, badge: plan.length ? `${planDone}/${plan.length}` : undefined,
      live: busy, open: () => openInspector("info") },
    { id: "preview", label: "预览", icon: <Eye size={15} />, badge: String(artifacts.length + previewReferenceItems.length),
      open: () => openInspector("preview") },
    scoped && { id: "files", label: "代码", icon: <FileCode2 size={15} />, badge: String(filteredWorkspaceFiles.length),
      open: () => openInspector("files") },
    scoped && { id: "diff", label: "变更", icon: <GitCompareArrows size={15} />, badge: String((gitReview?.files.length ?? 0) + isolatedDeliveries.length),
      open: () => { openInspector("diff"); void closeTaskBranchReview(); void refreshPrDelivery(); void refreshIsolatedDeliveries(); } },
    scoped && { id: "runs", label: "控制台", icon: <SquareTerminal size={15} />, badge: String(activeRuns.length),
      open: () => openInspector("runs") },
    (inspectorTab === "project" || activeScope === "general" || artifacts.length > 0 || (repoMemory?.total_entries ?? 0) > 0)
      && { id: "project", label: "上下文", icon: <Library size={15} />, badge: String(artifacts.length + (repoMemory?.total_entries ?? 0)),
        open: () => { openInspector("project"); void refreshProjectAssets(activeScope !== "general"); } },
  ] as (WorkbenchTool | false)[])
    .filter((tool): tool is WorkbenchTool => Boolean(tool))
    .map((tool) => ({ ...tool, badge: tool.badge === "0" ? undefined : tool.badge, attention: tabsNeedingAttention.has(tool.id) }));
  const shownWorkbenchTabs = (workbenchTabs.includes(inspectorTab) || isManagementTab(inspectorTab) ? workbenchTabs : [...workbenchTabs, inspectorTab])
    .filter((tab) => workbenchTools.some((tool) => tool.id === tab));
  const showWorkbenchLauncher = workbenchLauncher || shownWorkbenchTabs.length === 0;
  const closeWorkbenchTool = (tab: InspectorTab) => {
    const { tabs, next } = closeWorkbenchTab(shownWorkbenchTabs, tab, inspectorTab);
    setWorkbenchTabs(tabs);
    if (workbenchLauncher) return;
    if (next === null) setWorkbenchLauncher(true);
    else if (next !== inspectorTab) openInspector(next);
  };

  // 启动时若上次停在助理模式、却被「回到上次项目」带进了项目工作区：只纠正一次，回到助理所在电脑的主要聊天。
  const assistantStartupCheckedRef = useRef(false);
  useEffect(() => {
    if (assistantStartupCheckedRef.current || connection !== "connected") return;
    assistantStartupCheckedRef.current = true;
    if (appMode === "assistant") {
      // 登记表可能还没读完：现读一次，再决定去常驻电脑还是留在本机通用工作区。
      void refreshProjectRegistry().catch(() => projects).then((known) => {
        const host = findAssistantHost(known);
        const placed = host ? onAssistantHost() && remoteProjectRef.current === host.id : !remoteProjectRef.current && activeScope === "general";
        if (!placed) void changeAppMode("assistant", known);
        else setInspectorOpen(true);
      });
    }
  // 只在首次连上时检查一次；changeAppMode 每次渲染都是新函数，故不列入依赖。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection]);

  useEffect(() => {
    if (appMode !== "assistant" || connection !== "connected") return;
    void refreshActivity();
    const later = window.setTimeout(() => void refreshActivity(), 8000);
    const stop = startPolling(refreshActivity, { intervalMs: 30_000, hiddenIntervalMs: 120_000, immediate: false, visibility: document });
    return () => { window.clearTimeout(later); stop(); };
  }, [appMode, connection, busy, activeSid, refreshActivity]);

  // 助理面板的操作：每次渲染按此刻的 scope 现装（点下去绑定的就是这一刻的会话和电脑）
  const panelActions = assistantPanelActions({
    client: () => clientRef.current,
    scope: () => assistantScopeRef.current,
    renderScope: assistantScope,
    scheduleEpoch: scheduleReadEpoch,
    connectorReads: connectorReads.current,
    actionGate: assistantActionGate.current,
    refreshConnectorStatus,
    refreshAudit,
    confirm: confirmAction,
    openUrl,
    copy: (text) => navigator.clipboard.writeText(text),
    prepareAvatar,
    setBanner,
    setSchedule: setAssistantSchedule,
    setMemories: setAssistantMemories,
    setProposals: setMemoryProposals,
    setAvatar: setAssistantAvatar,
  });

  return (
    <main className="app-shell">
      <header className="topbar" data-tauri-drag-region="deep">
        <div className="topbar-spacer" />
        <BuildVersion />
        {/* 平时不摆状态字（MiMo 式干净顶栏）；只有没连上时才露出连接状态。 */}
        {connection !== "connected" && (
          <div className={`connection-pill ${connection}`} title={`${runtime.workdir || repoRoot || "通用会话"} · 协议 ${protocolVersion ?? "—"}`}>
            <span className="connection-dot" />
            {connectionText}
          </div>
        )}
      </header>

      <div className="banner-slot">
        {banner && (
          <div className="banner">
            <span>{banner}</span>
            <button aria-label="关闭提示" onClick={() => setBanner(null)}><X size={15} /></button>
          </div>
        )}
      </div>

      <div className={`workbench ${computerWorkspace.view ? "computer-open" : ""} ${mainView === "chat" && inspectorOpen ? "inspector-open" : "inspector-closed"} ${mainView === "chat" && inspectorOpen && appMode !== "assistant" && !showWorkbenchLauncher && ["files", "diff", "runs", "project"].includes(inspectorTab) ? "inspector-wide" : ""}`}>
        <aside className="sidebar">
          <SidebarBrand query={sidebarQuery} onQuery={setSidebarQuery} />
          <ModeSwitch mode={appMode} resident={Boolean(findAssistantHost(projects))} onChange={(mode) => void changeAppMode(mode)} />
          {appMode === "assistant" ? (
            <AssistantSidebar
              mainChat={{ sid: mainChatSid, preview: agentTurns.find((turn) => turn.sid === mainChatSid)?.outcome, busy: busy && activeSid === mainChatSid }}
              sideChats={sessions.filter((session) => session.sid !== mainChatSid && !isPhoneSession(session.sid) && (session.messages > 0 || session.sid === activeSid))
                .map((session) => ({ sid: session.sid, title: session.title, busy: session.status === "working" }))}
              current={activeSid}
              onSelect={(sid) => { setMainView("chat"); void (sid === mainChatSid ? openMainChat() : switchSession(sid)); }}
              phoneChats={phoneChats.map((chat) => ({ sid: chat.sid, title: chat.title }))}
              onOpenPhoneChat={openPhoneChat}
              offlineChats={onResidentNow ? offlineChats.map((chat) => ({ sid: chat.sid, title: chat.title })) : []}
              onOpenOfflineChat={setOfflineChatSid}
              onNewSideChat={() => void newSideChat()}
            />
          ) : <>
          <WorkNav view={mainView} connected={connection === "connected"}
            onNewChat={() => { setMainView("chat"); void newSession(); }}
            onWork={() => { openInspector("inbox"); void refreshRuntimeInboxes(); }}
            onArtifacts={() => { setMainView("artifacts"); void refreshProjectAssets(activeScope !== "general"); }} />
          <SidebarSectionTitle label="项目" className="project-section-title sidebar-first-section"
            open={projectsOpen} onToggle={() => setProjectsOpen(!projectsOpen)}>
            <button aria-label="连接远端工作区" title="连接远端工作区（另一台电脑上的项目）" onClick={() => setRemoteDialogOpen("project")} disabled={projectSwitching}><Server size={15} /></button>
            <button aria-label="添加 Git 项目" title="添加 Git 项目" onClick={() => void chooseRepo()} disabled={projectSwitching}><FolderPlus size={15} /></button>
          </SidebarSectionTitle>
          {projectsOpen && (
            <SidebarProjects
              activeScope={activeScope}
              currentRuntimeId={processStatus.runtimeId}
              runtimeProcesses={runtimeProcesses}
              runtimeRecoveries={runtimeRecoveries}
              projects={projects}
              residentHost={residentHost}
              query={sidebarQuery}
              projectSessions={projectSessions}
              sessions={sessions}
              residentSessions={residentSessions}
              remoteProjectId={remoteProjectRef.current}
              repoRoot={repoRoot}
              connection={connection}
              connectionText={connectionText}
              activeSid={activeSid}
              onSwitchScratch={(item) => void switchManagedScope("scratch", item)}
              onSwitchProject={(project, options) => void switchProject(project, options)}
              onSwitchSession={(sid) => void switchSession(sid)}
              onForgetProject={(project) => void forgetProject(project)}
              onBanner={setBanner}
            />
          )}

          <SidebarSectionTitle label="最近" className="work-items-heading" open={recentOpen} onToggle={() => setRecentOpen(!recentOpen)}>
            <button aria-label="新建任务" title="新建任务" onClick={() => void newSession()} disabled={connection !== "connected"}><Plus size={15} /></button>
          </SidebarSectionTitle>
          {recentOpen && (
            <SidebarSessions
              sessions={sessions}
              activeSid={activeSid}
              busy={busy}
              query={sidebarQuery}
              decisions={decisionItems}
              goals={activeGoals}
              tasks={activeTasks}
              onSelect={(sid) => void switchSession(sid)}
              onFocusTask={focusTask}
              onOpenHooks={(sid) => { openInspector("decisions"); void switchSession(sid); }}
              onRename={(session) => void renameSession(session)}
              onDelete={(session) => void deleteSession(session)}
              onOpenDecision={(decision) => void openDecision(decision)}
              onOpenGoals={() => openInspector("goals")}
              onOpenTasks={() => openInspector("tasks")}
            />
          )}
          </>}
          <SidebarFooter
            identity={identity}
            update={availableUpdate}
            onOpenAccount={() => { setSettingsSection("account"); setSettingsOpen(true); }}
            onOpenSettings={() => setSettingsOpen(true)}
          />
        </aside>

        {mainView === "plugins" ? (
          <PluginsPage
            activeScope={activeScope}
            mcpServers={<McpServersCard key={`mcp-${pluginsRevision}`} client={clientRef.current} connection={connection} />}
            projectSkills={<ProjectSkillsCard key={`skills-${pluginsRevision}`} client={clientRef.current} connection={connection} />}
            catalog={<CatalogCard client={clientRef.current} connection={connection} activeScope={activeScope} onInstalled={() => setPluginsRevision(value => value + 1)} />}
            mcpConnection={<McpConnectionCard client={clientRef.current} session={activeSid} connection={connection} />}
            personalSkills={<PersonalSkillsCard client={clientRef.current} connection={connection} />}
            projectName={repoRoot.split("/").filter(Boolean).slice(-1)[0] || ""}
            inspector={(
              <ExtensionsInspector
                extensionsInspect={extensionsInspect}
                extensionsInspectBusy={extensionsInspectBusy}
                hookStatus={hookStatus}
                hookTrustBusy={hookTrustBusy}
                connection={connection}
                onRefresh={() => void Promise.all([refreshExtensionsInspect(), refreshHookStatus()])}
                onToggleHookTrust={toggleHookTrust}
              />
            )}
            onChooseProject={() => void chooseRepo()}
            onOpenBrowserSettings={() => { setSettingsSection("permissions"); setSettingsOpen(true); }}
          />
        ) : mainView === "management" ? null : mainView === "artifacts" ? (
          <ArtifactCenter
            artifacts={artifacts}
            loading={projectAssetsLoading}
            error={projectAssetsError}
            selectedId={selectedArtifactId}
            onRefresh={() => void refreshProjectAssets(activeScope !== "general")}
            onOpen={(id) => {
              setSelectedArtifactId(id);
              void loadArtifactPreview(id);
              setPreviewView("artifacts");
              openInspector("preview");
            }}
          />
        ) : (
        <section className={`conversation ${messages.length === 0 && !streaming ? "empty-state" : ""}`}>
          <ConversationHeader
            title={appMode === "assistant" && activeSid === mainChatSid ? "主要聊天" : currentSession?.title || "新对话"}
            place={appMode !== "assistant" && residentHost && currentWorkProject() ? (remoteProjectRef.current ? "resident" : "local") : null}
            panelOpen={inspectorOpen} waiting={feedWaiting} plan={{ done: planDone, total: plan.length }} busy={busy}
            onTogglePanel={() => inspectorOpen ? setInspectorOpen(false)
              : appMode === "assistant" || (workbenchTabs.length > 0 && !isManagementTab(inspectorTab)) ? setInspectorOpen(true) : openInspector("feed")}
            onStop={() => void cancelTurn()} />

          <div className="conversation-body">
            <ConversationLocation status={conversationLocation.status} onLatest={conversationLocation.latest} onRetry={conversationLocation.retry} />
          <div
            className="conversation-scroll"
            ref={conversationScrollRef}
            tabIndex={-1}
            onScroll={(event) => {
              const node = event.currentTarget;
              autoScrollRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96;
            }}
          >
            {messages.length === 0 && !streaming && (
              <ConversationHome starting={homeStarting} showStarting={showHomeStarting} assistant={appMode === "assistant"}
                resident={onAssistantHost()}
                tips={showStarterTips && llmProfile?.configured && <AssistantStarterTips
                  tips={starterTips({ resident: onAssistantHost(), projects: residentProjects.map((item) => item.name) })}
                  onDismiss={() => { dismissStarterTips(); setShowStarterTips(false); }}
                  onPick={(tip) => {
                    setPrompt(tip.prompt);
                    window.requestAnimationFrame(() => {
                      const input = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="任务输入"]');
                      input?.focus();
                      input?.setSelectionRange(tip.prompt.length, tip.prompt.length);
                    });
                  }} />}>
                {llmProfileChecked && !llmProfile?.configured && !onAssistantHost() && (
                  <FirstRunCard signedIn={Boolean(vortoAccount.status?.signedIn)}
                    onAccount={() => { setSettingsSection("account"); setSettingsOpen(true); }}
                    onModel={() => { setSettingsSection("model"); setSettingsOpen(true); }} />
                )}
              </ConversationHome>
            )}

            {plan.length > 0 && (
              <button className="plan-summary" onClick={()=>openInspector("info")}>
                执行编排 · {plan.filter(item=>item.status === "completed").length}/{plan.length} 步 · 查看进展
              </button>
            )}

            <ChatHistory messages={messages} activityGroups={activityGroups} busy={busy} activeTurnRid={activeTurnRid}
              assistant={appMode === "assistant"} name={assistantProfile.name || "VortoCode"} avatar={assistantAvatar} focusedRid={conversationLocation.rid} />

            {appMode === "assistant" && dispatches.filter(item => item.session?.replace(/^sid-/, "") === activeSid
              && item.status !== "cancelled").slice(0, 8).map(item => <AssistantTaskCard
                key={`${assistantScope}:${item.id}`} task={item} connected={connection === "connected"}
                onAction={(action, answer) => actOnDispatch(item, action, answer)} />)}

            {/* 定时监管拟的动作不属于哪段对话：还在等你批时，出现在当前助理对话里 */}
            {appMode === "assistant" && codexActions.filter(action => (action.session
              ? action.session.replace(/^sid-/, "") === activeSid
              : action.status === "proposed") && action.status !== "cancelled").slice(0, 6).map(action => <CodexActionCard
                key={`${assistantScope}:${action.id}`} action={action} connected={connection === "connected"}
                onDecide={decision => decideCodexAction(action, decision)} />)}

            {orphanActivities.length > 0 && <TurnTimeline items={orphanActivities} active={busy && !activeTurnRid} />}

            {streaming && (
              <article className="message assistant streaming">
                {appMode === "assistant"
                  ? <div className="message-author with-avatar"><AssistantAvatarImage name={assistantProfile.name || "VortoCode"} avatar={assistantAvatar} size={22} />{assistantProfile.name || "VortoCode"} <span>正在回复</span></div>
                  : <div className="message-author">VortoCode <span>正在回复</span></div>}
                <div className="message-body"><MarkdownMessage text={streaming} /><i className="cursor" data-rid={streamingRid} /></div>
              </article>
            )}

          </div>

          </div>
          <div className="composer-wrap">
            {residentMissed && (
              <div className="composer-notice" role="status">
                常驻电脑没连上，现在是这台电脑上的本机助理：派活、简报和合上笔记本后继续做，都要连上常驻电脑。
                {residentFailure && <> 原因：{residentFailure}</>}
                <button onClick={() => void changeAppMode("assistant")} disabled={projectSwitching}>重新连接</button>
              </div>
            )}
            {llmProfileChecked && !llmProfile?.configured && !onAssistantHost() && messages.length > 0 && (
              <div className="composer-notice" role="status">
                还没有连接模型，消息不会有回复。
                <button onClick={() => { setSettingsSection("account"); setSettingsOpen(true); }}>{vortoAccount.status?.signedIn ? "使用会员模型" : "登录 VortoCode 账号"}</button>
                <button onClick={() => { setSettingsSection("model"); setSettingsOpen(true); }}>用自己的模型</button>
              </div>
            )}
            {pendingConfirm && (
              <ConfirmCard confirm={pendingConfirm} trust={trust} trustBusy={trustBusy} connected={connection === "connected"}
                onTrust={changeTrustLevel} onCheck={() => void checkConfirmation()} onAnswer={(ok) => void answerConfirmation(ok)} />
            )}

            {workspaceRequest && (
              <WorkspaceRequestCard request={workspaceRequest} onDecline={() => setWorkspaceRequest(null)}
                onAccept={() => void acceptWorkspaceRequest()} />
            )}

            {promptQueue.length > 0 && (
              <PromptQueuePanel items={promptQueue} paused={promptQueuePaused}
                onRunNow={(id) => void sendQueuedPromptNow(id)} onRemove={(id) => void removeQueuedPrompt(id)} />
            )}
              {appMode !== "assistant" && <ProjectPicker
                projects={listedProjects(projects, residentHost)}
                activeProjectName={activeScope === "project" ? (currentWorkProject()?.name || repoRoot.split("/").filter(Boolean).slice(-1)[0] || "项目") : null}
                disabled={projectSwitching || runtimeStarting || busy || !!pendingConfirm}
                onPick={(project) => void switchProject(project)}
                onAddProject={() => void chooseRepo()}
                onAddRemote={() => setRemoteDialogOpen("project")}
                onLeaveProject={() => void switchManagedScope("general")}
                place={(() => {
                  const current = currentWorkProject();
                  if (!current || !residentHost) return undefined;
                  const localAvailable = current.kind !== "remote" || Boolean(localTwin(current, projects));
                  const residentAvailable = Boolean(residentTwin(current, projects, residentHost) || residentEntry(current, residentProjects));
                  if (!localAvailable || !residentAvailable) return undefined;
                  return { current: current.kind === "remote" ? "resident" as const : "local" as const, localAvailable, residentAvailable,
                    onChange: (target: WorkPlace) => void changeWorkPlace(target) };
                })()}
              />}
            <div
              className={`composer ${runtimeStarting || projectSwitching ? "preparing" : ""} ${dragOver ? "drag-over" : ""}`}
              onDragOver={(event) => {
                if (!event.dataTransfer.types.includes("Files")) return;
                event.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragOver(false);
              }}
              onDrop={(event) => {
                if (!event.dataTransfer.files.length) return;
                event.preventDefault();
                setDragOver(false);
                void addFiles(event.dataTransfer.files);
              }}
            >
              <AttachmentChips items={attachments} onRemove={removeAttachment} />
              <ContextChips items={contextItems}
                onRemove={(item) => setContextItems((previous) => previous.filter((candidate) => contextItemKey(candidate) !== contextItemKey(item)))} />
              <ComposerCommands key={activeSid} prompt={prompt} onPrompt={setPrompt} project={activeScope !== "general"}
                disabled={runtimeStarting || projectSwitching} onSend={() => void sendPrompt()}
                onPaste={(event) => { const files = Array.from(event.clipboardData.files); if (files.length) { event.preventDefault(); void addFiles(files); } }}
                onAction={(command) => {
                  if (command === "attach") attachmentInputRef.current?.click();
                  else if (command === "project") void chooseRepo();
                  else if (command === "files") openInspector("files");
                  else if (command === "info") openInspector("info");
                  else if (command === "model") { setSettingsSection("model"); setSettingsOpen(true); }
                  else if (command === "new" && connection === "connected") void newSession();
                }}>
              <div className="composer-footer">
                <div className="composer-tools">
                  <input
                    ref={attachmentInputRef}
                    type="file"
                    multiple
                    hidden
                    onChange={(event) => {
                      if (event.target.files?.length) void addFiles(event.target.files);
                      event.target.value = "";
                    }}
                  />
                  <TrustChip trust={trust} busy={trustBusy} onChange={(level) => void changeTrustLevel(level)} />
                  {(busy || contextItems.length > 0) && (
                    <span>{contextItems.length > 0 ? `${contextItems.length} 个源码引用` : "Enter 加入队列"}</span>
                  )}
                </div>
                <div className="composer-actions">
                <ComposerModelPicker residentModel={runtime.model} onResident={onAssistantHost()} checked={llmProfileChecked}
                  choices={composerModelChoices} value={modelChoice} profile={llmProfile}
                  onChoose={(value) => { setSavedModelChoice(value); persistModelChoice(value); }}
                  onManage={() => { setSettingsSection("model"); setSettingsOpen(true); }} />
                <ContextStatus key={activeSid} context={currentSession?.context} />
                <button className={`composer-send ${busy ? "queueing" : ""}`} aria-label={busy ? "加入待运行队列" : "发送"} title={busy ? "加入待运行队列" : "发送"} onClick={() => void sendPrompt()} disabled={(!prompt.trim() && contextItems.length === 0 && attachments.length === 0) || savingFile || runtimeStarting || projectSwitching}><ArrowUp size={17} /></button>
                </div>
              </div>
              </ComposerCommands>
            </div>

          </div>
        </section>
        )}

        {mainView === "chat" && inspectorOpen && appMode === "assistant" && (
          computerWorkspace.view ? <ComputerWorkspacePanel view={computerWorkspace.view} onClose={computerWorkspace.close} onRetry={() => void computerWorkspace.open()} />
          : <AssistantPanel name={assistantProfile.name || "VortoCode"} avatar={assistantAvatar} connected={connection === "connected"}
            computer={<ComputerLauncher onOpen={() => void computerWorkspace.open()} onSettings={() => { setSettingsSection("computer"); setSettingsOpen(true); }} />}
            onEditProfile={() => setProfileDialogOpen(true)}
            host={onAssistantHost() ? { kind: "resident", name: findAssistantHost(projects)?.name ?? "常驻电脑", reachable: connection === "connected" } : { kind: "local", canAddResident: !findAssistantHost(projects) }}
            onAddResident={() => setRemoteDialogOpen("assistant")}
            onRetryResident={() => void changeAppMode("assistant")}
            modelLabel={onAssistantHost() ? runtime.model : llmProfile?.configured ? composerModelChoices.find(item => item.value === modelChoice)?.label : "未连接模型"}
            tab={assistantTab} onClose={() => setInspectorOpen(false)} waiting={assistantWaiting} waitingUncertain={!activityComplete}
            onTab={(tab) => { if (tab === "goals") setAssistantGoalFocus(null); setAssistantTab(tab); panelActions.tabOpened(tab); }}>
            {assistantTab === "audit" ? <AssistantAudit entries={auditEntries} />
              : assistantTab === "goals" ? <AssistantGoals key={assistantScope} client={clientRef.current} connected={connection === "connected"}
                  focusId={assistantGoalFocus?.client === clientRef.current && assistantGoalFocus?.scope === assistantScope ? assistantGoalFocus.id : undefined}
                  onFocusGoal={(id, owner) => {
                    if (clientRef.current === owner && assistantScopeRef.current === assistantScope) {
                      setAssistantGoalFocus({ client: clientRef.current, scope: assistantScope, id });
                    }
                  }}
                  onClearFocus={() => setAssistantGoalFocus(null)} />
              : assistantTab === "schedule" ? <AssistantSchedule key={assistantScope} items={assistantSchedule} connected={connection === "connected"} local={!onAssistantHost()}
                  onRefresh={panelActions.scheduleRefresh}
                  onToggle={panelActions.scheduleToggle}
                  onHistory={panelActions.scheduleHistory} />
              : assistantTab === "identity" ? <AssistantIdentity profile={assistantProfile} memories={assistantMemories}
                  avatar={assistantAvatar}
                  onAvatar={(file) => void panelActions.avatar(file)}
                  proposals={memoryProposals}
                  connectors={connectors}
                  plugins={<AssistantPlugins plugins={plugins} busy={pluginBusy}
                    checks={pluginChecks}
                    onCheck={(plugin) => pluginAction("check", plugin)}
                    onRemove={(plugin) => pluginAction("remove", plugin)}
                    onSave={(plugin, credential) => savePlugin(plugin, credential)} />}
                  onConnector={(action, connector) => void panelActions.connector(action, connector)}
                  onDecideProposal={(proposal, approve, replace) => void panelActions.decideProposal(proposal, approve, replace)}
                  onEdit={() => setProfileDialogOpen(true)}
                  onDeleteMemory={(memory) => void panelActions.deleteMemory(memory)} />
              : <AssistantActivity key={assistantScope} items={assistantFeedItems}
                  client={clientRef.current}
                  currentSession={activeSid} connected={connection === "connected"} sources={activitySources} onRefresh={() => void refreshActivity()}
                  onOpen={(item) => {
                    if (item.confirmationId) { focusConfirmation(item.confirmationId); return; }
                    if (item.codexActionId) {
                      setSelectedDispatchId(null);
                      setSelectedCodexAction({ client: clientRef.current, scope: assistantScope, id: item.codexActionId }); return;
                    }
                    if (item.dispatchId) { void openDispatch(item.dispatchId); return; }
                    if (item.taskId) { focusTask(item.taskId); return; }
                    const sid = item.session?.replace(/^sid-/, "");
                    if (sid && isPhoneSession(sid)) { openPhoneChat(sid, item.rid); return; }
                    if (sid) {
                      setMainView("chat");
                      void switchSession(sid).then(opened => {
                        const client = clientRef.current;
                        if (opened && client && item.rid && mainViewRef.current === "chat") conversationLocation.locate({ client, sid, rid: item.rid });
                      });
                    }
                  }} overview={<AssistantWorkday client={clientRef.current} scope={assistantScope}
                  connected={connection === "connected"} resident={onAssistantHost()} dispatches={dispatches}
                  dispatchesLoaded={dispatchSnapshotScope === assistantScope} codexActions={codexActions}
                  confirmation={pendingConfirm} onConfirmation={() => focusConfirmation()}
                  onDispatch={id => void openDispatch(id)} onGoals={id => {
                    setAssistantGoalFocus({ client: clientRef.current, scope: assistantScope, id }); setAssistantTab("goals");
                  }}
                  onRefreshTasks={() => refreshAgentTurns()} onCodexDecision={decideCodexAction} />}>
                <section className="assistant-task-cards" aria-label="交互任务单">
                  {activityCodexAction && <AssistantActivityDetail key={`${assistantScope}:codex:${activityCodexAction.id}`}
                    title={`Codex 动作详情：${activityCodexAction.thread_name}`} statusLabel="动作记录"
                    steps={[{id:"request",title:"请求正文",status:"recorded",detail:activityCodexAction.text}]} onClose={() => setSelectedCodexAction(null)}>
                    <CodexActionCard action={activityCodexAction} connected={connection === "connected"}
                      onDecide={decision => decideCodexAction(activityCodexAction, decision)} />
                  </AssistantActivityDetail>}
                  {selectedDispatchId && dispatches.find(item => item.id === selectedDispatchId) && <AssistantActivityDetail
                    key={`${assistantScope}:dispatch:${selectedDispatchId}`} title={dispatches.find(item => item.id === selectedDispatchId)!.title}
                    steps={dispatchDetailSteps(dispatches.find(item => item.id === selectedDispatchId)!)} statusLabel="任务单记录" onClose={() => setSelectedDispatchId(null)}><AssistantTaskCard
                    key={`${assistantScope}:${selectedDispatchId}`} task={dispatches.find(item => item.id === selectedDispatchId)!}
                    connected={connection === "connected"} onAction={(action, answer) => actOnDispatch(dispatches.find(item => item.id === selectedDispatchId)!, action, answer)} /></AssistantActivityDetail>}
                </section></AssistantActivity>}
          </AssistantPanel>
        )}
        {(mainView === "management" || (mainView === "chat" && inspectorOpen && appMode !== "assistant")) && (
        <aside className={mainView === "management" ? "inspector management-page" : "inspector"}>
          {mainView === "management" ? <>
            <div className="inspector-context-head">
              <div><span>任务中心</span><strong>{inspectorTabLabel(inspectorTab)}</strong></div>
              <button aria-label="返回对话" onClick={() => {setMainView("chat");setInspectorOpen(false);}}><X size={16} /></button>
            </div>
            <ManagementNav current={inspectorTab} onSelect={openInspector}/>
          </> : <WorkbenchTabs tools={workbenchTools} tabs={shownWorkbenchTabs} current={inspectorTab} launcher={showWorkbenchLauncher}
            onShowLauncher={() => setWorkbenchLauncher(true)} onCloseTab={closeWorkbenchTool} onClosePanel={() => setInspectorOpen(false)} />}
          {mainView === "chat" && showWorkbenchLauncher && <WorkbenchLauncher tools={workbenchTools} />}
          <div className="inspector-body" hidden={mainView === "chat" && showWorkbenchLauncher}>
            {inspectorTab === "feed" && <ActivityFeed items={feedItems} scope={feedScope} onScope={setFeedScope}
              currentSession={activeSid} onOpen={(item) => { if (item.taskId) focusTask(item.taskId); }} />}
            {inspectorTab === "info" && <SessionInfo key={activeSid} context={currentSession?.context} busy={busy} needsInput={Boolean(pendingConfirm)}
              connected={connection === "connected"} plan={plan} queued={promptQueue.length}
              activities={activities.filter(item => item.rid === (activeTurnRid ?? [...activities].reverse().find(candidate => candidate.rid)?.rid))}
              model={onAssistantHost() ? runtime.model ?? "" : composerModelChoices.find(item => item.value === modelChoice)?.label ?? ""}
              onRefresh={() => void refreshSessions()} onOpenSettings={() => { setSettingsSection("advanced"); setSettingsOpen(true); }} />}

            {inspectorTab === "inbox" && (
              <RuntimeInboxPanel
                runtimeInboxes={runtimeInboxes}
                summary={runtimeInboxSummary}
                currentRuntimeId={processStatus.runtimeId}
                onRefresh={() => void refreshRuntimeInboxes()}
                onActivate={(runtimeInbox) => void openRuntimeInboxPanel(runtimeInbox,"inbox")}
                onOpenSession={(runtimeInbox, sid, needsInput) => void openRuntimeInboxSession(runtimeInbox, sid, needsInput)}
                onOpenPanel={(runtimeInbox, tab, sid, taskId, goalId) => void openRuntimeInboxPanel(runtimeInbox, tab, sid, taskId, goalId)}
              />
            )}
            {inspectorTab === "files" && (
              <FilesPanel
                selectedFile={selectedFile}
                filePreview={filePreview}
                editorMode={editorMode}
                editorContent={editorContent}
                editorEol={editorEol}
                editorDirty={editorDirty}
                savingFile={savingFile}
                sourceSelection={sourceSelection}
                workspaceLoading={workspaceLoading}
                workspaceError={workspaceError}
                fileQuery={fileQuery}
                filteredWorkspaceFiles={filteredWorkspaceFiles}
                totalFileCount={workspaceFiles.length}
                workspaceTruncated={workspaceTruncated}
                contextItems={contextItems}
                previewContextItem={previewContextItem}
                previewContextAttached={previewContextAttached}
                connection={connection}
                busy={busy}
                activeScope={activeScope}
                workspaceMatchesRuntime={workspaceMatchesRuntime}
                canRefresh={Boolean(repoRoot.trim() || runtime.workdir)}
                onOpenFile={openWorkspaceFile}
                onCloseFile={closeWorkspaceFile}
                onToggleEditorMode={() => setEditorMode((current) => !current)}
                onEditorContentChange={setEditorContent}
                onDiscard={discardEditorChanges}
                onSave={saveWorkspaceFile}
                onLaunchExternal={launchExternalEditor}
                onAddContext={addFileToContext}
                onSelectSourceLine={selectSourceLine}
                onClearSourceSelection={() => setSourceSelection(null)}
                onFileQueryChange={setFileQuery}
                onRefresh={() => void refreshWorkspaceFiles(repoRoot.trim() || runtime.workdir || "")}
              />
            )}
            {inspectorTab === "diff" && (
              <GitReviewPanel
                connection={connection}
                busy={busy}
                diffPayload={diffPayload}
                isolatedDeliveries={isolatedDeliveries}
                onPullToLocal={(() => {
                  const current = currentWorkProject();
                  const local = current?.kind === "remote" ? localTwin(current, projects) : undefined;
                  if (!current || !local) return undefined;
                  return (branch: string) => void invoke<string>("fetch_resident_branch", {
                    residentProjectId: current.id, localProjectId: local.id, branch,
                  }).then((message) => setBanner(`${message}。切到「本机」后可在变更面板审查、开 PR。`))
                    .catch((error) => setBanner(errorText(error, "拉回本机失败")));
                })()}
                isolatedDelivery={isolatedDelivery}
                isolatedDeliveryDiff={isolatedDeliveryDiff}
                isolatedDeliveryPath={isolatedDeliveryPath}
                isolatedDeliveryError={isolatedDeliveryError}
                isolatedDeliveryLoading={isolatedDeliveryLoading}
                onRefreshIsolatedDeliveries={refreshIsolatedDeliveries}
                onOpenIsolatedDelivery={openIsolatedDelivery}
                onLoadIsolatedDeliveryDiff={loadIsolatedDeliveryDiff}
                gitReview={gitReview}
                gitReviewDiff={gitReviewDiff}
                gitReviewScope={gitReviewScope}
                gitReviewLoading={gitReviewLoading}
                gitReviewError={gitReviewError}
                gitReviewRevision={gitReviewRevision}
                gitSelectedPath={gitSelectedPath}
                gitActionBusy={gitActionBusy}
                gitComments={gitComments}
                openGitComments={openGitComments}
                sentGitComments={sentGitComments}
                pendingGitComment={pendingGitComment}
                gitCommentDraft={gitCommentDraft}
                gitCommentSaving={gitCommentSaving}
                gitCommitMessage={gitCommitMessage}
                gitPrTitle={gitPrTitle}
                gitPrBase={gitPrBase}
                gitDeliveryBusy={gitDeliveryBusy}
                prDelivery={prDelivery}
                prDeliveryLoading={prDeliveryLoading}
                prCheckLogs={prCheckLogs}
                taskReviewTask={taskReviewTask}
                taskBranchReview={taskBranchReview}
                taskBranchDiff={taskBranchDiff}
                taskBranchSelectedPath={taskBranchSelectedPath}
                taskReviewVerifying={taskReviewVerifying}
                onCloseTaskBranchReview={closeTaskBranchReview}
                onOpenTaskBranchReview={openTaskBranchReview}
                onRefreshGitReview={refreshGitReview}
                onLoadTaskBranchDiff={loadTaskBranchReviewDiff}
                onOpenGitReviewFile={openGitReviewFile}
                onApplyGitAction={applyGitAction}
                onApplyTaskBranchAction={applyTaskBranchAction}
                onVerifyReviewedBranch={() => void verifyReviewedTaskBranch()}
                onStartGitComment={startGitComment}
                onCommentDraftChange={setGitCommentDraft}
                onCancelComment={() => { setPendingGitComment(null); setGitCommentDraft(""); }}
                onAddGitComment={addGitComment}
                onSendGitComments={sendGitCommentsToAgent}
                onUpdateCommentStatus={updateGitCommentStatus}
                onDeleteGitComment={deleteGitComment}
                onRefreshPrDelivery={refreshPrDelivery}
                onSendPrFeedback={sendPrFeedbackToAgent}
                onLoadPrCheckLog={loadPrCheckLog}
                onCommitMessageChange={setGitCommitMessage}
                onCommitGitReview={commitGitReview}
                onPrTitleChange={setGitPrTitle}
                onPrBaseChange={setGitPrBase}
                onOpenGitReviewPr={openGitReviewPr}
              />
            )}
            {inspectorTab === "runs" && (
              <RunsPanel
                runs={runs}
                goals={goals}
                connection={connection}
                client={clientRef.current}
                onNotice={setBanner}
                startWorkspaceRun={startWorkspaceRun}
                cancelWorkspaceRun={cancelWorkspaceRun}
                adoptRunEvidence={adoptRunEvidence}
              />
            )}
            {inspectorTab === "goals" && (
              <GoalsPanel
                goalFocus={goalFocus}
                onRetryFocus={() => { if (goalFocus) void focusGoal(goalFocus.id); }}
                onClearFocus={() => { resetGoalNavigation(); void refreshGoals(); }}
                goals={goals}
                tasks={tasks}
                runs={runs}
                connection={connection}
                editingGoalId={editingGoalId}
                goalObjective={goalObjective}
                goalCriteria={goalCriteria}
                goalConstraints={goalConstraints}
                goalNonGoals={goalNonGoals}
                goalSubmitting={goalSubmitting}
                goalEvidenceDrafts={goalEvidenceDrafts}
                goalVerifierDrafts={goalVerifierDrafts}
                onObjectiveChange={setGoalObjective}
                onCriteriaChange={setGoalCriteria}
                onConstraintsChange={setGoalConstraints}
                onNonGoalsChange={setGoalNonGoals}
                onEvidenceDraftsChange={setGoalEvidenceDrafts}
                onVerifierDraftsChange={setGoalVerifierDrafts}
                onResetForm={resetGoalForm}
                onSaveDraft={saveGoalDraft}
                onEditDraft={editGoalDraft}
                onDeleteDraft={deleteGoalDraft}
                onRunGoal={runGoal}
                onRunVerifiers={runGoalVerifiers}
                onSaveVerifier={saveGoalVerifier}
                onRecordEvidence={recordGoalEvidence}
              />
            )}
            {inspectorTab === "tasks" && (
              <TasksPanel tasks={tasks} focusedTaskId={focusedTaskId} worktreeWorkspace={worktreeWorkspace}
                dispatchForm={{ dispatch: taskDispatch, value: backgroundPrompt, onChange: setBackgroundPrompt,
                  enabled: connection === "connected" && activeScope !== "general" }}
                onFocusTask={focusTask}
                cardActions={{ taskReviewVerifying,
                  loadPlanGraph: (id) => clientRef.current!.devPlanGraph(id),
                  onPause: (item) => pauseTask(item.id), onCancel: (item) => cancelTask(item.id),
                  onResume: resumeTask, onCopy: copyTaskHandoff, onReviewBranch: openTaskBranchReview,
                  onVerify: verifyReviewedTaskBranch, onOpenPr: (item) => openTaskPr(item.id),
                  onCollaboration: draftTaskCollaboration, getClient: () => clientRef.current,
                  onDispatchUpdated: () => void refreshTasks() }}
              />
            )}
            {inspectorTab === "preview" && (
              <PreviewPanel
                view={previewView}
                onSelectView={(view) => {
                  setPreviewView(view);
                  if (view === "artifacts" && artifacts.length === 0) void refreshProjectAssets(activeScope !== "general");
                }}
                plan={plan}
                steps={previewSteps}
                busy={busy}
                artifacts={artifacts}
                selectedArtifact={selectedArtifact}
                artifactVersion={artifactVersion}
                artifactVersions={artifactVersions}
                artifactPreviewLoading={artifactPreviewLoading}
                securedArtifactHtml={securedArtifactHtml}
                onSelectArtifact={setSelectedArtifactId}
                onSelectVersion={(id, version) => void loadArtifactPreview(id, version)}
                onOpenArtifact={openSelectedArtifact}
                references={previewReferenceItems}
                onOpenReference={(url) => void openUrl(url).catch((error) => setBanner(errorText(error, "打开链接失败")))}
              />
            )}
            {inspectorTab === "project" && (
              <ProjectAssetsPanel
                activeScope={activeScope}
                projectAssetView={projectAssetView}
                projectAssetsLoading={projectAssetsLoading}
                projectAssetsError={projectAssetsError}
                connection={connection}
                repoMemory={repoMemory}
                repoMemoryDraft={repoMemoryDraft}
                artifacts={artifacts}
                selectedArtifactId={selectedArtifactId}
                selectedArtifact={selectedArtifact}
                artifactVersion={artifactVersion}
                artifactVersions={artifactVersions}
                artifactPreviewLoading={artifactPreviewLoading}
                securedArtifactHtml={securedArtifactHtml}
                onRefresh={() => void refreshProjectAssets(activeScope !== "general").then(() => {
                  if (selectedArtifactId) void loadArtifactPreview(selectedArtifactId);
                })}
                onSelectView={setProjectAssetView}
                onRepoMemoryDraftChange={setRepoMemoryDraft}
                onAddRepoMemoryFact={addRepoMemoryFact}
                onSelectArtifact={setSelectedArtifactId}
                onSelectVersion={loadArtifactPreview}
                onAttachArtifact={attachSelectedArtifact}
                onOpenArtifact={openSelectedArtifact}
              />
            )}
            {inspectorTab === "decisions" && (
              <div className="decisions-panel">
                <JournalCard
                  journal={journal}
                  journalDays={journalDays}
                  weeklyJournal={weeklyJournal}
                  journalContinuation={journalContinuation}
                  journalView={journalView}
                  journalDate={journalDate}
                  journalNote={journalNote}
                  journalBusy={journalBusy}
                  today={localDay()}
                  onSetView={setJournalView}
                  onSelectDay={selectJournalDay}
                  onRefreshDay={refreshJournal}
                  onRefreshWeekly={refreshWeeklyJournal}
                  onContinueYesterday={continueFromYesterday}
                  onSaveSnapshot={saveJournalSnapshot}
                  onOpenAction={openJournalAction}
                  onNoteChange={setJournalNote}
                  onAddNote={addJournalNote}
                />
                <DecisionsPanel
                  decisionItems={decisionItems}
                  auditEntries={auditEntries}
                  notices={notices}
                  prDelivery={prDelivery}
                  notificationsEnabled={notificationsEnabled}
                  onToggleNotifications={toggleSystemNotifications}
                  onRefresh={() => { void refreshDecisions(); void refreshAudit(); void refreshPrDelivery(); }}
                  onOpenDecision={openDecision}
                  onSendPrFeedback={sendPrFeedbackToAgent}
                  onDismissDecision={dismissDecision}
                />
              </div>
            )}
          </div>
        </aside>
        )}
      </div>

      {phoneChatSid && phoneChats.some((chat) => chat.sid === phoneChatSid) && <PhoneChatDialog
        key={`${assistantScope}:${phoneChatSid}`} chat={phoneChats.find((chat) => chat.sid === phoneChatSid)!}
        owner={clientRef.current} focusedRid={phoneChatRid} name={assistantProfile.name || "VortoCode"}
        onClose={() => { setPhoneChatSid(null); setPhoneChatRid(undefined); }} />}
      {onResidentNow && offlineChatSid && offlineChats.some((chat) => chat.sid === offlineChatSid) && (() => {
        const chat = offlineChats.find((item) => item.sid === offlineChatSid)!;
        return <PhoneChatDialog key={`offline:${chat.sid}`} variant="offline" chat={chat} owner={null}
          name={assistantProfile.name || "VortoCode"} onClose={() => setOfflineChatSid(null)}
          actions={<>
            <button onClick={() => { setOfflineChatIds(dismissOfflineChat(chat.sid)); setOfflineChatSid(null); }}>从列表移除</button>
            <button className="primary" onClick={() => bringOfflineChat(chat)}>带回常驻电脑</button>
          </>} />;
      })()}
      {profileDialogOpen && <AssistantProfileDialog profile={assistantProfile} onClose={() => setProfileDialogOpen(false)}
        onSave={async (next) => {
          const client = clientRef.current;
          if (!client) throw new Error("还没连上助理");
          const scope = assistantScopeRef.current;
          invalidateAssistantReads(); // A pre-save profile read cannot restore the old identity.
          const profile = await client.saveAssistantProfile(next);
          if (clientRef.current !== client || assistantScopeRef.current !== scope) return;
          setAssistantProfile(profile); setProfileDialogOpen(false);
        }} />}
      {remoteDialogOpen && <RemoteProjectDialog purpose={remoteDialogOpen} onClose={() => setRemoteDialogOpen(false)} onRegistered={async (profile) => {
        const known = [profile, ...projects.filter((item) => item.id !== profile.id)];
        setProjects(known);
        setRemoteDialogOpen(false);
        if (isAssistantHost(profile)) await changeAppMode("assistant", known);
        else await switchProject(profile);
      }} />}
      {settingsOpen && (
        <SettingsModal
          residentAccountProjectId={residentHost?.id}
          readCodexObserver={readCodexObserver}
          activeScope={activeScope}
          connection={connection}
          connectionText={connectionText}
          connectionNote={connectionNote}
          runtimeStarting={runtimeStarting}
          projectSwitching={projectSwitching}
          processStatus={runtimeControlStatus}
          runtimeActionBusy={runtimeActionBusy}
          runtimeActionNote={runtimeActionNote}
          runtimeIsRemote={Boolean(remoteProjectRef.current)}
          onReloadRuntime={reloadRuntime}
          recoveryRecord={recoveryRecord}
          llmProfile={llmProfile}
          llmBaseInput={llmBaseInput}
          llmModelInput={llmModelInput}
          llmKeyInput={llmKeyInput}
          llmFastInput={llmFastInput}
          llmStrongInput={llmStrongInput}
          llmRolesInput={llmRolesInput}
          onLlmRolesChange={setLlmRolesInput}
          engineWorkers={engineWorkers}
          llmProfileBusy={llmProfileBusy}
          repoRoot={repoRoot}
          baseUrl={baseUrl}
          token={token}
          trust={trust}
          trustBusy={trustBusy}
          onTrustChange={changeTrustLevel}
          onClose={() => { setSettingsOpen(false); setSettingsSection(undefined); }}
          onLlmBaseChange={setLlmBaseInput}
          onLlmModelChange={setLlmModelInput}
          onLlmFastChange={setLlmFastInput}
          onLlmStrongChange={setLlmStrongInput}
          onRestartRuntime={restartCurrentRuntimeForLlmProfile}
          onLlmProfileChange={applyLlmProfile}
          modelPicks={modelPicks}
          onModelPicksChange={changeModelPicks}
          initialSection={settingsSection}
          onOpenPlugins={() => { setMainView("plugins"); }}
          hasResident={Boolean(findAssistantHost(projects))}
          residentName={findAssistantHost(projects)?.name}
          residentConnected={onAssistantHost() && connection === "connected"}
          onOpenResidentSetup={() => setRemoteDialogOpen("assistant")}
          onConnectResident={() => { const host = findAssistantHost(projects); if (host) void connectAssistantHost(host).then(ok => { if (ok) { setAppMode("assistant"); void openMainChat(host.id); } }); }}
          onOpenSchedule={() => { setAppMode("assistant"); setMainView("chat"); setAssistantTab("schedule"); setInspectorOpen(true); }}
          llmSaveNote={llmSaveNote}
          llmSaveKind={llmSaveKind}
          llmProfileLoading={llmProfileLoading}
          llmProfileError={llmProfileError} llmProfileConflict={llmProfileConflict}
          onReloadLlmProfile={reloadLlmProfile}
          onEditAssistant={() => setProfileDialogOpen(true)}
          onLlmKeyChange={setLlmKeyInput}
          onSaveLlmProfile={saveLlmProfile}
          onClearLlmProfile={clearLlmProfile}
          onChooseRepo={chooseRepo}
          onDismissRecovery={dismissRecoveryRecord}
          onRestoreRecovery={restoreRecoveryConfig}
          onBaseUrlChange={setBaseUrl}
          onTokenChange={setToken}
          onStopRuntime={stopRuntime}
          onConnectExisting={() => void connectToRuntime(activeSid)}
          onStartRuntime={startRuntime}
          onSwitchScope={(scope) => void (async () => {
            // An explicit local settings action leaves Assistant mode; otherwise
            // its resident retry would reconnect the remote host after 10 seconds.
            const leavingAssistant = appMode === "assistant";
            if (!await switchManagedScope(scope) || !leavingAssistant) return;
            workReturnRef.current = null;
            setAppMode("work");
            try { localStorage.setItem(STORAGE_KEYS.appMode, "work"); } catch { /* next launch only */ }
          })()}
        >
          {activeScope === "project" && (
            <ExtensionsInspector
              extensionsInspect={extensionsInspect}
              extensionsInspectBusy={extensionsInspectBusy}
              hookStatus={hookStatus}
              hookTrustBusy={hookTrustBusy}
              connection={connection}
              onRefresh={() => void Promise.all([refreshExtensionsInspect(), refreshHookStatus()])}
              onToggleHookTrust={toggleHookTrust}
            />
          )}
        </SettingsModal>
      )}
    </main>
  );
}

export default App;
