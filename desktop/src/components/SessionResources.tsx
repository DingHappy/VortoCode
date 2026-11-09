import type { ResourceView, SessionResource } from "../lib/sessionResources";
import { useState } from "react";
import { ChevronRight } from "lucide-react";
import "./SessionResources.css";

const labels: Record<SessionResource["state"], string> = {
  idle: "已装配，尚未打开", active: "会话已打开", needs_human: "等待人工处理", cleanup_pending: "清理待核对",
  unknown: "未核实", not_attached: "尚未附加设备", needs_reconciliation: "上次操作待核对", loaded: "工具已装配",
  not_provided: "本会话未提供", blocked: "当前会话受限", disconnected: "尚未连接", connecting: "连接中",
  connected: "已有连接观察", partial: "部分连接", error: "连接异常",
};

/** 输入框上方这一行写哪台运行端。常驻电脑登记名本身常叫「常驻电脑」，不再补「（常驻）」写成「常驻电脑（常驻）」。 */
export function residentHostLabel(name: string | undefined, remote: boolean, resident: boolean): string {
  if (!remote) return "本机运行端";
  const base = name?.trim() || (resident ? "常驻电脑" : "已登记运行端");
  if (resident) return base.includes("常驻") ? base : `${base}（常驻）`;
  return `${base}（远端）`;
}

const ATTENTION: SessionResource["state"][] = ["needs_human", "needs_reconciliation", "cleanup_pending", "error"];

/** 折叠行右侧那几个字：要核对时直接写是哪一项、什么情况，不只写「需要核对」。 */
export function resourceSummary(view: ResourceView, connected: boolean): { text: string; attention: boolean } {
  if (!connected) return { text: "连接中断", attention: true };
  if (view.error) return { text: "状态没读到", attention: true };
  const flagged = view.snapshot?.items.filter(item => ATTENTION.includes(item.state)) ?? [];
  if (flagged.length) {
    const first = `${flagged[0].title}${labels[flagged[0].state]}`;
    return { text: flagged.length > 1 ? `${first}，另有 ${flagged.length - 1} 项` : first, attention: true };
  }
  if (view.busy) return { text: "读取中", attention: false };
  if (!view.snapshot) return { text: "按需查看", attention: false };
  return { text: view.stale ? "记录待核对" : "查看详情", attention: false };
}

/** One disclosure in the current conversation; it never installs, connects or approves.
 * quietWhenOk（助理模式，ADR-016）：一切正常时不占位置，只在需要核对或连接中断时出现；常驻电脑的状态平时看侧栏底部。 */
export function SessionResources({ view, connected, hostLabel, pendingId, onRefresh, onFocusConfirmation, onManage, onBrowserSettings, quietWhenOk = false }: {
  view: ResourceView; connected: boolean; hostLabel: string; pendingId?: string;
  onRefresh: () => void; onFocusConfirmation: () => void; onManage: () => void;
  onBrowserSettings?: () => void; quietWhenOk?: boolean;
}) {
  const snapshot = view.snapshot;
  const [open, setOpen] = useState(false);
  const summary = resourceSummary(view, connected);
  if (quietWhenOk && connected && !summary.attention && !open) return null;
  return <details className="session-resources" onToggle={event => { setOpen(event.currentTarget.open); if (event.currentTarget.open) onRefresh(); }}>
    <summary><ChevronRight aria-hidden="true" /><span>{hostLabel} · 连接与能力</span>
      <small className={summary.attention ? "attention" : undefined}>{summary.text}</small></summary>
    <div className="session-resource-actions">
      <button disabled={!connected || view.busy} onClick={onRefresh}>{view.busy ? "读取中…" : "刷新状态"}</button>
      {pendingId && snapshot?.attention_id === pendingId && <button onClick={onFocusConfirmation}>查看当前确认</button>}
      <button onClick={onManage}>管理连接</button>
      {onBrowserSettings && <button onClick={onBrowserSettings}>浏览器设置</button>}
    </div>
    <SessionResourceDetails view={view} connected={connected} hostLabel={hostLabel} />
  </details>;
}

export function SessionResourceDetails({ view, connected, hostLabel }: { view: ResourceView; connected: boolean; hostLabel: string }) {
  const snapshot = view.snapshot;
  const stale = view.stale || !connected;
  return <div className="session-resources-body">
      <p>当前连接：{hostLabel}。这里的状态不代表已登录、已授权执行或任务完成。</p>
      {snapshot && <p>读取于 {new Date(snapshot.observed_at).toLocaleString()}{stale ? " · 保留上次记录，当前状态待核对" : " · 会话观察，未作在线账号检查"}</p>}
      {view.error && <p role="status">{view.error}</p>}
      {!snapshot && !view.busy && !view.error && <p>当前会话尚无资源记录，可点击刷新读取；不会连接设备、检查账号或调用模型。</p>}
      {snapshot?.status === "not_loaded" && <p>会话尚未初始化，暂时没有资源记录。正常打开会话后可手动刷新。</p>}
      {snapshot?.truncated && <p role="status">资源清单未完整读取，未列出的能力保持未知。</p>}
      {snapshot?.items.length ? <ul>{snapshot.items.map(item => <li key={item.id}>
        <div className="session-resource-title"><strong>{item.title}</strong><span>{stale ? "上次记录：" : ""}{item.kind === "device" && item.state === "active" ? "已有附加会话记录" : labels[item.state]}</span></div>
        <dl>
          {item.target && <><dt>目标</dt><dd>{item.target}</dd></>}
          {item.device && <><dt>设备</dt><dd>{item.device}</dd></>}
          {item.account && <><dt>账号标签</dt><dd>{item.account}（实际账号待核对）</dd></>}
          <dt>{item.kind === "device" ? "控制端" : "执行位置"}</dt><dd>{item.kind !== "service" ? hostLabel : item.id === "mcp" ? "对应 MCP 服务端，实际位置待核对" : "由当前运行端访问外部服务"}</dd>
          <dt>动作授权</dt><dd>{item.approval === "each_action" ? "交互动作每次确认" : item.approval === "tool_policy" ? "按具体工具和原权限规则判断" : "未核实，不据此授权"}</dd>
          {item.checked_at && <><dt>连接观察</dt><dd>{new Date(item.checked_at).toLocaleString()}</dd></>}
        </dl>
        <p>{item.detail}</p>
      </li>)}</ul> : null}
    </div>;
}
