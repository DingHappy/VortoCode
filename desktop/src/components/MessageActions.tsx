import { useEffect, useId, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { HintButton } from "./ui";

/** Copies only the already displayed text, without fetching or changing the conversation. */
export async function copyMessageText(text: string, write: (text: string) => Promise<void>) {
  try { await write(text); return "copied" as const; }
  catch { return "failed" as const; }
}

export function MessageActions({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copying" | "copied" | "failed">("idle");
  const generation = useRef(0), statusId = useId();
  useEffect(() => {
    generation.current += 1; setState("idle");
    return () => { generation.current += 1; };
  }, [text]);
  const copy = async () => {
    if (state === "copying") return;
    const current = generation.current; setState("copying");
    const result = await copyMessageText(text, value => navigator.clipboard.writeText(value));
    if (current === generation.current) setState(result);
  };
  if (!text.trim()) return null;
  const status = state === "copied" ? "已复制" : state === "failed" ? "复制失败，可重试或选中文字复制" : "";
  return <div className={`message-actions${state === "failed" ? " has-error" : ""}`}>
    <HintButton className="message-copy" aria-label="复制消息" aria-describedby={status ? statusId : undefined}
      hint="复制消息原文" disabled={state === "copying"} onClick={() => void copy()}>
      {state === "copied" ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
    </HintButton>
    <span id={statusId} role="status">{status}</span>
  </div>;
}
