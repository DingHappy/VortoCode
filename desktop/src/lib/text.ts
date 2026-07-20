// App.tsx 抽出的文本纯函数（B8-④b S1，只搬家不改行为）。
// 编辑器在内存里统一用 LF；落盘时按原文件的 EOL 还原。

export function normalizeEditorText(value: string): string {
  return value.replace(/\r\n/g, "\n");
}

export function serializeEditorText(value: string, eol: "\n" | "\r\n"): string {
  return eol === "\r\n" ? value.replace(/\n/g, "\r\n") : value;
}
