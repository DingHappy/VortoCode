import { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { McpConnectionCard } from "../src/components/McpConnectionCard";
import type { GatewayClient } from "../src/gateway";
import "../src/App.css";
function Fixture() {
  const [sid, setSid] = useState("a");
  const [calls, setCalls] = useState(0);
  const client = useMemo(() => {
    let attempts = 0;
    return {
      async getMcpConnection(session: string) {
        return { status: session === "blocked" ? "blocked" : "disconnected", detail: session === "blocked" ? "当前项目会话不允许外部 MCP" : "隔离目录检查", tool_count: 0, observed_at: null, profile: session === "blocked" ? "local" : "external" };
      },
      async connectMcp(session: string) {
        attempts++; setCalls(attempts);
        await new Promise((resolve) => setTimeout(resolve, 300));
        if (attempts === 1) throw new Error("隔离服务第一次失败，请重试");
        return { status: "connected", detail: `隔离服务 ${session}`, tool_count: 2, observed_at: new Date().toISOString(), profile: "external" };
      },
    } as unknown as GatewayClient;
  }, []);
  return <main style={{ maxWidth: 760, margin: "30px auto" }}>
    <p>隔离原组件；不写配置、不接入真实服务、不调用模型。</p>
    <button id="switch-session" onClick={() => setSid("b")}>切到会话B</button>{" "}
    <button id="blocked-session" onClick={() => setSid("blocked")}>切到受限会话</button>
    <output id="fixture-calls">{calls}</output>
    <McpConnectionCard client={client} session={sid} connection="connected" />
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
