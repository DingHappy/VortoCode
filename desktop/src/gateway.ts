import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import WebSocket, { type Message as WebSocketMessage } from "@tauri-apps/plugin-websocket";

import type {
  ArtifactMeta,
  ArtifactVersionSnapshot,
  AuditEntry,
  CommandRunItem,
  CommandRunKind,
  ConnectionSettings,
  CreateGoalInput,
  DecisionItem,
  DevPlanGraph,
  ExtensionsInspectSnapshot,
  GoalItem,
  GoalVerifierKind,
  HookConfigStatus,
  GitReviewAction,
  GitReviewComment,
  GitReviewCommitResult,
  GitReviewDiff,
  GitReviewPrResult,
  GitReviewScope,
  GitReviewSnapshot,
  JournalDaySummary,
  JournalContinuation,
  JournalSnapshot,
  NoticeItem,
  WeeklyJournalSnapshot,
  ProtocolEvent,
  PrDeliveryCheckLog,
  PrDeliverySnapshot,
  RepoMemorySnapshot,
  RuntimeInboxSnapshot,
  SessionSummary,
  TaskItem,
  TaskBranchReviewDiff,
  TaskBranchReviewState,
  TaskBranchReviewSnapshot,
  TerminalSession,
  TrustLevel,
  TrustStatus,
  WorktreeWorkspaceSnapshot,
} from "./types";

export const DESKTOP_PROTOCOL_VERSION = 9;
const EVENT_CURSOR_PREFIX = "vortocode.desktop.eventCursor:";

export function createSessionId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `desktop-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function normalizeLocalBaseUrl(value: string): string {
  const raw = value.trim() || "http://127.0.0.1:8080";
  const withScheme = /^https?:\/\//i.test(raw) ? raw : `http://${raw}`;
  const parsed = new URL(withScheme);
  if (parsed.protocol !== "http:") {
    throw new Error("V0 只支持本机 http runtime；远程连接将在 TLS/配对方案完成后开放");
  }
  if (!["127.0.0.1", "localhost"].includes(parsed.hostname)) {
    throw new Error("V0 只允许连接 127.0.0.1 / localhost");
  }
  parsed.pathname = "";
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString().replace(/\/$/, "");
}

function websocketUrl(baseUrl: string, sid: string): string {
  const parsed = new URL(normalizeLocalBaseUrl(baseUrl));
  parsed.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
  parsed.pathname = "/ws";
  parsed.searchParams.set("sid", sid);
  return parsed.toString();
}

function authHeaders(settings: ConnectionSettings, json = false): Record<string, string> {
  const headers: Record<string, string> = {};
  if (settings.token.trim()) headers.Authorization = `Bearer ${settings.token.trim()}`;
  if (json) headers["Content-Type"] = "application/json";
  return headers;
}

function runtimeError(detail: string, status: number): Error {
  if (detail.trim()) {
    try {
      const payload = JSON.parse(detail) as { detail?: unknown; error?: unknown; message?: unknown };
      const value = payload.detail ?? payload.error ?? payload.message;
      if (typeof value === "string" && value.trim()) return new Error(value);
      if (value && typeof value === "object" && ("message" in value || "reason" in value)) {
        const message = String((value as { message?: unknown; reason?: unknown }).message
          ?? (value as { reason?: unknown }).reason ?? "").trim();
        if (message) return new Error(message);
      }
    } catch {
      return new Error(detail);
    }
    return new Error(detail);
  }
  return new Error(`runtime request failed (${status})`);
}

export class GatewayClient {
  private socket: WebSocket | null = null;
  private removeListener: (() => void) | null = null;
  private sessionId = "";

  constructor(private readonly settings: ConnectionSettings) {}

