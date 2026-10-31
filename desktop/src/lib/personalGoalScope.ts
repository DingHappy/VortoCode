export function personalGoalScopeInput(constraints: string, nonGoals: string) {
  const lines = (text: string) => text.split("\n").map(line => line.trim()).filter(Boolean);
  const value = { constraints: lines(constraints), non_goals: lines(nonGoals) };
  const groups = [value.constraints, value.non_goals];
  const error = groups.some(group => group.length > 20) ? "约束和非目标各最多 20 条。"
    : groups.some(group => group.some(line => line.length > 1000 || line.includes("\0"))) ? "每条约束或非目标最多 1000 字符。"
    : groups.flat().reduce((sum, line) => sum + line.length, 0) > 8000 ? "约束和非目标合计最多 8000 字符。" : "";
  return { ...value, error };
}
