import { useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { OwnerAccessSnapshot } from "../lib/ownerAccess";
import { approvalOutcome, domainStatus, originLabel, recordLabel, unavailableHistorySources } from "../lib/ownerAccess";
import { approvalCategory, approvalScope, approvalSource } from "../lib/approval";

export function OwnerAccessDetails({ value }: { value: OwnerAccessSnapshot }) {
  return <>
    <p>手机对话可查资料、更新自己的事项和记忆；命令、任意文件写入、操控电脑和直接对外发送不开放。</p>
    <p>普通操作：{value.permissions.effective_level === "reads" ? "可按已有授权免逐次确认" : "每次询问"}。读过外部内容后，已有授权失效，仍需你逐次确认。</p>
    <small>普通卡用日常钥匙，加强确认用 Face ID。这里仅查看规则；单次批准不扩大手机工具权限。</small>
    <p>本轮同类授权只在当时回合内生效，历史记录不会恢复授权。</p>
    <p>最近审批记录：有限读取，不含操作正文。汇总内核决定、App 回合卡片、Codex 动作和任务单；当前领域记录之外的归档尚未汇总。批准不代表执行完成。</p>
    {value.history.status !== "current" && <p role="status">审批记录来源待核对，当前列表可能不完整。</p>}
    {!!unavailableHistorySources(value).length && <p role="status">待核对来源：{unavailableHistorySources(value).join("、")}</p>}
    {!value.history.entries.length && <p>{value.history.status === "current" ? "最近读取范围内没有审批记录。" : "暂时无法确认审批记录。"}</p>}
    <ul className="assistant-audit">{value.history.entries.map(row => <li key={row.id}><span className="assistant-audit-text">
      <strong>{row.record_type === "dispatch" || row.record_type === "codex_action" ? recordLabel(row) : approvalCategory(row.operation_kind)} · {approvalOutcome(row.approval_outcome, row.decision)}</strong>
      <small>{approvalSource(row.decision_source)} · {approvalScope(row.approval_scope)}</small>
      <small>{recordLabel(row)} · {originLabel(row.origin)} · {row.ts || "审批时点待核对"}{row.tainted ? " · 读过外部内容后" : ""}</small>
      {row.decision_metadata_missing && <small>审批来源与时点没有完整记录，请人工核对。</small>}
      {domainStatus(row) && <small>当前来源状态：{domainStatus(row)}</small>}
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
