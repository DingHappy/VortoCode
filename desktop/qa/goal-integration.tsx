import { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { GatewayClient } from "../src/gateway";
import { AssistantGoals } from "../src/components/AssistantGoals";
import { AssistantWorkday } from "../src/components/AssistantWorkday";
import "../src/App.css";

function Integration() {
  const client = useMemo(() => new GatewayClient({ baseUrl: location.origin, token: "goal-integration-only" }), []);
  const [tab, setTab] = useState<"today" | "goals">("today"), [focus, setFocus] = useState<string>();
  const [shown, setShown] = useState(true), [notice, setNotice] = useState("");
  async function inject(mode: "lost" | "read-back" | "none") {
    const response = await fetch("/qa/fault", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ next_write: mode }) });
    setNotice(response.ok ? `下一次写入故障：${mode}` : "故障设置失败");
  }
  return <main style={{ maxWidth: 960, height: "100vh", overflowY: "auto", margin: "0 auto", padding: 16, boxSizing: "border-box" }}>
    <h3>目标 HTTP 联调</h3><p>当前源码组件和 GatewayClient 连接隔离的真实 HTTP 接口与临时磁盘存储；没有模型、通知或开发任务执行。</p>
    <nav>
      <button onClick={() => setTab("today")}>今日工作</button>
      <button onClick={() => { setFocus(undefined); setTab("goals"); }}>全部目标</button>
      <button onClick={() => setShown(value => !value)}>{shown ? "关闭面板" : "打开面板"}</button>
      <button onClick={() => void inject("lost")}>下一次写入回执故障</button>
      <button onClick={() => void inject("read-back")}>下一次提交后读取故障</button>
    </nav>
    <p id="qa-notice">{notice}</p>
    {shown && (tab === "today" ? <AssistantWorkday client={client} scope="isolated-http-qa" connected
      dispatches={[]} dispatchesLoaded codexActions={[]} onDispatch={() => {}}
      onGoals={id => { setFocus(id); setTab("goals"); }} onRefreshTasks={async () => {}} onCodexDecision={async () => {}} />
      : <AssistantGoals client={client} connected focusId={focus} onClearFocus={() => setFocus(undefined)}
          onFocusGoal={(id, owner) => { if (owner === client) setFocus(id); }} />)}
  </main>;
}

createRoot(document.getElementById("root")!).render(<Integration />);
