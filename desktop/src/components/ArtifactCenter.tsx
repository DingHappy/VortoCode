// 侧边栏「产物中心」：Agent 发布的页面和文档。点一项在右侧「预览」里打开（沙箱、可切版本）。
import { FileText, Globe, RefreshCw } from "lucide-react";

import { formatBytes } from "../lib/labels";
import type { ArtifactMeta } from "../types";

type Props = {
  artifacts: ArtifactMeta[];
  loading: boolean;
  error: string;
  selectedId: string;
  onRefresh: () => void;
  onOpen: (id: string) => void;
};

export function ArtifactCenter({ artifacts, loading, error, selectedId, onRefresh, onOpen }: Props) {
  return (
    <section className="main-page">
      <header className="main-page-head">
        <div>
          <h1>产物中心</h1>
          <p>Agent 生成的网页和文档都在这里，点开即可预览，也可以切换历史版本。</p>
        </div>
        <button aria-label="刷新产物" title="刷新" onClick={onRefresh} disabled={loading}><RefreshCw size={15} /></button>
      </header>
      {error && <p className="main-page-error">{error}</p>}
      {artifacts.length === 0 ? (
        <div className="main-page-empty">
          <FileText size={22} />
          <strong>{loading ? "正在读取…" : "还没有产物"}</strong>
          <p>让 Agent「把结果做成网页 / 报告」，发布后会出现在这里。</p>
        </div>
      ) : (
        <ul className="artifact-grid">
          {artifacts.map((artifact) => (
            <li key={artifact.id}>
              <button className={artifact.id === selectedId ? "active" : ""} onClick={() => onOpen(artifact.id)}>
                <span className="artifact-grid-icon">{artifact.kind === "markdown" ? <FileText size={18} /> : <Globe size={18} />}</span>
                <strong title={artifact.title}>{artifact.title}</strong>
                <small>{artifact.kind === "markdown" ? "文档" : "网页"} · v{artifact.version} · {formatBytes(artifact.bytes)}</small>
                <small>{artifact.updated_at}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
