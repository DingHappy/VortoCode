/** Synthetic transport using the production read controller and component. */
import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { SessionResources } from "../src/components/SessionResources";
import { createResourceReads, emptyResourceView, type ResourceClient, type ResourceView, type SessionResources as Snapshot } from "../src/lib/sessionResources";
import "../src/App.css";
import "../src/daily.css";

const items: Snapshot["items"] = [
  { id: "browser", kind: "browser", title: "网站浏览器", state: "needs_human", approval: "each_action", target: "https://example.com", account: "工作", device: "", detail: "仅当前会话记录，恢复前仍核对站点和连接。", checked_at: null },
  { id: "android", kind: "device", title: "Android 设备", state: "not_attached", approval: "each_action", target: "", account: "", device: "", detail: "没有附加设备，不会自动发现或连接。", checked_at: null },
  { id: "mcp", kind: "service", title: "MCP 服务", state: "disconnected", approval: "tool_policy", target: "", account: "", device: "", detail: "尚未连接；工具安装与账号授权分别管理。", checked_at: null },
];
function Fixture() {
  const [view, setView] = useState<ResourceView>(emptyResourceView);
  const [host, setHost] = useState(0);
  const [connected, setConnected] = useState(true);
  const [calls, setCalls] = useState(0);
  const [message, setMessage] = useState("");
  const delayed = useRef(false), fail = useRef(false), late = useRef<Snapshot | null>(null);
  const source = useRef<{ client: ResourceClient | null; session: string; connected: boolean }>({ client: null, session: "one", connected: true });
  const clients = useRef<ResourceClient[]>([]);
  const [reader] = useState(() => createResourceReads(() => source.current, (_owner, state) => setView(state)));
  if (!clients.current.length) clients.current = [0, 1].map(number => ({ send: async event => {
    setCalls(value => value + 1);
    if (fail.current) throw Error("synthetic read failure");
    const reply: Snapshot = { version: 1, session: `sid-${source.current.session}`, nonce: String(event.resources),
      observed_at: new Date().toISOString(), status: "observed", truncated: false, attention_id: "c1",
      items: number === 0 ? items : [{ ...items[0], state: "idle", account: "常驻电脑标签", target: "" }, ...items.slice(1)] };
    if (delayed.current) late.current = reply; else reader.accept(reply);
  } }));
  source.current = { client: clients.current[host], session: "one", connected };
  return <main style={{ maxWidth: 700, padding: 16, margin: "20px auto" }}>
    <h1>会话资源状态验收</h1><p>合成数据，不读取凭证、不连接设备、不调用模型。</p>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      <button onClick={() => { const next = host ? 0 : 1; source.current = { ...source.current, client: clients.current[next] }; setHost(next); reader.reset(); }}>切换运行端</button>
      <button onClick={() => { source.current.connected = !connected; setConnected(!connected); if (connected) reader.offline(); }}>切换断连</button>
      <button onClick={() => reader.changed()}>出现新进展</button>
      <label><input type="checkbox" onChange={e => { delayed.current = e.target.checked; }}/>延迟回包</label>
      <label><input type="checkbox" onChange={e => { fail.current = e.target.checked; }}/>读取失败</label>
      <button onClick={() => { if (late.current) reader.accept(late.current); }}>回传旧请求</button>
    </div>
    <SessionResources view={view} connected={connected} hostLabel={host ? "已登记的常驻运行端" : "本机运行端"}
      pendingId="c1" onRefresh={() => void reader.load()} onFocusConfirmation={() => setMessage("已请求定位当前确认卡（夹具）")}
      onManage={() => setMessage("已请求打开原连接管理入口（夹具）")} />
    <p role="status">{message}</p><output>状态读取次数：{calls}</output>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
