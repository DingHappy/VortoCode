// 侧边栏「插件」：集中查看当前项目加载的技能、MCP 服务、Hook 和规则，以及浏览器操控这类内置能力的开关入口。
// 扩展按项目加载（来自仓库里的 .vortocode/、AGENTS.md 等），通用会话没有项目，只给出说明和入口。
import { Globe, Puzzle } from "lucide-react";
import type { ReactNode } from "react";

import type { WorkspaceScope } from "../types";

type Props = {
  activeScope: WorkspaceScope;
  projectName: string;
  inspector: ReactNode;
  mcpConnection?: ReactNode;
  onChooseProject: () => void;
  onOpenBrowserSettings: () => void;
};

export function PluginsPage({ activeScope, projectName, inspector, mcpConnection, onChooseProject, onOpenBrowserSettings }: Props) {
  return (
    <section className="main-page">
      <header className="main-page-head">
        <div>
          <h1>插件</h1>
          <p>管理工具连接与项目扩展。</p>
        </div>
      </header>

      <div className="plugin-builtins">
        <button onClick={onOpenBrowserSettings}>
          <Globe size={18} />
          <span><strong>浏览器操控</strong><small>打开网页、读取内容并操作页面</small></span>
        </button>
      </div>

      {mcpConnection}
      {activeScope === "project" ? (
        <div className="plugin-project">
          <h2>{projectName || "当前项目"} 的扩展</h2>
          {inspector}
        </div>
      ) : (
        <div className="main-page-empty">
          <Puzzle size={22} />
          <strong>还未选择项目</strong>
          <p>打开项目后，这里会显示它的技能与规则。</p>
          <button onClick={onChooseProject}>选择项目</button>
        </div>
      )}
    </section>
  );
}
