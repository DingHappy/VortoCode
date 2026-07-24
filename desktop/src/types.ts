export type ConnectionState = "disconnected" | "connecting" | "connected" | "error";
export type WorkspaceScope = "general" | "scratch" | "project";

export interface ConnectionSettings {
  baseUrl: string;
  token: string;
}

export interface RuntimeSnapshot {
  workdir?: string;
  scope?: WorkspaceScope;
  has_workspace?: boolean;
  is_project?: boolean;
  model?: string;
  logs?: Array<Record<string, unknown>>;
}

export interface ProtocolEvent {
  type: string;
  rid?: string;
  seq?: number;
  v?: number;
  cursor?: number;
  earliest_seq?: number;
  latest_seq?: number;
  truncated?: boolean;
  data?: unknown;
  text?: string;
  title?: string;
  diff?: string;
  id?: string;
  name?: string;
  event?: string;
  tool?: string;
  phase?: string;
  status?: string;
  label?: string;
  summary?: string;
  args?: Record<string, unknown>;
  result?: unknown;
  duration_ms?: number;
  ok?: boolean;
  path?: string;
  message?: string;
  error?: string;
  stop_execution?: boolean;
  sha256?: string;
  tainted?: boolean;
  scope?: WorkspaceScope;
  reason?: string;
  task?: string;
  items?: unknown[];
  running?: PromptQueueItem;
  baseline?: string;
  source_revision?: string;
  head?: string;
  files?: number;
  paths?: string[];
}

export interface PromptQueueItem {
  id: string;
  version: number;
  text: string;
  mode: "plan" | "build";
  position: number;
  created_at: string;
  context_count: number;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  rid?: string;
}

export type TurnActivityStatus = "running" | "completed" | "succeeded" | "failed" | "blocked" | "cancelled" | string;

export interface TurnActivity {
  id: string;
  rid?: string;
  kind: "phase" | "tool" | "hook" | "event";
  status: TurnActivityStatus;
  label: string;
  phase?: string;
  name?: string;
  event?: string;
  tool?: string;
  error?: string;
  args?: Record<string, unknown>;
  result?: string;
  durationMs?: number;
}

export interface PlanItem {
  step: string;
  status: "pending" | "in_progress" | "completed" | string;
}

export type SessionStatus = "needs_input" | "working" | "queued" | "idle" | "inactive" | "completed" | "failed";

export interface SessionContextUsage {
  used_tokens: number;
  max_tokens: number;
  pct: number;
  system_tokens?: number;
  history_tokens?: number;
  context_window_tokens?: number;
  context_window_pct?: number;
  context_window_source?: "service" | "catalog" | "configured" | "unknown" | string;
  history_messages: number;
  policy: string;
  will_compact: boolean;
}

export interface SessionBackgroundTasks {
  total: number;
  active: number;
  attention: number;
  completed: number;
  latest_status: string;
  latest_prompt: string;
  branch: string;
  latest_task_id: string;
  active_task_id: string;
  attention_task_id: string;
  plan_id: string;
  worktree_count: number;
}

export interface SessionWorktreeSummary {
  kind: "none" | "main" | "linked" | string;
  name: string;
  owned_count: number;
}

export interface SessionHookIssue {
  id: string;
  name: string;
  event: string;
  tool: string;
  status: "failed" | "timed_out" | "blocked" | string;
  summary: string;
  message: string;
  error: string;
  duration_ms: number;
  created?: string;
}

export interface SessionHookIssues {
  count: number;
  latest?: SessionHookIssue | null;
  items: SessionHookIssue[];
  truncated: boolean;
}

export interface SessionSummary {
  sid: string;
  title: string;
  messages: number;
  updated: number;
  status?: SessionStatus;
  queue_count?: number;
  pending_input?: boolean;
  pending_input_count?: number;
  running_prompt?: string;
  activity?: string;
  mode?: "plan" | "build" | null;
  cwd?: string;
  branch?: string;
  head?: string;
  worktree?: SessionWorktreeSummary;
  background_tasks?: SessionBackgroundTasks;
  hook_issues?: SessionHookIssues;
  context?: SessionContextUsage;
}

export interface TaskItem {
  id: string;
  prompt?: string;
  status: string;
  owner_session?: string;
  goal_id?: string;
  branch?: string;
  plan_id?: string;
  parent_task_id?: string;
  result?: string;
  error?: string;
  log?: string[];
  created_at?: string;
  updated_at?: string;
  can_pause?: boolean;
  can_resume?: boolean;
  worktrees?: WorktreeSession[];
  branch_review?: TaskBranchReviewState | null;
  plan?: DevPlanSession | null;
  handoff?: {
    completed: string[];
    remaining: string[];
    next_action: string;
    text: string;
  };
}

