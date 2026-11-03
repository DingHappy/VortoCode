import { useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { OwnerAccessSnapshot } from "../lib/ownerAccess";
import { approvalOutcome } from "../lib/ownerAccess";
import { approvalCategory, approvalSource } from "../lib/approval";

export function OwnerAccessDetails({ value }: { value: OwnerAccessSnapshot }) {
  return <>
    <p>手机对话可查资料、更新自己的事项和记忆；命令、任意文件写入、操控电脑和直接对外发送不开放。</p>
    <p>普通操作：{value.permissions.effective_level === "reads" ? "可按已有授权免逐次确认" : "每次询问"}。读过外部内容后，已有授权失效，仍需你逐次确认。</p>
    <small>普通卡用日常钥匙，加强确认用 Face ID。这里仅查看规则；单次批准不扩大手机工具权限。</small>
    <p>最近审批记录：有限读取，不含操作正文。目前展示内核决定与 App 回合卡片，不包含任务单、Codex 动作等全部领域记录。批准不代表执行完成。</p>
    {value.history.status !== "current" && <p role="status">审批记录来源待核对，当前列表可能不完整。</p>}
    {!value.history.entries.length && <p>{value.history.status === "current" ? "最近读取范围内没有审批记录。" : "暂时无法确认审批记录。"}</p>}
    <ul className="assistant-audit">{value.history.entries.map(row => <li key={row.id}><span className="assistant-audit-text">
      <strong>{approvalCategory(row.operation_kind)} · {approvalOutcome(row.approval_outcome, row.decision)}</strong>
      <small>{approvalSource(row.decision_source)} · {row.approval_scope === "once" ? "仅本次" : row.approval_scope === "existing_trust" ? "既有授权范围" : row.approval_scope === "none" ? "无新增授权" : "范围待核对"}</small>
      <small>{row.record_type === "card" ? "卡片处理" : "内核决定"} · {row.origin === "app" ? "App" : "助理运行端"} · {row.ts}{row.tainted ? " · 读过外部内容后" : ""}</small>
    </span></li>)}</ul>
    <small>读取时间：{value.generated_at}</small>
  </>;
}

/** 显式读取；连接/客户端变化或更新请求会使旧响应失效。 */
export function OwnerAccess({ client, connected }: {
  client: Partial<Pick<GatewayClient, "ownerAccess">> | null | undefined; connected: boolean;
}) {
  const [reading, setReading] = useState<{ client: typeof client; generation: number; value?: OwnerAccessSnapshot; error?: string; loading?: boolean }>();
  const state = useRef({ client, connected, generation: 0 });
  if (state.current.client !== client || state.current.connected !== connected) state.current = { client, connected, generation: state.current.generation + 1 };
  const visible = reading?.client === client && connected && reading?.generation === state.current.generation ? reading : undefined;
  async function refresh() {
    if (!client?.ownerAccess || !connected) return;
    const generation = ++state.current.generation;
    const current = () => state.current.client === client && state.current.connected && state.current.generation === generation;
    setReading({ client, generation, loading: true });
    try { const value = await client.ownerAccess(); if (current()) setReading({ client, generation, value }); }
    catch { if (current()) setReading({ client, generation, error: "权限与审批记录暂时读不到，请刷新核对。" }); }
  }
  if (!client?.ownerAccess) return null;
  return <section className="assistant-section" aria-label="手机权限与审批记录"><details>
    <summary>手机权限与审批记录</summary>
    <button disabled={!connected || visible?.loading} onClick={() => void refresh()}>{visible?.loading ? "正在核对…" : "核对权限与记录"}</button>
    {!connected && <p role="status">连接中断，权限与记录尚未核对。</p>}
    {visible?.error && <p role="status">{visible.error}</p>}
    {visible?.value && <OwnerAccessDetails value={visible.value} />}
  </details></section>;
}
