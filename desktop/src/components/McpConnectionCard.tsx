import { useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { ConnectionState, McpConnectionSnapshot } from "../types";

const labels = { blocked: "当前会话受限", disconnected: "尚未连接", connecting: "连接中", connected: "已接入", partial: "部分接入", error: "连接失败" };
export function McpConnectionCard({ client, session, connection }: { client: GatewayClient | null; session: string; connection: ConnectionState }) {
  const [state, setState] = useState<McpConnectionSnapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const pending = useRef(false);
  const source = useRef({ client, session, connection });
  source.current = { client, session, connection };
  async function load(connect = false) {
    if (!client || connection !== "connected" || pending.current) return;
    const id = ++generation.current;
    const current = () => id === generation.current && source.current.client === client && source.current.session === session && source.current.connection === connection;
    pending.current = true; setBusy(true); setError("");
    try {
      const result = connect ? await client.connectMcp(session) : await client.getMcpConnection(session);
      if (current()) setState(result);
    } catch (e) {
      if (current()) setError(e instanceof Error ? e.message : "连接状态读取失败，请重试。");
    } finally {
      if (current()) { pending.current = false; setBusy(false); }
    }
  }
  useEffect(() => {
    generation.current++; pending.current = false; setState(null); setError(""); setBusy(false);
    void load();
    return () => { generation.current++; };
    // Requests are bound to this client/session/connection generation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, session, connection]);
  return <section className="extensions-inspect-card" aria-label="MCP 连接">
    <div className="extensions-inspect-heading"><strong>MCP 连接</strong><span>{error ? "状态未确认" : state ? labels[state.status] : "状态未确认"}</span></div>
    <p>{state?.detail || "读取当前会话状态；不会调用模型或执行工具。"}</p>
    {state && <p>{state.tool_count} 个工具 · {state.observed_at ? `观察于 ${new Date(state.observed_at).toLocaleString()}` : "尚无连接观察时间"}</p>}
    {error && <p role="alert">{error}</p>}
    <button disabled={connection !== "connected" || busy} onClick={() => void load()}>{busy ? "检查中…" : "刷新状态"}</button>{" "}
    <button disabled={connection !== "connected" || busy || state?.status === "blocked"} onClick={() => void load(true)}>
      {state?.status === "disconnected" ? "连接已配置服务" : "重新连接"}
    </button>
    <p>连接只读取已启用配置和目录。任务运行时不能更新；连接成功不代表工具执行验收。</p>
  </section>;
}