export interface HookConfigStatus {
  configured: boolean;
  trusted: boolean;
  active: boolean;
  config_path: string;
  config_sha256: string;
  error?: string;
  sessions_reloaded?: number;
  hooks: Array<{
    name: string;
    type: string;
    events: string[];
    matcher?: string;
    target?: string;
    timeout: number;
    capabilities?: string[];
  }>;
}

export type ExtensionInspectKind = "rules" | "skill" | "hook" | "mcp";
export type ExtensionInspectStatus = "active" | "available" | "disabled" | "needs_trust" | "blocked" | "error";

export interface ExtensionInspectItem {
  id: string;
  kind: ExtensionInspectKind;
  name: string;
  source: string;
  source_scope: "project" | "user" | "bundled";
  status: ExtensionInspectStatus;
  enabled: boolean;
  trusted: boolean | null;
  description: string;
  capabilities: string[];
  detail: string;
  issue: string;
}

export interface ExtensionsInspectSnapshot {
  version: number;
  summary: {
    total: number;
    active: number;
    attention: number;
  };
  items: ExtensionInspectItem[];
  issues: string[];
}

export interface DevPlanBlock {
  id: string;
  title: string;
  kind: string;
  status: string;
  attempts: number;
  note?: string;
}

export interface DevPlanSession {
  plan_id?: string;
  task?: string;
  status: string;
  branch: string;
  base: string;
  updated?: number;
  progress: {
    landed: number;
    failed: number;
    running: number;
    pending: number;
    total: number;
  };
  blocks: DevPlanBlock[];
  integration?: Record<string, unknown> | null;
  review?: Record<string, unknown> | null;
  can_resume?: boolean;
}

export interface WorktreeSession {
  id: string;
  path: string;
  head: string;
  branch?: string;
  detached: boolean;
  changed_files: number;
  locked: boolean;
  prunable: boolean;
  task_id?: string;
  owner_session?: string;
  plan_id?: string;
  created?: string;
}

export interface WorktreeWorkspaceSnapshot {
  worktrees: WorktreeSession[];
  plans: DevPlanSession[];
}

export type CommandRunKind = "terminal" | "test" | "preview";

export interface TestResultCase {
  name: string;
  path: string;
  status: "passed" | "failed" | "skipped" | "error" | string;
  detail: string;
  duration: string;
}

export interface TestResultSnapshot {
  framework: string;
  summary: {
    passed: number;
    failed: number;
    skipped: number;
    errors: number;
    total: number;
  };
  cases: TestResultCase[];
  complete: boolean;
  truncated: boolean;
}

export interface CommandRunItem {
  id: string;
  command: string;
  kind: CommandRunKind;
  status: string;
  process_id?: string;
  pid?: number;
  code?: number | null;
  output: string;
  dropped: number;
  preview_url?: string;
  warning?: string;
  error?: string;
  sandbox?: Record<string, unknown>;
  goal_id?: string;
  criterion_id?: string;
  evidence_kind?: string;
  require_isolation?: boolean;
  timeout_seconds?: number;
  passed?: boolean | null;
  test_results?: TestResultSnapshot;
  created?: string;
  updated?: string;
}

export interface TerminalSession {
  id: string;
  status: "running" | "exited" | string;
  pid: number;
  cwd: string;
  shell: string;
  cols: number;
  rows: number;
  code?: number | null;
  output?: string;
  offset?: number;
  dropped?: boolean;
  created: string;
  updated: string;
  sandbox?: Record<string, unknown>;
}

export type GoalVerifierKind = "test" | "build" | "lint" | "file";

export interface GoalVerifier {
  kind: GoalVerifierKind;
  command?: string;
  path?: string;
  contains?: string;
  timeout: number;
}

export interface GoalCriterion {
  id: string;
  text: string;
  status: "pending" | "passed" | "failed" | string;
  evidence_ids: string[];
  verifier?: GoalVerifier | null;
}

export interface GoalEvidence {
  id: string;
  criterion_id: string;
  kind: string;
  summary: string;
  passed: boolean;
  source?: string;
  created?: string;
}

