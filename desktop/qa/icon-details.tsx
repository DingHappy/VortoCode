import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { PanelLeftOpen, X } from "lucide-react";
import { AssistantPanel, AssistantSidebar, type AssistantTab } from "../src/components/AssistantMode";
import { SidebarBrand, SidebarSearch } from "../src/components/SidebarChrome";
import { ConversationHeader } from "../src/components/ConversationHeader";
import { ComposerCommands } from "../src/components/ComposerCommands";
import { IconButton } from "../src/components/ui";
import { useDialogSurface } from "../src/hooks/useDialogSurface";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./icon-details.css";

const labels: Record<AssistantTab, string> = { feed: "动态", goals: "目标", audit: "审核记录", schedule: "定时", identity: "身份" };
function ExampleDialog({ onClose }: { onClose: () => void }) {
  const dialog = useDialogSurface();
  return <dialog className="qa-icon-dialog" ref={dialog} aria-label="弹窗提示验收" onCancel={event => { event.preventDefault(); onClose(); }}>
    <header><h2>弹窗里的图标提示</h2><IconButton label="关闭弹窗" icon={<X/>} onClick={onClose}/></header>
    <p>提示应显示在弹窗上。首次 Escape 只收起提示，再按一次关闭弹窗。</p>
  </dialog>;
}
function Fixture() {
  const [tab, setTab] = useState<AssistantTab>("feed"), [panel, setPanel] = useState(true), [side, setSide] = useState(true);
  const [prompt, setPrompt] = useState(""), [search, setSearch] = useState(false), [query, setQuery] = useState("");
  const [light, setLight] = useState(new URLSearchParams(location.search).has("light")), [narrow, setNarrow] = useState(new URLSearchParams(location.search).has("narrow"));
  const [notice, setNotice] = useState(""), [dialog, setDialog] = useState(false), [changes, setChanges] = useState(0);
  const first = useRef<HTMLButtonElement>(null);
  document.documentElement.dataset.theme = light ? "light" : "dark";
  return <main className={`qa-icons ${narrow ? "narrow" : ""}`}>
    <div className="qa-icon-controls"><strong>生产组件 · 图标细节验收</strong>
      <button ref={first} onClick={() => setLight(!light)}>切换{light ? "深色" : "浅色"}</button>
      <button onClick={() => setNarrow(!narrow)}>{narrow ? "恢复宽栏" : "窄栏 360"}</button>
      <button onClick={() => setDialog(true)}>检查弹窗提示</button>
      <button onClick={() => document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')?.focus()}>聚焦当前标签</button>
      <button onClick={() => first.current?.focus()}>离开图标区域</button></div>
    <div className={`qa-icon-frame workbench vc-mode-assistant ${panel ? "panel-open" : ""} ${side ? "" : "side-closed"}`}>
      {side && <aside className="sidebar vc-sidebar"><SidebarBrand onCollapse={() => setSide(false)}/>
        <AssistantSidebar mainChat={{ sid: "main", preview: "周报草稿已整理" }} sideChats={[{ sid: "side", title: "整理工作清单" }]}
          current="main" waiting={2} onSelect={() => setNotice("只读演示：会话入口")} onNewSideChat={() => setNotice("演示：新对话，未创建真实会话")}
          search={<SidebarSearch open={search} query={query} placeholder="搜索会话" onOpenChange={setSearch} onQuery={setQuery}/>}/>
      </aside>}
      <section className="conversation">
        {!side && <IconButton className="qa-sidebar-open" label="展开侧栏" icon={<PanelLeftOpen/>} onClick={() => setSide(true)}/>}
        <ConversationHeader title="主要聊天" place={null} panelLabel="助理面板" panelOpen={panel} waiting={2} plan={{ done: 0, total: 0 }} busy={false}
          onTogglePanel={() => setPanel(!panel)} onStop={() => {}}/>
        <div className="conversation-scroll"><article className="message user"><div className="message-body">帮我看看这周有哪些事。</div></article>
          <article className="message assistant"><div className="message-body"><p>周报已有草稿，有两项决定等你处理。</p><p>点击右上角打开助理面板；图标获得焦点或悬停时会显示说明。</p></div></article></div>
        <div className="composer-wrap"><div className="composer"><ComposerCommands prompt={prompt} onPrompt={setPrompt} project={false} disabled={false}
          onAction={command => setNotice(`仅导航演示：${command}`)} onSend={() => setNotice("演示页面不发送消息")}/></div></div>
      </section>
      {panel && <AssistantPanel name="小V" connected host={{ kind: "resident", name: "常驻电脑" }} tab={tab}
        onTab={value => { setTab(value); setChanges(count => count + 1); }} onClose={() => setPanel(false)} waiting={2}
        onEditProfile={() => setNotice("只读演示：资料入口")} onAddResident={() => {}} onRetryResident={() => {}}>
        <section className="assistant-section"><h3>{labels[tab]}</h3><p>{tab === "identity" ? "资料、性格与记忆放在这里；无需指纹认证。" : tab === "goals" ? "本周周报：整理素材，下一步完成初稿。" : tab === "audit" ? "这里查看此前允许或拒绝的操作记录。" : tab === "schedule" ? "每天早上查看工作摘要，具体时间以计划为准。" : "两项决定等待处理；普通进展保持安静。"}</p></section>
      </AssistantPanel>}
    </div>
    <output aria-label="交互验收量测">当前标签：{labels[tab]}；切换 {changes} 次；真实发送与配置修改：0。{notice}</output>
    {dialog && <ExampleDialog onClose={() => setDialog(false)}/>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
