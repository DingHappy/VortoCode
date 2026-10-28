import { useState } from "react";
import { createRoot } from "react-dom/client";
import { AssistantActivity, AssistantActivityDetail } from "../src/components/AssistantActivity";
import { CodexActionCard } from "../src/components/CodexActionCard";
import { ConfirmCard } from "../src/components/ComposerPanels";
import { AssistantAudit, AssistantIdentity, AssistantPanel, AssistantSchedule, type AssistantTab } from "../src/components/AssistantMode";
import type { AssistantScheduleItem, AuditEntry } from "../src/types";

const qaAudit: AuditEntry[] = [
  { id: "a1", category: "decision", operation: "推送分支到远端\n合成记录：把演示分支推上去，开 PR 给你看。", decision: "allowed", ts: "2026-10-27T10:00:00Z" },
  { id: "a2", category: "decision", operation: "删除下载目录里的旧安装包\n合成记录：清理 3 个旧版本。", decision: "denied", ts: "2026-10-26T09:00:00Z" },
];
const qaSchedule: AssistantScheduleItem[] = [
  { id: "s1", kind: "heartbeat", title: "心跳巡检", schedule: "every 30m", enabled: true },
  { id: "s2", kind: "report", title: "早报", schedule: "0 9 * * *", enabled: true },
];
import { AssistantWorkdayView } from "../src/components/AssistantWorkday";
import { ComputerLauncherView, ComputerWorkspaceView } from "../src/components/ComputerWorkspace";
import type { FeedItem } from "../src/lib/activityFeed";
import { codexActionFeedItem, confirmationFeedItem, noticeFeedItems } from "../src/lib/activityFeed";
import type { CodexAction, NoticeItem, PendingConfirmation } from "../src/types";
import type { AssistantWorkday } from "../src/lib/assistantWorkday";
import { activityCoverage, type ActivitySource } from "../src/lib/activitySources";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./assistant-activity.css";

const now = new Date();
const at = (minutes: number) => new Date(now.getTime() - minutes * 60_000).toISOString();
const items: FeedItem[] = [
  { id: "waiting", kind: "code", title: "准备下一次项目更新", summary: "安装包已准备，等你确认更新范围", status: "waiting", statusLabel: "待确认下发", sourceLabel: "任务单 · VortoCode", at: at(24 * 60 + 5), dispatchId: "qa-dispatch" },
  { id: "running", kind: "task", title: "核对发布结果", summary: "正在核对构建标识与接口健康", status: "running", at: at(1), taskId: "qa-task" },
  { id: "reply", kind: "chat", title: "整理本周工作安排", summary: "已整理下一步与待决定事项", status: "done", statusLabel: "已回复", sourceLabel: "对话", at: at(20), session: "qa-main", rid:"qa-turn" },
  { id: "failed", kind: "web", title: "核对外部资料", summary: "来源暂时不可用，等待下次核对", status: "failed", at: at(90) },
];
const model: AssistantWorkday = {
  today: [{ id: "qa-goal", title: "完成本周发布准备", source: "个人目标", at: at(5), state: "review", detail: "安装包已准备，仍需核对实际更新结果", target: { kind: "goal", id: "qa-goal" } }],
  progress: [], completed: [], decisions: [], warnings: [],
};
const confirmation: PendingConfirmation = { id: "qa-confirm", text: "是否继续核对本周更新范围？（合成确认，不执行真实操作）", tainted: false };
const notice: NoticeItem = { ts: at(60), source: "assistant", text: "🗒 今日简报\n项目更新：构建已准备，实际安装结果仍待核对。\n等你决定：下一次更新的范围。\n下一步：确认后再安排执行。" };

