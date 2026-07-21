// App.tsx 抽出的 Project Journal 卡（B8-④c S6a，只搬家不改行为）。
//
// ④a 盘点发现的意外耦合：Journal 卡一直嵌在 decisions tab 的 JSX 里。本次把它拆成
// 独立组件、仍渲染在 decisions tab 原位——是否给 Journal 独立 tab 是 UX 变更，另行拍板，
// 不混进重构 PR。
//
// 与 S3–S5 同构的纯展示件：journal 8 个 state 留 App（connect() 预取 snapshotTodayJournal/
// refreshWeeklyJournal，事件总线在 agent_done/task_update 等 case 里调 snapshotTodayJournal
// 续期），全部经 props 下传；写路径（selectJournalDay/continueFromYesterday/saveJournalSnapshot/
// openJournalAction/addJournalNote 触碰 client、inspector 与任务域）留 App 经回调注入。
// 搬进来的只有 journalActions 派生（仅本卡消费）。App 的 localDay() 以 today prop 传值注入
// （该函数在 App 另有十余处调用，不动它）。
import type {
  JournalContinuation,
  JournalDaySummary,
  JournalNextAction,
  JournalSnapshot,
  WeeklyJournalSnapshot,
} from "../types";
import { statusLabel } from "../lib/labels";

type JournalCardProps = {
  journal: JournalSnapshot | null;
  journalDays: JournalDaySummary[];
  weeklyJournal: WeeklyJournalSnapshot | null;
  journalContinuation: JournalContinuation | null;
  journalView: "day" | "week";
  journalDate: string;
  journalNote: string;
  journalBusy: boolean;
  today: string;
  onSetView: (view: "day" | "week") => void;
  onSelectDay: (date: string) => void;
  onRefreshDay: (date: string) => void;
  onRefreshWeekly: (end: string) => void;
  onContinueYesterday: () => void;
  onSaveSnapshot: () => void;
  onOpenAction: (action: JournalNextAction) => void;
  onNoteChange: (value: string) => void;
  onAddNote: () => void;
};

