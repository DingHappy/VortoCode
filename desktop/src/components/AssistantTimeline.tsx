import { useCallback, useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import { startPolling } from "../lib/polling";
import type { Timeline, TimelineItem } from "../types";

export type TimelineClient = Pick<GatewayClient, "getTimeline" | "resumeGoalAdvance" | "undoAssistantChange">;
const KIND_LABEL: Record<string, string> = {
  notice: "通知", change: "替你做的", dispatch: "任务单", codex: "Codex", review: "自动审核", paused: "已暂停",
};
const SOURCE_LABEL: Record<string, string> = {
  notices: "通知", changes: "改动记录", dispatches: "任务单", codex: "Codex 动作", codex_progress: "Codex 进展", reviews: "自动审核", paused: "暂停目标",
};
function when(at: string): string {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "时间待核对" : date.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** 同一个聚合接口；操作仍走既有撤销和恢复接口。只在动态页打开时轮询。 */
export function AssistantTimeline({ client, connected, version, onOpen, onChanged }: {
  client: TimelineClient | null | undefined; connected: boolean; version?: number;
  onOpen?: (type: "decide_dispatch" | "decide_codex", id: string) => void;
  onChanged?: () => void;
}) {
  const [data, setData] = useState<Timeline | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState("");
  const [expanded, setExpanded] = useState(false);
  const current = useRef({ client, connected, epoch: 0 });
  if (current.current.client !== client || current.current.connected !== connected) {
    current.current = { client, connected, epoch: current.current.epoch + 1 };
  }
  const request = useRef(0);
  const busy = useRef(false);

  const refresh = useCallback(async () => {
    if (!client || !connected) return;
    const scope = current.current, sequence = ++request.current;
    try {
      const value = await client.getTimeline(60);
      if (current.current === scope && request.current === sequence) { setData(value); setError(""); }
    } catch {
      if (current.current === scope && request.current === sequence) setError("时间线暂时读不到；已有内容可能过期，请稍后刷新。");
      return false;
    }
  }, [client, connected]);

  useEffect(() => { setData(null); setMessage(""); setError(""); setPending(""); busy.current = false; }, [client, connected]);
  useEffect(() => { void refresh(); }, [refresh, version]);
  useEffect(() => {
    if (!client || !connected) return;
    const stop = startPolling(refresh, { intervalMs: 30_000, hiddenIntervalMs: 120_000, immediate: false, visibility: document });
    return () => { stop(); current.current = { ...current.current, epoch: current.current.epoch + 1 }; };
  }, [client, connected, refresh]);

  async function act(item: TimelineItem, type: string, id: string) {
    if (!client || !connected || busy.current) return;
    const scope = current.current;
    busy.current = true; setPending(item.id); setMessage("");
    try {
      if (type === "undo") {
        const outcome = await client.undoAssistantChange(id);
        if (current.current !== scope) return;
        setMessage([outcome.detail, ...outcome.not_recallable].filter(Boolean).join("；"));
      } else if (type === "resume_goal") {
        await client.resumeGoalAdvance(id);
        if (current.current !== scope) return;
        setMessage("已恢复自动推进，助理会在后续巡检中继续处理。");
      }
      onChanged?.();
    } catch (failure) {
      if (current.current === scope) setMessage(failure instanceof Error ? failure.message : "操作没成功，请刷新后重试");
    } finally {
      if (current.current === scope) { busy.current = false; setPending(""); await refresh(); }
    }
  }

  if (!client || !connected) return null;
  // 正常历史默认折叠，暂停、待决定和异常始终可见。
  const important = (item: TimelineItem) => item.actions.some(a => a.type !== "undo") || /失败|出错|拒绝|核对/.test(item.status);
  const shown = data?.items.filter((item, index) => expanded || index < 8 || important(item)) ?? [];
  return <section className="assistant-section assistant-timeline" aria-label="动态时间线">
    <header><h5>时间线</h5><button onClick={() => void refresh()}>刷新时间线</button></header>
    {error && <p role="status">{error}</p>}
    {message && <p role="status">{message}</p>}
    {data === null && !error && <p role="status">正在读取时间线…</p>}
    {data && data.unavailable.length > 0 && <p role="status">部分来源暂时读不到（{data.unavailable.map(name => SOURCE_LABEL[name] ?? "其他来源").join("、")}），不代表没有动静。</p>}
    {data && data.items.length === 0 && data.unavailable.length === 0 && <p>还没有动静。</p>}
    <ul className="assistant-audit">
      {shown.map(item => <li key={item.id}>
        <span className="assistant-audit-text">
          <strong>{item.title}</strong>
          <small>{KIND_LABEL[item.kind] ?? "动态"} · {when(item.at)}{item.status ? ` · ${item.status}` : ""}{item.tainted ? " · 读过外部内容后" : ""}</small>
          {item.detail && <details><summary>详情</summary><span>{item.detail}</span></details>}
        </span>
        {item.actions.map(action => action.type === "undo" || action.type === "resume_goal" ?
          <button key={action.type} disabled={Boolean(pending)} onClick={() => void act(item, action.type, action.id)}>
            {pending === item.id ? "处理中…" : action.type === "undo" ? "撤销" : "恢复推进"}</button>
          : onOpen && (action.type === "decide_dispatch" || action.type === "decide_codex") ?
            <button key={action.type} onClick={() => onOpen(action.type as "decide_dispatch" | "decide_codex", action.id)}>查看并决定</button> : null)}
      </li>)}
    </ul>
    {data && data.items.length > 8 && <button onClick={() => setExpanded(!expanded)}>{expanded ? "收起历史" : "展开近期历史"}</button>}
  </section>;
}
