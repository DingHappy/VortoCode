// 输入框附件（图片 / 音频 / 文本类文件）。规则与限额在 lib/attachments.ts；这里只管读文件和状态。
// 发送失败时由 App 调 restore 放回，附件不丢。
import { useCallback, useEffect, useRef, useState } from "react";

import {
  type Attachment,
  type AttachmentKind,
  checkAttachment,
  classifyAttachment,
} from "../lib/attachments";

function readFile(file: File, kind: AttachmentKind): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("读取文件失败"));
    if (kind === "text") reader.readAsText(file);
    else reader.readAsDataURL(file);
  });
}

let sequence = 0;

export function useAttachments(onBanner: (text: string) => void) {
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const currentRef = useRef(attachments);
  useEffect(() => { currentRef.current = attachments; }, [attachments]);

  const addFiles = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files);
    const added: Attachment[] = [];
    for (const file of list) {
      const name = file.name || (file.type.startsWith("image/") ? "粘贴的图片.png" : "附件");
      const kind = classifyAttachment(name, file.type);
      if (!kind) {
        onBanner(`暂不支持 ${name}：可以添加图片、音频（mp3/wav/m4a/flac/ogg）或文本类文件`);
        continue;
      }
      const check = checkAttachment({ name, kind, size: file.size }, [...currentRef.current, ...added]);
      if (!check.ok) {
        onBanner(check.reason);
        continue;
      }
      try {
        const data = await readFile(file, kind);
        if (kind !== "text" && !data.startsWith(kind === "image" ? "data:image/" : "data:audio/")) {
          onBanner(`${name} 的文件类型无法识别`);
          continue;
        }
        added.push({ id: `att-${Date.now()}-${sequence += 1}`, name, kind, size: file.size, data });
      } catch {
        onBanner(`读取 ${name} 失败`);
      }
    }
    if (added.length) setAttachments((previous) => [...previous, ...added]);
  }, [onBanner]);

  const removeAttachment = useCallback((id: string) => {
    setAttachments((previous) => previous.filter((item) => item.id !== id));
  }, []);

  return { attachments, setAttachments, addFiles, removeAttachment };
}
