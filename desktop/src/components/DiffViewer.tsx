// App.tsx 抽出的结构化 diff 展示组件（B8-④b S1，只搬家不改行为）。
// dev 流水线请求确认前把提案 diff 逐行着色展示。
import type { DiffPayload } from "../types";

export function DiffViewer({ payload }: { payload: DiffPayload | null }) {
  if (!payload?.diff) {
    return (
      <div className="panel-empty">
        <span className="panel-empty-icon">±</span>
        <strong>等待改动</strong>
        <p>dev 流水线请求确认前，结构化 diff 会出现在这里。</p>
      </div>
    );
  }

  return (
    <div className="diff-view">
      <div className="diff-title">{payload.title || "待审查改动"}</div>
      <pre>
        {payload.diff.split("\n").map((line, index) => {
          const kind = line.startsWith("+++") || line.startsWith("---")
            ? "meta"
            : line.startsWith("+")
              ? "add"
              : line.startsWith("-")
                ? "remove"
                : line.startsWith("@@")
                  ? "hunk"
                  : "context";
          return (
            <span className={`diff-line ${kind}`} key={`${index}-${line.slice(0, 12)}`}>
              <span className="diff-number">{index + 1}</span>
              <span>{line || " "}</span>
            </span>
          );
        })}
      </pre>
    </div>
  );
}
