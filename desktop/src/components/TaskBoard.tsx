import { useState } from "react";
import type { DesktopRuntimeInbox } from "../types";
import { BOARD_COLUMNS, boardIdentity, buildTaskBoard } from "../lib/taskBoard";
import type { BoardItem } from "../lib/taskBoard";

type Props = {
  sources: DesktopRuntimeInbox[];
  currentRuntimeId?: string;
  onOpen: (item: BoardItem) => void;
  onOpenGoals: (source: DesktopRuntimeInbox, goalId?: string) => void;
};

export function TaskBoard({ sources, currentRuntimeId, onOpen, onOpenGoals }: Props) {
  const [runtimeId, setRuntimeId] = useState("");
  const [sessionKey, setSessionKey] = useState("");
  const [goalKey, setGoalKey] = useState("");
  const scopedSources = sources.filter((source) => !runtimeId || source.runtimeId === runtimeId);
  const sessions = scopedSources.flatMap((source) => (source.snapshot?.sessions ?? []).map((session) => ({
    key: boardIdentity(source.runtimeId, session.sid), label: `${source.label} · ${session.title || session.sid}`,
  })));
  const goals = scopedSources.flatMap((source) => (source.snapshot?.goals ?? []).map((goal) => ({
    key: boardIdentity(source.runtimeId, goal.id), label: `${source.label} · ${goal.objective || goal.id}`,
  })));
  const items = buildTaskBoard(sources, { runtimeId, sessionKey, goalKey });
  const history = items.filter((item) => item.column === "history");
  const goalSource = sources.find((source) => source.runtimeId === (runtimeId || currentRuntimeId));
  return (
    <section className="task-board" aria-label="跨会话工作看板">
      <div className="task-board-filters">
        <label>项目<select aria-label="看板项目" value={runtimeId} onChange={(event) => {
          setRuntimeId(event.target.value); setSessionKey(""); setGoalKey("");
        }}><option value="">所有工作区</option>
          {runtimeId && !sources.some((source) => source.runtimeId === runtimeId) && <option value={runtimeId}>来源已移除</option>}
          {sources.map((source) => <option key={source.runtimeId} value={source.runtimeId}>{source.label}</option>)}
        </select></label>
        <label>会话<select aria-label="看板会话" value={sessionKey} onChange={(event) => setSessionKey(event.target.value)}>
          <option value="">所有会话</option>
          {sessionKey && !sessions.some((item) => item.key === sessionKey) && <option value={sessionKey}>会话未在本次摘要中</option>}
          {sessions.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select></label>
        <label>Goal<select aria-label="看板目标" value={goalKey} onChange={(event) => setGoalKey(event.target.value)}>
          <option value="">所有目标</option>
          {goalKey && !goals.some((item) => item.key === goalKey) && <option value={goalKey}>目标未在本次摘要中</option>}
          {goals.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select></label>
        <button disabled={!goalSource?.snapshot || !!goalSource.error || goalSource.snapshot.version !== 1 || goalSource.scope === "general"}
          onClick={() => { if (goalSource) onOpenGoals(goalSource); }}
          title={goalSource ? `打开 ${goalSource.label} 的目标设置` : "请选择项目以设置目标"}>设置 Goal{goalSource ? ` · ${goalSource.label}` : ""}</button>
      </div>
      <p className="task-board-scope">当前摘要内 {items.length} 项；包含会话、任务、目标与待处理事项，同一工作可能有多个关联事项。</p>
      <details className="task-board-scope"><summary>摘要范围与完成状态说明</summary><p>这里只展示最近的有界摘要，不是完整台账。每工作区最多 100 会话、100 事项、200 目标、200 任务。生命周期摘要缺失或未知时需打开原任务核对；已恢复的旧任务保留在历史记录。</p></details>
      {scopedSources.map((source) => <div className={`task-board-source ${source.error ? "degraded" : ""}`} key={source.runtimeId}>
        <strong>{source.label}</strong><span>{source.error ? "无法刷新 · 状态未知，历史卡片暂停操作"
          : source.snapshot?.version !== 1 && source.snapshot ? "摘要版本不支持 · 状态未知" : source.snapshot ? `摘要观察时间 ${source.snapshot.generated_at}` : "尚未获取摘要"}</span>
      </div>)}
      <div className="task-board-columns" tabIndex={0} aria-label="看板列，可横向滚动">
        {BOARD_COLUMNS.map((column) => {
          const entries = items.filter((item) => item.column === column.id);
          return <section className={`task-board-column ${column.id}`} key={column.id} aria-label={column.label}>
            <h3><i />{column.label}<span>{entries.length} 项</span></h3>
            {entries.length === 0 && <p className="task-board-empty">当前摘要与筛选内暂无事项</p>}
            {entries.map((item) => <article className="task-board-card" key={item.key}>
              <small>{item.source.label} · {item.kind === "task" ? "任务" : item.kind === "goal" ? "Goal" : item.kind === "session" ? "会话" : "事项"}</small>
              <h4>{item.title}</h4><p>{item.detail}</p>
              {item.lifecycle && <small>验收：{({accepted: "已验收", pending: "待验收", not_submitted: "未提交", rework_requested: "需返工", not_applicable: "不适用", unknown: "未知"})[item.lifecycle.review]} · 交接：{({handled: "已处理", pending: "待处理", not_applicable: "不适用", unknown: "未知"})[item.lifecycle.handling]} · 依赖有效性：{({valid: "当前摘要内有效", invalid: "已失效", not_applicable: "不适用", unknown: "未知"})[item.lifecycle.validity]}</small>}
              {item.sessionId && <small>来源会话：{item.source.snapshot?.sessions.find((session) => session.sid === item.sessionId)?.title || item.sessionId}</small>}
              {item.goalId && item.kind !== "goal" && <small>Goal：{item.source.snapshot?.goals.find((goal) => goal.id === item.goalId)?.objective || item.goalId}</small>}
              <footer>{item.goalId && item.kind === "task" && <button disabled={!!item.source.error} onClick={() => onOpenGoals(item.source, item.goalId)}>查看关联 Goal</button>}<span>{item.source.error ? "状态未知" : item.status}</span>
                <button disabled={!!item.source.error} onClick={() => onOpen(item)}>{item.action}</button></footer>
            </article>)}
          </section>;
        })}
      </div>
      {history.length > 0 && <details className="task-board-scope"><summary>已恢复的历史任务 · {history.length} 项</summary>
        {history.map((item) => <article className="task-board-card" key={item.key}><small>{item.source.label}</small>
          <h4>{item.title}</h4><p>{item.status} · 接续任务 {item.lifecycle?.recovered_by}</p>
          <p>原交接：{item.lifecycle?.handling === "handled" ? "已处理" : "仍需核对"}；恢复不证明验收或目标达成。</p>
          <button disabled={!!item.source.error} onClick={() => onOpen(item)}>查看历史任务</button></article>)}
      </details>}
      <p className="task-board-scope">任务执行结束、验收、交接处理、持久交付和 Goal 达成分别记录。看板不启动任务；操作回到来源项目与会话。</p>
    </section>
  );
}
