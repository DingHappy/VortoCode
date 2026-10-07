// 右侧「预览」：任务清单 / 产物 / 参考三视图，跟随当前会话实时更新。
// 纯展示件：制品状态与预览加载仍在 useProjectAssets + App；这里只读 props、回调上抛。
// 制品沙箱沿用 ProjectAssetsPanel 的口径：iframe sandbox="" + 禁脚本禁外联的 CSP。
import { CheckCircle2, Circle, CircleDot, ExternalLink, Globe, Search } from "lucide-react";

import type { PreviewReference, PreviewView } from "../lib/preview";
import type { ArtifactMeta, ArtifactVersionSnapshot, PlanItem, TurnActivity } from "../types";

type PreviewPanelProps = {
  view: PreviewView;
  onSelectView: (view: PreviewView) => void;
  plan: PlanItem[];
  steps: TurnActivity[];
  busy: boolean;
  artifacts: ArtifactMeta[];
  selectedArtifact: ArtifactMeta | null;
  artifactVersion: number | null;
  artifactVersions: ArtifactVersionSnapshot | null;
  artifactPreviewLoading: boolean;
  securedArtifactHtml: string;
  onSelectArtifact: (id: string) => void;
  onSelectVersion: (id: string, version: number) => void;
  onOpenArtifact: () => void | Promise<void>;
  references: PreviewReference[];
  onOpenReference: (url: string) => void;
};

function StepIcon({ status }: { status: string }) {
  if (status === "completed" || status === "succeeded") return <CheckCircle2 size={14} />;
  if (status === "in_progress" || status === "running") return <CircleDot size={14} />;
  return <Circle size={14} />;
}

export function PreviewPanel(props: PreviewPanelProps) {
  const { view, onSelectView, plan, steps, busy, artifacts, selectedArtifact, references } = props;
  const done = plan.filter((item) => item.status === "completed").length;
  return (
    <div className="preview-panel">
      <div className="preview-switch" role="tablist" aria-label="预览内容">
        <button role="tab" aria-selected={view === "plan"} className={view === "plan" ? "active" : ""} onClick={() => onSelectView("plan")}>任务清单</button>
        <button role="tab" aria-selected={view === "artifacts"} className={view === "artifacts" ? "active" : ""} onClick={() => onSelectView("artifacts")}>产物<small>{artifacts.length}</small></button>
        <button role="tab" aria-selected={view === "references"} className={view === "references" ? "active" : ""} onClick={() => onSelectView("references")}>参考<small>{references.length}</small></button>
      </div>

      {view === "plan" && (
        plan.length > 0 ? (
          <section className="preview-section">
            <header><strong>执行计划</strong><span>{done}/{plan.length}</span></header>
            <div className="preview-progress"><i style={{ width: `${Math.round((done / plan.length) * 100)}%` }} /></div>
            <ol className="preview-steps">
              {plan.map((item, index) => (
                <li className={item.status} key={`${index}-${item.step}`}><StepIcon status={item.status} /><span>{item.step}</span></li>
              ))}
            </ol>
          </section>
        ) : steps.length > 0 ? (
          <section className="preview-section">
            <header><strong>{busy ? "正在执行" : "最近一轮"}</strong><span>{steps.length} 步</span></header>
            <ol className="preview-steps">
              {steps.map((item) => (
                <li className={item.status} key={item.id}><StepIcon status={item.status} /><span title={item.label}>{item.label}</span></li>
              ))}
            </ol>
          </section>
        ) : (
          <div className="panel-empty"><strong>还没有任务清单</strong><p>Agent 开始执行后，计划与每一步进展会实时出现在这里。</p></div>
        )
      )}

      {view === "artifacts" && (
        artifacts.length === 0 ? (
          <div className="panel-empty"><strong>暂无产物</strong><p>让 Agent“把结果做成页面”，发布后会自动在这里预览。</p></div>
        ) : (
          <section className="preview-section preview-artifact">
            <header>
              <select aria-label="选择产物" value={selectedArtifact?.id ?? ""} onChange={(event) => props.onSelectArtifact(event.target.value)}>
                {artifacts.map((artifact) => <option value={artifact.id} key={artifact.id}>{artifact.title}</option>)}
              </select>
              {selectedArtifact && (
                <select
                  aria-label="产物版本"
                  value={props.artifactVersion ?? ""}
                  onChange={(event) => props.onSelectVersion(selectedArtifact.id, Number(event.target.value))}
                >
                  {(props.artifactVersions?.versions ?? []).slice().reverse().map((version) => (
                    <option value={version.v} key={version.v}>
                      v{version.v}{version.v === props.artifactVersions?.current ? " · 最新" : ""}
                    </option>
                  ))}
                </select>
              )}
              <button aria-label="外部打开产物" title="外部打开" onClick={() => void props.onOpenArtifact()}><ExternalLink size={14} /></button>
            </header>
            <div className="preview-frame">
              {props.artifactPreviewLoading && <span>正在载入预览…</span>}
              {!props.artifactPreviewLoading && props.securedArtifactHtml && selectedArtifact && (
                <iframe sandbox="" srcDoc={props.securedArtifactHtml} title={`${selectedArtifact.title} 预览`} />
              )}
            </div>
          </section>
        )
      )}

      {view === "references" && (
        references.length === 0 ? (
          <div className="panel-empty"><strong>暂无参考</strong><p>Agent 联网搜索或读取网页时，来源会列在这里。</p></div>
        ) : (
          <ul className="preview-references">
            {references.map((item) => (
              <li key={item.id} className={item.failed ? "failed" : ""}>
                {item.kind === "search" ? <Search size={14} /> : <Globe size={14} />}
                {item.url ? (
                  <button title={item.url} onClick={() => props.onOpenReference(item.url!)}>{item.label}</button>
                ) : (
                  <span title={item.label}>搜索：{item.label}</span>
                )}
                {item.failed && <small>未读取成功</small>}
              </li>
            ))}
          </ul>
        )
      )}
    </div>
  );
}