  async connect(sid: string, onEvent: (event: ProtocolEvent) => void): Promise<void> {
    await this.disconnect();
    this.sessionId = sid;
    const cursorKey = `${EVENT_CURSOR_PREFIX}${sid}`;
    const storedCursor = Number.parseInt(localStorage.getItem(cursorKey) ?? "", 10);
    const resumeCursor = Number.isSafeInteger(storedCursor) && storedCursor >= 0 ? storedCursor : null;
    let deliveredCursor = resumeCursor ?? 0;
    const seenSequences = new Set<number>();
    const persistCursor = (value: number, force = false) => {
      if (!Number.isSafeInteger(value) || (!force && value < deliveredCursor)) return;
      deliveredCursor = value;
      localStorage.setItem(cursorKey, String(value));
    };
    const deliver = (event: ProtocolEvent, replay = false) => {
      if (event.type === "agent_events") {
        const latest = Number.isSafeInteger(event.latest_seq) ? Number(event.latest_seq) : null;
        const reset = resumeCursor !== null && latest !== null && latest < resumeCursor;
        if (reset) {
          seenSequences.clear();
          persistCursor(0, true);                 // sid 被删除后复用：不能拿旧 cursor 吞掉新事件
        }
        const replayFloor = reset ? 0 : resumeCursor;
        const entries = Array.isArray(event.items) ? event.items as ProtocolEvent[] : [];
        entries
          .filter((item) => Number.isSafeInteger(item?.seq))
          .sort((left, right) => Number(left.seq) - Number(right.seq))
          .forEach((item) => {
            const seq = Number(item.seq);
            if (replayFloor !== null && seq <= replayFloor) return;
            if (seenSequences.has(seq)) return;
            seenSequences.add(seq);
            onEvent(item);
            persistCursor(Math.max(deliveredCursor, seq));
          });
        if (Number.isSafeInteger(event.cursor)) persistCursor(Math.max(deliveredCursor, Number(event.cursor)));
        return;
      }
      if (event.type === "status" && Number.isSafeInteger(event.cursor) && resumeCursor === null) {
        persistCursor(Number(event.cursor));       // 首次连接的完整 hydrate 就是这一 cursor 的快照
      }
      if (Number.isSafeInteger(event.seq)) {
        const seq = Number(event.seq);
        if (!replay && (seq <= deliveredCursor || seenSequences.has(seq))) return;
        seenSequences.add(seq);
        if (seenSequences.size > 1000) seenSequences.delete(seenSequences.values().next().value as number);
        persistCursor(Math.max(deliveredCursor, seq));
      }
      onEvent(event);
    };
    const headers = authHeaders(this.settings);
    this.socket = await WebSocket.connect(websocketUrl(this.settings.baseUrl, sid), {
      headers,
      maxFrameSize: 16 * 1024 * 1024,
      maxMessageSize: 32 * 1024 * 1024,
    });
    this.removeListener = this.socket.addListener((message: WebSocketMessage) => {
      if (message.type !== "Text") return;
      try {
        const event = JSON.parse(message.data) as ProtocolEvent;
        if (event && typeof event.type === "string") deliver(event);
      } catch {
        // Gateway 的协议是 JSON；坏帧不应让 Desktop 崩溃。
      }
    });
    // Tauri 原生插件在 connect() 返回后才能注册 JS listener。先取完整快照；已有 cursor 时再
    // 补收上次关闭之后的持久事件。Gateway 的 seq 与本地去重保证两条流重叠也不会重复执行。
    await this.send({ type: "get_status", hydrate: true });
    if (resumeCursor !== null) {
      await this.send({ type: "agent_events_replay", after_seq: resumeCursor, limit: 500 });
    }
  }

  async disconnect(): Promise<void> {
    this.removeListener?.();
    this.removeListener = null;
    if (this.socket) {
      await this.socket.disconnect().catch(() => undefined);
      this.socket = null;
    }
    this.sessionId = "";
  }

