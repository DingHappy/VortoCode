// App.tsx 抽出的「上下文」inspector 面板（B8-④b S5，只搬家不改行为）。
// 仓库记忆（repo memory）与制品（artifacts）两面：既是 general 会话的资产，也是项目工作区的长期资产。
//
// 与 FilesPanel 同构，做成**纯展示件**：L 域状态全部留在 App。原因同 S4——数据由 App 编排：
// refreshProjectAssets/loadArtifactPreview 被 connect() 与「打开上下文 tab」预取，artifacts+repoMemory
// 还喂 sidebar「上下文」角标（跨域读），selectedArtifactId 又被一条 App effect 监听拉预览；搬进面板会
// 断掉预取、角标与 effect。故状态经 props 下传，副作用（写 client / 审计 / banner）留 App。
//
// 一条跨域外泄边按方案显式化：attachSelectedArtifact 会把 `@artifact:<id>` 追加进 composer 的输入框，
// 改走注入回调 onAttachArtifact（setPrompt 逻辑仍在 App），面板不感知 composer。
import { formatBytes } from "../lib/labels";
import type {
  ArtifactMeta,
  ArtifactVersionSnapshot,
  ConnectionState,
  RepoMemorySnapshot,
  WorkspaceScope,
} from "../types";

type ProjectAssetView = "memory" | "artifacts";

type ProjectAssetsPanelProps = {
  activeScope: WorkspaceScope;
  projectAssetView: ProjectAssetView;
  projectAssetsLoading: boolean;
  projectAssetsError: string;
  connection: ConnectionState;
  repoMemory: RepoMemorySnapshot | null;
  repoMemoryDraft: string;
  artifacts: ArtifactMeta[];
  selectedArtifactId: string;
  selectedArtifact: ArtifactMeta | null;
  artifactVersion: number | null;
  artifactVersions: ArtifactVersionSnapshot | null;
  artifactPreviewLoading: boolean;
  securedArtifactHtml: string;
  onRefresh: () => void;
  onSelectView: (view: ProjectAssetView) => void;
  onRepoMemoryDraftChange: (value: string) => void;
  onAddRepoMemoryFact: () => void;
  onSelectArtifact: (id: string) => void;
  onSelectVersion: (id: string, version: number) => void;
  onAttachArtifact: () => void; // 跨域边：把制品引用追加进 composer 输入框
  onOpenArtifact: () => void;
};

