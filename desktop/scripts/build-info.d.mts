export function createBuildInfo(desktopRoot: string, now?: Date): {
  version: string; builtAt: string; sourceFingerprint: string; buildId: string;
};
