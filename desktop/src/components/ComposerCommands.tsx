import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ClipboardEvent, type ReactNode } from "react";
import { Plus } from "lucide-react";

export const COMPOSER_COMMANDS = [
  { id: "attach", label: "添加附件", description: "图片、音频或文本文件" },
  { id: "project", label: "选择项目", description: "进入 Git 项目工作" },
  { id: "files", label: "引用源码", description: "从当前项目选择文件" },
  { id: "info", label: "运行信息", description: "查看步骤、上下文与压缩策略" },
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
  const open = expanded || slash;
  const choices = matchingCommands(expanded ? "" : prompt, project);
  useEffect(() => { setSelected(0); setDismissed(false); }, [prompt]);
  // 输入框随内容长高（CSS 封顶 180px，再多就滚动），不用手动拖右下角
  const textarea = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const node = textarea.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [prompt]);
  const act = (id: ComposerCommand) => {
    if (disabled) return;
    setExpanded(false); setDismissed(true);
    if (slash) onPrompt("");
    onAction(id);
  };
  const keyDown = (event: KeyboardEvent) => {
    if (event.nativeEvent.isComposing || (!(event.target instanceof HTMLTextAreaElement) && !(event.target as HTMLElement).closest(".composer-command-menu"))) return;
    if (open && event.key === "Escape") { event.preventDefault(); setExpanded(false); setDismissed(true); return; }
    if (open && ["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault(); setSelected(index => choices.length ? (index + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length : 0); return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (open) { if (choices[selected]) act(choices[selected].id); }
      else onSend();
    }
  };
  return <div className="composer-command-host" onKeyDown={keyDown}>
    {open && <div className="composer-command-menu" role="dialog" aria-label="快捷操作">
      <div className="composer-command-head">快捷操作 <button aria-label="关闭快捷操作" onClick={() => { setExpanded(false); setDismissed(true); }}>关闭</button></div>
      {choices.map((item, index) => <button key={item.id} disabled={disabled} className={index === selected ? "selected" : ""} onClick={() => act(item.id)}>
        <strong>/{item.id} · {item.label}</strong><small>{item.description}</small>
      </button>)}
      {!choices.length && <p>没有匹配的快捷操作；Esc 关闭后可发送原文。</p>}
    </div>}
    <textarea ref={textarea} onPaste={onPaste} aria-label="任务输入" value={prompt} onChange={event => onPrompt(event.target.value)} placeholder={placeholder} />
    <div className="composer-command-toolbar">
      <button className="composer-attach" aria-label="添加与快捷操作" title="添加附件、引用源码或选择项目" aria-expanded={open} disabled={disabled} onClick={() => { setExpanded(value => !value); setDismissed(false); }}><Plus size={17} aria-hidden="true" /></button>
      {children}
    </div>
  </div>;
}
