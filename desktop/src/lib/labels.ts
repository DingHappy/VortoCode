// App.tsx 抽出的 label / format 纯函数族（B8-④b S1，只搬家不改行为）。
// 这些函数把状态/枚举/数值映射成中文展示文案或格式化字符串，无副作用、无 React 依赖。
import type {
  CommandRunKind,
  DecisionItem,
  ExtensionInspectKind,
  GoalVerifierKind,
  SessionSummary,
  TaskBranchReviewState,
} from "../types";

export function formatRelativeTime(epochSeconds?: number): string {
  if (!epochSeconds) return "";
  const elapsed = Math.max(0, Date.now() - epochSeconds * 1000);
  if (elapsed < 60_000) return "刚刚";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} 分钟前`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} 小时前`;
  return new Date(epochSeconds * 1000).toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

export function formatBytes(bytes?: number): string {
  const value = Math.max(0, Number(bytes ?? 0));
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

export function statusLabel(status: string): string {
  return (
    {
      queued: "排队",
      running: "执行中",
      cancelling: "停止中",
      done: "完成",
      failed: "失败",
      cancelled: "已取消",
      interrupted: "已中断",
      paused: "已暂停",
      pending: "待处理",
      in_progress: "进行中",
      completed: "完成",
      draft: "草稿",
      active: "推进中",
      blocked: "阻塞",
      achieved: "已达成",
    }[status] ?? status
  );
}

export function extensionKindLabel(kind: ExtensionInspectKind): string {
  return ({ rules: "规则", skill: "Skills", hook: "Hooks", mcp: "MCP" })[kind];
}

export function extensionStatusLabel(status: string): string {
  return ({
    active: "已加载",
    available: "可用",
    disabled: "已关闭",
    needs_trust: "待信任",
    blocked: "已阻止",
    error: "配置错误",
  })[status] ?? status;
}

export function taskReviewGateReason(review?: TaskBranchReviewState | null): string {
  if (!review) return "";
  if (review.verification_stale) return "先重新验证审查后的分支";
  if (review.policy?.error) return `团队审查策略无效：${review.policy.error}`;
  if (review.policy?.require_all_hunks_decided && !review.coverage?.complete) {
    if (!review.coverage?.known) return `无法确认全部 hunk 已完成决策${review.coverage?.error ? `：${review.coverage.error}` : ""}`;
    return `团队策略要求先处理剩余 ${review.coverage.pending_hunks} 个 hunk`;
  }
  return "";
}

export function sessionStatusLabel(session: SessionSummary, active: boolean, locallyBusy: boolean): string {
  const status = active && locallyBusy && session.status !== "needs_input" ? "working" : session.status;
  switch (status) {
    case "needs_input":
      return (session.pending_input_count ?? 0) > 1
        ? `等待你的确认 · ${session.pending_input_count} 项`
        : "等待你的确认";
    case "working":
      return session.activity
        || ((session.background_tasks?.active ?? 0) > 0
          ? `${session.background_tasks?.active} 个后台任务执行中`
          : session.mode === "build" ? "Agent 正在执行" : "Agent 正在分析");
    case "queued":
      return `${session.queue_count ?? 0} 条待运行 · 已暂停`;
    case "idle":
      return active ? "当前工作项 · 空闲" : `空闲 · ${formatRelativeTime(session.updated)}`;
    case "completed":
      return `已完成 · ${formatRelativeTime(session.updated)}`;
    case "failed":
      if ((session.background_tasks?.attention ?? 0) > 0 && (session.hook_issues?.count ?? 0) > 0) {
        return `${session.background_tasks?.attention} 个后台 · ${session.hook_issues?.count} 个 Hook 待处理`;
      }
      if ((session.background_tasks?.attention ?? 0) > 0) {
        return `${session.background_tasks?.attention} 个后台任务需要处理`;
      }
      if ((session.hook_issues?.count ?? 0) > 0) {
        return `${session.hook_issues?.count} 个 Hook 需要处理`;
      }
      return `需要处理 · ${formatRelativeTime(session.updated)}`;
    case "inactive":
    default:
      return active ? "当前工作项" : `${session.messages} 条消息 · ${formatRelativeTime(session.updated)}`;
  }
}

export function compactSessionCwd(value?: string): string {
  const parts = String(value ?? "").split(/[\\/]+/).filter(Boolean);
  return parts.slice(-2).join("/") || "";
}

export function sessionContextTone(pct?: number): string {
  if ((pct ?? 0) >= 90) return "danger";
  if ((pct ?? 0) >= 70) return "warning";
  return "normal";
}

export function formatTokenCount(value?: number): string {
  const tokens = Math.max(0, Number(value ?? 0));
  if (tokens >= 1_000_000) return `${Number((tokens / 1_000_000).toFixed(tokens % 1_000_000 ? 1 : 0))}M`;
  if (tokens >= 1_000) return `${Number((tokens / 1_000).toFixed(tokens % 1_000 ? 1 : 0))}K`;
  return tokens.toLocaleString("zh-CN");
}

export function contextWindowSourceLabel(source?: string): string {
  return ({ service: "服务端检测", catalog: "官方模型目录", configured: "配置覆盖", unknown: "窗口未知" } as Record<string, string>)[source ?? "unknown"] ?? "模型能力";
}

export function sessionContextPresentation(context: SessionSummary["context"]): { label: string; pct: number; title: string } {
  if (!context) return { label: "", pct: 0, title: "" };
  if ((context.context_window_tokens ?? 0) > 0) {
    const windowPct = Math.max(0, Number(context.context_window_pct ?? 0));
    const pctLabel = windowPct > 0 && windowPct < 0.1 ? "<0.1" : windowPct.toFixed(windowPct < 10 ? 1 : 0).replace(/\.0$/, "");
    return {
      label: `上下文 ${pctLabel}%`,
      pct: windowPct,
      title: `${context.used_tokens.toLocaleString()} / ${context.context_window_tokens!.toLocaleString()} tokens · ${contextWindowSourceLabel(context.context_window_source)} · 历史管理预算 ${context.max_tokens.toLocaleString()}`,
    };
  }
  return {
    label: `历史预算 ${context.pct}%`,
    pct: context.pct,
    title: `${context.used_tokens.toLocaleString()} / ${context.max_tokens.toLocaleString()} tokens · 模型窗口未知 · ${context.policy}`,
  };
}

export function runKindLabel(kind: CommandRunKind): string {
  return { terminal: "命令", test: "测试", preview: "预览" }[kind];
}

export function fileGlyph(path: string): string {
  const extension = path.split(".").pop()?.toLowerCase();
  if (["ts", "tsx", "js", "jsx"].includes(extension ?? "")) return "TS";
  if (extension === "py") return "PY";
  if (extension === "rs") return "RS";
  if (["md", "mdx", "rst"].includes(extension ?? "")) return "MD";
  if (["json", "yaml", "yml", "toml"].includes(extension ?? "")) return "{}";
  if (["css", "scss", "less"].includes(extension ?? "")) return "#";
  return "·";
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KiB`;
}

export function verifierKindLabel(kind: GoalVerifierKind): string {
  return { test: "测试", build: "构建", lint: "Lint", file: "文件" }[kind];
}

export function decisionKindLabel(kind: DecisionItem["kind"]): string {
  return {
    confirmation: "确认",
    goal: "目标",
    task: "任务",
    run: "运行",
    pr_check: "CI",
    pr_review: "Review",
    hook: "Hook",
  }[kind];
}

export function hookCapabilityLabel(capability: string): string {
  return {
    observe_event: "观察事件",
    emit_annotation: "输出注释",
    block_tool: "阻止工具",
    run_command: "运行命令",
    send_http: "发送 HTTP",
    request_model: "调用模型",
  }[capability] ?? capability;
}

export function compactAuditData(value?: Record<string, unknown>): string {
  if (!value || Object.keys(value).length === 0) return "";
  try {
    const serialized = JSON.stringify(value);
    return serialized.length > 260 ? `${serialized.slice(0, 260)}…` : serialized;
  } catch {
    return "";
  }
}

export function formatActivityDuration(durationMs?: number): string {
  if (durationMs == null) return "";
  if (durationMs < 1000) return "不到 1 秒";
  if (durationMs < 10_000) return `${(durationMs / 1000).toFixed(1)} 秒`;
  if (durationMs < 60_000) return `${Math.round(durationMs / 1000)} 秒`;
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.round((durationMs % 60_000) / 1000);
  return seconds ? `${minutes} 分 ${seconds} 秒` : `${minutes} 分钟`;
}
