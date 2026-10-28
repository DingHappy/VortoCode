// 侧栏「资料库」：助理发布的网页和文档。按天分组的列表；点一项在页内右侧预览（沙箱、可切版本），关掉不留占位。
// 预览不借右侧面板：助理模式下那里只放助理面板（docs/DESIGN_SYSTEM.md 第 2、3 节）。
import { ExternalLink, FileText, Globe, RefreshCw, X } from "lucide-react";
import { useState } from "react";

import { formatBytes } from "../lib/labels";
import { groupArtifacts, artifactTimeLabel } from "../lib/libraryGroups";
import type { ArtifactMeta, ArtifactVersionSnapshot } from "../types";
import { EmptyState, GroupTitle, IconButton } from "./ui";

type Props = {
  artifacts: ArtifactMeta[];
  loading: boolean;
  error: string;
  selectedId: string;
  version: number | null;
  versions: ArtifactVersionSnapshot | null;
  previewLoading: boolean;
  /** 已套上禁脚本、禁外联 CSP 的预览文档 */
  previewHtml: string;
  onRefresh: () => void;
  onOpen: (id: string) => void;
  onVersion: (id: string, version: number) => void;
  onOpenExternal: () => void;
};

const kindLabel = (artifact: ArtifactMeta) => artifact.kind === "markdown" ? "文档" : "网页";

export function ArtifactCenter({ artifacts, loading, error, selectedId, version, versions, previewLoading, previewHtml,
  onRefresh, onOpen, onVersion, onOpenExternal }: Props) {
  const [open, setOpen] = useState(false);
  const selected = open ? artifacts.find((artifact) => artifact.id === selectedId) ?? null : null;
  return (
    <section className={`vc-library${selected ? " has-detail" : ""}`}>
      <div className="vc-library-list">
        <header className="vc-page-head">
          <div>
            <h1>资料库</h1>
            <p>助理做出来的网页和文档。点一项在右侧预览，可以切换历史版本。</p>
          </div>
          <IconButton label="刷新资料库" icon={<RefreshCw />} onClick={onRefresh} disabled={loading} />
        </header>
        {error && <p className="vc-page-error" role="alert">{error}</p>}
        {artifacts.length === 0 ? (
          <EmptyState message={loading ? "正在读取…" : "还没有资料。让助理「把结果做成网页或报告」，发布后会出现在这里。"} />
        ) : groupArtifacts(artifacts).map((group) => (
          <section key={group.key} className="vc-library-group" aria-label={group.label}>
            <GroupTitle>{group.label}</GroupTitle>
            <ul className="vc-list">
              {group.items.map((artifact) => (
                <li key={artifact.id}>
                  <button type="button" className="vc-library-row" aria-pressed={artifact.id === selected?.id}
                    onClick={() => { setOpen(true); onOpen(artifact.id); }}>
                    <span className="vc-library-icon" aria-hidden="true">{artifact.kind === "markdown" ? <FileText /> : <Globe />}</span>
                    <span className="vc-library-body">
                      <span className="vc-library-title" title={artifact.title}>{artifact.title}</span>
                      <span className="vc-library-desc">{kindLabel(artifact)} · v{artifact.version} · {formatBytes(artifact.bytes)}</span>
                    </span>
                    <span className="vc-library-time">{artifactTimeLabel(artifact.updated_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      {selected && (
        <aside className="vc-library-detail" aria-label={`${selected.title} 预览`}>
          <header>
            <div>
              <strong title={selected.title}>{selected.title}</strong>
              <span>{kindLabel(selected)} · {formatBytes(selected.bytes)}</span>
            </div>
            {(versions?.versions.length ?? 0) > 1 && (
              <select aria-label="版本" value={version ?? ""} onChange={(event) => onVersion(selected.id, Number(event.target.value))}>
                {(versions?.versions ?? []).slice().reverse().map((item) => (
                  <option value={item.v} key={item.v}>v{item.v}{item.v === versions?.current ? " · 最新" : ""}</option>
                ))}
              </select>
            )}
            <IconButton label="在浏览器里打开" icon={<ExternalLink />} onClick={onOpenExternal} />
            <IconButton label="关闭预览" icon={<X />} onClick={() => setOpen(false)} />
          </header>
          <div className="vc-library-frame">
            {previewLoading ? <p>正在载入预览…</p>
              : previewHtml ? <iframe sandbox="" srcDoc={previewHtml} title={`${selected.title} 预览`} />
              : <p>预览没有载入，点右上角在浏览器里打开。</p>}
          </div>
        </aside>
      )}
    </section>
  );
}
