import { ArrowRight, Laptop, Monitor, Server } from "lucide-react";

export function RuntimeLocationChoices({ disabled, onChoose }: {
  disabled: boolean; onChoose: (choice: "local" | "remote") => void;
}) {
  return <div className="onboarding-choices" aria-label="选择助理的运行位置">
    <button type="button" disabled={disabled} onClick={() => onChoose("remote")}>
      <span className="onboarding-choice-icon" aria-hidden="true"><Monitor /><ArrowRight /><Server /></span>
      <strong>连接已有助手</strong>
      <span className="onboarding-choice-description">助理在另一台电脑上运行。关掉这里的窗口，它仍可以继续工作。</span>
      <span className="onboarding-choice-action">连接另一台电脑<ArrowRight aria-hidden="true" /></span>
    </button>
    <button type="button" disabled={disabled} onClick={() => onChoose("local")}>
      <span className="onboarding-choice-icon" aria-hidden="true"><Laptop /></span>
      <strong>在这台 Mac 运行</strong>
      <span className="onboarding-choice-description">资料保存在本机。退出 Desktop 后仍可运行；Mac 需开机并登录，睡眠时暂停。</span>
      <span className="onboarding-choice-action">准备这台 Mac<ArrowRight aria-hidden="true" /></span>
    </button>
  </div>;
}