export function JournalCard({
  journal,
  journalDays,
  weeklyJournal,
  journalContinuation,
  journalView,
  journalDate,
  journalNote,
  journalBusy,
  today,
  onSetView,
  onSelectDay,
  onRefreshDay,
  onRefreshWeekly,
  onContinueYesterday,
  onSaveSnapshot,
  onOpenAction,
  onNoteChange,
  onAddNote,
}: JournalCardProps) {
  const journalActions = journalDate === today
    ? journal?.next_actions ?? []
    : journalContinuation?.from_date === journalDate
      ? journalContinuation.actions
      : [];

  return (
    <section className="journal-card">
      <div className="journal-head">
        <div>
          <span>Project Journal</span>
          <strong>{journalView === "week" ? "7 天项目周报" : journalDate === today ? "今日工作摘要" : `${journalDate} 工作快照`}</strong>
        </div>
        <div>
          <div className="journal-range-toggle">
            <button className={journalView === "day" ? "active" : ""} onClick={() => onSetView("day")}>日报</button>
            <button className={journalView === "week" ? "active" : ""} onClick={() => { onSetView("week"); void onRefreshWeekly(journalDate); }}>7 天</button>
          </div>
          <input
            type="date"
            max={today}
            value={journalDate}
            onChange={(event) => void onSelectDay(event.target.value)}
          />
          {journalDays.length > 0 && (
            <select
              aria-label="已保存 Journal"
              value={journalDays.some((item) => item.date === journalDate) ? journalDate : ""}
              onChange={(event) => { if (event.target.value) void onSelectDay(event.target.value); }}
            >
              <option value="">历史快照</option>
              {journalDays.map((item) => <option value={item.date} key={item.date}>{item.date}</option>)}
            </select>
          )}
          <button disabled={journalBusy} onClick={() => void onContinueYesterday()}>继续昨天</button>
          <button disabled={journalBusy} onClick={() => void (journalView === "week" ? onRefreshWeekly(journalDate) : onRefreshDay(journalDate))}>刷新</button>
          {journalView === "day" && (
            <button className="primary" disabled={journalBusy || journalDate !== today} onClick={() => void onSaveSnapshot()}>
              {journalBusy ? "保存中…" : "保存快照"}
            </button>
          )}
        </div>
      </div>
      {journalView === "week" && weeklyJournal ? (
        <>
          <p className="journal-headline">{weeklyJournal.headline}</p>
          <div className="journal-meta">
            <span>{weeklyJournal.start_date} — {weeklyJournal.end_date}</span>
            <span>历史按冻结快照，今天按实时台账</span>
          </div>
          <div className="journal-metrics">
            <div><b>{weeklyJournal.summary.tasks_done}</b><span>完成任务</span></div>
            <div><b>{weeklyJournal.summary.goals_achieved}</b><span>达成目标</span></div>
            <div><b>{weeklyJournal.summary.evidence_passed}</b><span>通过证据</span></div>
            <div className={weeklyJournal.summary.runs_failed ? "warn" : ""}><b>{weeklyJournal.summary.runs_failed}</b><span>失败运行</span></div>
            <div><b>{weeklyJournal.summary.tools}</b><span>工具调用</span></div>
            <div className={weeklyJournal.summary.decisions_denied ? "warn" : ""}><b>{weeklyJournal.summary.decisions_denied}</b><span>拒绝操作</span></div>
          </div>
          {weeklyJournal.carryovers.length > 0 && (
            <div className="journal-next-actions">
              <strong>周报结束时仍需继续</strong>
              {weeklyJournal.carryovers.slice(0, 6).map((action) => (
                <div key={`weekly-${action.kind}-${action.target_id}`}>
                  <span>{action.kind === "goal" ? "目标" : action.kind === "task" ? "任务" : "运行"}</span>
                  <p><b>{action.title}</b><small>{action.detail}</small></p>
                  <button onClick={() => void onOpenAction(action)}>{action.action === "resume_task" ? "恢复" : "打开"}</button>
                </div>
              ))}
            </div>
          )}
          <div className="journal-week-days">
            {weeklyJournal.days.map((day) => (
              <button onClick={() => { onSetView("day"); void onSelectDay(day.date); }} key={day.date}>
                <span>{day.date.slice(5)}</span>
                <b>{day.summary.tasks_done}</b>
                <small>任务</small>
                <i className={day.summary.runs_failed || day.summary.tasks_failed ? "warn" : ""} />
              </button>
            ))}
          </div>
          {weeklyJournal.highlights.length > 0 && (
            <div className="journal-details">
              <details>
                <summary>本周重点 · {weeklyJournal.highlights.length}</summary>
                {weeklyJournal.highlights.map((item) => (
                  <div className={`journal-event ${item.status || ""}`} key={`weekly-${item.date}-${item.id}`}>
                    <i />
                    <p><b>{item.title}</b>{item.detail && <span>{item.detail}</span>}</p>
                    <time>{item.date.slice(5)}</time>
                  </div>
                ))}
              </details>
            </div>
          )}
        </>
      ) : journalView === "day" && journal ? (
        <>
          <p className="journal-headline">{journal.headline}</p>
          <div className="journal-meta">
            <span>{journal.frozen ? "历史冻结快照" : "从持久台账实时生成"}</span>
            <span>{journal.stored_at ? `已保存 ${new Date(journal.stored_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}` : "尚未保存快照"}</span>
          </div>
          <div className="journal-metrics">
            <div><b>{journal.summary.tasks_done}</b><span>完成任务</span></div>
            <div><b>{journal.summary.goals_achieved}</b><span>达成目标</span></div>
            <div><b>{journal.summary.evidence_passed}</b><span>通过证据</span></div>
            <div className={journal.summary.runs_failed ? "warn" : ""}><b>{journal.summary.runs_failed}</b><span>失败运行</span></div>
            <div><b>{journal.summary.tools}</b><span>工具调用</span></div>
            <div className={journal.summary.decisions_denied ? "warn" : ""}><b>{journal.summary.decisions_denied}</b><span>拒绝操作</span></div>
          </div>

          {journalDate < today && journalContinuation && (
            <div className="journal-continuation-meta">
              <span>{journalContinuation.source_stored ? "已从冻结快照对账" : "已从历史台账重建"}</span>
              <p>{journalContinuation.headline}</p>
            </div>
          )}

          {journalActions.length > 0 && (
            <div className="journal-next-actions">
              <strong>{journalDate === today ? "下一步" : "当前仍可继续"}</strong>
              {journalActions.slice(0, 6).map((action) => (
                <div key={`${action.kind}-${action.target_id}`}>
                  <span>{action.kind === "goal" ? "目标" : action.kind === "task" ? "任务" : "运行"}</span>
                  <p><b>{action.title}</b><small>{action.detail}</small></p>
                  <button onClick={() => void onOpenAction(action)}>{action.action === "resume_task" ? "恢复" : "打开"}</button>
                </div>
              ))}
            </div>
          )}

          {journalDate === today && (
            <div className="journal-note-form">
              <input
                value={journalNote}
                onChange={(event) => onNoteChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) void onAddNote();
                }}
                placeholder="记录决定、交接背景或明天要继续的事项…"
              />
              <button disabled={journalBusy || !journalNote.trim()} onClick={() => void onAddNote()}>记录</button>
            </div>
          )}

          <div className="journal-details">
            <details open={journal.highlights.length > 0}>
              <summary>{journalDate === today ? "今日重点" : "当日重点"} · {journal.highlights.length}</summary>
              {journal.highlights.map((item) => (
                <div className={`journal-event ${item.status || ""}`} key={item.id}>
                  <i />
                  <p><b>{item.title}</b>{item.detail && <span>{item.detail}</span>}</p>
                  <time>{item.ts ? new Date(item.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : ""}</time>
                </div>
              ))}
            </details>
            {journal.handoffs.length > 0 && (
              <details>
                <summary>任务交接 · {journal.handoffs.length}</summary>
                {journal.handoffs.map((handoff) => (
                  <div className="journal-handoff" key={handoff.task_id}>
                    <div><b>{handoff.title}</b><span>{statusLabel(handoff.status)}</span></div>
                    <p>{handoff.next_action}</p>
                    {handoff.remaining.length > 0 && <small>待处理：{handoff.remaining.join("；")}</small>}
                  </div>
                ))}
              </details>
            )}
            {journal.evidence.length > 0 && (
              <details>
                <summary>Goal 验收证据 · {journal.evidence.length}</summary>
                {journal.evidence.map((evidence) => (
                  <div className={`journal-evidence ${evidence.passed ? "passed" : "failed"}`} key={evidence.id}>
                    <span>{evidence.passed ? "✓" : "!"}</span>
                    <p><b>{evidence.criterion}</b><small>{evidence.summary}</small></p>
                  </div>
                ))}
              </details>
            )}
            <details>
              <summary>完整时间线 · {journal.timeline.length}</summary>
              {journal.timeline.map((item) => (
                <div className="journal-event compact" key={`timeline-${item.id}`}>
                  <i />
                  <p><b>{item.title}</b>{item.detail && <span>{item.detail}</span>}</p>
                  <time>{item.ts ? new Date(item.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : ""}</time>
                </div>
              ))}
            </details>
          </div>
        </>
      ) : (
        <div className="panel-empty compact"><strong>正在生成工作摘要</strong><p>Journal 会从审计、Goal、任务和运行台账恢复。</p></div>
      )}
    </section>
  );
}
