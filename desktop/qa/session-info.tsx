import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ComposerCommands } from "../src/components/ComposerCommands";
import { ContextStatus, SessionInfo } from "../src/components/SessionInfo";
import { BuildVersion } from "../src/components/BuildVersion";
import "../src/App.css";
const context = { used_tokens: 6000, max_tokens: 8000, pct: 75, context_window_tokens: 1000000, context_window_pct: .6, history_messages: 24, policy: "preserve", will_compact: true, compact_enabled: true };
function Fixture() {
  const [mode, setMode] = useState("idle"); const [width, setWidth] = useState(520); const [prompt, setPrompt] = useState(""); const [open, setOpen] = useState(false); const [action, setAction] = useState("");
  return <main style={{ padding: 20, height: "100%", overflow: "auto" }}><h1>原组件隔离验收（合成快照）</h1><BuildVersion />
    <p role="status">动作：{action}</p><button aria-label="切换窄输入框" onClick={() => setWidth(value => value === 520 ? 320 : 520)}>切换窄输入框</button><div>{["idle", "working", "confirm", "offline"].map(value => <button key={value} onClick={() => { setMode(value); setOpen(true); }}>{value}</button>)}</div><div style={{ display: "flex", flexWrap: "wrap", gap: 24 }}>
      <div className="composer" style={{ width, maxWidth: "100%", alignSelf: "flex-end", marginTop: 340 }}>
        <ComposerCommands prompt={prompt} onPrompt={setPrompt} project={false} disabled={false} onSend={() => setAction(`发送:${prompt}`)} onAction={id => { setAction(id); if (id === "info") setOpen(true); }}>
          <div className="composer-footer"><div className="composer-tools">📎</div><div className="composer-actions"><span className="composer-model single">Qwen3.6-35B-A3B-4bit</span><ContextStatus context={context} /><button className="composer-send" aria-label="发送夹具">↑</button></div></div>
        </ComposerCommands></div>
      {open && <aside style={{ width: 330, maxWidth: "100%" }}><button onClick={() => setOpen(false)}>关闭信息</button><SessionInfo context={context} busy={mode === "working" || mode === "confirm"} connected={mode !== "offline"} needsInput={mode === "confirm"} model="本地模型（夹具）" plan={mode === "working" || mode === "confirm" ? [{ step: "读取数据", status: "completed" }] : []} activities={mode === "working" ? [{ id: "activity", kind: "tool", status: "failed", label: "搜索网页" }] : []} queued={mode === "working" ? 1 : 0} onOpenSettings={() => setAction("连接设置")} onRefresh={() => setAction("刷新")} /></aside>}
    </div></main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