  async send(event: Record<string, unknown>): Promise<void> {
    if (!this.socket) throw new Error("尚未连接 runtime");
    await this.socket.send(JSON.stringify(event));
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const base = normalizeLocalBaseUrl(this.settings.baseUrl);
    const headers = {
      ...authHeaders(this.settings, Boolean(init.body)),
      ...(init.headers as Record<string, string> | undefined),
    };
    const response = await tauriFetch(`${base}${path}`, { ...init, headers });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw runtimeError(detail, response.status);
    }
    return (await response.json()) as T;
  }

  private async requestText(path: string, init: RequestInit = {}): Promise<string> {
    const base = normalizeLocalBaseUrl(this.settings.baseUrl);
    const headers = {
      ...authHeaders(this.settings, Boolean(init.body)),
      ...(init.headers as Record<string, string> | undefined),
    };
    const response = await tauriFetch(`${base}${path}`, { ...init, headers });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw runtimeError(detail, response.status);
    }
    return response.text();
  }

  async waitUntilReady(attempts = 24, intervalMs = 250): Promise<void> {
    let lastError: unknown = null;
    for (let index = 0; index < attempts; index += 1) {
      try {
        await this.request<Record<string, unknown>>("/api/health/quick");
        return;
      } catch (error) {
        lastError = error;
        await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
      }
    }
    throw lastError instanceof Error ? lastError : new Error("runtime 未在预期时间内就绪");
  }

  async listSessions(): Promise<SessionSummary[]> {
    const payload = await this.request<{ sessions?: SessionSummary[] }>("/api/agent/sessions");
    return payload.sessions ?? [];
  }

  async renameSession(sid: string, title: string): Promise<void> {
    await this.request(`/api/agent/sessions/${encodeURIComponent(sid)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  }

  async deleteSession(sid: string): Promise<void> {
    await this.request(`/api/agent/sessions/${encodeURIComponent(sid)}`, { method: "DELETE" });
  }

  async acknowledgeHookIssue(sid: string, issueId: string): Promise<void> {
    await this.request(
      `/api/agent/sessions/${encodeURIComponent(sid)}/hook-issues/${encodeURIComponent(issueId)}/ack`,
      { method: "POST" },
    );
  }

  async listTasks(): Promise<TaskItem[]> {
    const payload = await this.request<{ tasks?: TaskItem[] }>("/api/tasks");
    return payload.tasks ?? [];
  }

  async devPlanGraph(planId: string): Promise<DevPlanGraph> {
    return this.request(`/api/dev-plans/${encodeURIComponent(planId)}/graph`);
  }

  async getTaskBranchReview(taskId: string): Promise<TaskBranchReviewSnapshot> {
    return this.request(`/api/tasks/${encodeURIComponent(taskId)}/review`);
  }

  async getTaskBranchReviewDiff(taskId: string, path: string): Promise<TaskBranchReviewDiff> {
    const params = new URLSearchParams({ path });
    return this.request(`/api/tasks/${encodeURIComponent(taskId)}/review/diff?${params.toString()}`);
  }

  async applyTaskBranchReviewAction(
    taskId: string,
    input: {
      action: "accept" | "reject";
      path: string;
      hunk_id: string;
      expected_sha256: string;
      confirm?: boolean;
    },
  ): Promise<{ ok: boolean; action: "accept" | "reject"; snapshot: TaskBranchReviewSnapshot }> {
    return this.request(`/api/tasks/${encodeURIComponent(taskId)}/review/action`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async verifyTaskBranchReview(taskId: string): Promise<{
    ok: boolean;
    verification: TaskBranchReviewState["verification"];
    snapshot: TaskBranchReviewSnapshot;
  }> {
    return this.request(`/api/tasks/${encodeURIComponent(taskId)}/review/verify`, {
      method: "POST",
    });
  }

  async getWorktreeWorkspace(): Promise<WorktreeWorkspaceSnapshot> {
    return this.request("/api/worktrees");
  }

  async listRuns(): Promise<CommandRunItem[]> {
    const payload = await this.request<{ runs?: CommandRunItem[] }>("/api/runs");
    return payload.runs ?? [];
  }

  async getGitReview(): Promise<GitReviewSnapshot> {
    return this.request("/api/git/review");
  }

  async getPrDelivery(): Promise<PrDeliverySnapshot> {
    return this.request("/api/git/delivery");
  }

  async getPrCheckLog(checkId: string): Promise<PrDeliveryCheckLog> {
    return this.request(`/api/git/delivery/checks/${encodeURIComponent(checkId)}/log`);
  }

  async getGitReviewDiff(path: string, scope: GitReviewScope): Promise<GitReviewDiff> {
    const params = new URLSearchParams({ path, scope });
    return this.request(`/api/git/review/diff?${params.toString()}`);
  }

  async applyGitReviewAction(input: {
    action: GitReviewAction;
    path: string;
    scope: GitReviewScope;
    hunk_id?: string;
    expected_sha256?: string;
    confirm?: boolean;
  }): Promise<{ ok: boolean; snapshot: GitReviewSnapshot }> {
    return this.request("/api/git/review/action", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async listGitReviewComments(): Promise<GitReviewComment[]> {
    const payload = await this.request<{ comments?: GitReviewComment[] }>("/api/git/review/comments");
    return payload.comments ?? [];
  }

  async createGitReviewComment(input: {
    path: string;
    scope: GitReviewScope;
    hunk_id: string;
    expected_sha256: string;
    line: number;
    side: "new" | "old";
    body: string;
  }): Promise<GitReviewComment> {
    return this.request("/api/git/review/comments", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async updateGitReviewComment(
    commentId: string,
    status: GitReviewComment["status"],
  ): Promise<GitReviewComment> {
    return this.request(`/api/git/review/comments/${encodeURIComponent(commentId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
  }

  async deleteGitReviewComment(commentId: string): Promise<void> {
    await this.request(`/api/git/review/comments/${encodeURIComponent(commentId)}`, {
      method: "DELETE",
    });
  }

  async commitGitReview(message: string): Promise<GitReviewCommitResult> {
    return this.request("/api/git/review/commit", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
  }

  async openGitReviewPr(input: {
    title: string;
    body?: string;
    base: string;
    confirm: boolean;
  }): Promise<GitReviewPrResult> {
    return this.request("/api/git/review/pr", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async startRun(
    command: string,
    kind: CommandRunKind,
    previewUrl = "",
  ): Promise<CommandRunItem> {
    return this.request("/api/runs", {
      method: "POST",
      body: JSON.stringify({ command, kind, preview_url: previewUrl }),
    });
  }

  async cancelRun(id: string): Promise<CommandRunItem> {
    return this.request(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  }

  async listTerminals(): Promise<TerminalSession[]> {
    const payload = await this.request<{ terminals?: TerminalSession[] }>("/api/terminals");
    return payload.terminals ?? [];
  }

  async createTerminal(cols = 100, rows = 28): Promise<TerminalSession> {
    return this.request("/api/terminals", {
      method: "POST",
      body: JSON.stringify({ cols, rows }),
    });
  }

  async readTerminal(id: string, offset = 0): Promise<TerminalSession> {
    return this.request(`/api/terminals/${encodeURIComponent(id)}/output?offset=${Math.max(0, offset)}`);
  }

  async writeTerminal(id: string, data: string): Promise<TerminalSession> {
    return this.request(`/api/terminals/${encodeURIComponent(id)}/input`, {
      method: "POST",
      body: JSON.stringify({ data }),
    });
  }

  async resizeTerminal(id: string, cols: number, rows: number): Promise<TerminalSession> {
    return this.request(`/api/terminals/${encodeURIComponent(id)}/resize`, {
      method: "POST",
      body: JSON.stringify({ cols, rows }),
    });
  }

  async stopTerminal(id: string): Promise<TerminalSession> {
    return this.request(`/api/terminals/${encodeURIComponent(id)}/stop`, { method: "POST" });
  }

  async listGoals(): Promise<GoalItem[]> {
    const payload = await this.request<{ goals?: GoalItem[] }>("/api/goals");
    return payload.goals ?? [];
  }

  async createGoal(input: CreateGoalInput): Promise<GoalItem> {
    return this.request("/api/goals", { method: "POST", body: JSON.stringify(input) });
  }

  async updateGoal(id: string, input: CreateGoalInput): Promise<GoalItem> {
    return this.request(`/api/goals/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  }

  async deleteGoal(id: string): Promise<void> {
    await this.request(`/api/goals/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  async runGoal(id: string, resume = false): Promise<{ goal: GoalItem; task: TaskItem }> {
    return this.request(`/api/goals/${encodeURIComponent(id)}/run`, {
      method: "POST",
      body: JSON.stringify({ resume }),
    });
  }

  async configureGoalVerifier(
    goalId: string,
    criterionId: string,
    input: {
      kind: GoalVerifierKind | "manual";
      command?: string;
      path?: string;
      contains?: string;
      timeout?: number;
    },
  ): Promise<GoalItem> {
    return this.request(
      `/api/goals/${encodeURIComponent(goalId)}/criteria/${encodeURIComponent(criterionId)}/verifier`,
      { method: "PUT", body: JSON.stringify(input) },
    );
  }

  async runGoalVerifiers(id: string): Promise<{
    goal: GoalItem;
    runs: CommandRunItem[];
    skipped_manual: number;
    skipped_active: number;
  }> {
    return this.request(`/api/goals/${encodeURIComponent(id)}/verify`, { method: "POST" });
  }

  async recordGoalEvidence(
    goalId: string,
    criterionId: string,
    input: { passed: boolean; summary: string; kind?: string; run_id?: string; evidence_id?: string },
  ): Promise<GoalItem> {
    return this.request(
      `/api/goals/${encodeURIComponent(goalId)}/criteria/${encodeURIComponent(criterionId)}/evidence`,
      { method: "POST", body: JSON.stringify(input) },
    );
  }

  async submitTask(prompt: string): Promise<{ id: string; status: string }> {
    return this.request("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ prompt, session: this.sessionId }),
    });
  }

  async getHookStatus(): Promise<HookConfigStatus> {
    return this.request("/api/hooks");
  }

  async getTrust(): Promise<TrustStatus> {
    return this.request("/api/trust");
  }

  async setTrust(level: TrustLevel): Promise<TrustStatus> {
    return this.request("/api/trust", {
      method: "PUT",
      body: JSON.stringify({ level }),
    });
  }

  async setHookTrust(trusted: boolean): Promise<HookConfigStatus> {
    return this.request("/api/hooks/trust", {
      method: "PUT",
      body: JSON.stringify({ trusted }),
    });
  }

  async getExtensionsInspect(): Promise<ExtensionsInspectSnapshot> {
    return this.request("/api/extensions/inspect");
  }

  async cancelTask(id: string): Promise<void> {
    await this.request(`/api/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  }

  async pauseTask(id: string): Promise<TaskItem> {
    return this.request(`/api/tasks/${encodeURIComponent(id)}/pause`, { method: "POST" });
  }

  async resumeTask(id: string): Promise<TaskItem> {
    return this.request(`/api/tasks/${encodeURIComponent(id)}/resume`, { method: "POST" });
  }

  async openTaskPr(id: string): Promise<{ ok?: boolean; url?: string; error?: string }> {
    return this.request(`/api/tasks/${encodeURIComponent(id)}/open_pr`, { method: "POST" });
  }

  async listNotices(): Promise<NoticeItem[]> {
    const payload = await this.request<{ notices?: NoticeItem[] }>("/api/notices?limit=50");
    return payload.notices ?? [];
  }

  async listDecisions(session: string): Promise<DecisionItem[]> {
    const params = new URLSearchParams({ limit: "100", session });
    const payload = await this.request<{ decisions?: DecisionItem[] }>(`/api/decisions?${params.toString()}`);
    return payload.decisions ?? [];
  }

  async getRuntimeInbox(): Promise<RuntimeInboxSnapshot> {
    return this.request("/api/runtime-inbox");
  }

  async dismissDecision(id: string): Promise<void> {
    await this.request(`/api/decisions/${encodeURIComponent(id)}/dismiss`, { method: "POST" });
  }

  async listAudit(limit = 100): Promise<AuditEntry[]> {
    const payload = await this.request<{ entries?: AuditEntry[] }>(`/api/audit?limit=${limit}`);
    return payload.entries ?? [];
  }

  async getJournal(date: string): Promise<JournalSnapshot> {
    const params = new URLSearchParams({ date });
    return this.request(`/api/journal?${params.toString()}`);
  }

  async snapshotJournal(date: string): Promise<JournalSnapshot> {
    return this.request("/api/journal/snapshot", {
      method: "POST",
      body: JSON.stringify({ date }),
    });
  }

  async addJournalNote(date: string, text: string): Promise<JournalSnapshot> {
    return this.request("/api/journal/notes", {
      method: "POST",
      body: JSON.stringify({ date, text }),
    });
  }

  async listJournalDays(): Promise<JournalDaySummary[]> {
    const payload = await this.request<{ days?: JournalDaySummary[] }>("/api/journal/days");
    return payload.days ?? [];
  }

  async getWeeklyJournal(end: string, days = 7): Promise<WeeklyJournalSnapshot> {
    const params = new URLSearchParams({ end, days: String(days) });
    return this.request(`/api/journal/weekly?${params.toString()}`);
  }

  async getJournalContinuation(date: string): Promise<JournalContinuation> {
    const params = new URLSearchParams({ date });
    return this.request(`/api/journal/continuation?${params.toString()}`);
  }

  async getRepoMemory(): Promise<RepoMemorySnapshot> {
    return this.request("/api/repo-memory");
  }

  async addRepoMemory(content: string): Promise<RepoMemorySnapshot> {
    return this.request("/api/repo-memory", {
      method: "POST",
      body: JSON.stringify({ content, confirm: true }),
    });
  }

  async listArtifacts(): Promise<ArtifactMeta[]> {
    const payload = await this.request<{ artifacts?: ArtifactMeta[] }>("/api/artifacts");
    return payload.artifacts ?? [];
  }

  async getArtifactVersions(id: string): Promise<ArtifactVersionSnapshot> {
    return this.request(`/api/artifacts/${encodeURIComponent(id)}/versions`);
  }

  async getArtifactHtml(id: string, version?: number): Promise<string> {
    const query = version ? `?v=${encodeURIComponent(String(version))}` : "";
    return this.requestText(`/artifact/${encodeURIComponent(id)}/raw${query}`);
  }
}