function Fixture() {
  const [computer, setComputer] = useState(false);
  const [tab, setTab] = useState<AssistantTab>("feed");
  const [connected, setConnected] = useState(true);
  const [selected, setSelected] = useState<FeedItem | null>(null);
  const [closed, setClosed] = useState(false);
  const [decisions, setDecisions] = useState(0);
  const [actionStatus, setActionStatus] = useState<CodexAction["status"]>("proposed");
  const [resolved, setResolved] = useState(false);
  const [longList, setLongList] = useState(false);
  const [newNotice, setNewNotice] = useState(false);
  const [hasNotice, setHasNotice] = useState(true);
  const [source, setSource] = useState(0);
  const [sourceFailed, setSourceFailed] = useState(false);
  const [detailUnavailable, setDetailUnavailable] = useState(false);
  const [checkedAt, setCheckedAt] = useState(Date.now);
  const action: CodexAction = { id: "qa-codex", thread_id: "qa-thread", thread_name: "本周发布核对", kind: "message",
    text: "请先整理发布核对清单，完成后汇报依据。", tainted: false, session: "", error: "", status: resolved ? "sent" : actionStatus,
    created_at: at(10), updated_at: at(2) };
  const history: FeedItem[] = longList ? Array.from({ length: 60 }, (_, index) => ({
    id: `qa-history-${index}`, title: `历史记录 ${index + 1}`, summary: "仅用于核对长列表滚动，不连接真实服务。",
    status: "done", at: at(120 + index), sourceLabel: "合成历史",
  })) : [];
  const feed = [...items.map(item => resolved && item.status === "waiting"
    ? { ...item, status: "done" as const, statusLabel: "已处理（合成）" } : item), ...history,
    ...noticeFeedItems([...(newNotice ? [{ ts: at(0), source: "assistant", text: "🗒 新增简报\n仅用于核对后台更新不打断阅读。" }] : []), ...(hasNotice ? [notice] : [])]),
    codexActionFeedItem(action)!, ...(!resolved ? [confirmationFeedItem(confirmation)] : [])];
  const waiting = feed.filter(item => item.status === "waiting").length;
  const sources: ActivitySource[] = (["turns", "dispatches", "codexActions", "notices"] as const).map(name => ({
    name, checkedAt, status: sourceFailed && name === "dispatches" ? "unavailable" : "current",
  }));
  const complete = activityCoverage(sources, connected, Date.now()).complete;
  return <div className="activity-qa-shell">
    <header className="activity-qa-controls"><span>隔离布局验收 · 合成数据</span>
      <button onClick={() => setConnected(value => !value)}>{connected ? "切换离线" : "恢复连接"}</button>
      <button onClick={() => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; }}>切换主题</button>
      <button onClick={() => setActionStatus(value => value === "proposed" ? "sending" : "proposed")}>模拟发送状态</button>
      <button onClick={() => setResolved(value => !value)}>{resolved ? "恢复待处理示例" : "模拟待处理清空"}</button>
      <button onClick={() => setLongList(value => !value)}>{longList ? "还原短列表" : "载入长列表"}</button>
      <button onClick={() => setNewNotice(value => !value)}>{newNotice ? "撤回新增简报" : "模拟新简报"}</button>
      <button onClick={() => setHasNotice(value => !value)}>{hasNotice ? "移出原简报" : "恢复原简报"}</button>
      <button onClick={() => setSource(value => value + 1)}>切换合成来源</button>
      <button onClick={() => setSourceFailed(value => !value)}>{sourceFailed ? "恢复来源状态" : "模拟来源失败"}</button>
      <button onClick={() => setCheckedAt(Date.now() - 180_000)}>模拟读取过期</button>
      <button onClick={() => setDetailUnavailable(value => !value)}>{detailUnavailable ? "恢复步骤记录" : "模拟步骤记录缺失"}</button>
      <span>合成批准次数：{decisions}</span>
      {closed && <button onClick={() => setClosed(false)}>打开助理面板</button>}
    </header>
    <main className={`activity-qa-layout ${computer ? "computer-demo" : ""}`}>
      <section className="conversation"><div className="conversation-header"><strong>主要聊天</strong></div>
        <div className="conversation-scroll"><article className="message user"><div className="message-body">帮我看看本周有哪些事需要跟进。</div></article>
          <article className="message assistant"><div className="message-author">VortoCode</div><div className="message-body">发布结果正在核对，下一次更新的范围还需要你确认。右侧动态保留每件事的进展与结果。</div></article></div>
        {!resolved && <ConfirmCard confirm={confirmation} connected={connected} trust={null} trustBusy={false} onTrust={() => {}} onCheck={() => {}} onAnswer={() => setDecisions(value => value + 1)} />}
        <p className="activity-qa-note">聊天和动态使用合成数据，不提交任务。电脑工作区连接专用远程桌面的只读画面。</p>
      </section>
      {computer ? <ComputerWorkspaceView view={{target:"ssh:vorto-code",url:"http://127.0.0.1:16088/",id:203}} onClose={()=>setComputer(false)} onRetry={()=>setComputer(true)} onWindow={()=>{}} />
      : !closed && <AssistantPanel name="VortoCode" connected={connected} modelLabel="合成模型"
        computer={<ComputerLauncherView status={{target:"ssh:vorto-code",sshConnected:false,viewerUrl:null,computer:null,checkedAt:null,error:null}}
          busy={false} onOpen={()=>setComputer(true)} onSettings={()=>{}} />}
        host={{ kind: "resident", name: "常驻电脑" }} onAddResident={() => {}} onRetryResident={() => setConnected(true)}
        tab={tab} onTab={setTab} onClose={() => setClosed(true)} waiting={waiting} waitingUncertain={!complete}>
        {tab === "feed" ? <AssistantActivity key={source} items={feed} currentSession="qa-main" connected={connected} sources={sources}
          client={detailUnavailable ? qaUnavailableClient : qaRecordMode ? qaRefreshClient : qaTurnClient}
          onRefresh={() => { setCheckedAt(Date.now()); setSourceFailed(false); }}
          onOpen={item => {
            if (item.confirmationId) { const card = document.getElementById("active-confirmation"); card?.scrollIntoView({ block: "nearest" }); card?.focus({ preventScroll: true }); }
            else setSelected(item);
          }}>
          {selected && <AssistantActivityDetail key={selected.id} title={selected.title} statusLabel={selected.statusLabel || "待处理"}
            steps={[{id:"request",title:"核对更新范围",status:"recorded",detail:"合成请求：整理更新范围，等待用户决定。"},
              {id:"build",title:"检查构建与接口",status:"done",detail:"合成输出：构建标识已记录，接口健康检查通过。\n此记录不代表真实安装验收。"}]}
            onClose={() => setSelected(null)}>
            {selected.codexActionId ? <CodexActionCard action={action} connected={connected} onDecide={async () => { setDecisions(value => value + 1); }} />
              : <><strong>已打开：{selected.title}</strong><p>{selected.summary}</p>
                <label>临时备注（合成，不保存）<input aria-label="详情临时备注" /></label></>}
          </AssistantActivityDetail>}
        </AssistantActivity>
        : tab === "audit" ? <AssistantAudit entries={qaAudit} />
        : tab === "schedule" ? <AssistantSchedule items={qaSchedule} connected={connected} />
        : tab === "identity" ? <AssistantIdentity profile={{ name: "VortoCode", soul: "说话直接，先给结论；拿不准的事先问。", about_you: "合成资料：晚上写代码，常用 Mac 和一台常驻电脑。" }}
          memories={[{ id: "m1", content: "合成记忆：回复用中文，先给结论", created_at: "2026-10-26T10:00:00" }]} proposals={[]}
          onAvatar={() => {}} onEdit={() => {}} onDeleteMemory={() => {}} />
        : <section className="assistant-section"><h5>个人目标</h5><p>合成记录；目标列表需要连接运行端。</p>
          <section className="assistant-goals-overview" aria-label="今日概览"><h3>今日概览</h3>
            <AssistantWorkdayView model={model} now={now} connected={connected} onRefresh={() => {}} onOpen={() => setTab("goals")} /></section></section>}
      </AssistantPanel>}
    </main>
  </div>;
}

