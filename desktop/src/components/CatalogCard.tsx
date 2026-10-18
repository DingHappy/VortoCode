// 插件页「精选目录」：审核过的免凭据 MCP 服务和通用技能，一键装进当前范围。
// 装 MCP 前用原生确认说清楚问题会发给谁；技能只在打开项目时可装（私人助理走个人技能）。
import { useEffect, useState } from "react";

import type { GatewayClient } from "../gateway";
import { confirmAction } from "../lib/confirm";
import { errorText } from "../lib/errorText";
import type { ConnectionState, ExtensionsCatalog, ProjectSkillScope, WorkspaceScope } from "../types";

export function CatalogCard({ client, connection, activeScope, onInstalled }: {
  client: GatewayClient | null; connection: ConnectionState; activeScope: WorkspaceScope; onInstalled?: () => void;
}) {
  const [catalog, setCatalog] = useState<ExtensionsCatalog | null>(null);
  const [scope, setScope] = useState<ProjectSkillScope>("local");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const ready = client !== null && connection === "connected";

  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    setError("");
    try {
      await action();
      if (client) setCatalog(await client.getExtensionsCatalog());
    } catch (failure) {
      setError(errorText(failure, "安装失败"));
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    setCatalog(null);
    if (ready) void run("load", async () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, connection]);

  return <section className="extensions-inspect-card" aria-label="精选目录">
    <div className="extensions-inspect-heading"><strong>精选目录</strong><span>审核过的 MCP 服务和技能</span></div>
    <p>MCP 服务都是免凭据的公开服务，实测能连上；装上后只在 external 会话里接入，每次调用工具仍要你确认。</p>
    {catalog?.mcp.map(item => (
      <div className="mcp-server-row" key={`mcp-${item.id}`}>
        <div>
          <strong>{item.name}</strong> <em>MCP</em>
          <small>{item.description}</small>
          <small>{item.privacy}</small>
        </div>
        <div className="mcp-server-actions">
          {item.installed ? <span>已安装</span> : <button disabled={!ready || busy !== ""} onClick={() => void (async () => {
            if (!await confirmAction(`安装 MCP 服务「${item.name}」？\n\n地址：${item.url}\n${item.privacy}\n\n装上后写进 config/mcp.yaml，只在 external 会话里接入，每次调用工具都要你确认。`)) return;
            await run(`mcp:${item.id}`, async () => {
              if (!client) return;
              await client.installCatalogMcp(item.id);
              setNotice(`已安装 ${item.name}，可以在上方「MCP 服务」里测试连接`);
              onInstalled?.();
            });
          })()}>{busy === `mcp:${item.id}` ? "安装中…" : "安装"}</button>}
        </div>
      </div>
    ))}
    {activeScope === "project" ? <>
      <div className="catalog-skill-scope">
        <span>技能装到</span>
        <select value={scope} onChange={event => setScope(event.target.value as ProjectSkillScope)}>
          <option value="local">只在本机（.vortocode/skills）</option>
          <option value="shared">随仓库（skills/，团队共享）</option>
        </select>
      </div>
      {catalog?.skills.map(item => (
        <div className="mcp-server-row" key={`skill-${item.id}`}>
          <div>
            <strong>{item.name}</strong> <em>技能</em>
            <small>{item.description}</small>
          </div>
          <div className="mcp-server-actions">
            {item.installed_scopes.includes(scope) ? <span>已安装</span> : <button disabled={!ready || busy !== ""} onClick={() => void run(`skill:${item.id}`, async () => {
              if (!client) return;
              await client.installCatalogSkill(item.id, scope);
              setNotice(`已安装技能「${item.name}」，新开的会话就能用；可以在「项目技能」里改成适合这个项目的写法`);
              onInstalled?.();
            })}>{busy === `skill:${item.id}` ? "安装中…" : "安装"}</button>}
          </div>
        </div>
      ))}
    </> : <p>技能需要先打开一个项目；私人助理请在「个人技能」里添加。</p>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