export interface GoalItem {
  id: string;
  objective: string;
  acceptance_criteria: GoalCriterion[];
  constraints: string[];
  non_goals: string[];
  status: "draft" | "active" | "blocked" | "achieved" | "failed" | string;
  plan_id?: string;
  task_ids: string[];
  branch?: string;
  evidence: GoalEvidence[];
  blocker?: string;
  next_action?: string;
  created?: string;
  updated?: string;
  progress: { passed: number; failed: number; total: number };
}

export interface CreateGoalInput {
  objective: string;
  acceptance_criteria: string[];
  constraints?: string[];
  non_goals?: string[];
  start?: boolean;
}

export interface NoticeItem {
  ts?: string;
  source?: string;
  text: string;
}

export type DecisionKind = "confirmation" | "goal" | "task" | "run" | "pr_check" | "pr_review" | "hook";
export type DecisionSeverity = "critical" | "high" | "medium" | "low";

export interface DecisionItem {
  id: string;
  kind: DecisionKind;
  severity: DecisionSeverity;
  title: string;
  detail: string;
  created?: string;
  target_id: string;
  action: "confirm" | "open_goal" | "resume_task" | "open_task" | "open_run" | "open_diff" | "open_session";
  session_id?: string;
  tainted?: boolean;
  can_dismiss: boolean;
}

export interface RuntimeInboxSession {
  sid: string;
  title: string;
  status: SessionStatus;
  updated: number;
  pending_input_count: number;
  queue_count: number;
  activity: string;
  running_prompt: string;
  cwd: string;
  branch: string;
  background_tasks: Pick<SessionBackgroundTasks,
    "active" | "attention" | "latest_task_id" | "active_task_id" | "attention_task_id">;
  hook_issues: { count: number };
  context: Pick<SessionContextUsage, "pct" | "used_tokens" | "max_tokens">;
}

export interface RuntimeInboxGoal {
  id: string;
  objective: string;
  status: GoalItem["status"];
  blocker: string;
  next_action: string;
  updated: string;
  progress: GoalItem["progress"];
}

export interface RuntimeInboxTask {
  id: string;
  status: string;
  prompt: string;
  detail: string;
  owner_session: string;
  goal_id: string;
  plan_id: string;
  branch: string;
  updated: string;
}

export interface RuntimeInboxCounts {
  sessions_needing_input: number;
  sessions_working: number;
  sessions_queued: number;
  decisions: number;
  hook_issues: number;
  goals_active: number;
  goals_blocked: number;
  tasks_active: number;
  tasks_attention: number;
}

export interface RuntimeInboxSnapshot {
  version: number;
  scope: WorkspaceScope;
  generated_at: string;
  sessions: RuntimeInboxSession[];
  decisions: DecisionItem[];
  goals: RuntimeInboxGoal[];
  tasks: RuntimeInboxTask[];
  counts: RuntimeInboxCounts;
}

export interface AuditEntry {
  id: string;
  ts?: string;
  category: "tool" | "decision" | "event" | "hook";
  session?: string;
  mode?: string;
  tool?: string;
  args?: Record<string, unknown>;
  result_len?: number;
  operation?: string;
  decision?: "allowed" | "denied";
  tainted?: boolean;
  event?: string;
  data?: Record<string, unknown>;
}

export interface JournalSummary {
  tasks_done: number;
  tasks_active: number;
  tasks_failed: number;
  tasks_paused: number;
  goals_achieved: number;
  goals_blocked: number;
  goals_active: number;
  evidence_passed: number;
  evidence_failed: number;
  runs_passed: number;
  runs_failed: number;
  tools: number;
  decisions_allowed: number;
  decisions_denied: number;
  notes: number;
}

export interface JournalEvent {
  id: string;
  kind: "tool" | "decision" | "event" | "task" | "goal" | "evidence" | "run" | "note" | string;
  ts?: string;
  title: string;
  detail?: string;
  status?: string;
  target_id?: string;
}

export interface JournalHandoff {
  task_id: string;
  status: string;
  title: string;
  branch?: string;
  next_action: string;
  completed: string[];
  remaining: string[];
  text: string;
}

export interface JournalEvidence {
  id: string;
  goal_id: string;
  objective: string;
  criterion_id: string;
  criterion: string;
  kind: string;
  summary: string;
  passed: boolean;
  source?: string;
  created?: string;
}

export interface JournalNextAction {
  kind: "goal" | "task" | "run";
  target_id: string;
  action: "open_goal" | "resume_task" | "open_task" | "open_run";
  title: string;
  detail: string;
}

