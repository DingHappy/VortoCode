// 侧栏「项目」：当前 Scratch、后台跑着的其他 Scratch、最近项目（含常驻电脑上的同名项目），
// 以及每个项目下最近的会话。只负责显示；切换、移除都交回 App 的回调。
import { Folder, Laptop, Server, X } from "lucide-react";
import { Fragment } from "react";

import { listedProjects, placedSessions, residentTwin } from "../lib/residentPlace";
import type {
  ConnectionState, DesktopProjectProfile, GatewayProcessStatus, GatewayRecoveryRecord, ProjectSessionSummary,
  SessionSummary, WorkspaceScope,
} from "../types";

export function SidebarProjects({
  activeScope, currentRuntimeId, runtimeProcesses, runtimeRecoveries, projects, residentHost, query,
  projectSessions, sessions, residentSessions, remoteProjectId, repoRoot, connection, connectionText, activeSid,
  onSwitchScratch, onSwitchProject, onSwitchSession, onForgetProject, onBanner,
}: {
  activeScope: WorkspaceScope;
  /** 前台这个 runtime 的身份：它自己不再作为「后台 Scratch」列出 */
  currentRuntimeId?: string;
  runtimeProcesses: GatewayProcessStatus[];
  runtimeRecoveries: GatewayRecoveryRecord[];
  projects: DesktopProjectProfile[];
  /** 常驻电脑（登记了的话） */
  residentHost?: DesktopProjectProfile;
  /** 侧栏搜索框的内容；空串 = 不筛 */
  query: string;
  projectSessions: ProjectSessionSummary[];
  /** 前台连接的会话列表（连着常驻电脑上的项目时，就是那边的会话） */
  sessions: SessionSummary[];
  residentSessions: Record<string, SessionSummary[]>;
  /** 前台连着的远端项目 id；null = 本机 */
  remoteProjectId: string | null;
  repoRoot: string;
  connection: ConnectionState;
  connectionText: string;
  activeSid: string;
  onSwitchScratch: (runtime: GatewayProcessStatus) => void;
  onSwitchProject: (project: DesktopProjectProfile, options?: { sid?: string }) => void;
  onSwitchSession: (sid: string) => void;
  onForgetProject: (project: DesktopProjectProfile) => void;
  onBanner: (text: string) => void;
}) {
  const listed = listedProjects(projects, residentHost);
  return (
    <div className="project-list">
      {activeScope === "scratch" && (
        <div className="project-row active healthy">
          <span className="project-status" />
          <div className="project-row-copy"><strong>Scratch</strong><span>当前任务的隔离临时工作区</span></div>
        </div>
      )}
      {runtimeProcesses
        .filter((item) => item.running && item.scope === "scratch" && item.runtimeId !== currentRuntimeId)
        .map((item, index) => (
          <div
            className="project-row healthy"
            key={item.runtimeId || `scratch-${index}`}
            onClick={() => onSwitchScratch(item)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") onSwitchScratch(item);
            }}
            role="button"
            tabIndex={0}
            title={item.workspaceRoot || "隔离 Scratch 工作区"}
          >
            <span className="project-status" />
            <div className="project-row-copy">
              <strong>Scratch {item.workspaceId?.slice(0, 6) || index + 1}</strong>
              <span>后台运行中</span>
            </div>
          </div>
        ))}
      {listed.length === 0 && <div className="project-empty">添加项目后，可以在这里查看和切换。</div>}
      {listed.slice(0, 8).map((project) => {
        const keyword = query.trim().toLowerCase();
        const nameMatches = !keyword || project.name.toLowerCase().includes(keyword);
        const twinId = project.kind !== "remote" && residentHost ? residentTwin(project, projects, residentHost)?.id : undefined;
        const nested = placedSessions(
          projectSessions.filter((item) => item.projectId === project.id),
          twinId ? (remoteProjectId === twinId ? sessions : residentSessions[twinId] ?? []) : [],
          twinId,
        ).filter((item) => nameMatches || item.title.toLowerCase().includes(keyword));
        if (!nameMatches && nested.length === 0) return null;
        const onResidentTwin = project.kind !== "remote" && Boolean(residentHost && remoteProjectId)
          && residentTwin(project, projects, residentHost!)?.id === remoteProjectId;
        const active = project.kind === "remote" ? remoteProjectId === project.id : onResidentTwin || (!remoteProjectId && project.repoRoot === repoRoot.trim());
        const managedRuntime = project.kind === "remote" ? undefined : runtimeProcesses.find((item) => item.projectId === project.id || item.repoRoot === project.repoRoot);
        const managedRunning = Boolean(managedRuntime?.running);
        const recovery = runtimeRecoveries.find((item) => item.projectId === project.id);
        const connected = active && connection === "connected";
        const health = onResidentTwin ? connected ? "在常驻电脑上" : "常驻电脑 · 连接中" : project.kind === "remote" ? connected ? "远端已连接" : "远端 · 点击连接" : project.missing ? "目录已不存在 · 可移除" : managedRunning && !active ? "后台运行中" : managedRunning ? "本地引擎运行中" : connected ? "工作区就绪" : recovery?.status === "crashed" ? "上次异常退出 · 点击恢复" : recovery?.status === "running" ? "可重新附着" : active ? connectionText : "未启动";
        return (
          <Fragment key={project.id}>
          <div
            className={`project-row ${active ? "active" : ""} ${managedRunning || connected ? "healthy" : ""} ${!managedRunning && recovery?.status === "crashed" ? "attention" : ""} ${project.missing ? "missing" : ""}`}
            onClick={() => project.missing
              ? onBanner(`项目目录已不存在：${project.repoRoot}。可以点右侧 × 把它从列表移除`)
              : onSwitchProject(project)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") onSwitchProject(project);
            }}
            role="button"
            tabIndex={0}
            title={project.repoRoot}
          >
            <Folder size={15} className="project-folder" />
            <div className="project-row-copy">
              <strong>{project.kind === "remote" ? `远端 · ${project.name}` : project.name}</strong>
              {/* 只在需要注意时显示状态；平时一行只有名字。 */}
              {(project.missing || managedRunning || recovery?.status) && <span>{health}</span>}
            </div>
            {!active && (
              <button
                aria-label={`移除 ${project.name}`}
                title="从最近项目移除"
                onClick={(event) => { event.stopPropagation(); onForgetProject(project); }}
              ><X size={14} /></button>
            )}
          </div>
          {/* 项目下直接列出它最近的会话（MiMo 式）：点一下切到该项目并打开那条会话。 */}
          {!project.missing && nested.map((item) => {
            const here = item.place === "resident" ? remoteProjectId === item.projectId : !remoteProjectId && active;
            const target = item.place === "resident" ? projects.find((entry) => entry.id === item.projectId) ?? project : project;
            return <button
              key={`${item.place}:${item.sid}`}
              className={`project-session ${here && item.sid === activeSid ? "active" : ""}`}
              title={`${item.title}${twinId ? ` · ${item.place === "resident" ? "在常驻电脑上" : "在本机"}` : ""}`}
              onClick={() => (here ? onSwitchSession(item.sid) : onSwitchProject(target, { sid: item.sid }))}
            >{twinId && (item.place === "resident" ? <Server size={12} aria-label="常驻电脑" /> : <Laptop size={12} aria-label="本机" />)}{item.title}</button>;
          })}
          </Fragment>
        );
      })}
    </div>
  );
}
