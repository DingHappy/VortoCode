// 助理头像：有图用图（常驻电脑上存的那张），没有就用名字首字 + 按名字定的颜色。
import type { AssistantAvatar } from "../types";

const ALLOWED_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;

/** 只把三种位图拼成 data URL；类型不对或数据不像 base64 就当没有（不把任意字符串塞进 src）。 */
export function avatarSrc(avatar: AssistantAvatar | null | undefined): string | null {
  if (!avatar || !ALLOWED_TYPES.has(avatar.type) || !avatar.data || !BASE64.test(avatar.data)) return null;
  return `data:${avatar.type};base64,${avatar.data}`;
}

/** 名字的第一个字（按字符而非 UTF-16 单元切，emoji/生僻字不切坏）。 */
export function avatarInitial(name: string): string {
  const first = Array.from(name.trim())[0] ?? "V";
  return first.toUpperCase();
}

/** 同一个名字永远同一个色相，换了名字颜色跟着变。 */
export function avatarHue(name: string): number {
  let hash = 0;
  for (const ch of name.trim() || "VortoCode") hash = (hash * 31 + (ch.codePointAt(0) ?? 0)) >>> 0;
  return hash % 360;
}

export const AVATAR_SIZE = 256;
const MAX_UPLOAD_BYTES = 480 * 1024;

/** 选好的图片裁成正方形、缩到 256px，返回 {type, data(base64)}。浏览器里跑（用 canvas）。 */
export async function prepareAvatar(file: File): Promise<AssistantAvatar> {
  if (!file.type.startsWith("image/")) throw new Error("请选一张图片");
  const bitmap = await createImageBitmap(file);
  const side = Math.min(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = AVATAR_SIZE;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("无法处理这张图片");
  context.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, AVATAR_SIZE, AVATAR_SIZE);
  bitmap.close();
  for (const [type, quality] of [["image/png", undefined], ["image/jpeg", 0.9], ["image/jpeg", 0.75]] as const) {
    const url = canvas.toDataURL(type, quality);
    const data = url.slice(url.indexOf(",") + 1);
    if (data.length * 0.75 <= MAX_UPLOAD_BYTES) return { type, data };
  }
  throw new Error("图片太大了，换一张试试");
}
