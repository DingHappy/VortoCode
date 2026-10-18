// 插件页「MCP 服务」：每个服务实际会不会启动、为什么；增删启停免凭据 HTTP 服务、测试连接。
// 状态由引擎按连接时同一套筛选算出，这里只展示，不自己判断。
import { useEffect, useState } from "react";

import type { GatewayClient } from "../gateway";
import { confirmAction } from "../lib/confirm";
import { errorText } from "../lib/errorText";
import type { ConnectionState, McpServersSnapshot, McpServerTest } from "../types";

const STATUS = { available: "可用", connector: "连接器", blocked: "不会启动", disabled: "已关闭" } as const;

export function McpServersCard({ client, connection }: { client: GatewayClient | null; connection: ConnectionState }) {
  const [snapshot, setSnapshot] = useState<McpServersSnapshot | null>(null);
  const [tests, setTests] = useState<Record<string, McpServerTest>>({});
  const [draft, setDraft] = useState({ name: "", url: "" });
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const ready = client !== null && connection === "connected";

  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    setError("");
    try {
      await action();
      if (client) setSnapshot(await client.listMcpServers());
    } catch (failure) {
      setError(errorText(failure, "操作失败"));
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    setSnapshot(null);
    setTests({});
    if (ready) void run("load", async () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, connection]);

  const servers = snapshot?.servers ?? [];
  return <section className="extensions-inspect-card" aria-label="MCP 服务">
    <div className="extensions-inspect-heading">
      <strong>MCP 服务</strong>
      <span>{snapshot ? `${servers.filter(item => item.status === "available" || item.status === "connector").length} 个会连接 · ${snapshot.path}` : "读取中…"}</span>
    </div>
    {snapshot && servers.length === 0 && <p>还没有配置 MCP 服务。可以添加一个免凭据的 HTTP 服务。</p>}
    {servers.map(item => (
      <div className={`mcp-server-row ${item.status}`} key={item.name}>
        <div>
          <strong>{item.name}</strong> <em>{STATUS[item.status]}</em>
          <small>{item.transport} · {item.target || "—"}</small>
          <small>{item.reason}</small>
          {tests[item.name] && <small role="status">{tests[item.name].detail}{tests[item.name].tools.length ? `：${tests[item.name].tools.join("、")}` : ""}</small>}
        </div>
        <div className="mcp-server-actions">
          {item.status === "available" && <button disabled={!ready || busy !== ""} onClick={() => void run(`test:${item.name}`, async () => {
            if (!client) return;
            const result = await client.testMcpServer(item.name);
            setTests(previous => ({ ...previous, [item.name]: result }));
          })}>{busy === `test:${item.name}` ? "测试中…" : "测试连接"}</button>}
          <button disabled={!ready || busy !== ""} onClick={() => void run(`toggle:${item.name}`, async () => {
            if (client) await client.setMcpServerEnabled(item.name, !item.enabled);
          })}>{item.enabled ? "关闭" : "启用"}</button>
          <button disabled={!ready || busy !== ""} onClick={() => void (async () => {
            if (!await confirmAction(`从 ${snapshot?.path ?? "config/mcp.yaml"} 删除 MCP 服务「${item.name}」？`)) return;
            await run(`remove:${item.name}`, async () => { if (client) await client.removeMcpServer(item.name); });
          })()}>删除</button>
        </div>
      </div>
    ))}
    {adding ? (
      <div className="custom-provider-form">
        <label><span>名称</span>
          <input value={draft.name} placeholder="my-tools" maxLength={56} onChange={event => setDraft({ ...draft, name: event.target.value })} />
        </label>
        <label><span>地址</span>
          <input value={draft.url} placeholder="https://example.com/mcp" maxLength={500} onChange={event => setDraft({ ...draft, url: event.target.value })} />
        </label>
        <div>
          <button className="primary" disabled={!ready || busy !== "" || !draft.name.trim() || !draft.url.trim()} onClick={() => void run("add", async () => {
            if (!client) return;
            await client.addMcpServer(draft.name.trim(), draft.url.trim());
            setDraft({ name: "", url: "" });
            setAdding(false);
          })}>{busy === "add" ? "添加中…" : "添加"}</button>{" "}
          <button onClick={() => { setAdding(false); setError(""); }}>取消</button>
        </div>
      </div>
    ) : <button disabled={!ready} onClick={() => setAdding(true)}>添加 HTTP 服务</button>}
    {error && <p role="alert">{error}</p>}
    <p>只能添加不需要凭据的 HTTP 服务；stdio 服务会在这台电脑上执行命令，被安全策略禁用。外部 MCP 只在 external 会话里接入，每次调用工具仍要你确认。改动写进 {snapshot?.path ?? "config/mcp.yaml"}，项目里会出现在 git 改动中。</p>
  </section>;
}
