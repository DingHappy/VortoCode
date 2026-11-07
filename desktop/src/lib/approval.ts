/** 展示服务端的类别与风险；这里不生成授权、推断正文或决定可执行能力。 */
export function approvalCategory(kind: unknown): string {
  const labels: Record<string, string> = {
    read: "只读访问", own_data: "你的事项", draft: "本地草稿", write: "文件修改",
    execute: "命令执行", deliver: "对外发送", interact: "电脑或网页操作", irreversible: "不可逆操作",
  };
  return typeof kind === "string" ? Object.prototype.hasOwnProperty.call(labels, kind) ? labels[kind] : "未分类操作" : "类别待核对";
}
export function approvalRisk(risk: unknown): string {
  return risk === "normal" ? "普通确认" : risk === "high" ? "加强确认" : "级别待核对";
}
export function approvalSource(source: unknown): string {
  const labels: Record<string, string> = { user: "人工确认", automatic_review: "后台审核", preauthorized: "既有授权", system: "系统决定", timeout: "超时", cancelled: "已取消" };
  return typeof source === "string" && Object.prototype.hasOwnProperty.call(labels, source) ? labels[source] : "来源待核对";
}
