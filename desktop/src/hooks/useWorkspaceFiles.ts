// 「代码」面板：工作区文件列表、源码预览/编辑/保存，以及随下一条消息发出的源码引用。
//
// 保存走 runtime 的 workspace_edit（经确认门），结果由 App 的协议事件转给 settleWorkspaceSave。
// 远端工作区不读本机同名目录。refreshWorkspaceFiles / settleWorkspaceSave 被 App 的 useCallback
// 依赖，必须恒等稳定（只依赖 ref 与 setter）。切项目、换会话时 App 调 reset / abandon 清理。
import { useCallback, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { invoke } from "@tauri-apps/api/core";

import { createSessionId, DESKTOP_PROTOCOL_VERSION, type GatewayClient } from "../gateway";
import { confirmAction } from "../lib/confirm";
import { errorText } from "../lib/errorText";
import { addContextItem, contextItemKey, selectSourceLine as nextSourceSelection, visibleWorkspaceFiles } from "../lib/sourceContext";
import { normalizeEditorText, serializeEditorText } from "../lib/text";
import type {
  ConnectionState, ContextItem, OpenWorkspaceFileResult, ProtocolEvent, SourceSelection, WorkspaceFileContent, WorkspaceFileList,
} from "../types";

type PendingWorkspaceSave = { rid: string; path: string; content: string; buffer: string };

export function useWorkspaceFiles({ clientRef, remoteProjectRef, repoRoot, runtimeWorkdir, connection, protocolVersion, onBanner }: {
  clientRef: RefObject<GatewayClient | null>;
  remoteProjectRef: RefObject<string | null>;
  repoRoot: string;
  runtimeWorkdir: string | undefined;
  connection: ConnectionState;
  protocolVersion: number | null;
  onBanner: (text: string) => void;
}) {
  const workspaceRootRef = useRef("");
  const pendingWorkspaceSaveRef = useRef<PendingWorkspaceSave | null>(null);
  const [workspaceFiles, setWorkspaceFiles] = useState<string[]>([]);
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [workspaceTruncated, setWorkspaceTruncated] = useState(false);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [fileQuery, setFileQuery] = useState("");
  const [selectedFile, setSelectedFile] = useState("");
  const [filePreview, setFilePreview] = useState<WorkspaceFileContent | null>(null);
  const [contextItems, setContextItems] = useState<ContextItem[]>([]);
  const [sourceSelection, setSourceSelection] = useState<SourceSelection | null>(null);
  const [editorMode, setEditorMode] = useState(false);
  const [editorContent, setEditorContent] = useState("");
  const [editorEol, setEditorEol] = useState<"\n" | "\r\n">("\n");
  const [savingFile, setSavingFile] = useState(false);

  const filteredWorkspaceFiles = useMemo(() => visibleWorkspaceFiles(workspaceFiles, fileQuery), [fileQuery, workspaceFiles]);
  const editorDirty = Boolean(filePreview && editorContent !== normalizeEditorText(filePreview.content));
  const workspaceMatchesRuntime = Boolean(
    runtimeWorkdir && workspaceRoot && runtimeWorkdir === workspaceRoot,
  );
  const previewContextItem: ContextItem | null = filePreview
    ? sourceSelection
      ? { path: filePreview.path, startLine: sourceSelection.start, endLine: sourceSelection.end }
      : { path: filePreview.path }
    : null;
  const previewContextAttached = Boolean(
    previewContextItem && contextItems.some((item) => contextItemKey(item) === contextItemKey(previewContextItem)),
  );

  const refreshWorkspaceFiles = useCallback(async (root: string) => {
    if (remoteProjectRef.current) {
      workspaceRootRef.current = "";
      setWorkspaceRoot("");
      setWorkspaceFiles([]);
      setWorkspaceError("远端工作区的结果、日志和变更请在任务与变更面板查看；这里不会读取本机同名目录。");
      return;
    }
    if (!root.trim()) return;
    workspaceRootRef.current = root.trim();
    setWorkspaceLoading(true);
    setWorkspaceError("");
    try {
      const result = await invoke<WorkspaceFileList>("list_workspace_files", { repoRoot: root.trim() });
      workspaceRootRef.current = result.root;
      setWorkspaceRoot(result.root);
      setWorkspaceFiles(result.files);
      setWorkspaceTruncated(result.truncated);
      setSelectedFile("");
      setFilePreview(null);
      setSourceSelection(null);
      setContextItems([]);
      setEditorMode(false);
      setEditorContent("");
      setEditorEol("\n");
    } catch (error) {
      workspaceRootRef.current = "";
      setWorkspaceRoot("");
      setWorkspaceFiles([]);
      setWorkspaceError(error instanceof Error ? error.message : String(error));
    } finally {
      setWorkspaceLoading(false);
    }
  }, [remoteProjectRef]);

  const openWorkspaceFile = useCallback(async (path: string) => {
    if (remoteProjectRef.current) {
      onBanner("远端源码请通过任务变更查看；不会打开本机同名文件。");
      return;
    }
    if (editorDirty && path !== selectedFile && !await confirmAction("当前文件有未保存修改，确定放弃并打开其他文件吗？")) {
      return;
    }
    const root = workspaceRoot || repoRoot.trim() || runtimeWorkdir || "";
    if (!root) return;
    setWorkspaceLoading(true);
    setWorkspaceError("");
    setSelectedFile(path);
    setSourceSelection(null);
    setEditorMode(false);
    try {
      const loaded = await invoke<WorkspaceFileContent>("read_workspace_file", {
        repoRoot: root,
        relativePath: path,
      });
      setFilePreview(loaded);
      setEditorEol(loaded.content.includes("\r\n") ? "\r\n" : "\n");
      setEditorContent(normalizeEditorText(loaded.content));
    } catch (error) {
      setFilePreview(null);
      setWorkspaceError(error instanceof Error ? error.message : String(error));
    } finally {
      setWorkspaceLoading(false);
    }
  }, [editorDirty, repoRoot, runtimeWorkdir, selectedFile, workspaceRoot, remoteProjectRef, onBanner]);

  /** 协议事件 workspace_edit_result：不是当前这次保存的结果返回 stale，App 照旧忽略。 */
  const settleWorkspaceSave = useCallback((event: ProtocolEvent): { stale: boolean; pending: boolean } => {
    const pending = pendingWorkspaceSaveRef.current;
    if (pending && event.rid && event.rid !== pending.rid) return { stale: true, pending: true };
    pendingWorkspaceSaveRef.current = null;
    setSavingFile(false);
    if (event.ok && pending && event.path === pending.path && event.sha256) {
      const savedSize = new TextEncoder().encode(pending.content).byteLength;
      setFilePreview((previous) => previous?.path === pending.path
        ? { ...previous, content: pending.content, size: savedSize, sha256: String(event.sha256) }
        : previous);
      setEditorContent(pending.buffer);
    }
    return { stale: false, pending: Boolean(pending) };
  }, []);

  /** 断开或换会话：不再等这次保存的结果。 */
  const abandonWorkspaceSave = useCallback(() => {
    setSavingFile(false);
    pendingWorkspaceSaveRef.current = null;
  }, []);

  /** 切项目：清掉列表、预览和编辑器（不碰源码引用，那归会话视图清）。 */
  const resetWorkspaceFiles = useCallback(() => {
    workspaceRootRef.current = "";
    setWorkspaceFiles([]);
    setWorkspaceRoot("");
    setWorkspaceTruncated(false);
    setWorkspaceError("");
    setFileQuery("");
    setSelectedFile("");
    setFilePreview(null);
    setSourceSelection(null);
    setEditorMode(false);
    setEditorContent("");
    setEditorEol("\n");
  }, []);

  const addFileToContext = (item: ContextItem) => {
    if (connection !== "connected" || !workspaceMatchesRuntime) {
      onBanner("源码工作区必须与当前 runtime 项目一致后才能加入 Agent 上下文");
      return;
    }
    setContextItems((previous) => {
      const { items, full } = addContextItem(previous, item);
      if (full) onBanner("单轮最多选择 8 个源码上下文引用");
      return items;
    });
  };

  const selectSourceLine = (line: number, extend: boolean) => {
    setSourceSelection((previous) => nextSourceSelection(previous, line, extend));
  };

  const launchExternalEditor = async () => {
    if (!filePreview) return;
    const root = workspaceRoot || repoRoot.trim() || runtimeWorkdir || "";
    if (!root) return;
    try {
      const result = await invoke<OpenWorkspaceFileResult>("open_workspace_file", {
        repoRoot: root,
        relativePath: filePreview.path,
        line: sourceSelection?.start ?? 1,
      });
      onBanner(result.message);
    } catch (error) {
      onBanner(error instanceof Error ? error.message : String(error));
    }
  };

  const saveWorkspaceFile = async () => {
    if (!filePreview || !editorDirty || savingFile || !clientRef.current) return;
    if (connection !== "connected" || !workspaceMatchesRuntime) {
      onBanner("只有连接到当前源码工作区的 runtime 才能保存");
      return;
    }
    if (protocolVersion !== DESKTOP_PROTOCOL_VERSION) {
      onBanner(`runtime 需要升级到协议 v${DESKTOP_PROTOCOL_VERSION} 才能安全保存源码`);
      return;
    }
    const contentToSave = serializeEditorText(editorContent, editorEol);
    if (new TextEncoder().encode(contentToSave).byteLength > 1024 * 1024) {
      onBanner("编辑内容超过保存上限（1 MiB）");
      return;
    }
    const rid = createSessionId().slice(0, 64);
    pendingWorkspaceSaveRef.current = {
      rid, path: filePreview.path, content: contentToSave, buffer: editorContent,
    };
    setSavingFile(true);
    try {
      await clientRef.current.send({
        type: "workspace_edit",
        path: filePreview.path,
        content: contentToSave,
        expected_sha256: filePreview.sha256,
        rid,
      });
    } catch (error) {
      pendingWorkspaceSaveRef.current = null;
      setSavingFile(false);
      onBanner(errorText(error, "源码保存请求发送失败"));
    }
  };

  const discardEditorChanges = async () => {
    if (!filePreview) return;
    if (editorDirty && !await confirmAction("放弃当前文件的未保存修改？")) return;
    setEditorContent(normalizeEditorText(filePreview.content));
    setEditorMode(false);
  };

  const closeWorkspaceFile = async () => {
    if (savingFile) {
      onBanner("请先完成或拒绝当前源码保存确认");
      return;
    }
    if (editorDirty && !await confirmAction("当前文件有未保存修改，确定关闭吗？")) return;
    setSelectedFile("");
    setFilePreview(null);
    setEditorContent("");
    setEditorMode(false);
    setSourceSelection(null);
    setWorkspaceError("");
  };

  return {
    workspaceRootRef, workspaceFiles, workspaceTruncated, workspaceLoading, workspaceError,
    fileQuery, setFileQuery, selectedFile, filePreview, contextItems, setContextItems,
    sourceSelection, setSourceSelection, editorMode, setEditorMode, editorContent, setEditorContent, editorEol, savingFile,
    filteredWorkspaceFiles, editorDirty, workspaceMatchesRuntime, previewContextItem, previewContextAttached,
    refreshWorkspaceFiles, openWorkspaceFile, settleWorkspaceSave, abandonWorkspaceSave, resetWorkspaceFiles,
    addFileToContext, selectSourceLine, launchExternalEditor, saveWorkspaceFile, discardEditorChanges, closeWorkspaceFile,
  };
}
