import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MarkdownMessage } from "../src/components/MarkdownMessage";
import { WaitingInbox, waitingTeams } from "../src/components/WaitingInbox";
import { AssistantActivity } from "../src/components/AssistantActivity";
import { ConversationHeader } from "../src/components/ConversationHeader";
import { TeamChat, type TeamChatClient } from "../src/components/TeamChat";
import { assistantAttention } from "../src/lib/assistantAttention";
import type { FeedItem } from "../src/lib/activityFeed";
import type { TeamDetail, TeamSummary, TeamTranscript } from "../src/types";
import type { ActivitySource } from "../src/lib/activitySources";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./chat-reading.css";

const objective = "调研今日股市市场情况：主要指数点位与涨跌幅、热点板块、重要市场消息，产出一份简明市场日报；由审查员核对来源与日期后交付。";
const team: TeamSummary = { id: "qa-team", objective, status: "review", mode: "open", waiting_reason: "", used: { tasks: 3, rounds: 4 }, limits: { parallel: 2 }, members: [], children: [], tainted: true };
const detail: TeamDetail = { ...team, project: null, acceptance: ["逐项附上来源并注明日期"], events: [], submission: null };
const transcript: TeamTranscript = { team: { ...team, acceptance: detail.acceptance }, members: [
  { name: "研究员", title: "研究员", role: "web-researcher", engine: "opencode", state: "idle", rounds: 2, delivered: 1, failures: 1 },
  { name: "审查员", title: "审查员", role: "reviewer", engine: "vortocode", state: "idle", rounds: 1, delivered: 1, failures: 0 },
], messages: [{ speaker: "coordinator", kind: "submitted", at: "2026-11-05T10:00:00Z", tainted: true,
  text: "日报已经整理完，等你验收。本页内容全部为合成资料，不是实时行情。\n\n主要结论和来源已经交叉核对；仍有两项资料需要补充，已在原文标出，尚未替你验收。",
  evidence: [
    { criterion: 0, text: "指数与日期", evidence: "所有示例指数均保留数据日期、收盘状态和来源。未经核实的内容明确标为待核实；这里只检查阅读与排版，不提供真实交易建议。" },
    { criterion: 0, text: "来源可追溯", evidence: "逐项保留原始链接和核对说明。你可以先读结论，再展开完整目标、成员和原始依据；读取不会产生批准。" },
    { criterion: 0, text: "下一步", evidence: "确认资料覆盖范围，再决定是否验收。不同事项使用原来的确认与验收入口；这份夹具不会连接真实服务。" },
  ] }] };
const records: FeedItem[] = [
  { id: "qa-a", title: "发布准备待你确认", summary: "变更范围、验证记录与交付分支已经整理好", status: "waiting", sourceLabel: "任务单", at: "2026-11-05T10:00:00Z" },
  { id: "qa-b", title: "拟发消息：请先核对交付依据，再汇报实际结果", summary: "尚未发出，查看内容后决定", status: "waiting", sourceLabel: "Codex 动作", at: "2026-11-05T09:50:00Z" },
  { id: "qa-error", title: "资料来源需核对", summary: "没有取得最新回执，不表示任务已完成", status: "failed", at: "2026-11-05T09:40:00Z" },
];
const reply = "## 今天需要关注的事\n\n结论：构建已经准备好，实际安装与验收仍需核对。项目名称、长路径和正文应该各自清楚可读。\n\n| 会话 | 项目 | 最近动态 |\n|---|---|---|\n|继续处理对话|VortoOne|构建已准备；实际安装与客户端界面仍待验收。|\n|查看当前改动|qban-ai-agents|已整理变更范围与下一步，保留来源和核对时间。|\n\n补充说明：`deploy/one-wish-agent-tasks` 是示例分支名，路径 `tests/unit/test_safe_id_edge_cases.py` 不应撑破聊天栏。\n\n> 尚未取得回执不等于操作没有发生；请从原记录核对依据。\n\n1. 先检查范围与来源。\n2. 再决定下一步，原有确认流程保持可达。\n\n```python\ndef add(a, b):\n    return a + b\n```";

