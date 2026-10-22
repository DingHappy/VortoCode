// 输入框周围的几块：钉在输入框上方的确认卡片、换工作区请求、待运行队列，以及右下角的模型选择。
// 只负责显示；授权、回答、执行和保存都交回 App。
import { ChevronsUpDown, Cpu, X } from "lucide-react";

import { MANAGE_MODELS, modelMenuSections, type ModelChoice } from "../lib/modelChoice";
import { isPlanBudgetConfirmation, isPlanExecutionConfirmation } from "../lib/onboarding";
import type { DesktopLlmProfileStatus, PendingConfirmation, PromptQueueItem, TrustLevel, TrustStatus } from "../types";
import { ConfirmationReply } from "./ConfirmationReply";
import { ConfirmationTrust } from "./ConfirmationTrust";

/** Runtime 请求确认。钉在输入框上方、不随对话流滚动：长回合一滚就看不见，用户只看到转圈（2026-09-17）。 */
export function ConfirmCard({ confirm, trust, trustBusy, connected, onTrust, onCheck, onAnswer }: {
  confirm: PendingConfirmation;
  trust: TrustStatus | null;
  trustBusy: boolean;
  connected: boolean;
  onTrust: (level: TrustLevel) => void;
  onCheck: () => void;
  onAnswer: (ok: boolean) => void;
}) {
  // plan 阶段结束请求动手，与"删文件/跑命令"那类确认不是一回事：前者是本次任务继续往下走的一次性授权，
  // 后者是单个危险动作。文案分开，人才知道自己在批什么。**污点提示优先级最高**——那是安全提示，任何时候都不能被别的文案盖掉。
  const plan = !confirm.tainted && isPlanExecutionConfirmation(confirm.text);
  return (
    <section className={`confirm-card ${confirm.tainted ? "tainted" : ""}`}>
      <div className="confirm-icon">{plan ? "▶" : "!"}</div>
      <div className="confirm-copy">
        <strong>
          {confirm.tainted
            ? "外部内容回合需要人工确认"
            : isPlanExecutionConfirmation(confirm.text)
              ? isPlanBudgetConfirmation(confirm.text)
                ? "规划未完成，授权后继续当前任务"
                : "计划已就绪，授权后在隔离工作区继续"
              : "Runtime 请求确认"}
        </strong>
        <p>{confirm.text}</p>
        <ConfirmationTrust key={confirm.id} trust={trust} busy={trustBusy}
          connected={connected} tainted={confirm.tainted}
          onChange={onTrust} />
        <ConfirmationReply state={confirm.responseState} error={confirm.responseError} connected={connected}
          allowLabel={plan ? "授权继续" : "允许一次"}
          onCheck={onCheck} onAnswer={onAnswer} />
      </div>
    </section>
  );
}

/** Agent 结构化请求文件能力：由用户决定切不切到 Scratch 或某个 Git 项目。 */
export function WorkspaceRequestCard({ request, onDecline, onAccept }: {
  request: { scope: "scratch" | "project"; reason: string; task: string };
  onDecline: () => void;
  onAccept: () => void;
}) {
  return (
    <section className="confirm-card workspace-request">
      <div className="confirm-icon">↗</div>
      <div className="confirm-copy">
        <strong>{request.scope === "scratch" ? "这个任务需要隔离 Scratch" : "这个任务需要一个 Git 项目"}</strong>
        <p>{request.reason}</p>
        {request.task && <code>{request.task}</code>}
        <div>
          <button className="deny" onClick={onDecline}>暂不切换</button>
          <button className="allow" onClick={onAccept}>{request.scope === "scratch" ? "创建 Scratch" : "选择项目"}</button>
        </div>
      </div>
    </section>
  );
}

/** 当前回合跑着时又发的话：排在这里，回合结束后依次执行。 */
export function PromptQueuePanel({ items, paused, onRunNow, onRemove }: {
  items: PromptQueueItem[];
  paused: boolean;
  onRunNow: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <section className="prompt-queue" aria-label="待运行任务">
      <div className="prompt-queue-head">
        <span>{paused ? "已暂停" : "接下来"}</span><b>{items.length}</b>
        <small>{paused ? "输入已保留，点击「现在执行」继续" : "当前回合结束后依次执行"}</small>
      </div>
      <div className="prompt-queue-list">
        {items.slice(0, 4).map((item, index) => (
          <div className="prompt-queue-row" key={`${item.id}:${item.version}`}>
            <span className="prompt-queue-index">{index + 1}</span>
            <span className="prompt-queue-copy" title={item.text}>
              <strong>{item.text}</strong>
              {item.context_count > 0 && <small>{item.context_count} 个引用</small>}
            </span>
            <button className="prompt-queue-now" onClick={() => onRunNow(item.id)}>现在执行</button>
            <button className="prompt-queue-remove" aria-label={`删除排队任务 ${index + 1}`} onClick={() => onRemove(item.id)}><X size={13} /></button>
          </div>
        ))}
        {items.length > 4 && <div className="prompt-queue-more">还有 {items.length - 4} 条</div>}
      </div>
    </section>
  );
}

/** 输入框右下角的模型：常驻电脑上的助理用那边的模型；本机按已接的模型服务列出可选项。 */
export function ComposerModelPicker({ residentModel, onResident, checked, choices, value, profile, onChoose, onManage }: {
  /** 常驻电脑那边配置的模型名 */
  residentModel?: string;
  /** 助理在常驻电脑上 */
  onResident: boolean;
  /** 本机模型服务配置已读到 */
  checked: boolean;
  choices: ModelChoice[];
  value: string;
  profile: DesktopLlmProfileStatus | null;
  onChoose: (value: string) => void;
  onManage: () => void;
}) {
  if (onResident) {
    return (
      <span className="composer-model single" title="助理在常驻电脑上，用那台电脑配置的模型">
        <Cpu size={13} />{residentModel || "常驻电脑的模型"}
      </span>
    );
  }
  if (choices.length === 0) {
    return checked ? (
      <button className="composer-model unset" title="还没有连接模型：点这里添加" onClick={onManage}>
        <Cpu size={13} /><span className="composer-model-name">未连接模型</span>
      </button>
    ) : null;
  }
  const current = choices.find((choice) => choice.value === value);
  return (
    <label className="composer-model" title={current?.hint || "选择模型"}>
      <Cpu size={13} />
      {/* 按钮上只写模型名；完整说明（档位、能力、上下文）只在展开的菜单里 */}
      <span className="composer-model-name">{current?.label ?? "选择模型"}</span>
      <ChevronsUpDown size={12} aria-hidden />
      <select
        aria-label="选择模型"
        value={value}
        onChange={(event) => {
          if (event.target.value === MANAGE_MODELS) { onManage(); return; }
          onChoose(event.target.value);
        }}
      >
        {modelMenuSections(choices, profile).map((section) => {
          const options = section.choices.map((choice) => (
            <option key={choice.value} value={choice.value}>{choice.label}{choice.value === "auto" ? "" : [choice.group ? "" : choice.hint, ...(choice.badges ?? [])].filter(Boolean).map((part) => ` · ${part}`).join("")}</option>
          ));
          return section.label ? <optgroup key={section.label} label={section.label}>{options}</optgroup> : options;
        })}
        <optgroup label="──────────"><option value={MANAGE_MODELS}>管理模型、添加本机或线上服务…</option></optgroup>
      </select>
    </label>
  );
}
