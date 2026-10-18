// 插件页「项目技能」：在 Desktop 里新建、查看、编辑、删除项目的 SKILL.md。
// shared 写 skills/（随仓库，出现在 git 改动里），local 写 .vortocode/skills/（只在本机）。
// 编辑只改名称、描述和正文，frontmatter 其它字段由引擎原样保留；版本对不上会被拒绝并要求刷新。
import { useEffect, useState } from "react";

import type { GatewayClient } from "../gateway";
import { confirmAction } from "../lib/confirm";
import { errorText } from "../lib/errorText";
import type { ConnectionState, ProjectSkill, ProjectSkillScope, ProjectSkillsSnapshot } from "../types";

const SCOPE_LABEL: Record<ProjectSkillScope, string> = { shared: "随仓库", local: "只在本机" };
type Draft = { id: string; scope: ProjectSkillScope; name: string; description: string; instructions: string; revision: string | null };
const blank: Draft = { id: "", scope: "local", name: "", description: "", instructions: "", revision: null };

export function ProjectSkillsCard({ client, connection }: { client: GatewayClient | null; connection: ConnectionState }) {
  const [snapshot, setSnapshot] = useState<ProjectSkillsSnapshot | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const ready = client !== null && connection === "connected";

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
      if (client) setSnapshot(await client.listProjectSkills());
    } catch (failure) {
      setError(errorText(failure, "操作失败"));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    setSnapshot(null);
    setDraft(null);
    if (ready) void run(async () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, connection]);

  async function edit(item: ProjectSkill) {
    await run(async () => {
      if (!client) return;
      const full = await client.getProjectSkill(item.scope, item.id);
      setDraft({ id: full.id, scope: full.scope, name: full.name, description: full.description,
        instructions: full.instructions ?? "", revision: full.revision });
    });
  }

  const items = snapshot?.items ?? [];
  const where = (scope: ProjectSkillScope) => snapshot?.locations[scope] ?? (scope === "shared" ? "skills" : ".vortocode/skills");
  return <section className="extensions-inspect-card" aria-label="项目技能">
    <div className="extensions-inspect-heading">
      <strong>项目技能</strong>
      <span>{snapshot ? `${items.length} 个` : "读取中…"}</span>
    </div>
    {snapshot && items.length === 0 && !draft && <p>这个项目还没有技能。技能是一段可复用的流程指令，agent 需要时加载。</p>}
    {!draft && items.map(item => (
      <div className="mcp-server-row" key={`${item.scope}/${item.id}`}>
        <div>
          <strong>{item.name}</strong> <em>{SCOPE_LABEL[item.scope]}</em>
          <small>{item.description || "（没有描述）"}</small>
          <small>{where(item.scope)}/{item.id}/SKILL.md{item.model_invocable ? "" : " · 禁止模型自动调用"}</small>
        </div>
        <div className="mcp-server-actions">
          <button disabled={!ready || busy} onClick={() => void edit(item)}>编辑</button>
          <button disabled={!ready || busy} onClick={() => void (async () => {
            if (!await confirmAction(`删除技能「${item.name}」（${where(item.scope)}/${item.id}/SKILL.md）？同目录的其它文件会保留。`)) return;
            await run(async () => { if (client) await client.deleteProjectSkill(item.scope, item.id, item.revision); });
          })()}>删除</button>
        </div>
      </div>
    ))}
    {snapshot?.issues.map(issue => <p key={issue}>⚠ {issue}</p>)}
    {draft ? (
      <div className="custom-provider-form">
        {draft.revision === null && <>
          <label><span>ID</span>
            <input value={draft.id} placeholder="release-notes" maxLength={48} onChange={event => setDraft({ ...draft, id: event.target.value })} />
          </label>
          <label><span>放在</span>
            <select value={draft.scope} onChange={event => setDraft({ ...draft, scope: event.target.value as ProjectSkillScope })}>
              <option value="local">只在本机（{where("local")}，不入库）</option>
              <option value="shared">随仓库（{where("shared")}，团队共享）</option>
            </select>
          </label>
        </>}
        <label><span>名称</span>
          <input value={draft.name} maxLength={80} onChange={event => setDraft({ ...draft, name: event.target.value })} />
        </label>
        <label><span>描述</span>
          <input value={draft.description} maxLength={300} placeholder="一句话说清什么时候用" onChange={event => setDraft({ ...draft, description: event.target.value })} />
        </label>
        <label><span>流程指令</span>
          <textarea rows={10} maxLength={60000} value={draft.instructions} onChange={event => setDraft({ ...draft, instructions: event.target.value })} />
        </label>
        <div>
          <button className="primary" disabled={!ready || busy || !draft.id.trim() || !draft.name.trim() || !draft.instructions.trim()} onClick={() => void run(async () => {
            if (!client) return;
            await client.saveProjectSkill(draft.scope, draft.id.trim(), { name: draft.name, description: draft.description,
              instructions: draft.instructions, revision: draft.revision });
            setNotice(`已保存「${draft.name}」；新开的会话才会用到改动`);
            setDraft(null);
          })}>{busy ? "保存中…" : "保存"}</button>{" "}
          <button onClick={() => { setDraft(null); setError(""); }}>取消</button>
        </div>
      </div>
    ) : <button disabled={!ready} onClick={() => { setNotice(""); setDraft({ ...blank }); }}>新建技能</button>}
    {notice && !draft && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
    <p>技能只是指令文本，不执行脚本、不增加工具权限。编辑时 frontmatter 里的其它字段（版本、参数等）原样保留。</p>
  </section>;
}