function Fixture() {
  const query = new URLSearchParams(location.search);
  const [theme, setTheme] = useState(query.get("theme") === "light" ? "light" : "dark");
  const [view, setView] = useState<"chat" | "team" | "activity">(query.get("view") === "team" ? "team" : query.get("view") === "activity" ? "activity" : "chat");
  const [narrow, setNarrow] = useState(query.has("narrow"));
  const [expanded, setExpanded] = useState(false);
  const [extra, setExtra] = useState(false);
  const [actions, setActions] = useState(0);
  const [metrics, setMetrics] = useState("");
  const stage = useRef<HTMLDivElement>(null);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const client = useMemo<TeamChatClient>(() => ({ getTeamTranscript: async () => transcript, getTeam: async () => detail,
    decideTeam: async () => { setActions(value => value + 1); return detail; },
    messageTeam: async () => { setActions(value => value + 1); return transcript; },
    replyTeam: async () => detail, stopTeam: async () => detail,
  }), []);
  const items = extra ? [...records, { ...records[0], id: "qa-extra", title: "后台新增的待确认事项" }] : records;
  const attention = assistantAttention(items);
  const total = attention.decisions.length + waitingTeams([team]).length;
  const sources: ActivitySource[] = (["turns", "dispatches", "codexActions", "notices"] as const).map(name => ({ name, status: "current", checkedAt: Date.now() }));
  useEffect(() => {
    let alive = true;
    const measure = () => {
      if (!alive || !stage.current) return;
      const cell = stage.current.querySelector("tbody tr td:nth-child(2)");
      const range = document.createRange();
      if (cell) range.selectNodeContents(cell);
      const code = stage.current.querySelector(".markdown-body code");
      const evidence = stage.current.querySelector(".team-chat-evidence");
      const band = stage.current.querySelector(".waiting-inbox-band");
      const result = JSON.stringify({ viewport: stage.current.clientWidth, pageOverflow: document.documentElement.scrollWidth > innerWidth,
        projectNameLines: cell ? range.getClientRects().length : null,
        codeBackground: code ? getComputedStyle(code).backgroundColor : null,
        inboxHeight: band ? Math.round(band.getBoundingClientRect().height) : null,
        evidenceLineHeight: evidence ? getComputedStyle(evidence).lineHeight : null,
        total, actions });
      setMetrics(result);
    };
    const observer = new ResizeObserver(measure);
    if (stage.current) observer.observe(stage.current);
    void document.fonts.ready.then(measure); measure();
    return () => { alive = false; observer.disconnect(); };
  }, [view, narrow, theme, expanded, extra, total, actions]);
  return <>
    <nav className="qa-reading-controls" aria-label="隔离验收控制">
      <span>真实组件 · 合成资料</span>
      <button onClick={() => setView("chat")}>聊天</button><button onClick={() => setView("team")}>小组</button>
      <button onClick={() => setView("activity")}>动态</button>
      <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>切换{theme === "dark" ? "浅色" : "深色"}</button>
      <button onClick={() => setNarrow(!narrow)}>{narrow ? "恢复宽栏" : "窄栏 360"}</button>
      <button onClick={() => setExtra(!extra)}>切换后台新事项</button>
    </nav>
    <div ref={stage} className="qa-reading-stage vc-mode-assistant" style={{ width: narrow ? "min(360px, 100%)" : "min(1000px, 100%)" }}>
      {view === "team" ? <TeamChat id={team.id} client={client} connected initial={transcript} /> : view === "activity" ?
        <AssistantActivity items={items} sources={sources} connected onOpen={() => {}} waitingInbox={{ count: total, uncertain: false, onOpen: () => { setExpanded(true); setView("chat"); } }} /> :
        <section className="conversation qa-reading-conversation">
          <ConversationHeader title="主要聊天" place={null} panelOpen={false} waiting={total} plan={{ done: 0, total: 0 }} busy={false} onTogglePanel={() => setView("activity")} onStop={() => {}} />
          <div className="waiting-inbox-band"><WaitingInbox items={attention.decisions} teams={[team]} exceptions={attention.exceptions.length} connected
            expanded={expanded} onExpandedChange={setExpanded} onOpen={() => setView("activity")} onOpenTeam={() => setView("team")}
            onAcceptTeam={async () => { setActions(value => value + 1); }} onShowActivity={() => setView("activity")} /></div>
          <div className="conversation-scroll"><article className="message assistant"><div className="message-body"><MarkdownMessage text={reply} /></div></article></div>
        </section>}
    </div>
    <output className="qa-reading-metrics" aria-label="隔离验收量测">{metrics}</output>
  </>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
