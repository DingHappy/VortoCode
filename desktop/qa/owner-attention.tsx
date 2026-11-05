import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { WaitingInbox, waitingTeams } from "../src/components/WaitingInbox";
import { AssistantActivity } from "../src/components/AssistantActivity";
import { TaskCard } from "../src/components/TaskCard";
import { TeamChat, type TeamChatClient } from "../src/components/TeamChat";
import { assistantAttention } from "../src/lib/assistantAttention";
import { taskFeedItem, type FeedItem } from "../src/lib/activityFeed";
import type { TaskItem, TeamDetail, TeamTranscript } from "../src/types";
import type { ActivitySource } from "../src/lib/activitySources";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./owner-attention.css";

const body = "## 市场日报（合成资料）\n\n结论：以下数字和来源均为测试示例，不是真实行情。\n\n| 项目 | 状态 | 来源 |\n|---|---|---|\n|示例指数|待核实|示例原文|\n|示例板块|已整理|模拟记录|\n\n### 未解决项\n\n- 日期仍待核对。\n- 请核对来源后再决定是否验收。\n\n[示例链接](https://example.com/report)\n\n<script>不应执行</script>";
const task: TaskItem = { id: "task-owner", kind: "delegation", status: "done", prompt: "整理一份有来源的简明日报。\n\n" + "这是完整要求与历史底稿，仅供展开查看，不应占据整个首屏。\n".repeat(30),
  owner_session: "sid-qa", result: body, collaboration: { assignee: "研究员", round: 1, review: "pending", acceptance: ["附来源"], tainted: true, messages: [] } };
const detail: TeamDetail = { id: "qa-team", objective: "调研公开资料，交付简明日报；由审查员核对来源后交主人验收。", status: "review", mode: "open", waiting_reason: "", used: { tasks: 3, rounds: 4 }, limits: { parallel: 2 }, project: null,
  members: [{ name: "研究员", role: "web-researcher", engine: "opencode" }], tainted: true, events: [], acceptance: ["来源可追溯"],
  children: [{ id: "task-member", member: "研究员", status: "done", review: "pending", round: 2, result: body, tainted: true },
    { id: "task-failed", member: "研究员", status: "failed", round: 1, error: "上一轮未交回内容", tainted: true },
    { id: "task-question", member: "审查员", status: "blocked", round: 1, tainted: true }],
  submission: { summary: "## 交付总结\n\n日报整理完，尚未替你验收。完整正文见 task-member 第2轮。仍有日期与来源需要核对。", tainted: true, evidence: [{ criterion: 0, text: "来源可追溯", evidence: "task-member 第2轮保留来源，未核实部分已注明。" }] } };
const transcript: TeamTranscript = { team: { ...detail, project: undefined }, members: [{ name: "研究员", title: "研究员", role: "web-researcher", engine: "opencode", state: "idle", rounds: 2, delivered: 1, failures: 1 }],
  messages: [{ at: "2026-11-05T10:00:00Z", speaker: "member", kind: "result", member: "研究员", child_id: "task-member", round: 2, text: body, tainted: true },
    { at: "2026-11-05T10:05:00Z", speaker: "coordinator", kind: "submitted", text: detail.submission!.summary, tainted: true }] };
const records: FeedItem[] = [taskFeedItem(task), taskFeedItem({ ...task, id: "task-member" }), taskFeedItem({ ...task, id: "task-failed", status: "failed", collaboration: { ...task.collaboration!, review: "not_submitted" } }),
  { ...taskFeedItem({ ...task, id: "task-question" }), requiresReply: true, title: "请你决定报告包含哪些市场", summary: "等你回复：是否包含港股？" },
  taskFeedItem({ id: detail.id, kind: "team", status: "review" }),
  { id: "unknown", codexActionId: "qa-action", title: "测试发送回执待核对", summary: "没有取得回执，不能重复发送", status: "waiting", at: "", statusLabel: "结果无法确认" },
  { id: "root-failure", title: "独立任务失败，需要核对", summary: "来源暂不可达", status: "failed", at: "" }];

