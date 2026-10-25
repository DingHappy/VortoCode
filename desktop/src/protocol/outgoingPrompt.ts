// 发出去的一条任务：本地先显示的那条用户消息，以及发给 runtime 的 agent 帧。纯函数。
//
// 源码引用分两种上送：整文件走 context_files，行区间走 context_selections。附件里的文本文件拼进正文，
// 图片/音频走媒体字段；界面上图片显示缩略图，其余附件与引用只列名字。
import { composeMessageText, mediaPayload, type Attachment } from "../lib/attachments";
import { contextItemLabel } from "../lib/sourceContext";
import type { ContextItem, ConversationMessage } from "../types";
import { messageId } from "./transcript";

export type OutgoingPrompt = {
  /** 输入框里的文字（已 trim，可以为空） */
  text: string;
  context: ContextItem[];
  attachments: Attachment[];
  rid: string;
};

/** 只带了附件或引用、没写字时，替用户补一句。 */
export function promptDisplayText({ text, attachments }: Pick<OutgoingPrompt, "text" | "attachments">): string {
  return text || (attachments.length > 0 ? "请查看我添加的附件。" : "请分析我选择的本地文件。");
}

export function outgoingMessage(prompt: OutgoingPrompt): ConversationMessage {
  const displayText = promptDisplayText(prompt);
  const sentImages = prompt.attachments.filter((item) => item.kind === "image").map((item) => item.data);
  const attachmentNote = [
    ...prompt.context.map(contextItemLabel),
    ...prompt.attachments.filter((item) => item.kind !== "image").map((item) => item.name),
  ];
  return {
    id: messageId("user"),
    role: "user",
    text: attachmentNote.length > 0 ? `${displayText}\n\n📎 ${attachmentNote.join(" · ")}` : displayText,
    rid: prompt.rid,
    ...(sentImages.length > 0 ? { images: sentImages } : {}),
  };
}

/**
 * model 只在本机模型配置已读到时才带：服务端只接受已配置的模型，「自动」由它按任务调度。
 * 常驻电脑用它自己配置的模型：本机的模型名在那边不一定有，调用方传 null。
 */
export function agentFrame(prompt: OutgoingPrompt, mode: "plan" | "build", model: string | null) {
  return {
    type: "agent",
    text: composeMessageText(promptDisplayText(prompt), prompt.attachments),
    ...mediaPayload(prompt.attachments),
    mode,
    context_files: prompt.context.filter((item) => !item.startLine).map((item) => item.path),
    context_selections: prompt.context
      .filter((item) => item.startLine && item.endLine)
      .map((item) => ({ path: item.path, start: item.startLine, end: item.endLine })),
    rid: prompt.rid,
    want_reasoning: false,
    ...(model ? { model } : {}),
  };
}
