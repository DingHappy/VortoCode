export type DesktopBuildInfo = { version: string; buildId: string; builtAt: string; sourceFingerprint: string };
declare const __DESKTOP_BUILD_INFO__: DesktopBuildInfo;
export const desktopBuildInfo: DesktopBuildInfo = typeof __DESKTOP_BUILD_INFO__ === "undefined"
  ? { version: "开发版", buildId: "未生成构建标识", builtAt: "", sourceFingerprint: "" }
  : __DESKTOP_BUILD_INFO__;
