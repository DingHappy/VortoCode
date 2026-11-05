import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { SessionCapabilities } from "../src/components/SessionCapabilities";
import { ComposerCommands } from "../src/components/ComposerCommands";
import { SessionResources } from "../src/components/SessionResources";
import { createResourceReads, emptyResourceView, type ResourceClient, type ResourceView, type SessionResources as Snapshot } from "../src/lib/sessionResources";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";
import "./session-capabilities.css";

const resource: Snapshot["items"][number] = { id: "browser", kind: "browser", title: "网站浏览器", state: "idle", approval: "each_action", target: "", account: "", device: "", detail: "已装配，交互动作仍需确认；未检查网站登录状态。", checked_at: null };
const inventory = { version: 1 as const, truncated: false, items: [
  { name: "web_search", read_only: true, required_capabilities: ["external_content"] },
  { name: "web_fetch", read_only: true, required_capabilities: ["external_content"] },
  { name: "edit_file", read_only: false, required_capabilities: ["host_process"] },
  { name: "team_propose", read_only: false, required_capabilities: [] },
  { name: "save_memory", read_only: false, required_capabilities: [] },
  { name: "mcp__demo__list", read_only: null, required_capabilities: null },
] };

function Fixture() {
  const query = new URLSearchParams(location.search);
  const [mode, setMode] = useState(query.get("mode") || "current"), [open, setOpen] = useState(false);
  const [connected, setConnected] = useState(true), [host, setHost] = useState(0), [prompt, setPrompt] = useState("");
  const [view, setView] = useState<ResourceView>(emptyResourceView), [reads, setReads] = useState(0), [notice, setNotice] = useState("");
  const [light, setLight] = useState(query.has("light"));
  const [narrow, setNarrow] = useState(query.has("narrow"));
  const modeRef = useRef(mode); modeRef.current = mode;
  const source = useRef<{ client: ResourceClient | null; session: string; connected: boolean }>({ client: null, session: "one", connected: true });
  const clients = useRef<ResourceClient[]>([]);
  const [reader] = useState(() => createResourceReads(() => source.current, (_source, next) => setView(next)));
  if (!clients.current.length) clients.current = [0, 1].map(number => ({ send: async event => {
    setReads(value => value + 1);
    if (modeRef.current === "error") throw new Error("synthetic read failure");
    const raw: Snapshot = { version: 1, session: `sid-${source.current.session}`, nonce: String(event.resources), observed_at: new Date().toISOString(),
      status: modeRef.current === "unloaded" ? "not_loaded" : "observed", truncated: false, items: modeRef.current === "unloaded" ? [] : [{ ...resource, title: number ? "常驻端浏览器" : resource.title }],
      ...(modeRef.current === "legacy" || modeRef.current === "unloaded" ? {} : { tool_inventory: { ...inventory, truncated: modeRef.current === "partial" } }) };
    reader.accept(raw);
  } }));
  source.current = { client: clients.current[host], session: "one", connected };
  document.documentElement.dataset.theme = light ? "light" : "dark";
  const show = () => { setOpen(true); void reader.load(); };
  const close = () => setOpen(false);
  return <main className={`qa-capabilities ${narrow ? "narrow" : ""}`}>
    <h1>能力视图 · 合成验收</h1><p>真实组件与读取控制器，不调用模型或工具，不启用权限。</p>
    <div className="qa-capabilities-controls">
      <label>运行端回包 <select aria-label="运行端回包" value={mode} onChange={event => { setMode(event.target.value); reader.reset(); }}>
        <option value="current">当前格式</option><option value="legacy">旧端，无工具清单</option><option value="partial">工具清单不完整</option><option value="unloaded">会话未初始化</option><option value="error">读取失败</option>
      </select></label>
      <button onClick={() => { setLight(!light); }}>切换{light ? "深色" : "浅色"}</button>
      <button onClick={() => setNarrow(!narrow)}>{narrow ? "恢复宽栏" : "窄栏 360"}</button>
      <button onClick={() => { source.current = { ...source.current, client: clients.current[host ? 0 : 1] }; setHost(host ? 0 : 1); reader.reset(); }}>切换运行端</button>
      <button onClick={() => { source.current.connected = !connected; setConnected(!connected); if (connected) reader.offline(); }}>切换断连</button>
      <button onClick={() => reader.changed()}>标记记录过期</button>
      <button onClick={() => setView({ snapshot: { version: 1, session: "sid-one", nonce: "seed", observed_at: new Date().toISOString(), status: "observed", truncated: false, items: [{ ...resource, state: "needs_human" }] }, busy: false, stale: false, error: "" })}>显示需核对入口</button>
    </div>
    <section className="qa-capabilities-composer"><ComposerCommands prompt={prompt} onPrompt={setPrompt} project={false} disabled={false} onAction={command => command === "capabilities" ? show() : setNotice(`仅导航：${command}`)} onSend={() => setNotice("合成夹具，不发送消息")}/></section>
    <button onClick={show}>查看此对话能力</button>
    <SessionResources view={view} connected={connected} quietWhenOk hostLabel={host ? "常驻测试端" : "本机测试端"} onRefresh={() => void reader.load()} onManage={() => setNotice("仅导航管理连接")} onFocusConfirmation={() => {}} />
    {open && <SessionCapabilities view={view} connected={connected} hostLabel={host ? "常驻测试端" : "本机测试端"} onClose={close} onRefresh={() => void reader.load()}
      onManage={() => setNotice("仅导航管理连接")} onLocalSettings={() => setNotice("仅导航这台 Mac 设置")} onFocusConfirmation={() => {}} />}
    <p role="status">{notice}</p><output aria-label="只读验收量测">状态读取：{reads}；工具执行、设备连接、模型调用：0</output>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