function Fixture() {
  const query = new URLSearchParams(location.search);
  const [view, setView] = useState(query.get("view") || "waiting");
  const [light, setLight] = useState(query.has("light"));
  const [narrow, setNarrow] = useState(query.has("narrow"));
  const [known, setKnown] = useState(true);
  const [actions, setActions] = useState(0);
  const [focus, setFocus] = useState(0);
  const [notice, setNotice] = useState("");
  const stage = useRef<HTMLDivElement>(null);
  const [metrics, setMetrics] = useState("");
  useEffect(() => { document.documentElement.dataset.theme = light ? "light" : "dark"; }, [light]);
  const teams = known ? [{ ...detail, project: detail.project?.name ?? null }] : [];
  const attention = assistantAttention(records, teams);
  const client = useMemo<TeamChatClient>(() => ({ getTeam: async () => detail, getTeamTranscript: async () => transcript,
    decideTeam: async () => { setActions(value => value + 1); return detail; }, messageTeam: async () => { setActions(value => value + 1); return transcript; },
    replyTeam: async () => detail, stopTeam: async () => detail }), []);
  const sources: ActivitySource[] = (["turns", "dispatches", "codexActions", "notices"] as const).map(name => ({ name, status: "current", checkedAt: Date.now() }));
  useEffect(() => {
    const measure = () => setMetrics(JSON.stringify({ pageOverflow: document.documentElement.scrollWidth > innerWidth,
      ownerWaiting: attention.decisions.length + waitingTeams(teams).length, internal: attention.teamRecords.length, records: attention.history.length,
      focusedExceptions: document.activeElement?.getAttribute("aria-label") === "需要核对", actions }));
    const observer = new ResizeObserver(measure); if(stage.current) observer.observe(stage.current);
    measure(); return () => observer.disconnect();
  }, [view, narrow, light, known, actions, focus]);
  return <><nav className="qa-owner-controls" aria-label="合成验收控制">
    <span>真实组件 · 合成资料</span>{["waiting", "task", "team", "activity"].map((name, i) => <button key={name} onClick={() => setView(name)}>{["待办", "任务详情", "小组交付", "动态"][i]}</button>)}
    <button onClick={() => setLight(!light)}>切换{light ? "深色" : "浅色"}</button><button onClick={() => setNarrow(!narrow)}>{narrow ? "恢复宽栏" : "窄栏 360"}</button>
    <button onClick={() => setKnown(!known)}>{known ? "模拟归属未读到" : "恢复小组归属"}</button>
    <button onClick={() => { setView("activity"); setFocus(value => value + 1); }}>查看需核对</button>
  </nav><main ref={stage} className="qa-owner-stage vc-mode-assistant" style={{ width: narrow ? "min(360px,100%)" : "min(1000px,100%)" }}>
    {view === "waiting" ? <><h2>需要你决定的事</h2><WaitingInbox items={attention.decisions} teams={teams} exceptions={attention.exceptions.length} connected
      onOpen={item => { if(item.taskId === task.id) setView("task"); else setNotice(`只读打开：${item.title}`); }} onOpenTeam={() => setView("team")}
      onAcceptTeam={async () => { setActions(value => value + 1); }} onShowActivity={() => { setView("activity"); setFocus(value => value + 1); }} />
      <p className="qa-owner-note">小组内部 {attention.teamRecords.length} 条记录保留在小组与全部记录中。</p>{notice && <p role="status">{notice}</p>}</> :
    view === "task" ? <TaskCard task={task} focused taskReviewVerifying={false} loadPlanGraph={async () => { throw new Error("unused"); }} onPause={() => {}} onCancel={() => {}} onResume={() => {}} onCopy={() => {}}
      onReviewBranch={() => {}} onVerify={() => {}} onOpenPr={() => {}} onCollaboration={() => setActions(value => value + 1)} getClient={() => null} onDispatchUpdated={() => {}} /> :
    view === "team" ? <TeamChat id={detail.id} client={client} connected initial={transcript} initialDetail={detail} /> :
    <><AssistantActivity items={records} teams={teams} sources={sources} connected onOpen={() => {}} exceptionFocus={focus} onExceptionFocused={() => setFocus(0)} />
      <section className="qa-owner-timeline"><h3>例行时间线（合成）</h3>{Array.from({ length: 20 }, (_, i) => <p key={i}>例行工作记录 {i + 1}</p>)}</section></>}
  </main><output className="qa-owner-metrics" aria-label="只读验收量测">{metrics}</output></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
