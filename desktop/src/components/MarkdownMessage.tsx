// App.tsx 抽出的 Markdown 渲染组件（B8-④b S1，只搬家不改行为）。
// 一个极简的行级 Markdown 渲染器：代码块 / 标题 / 引用 / 列表 / 段落 + 行内标记。
// safeLink / inlineMarkdown 是它的私有辅助函数，随组件一起搬出。
import { openUrl } from "@tauri-apps/plugin-opener";
import type { ReactNode } from "react";

function safeLink(value: string): string | null {
  const url = value.trim();
  return /^(https?:|mailto:)/i.test(url) ? url : null;
}

function inlineMarkdown(text: string, key: string): ReactNode[] {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\n]+\*|\[[^\]]+\]\([^\s)]+\))/g;
  const output: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > cursor) output.push(text.slice(cursor, start));
    const token = match[0];
    const tokenKey = `${key}-${start}`;
    if (token.startsWith("`")) {
      output.push(<code key={tokenKey}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      output.push(<strong key={tokenKey}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("*")) {
      output.push(<em key={tokenKey}>{token.slice(1, -1)}</em>);
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = link ? safeLink(link[2]) : null;
      output.push(href ? (
        <a
          href={href}
          key={tokenKey}
          rel="noopener noreferrer"
          onClick={(event) => {
            event.preventDefault();
            void openUrl(href);
          }}
        >
          {link?.[1]}
        </a>
      ) : token);
    }
    cursor = start + token.length;
  }
  if (cursor < text.length) output.push(text.slice(cursor));
  return output;
}

export function MarkdownMessage({ text }: { text: string }) {
  const lines = String(text).replace(/\u0000/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = line.match(/^```\s*([\w+-]*)\s*$/);
    if (fence) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push(<pre key={`code-${index}`}><code data-language={fence[1] || undefined}>{code.join("\n")}</code></pre>);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      blocks.push(<h3 className={`md-heading md-h${heading[1].length}`} key={`heading-${index}`}>{inlineMarkdown(heading[2], `h-${index}`)}</h3>);
      index += 1;
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      blocks.push(<hr key={`rule-${index}`} />);
      index += 1;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      blocks.push(<blockquote key={`quote-${index}`}>{quote.map((value, quoteIndex) => <p key={quoteIndex}>{inlineMarkdown(value, `q-${index}-${quoteIndex}`)}</p>)}</blockquote>);
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) items.push(lines[index++].replace(/^\s*[-*+]\s+/, ""));
      blocks.push(<ul key={`ul-${index}`}>{items.map((value, itemIndex) => <li key={itemIndex}>{inlineMarkdown(value, `ul-${index}-${itemIndex}`)}</li>)}</ul>);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) items.push(lines[index++].replace(/^\s*\d+[.)]\s+/, ""));
      blocks.push(<ol key={`ol-${index}`}>{items.map((value, itemIndex) => <li key={itemIndex}>{inlineMarkdown(value, `ol-${index}-${itemIndex}`)}</li>)}</ol>);
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim()
      && !/^(#{1,6})\s+/.test(lines[index])
      && !/^```/.test(lines[index])
      && !/^\s*(?:>|[-*+]\s+|\d+[.)]\s+)/.test(lines[index])) {
      paragraph.push(lines[index++]);
    }
    blocks.push(
      <p key={`p-${index}`}>
        {paragraph.map((value, paragraphIndex) => (
          <span key={paragraphIndex}>{inlineMarkdown(value, `p-${index}-${paragraphIndex}`)}{paragraphIndex < paragraph.length - 1 && <br />}</span>
        ))}
      </p>,
    );
  }
  return <div className="markdown-body">{blocks}</div>;
}
