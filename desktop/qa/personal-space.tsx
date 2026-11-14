/** Deterministic production components/hooks; no runtime, credential or model access. */
import { StrictMode, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { PersonalSpacePicker } from "../src/components/PersonalSpacePicker";
import { AssistantSidebar } from "../src/components/AssistantMode";
import { SidebarBrand } from "../src/components/SidebarChrome";
import { desktopContext } from "../src/lib/desktopContext";
import { useComposerDraft } from "../src/hooks/useComposerDraft";
import type { DesktopProjectProfile } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./personal-space.css";

const profiles: DesktopProjectProfile[] = ["a", "b"].map(id => ({ id, name: "我的助手", kind: "remote",
  repoRoot: "@assistant", baseUrl: "https://assistant.example", lastOpenedAt: 0 }));
function Fixture() {
  const [host, setHost] = useState("a"), [sid, setSid] = useState("main"), [generation, setGeneration] = useState(1);
  const [fail, setFail] = useState(false), [message, setMessage] = useState(""), [theme, setTheme] = useState("light");
  const sequence = useRef(0), recovery = useRef<(() => boolean) | null>(null);
  const context = desktopContext({ registrationId: host, assistantId: host, scope: "general", repoRoot: "", sid,
    baseUrl: profiles[0].baseUrl, runtimeId: "", connection: "connected", profiles, localResidentId: "a",
    source: { registrationId: host, baseUrl: profiles[0].baseUrl, sid, generation, epoch: 1 } });
  const composer = useComposerDraft(context, setMessage);
  const [sent, setSent] = useState<string[]>([]);
  return <div className="qa-space-shell" data-theme={theme}>
    <aside className="sidebar vc-sidebar">
      <SidebarBrand onCollapse={() => {}} />
      <PersonalSpacePicker context={context} profiles={profiles} localResidentId="a" connection="connected" switching={false}
        onSelect={async selected => {
          if (fail) throw Error("合成连接失败，未切换当前空间");
          setHost(selected.id); setSid("main"); setGeneration(value => value + 1);
        }} onManage={() => setMessage("已请求打开电脑与连接设置")} />
      <AssistantSidebar mainChat={{ sid: "main" }} sideChats={composer.draftChats.filter(chat => chat.sid !== "main")}
        current={sid} onSelect={setSid} onNewSideChat={() => setSid(`draft-${++sequence.current}`)} />
    </aside>
    <main className="qa-space-main"><h1>个人空间验收</h1><p>合成环境；不访问运行端、凭证或模型。</p>
      <p role="status">当前目标：{host} / {sid}</p>
      <label>消息草稿<textarea aria-label="消息草稿" value={composer.prompt} onChange={event => {
        composer.markComposerEdited(); composer.setPromptState(event.target.value);
      }} /></label>
      <label>添加附件<input type="file" aria-label="添加附件" onChange={event => {
        if (event.target.files) void composer.addFiles(event.target.files); event.target.value = "";
      }} /></label>
      <ul aria-label="当前草稿附件">{composer.attachments.map(item => <li key={item.id}>{item.name}
        <button onClick={() => composer.removeAttachment(item.id)}>移除 {item.name}</button></li>)}</ul>
      <div className="qa-space-controls"><button onClick={() => {
        setSent(items => [...items, `${host}/${sid}: ${composer.prompt} (${composer.attachments.map(item => item.name).join(", ")})`]);
        composer.takeAttachments(composer.attachments); composer.setPromptState("");
      }}>模拟发送</button><button onClick={() => {
        const text = composer.prompt, receipt = composer.takeAttachments(composer.attachments);
        composer.setPromptState("");
        recovery.current = () => {
          if (!composer.isCurrentAttachments() || !receipt?.restore()) return false;
          composer.setPromptState(text); return true;
        };
      }}>模拟延迟发送</button><button onClick={() => setMessage(recovery.current?.() ? "已恢复原草稿" : "旧请求已作废")}>回传旧失败</button>
      <button onClick={() => { const next = theme === "light" ? "dark" : "light"; setTheme(next); document.documentElement.dataset.theme = next; }}>切换主题</button></div>
      <label><input type="checkbox" checked={fail} onChange={event => setFail(event.target.checked)} />模拟切换失败</label>
      <p role="status">{message}</p><ul aria-label="发送记录">{sent.map((item, index) => <li key={index}>{item}</li>)}</ul>
    </main>
  </div>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);
