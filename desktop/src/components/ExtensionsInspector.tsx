// App.tsx 抽出的「扩展检查」卡（B8-④b S4，只搬家不改行为）。
// settings 弹窗内、activeScope==="project" 时显示的独立卡：列出当前项目生效的
// rules/skill/hook/mcp 扩展与安全状态，并提供项目 Hook 信任开关。
//
// 与 RunsPanel 同构的抽法：**只有纯本地 UI 状态下移**——类型筛选 tab（filter）由本组件自持。
// 被拉取/被 App 编排的数据留在 App 经 props 注入：extensionsInspect / hookStatus 由
// connect() 与「设置打开」effect 预取并缓存（弹窗关闭态仍持有，重开不闪「读取中」），
// 若搬进只在弹窗内挂载的本组件会丢预取、每次重开都要重新拉，故按行为零变化留 App。
// 拉取与 Hook 信任写入（refresh*/toggleHookTrust，触碰 client/审计/banner）也留 App。
import { useState } from "react";

import { extensionKindLabel, extensionStatusLabel, hookCapabilityLabel } from "../lib/labels";
import type {
  ConnectionState,
  ExtensionInspectKind,
  ExtensionsInspectSnapshot,
  HookConfigStatus,
} from "../types";

type ExtensionInspectFilter = "all" | ExtensionInspectKind;

type ExtensionsInspectorProps = {
  extensionsInspect: ExtensionsInspectSnapshot | null;
  extensionsInspectBusy: boolean;
  hookStatus: HookConfigStatus | null;
  hookTrustBusy: boolean;
  connection: ConnectionState;
  onRefresh: () => void;
  onToggleHookTrust: () => void;
};

export function ExtensionsInspector({
  extensionsInspect,
  extensionsInspectBusy,
  hookStatus,
  hookTrustBusy,
  connection,
  onRefresh,
  onToggleHookTrust,
}: ExtensionsInspectorProps) {
  const [extensionsInspectFilter, setExtensionsInspectFilter] = useState<ExtensionInspectFilter>("all");

  return (
    <section className="extensions-inspect-card">
      <div className="extensions-inspect-heading">
        <div>
          <span>Extensions</span>
          <strong>当前项目加载面</strong>
        </div>
        <button
          onClick={onRefresh}
          disabled={connection !== "connected" || extensionsInspectBusy || hookTrustBusy}
        >{extensionsInspectBusy ? "读取中…" : "重新检查"}</button>
      </div>
      <p>只显示生效来源与安全状态；不会执行扩展，也不会展示规则正文、MCP 地址、命令或凭据。</p>
      {extensionsInspect && (
        <div className="extensions-inspect-summary">
          <span><b>{extensionsInspect.summary.active}</b> 已加载</span>
          <span><b>{extensionsInspect.summary.total}</b> 总计</span>
          <span className={extensionsInspect.summary.attention > 0 ? "attention" : ""}><b>{extensionsInspect.summary.attention}</b> 需注意</span>
        </div>
      )}
      <div className="extensions-inspect-tabs" role="tablist" aria-label="扩展类型">
        {(["all", "rules", "skill", "hook", "mcp"] as ExtensionInspectFilter[]).map((kind) => {
          const count = kind === "all"
            ? extensionsInspect?.items.length ?? 0
            : extensionsInspect?.items.filter((item) => item.kind === kind).length ?? 0;
          return (
            <button
              key={kind}
              className={extensionsInspectFilter === kind ? "active" : ""}
              onClick={() => setExtensionsInspectFilter(kind)}
              role="tab"
              aria-selected={extensionsInspectFilter === kind}
            >{kind === "all" ? "全部" : extensionKindLabel(kind)} <i>{count}</i></button>
          );
        })}
      </div>

      {(extensionsInspectFilter === "all" || extensionsInspectFilter === "hook") && hookStatus?.configured && (
        <div className={`extensions-hook-trust ${hookStatus.trusted ? "trusted" : "untrusted"}`}>
          <div>
            <strong>{hookStatus.trusted ? "项目 Hook 已信任" : "项目 Hook 等待信任"}</strong>
            <span>{hookStatus.config_path} · {hookStatus.config_sha256}</span>
          </div>
          <button
            className={hookStatus.trusted ? "danger" : "primary"}
            onClick={() => void onToggleHookTrust()}
            disabled={hookTrustBusy || Boolean(hookStatus.error)}
          >{hookTrustBusy ? "更新中…" : hookStatus.trusted ? "撤销信任" : "检查后信任"}</button>
          <p>{hookStatus.error
            ? `配置无法加载：${hookStatus.error}`
            : hookStatus.trusted
              ? "当前会话已热加载；只有明确的 PreToolUse 拒绝可以阻止工具。"
              : "仓库 Hook 可能执行本机命令或发送 HTTP 请求，默认不会运行。"}</p>
        </div>
      )}

      {connection !== "connected" ? (
        <div className="extensions-inspect-empty">本地引擎连接后显示当前项目扩展。</div>
      ) : extensionsInspectBusy && !extensionsInspect ? (
        <div className="extensions-inspect-empty">正在读取项目扩展…</div>
      ) : (
        <div className="extensions-inspect-list">
          {(extensionsInspect?.items ?? [])
            .filter((item) => extensionsInspectFilter === "all" || item.kind === extensionsInspectFilter)
            .map((item) => (
              <div className={`extensions-inspect-item ${item.status}`} key={item.id}>
                <div className="extensions-inspect-item-head">
                  <span>{extensionKindLabel(item.kind)}</span>
                  <strong title={item.name}>{item.name}</strong>
                  <i>{extensionStatusLabel(item.status)}</i>
                </div>
                <code title={item.source}>{item.source}</code>
                {item.description && <p>{item.description}</p>}
                {item.capabilities.length > 0 && (
                  <small title={item.capabilities.join(", ")}>能力：{item.capabilities.map(hookCapabilityLabel).join(" / ")}</small>
                )}
                {item.detail && <small>{item.detail}</small>}
                {item.issue && <em>{item.issue}</em>}
              </div>
            ))}
          {(extensionsInspect?.items ?? []).filter((item) => extensionsInspectFilter === "all" || item.kind === extensionsInspectFilter).length === 0 && (
            <div className="extensions-inspect-empty">当前类型没有发现扩展。</div>
          )}
        </div>
      )}
      {(extensionsInspect?.issues.length ?? 0) > 0 && (
        <details className="extensions-inspect-issues">
          <summary>{extensionsInspect?.issues.length} 项安全提示</summary>
          {extensionsInspect?.issues.map((issue, index) => <p key={`${issue}-${index}`}>{issue}</p>)}
        </details>
      )}
    </section>
  );
}
