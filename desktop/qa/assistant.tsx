import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowUp, Cpu, Paperclip, Search, Settings } from "lucide-react";
import { ActivityFeed } from "../src/components/ActivityFeed";
import { AssistantAudit, AssistantAvatarImage, AssistantIdentity, AssistantPanel, AssistantSchedule, AssistantSidebar, ModeSwitch, type AppMode } from "../src/components/AssistantMode";
import { MarkdownMessage } from "../src/components/MarkdownMessage";
import { taskFeedItem, turnFeedItem } from "../src/lib/activityFeed";
import type { AgentTurn, TaskItem } from "../src/types";
import "../src/App.css";
import "../src/daily.css";

// 合成数据：结构与 /api/agent/activity、/api/tasks 一致，走与真实数据相同的投影函数。
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const turns: AgentTurn[] = [
  { sid: "main", session_title: "主要聊天", rid: "r5", title: "调研 VortoCode 项目", outcome: "完成静态审查并归档记录", digest_source: "model", status: "done", tools: 6, tool_names: ["read_file", "grep"], at: ago(8) },
  { sid: "main", session_title: "主要聊天", rid: "r4", title: "查询宁波天气", outcome: "给出今明两天天气与出行建议", digest_source: "model", status: "done", tools: 3, tool_names: ["web_search", "web_fetch"], at: ago(55) },
  { sid: "side-1", session_title: "整理周报", rid: "r1", title: "整理本周工作周报", outcome: "按项目汇总了 9 个 PR 的进展", digest_source: "model", status: "done", tools: 2, tool_names: ["run_command"], at: ago(130) },
  { sid: "main", session_title: "主要聊天", rid: "r3", title: "检查本地模型连接", outcome: "确认 Qwen3.6 连接正常、工具调用格式正确", digest_source: "model", status: "done", tools: 1, tool_names: ["run_command"], at: ago(60 * 20) },
  { sid: "main", session_title: "主要聊天", rid: "r2", title: "修复面板空白问题", outcome: "无计划时改列本轮执行步骤", digest_source: "model", status: "done", tools: 5, tool_names: ["write_file"], at: ago(60 * 21) },
  { sid: "side-2", session_title: "MCP 接入", rid: "r1", title: "接入 GitHub MCP", outcome: "没有完成，可以在对话里继续", digest_source: "text", status: "no_reply", tools: 2, tool_names: [], at: ago(60 * 23) },
];
const tasks: TaskItem[] = [
  { id: "t1", status: "running", prompt: "清理 workflow/tasks 的循环依赖", owner_session: "sid-main", development: { version: 1, round: 1, owner_session: "sid-main", plan_id: "", root_task_id: "t1", questions: [{ id: "q", task_id: "t1", round: 1, status: "open", question: "CollaborationConflict 要不要单独成模块？", options: [], context: "", asked_by: "worker", answerer: "owner", created: "", expires_at: "", answer: "", answered_at: "" }] }, updated_at: ago(12) },
  { id: "t2", status: "running", prompt: "给 Desktop 打包签名版", log: ["正在构建 sidecar 运行时"], owner_session: "sid-main", updated_at: ago(3) },
];
const feed = [...turns.map(turnFeedItem), ...tasks.map(taskFeedItem)];
const audit = [
  { id: "a1", category: "decision" as const, operation: "下发任务单「登录加超时重试」到 VortoCode", decision: "allowed" as const, ts: ago(60 * 23) },
  { id: "a2", category: "decision" as const, operation: "保存长期记忆？\n汇报先给结论，再说细节", decision: "allowed" as const, ts: ago(60 * 5) },
  { id: "a3", category: "decision" as const, operation: "发布制品「本周周报」？", decision: "denied" as const, tainted: true, ts: ago(60 * 26) },
];
const schedule = [
  { id: "report-09:00", kind: "report" as const, title: "早报", schedule: "每天 09:00", detail: "今天聊过的事、任务单状态、等你处理的", enabled: true },
  { id: "report-21:00", kind: "report" as const, title: "晚报", schedule: "每天 21:00", detail: "今天聊过的事、任务单状态、等你处理的", enabled: true },
  { id: "follow-up", kind: "watch" as const, title: "跟进派出去的活", schedule: "每 1 分钟", detail: "状态有变化就推到手机", enabled: true },
  { id: "heartbeat", kind: "heartbeat" as const, title: "Heartbeat", schedule: "每 30m", detail: "按 HEARTBEAT.md 检查单巡视，有情况才告诉你", enabled: true },
];
const memories = [{ id: "m1", content: "汇报先给结论，再说细节", created_at: "2026-10-11T09:00:00" }, { id: "m2", content: "住在宁波，常用 Telegram", created_at: "2026-10-09T09:00:00" }];
const profile = { name: "小V", soul: "简短直接，先给结论；不确定就直说。", about_you: "丁开心，在宁波做 AI 编码工具。" };

