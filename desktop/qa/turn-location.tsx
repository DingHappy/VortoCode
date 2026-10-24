import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ChatHistory } from "../src/components/ChatHistory";
import { ConversationLocation } from "../src/components/ConversationLocation";
import { ActivityRecordDialog } from "../src/components/ActivityRecordDialog";
import { PhoneChatDialog } from "../src/components/PhoneChatDialog";
import { useConversationLocation } from "../src/hooks/useConversationLocation";
import { hydrateActivities } from "../src/protocol/activities";
import type { ConversationMessage } from "../src/types";
import type { FeedItem } from "../src/lib/activityFeed";
import "../src/App.css";
import "../src/daily.css";
import "./turn-location.css";

const clients = { a: {}, b: {} };
const activityGroups = new URLSearchParams(window.location.search).get("history") === "status"
  ? new Map([["turn-12", hydrateActivities([
    { type: "agent_phase", id: "unknown", rid: "turn-12", label: "旧阶段缺少状态" },
    { type: "agent_tool", id: "unfinished", rid: "turn-12", name: "write_file", summary: "只保存了写入开始记录", status: "running", args: { path: "unconfirmed.txt" } },
    { type: "agent_tool", id: "written", rid: "turn-12", name: "write_file", summary: "保存文件的工具记录", status: "completed", args: { path: "recorded.txt" } },
    { type: "agent_tool", id: "timeout", rid: "turn-12", name: "run_tests", summary: "读取测试结果超时", status: "timed_out", error: "合成：没有取得测试结果。" },
  ], { missingStatus: "unknown" })]]) : new Map();
const history = (sid: string): ConversationMessage[] => Array.from({ length: 40 }, (_, index) => [
  { id: `${sid}-u-${index}`, rid: `turn-${index}`, role: "user" as const, text: `${sid === "a" ? "原会话" : "另一个运行环境"}事项 ${index + 1}：核对交付依据。` },
  { id: `${sid}-a-${index}`, rid: `turn-${index}`, role: "assistant" as const, text: "合成记录：过程和结果已经记录，仍需要你核对实际交付。\n\n这是一份用于滚动与定位验收的记录，不代表真实任务执行。" },
]).flat();
const detailClient = { getAgentActivityDetail: async (sid: string, rid: string) => ({
  sid, rid, source: "session_archive" as const, request: "原会话事项 13：核对交付依据。", reply: "合成结果，可打开原对话查看上下文。", events: [], has_reply: true, truncated: false,
}) };

function Fixture() {
  const [sid, setSid] = useState<"a" | "b">("a");
  const [messages, setMessages] = useState(() => history("a"));
  const [selected, setSelected] = useState<FeedItem | null>(null);
  const [phone, setPhone] = useState<{ rid?: string } | null>(null);
  useEffect(() => {
    if (!phone || new URLSearchParams(window.location.search).get("phone") !== "updates") return;
    const timer = setTimeout(() => setMessages(old => [...old, { id: `phone-update-${old.length}`, rid: "phone-live-update",
      role: "assistant", text: "手机记录已更新（合成）；阅读原消息时不应被拉到最新。" }]), 2500);
    return () => clearTimeout(timer);
  }, [phone]);
  const container = useRef<HTMLDivElement>(null), autoScroll = useRef(true);
  const location = useConversationLocation({ client: clients[sid], sid, messages, container, autoScroll });
  useEffect(() => {
    if (!autoScroll.current || location.rid) return;
    const frame = requestAnimationFrame(() => { if (container.current) container.current.scrollTop = container.current.scrollHeight; });
    return () => cancelAnimationFrame(frame);
  }, [messages, location.rid]);
  const item: FeedItem = { id: "turn:a:turn-12", title: "核对第13件事项", session: sid, rid: "turn-12", status: "done", statusLabel: "已回复", summary: "合成结果", at: "" };
  return <main className="location-qa"><header><span>原对话定位验收 · 合成数据 · 当前环境 {sid.toUpperCase()}</span>
    <button onClick={() => setSelected(item)}>打开动态详情</button>
    <button onClick={() => setSelected({ ...item, session: "telegram-qa", title: "手机第13件事项" })}>打开手机动态详情</button>
    <button onClick={() => setPhone({})}>打开手机最近聊天</button>
    <button onClick={() => setPhone({ rid: "missing" })}>打开手机缺失消息</button>
    <button onClick={() => location.locate({ client: clients[sid], sid, rid: "missing" })}>定位缺失记录</button>
    <button onClick={() => setMessages(old => [{ id: "missing", rid: "missing", role: "user", text: "刚补回的缺失记录（合成）" }, ...old])}>载入缺失记录</button>
    <button onClick={() => { const next = sid === "a" ? "b" : "a"; setSid(next); setMessages(history(next)); }}>切换运行环境</button>
    <button onClick={() => setMessages(old => [...old, { id: `new-${old.length}`, role: "assistant", rid: "latest", text: "新到的合成回复，应保持当前阅读位置。" }])}>模拟新回复</button>
    <button onClick={() => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; }}>切换主题</button>
    </header><section className="conversation"><div className="conversation-header"><strong>当前对话（合成）</strong></div>
      <div className="conversation-body">
        <ConversationLocation status={location.status} onLatest={location.latest} onRetry={location.retry} />
      <div ref={container} className="conversation-scroll" tabIndex={-1}>
        <ChatHistory messages={messages} activityGroups={activityGroups} busy={false} activeTurnRid={null}
          assistant name="VortoCode" avatar={null} focusedRid={location.rid} />
      </div></div><p className="location-qa-note">只使用本地合成消息，不连接模型、远端桌面或真实会话。</p></section>
    {selected && <ActivityRecordDialog item={selected} client={detailClient} onClose={() => setSelected(null)}
      onNavigate={() => selected.session === "telegram-qa" ? setPhone({ rid: selected.rid }) : location.locate({ client: clients[sid], sid, rid: selected.rid! })} />}
    {phone && <PhoneChatDialog chat={{ sid: "telegram-qa", title: "📱 手机验收（合成）",
      transcript: new URLSearchParams(window.location.search).get("phone") === "empty" ? [] : messages }}
      owner={clients[sid]} focusedRid={phone.rid} onClose={() => setPhone(null)} />}
  </main>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);
