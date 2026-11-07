import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { PanelLeftOpen, X } from "lucide-react";
import { AssistantPanel, AssistantSidebar, type AssistantTab } from "../src/components/AssistantMode";
import { SidebarBrand, SidebarSearch } from "../src/components/SidebarChrome";
import { ConversationHeader } from "../src/components/ConversationHeader";
import { ComposerCommands } from "../src/components/ComposerCommands";
import { IconButton } from "../src/components/ui";
import { useDialogSurface } from "../src/hooks/useDialogSurface";
import { useSidebarVisibility } from "../src/hooks/useSidebarVisibility";
import { appShortcut } from "../src/lib/appShortcuts";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./icon-details.css";

const labels: Record<AssistantTab, string> = { feed: "动态", goals: "目标", audit: "审核记录", schedule: "定时", identity: "身份" };
const listExamples = new URLSearchParams(location.search).has("list");
const searchExamples = listExamples || new URLSearchParams(location.search).has("search");
const sideChats = searchExamples
  ? [listExamples ? "本周交付与下一轮开发安排：核对原有计划和完整成果" : "整理工作清单", listExamples ? "   " : "会议纪要", "周报草稿", "发布计划", "采购清单", "界面反馈", "项目资料", "旅行安排"].map((title, index) => ({ sid: `side-${index}`, title }))
  : [{ sid: "side", title: "整理工作清单" }];
