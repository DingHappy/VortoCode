import { useEffect, useMemo, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import { residentComputerReads, type ComputerRead } from "../lib/residentComputer";

const PASSWORD = { needs_setup: "需要首次设置", locked: "需要你解锁", unlocked: "已解锁", unavailable: "暂不可用", unknown: "尚未确认" };

export function ResidentComputerSummary({ source, connected, resident, busy, now, onRefresh }: {
  source: ComputerRead; connected: boolean; resident: boolean; busy: boolean; now: number; onRefresh: () => void;
}) {
  const age = source.checkedAt === undefined ? Infinity : now - source.checkedAt;
  const current = connected && source.status === "current" && age >= 0 && age < 60_000;
  const data = source.data;
  const title = !connected ? "运行端已断线" : source.status === "unsupported" ? "运行端尚未提供状态"
    : !current ? source.status === "loading" ? "正在核对运行端" : "状态待刷新"
    : data?.state === "ready" ? "桌面组件就绪" : data?.state === "not_installed" ? "尚未安装常驻桌面"
    : data?.state === "not_ready" ? "桌面组件未就绪" : "桌面状态暂不可用";
  return <section className="resident-computer-card" aria-label="电脑执行端">
    <div className="resident-computer-heading"><strong>电脑执行端</strong>
      <button onClick={onRefresh} disabled={!connected || busy} aria-label="刷新电脑执行端">{busy ? "核对中…" : "刷新状态"}</button></div>
    <p>{resident ? "执行位置：常驻电脑" : "当前连接：本机助理"} · {title}</p>
    {current && data && <p>密码库：{PASSWORD[data.password_store]} · {data.control_mode === "human" ? "人工控制"
      : data.control_mode === "agent" ? "助手已获桌面控制权" : "控制权待确认"}
      {data.viewers === null ? "" : ` · ${data.viewers} 个接管连接`}</p>}
    {current && data && <p>{data.observation === null ? "仅观察入口：尚未确认"
      : data.observation.available ? `仅观察入口可用 · ${data.observation.viewers} 人观看` : "仅观察入口未就绪"}</p>}
    <p className="resident-computer-note">{resident ? "执行设计：常驻端采集画面并调用其配置的模型，本地助手默认只接收状态与结果。你可以另开远程画面，亲自操作与接管。" : "当前状态来自本机，不能代表常驻电脑。"}
      无人值守桌面任务尚未接通；桌面就绪不代表自动签到已可用。</p>
    {!current && data && <small>上次结果已保留，当前未确认有效。</small>}
  </section>;
}

export function ResidentComputerCard({ client, scope, connected, resident, now }: {
  client: Partial<Pick<GatewayClient, "getResidentComputer">> | null; scope: string;
  connected: boolean; resident: boolean; now: number;
}) {
  const owner = useMemo(() => ({ client, scope }), [client, scope]);
  const identity = useRef({ owner, connected });
  identity.current = { owner, connected };
  const [snapshot, setSnapshot] = useState<{ owner: typeof owner; source: ComputerRead } | null>(null);
  const refreshRef = useRef<(() => Promise<void>) | null>(null);
  useEffect(() => {
    if (!connected || !client?.getResidentComputer) return;
    let active = true;
    const reads = residentComputerReads({ read: () => client.getResidentComputer!(),
      current: () => active && identity.current.owner === owner && identity.current.connected,
      publish: source => setSnapshot({ owner, source }) });
    refreshRef.current = reads.refresh;
    void reads.refresh();
    return () => { active = false; if (refreshRef.current === reads.refresh) refreshRef.current = null; };
  }, [client, owner, connected]);
  const source: ComputerRead = snapshot?.owner === owner ? snapshot.source
    : { status: client?.getResidentComputer ? "loading" : "unsupported" };
  return <ResidentComputerSummary source={source} connected={connected} resident={resident} now={now}
    busy={connected && source.status === "loading"} onRefresh={() => void refreshRef.current?.()} />;
}
