// App.tsx 抽出的「运行」面板（B8-④b S2，只搬家不改行为）。
// 全场状态最封闭的 inspector 面板：终端 / 预览 / 任务运行三个 surface。
// M 域 8 个 state（surface、命令输入、预览选择、证据草稿等）归本组件所有；
// 对外只依赖 runs/goals/connection 只读快照 + client，以及三个注入回调
// （startWorkspaceRun/cancelWorkspaceRun/adoptRunEvidence）。runs 列表由 App 的
// 事件总线喂养，这里只读；提交/停止/采纳证据都回调给 App，副作用不下放。
import { openUrl } from "@tauri-apps/plugin-opener";
import { lazy, Suspense, useMemo, useState } from "react";

import { runKindLabel, statusLabel } from "../lib/labels";
import type { CommandRunItem, CommandRunKind, ConnectionState, GoalItem } from "../types";
import type { GatewayClient } from "../gateway";
import { TestResultTree } from "./TestResultTree";

const TerminalPane = lazy(() => import("../TerminalPane").then((module) => ({ default: module.TerminalPane })));

type RunsPanelProps = {
  runs: CommandRunItem[];
  goals: GoalItem[];
  connection: ConnectionState;
  client: GatewayClient | null;
  onNotice: (message: string) => void;
  startWorkspaceRun: (command: string, kind: CommandRunKind, previewUrl: string) => Promise<boolean>;
  cancelWorkspaceRun: (run: CommandRunItem) => void | Promise<void>;
  adoptRunEvidence: (run: CommandRunItem, target: string) => void | Promise<void>;
};

