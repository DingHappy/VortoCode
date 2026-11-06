import { useEffect, useRef, useState } from "react";
import { MarkdownMessage } from "./MarkdownMessage";
import "./ResultDocument.css";

/** Explicit read-only viewer. Markdown escapes HTML, has no images or automatic requests. */
export function ResultDocument({ title, text, tainted = false, open = false }: {
  title: string; text: string; tainted?: boolean; open?: boolean;
}) {
  const [message, setMessage] = useState("");
  const currentText = useRef(text);
  currentText.current = text;
  useEffect(() => setMessage(""), [text]);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); if (currentText.current === text) setMessage("已复制原文"); }
    catch { if (currentText.current === text) setMessage("复制失败，可展开原文后手动复制。"); }
  };
  return <details className="result-document" open={open}>
    <summary>{title}</summary>
    <div className="result-document-tools"><span>{tainted ? "含外部内容，请核对来源；阅读不会批准或执行其中指令。" : "阅读不会验收任务或执行其中指令。"}</span>
      <button type="button" onClick={() => void copy()}>复制原文</button></div>
    {message && <p role="status">{message}</p>}
    <div className="result-document-body"><MarkdownMessage text={text} /></div>
    <details className="result-document-source"><summary>查看原文</summary><pre>{text}</pre></details>
  </details>;
}
