// 侧边栏「插件」：集中查看当前项目加载的技能、MCP 服务、Hook 和规则，以及浏览器操控这类内置能力的开关入口。
// General 可显式连接已配置 MCP；项目 Skill、Hook 和规则在选择工作区后加载。
import { Globe, Puzzle } from "lucide-react";
import type { ReactNode } from "react";

import type { WorkspaceScope } from "../types";

type Props = {
  activeScope: WorkspaceScope;
  projectName: string;
  inspector: ReactNode;
  mcpConnection?: ReactNode;
  mcpServers?: ReactNode;
  personalSkills?: ReactNode;
  onChooseProject: () => void;
  onOpenBrowserSettings: () => void;
};

export function PluginsPage({ activeScope, projectName, inspector, mcpConnection, mcpServers, personalSkills, onChooseProject, onOpenBrowserSettings }: Props) {
  return (
    <section className="main-page">
      <header className="main-page-head">
        <div>
          <h1>插件</h1>
          <p>连接 MCP 工具服务，查看项目 Skill、Hook 和规则。</p>
        </div>
      </header>

      <div className="plugin-builtins">
        <button onClick={onOpenBrowserSettings}>
          <Globe size={18} />
          <span><strong>浏览器操控</strong><small>打开网页、读取内容并操作页面</small></span>
        </button>
      </div>

      {mcpServers}
      {mcpConnection}
      {activeScope === "general" && personalSkills}
      {activeScope === "project" ? (
        <div className="plugin-project">
          <h2>{projectName || "当前项目"} 的扩展</h2>
          {inspector}
        </div>
      ) : (
        <div className="main-page-empty">
          <Puzzle size={22} />
          <strong>{activeScope === "general" ? "私人助理扩展" : "项目扩展"}</strong>
          <p>{activeScope === "general"
            ? "私人助理可管理上方个人技能并连接已配置 MCP 服务。项目 Skill、Hook 和规则需要选择项目后加载。"
            : "选择项目后，这里会显示它的 Skill、Hook 和规则。"}</p>
          <button onClick={onChooseProject}>选择项目</button>
        </div>
      )}
    </section>
  );
}
