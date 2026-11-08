import { copyLabel } from "./copy.generated";

/** 展示服务端的类别与风险；这里不生成授权、推断正文或决定可执行能力。文案来自 design/copy.json（两端共用）。 */
export function approvalCategory(kind: unknown): string {
  return copyLabel("approval_category", kind);
}
export function approvalRisk(risk: unknown): string {
  return copyLabel("approval_risk", risk);
}
export function approvalSource(source: unknown): string {
  return copyLabel("approval_source", source);
}
export function approvalScope(scope: unknown): string {
  return copyLabel("approval_scope", scope);
}
