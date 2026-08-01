import {
  ChevronDown,
  CircleCheck,
  CircleDashed,
  FolderGit2,
  KeyRound,
  Sparkles,
} from "lucide-react";

import { firstDeliveryReadiness } from "../lib/onboarding";
import type { ConnectionState, WorkspaceScope } from "../types";

type WelcomeGuideProps = {
  activeScope: WorkspaceScope;
  projectName: string;
  connection: ConnectionState;
  modelLoaded: boolean;
  modelConfigured: boolean;
  runtimeStarting: boolean;
  projectSwitching: boolean;
  onOpenSettings: () => void;
  onChooseProject: () => void;
  onDraftFirstDelivery: () => void;
  onDraftProjectBrief: () => void;
};

export function WelcomeGuide({
  activeScope,
  projectName,
  connection,
  modelLoaded,
  modelConfigured,
  runtimeStarting,
  projectSwitching,
  onOpenSettings,
  onChooseProject,
  onDraftFirstDelivery,
  onDraftProjectBrief,
}: WelcomeGuideProps) {
  const hasProject = activeScope === "project" && Boolean(projectName);
  const readiness = firstDeliveryReadiness({
    modelLoaded,
    modelConfigured,
    hasProject,
    runtimeConnected: connection === "connected",
  });
  const preparing = runtimeStarting || projectSwitching || connection === "connecting";
  const scopeLabel = activeScope === "project"
    ? projectName || "项目工作区"
    : activeScope === "scratch"
      ? "隔离 Scratch"
      : "通用任务";

  return (
    <div className="welcome">
      <span className="welcome-kicker">VortoCode Desktop</span>
      <h1>把一个任务交出去</h1>
      <p className="welcome-promise">你只在目标和合并口做决定；Agent 在隔离工作区完成实现、测试与审查。</p>
      <button className="welcome-scope" onClick={onOpenSettings}>
        {scopeLabel}
        <ChevronDown size={14} />
      </button>

      <section className="first-delivery-guide" aria-label="首次交付引导">
        <div className={`first-delivery-step ${readiness.model}`}>
          {readiness.model === "ready" ? <CircleCheck size={17} /> : readiness.model === "checking" ? <CircleDashed size={17} /> : <KeyRound size={17} />}
          <div>
            <strong>1. 模型服务</strong>
            <span>{readiness.model === "ready" ? "已安全配置" : readiness.model === "checking" ? "正在检查 Keychain…" : "配置 Relay 或兼容服务"}</span>
          </div>
          {readiness.model !== "ready" && <button onClick={onOpenSettings} disabled={readiness.model === "checking"}>配置</button>}
        </div>

        <div className={`first-delivery-step ${readiness.project}`}>
          {readiness.project === "ready" ? <CircleCheck size={17} /> : <FolderGit2 size={17} />}
          <div>
            <strong>2. Git 项目</strong>
            <span>{readiness.project === "ready" ? projectName : "选择要交付代码的仓库"}</span>
          </div>
          {readiness.project !== "ready" && <button onClick={onChooseProject} disabled={preparing}>选择</button>}
        </div>

        <div className={`first-delivery-step ${readiness.runtime}`}>
          {readiness.runtime === "ready" ? <CircleCheck size={17} /> : <CircleDashed size={17} />}
          <div>
            <strong>3. 首个交付</strong>
            <span>{readiness.runtime === "ready" ? "项目 runtime 已就绪" : preparing ? "正在准备隔离工作区…" : "完成前两步后开始"}</span>
          </div>
        </div>
      </section>

      <div className="first-delivery-actions">
        <button className="secondary" onClick={onDraftProjectBrief} disabled={!readiness.canDraft}>
          先只读了解项目
        </button>
        <button className="primary" onClick={onDraftFirstDelivery} disabled={!readiness.canDraft}>
          <Sparkles size={15} />准备首个交付
        </button>
      </div>
      <small className="first-delivery-note">
        按钮只会把任务草稿放入输入框；发送前仍可修改。计划准备好后会在原位置请求确认，
        同意后同一任务继续隔离实现，写入和外发仍经过现有确认门。
      </small>
    </div>
  );
}
