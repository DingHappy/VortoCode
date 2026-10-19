import { desktopBuildInfo, type DesktopBuildInfo } from "./buildInfo";

/** 试用反馈的收件地址（与账号网站协议、隐私页上的联系邮箱一致）。 */
export const FEEDBACK_EMAIL = "service@vortotech.com";

/** 「问题反馈」：用系统邮件客户端写信，正文预填版本与系统，便于排查。不自动带任何对话或文件内容。 */
export function feedbackMailto(info: DesktopBuildInfo = desktopBuildInfo, system: string = navigator.userAgent): string {
  const body = [
    "遇到的问题：",
    "",
    "怎么出现的（操作步骤）：",
    "",
    "—— 以下信息帮助我们排查，可删 ——",
    `版本：VortoCode ${info.version}（${info.buildId}）`,
    `系统：${system}`,
  ].join("\n");
  const query = `subject=${encodeURIComponent(`VortoCode ${info.version} 问题反馈`)}&body=${encodeURIComponent(body)}`;
  return `mailto:${FEEDBACK_EMAIL}?${query}`;
}