const qaTurnClient = { getAgentActivityDetail: async (sid:string,rid:string) => ({ sid,rid,request:"帮我整理本周工作安排（合成数据）",reply:"已整理本周工作安排。\n\n- 发布前核对实际安装版本。\n- 更新范围仍需你决定。\n\n这是一份合成记录，不能作为真实任务完成的依据。",
  has_reply:true,truncated:false,source:"session_archive" as const,
  events:[{type:"agent_tool",id:"read",rid,name:"read_file",summary:"读取工作记录",status:"completed",result:"读取了合成事项清单，共3项。"},
    {type:"agent_tool",id:"check",rid,name:"run_command",summary:"核对验收依据",status:"completed",result:"合成校验：版本信息存在，安装结果待人工核对。"}] }) };
const qaUnavailableClient = { getAgentActivityDetail: async () => { throw new Error("QA: archived turn endpoint unavailable"); } };
const qaRecordMode = new URLSearchParams(window.location.search).get("record");
let qaRecordReads = 0;
const qaRefreshClient = { getAgentActivityDetail: async (sid: string, rid: string) => {
  const count = ++qaRecordReads;
  if (qaRecordMode === "refresh-failure" && count === 2) throw new Error("QA: temporary read failure");
  const value = await qaTurnClient.getAgentActivityDetail(sid, rid);
  if (qaRecordMode === "archive-status") return { ...value, events: [
    { type: "agent_tool", id: "unknown", rid, name: "read_file", summary: "旧记录缺少状态" },
    { type: "agent_tool", id: "running", rid, name: "run_command", summary: "只保存了执行中状态", status: "running", recorded_at: "2026-10-24T09:00:00Z" },
    { type: "agent_tool", id: "timeout", rid, name: "run_command", summary: "核对外部来源", status: "timed_out", error: "合成记录：来源读取超时。", duration_ms: 1250, recorded_at: "2026-10-24T09:00:04Z" },
    { type: "agent_hook", id: "blocked", rid, name: "权限检查", summary: "检查执行条件", status: "blocked", event: "PreToolUse", tool: "run_command" },
  ] };
  if (qaRecordMode === "progress" && count === 1) return { ...value, has_reply: false, reply: "",
    events: [{ type: "agent_tool", id: "read", rid, name: "read_file", summary: "读取工作记录", status: "running", args: { path: "README.md" } }] };
  return { ...value, reply: count > 1 ? "合成新记录：过程已经重新核对。核对工具失败，仍需检查原因。" : value.reply,
    events: [{ type: "agent_tool", id: "read", rid, name: "read_file", summary: "读取工作记录", status: "running", args: { path: "README.md" } },
      { type: "agent_tool", id: "read", rid, name: "read_file", summary: "读取工作记录", status: "failed", error: "合成错误：文件暂不可读。" }] };
} };

createRoot(document.getElementById("root")!).render(<Fixture />);
