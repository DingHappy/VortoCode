// App.tsx 抽出的「代码」inspector 面板（B8-④b S3，只搬家不改行为）。
//
// 与 RunsPanel 不同，本面板刻意做成**纯展示件**：文件/编辑器状态（filePreview、
// editorContent、savingFile、contextItems、workspaceFiles…）全部留在 App。原因是这些
// 状态被中央事件总线 handleProtocolEvent 直接写——例如 workspace_edit_result 回来时
// 会就地改 filePreview.sha256、把 editorContent 重置为已保存缓冲、清 savingFile；
// refreshWorkspaceFiles 又被总线在 init/连接时调用喂 workspaceFiles。React 无法让子组件
// 拥有父级事件总线要写的状态，硬搬会逼总线下放（违反红线①），故状态一律经 props 下传。
//
// 两条跨域外泄边按方案显式化：
//   1. contextItems 是 composer 的状态，本面板只读它渲染「已选」，写入改走注入回调
//      onAddContext（原 addFileToContext），副作用（连接/工作区一致性校验、8 条上限）仍在 App。
//   2. editorDirty / savingFile 由 composer 发送闸与切换确认框在用，留 App 计算后 props 下传。
//
// 搬进来的只有：本面板专属的派生（可见文件切片、预览行、行数、裁剪标记）、Cmd+S 保存
// 快捷键 effect、以及 Monaco 懒加载。JSX 与类名逐字保持，行为在可达路径上不变。
import { ArrowLeft } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo } from "react";

import { fileGlyph, formatFileSize } from "../lib/labels";
import type {
  ConnectionState,
  ContextItem,
  SourceSelection,
  WorkspaceFileContent,
  WorkspaceScope,
} from "../types";

const MonacoEditor = lazy(() => import("../MonacoEditor").then((module) => ({ default: module.MonacoEditor })));

type FilesPanelProps = {
  // —— 只读状态快照（权威副本在 App）——
  selectedFile: string;
  filePreview: WorkspaceFileContent | null;
  editorMode: boolean;
  editorContent: string;
  editorEol: "\n" | "\r\n";
  editorDirty: boolean;
  savingFile: boolean;
  sourceSelection: SourceSelection | null;
  workspaceLoading: boolean;
  workspaceError: string;
  fileQuery: string;
  filteredWorkspaceFiles: string[];
  totalFileCount: number;
  workspaceTruncated: boolean;
  contextItems: ContextItem[];
  previewContextItem: ContextItem | null;
  previewContextAttached: boolean;
  connection: ConnectionState;
  busy: boolean;
  activeScope: WorkspaceScope;
  workspaceMatchesRuntime: boolean;
  canRefresh: boolean;
  // —— 注入回调（副作用仍在 App，含事件总线耦合与跨域写入）——
  onOpenFile: (path: string) => void;
  onCloseFile: () => void;
  onToggleEditorMode: () => void;
  onEditorContentChange: (value: string) => void;
  onDiscard: () => void;
  onSave: () => void;
  onLaunchExternal: () => void;
  onAddContext: (item: ContextItem) => void; // 跨域边①：写 composer 的 contextItems
  onSelectSourceLine: (line: number, extend: boolean) => void;
  onClearSourceSelection: () => void;
  onFileQueryChange: (value: string) => void;
  onRefresh: () => void;
};