export interface JournalSnapshot {
  date: string;
  generated_at: string;
  stored_at?: string;
  frozen: boolean;
  headline: string;
  digest: string;
  summary: JournalSummary;
  source_counts: Record<string, number>;
  highlights: JournalEvent[];
  timeline: JournalEvent[];
  handoffs: JournalHandoff[];
  evidence: JournalEvidence[];
  notes: Array<{ id: string; text: string; created: string }>;
  next_actions: JournalNextAction[];
}

export interface JournalDaySummary {
  date: string;
  headline: string;
  stored_at?: string;
  summary: Partial<JournalSummary>;
}

export interface WeeklyJournalDay {
  date: string;
  headline: string;
  summary: JournalSummary;
  stored: boolean;
  frozen: boolean;
}

export interface WeeklyJournalSnapshot {
  start_date: string;
  end_date: string;
  days_count: number;
  generated_at: string;
  headline: string;
  summary: JournalSummary;
  days: WeeklyJournalDay[];
  highlights: Array<JournalEvent & { date: string }>;
  carryovers: JournalNextAction[];
}

export interface JournalContinuation {
  from_date: string;
  generated_at: string;
  source_stored: boolean;
  headline: string;
  actions: JournalNextAction[];
  stale_count: number;
}

export interface PendingConfirmation {
  id: string;
  text: string;
  tainted: boolean;
}

export interface DiffPayload {
  title: string;
  diff: string;
}

export type GitReviewScope = "working" | "staged";
export type GitReviewAction = "stage" | "unstage" | "revert";

export interface GitReviewFile {
  path: string;
  original_path?: string;
  status: string;
  index_status: string;
  worktree_status: string;
  staged: boolean;
  unstaged: boolean;
  untracked: boolean;
  conflicted: boolean;
}

// 行级评论的锚定状态（B8-④c S8 从 App.tsx 上收，App 状态与 <GitReviewPanel> 共用）。
export interface PendingGitComment {
  path: string;
  scope: GitReviewScope;
  hunkId: string;
  hunkSha256: string;
  line: number;
  side: "new" | "old";
}

export interface GitReviewSnapshot {
  ok: boolean;
  root: string;
  branch: string;
  head: string;
  files: GitReviewFile[];
  truncated: boolean;
}

export interface GitReviewLine {
  kind: "header" | "add" | "remove" | "context" | "meta";
  text: string;
  old_line?: number | null;
  new_line?: number | null;
}

export interface GitReviewHunk {
  id: string;
  path: string;
  header: string;
  old_start: number;
  new_start: number;
  section: string;
  sha256: string;
  source: "agent" | "user" | "hook" | "mixed" | "external";
  source_session?: string;
  source_turn?: string;
  source_tool?: string;
  source_at?: string;
  accepted?: boolean;
  lines: GitReviewLine[];
}

export interface GitReviewDiff {
  ok: boolean;
  path: string;
  scope: GitReviewScope;
  hunks: GitReviewHunk[];
  binary: boolean;
  diff: string;
  baseline: string;
  head: string;
}

export interface TaskBranchVerification {
  ok: boolean;
  head: string;
  cmd: string;
  output: string;
  sandbox?: Record<string, unknown>;
  at: string;
}

export interface TaskBranchReviewCoverage {
  known: boolean;
  total_hunks: number;
  accepted_hunks: number;
  pending_hunks: number;
  stale_hunks?: number;
  complete: boolean;
  truncated: boolean;
  pending?: Array<{ path: string; hunk_id: string }>;
  pending_truncated?: boolean;
  head?: string;
  error?: string;
}

export interface TaskBranchReviewPolicy {
  configured: boolean;
  active: boolean;
  config_path: string;
  config_sha256: string;
  require_all_hunks_decided: boolean;
  error: string;
}

export interface TaskBranchReviewState {
  accepted_hunks: number;
  verification_stale: boolean;
  verification?: TaskBranchVerification | null;
  coverage?: TaskBranchReviewCoverage;
  policy?: TaskBranchReviewPolicy;
  last_mutation?: {
    action: string;
    path: string;
    hunk_id: string;
    old_head: string;
    head: string;
    at: string;
  } | null;
}

export interface TaskBranchReviewSnapshot extends GitReviewSnapshot {
  target: "task";
  task_id: string;
  base: string;
  head_oid: string;
  mutable: boolean;
  mutation_reason: string;
  review: TaskBranchReviewState;
}

export interface TaskBranchReviewDiff extends Omit<GitReviewDiff, "scope"> {
  target: "task";
  task_id: string;
  scope: "branch";
}

