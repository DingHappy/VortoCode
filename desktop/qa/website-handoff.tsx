/** UI-only fixture. No website, browser launch, gateway, credentials or model. */
import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ConfirmCard } from "../src/components/ComposerPanels";
import { createConfirmationReply } from "../src/lib/confirm";
import type { PendingConfirmation } from "../src/types";
import type { WebsiteConfirmation } from "../src/lib/websiteConfirmation";
import "../src/App.css";
import "../src/daily.css";

function Fixture() {
  const seq = useRef(0);
  const [card, setCard] = useState<PendingConfirmation | null>(null);
  const [connected, setConnected] = useState(true);
  const [failSend, setFailSend] = useState(false);
  const [tainted, setTainted] = useState(false);
  const [calls, setCalls] = useState(0);
  const [status, setStatus] = useState("选择一个场景开始。这里的按钮与数据均为夹具。" );
  const owner = useRef({});
  const current = useRef({ client: owner.current as object | null, scope: "fixture", id: "", state: card?.responseState });
  current.current = { client: connected ? owner.current : null, scope: "fixture", id: card?.id ?? "", state: card?.responseState };
  const result = useRef(false);
  const fail = useRef(false); fail.current = failSend;
  const [reply] = useState(() => createConfirmationReply(
    () => current.current,
    async (_client, _id, ok) => {
      setCalls(value => value + 1); result.current = ok;
      if (fail.current) throw Error("synthetic response lost");
    },
    (id, responseState, responseError) => setCard(value => value?.id === id ? { ...value, responseState, responseError } : value)));
  const show = (action: WebsiteConfirmation["action"]) => {
    reply.close();
    setCard({ id: `website-${++seq.current}`, text: "合成请求：网站与账号标签只用于展示。恢复时运行端仍需核对站点、敏感表单与连接授权。",
      tainted, website: { version: 1, action, origin: "https://example.com", account_label: "工作", device_mode: "desktop" } });
    setStatus("等待当前会话的人工确认（夹具）。");
  };
  return <main style={{ maxWidth: 700, padding: 16, margin: "20px auto" }}>
    <h1>网站接管交互验收</h1><p>合成场景，不访问真实网站、凭证或模型。</p>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      <button onClick={() => show("connect")}>需要连接</button><button onClick={() => show("open")}>需要打开</button>
      <button onClick={() => show("login")}>需要人工登录 / 新用户消息</button>
      <button onClick={() => setConnected(value => !value)}>{connected ? "模拟断连" : "恢复连接"}</button>
      <label><input type="checkbox" checked={failSend} onChange={e => setFailSend(e.target.checked)}/>模拟发送结果不明</label>
      <label><input type="checkbox" checked={tainted} onChange={e => setTainted(e.target.checked)}/>下一张卡带外部内容警示</label>
    </div>
    {card && <ConfirmCard confirm={card} trust={null} trustBusy={false} connected={connected} onTrust={() => {}}
      onAnswer={ok => void reply.submit(card.id, ok)} onCheck={() => {
        reply.close(card.id); setCard(value => value ? { ...value, responseState: "idle", responseError: "夹具核对：运行端仍在等待，可重新选择。" } : value);
      }} />}
    {card?.responseState === "awaiting" && <div>
      <button onClick={() => { reply.close(card.id); setCard(null); setStatus(result.current
        ? "夹具：运行端核对通过，回到同一任务。该回执不代表业务验收完成。" : "夹具：保持暂停，不会自动再次询问。"); }}>模拟运行端回执</button>
      <button onClick={() => { reply.close(card.id); setCard(null); setStatus("夹具：仍在登录页，保持暂停。本轮不重复弹卡，需新用户消息后再请求核对。"); }}>模拟仍在登录页</button>
    </div>}
    <p role="status">{status}</p><output>批准 / 拒绝提交次数：{calls}</output>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
