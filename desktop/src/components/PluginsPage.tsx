// 侧边栏「插件」：集中查看当前项目加载的技能、MCP 服务、Hook 和规则，以及浏览器操控这类内置能力的开关入口。
// 扩展按项目加载（来自仓库里的 .vortocode/、AGENTS.md 等），通用会话没有项目，只给出说明和入口。
import { Globe, Puzzle } from "lucide-react";
import type { ReactNode } from "react";

import type { WorkspaceScope } from "../types";

type Props = {
  activeScope: WorkspaceScope;
  projectName: string;
  inspector: ReactNode;
  onChooseProject: () => void;
  onOpenBrowserSettings: () => void;
};

export function PluginsPage({ activeScope, projectName, inspector, onChooseProject, onOpenBrowserSettings }: Props) {
  return (
    <section className="main-page">
      <header className="main-page-head">
        <div>
          <h1>插件</h1>
          <p>扩展 Agent 能做的事：项目里的技能、MCP 服务、Hook 和规则，以及内置的浏览器操控。</p>
        </div>
      </header>

      <div className="plugin-builtins">
        <button onClick={onOpenBrowserSettings}>
          <Globe size={18} />
          <span><strong>浏览器操控</strong><small>在独立浏览器里打开网页、读取和截图，点击和输入逐次确认</small></span>
        </button>
      </div>

      {activeScope === "project" ? (
        <div className="plugin-project">
          <h2>{projectName || "当前项目"} 的扩展</h2>
          {inspector}
        </div>
      ) : (
        <div className="main-page-empty">
          <Puzzle size={22} />
          <strong>技能、MCP 和 Hook 跟着项目加载</strong>
          <p>选择一个 Git 项目后，这里会列出它带来的扩展和它们的安全状态。</p>
          <button onClick={onChooseProject}>选择项目</button>
        </div>
      )}
    </section>
  );
}
