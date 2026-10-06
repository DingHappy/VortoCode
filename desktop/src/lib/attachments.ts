// 输入框附件：图片 / 音频随消息以 data: URL 发给 runtime（后端 _sanitize_images/_sanitize_audio
// 已支持，模型侧拼多模态块）；文本类文件把内容作为带文件名的代码块拼进消息。PDF、Office、
// 压缩包等后端没有解析能力的格式如实拒绝，不假装能处理。
export type AttachmentKind = "image" | "audio" | "text";

export interface Attachment {
  id: string;
  name: string;
  kind: AttachmentKind;
  size: number;
  /** image/audio：data: URL；text：文件内容。 */
  data: string;
}

// 与 runtime 对齐：_MAX_IMAGES = 6（图片、音频各自计数），单条 data: URL ≤ 8M 字符。
export const MAX_MEDIA_PER_KIND = 6;
export const MAX_DATA_URL_CHARS = 8 * 1024 * 1024;
export const MAX_TEXT_FILE_BYTES = 100 * 1024;
export const MAX_TEXT_TOTAL_BYTES = 200 * 1024;

const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp"]);
// 中转站实测只收这几种音频（webm 会 400）。
const AUDIO_EXT = new Set(["mp3", "wav", "m4a", "flac", "ogg"]);
const TEXT_EXT = new Set([
  "txt", "md", "markdown", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml", "xml", "html", "css",
  "js", "jsx", "ts", "tsx", "py", "rs", "go", "java", "kt", "swift", "c", "h", "cpp", "hpp", "cs",
  "rb", "php", "sh", "zsh", "sql", "ini", "cfg", "conf", "log", "env.example",
]);

function extension(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith(".env.example")) return "env.example";
  const dot = lower.lastIndexOf(".");
  return dot >= 0 ? lower.slice(dot + 1) : "";
}

/** 按扩展名（其次 MIME）判断附件类型；不支持的返回 null。 */
export function classifyAttachment(name: string, mime = ""): AttachmentKind | null {
  const ext = extension(name);
  if (IMAGE_EXT.has(ext) || (!ext && mime.startsWith("image/"))) return "image";
  if (AUDIO_EXT.has(ext)) return "audio";
  if (TEXT_EXT.has(ext) || (!ext && mime.startsWith("text/"))) return "text";
  return null;
}

export type AttachmentCheck = { ok: true } | { ok: false; reason: string };

/** 加入一个附件前的限额检查（existing 是已在输入框里的附件）。 */
export function checkAttachment(
  candidate: { name: string; kind: AttachmentKind; size: number },
  existing: Attachment[],
): AttachmentCheck {
  if (candidate.kind === "text") {
    if (candidate.size > MAX_TEXT_FILE_BYTES) return { ok: false, reason: `${candidate.name} 超过 100KB，文本附件请精简后再发` };
    const total = existing.filter((item) => item.kind === "text").reduce((sum, item) => sum + item.size, 0);
    if (total + candidate.size > MAX_TEXT_TOTAL_BYTES) return { ok: false, reason: "文本附件合计超过 200KB" };
    return { ok: true };
  }
  if (existing.filter((item) => item.kind === candidate.kind).length >= MAX_MEDIA_PER_KIND) {
    return { ok: false, reason: `${candidate.kind === "image" ? "图片" : "音频"}最多 ${MAX_MEDIA_PER_KIND} 个` };
  }
  // data: URL 约为原文件 4/3 再加头部。
  if (Math.ceil(candidate.size / 3) * 4 + 64 > MAX_DATA_URL_CHARS) {
    return { ok: false, reason: `${candidate.name} 太大（单个约 6MB 以内）` };
  }
  return { ok: true };
}

/** 把文本附件拼进消息正文：每个文件一个带文件名的代码块。 */
export function composeMessageText(text: string, attachments: Attachment[]): string {
  const blocks = attachments
    .filter((item) => item.kind === "text")
    .map((item) => `附件 ${item.name}：\n\`\`\`\n${item.data.replace(/```/g, "ˋˋˋ")}\n\`\`\``);
  return [text.trim(), ...blocks].filter(Boolean).join("\n\n");
}

export function mediaPayload(attachments: Attachment[]): { images: string[]; audio: string[] } {
  return {
    images: attachments.filter((item) => item.kind === "image").map((item) => item.data),
    audio: attachments.filter((item) => item.kind === "audio").map((item) => item.data),
  };
}
