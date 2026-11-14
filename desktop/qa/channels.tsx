/** Real production components + draft hook, synthetic records/transport. No runtime or model calls. */
import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowUp } from "lucide-react";
import { AssistantSidebar } from "../src/components/AssistantMode";
import { ConversationHeader } from "../src/components/ConversationHeader";
import { ChannelWelcome, CreateChannelDialog } from "../src/components/SessionChannels";
import { ComposerCommands } from "../src/components/ComposerCommands";
import { SidebarBrand, SidebarSearch } from "../src/components/SidebarChrome";
import { useComposerDraft } from "../src/hooks/useComposerDraft";
import { desktopContext } from "../src/lib/desktopContext";
import { settingsTargetLease, type SettingsTargetLease } from "../src/lib/settingsTarget";
import { sessionChannel } from "../src/lib/sessionChannels";
import type { SessionSummary } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "../src/components/ConversationLocation.css";
import "./channels.css";

function Fixture() {
  const [source, setSource] = useState("A"), [sid, setSid] = useState("research"), [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [online, setOnline] = useState(true), [late, setLate] = useState(false), [notice, setNotice] = useState("");
  const [records, setRecords] = useState<Record<string, SessionSummary[]>>({ A: [
    { sid: "research", title: "资料研究", messages: 0, updated: 1, channel: { version: 1, topic: "收集公开资料，在这里继续讨论。" } },
    { sid: "product", title: "产品想法", messages: 0, updated: 1, channel: { version: 1, topic: "界面、体验与值得尝试的方向" } },
  ], B: [] });
  const current = desktopContext({ registrationId: source, assistantId: source, profiles: [{ id: source, kind: "remote", name: `个人助手 ${source}`,
    repoRoot: "@assistant", baseUrl: `https://${source.toLowerCase()}.example`, lastOpenedAt: 0 }], scope: "general", repoRoot: "", sid,
    baseUrl: `https://${source.toLowerCase()}.example`, runtimeId: "", connection: online ? "connected" : "disconnected", localResidentId: null,
    source: { registrationId: source, baseUrl: `https://${source.toLowerCase()}.example`, sid, epoch: 1, generation: 1 } });
  const state = useRef({ source, records, late }); state.current = { source, records, late };
  const client = useRef({ createChannel: async (name: string, topic: string) => {
    const captured = state.current;
    if (captured.records[captured.source].some(row => row.title.toLocaleLowerCase() === name.toLocaleLowerCase())) throw Error("已有同名频道，请换一个名称");
    const row: SessionSummary = { sid: crypto.randomUUID(), title: name, messages: 0, updated: Date.now() / 1000, channel: { version: 1, topic } };
    setRecords(previous => ({ ...previous, [captured.source]: [...previous[captured.source], row] }));
    if (captured.late) { setSource(captured.source === "A" ? "B" : "A"); await new Promise(resolve => setTimeout(resolve, 50)); }
    return row;
  } }).current;
  const target = useRef({ context: current, client }); target.current = { context: current, client };
  const [lease, setLease] = useState<SettingsTargetLease<typeof client> | null>(null);
  const draft = useComposerDraft(current, setNotice);
  const row = records[source].find(row => row.sid === sid), channel = sessionChannel(row);
  return <main className="channel-qa">
    <div className="channel-qa-tools"><span>合成界面验收 · 无真实服务 / 模型</span>
      <button onClick={() => setOnline(value => !value)}>{online ? "模拟断线" : "恢复连接"}</button>
      <button onClick={() => { setSource(source === "A" ? "B" : "A"); setSid("main"); }}>切换空间（测试）</button>
      <label><input type="checkbox" checked={late} onChange={event => setLate(event.target.checked)} />模拟创建时切换空间</label>
      <button onClick={() => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; }}>切换主题</button></div>
    <div className="workbench vc-mode-assistant inspector-closed">
      <aside className="sidebar vc-sidebar"><SidebarBrand onCollapse={() => setNotice("此夹具固定展示侧栏，正式应用仍使用原折叠入口")} /><div className="channel-qa-space">个人助手 {source} · 个人空间</div>
        <AssistantSidebar mainChat={{ sid: "main" }} current={sid} sideChats={[{ sid: "chat", title: "临时聊两句" }]}
          channels={records[source]} channelsDisabled={!online} onSelect={setSid} onNewSideChat={() => setSid("chat")}
          onNewChannel={() => setLease(settingsTargetLease(() => target.current))}
          onDeleteChannel={id => { if (window.confirm("删除这个合成频道及其对话记录？")) { setRecords(previous => ({ ...previous,
            [source]: previous[source].filter(row => row.sid !== id) })); if (id === sid) setSid("main"); } }}
          search={<SidebarSearch open={searchOpen} query={query} onQuery={setQuery} onOpenChange={setSearchOpen} placeholder="搜索聊天" />} query={query} /></aside>
      <section className={`conversation empty-state ${channel ? "conversation-channel" : ""}`}><ConversationHeader title={row?.title || (sid === "main" ? "主要聊天" : "临时聊两句")}
        channelTopic={channel?.topic} place={null} busy={false} waiting={0} panelOpen={false} plan={{ done: 0, total: 0 }}
        onTogglePanel={() => setNotice("此夹具只验收频道，原助理面板没有被替换")} onStop={() => {}} />
        <div className="conversation-body"><div className="conversation-scroll">{channel ? <ChannelWelcome title={row!.title} topic={channel.topic} />
          : <ChannelWelcome title={sid === "main" ? "主要聊天" : "临时聊两句"} topic="这里是原有的聊天入口" />}</div>
          <div className="composer-wrap"><div className="composer"><ComposerCommands prompt={draft.prompt} onPrompt={draft.setPromptState} project={false} disabled={!online}
            placeholder="在这里继续这个话题…" onSend={() => setNotice("合成夹具不向真实模型发送消息")} onAction={() => {}}>
            <div className="composer-footer"><span>独立对话 · 使用原有确认流程</span><button disabled={!online || !draft.prompt.trim()}
              onClick={() => setNotice("合成夹具不向真实模型发送消息")} aria-label="发送"><ArrowUp /></button></div></ComposerCommands></div></div>
          {notice && <p role="status">{notice}</p>}</div>
      </section></div>
    {lease && <CreateChannelDialog lease={lease} onClose={() => setLease(null)} onCreated={row => { setSid(row.sid); setLease(null); }} />}
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