function ExampleDialog({ onClose }: { onClose: () => void }) {
  const dialog = useDialogSurface();
  const [editorShortcuts, setEditorShortcuts] = useState(0);
  return <dialog className="qa-icon-dialog" ref={dialog} aria-label="弹窗提示验收" onCancel={event => { event.preventDefault(); onClose(); }}>
    <header><h2>弹窗里的图标提示</h2><IconButton label="关闭弹窗" icon={<X/>} onClick={onClose}/></header>
    <p>提示应显示在弹窗上。首次 Escape 只收起提示，再按一次关闭弹窗。</p>
    <label>合成弹窗草稿<input aria-label="弹窗草稿" onKeyDown={event => {
      if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.altKey || event.shiftKey) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setEditorShortcuts(value => value + 1);
      }
    }} /></label>
    <output aria-label="编辑器快捷键计数">编辑器已处理 ⌘K：{editorShortcuts} 次；仅合成操作。</output>
  </dialog>;
}
function Fixture() {
  const [tab, setTab] = useState<AssistantTab>("feed"), [panel, setPanel] = useState(true);
  const sidebar = useSidebarVisibility();
  const [prompt, setPrompt] = useState(""), [search, setSearch] = useState(false), [query, setQuery] = useState("");
  const [searchFocus, setSearchFocus] = useState(0);
  const [current, setCurrent] = useState("main"), [currentTeam, setCurrentTeam] = useState("");
  const currentTitle = currentTeam ? "任务小组：修复登录" : current === "main" ? "主要聊天" : sideChats.find(chat => chat.sid === current)?.title.trim() || "新的旁聊";
  const [light, setLight] = useState(new URLSearchParams(location.search).has("light")), [narrow, setNarrow] = useState(new URLSearchParams(location.search).has("narrow"));
  const [notice, setNotice] = useState(""), [dialog, setDialog] = useState(false), [changes, setChanges] = useState(0);
  const [commands, setCommands] = useState(0), [sends, setSends] = useState(0);
  const [preparing, setPreparing] = useState(false);
  const first = useRef<HTMLButtonElement>(null);
  const panelToggle = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (appShortcut(event, document.querySelector("dialog[open]") !== null) !== "search") return;
      event.preventDefault(); sidebar.setCollapsed(false); setSearch(true); setSearchFocus(value => value + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  document.documentElement.dataset.theme = light ? "light" : "dark";
  return <main className={`qa-icons ${narrow ? "narrow" : ""}`}>
    <div className="qa-icon-controls"><strong>生产组件 · 图标细节验收</strong>
      <button ref={first} onClick={() => setLight(!light)}>切换{light ? "深色" : "浅色"}</button>
      <button onClick={() => setNarrow(!narrow)}>{narrow ? "恢复宽栏" : "窄栏 360"}</button>
      <button onClick={() => setDialog(true)}>检查弹窗提示</button>
      <button onClick={() => document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')?.focus()}>聚焦当前标签</button>
      <button onClick={() => panelToggle.current?.focus()}>聚焦面板开关</button>
      <button onClick={() => (sidebar.collapsed ? sidebar.reopenButton : sidebar.collapseButton).current?.focus()}>聚焦侧栏开关</button>
      <button onClick={() => setPreparing(!preparing)}>{preparing ? "结束准备状态" : "模拟准备状态"}</button>
      <button onClick={() => first.current?.focus()}>离开图标区域</button></div>
    <div className={`qa-icon-frame workbench vc-mode-assistant ${panel ? "panel-open" : ""} ${sidebar.collapsed ? "side-closed" : ""}`}>
      <aside id="qa-conversation-sidebar" className="sidebar vc-sidebar"><SidebarBrand sidebarId="qa-conversation-sidebar" collapseRef={sidebar.collapseButton} onCollapse={() => sidebar.toggle(true)}/>
        <AssistantSidebar mainChat={{ sid: "main", preview: "周报草稿已整理" }} sideChats={sideChats}
          phoneChats={searchExamples ? [{ sid: "phone", title: listExamples ? " 📱  " : "📱 手机备忘" }] : []}
          offlineChats={searchExamples ? [{ sid: "local", title: listExamples ? "   " : "本机备忘" }] : []}
          teams={searchExamples ? [{ id: "team", title: "修复登录", status: "review" }] : []}
          current={current} currentTeam={currentTeam} onOpenTeam={id => { setCurrentTeam(id); setNotice("仅切换合成小组入口"); }}
          waiting={2} onSelect={sid => { setCurrent(sid); setCurrentTeam(""); setNotice("仅切换合成会话入口"); }} onNewSideChat={() => setNotice("演示：新对话，未创建真实会话")}
          query={query} search={<SidebarSearch open={search} query={query} placeholder="搜索会话" focusRequest={searchFocus} onOpenChange={setSearch} onQuery={setQuery}/>}/>
      </aside>
      <section className="conversation">
        {sidebar.collapsed && <IconButton className="qa-sidebar-open" label="展开侧栏" icon={<PanelLeftOpen/>} buttonRef={sidebar.reopenButton}
          aria-expanded="false" aria-controls="qa-conversation-sidebar" onClick={() => sidebar.toggle(false)}/>}
        <ConversationHeader title={currentTitle} place={null} panelLabel="助理面板" panelOpen={panel} panelId="qa-assistant-panel" panelButtonRef={panelToggle}
          waiting={2} plan={{ done: 0, total: 0 }} busy={false}
          onTogglePanel={() => setPanel(!panel)} onStop={() => {}}/>
        <div className="conversation-scroll"><article className="message user"><div className="message-body">帮我看看这周有哪些事。</div></article>
          <article className="message assistant"><div className="message-body"><p>周报已有草稿，有两项决定等你处理。</p><p>点击右上角打开助理面板；图标获得焦点或悬停时会显示说明。</p></div></article></div>
        <div className="composer-wrap"><div className="composer"><ComposerCommands prompt={prompt} onPrompt={setPrompt} project={false} disabled={preparing}
          onAction={command => { setCommands(count => count + 1); setNotice(`仅导航演示：${command}`); }}
          onSend={() => { setSends(count => count + 1); setNotice("演示页面不发送消息"); }}/></div></div>
      </section>
      {panel && <AssistantPanel id="qa-assistant-panel" toggleRef={panelToggle} name="小V" connected host={{ kind: "resident", name: "常驻电脑" }} tab={tab}
        onTab={value => { setTab(value); setChanges(count => count + 1); }} onClose={() => setPanel(false)} waiting={2}
        onEditProfile={() => setNotice("只读演示：资料入口")} onAddResident={() => {}} onRetryResident={() => {}}>
        <section className="assistant-section"><h3>{labels[tab]}</h3><p>{tab === "identity" ? "资料、性格与记忆放在这里；无需指纹认证。" : tab === "goals" ? "本周周报：整理素材，下一步完成初稿。" : tab === "audit" ? "这里查看此前允许或拒绝的操作记录。" : tab === "schedule" ? "每天早上查看工作摘要，具体时间以计划为准。" : "两项决定等待处理；普通进展保持安静。"}</p>
          <label>合成草稿<input aria-label="面板草稿" placeholder="编辑时 Escape 不收起面板"/></label></section>
      </AssistantPanel>}
    </div>
    <output aria-label="交互验收量测">当前标签：{labels[tab]}；切换 {changes} 次；菜单动作 {commands} 次；合成发送 {sends} 次；真实发送与配置修改：0。{notice}</output>
    {dialog && <ExampleDialog onClose={() => setDialog(false)}/>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
