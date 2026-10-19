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

/** 摘要里的时间是 UTC 的 ISO 字符串：显示成本地时间，今天只写时分。 */
function localTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const now = new Date();
  const hm = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return date.toDateString() === now.toDateString() ? hm : `${date.getMonth() + 1}月${date.getDate()}日 ${hm}`;
}

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
        <label>目标<select aria-label="看板目标" value={goalKey} onChange={(event) => setGoalKey(event.target.value)}>
          <option value="">所有目标</option>
          {goalKey && !goals.some((item) => item.key === goalKey) && <option value={goalKey}>目标未在本次摘要中</option>}
          {goals.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select></label>
        <button disabled={!goalSource?.snapshot || !!goalSource.error || goalSource.snapshot.version !== 1 || goalSource.scope === "general"}
          onClick={() => { if (goalSource) onOpenGoals(goalSource); }}
          title={goalSource?.scope === "general" ? "目标要在具体项目里设置：先在上面选一个项目" : goalSource ? `打开 ${goalSource.label} 的目标设置` : "请选择项目以设置目标"}>设置目标</button>
      </div>
      {items.length === 0 && scopedSources.length > 0 && scopedSources.every((source) => !source.error && source.snapshot?.version === 1) && <div className="task-board-welcome">
        <strong>还没有进行中的工作</strong>
        <p>在对话里说「帮我在某某项目里做……」，VortoCode 会拆成任务在后台做；需要你确认、正在做和做完的，都会出现在这里。</p>
      </div>}
      {scopedSources.map((source) => <div className={`task-board-source ${source.error ? "degraded" : ""}`} key={source.runtimeId}>
        <strong>{source.label}</strong><span>{source.error ? "暂时连不上，状态未知；恢复后自动刷新"
          : source.snapshot?.version !== 1 && source.snapshot ? "版本不兼容，状态未知" : source.snapshot ? `更新于 ${localTime(source.snapshot.generated_at)}` : "正在读取…"}</span>
      </div>)}
      <div className="task-board-columns" tabIndex={0} aria-label="看板列，可横向滚动">
        {BOARD_COLUMNS.map((column) => {
          const entries = items.filter((item) => item.column === column.id);
          return <section className={`task-board-column ${column.id}`} key={column.id} aria-label={column.label}>
            <h3><i />{column.label}<span>{entries.length} 项</span></h3>
            {entries.length === 0 && <p className="task-board-empty">暂无</p>}
            {entries.map((item) => <article className="task-board-card" key={item.key}>
              <small>{item.source.label} · {item.kind === "task" ? "任务" : item.kind === "goal" ? "目标" : item.kind === "session" ? "会话" : "事项"}</small>
              <h4>{item.title}</h4><p>{item.detail}</p>
              {item.lifecycle && <small>验收：{({accepted: "已验收", pending: "待验收", not_submitted: "未提交", rework_requested: "需返工", not_applicable: "不适用", unknown: "未知"})[item.lifecycle.review]} · 交接：{({handled: "已处理", pending: "待处理", not_applicable: "不适用", unknown: "未知"})[item.lifecycle.handling]} · 依赖有效性：{({valid: "当前摘要内有效", invalid: "已失效", not_applicable: "不适用", unknown: "未知"})[item.lifecycle.validity]}</small>}
              {item.sessionId && <small>来源会话：{item.source.snapshot?.sessions.find((session) => session.sid === item.sessionId)?.title || item.sessionId}</small>}
              {item.goalId && item.kind !== "goal" && <small>目标：{item.source.snapshot?.goals.find((goal) => goal.id === item.goalId)?.objective || item.goalId}</small>}
              <footer>{item.goalId && item.kind === "task" && <button disabled={!!item.source.error} onClick={() => onOpenGoals(item.source, item.goalId)}>查看关联目标</button>}<span>{item.source.error ? "状态未知" : item.status}</span>
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
      <details className="task-board-scope"><summary>关于这个看板</summary><p>只显示各项目最近的工作（每个项目最多 100 个会话、200 个任务和目标），不是完整记录。任务执行结束、验收、交接和目标达成分开记录；看板本身不启动任务，点卡片会回到原来的项目和会话。</p></details>
    </section>
  );
}