// ?avatar=1：用一张画出来的示例图当头像（合成数据）
function sampleAvatar(): import("../src/types").AssistantAvatar | null {
  if (new URLSearchParams(location.search).get("avatar") !== "1") return null;
  const canvas = document.createElement("canvas"); canvas.width = canvas.height = 256;
  const c = canvas.getContext("2d")!; const g = c.createLinearGradient(0, 0, 256, 256);
  g.addColorStop(0, "#f6b26b"); g.addColorStop(1, "#e06666"); c.fillStyle = g; c.fillRect(0, 0, 256, 256);
  c.font = "150px serif"; c.textAlign = "center"; c.textBaseline = "middle"; c.fillText("🦊", 128, 140);
  const url = canvas.toDataURL("image/png"); return { type: "image/png", data: url.slice(url.indexOf(",") + 1) };
}

function Fixture() {
  const [avatar, setAvatar] = useState(sampleAvatar);
  const [mode, setMode] = useState<AppMode>("assistant");
  const [current, setCurrent] = useState("main");
  const [tab, setTab] = useState<import("../src/components/AssistantMode").AssistantTab>("feed");
  const [panel, setPanel] = useState(true);
  return <div className="app-shell"><header className="topbar"><small>助理模式隔离验收 · 合成数据</small>
    <button onClick={() => document.documentElement.dataset.theme = document.documentElement.dataset.theme === "light" ? "dark" : "light"}>切换主题</button>
    <button onClick={() => setPanel(!panel)}>切换右栏</button></header><div className="banner-slot" />
    <div className={`workbench ${panel ? "inspector-open" : "inspector-closed"}`}>
      <aside className="sidebar">
        <div className="sidebar-brand"><strong>VortoCode</strong><Search size={16} /></div>
        <ModeSwitch mode={mode} onChange={setMode} />
        <AssistantSidebar mainChat={{ sid: "main", preview: "已完成静态审查并归档记录", busy: true }}
          sideChats={[{ sid: "side-1", title: "整理周报" }, { sid: "side-2", title: "MCP 接入" }]}
          current={current} onSelect={setCurrent} onNewSideChat={() => {}} />
        <div className="sidebar-footer" style={{ marginTop: "auto", padding: 16 }}><small>账号</small><Settings size={16} /></div>
      </aside>
      <section className="conversation"><div className="conversation-header"><strong>{current === "main" ? "主要聊天" : current === "side-1" ? "整理周报" : "MCP 接入"}</strong></div>
        <div className="conversation-scroll">
          <article className="message user"><div className="message-body">你帮我看一下 VortoCode 这个项目，做一次静态审查。</div></article>
          <article className="message assistant"><div className="message-author with-avatar"><AssistantAvatarImage name={profile.name} avatar={avatar} size={22} />{profile.name}</div><div className="message-body"><MarkdownMessage text={"看完了，结论先说：**工程纪律是全场最强的一项**——离线 CI、契约测试钉死路由基线、污点追踪。\n\n完整报告已归档到知识库，右侧动态里有这次的记录。"} /></div></article>
        </div>
        <div className="composer-wrap"><div className="composer"><textarea aria-label="消息" placeholder="给助理交代一件事…" /><div className="composer-footer"><div className="composer-tools"><Paperclip size={16} /><span className="composer-model single"><Cpu size={14} />Qwen3.6 · 本地</span></div><div className="composer-actions"><button className="composer-send" aria-label="发送"><ArrowUp size={16} /></button></div></div></div></div>
      </section>
      {panel && <AssistantPanel name={profile.name} avatar={avatar} onEditProfile={() => {}} connected modelLabel="mimo-v2.6-pro" host={{ kind: "resident", name: "home-server" }} onAddResident={() => {}} onRetryResident={() => {}} tab={tab} onTab={setTab} onClose={() => setPanel(false)} waiting={feed.filter((item) => item.status === "waiting").length}>
        {tab === "audit" ? <AssistantAudit entries={audit} />
          : tab === "schedule" ? <AssistantSchedule items={schedule} />
          : tab === "identity" ? <AssistantIdentity profile={profile} avatar={avatar} onAvatar={(file) => void (file ? import("../src/lib/avatar").then(m => m.prepareAvatar(file)).then(setAvatar) : setAvatar(null))} memories={memories} onEdit={() => {}} onDeleteMemory={() => {}} />
          : <ActivityFeed items={feed} scope="all" onScope={() => {}} onOpen={(item) => item.session && setCurrent(item.session.replace(/^sid-/, ""))} />}
      </AssistantPanel>}
    </div></div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
