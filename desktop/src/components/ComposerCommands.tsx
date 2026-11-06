import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ClipboardEvent, type ReactNode } from "react";
import { Plus } from "lucide-react";
import { HintButton } from "./ui";
import { commandKeyTarget } from "../lib/iconNavigation";

export const COMPOSER_COMMANDS = [
  { id: "attach", label: "添加附件", description: "图片、音频或文本文件" },
  { id: "project", label: "选择项目", description: "进入 Git 项目工作" },
  { id: "files", label: "引用源码", description: "从当前项目选择文件" },
  { id: "info", label: "运行信息", description: "查看步骤、上下文与压缩策略" },
  { id: "capabilities", label: "此对话的能力", description: "当前运行端的工具、连接与限制（只读）" },
  { id: "model", label: "模型设置", description: "连接、模型和能力配置" },
  { id: "new", label: "新建会话", description: "开始独立会话，保留当前历史" },
] as const;
export type ComposerCommand = typeof COMPOSER_COMMANDS[number]["id"];
export function matchingCommands(prompt: string, project: boolean) {
  const query = prompt.startsWith("/") ? prompt.slice(1).trim().toLowerCase() : "";
  return COMPOSER_COMMANDS.filter(item => (project || item.id !== "files") && `${item.id} ${item.label} ${item.description}`.toLowerCase().includes(query));
}

export function ComposerCommands({ prompt, onPrompt, project, disabled, onAction, onSend, onPaste, placeholder = "描述任务，输入 / 查看快捷操作，Shift+Enter 换行…", children }: {
  prompt: string; onPrompt: (value: string) => void; project: boolean; disabled: boolean; placeholder?: string;
  onAction: (command: ComposerCommand) => void; onSend: () => void; onPaste?: (event: ClipboardEvent<HTMLTextAreaElement>) => void; children?: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [selected, setSelected] = useState(0);
  const slash = prompt.startsWith("/") && !prompt.includes("\n") && !dismissed;
  const open = !disabled && (expanded || slash);
  const choices = matchingCommands(expanded ? "" : prompt, project);
  const active = Math.min(selected, choices.length - 1);
  const menuId = useId(), host = useRef<HTMLDivElement>(null), menu = useRef<HTMLDivElement>(null);
  useEffect(() => { setSelected(0); setDismissed(false); }, [prompt]);
  // 输入框随内容长高（CSS 封顶 180px，再多就滚动），不用手动拖右下角
  const textarea = useRef<HTMLTextAreaElement>(null);
  const dismiss = (restoreFocus = false) => {
    setExpanded(false); setDismissed(true);
    if (restoreFocus) textarea.current?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !host.current?.contains(event.target)) dismiss(); };
    document.addEventListener("pointerdown", outside, true);
    return () => document.removeEventListener("pointerdown", outside, true);
  }, [open]);
  useEffect(() => { if (disabled) dismiss(); }, [disabled]);
  useLayoutEffect(() => {
    if (open && expanded) menu.current?.querySelector<HTMLButtonElement>('[data-command-index="0"]')?.focus({ preventScroll: true });
  }, [open, expanded]);
  useLayoutEffect(() => {
    if (open && active >= 0) menu.current?.querySelector<HTMLElement>(`[data-command-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active, prompt]);
  useLayoutEffect(() => {
    const node = textarea.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [prompt]);
  const act = (id: ComposerCommand) => {
    if (disabled) return;
    dismiss(true);
    if (slash) onPrompt("");
    if (id === "capabilities") textarea.current?.focus();
    onAction(id);
  };
  const keyDown = (event: KeyboardEvent) => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (disabled) { if (event.target === textarea.current && event.key === "Enter" && !event.shiftKey) event.preventDefault(); return; }
    if (open && event.key === "Escape") {
      event.preventDefault(); event.stopPropagation(); dismiss(true); return;
    }
    const inMenu = menu.current?.contains(event.target as Node);
    if (open && inMenu) {
      const row = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-command-index]");
      const next = commandKeyTarget(event.key, row ? Number(row.dataset.commandIndex) : -1, choices.length);
      if (next !== null) {
        event.preventDefault(); setSelected(next);
        menu.current?.querySelector<HTMLButtonElement>(`[data-command-index="${next}"]`)?.focus({ preventScroll: true });
      }
      // Enter and Space belong to the focused native button, including the close button.
      return;
    }
    if (event.target !== textarea.current) return;
    if (open && ["ArrowDown", "ArrowUp"].includes(event.key)) {
      const next = commandKeyTarget(event.key, active, choices.length);
      event.preventDefault(); if (next !== null) setSelected(next); return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (open) { if (choices[active]) act(choices[active].id); }
      else onSend();
    }
  };
  return <div ref={host} className="composer-command-host" onKeyDown={keyDown} onBlur={event => {
    if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) dismiss();
  }}>
    {open && <div ref={menu} id={menuId} className="composer-command-menu" role="dialog" aria-label="快捷操作" aria-describedby={`${menuId}-help`}>
      <div className="composer-command-heading">
        <div className="composer-command-head">快捷操作 <button type="button" aria-label="关闭快捷操作" onClick={() => dismiss(true)}>关闭</button></div>
        <p className="composer-command-help" id={`${menuId}-help`}>方向键选择，Enter 打开，Esc 关闭</p>
      </div>
      <div className="composer-command-list">
      {choices.map((item, index) => <button type="button" key={item.id} data-command-index={index} disabled={disabled} className={index === active ? "selected" : ""}
        onFocus={() => setSelected(index)} onClick={() => act(item.id)}>
        <strong>/{item.id} · {item.label}</strong><small>{item.description}</small>
      </button>)}
      {!choices.length && <p>没有匹配的快捷操作；Esc 关闭后可发送原文。</p>}
      </div>
      <span className="composer-command-selection" role="status">{choices[active] ? `已选择 ${choices[active].label}，共 ${choices.length} 项` : "没有匹配项"}</span>
    </div>}
    <textarea ref={textarea} onPaste={onPaste} aria-label="任务输入" aria-controls={open ? menuId : undefined}
      aria-describedby={open ? `${menuId}-help` : undefined} value={prompt} onChange={event => onPrompt(event.target.value)}
      onFocus={() => { if (expanded) dismiss(); }} placeholder={placeholder} />
    <div className="composer-command-toolbar">
      <HintButton className="composer-attach" aria-label="添加与快捷操作" hint="添加附件、引用源码或选择项目" aria-haspopup="dialog" aria-controls={open ? menuId : undefined}
        aria-expanded={open} disabled={disabled} onClick={() => { setExpanded(value => !value); setDismissed(false); }}><Plus size={17} aria-hidden="true" /></HintButton>
      {children}
    </div>
  </div>;
}
