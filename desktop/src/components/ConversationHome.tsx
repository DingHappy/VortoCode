// 空白对话的首页：正在启动 / 助理模式的问候与上手示例 / 工作台的问候，下面可以挂首次接模型的卡片。
// 只负责显示；打开设置、选示例都交回 App。
import type { ReactNode } from "react";
import { LoaderCircle } from "lucide-react";

/** 按时段问候：首页唯一用衬线字的地方。 */
export function greeting(now: Date = new Date()): string {
  const hour = now.getHours();
  return hour >= 5 && hour < 11 ? "上午好" : hour >= 11 && hour < 13 ? "中午好" : hour >= 13 && hour < 18 ? "下午好" : "晚上好";
}

export function ConversationHome({ starting, showStarting, assistant, now, children }: {
  /** 本地引擎还没连上（冷启动、正在拉起或连接中） */
  starting: boolean;
  /** 延后 400ms 才露出「正在启动」，连得快就不闪 */
  showStarting: boolean;
  assistant: boolean;
  now?: Date;
  children?: ReactNode;
}) {
  return (
    <div className="home-greeting">
      {/* 冷启动要等本地引擎起来（约数秒）；这期间别摆出一个看着能用、其实还没连上的首页，
          连上后再切到上次的会话也就不显得突兀（真机 2026-10-06）。 */}
      {/* 引擎通常一秒内就连上（Desktop 一启动就预先拉起 runtime）：提示延后 400ms 才显示，
          连得快就不闪；占位保持同样高度，避免布局跳动。 */}
      {starting
        ? <h1 className={`home-starting ${showStarting ? "" : "pending"}`} aria-hidden={!showStarting}><LoaderCircle className="activity-spinner" size={20} />正在启动本地引擎…</h1>
        : assistant
          // 在哪做、做完在哪看，写在输入框的占位文字里，这里只留一句问候
          ? <h1 className="vc-greeting">{greeting(now)}，今天想交代点什么？</h1>
          : <><h1>开始一项工作</h1><p>描述你要解决的问题，或选择工作区域开始。</p></>}
      {children}
    </div>
  );
}

/** 还没接模型：会员模型或自己的模型，二选一。 */
export function FirstRunCard({ signedIn, onAccount, onModel }: { signedIn: boolean; onAccount: () => void; onModel: () => void }) {
  return (
    <div className="first-run-card">
      <strong>先接一个模型，就能开始</strong>
      <p>VortoCode 本身不带模型，两种方式任选一种：</p>
      <div className="first-run-actions">
        <button className="primary" onClick={onAccount}>
          {signedIn ? "使用会员模型" : "登录 VortoCode 账号"}
          <small>会员直接用，不用填地址和 Key</small>
        </button>
        <button onClick={onModel}>
          用自己的模型
          <small>本机模型（oMLX、Ollama 等）或自己的 API Key</small>
        </button>
      </div>
    </div>
  );
}