export function ProjectAssetsPanel({
  activeScope,
  projectAssetView,
  projectAssetsLoading,
  projectAssetsError,
  connection,
  repoMemory,
  repoMemoryDraft,
  artifacts,
  selectedArtifactId,
  selectedArtifact,
  artifactVersion,
  artifactVersions,
  artifactPreviewLoading,
  securedArtifactHtml,
  onRefresh,
  onSelectView,
  onRepoMemoryDraftChange,
  onAddRepoMemoryFact,
  onSelectArtifact,
  onSelectVersion,
  onAttachArtifact,
  onOpenArtifact,
}: ProjectAssetsPanelProps) {
  return (
    <div className="project-assets-panel">
      <div className="project-assets-head">
        <div>
          <span>{activeScope === "general" ? "General Assets" : "Project Assets"}</span>
          <strong>{activeScope === "general" ? "不依赖目录的会话制品" : "跟着当前工作区的长期资产"}</strong>
        </div>
        <button
          onClick={onRefresh}
          disabled={projectAssetsLoading || connection !== "connected"}
        >{projectAssetsLoading ? "同步中" : "刷新"}</button>
      </div>
      <div className="project-assets-toggle">
        {activeScope !== "general" && <button className={projectAssetView === "memory" ? "active" : ""} onClick={() => onSelectView("memory")}>仓库记忆 · {repoMemory?.total_entries ?? 0}</button>}
        <button className={projectAssetView === "artifacts" ? "active" : ""} onClick={() => onSelectView("artifacts")}>制品 · {artifacts.length}</button>
      </div>

      {projectAssetsError && <div className="project-assets-error">{projectAssetsError}</div>}

      {activeScope !== "general" && projectAssetView === "memory" && (
        <div className="repo-memory-view">
          <section className="repo-memory-summary">
            <div>
              <span>自动注入</span>
              <strong>{repoMemory?.injected_entries ?? repoMemory?.total_entries ?? 0} 条</strong>
            </div>
            <div>
              <span>注入预算</span>
              <strong>{repoMemory?.max_chars ?? 2_000} 字</strong>
            </div>
            <code>{repoMemory?.path ?? ".vortocode/memory/repo.md"}</code>
          </section>
          {repoMemory?.redacted && <div className="asset-warning">检测到疑似凭据，Desktop 投影已脱敏。</div>}
          {repoMemory?.truncated && (
            <div className="asset-warning">
              记忆已超过注入上限；更早的 {Math.max(0, repoMemory.dropped_entries)} 条不会进入新会话。
            </div>
          )}
          <div className="repo-memory-list">
            {!repoMemory || (!repoMemory.entries.length && !repoMemory.content) ? (
              <div className="panel-empty compact"><strong>暂无仓库记忆</strong><p>适合保存真实测试命令、目录约定和已知坑。</p></div>
            ) : repoMemory.entries.length > 0 ? (
              repoMemory.entries.map((entry, index) => (
                <div className="repo-memory-entry" key={`${index}-${entry.slice(0, 40)}`}>
                  <span>{index + 1}</span><p>{entry}</p>
                </div>
              ))
            ) : (
              <pre>{repoMemory.content}</pre>
            )}
          </div>
          <section className="repo-memory-composer">
            <div><strong>追加可信仓库事实</strong><span>保存后从下个新会话开始生效</span></div>
            <textarea
              value={repoMemoryDraft}
              onChange={(event) => onRepoMemoryDraftChange(event.target.value)}
              maxLength={4_000}
              placeholder="例如：完整测试必须运行 npm run test:integration"
            />
            <div>
              <small>{repoMemoryDraft.length}/4000</small>
              <button
                className="primary"
                onClick={() => void onAddRepoMemoryFact()}
                disabled={!repoMemoryDraft.trim() || projectAssetsLoading || connection !== "connected"}
              >确认后追加</button>
            </div>
          </section>
        </div>
      )}

      {projectAssetView === "artifacts" && (
        <div className="artifacts-workbench">
          {artifacts.length === 0 ? (
            <div className="panel-empty"><strong>暂无制品</strong><p>在 Build 模式让 Agent“把结果做成可交互页面”，发布后会出现在这里。</p></div>
          ) : (
            <>
              <div className="artifact-list">
                {artifacts.map((artifact) => (
                  <button
                    className={artifact.id === selectedArtifactId ? "active" : ""}
                    key={artifact.id}
                    onClick={() => onSelectArtifact(artifact.id)}
                  >
                    <span>{artifact.kind === "markdown" ? "MD" : "HTML"}</span>
                    <div><strong>{artifact.title}</strong><small>v{artifact.version} · {formatBytes(artifact.bytes)} · {artifact.updated_at}</small></div>
                  </button>
                ))}
              </div>
              {selectedArtifact && (
                <section className="artifact-preview-card">
                  <div className="artifact-preview-head">
                    <div><strong>{selectedArtifact.title}</strong><span>{selectedArtifact.id}</span></div>
                    <select
                      aria-label="制品版本"
                      value={artifactVersion ?? ""}
                      onChange={(event) => void onSelectVersion(selectedArtifact.id, Number(event.target.value))}
                    >
                      {(artifactVersions?.versions ?? []).slice().reverse().map((version) => (
                        <option value={version.v} key={version.v}>
                          v{version.v}{version.v === artifactVersions?.pinned ? " · 默认" : ""}{version.v === artifactVersions?.current ? " · 最新" : ""}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="artifact-preview-actions">
                    <button className="primary" onClick={onAttachArtifact}>交给 Agent 迭代</button>
                    <button onClick={() => void onOpenArtifact()}>外部打开</button>
                  </div>
                  <div className="artifact-sandbox">
                    {artifactPreviewLoading && <span>正在载入隔离预览…</span>}
                    {!artifactPreviewLoading && securedArtifactHtml && (
                      <iframe
                        sandbox=""
                        srcDoc={securedArtifactHtml}
                        title={`${selectedArtifact.title} 制品预览`}
                      />
                    )}
                  </div>
                  <p className="artifact-security-note">Desktop 内置预览禁用脚本、交互与外联；完整交互请在无 token 的本机查看页打开。</p>
                </section>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