export function FilesPanel({
  selectedFile,
  filePreview,
  editorMode,
  editorContent,
  editorEol,
  editorDirty,
  savingFile,
  sourceSelection,
  workspaceLoading,
  workspaceError,
  fileQuery,
  filteredWorkspaceFiles,
  totalFileCount,
  workspaceTruncated,
  contextItems,
  previewContextItem,
  previewContextAttached,
  connection,
  busy,
  activeScope,
  workspaceMatchesRuntime,
  canRefresh,
  onOpenFile,
  onCloseFile,
  onToggleEditorMode,
  onEditorContentChange,
  onDiscard,
  onSave,
  onLaunchExternal,
  onAddContext,
  onSelectSourceLine,
  onClearSourceSelection,
  onFileQueryChange,
  onRefresh,
}: FilesPanelProps) {
  const visibleWorkspaceFiles = filteredWorkspaceFiles.slice(0, 500);
  const previewLines = useMemo(() => filePreview?.content.split("\n").slice(0, 5_000) ?? [], [filePreview]);
  const previewWasClipped = Boolean(filePreview && filePreview.content.split("\n").length > previewLines.length);
  const editorLineCount = useMemo(() => editorContent.split("\n").length, [editorContent]);

  useEffect(() => {
    if (!editorMode) return;
    const saveFromKeyboard = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "s") return;
      event.preventDefault();
      void onSave();
    };
    window.addEventListener("keydown", saveFromKeyboard);
    return () => window.removeEventListener("keydown", saveFromKeyboard);
  }, [editorMode, onSave]);

  return (
    <div className="files-panel">
      {selectedFile ? (
        <>
          <div className="file-preview-head">
            <button className="file-back" aria-label="返回文件列表" onClick={onCloseFile}><ArrowLeft size={15} /></button>
            <div className="file-preview-title">
              <strong title={selectedFile}>{selectedFile.split("/").slice(-1)[0]}{editorDirty ? " •" : ""}</strong>
              <span title={selectedFile}>{selectedFile}</span>
            </div>
            {filePreview && previewContextItem && (
              <div className="file-preview-actions">
                <button
                  onClick={onToggleEditorMode}
                  disabled={savingFile || (!editorMode && editorLineCount > 50_000)}
                  title={editorLineCount > 50_000 ? "超过 50,000 行的文件请使用外部编辑器" : "切换内嵌编辑"}
                >{editorMode ? "只读预览" : "编辑"}</button>
                {editorMode ? (
                  <>
                    <button onClick={onDiscard} disabled={savingFile}>放弃</button>
                    <button className="primary" onClick={() => void onSave()} disabled={!editorDirty || savingFile || busy || connection !== "connected"}>
                      {savingFile ? "等待确认…" : "保存"}
                    </button>
                  </>
                ) : (
                  <>
                    <button onClick={() => void onLaunchExternal()} title={`在外部编辑器打开第 ${sourceSelection?.start ?? 1} 行`}>
                      ↗ 外部
                    </button>
                    <button
                      className={`primary ${previewContextAttached ? "attached" : ""}`}
                      onClick={() => onAddContext(previewContextItem)}
                      disabled={connection !== "connected" || !workspaceMatchesRuntime}
                      title={workspaceMatchesRuntime ? "经共享 read_file 权限门加入下一轮" : "请先连接这个项目的 runtime"}
                    >
                      {previewContextAttached ? "已加入" : sourceSelection ? `加入 L${sourceSelection.start}–${sourceSelection.end}` : "加入文件"}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
          {workspaceLoading && <div className="file-loading">正在读取源码…</div>}
          {workspaceError && <div className="file-error"><strong>无法预览</strong><p>{workspaceError}</p></div>}
          {filePreview && !workspaceLoading && (
            <>
              <div className="file-preview-meta">
                <span>{formatFileSize(filePreview.size)}</span>
                <span>{filePreview.content.split("\n").length.toLocaleString("zh-CN")} 行</span>
                <span>UTF-8 · {editorEol === "\r\n" ? "CRLF" : "LF"} · {editorMode ? editorDirty ? "未保存" : "编辑缓冲区" : "只读"}</span>
                <span>{editorMode ? "Monaco · ⌘/Ctrl+S 保存 · 未保存内容不会注入 Agent" : "点行号选择，Shift 扩展（最多 500 行）"}</span>
                {!editorMode && sourceSelection && (
                  <button onClick={onClearSourceSelection}>L{sourceSelection.start}–L{sourceSelection.end} ×</button>
                )}
              </div>
              {editorMode ? (
                <div className={`monaco-editor-shell ${editorDirty ? "dirty" : ""}`}>
                  <Suspense fallback={<div className="file-loading">正在加载代码编辑器…</div>}>
                  <MonacoEditor
                    path={filePreview.path}
                    value={editorContent}
                    onChange={onEditorContentChange}
                  />
                  </Suspense>
                </div>
              ) : (
                <>
                  <pre className="source-preview">
                    {previewLines.map((line, index) => {
                      const lineNumber = index + 1;
                      const selected = Boolean(sourceSelection && lineNumber >= sourceSelection.start && lineNumber <= sourceSelection.end);
                      return (
                        <span className={`source-line ${selected ? "selected" : ""}`} key={`${index}-${line.slice(0, 12)}`}>
                          <button
                            aria-label={`选择第 ${lineNumber} 行`}
                            onClick={(event) => onSelectSourceLine(lineNumber, event.shiftKey)}
                          >{lineNumber}</button>
                          <code>{line || " "}</code>
                        </span>
                      );
                    })}
                  </pre>
                  {previewWasClipped && <div className="file-clipped">仅展示前 5,000 行；Agent 上下文仍受单轮大小限制。</div>}
                </>
              )}
            </>
          )}
        </>
      ) : (
        <>
          <div className="file-toolbar">
            <input value={fileQuery} onChange={(event) => onFileQueryChange(event.target.value)} placeholder="搜索文件路径…" />
            <button
              title="刷新文件"
              onClick={onRefresh}
              disabled={!canRefresh}
            >↻</button>
          </div>
          <div className="file-list-meta">
            <span>{filteredWorkspaceFiles.length.toLocaleString("zh-CN")} 个文件</span>
            <span>{activeScope === "scratch" ? "Scratch 文件" : "Git 项目"}{workspaceTruncated ? " · 已截断" : ""}</span>
          </div>
          {workspaceLoading && <div className="file-loading">正在扫描项目…</div>}
          {workspaceError && <div className="file-error"><strong>无法读取源码工作区</strong><p>{workspaceError}</p></div>}
          {!workspaceLoading && !workspaceError && totalFileCount === 0 && (
            <div className="panel-empty compact"><strong>尚未加载项目文件</strong><p>在工作区设置中选择一个 Git 项目。</p></div>
          )}
          <div className="file-list">
            {visibleWorkspaceFiles.map((path) => (
              <button key={path} onClick={() => void onOpenFile(path)} title={path}>
                <span>{fileGlyph(path)}</span>
                <span>{path}</span>
                {contextItems.some((item) => item.path === path) && <b>已选</b>}
              </button>
            ))}
          </div>
          {filteredWorkspaceFiles.length > visibleWorkspaceFiles.length && (
            <div className="file-list-limit">继续输入路径以缩小结果；当前最多渲染 500 项。</div>
          )}
        </>
      )}
    </div>
  );
}