export interface GitReviewComment {
  id: string;
  path: string;
  scope: GitReviewScope;
  hunkId: string;
  hunkSha256: string;
  baseline?: string;
  line: number;
  side: "new" | "old";
  body: string;
  status: "open" | "sent" | "resolved";
  created: string;
  updated: string;
  sentAt?: string;
  resolvedAt?: string;
}

export interface GitReviewCommitResult {
  ok: boolean;
  sha: string;
  output: string;
  snapshot: GitReviewSnapshot;
}

export interface GitReviewPrResult {
  ok: boolean;
  pushed: boolean;
  url: string;
  branch: string;
  base: string;
}

export interface PrDeliveryCheck {
  id: string;
  name: string;
  link?: string;
  conclusion?: string;
  state?: string;
  status?: string;
  workflow?: string;
  started_at?: string;
  completed_at?: string;
  failing: boolean;
  pending: boolean;
}

export interface PrDeliveryComment {
  author: string;
  body: string;
  path?: string | null;
  line?: number | null;
  resolved: boolean;
}

export interface PrDeliverySnapshot {
  ok: boolean;
  error?: string;
  pr?: number;
  url?: string;
  title?: string;
  state?: string;
  draft?: boolean;
  branch: string;
  base?: string;
  review_decision?: string;
  merge_state?: string;
  comments: PrDeliveryComment[];
  checks: PrDeliveryCheck[];
  failing_checks: Array<PrDeliveryCheck>;
  summary: { total: number; failed: number; pending: number; passed: number };
}

export interface PrDeliveryCheckLog {
  ok: boolean;
  check_id: string;
  error?: string;
  log?: {
    name: string;
    run_id: string;
    job_name?: string;
    step_name?: string;
    excerpt?: string;
    error?: string;
    location_error?: string;
  } | null;
}

export interface GatewayProcessStatus {
  running: boolean;
  pid?: number;
  runtimeId?: string;
  projectId?: string;
  workspaceId?: string;
  command?: string;
  scope?: WorkspaceScope;
  workspaceRoot?: string;
  repoRoot?: string;
  baseUrl?: string;
  message: string;
}

export interface DesktopLlmProfileStatus {
  configured: boolean;
  baseUrl: string;
  model: string;
  provider: "vortocode" | "custom" | "local";
  requiresKey: boolean;
  contextWindow?: number;
  contextWindowSource?: "service" | "catalog" | "configured" | "unknown" | string;
}

export interface GatewayRecoveryRecord {
  runtimeId: string;
  projectId?: string;
  workspaceId?: string;
  scope?: WorkspaceScope;
  workspaceRoot: string;
  repoRoot: string;
  baseUrl: string;
  pid?: number | null;
  startedAt: number;
  updatedAt: number;
  status: "running" | "crashed";
  message: string;
}

export interface DesktopProjectProfile {
  id: string;
  name: string;
  // local：本机 Git 工作区（repoRoot 为本机路径）；remote：连服务器上的 runtime
  // （repoRoot 为服务器侧路径、baseUrl 为远端 server_url）。旧注册表条目缺此字段时后端默认 local。
  kind: "local" | "remote";
  repoRoot: string;
  baseUrl: string;
  lastOpenedAt: number;
}

export interface RepoMemorySnapshot {
  path: string;
  content: string;
  effective: string;
  entries: string[];
  total_entries: number;
  injected_entries?: number | null;
  dropped_entries: number;
  truncated: boolean;
  max_chars: number;
  redacted: boolean;
  message?: string;
}

export interface ArtifactMeta {
  id: string;
  title: string;
  kind: string;
  version: number;
  pinned?: number | null;
  created_at: string;
  updated_at: string;
  bytes: number;
  url: string;
}

export interface ArtifactVersion {
  v: number;
  ts: string;
  bytes: number;
}

export interface ArtifactVersionSnapshot {
  id: string;
  current: number;
  pinned?: number | null;
  versions: ArtifactVersion[];
}

export interface WorkspaceFileList {
  root: string;
  files: string[];
  truncated: boolean;
}

export interface WorkspaceFileContent {
  path: string;
  content: string;
  size: number;
  sha256: string;
}

export interface ContextItem {
  path: string;
  startLine?: number;
  endLine?: number;
}

// 源码预览里「点行号选段」的锚定状态（anchor 为落点，start/end 为闭区间边界）。
export interface SourceSelection {
  anchor: number;
  start: number;
  end: number;
}

export interface OpenWorkspaceFileResult {
  launcher: string;
  lineAware: boolean;
  message: string;
}
