import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { SessionContextUsage } from "../types";
import { sessionContextPresentation } from "../lib/labels";

export function contextStatusText(context?: SessionContextUsage) {
  if (!context) return { percent: "—", label: "上下文用量未知", detail: "尚未收到会话快照。", compression: "" };
  const known = (context.context_window_tokens ?? 0) > 0;
  const presentation = sessionContextPresentation(context);
  return {
    percent: presentation.label.replace(/^(上下文|历史预算) /, ""),
    label: known ? "上下文使用量（估算）" : "历史预算使用量（模型窗口未知）",
    detail: known ? `${context.used_tokens.toLocaleString()} / ${context.context_window_tokens!.toLocaleString()} tokens` : `${context.used_tokens.toLocaleString()} / ${context.max_tokens.toLocaleString()} tokens · 历史预算`,
    compression: context.compact_enabled === false ? "自动压缩已关闭" : context.compact_enabled == null ? "压缩状态未知" : context.will_compact ? "下一回合开始前尝试压缩" : "自动压缩已启用",
  };
}

export function ContextStatus({ context }: { context?: SessionContextUsage }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<Record<string, number>>({});
  const button = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const text = contextStatusText(context);
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = button.current?.getBoundingClientRect(); if (!rect) return;
      const width = Math.min(280, window.innerWidth - 24);
      const left = Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12));
      setPosition(rect.top > 180
        ? { left, width, bottom: window.innerHeight - rect.top + 8, maxHeight: rect.top - 20 }
        : { left, width, top: rect.bottom + 8, maxHeight: Math.max(80, window.innerHeight - rect.bottom - 20) });
    };
    const close = (event: PointerEvent) => {
      if (!button.current?.contains(event.target as Node) && !popup.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setOpen(false); button.current?.focus(); }
    };
    place(); popup.current?.focus(); window.addEventListener("resize", place); window.addEventListener("scroll", place, true);
    document.addEventListener("pointerdown", close); document.addEventListener("keydown", escape);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", escape); };
  }, [open]);
  return <>
    <button ref={button} type="button" className="context-status" aria-label={`${text.label} ${text.percent}`} aria-haspopup="dialog" aria-expanded={open} title={text.label} onClick={() => setOpen(value => !value)}>{text.percent}</button>
    {open && createPortal(<div ref={popup} role="dialog" tabIndex={-1} aria-label="上下文用量" className="context-popover" style={position}>
      <div className="context-popover-head"><strong>{text.label}</strong><button type="button" aria-label="关闭上下文信息" onClick={() => { setOpen(false); button.current?.focus(); }}>×</button></div>
      <p>{text.detail}</p>{text.compression && <p>{text.compression}</p>}
      <small>最近快照估算</small>
    </div>, document.body)}
  </>;
}
