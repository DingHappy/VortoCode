// App.tsx 抽出的「待处理」决策中心（B8-④c S6a，只搬家不改行为）：
// 待决策队列 + 工具与权限审计 + 后台通知三段。Journal 卡已另拆 <JournalCard>，
// 两件在 App 的 decisions tab 里并列渲染——这就是「Journal 与 decisions 解耦」的落点。
//
// 纯展示件契约（同 S3–S5）：decisionItems 喂 sidebar 角标与 inbox 汇总（跨域读）、
// auditEntries/notices 被总线经 refresh 回调续期、prDelivery 与 diff tab 共享，全部留 App
// 经 props 下传；决策动作（确认应答/打开定位/交给 Agent/忽略，触碰 client 与审计）留 App
// 经回调注入。只有 auditFilter（审计类别筛选）是纯本地 UI 态，随组件自持（同 S4 先例）。
import { useMemo, useState } from "react";

import { compactAuditData, decisionKindLabel } from "../lib/labels";
import type {
  AuditEntry,
  DecisionItem,
  NoticeItem,
  PrDeliveryCheck,
  PrDeliverySnapshot,
} from "../types";

type AuditFilter = "all" | "tool" | "decision" | "event";

type DecisionsPanelProps = {
  decisionItems: DecisionItem[];
  auditEntries: AuditEntry[];
  notices: NoticeItem[];
  prDelivery: PrDeliverySnapshot | null;
  notificationsEnabled: boolean;
  onToggleNotifications: () => void;
  onRefresh: () => void;
  onAnswerConfirmation: (id: string, ok: boolean) => void;
  onOpenDecision: (decision: DecisionItem) => void;
  onSendPrFeedback: (check?: PrDeliveryCheck) => void;
  onDismissDecision: (decision: DecisionItem) => void;
};

export function DecisionsPanel({
  decisionItems,
  auditEntries,
  notices,
  prDelivery,
  notificationsEnabled,
  onToggleNotifications,
  onRefresh,
  onAnswerConfirmation,
  onOpenDecision,
  onSendPrFeedback,
  onDismissDecision,
}: DecisionsPanelProps) {
  const [auditFilter, setAuditFilter] = useState<AuditFilter>("all");
  const filteredAuditEntries = useMemo(
    () => auditFilter === "all" ? auditEntries : auditEntries.filter((entry) => entry.category === auditFilter),
    [auditEntries, auditFilter],
  );

  return (
    <>
      <div className="decision-section-head">
        <div><strong>待决策队列</strong><span>确认、阻塞目标、失败任务与 PR 反馈</span></div>
        <div className="decision-toolbar">
          <button className={notificationsEnabled ? "active" : ""} onClick={() => void onToggleNotifications()}>
            {notificationsEnabled ? "系统通知 已开" : "开启系统通知"}
          </button>
          <button onClick={onRefresh}>刷新</button>
        </div>
      </div>
      {decisionItems.length === 0 && (
        <div className="panel-empty compact"><strong>没有待决策事项</strong><p>运行失败、人工确认和 PR 反馈会在这里集中出现。</p></div>
      )}
      {decisionItems.map((decision) => (
        <section className={`decision-card ${decision.severity} ${decision.tainted ? "tainted" : ""}`} key={decision.id}>
          <div className="decision-card-head">
            <span>{decisionKindLabel(decision.kind)} · {decision.severity === "critical" ? "关键" : decision.severity === "high" ? "高" : "中"}</span>
            <time>{decision.created ? new Date(decision.created).toLocaleString("zh-CN") : "当前"}</time>
          </div>
          <strong>{decision.title}</strong>
          <p>{decision.detail}</p>
          {decision.tainted && <div className="decision-warning">外部内容已进入本回合，自动授权失效，必须由你核对。</div>}
          <div className="decision-actions">
            {decision.kind === "confirmation" ? (
              <>
                <button className="danger" onClick={() => void onAnswerConfirmation(decision.target_id, false)}>拒绝</button>
                <button className="primary" onClick={() => void onAnswerConfirmation(decision.target_id, true)}>允许一次</button>
              </>
            ) : (
              <>
                <button className="primary" onClick={() => void onOpenDecision(decision)}>
                  {decision.action === "resume_task" ? "恢复任务" : decision.kind === "pr_check" ? "查看失败日志" : decision.kind === "hook" ? "定位会话" : "打开详情"}
                </button>
                {decision.kind === "pr_check" && (
                  <button onClick={() => {
                    const check = prDelivery?.failing_checks.find((item) => item.id === decision.target_id);
                    if (check) void onSendPrFeedback(check);
                  }}>交给 Agent</button>
                )}
                {decision.kind === "pr_review" && <button onClick={() => void onSendPrFeedback()}>交给 Agent</button>}
                {decision.can_dismiss && <button onClick={() => void onDismissDecision(decision)}>{decision.kind === "hook" ? "已查看" : "忽略"}</button>}
              </>
            )}
          </div>
        </section>
      ))}

      <div className="decision-section-head audit-heading">
        <div><strong>工具与权限审计</strong><span>只记录脱敏参数、结果长度和明确决定</span></div>
      </div>
      <div className="audit-filters">
        {(["all", "tool", "decision", "event"] as AuditFilter[]).map((filter) => (
          <button className={auditFilter === filter ? "active" : ""} onClick={() => setAuditFilter(filter)} key={filter}>
            {{ all: "全部", tool: "工具", decision: "权限", event: "事件" }[filter]}
          </button>
        ))}
      </div>
      {filteredAuditEntries.length === 0 && <div className="audit-empty">还没有审计记录。</div>}
      <div className="audit-timeline">
        {filteredAuditEntries.map((entry) => {
          const details = entry.category === "tool"
            ? compactAuditData(entry.args)
            : entry.category === "decision"
              ? entry.operation || "操作确认"
              : compactAuditData(entry.data);
          return (
            <section className={`audit-entry ${entry.category} ${entry.decision || ""}`} key={entry.id}>
              <i />
              <div>
                <div>
                  <strong>{entry.category === "tool" ? entry.tool : entry.category === "decision" ? `权限${entry.decision === "allowed" ? "允许" : "拒绝"}` : entry.event || "事件"}</strong>
                  <time>{entry.ts ? new Date(entry.ts).toLocaleString("zh-CN") : ""}</time>
                </div>
                {details && <code>{details}</code>}
                <span>{entry.mode || "runtime"}{entry.tainted ? " · 外部内容回合" : ""}{entry.result_len != null ? ` · ${entry.result_len} 字符结果` : ""}</span>
              </div>
            </section>
          );
        })}
      </div>

      {notices.length > 0 && (
        <>
          <div className="decision-section-head notice-heading"><div><strong>后台通知</strong><span>cron、heartbeat 与常驻任务</span></div></div>
          {notices.map((notice, index) => (
            <section className="notice-card" key={`${notice.ts}-${index}`}>
              <div><span>{notice.source || "Runtime"}</span><time>{notice.ts ? new Date(notice.ts).toLocaleString("zh-CN") : "刚刚"}</time></div>
              <p>{notice.text}</p>
            </section>
          ))}
        </>
      )}
    </>
  );
}
