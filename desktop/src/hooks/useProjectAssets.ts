// 项目资产域 hook（仓库记忆 + 制品预览；沿用 useJournal 的 hook 模式与三条红线）。
//
// 收进来的是资产域自身状态、制品预览代数守卫、refresh/load 两个读回调和整域清空。
// 留在 App 的是跨域写入：addRepoMemoryFact 还要刷新审计域、attachSelectedArtifact 写输入框、
// openSelectedArtifact 要看 token；按连接状态自动加载预览的 effect 依赖 App 的 connection，也留在 App。
//
// 红线 3：refreshProjectAssets / loadArtifactPreview 被 refreshAllForScope 与 effect 依赖，
// 必须恒等稳定（依赖只有恒等的 clientRef，与迁移前的 [] 等价）。
import { useCallback, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import { errorText } from "../lib/errorText";
import type { ArtifactMeta, ArtifactVersionSnapshot, RepoMemorySnapshot } from "../types";

/** 制品在 Desktop 内置沙箱里只读预览：去掉跳转/base，并注入禁脚本、禁外连的 CSP。 */
function secureArtifactDocument(content: string): string {
  const parsed = new DOMParser().parseFromString(content, "text/html");
  parsed.querySelectorAll('meta[http-equiv="refresh"], base').forEach((node) => node.remove());
  const policy = parsed.createElement("meta");
  policy.httpEquiv = "Content-Security-Policy";
  policy.content = [
    "default-src 'none'",
    "img-src data: blob:",
    "media-src data: blob:",
    "style-src 'unsafe-inline'",
    "script-src 'none'",
    "font-src data:",
    "form-action 'none'",
    "base-uri 'none'",
  ].join("; ");
  parsed.head.prepend(policy);
  return `<!doctype html>${parsed.documentElement.outerHTML}`;
}

export function useProjectAssets(clientRef: RefObject<GatewayClient | null>) {
  const artifactPreviewGenerationRef = useRef(0);
  const [projectAssetView, setProjectAssetView] = useState<"memory" | "artifacts">("memory");
  const [repoMemory, setRepoMemory] = useState<RepoMemorySnapshot | null>(null);
  const [repoMemoryDraft, setRepoMemoryDraft] = useState("");
  const [projectAssetsLoading, setProjectAssetsLoading] = useState(false);
  const [projectAssetsError, setProjectAssetsError] = useState("");
  const [artifacts, setArtifacts] = useState<ArtifactMeta[]>([]);
  const [selectedArtifactId, setSelectedArtifactId] = useState("");
  const [artifactVersions, setArtifactVersions] = useState<ArtifactVersionSnapshot | null>(null);
  const [artifactVersion, setArtifactVersion] = useState<number | null>(null);
  const [artifactHtml, setArtifactHtml] = useState("");
  const [artifactPreviewLoading, setArtifactPreviewLoading] = useState(false);

  const selectedArtifact = artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? null;
  const securedArtifactHtml = useMemo(
    () => artifactHtml ? secureArtifactDocument(artifactHtml) : "",
    [artifactHtml],
  );

  const refreshProjectAssets = useCallback(async (includeMemory = true) => {
    const client = clientRef.current;
    if (!client) return;
    setProjectAssetsLoading(true);
    setProjectAssetsError("");
    const [memoryResult, artifactsResult] = await Promise.allSettled([
      includeMemory ? client.getRepoMemory() : Promise.resolve(null),
      client.listArtifacts(),
    ]);
    const failures: string[] = [];
    if (!includeMemory) {
      setRepoMemory(null);
      setProjectAssetView("artifacts");
    } else if (memoryResult.status === "fulfilled") {
      if (memoryResult.value) setRepoMemory(memoryResult.value);
    } else {
      failures.push(errorText(memoryResult.reason, "读取仓库记忆失败"));
    }
    if (artifactsResult.status === "fulfilled") {
      const items = artifactsResult.value;
      setArtifacts(items);
      setSelectedArtifactId((current) => (
        current && items.some((item) => item.id === current) ? current : items[0]?.id ?? ""
      ));
      if (items.length === 0) {
        setArtifactVersions(null);
        setArtifactVersion(null);
        setArtifactHtml("");
      }
    } else {
      failures.push(errorText(artifactsResult.reason, "读取制品失败"));
    }
    setProjectAssetsError(failures.join("；"));
    setProjectAssetsLoading(false);
  }, [clientRef]);

  const loadArtifactPreview = useCallback(async (artifactId: string, requestedVersion?: number) => {
    const client = clientRef.current;
    if (!client || !artifactId) return;
    const generation = artifactPreviewGenerationRef.current + 1;
    artifactPreviewGenerationRef.current = generation;
    setArtifactPreviewLoading(true);
    setProjectAssetsError("");
    try {
      const versions = await client.getArtifactVersions(artifactId);
      const version = requestedVersion ?? versions.pinned ?? versions.current;
      const html = await client.getArtifactHtml(artifactId, version);
      if (generation !== artifactPreviewGenerationRef.current) return;
      setArtifactVersions(versions);
      setArtifactVersion(version);
      setArtifactHtml(html);
    } catch (error) {
      if (generation !== artifactPreviewGenerationRef.current) return;
      setArtifactVersions(null);
      setArtifactVersion(null);
      setArtifactHtml("");
      setProjectAssetsError(errorText(error, "读取制品预览失败"));
    } finally {
      if (generation === artifactPreviewGenerationRef.current) setArtifactPreviewLoading(false);
    }
  }, [clientRef]);

  /** 切换项目时整域清空；代数 +1 让仍在途的旧预览请求落地时被丢弃。 */
  const resetProjectAssets = () => {
    setProjectAssetView("memory");
    setRepoMemory(null);
    setRepoMemoryDraft("");
    setProjectAssetsLoading(false);
    setProjectAssetsError("");
    setArtifacts([]);
    setSelectedArtifactId("");
    setArtifactVersions(null);
    setArtifactVersion(null);
    setArtifactHtml("");
    setArtifactPreviewLoading(false);
    artifactPreviewGenerationRef.current += 1;
  };

  return {
    projectAssetView, setProjectAssetView, repoMemory, setRepoMemory, repoMemoryDraft, setRepoMemoryDraft,
    projectAssetsLoading, setProjectAssetsLoading, projectAssetsError, setProjectAssetsError,
    artifacts, selectedArtifactId, setSelectedArtifactId, selectedArtifact,
    artifactVersions, artifactVersion, artifactPreviewLoading, securedArtifactHtml,
    refreshProjectAssets, loadArtifactPreview, resetProjectAssets,
  };
}