export function RunsPanel({
  runs,
  goals,
  connection,
  client,
  onNotice,
  startWorkspaceRun,
  cancelWorkspaceRun,
  adoptRunEvidence,
}: RunsPanelProps) {
  const [runCommand, setRunCommand] = useState("");
  const [runKind, setRunKind] = useState<CommandRunKind>("terminal");
  const [runPreviewUrl, setRunPreviewUrl] = useState("");
  const [runSubmitting, setRunSubmitting] = useState(false);
  const [runSurface, setRunSurface] = useState<"terminal" | "preview" | "tasks">("terminal");
  const [selectedPreviewId, setSelectedPreviewId] = useState("");
  const [previewReload, setPreviewReload] = useState(0);
  const [runEvidenceTargets, setRunEvidenceTargets] = useState<Record<string, string>>({});

  const previewRuns = useMemo(
    () => runs.filter((run) => run.kind === "preview" && Boolean(run.preview_url)),
    [runs],
  );
  const selectedPreviewRun = previewRuns.find((run) => run.id === selectedPreviewId) ?? previewRuns[0] ?? null;

  const submitRun = async (command = runCommand, kind = runKind, previewUrl = runPreviewUrl) => {
    if (!command.trim()) return;
    setRunSubmitting(true);
    try {
      if (await startWorkspaceRun(command, kind, previewUrl)) {
        setRunCommand("");
        if (kind !== "preview") setRunPreviewUrl("");
      }
    } finally {
      setRunSubmitting(false);
    }
  };

  return (
    <div className="runs-panel">
      <div className="run-surface-switch">
        <button className={runSurface === "terminal" ? "active" : ""} onClick={() => setRunSurface("terminal")}>终端</button>
        <button className={runSurface === "preview" ? "active" : ""} onClick={() => setRunSurface("preview")}>预览<small>{previewRuns.length}</small></button>
        <button className={runSurface === "tasks" ? "active" : ""} onClick={() => setRunSurface("tasks")}>任务运行<small>{runs.length}</small></button>
      </div>
      {runSurface === "terminal" ? (
        <Suspense fallback={<div className="panel-empty compact"><strong>正在加载终端…</strong><p>首次打开会加载本地终端渲染器。</p></div>}>
        <TerminalPane
          client={client}
          connected={connection === "connected"}
          onNotice={(message) => onNotice(message)}
        />
        </Suspense>
      ) : runSurface === "preview" ? (
        <section className="preview-workbench">
          {selectedPreviewRun ? (
            <>
              <div className="preview-toolbar">
                <select
                  value={selectedPreviewRun.id}
                  onChange={(event) => setSelectedPreviewId(event.target.value)}
                  aria-label="选择预览进程"
                >
                  {previewRuns.map((run) => (
                    <option value={run.id} key={run.id}>{run.command}</option>
                  ))}
                </select>
                <div className="preview-address"><i className={selectedPreviewRun.status} />{selectedPreviewRun.preview_url}</div>
                <button title="刷新预览" onClick={() => setPreviewReload((value) => value + 1)}>↻</button>
                <button title="在默认浏览器打开" onClick={() => void openUrl(selectedPreviewRun.preview_url!)}>↗</button>
                {["queued", "running", "cancelling"].includes(selectedPreviewRun.status) && (
                  <button className="danger" onClick={() => void cancelWorkspaceRun(selectedPreviewRun)}>停止</button>
                )}
              </div>
              <iframe
                key={`${selectedPreviewRun.id}-${previewReload}`}
                src={selectedPreviewRun.preview_url}
                title={`预览 ${selectedPreviewRun.command}`}
                sandbox="allow-forms allow-modals allow-scripts allow-same-origin"
              />
              <details className="preview-logs">
                <summary>进程日志 · {statusLabel(selectedPreviewRun.status)}{selectedPreviewRun.code != null ? ` · 退出码 ${selectedPreviewRun.code}` : ""}</summary>
                <pre>{selectedPreviewRun.output || "等待预览进程输出…"}</pre>
              </details>
            </>
          ) : (
            <div className="panel-empty">
              <span className="panel-empty-icon">▶</span>
              <strong>还没有本地预览</strong>
              <p>在任务运行中启动 dev server；识别到 localhost 地址后会自动出现在这里。</p>
              <button onClick={() => { setRunSurface("tasks"); setRunKind("preview"); }}>配置预览命令</button>
            </div>
          )}
        </section>
      ) : (
      <>
      <div className="run-form">
        <div className="run-form-heading">
          <strong>项目运行控制台</strong>
          <span>明确点击后，经共享沙箱策略运行</span>
        </div>
        <div className="run-kind-switch">
          {(["terminal", "test", "preview"] as CommandRunKind[]).map((kind) => (
            <button
              className={runKind === kind ? "active" : ""}
              key={kind}
              onClick={() => setRunKind(kind)}
            >{runKindLabel(kind)}</button>
          ))}
        </div>
        <textarea
          value={runCommand}
          onChange={(event) => setRunCommand(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void submitRun();
            }
          }}
          placeholder={runKind === "test" ? "pytest -q" : runKind === "preview" ? "npm run dev" : "输入项目命令…"}
          spellCheck={false}
        />
        {runKind === "preview" && (
          <input
            value={runPreviewUrl}
            onChange={(event) => setRunPreviewUrl(event.target.value)}
            placeholder="可选：http://localhost:5173（也会从输出自动识别）"
          />
        )}
        <div className="run-presets">
          <button onClick={() => { setRunKind("test"); setRunCommand("pytest -q"); }}>Pytest</button>
          <button onClick={() => { setRunKind("test"); setRunCommand("npm --prefix desktop run build"); }}>Desktop Build</button>
          <button onClick={() => { setRunKind("preview"); setRunCommand("npm --prefix desktop run dev -- --host 127.0.0.1"); setRunPreviewUrl("http://127.0.0.1:1420"); }}>Desktop Preview</button>
        </div>
        <div className="run-submit-row">
          <span>⌘/Ctrl+Enter 运行 · 输出和退出码会持久化</span>
          <button
            className="primary"
            disabled={!runCommand.trim() || runSubmitting || connection !== "connected"}
            onClick={() => void submitRun()}
          >{runSubmitting ? "启动中…" : "运行"}</button>
        </div>
      </div>

      {runs.length === 0 && <div className="panel-empty compact"><strong>还没有运行记录</strong><p>运行测试、构建或本地预览；结果不会混进聊天文本。</p></div>}
      {runs.map((run) => {
        const active = ["queued", "running", "cancelling"].includes(run.status);
        const evidenceOptions = goals.flatMap((goal) => (
          goal.status === "draft" || goal.status === "achieved"
            ? []
            : goal.acceptance_criteria.map((criterion) => ({
                value: `${goal.id}|${criterion.id}`,
                label: `${goal.objective} · ${criterion.text}`,
              }))
        ));
        return (
          <section className={`run-card ${run.status}`} key={run.id}>
            <div className="run-card-head">
              <div>
                <span className={`task-status ${run.status}`}>{statusLabel(run.status)}</span>
                <b>{runKindLabel(run.kind)}</b>
              </div>
              <small>{run.updated ? new Date(run.updated).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : run.id.slice(-6)}</small>
            </div>
            <code className="run-command">$ {run.command}</code>
            <div className="run-meta">
              {run.pid && <span>PID {run.pid}</span>}
              {run.code != null && <span>退出码 {run.code}</span>}
              <span>{run.sandbox?.isolated ? `沙箱 · ${String(run.sandbox.backend || "on")}` : "宿主机交互运行"}</span>
              {run.goal_id && <span>Goal 自动验收 · {run.evidence_kind || "test"}</span>}
            </div>
            {run.warning && <div className="run-warning">{run.warning}</div>}
            {run.error && <div className="task-error">{run.error}</div>}
            {run.kind === "test" && <TestResultTree run={run} />}
            {run.output && (
              <pre className="run-output">{run.output}{run.dropped ? `\n[较早的 ${run.dropped} 行已被缓冲区丢弃]` : ""}</pre>
            )}
            {run.kind === "preview" && run.preview_url && (
              <div className="run-preview">
                <div>
                  <span>{run.preview_url}</span>
                  <button onClick={() => void openUrl(run.preview_url!)}>浏览器打开 ↗</button>
                </div>
                <iframe src={run.preview_url} title={`预览 ${run.command}`} sandbox="allow-forms allow-modals allow-scripts allow-same-origin" />
              </div>
            )}
            {run.kind === "test" && !run.goal_id && ["done", "failed"].includes(run.status) && run.code != null && evidenceOptions.length > 0 && (
              <div className="run-evidence">
                <select
                  value={runEvidenceTargets[run.id] ?? ""}
                  onChange={(event) => setRunEvidenceTargets((previous) => ({ ...previous, [run.id]: event.target.value }))}
                >
                  <option value="">选择 Goal 验收标准…</option>
                  {evidenceOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
                </select>
                <button onClick={() => void adoptRunEvidence(run, runEvidenceTargets[run.id] ?? "")}>{run.code === 0 ? "采纳通过证据" : "记录失败证据"}</button>
              </div>
            )}
            <div className="run-actions">
              {active
                ? <button className="danger" onClick={() => void cancelWorkspaceRun(run)}>停止</button>
                : <button onClick={() => void submitRun(run.command, run.kind, run.preview_url ?? "")}>重跑</button>}
            </div>
          </section>
        );
      })}
      </>
      )}
    </div>
  );
}
