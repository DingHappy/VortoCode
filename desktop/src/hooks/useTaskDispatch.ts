import { useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { DispatchCapabilities, TaskItem } from "../types";
import { DispatchAttempt, chainBudgetSelection, dependencyCandidates, dependencySelectionCurrent } from "../lib/taskDispatch";

export type TaskDispatchOptions = {
  value: string;
  onChange: (value: string) => void;
  getClient: () => GatewayClient | null;
  scopeKey: string;
  session: string;
  enabled: boolean;
  onSubmitted: (id: string) => void;
  tasks?: TaskItem[];
};

export function useTaskDispatch(props: TaskDispatchOptions) {
  const [mode, setMode] = useState<"dev" | "research">("dev");
  const [agent, setAgent] = useState("");
  const [criteria, setCriteria] = useState("");
  const [maxSteps, setMaxSteps] = useState(8);
  const [requirements, setRequirements] = useState<Array<{ task_id: string; round: number }>>([]);
  const [budgetEnabled, setBudgetEnabled] = useState(false);
  const [capabilities, setCapabilities] = useState<DispatchCapabilities | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [capabilityError, setCapabilityError] = useState("");
  const attempt = useRef(new DispatchAttempt());
  const pending = useRef(false);
  const current = useRef(props);
  current.current = props;
  const client = props.getClient();

  useEffect(() => {
    let disposed = false;
    setCapabilities(null);
    setCapabilityError("");
    if (mode !== "research" || !client || !props.enabled) return;
    void client.dispatchCapabilities().then((value) => {
      if (!disposed && current.current.getClient() === client) {
        if (value.version === 1 && value.mode === "read") setCapabilities(value);
        else setCapabilityError("当前运行服务不支持研究任务下派。");
      }
    }).catch(() => {
      if (!disposed) setCapabilityError("暂时无法读取研究角色；请重新连接，或使用隔离开发入口。");
    });
    return () => { disposed = true; };
  }, [client, mode, props.enabled, props.scopeKey]);

  useEffect(() => {
    setAgent("");
    setRequirements([]);
    setBudgetEnabled(false);
    setError("");
  }, [props.scopeKey]);

  const submit = async () => {
    const snapshot = current.current;
    const activeClient = snapshot.getClient();
    const text = snapshot.value.trim();
    if (!activeClient || !snapshot.enabled || !text || pending.current) return;
    if (mode === "research" && !capabilities) return;
    if (mode === "research" && agent && !capabilities?.agents.some((role) => role.name === agent)) return;
    if (mode === "research" && !dependencySelectionCurrent(requirements, snapshot.tasks || [], snapshot.session)) {
      setError("前置任务归属或轮次已变化，或超过 8 项；请刷新后重新选择。");
      return;
    }
    if (mode === "research" && requirements.length && capabilities?.dependencies?.mode !== "explicit_release") {
      setError("当前运行服务不支持依赖任务；请升级运行服务后重试。");
      return;
    }
    const budgetSelection = chainBudgetSelection(requirements, snapshot.tasks || [], snapshot.session);
    if (mode === "research" && (budgetSelection.invalid || budgetSelection.roots.length > 1)) {
      setError("前置任务的额度合同无效或属于不同预算链；请核对后分别下派。");
      return;
    }
    if (mode === "research" && (budgetEnabled || budgetSelection.roots.length) &&
      (!capabilities?.chain_budget?.available || capabilities.chain_budget.mode !== "execution_allowance")) {
      setError("当前运行服务不支持任务链额度，请刷新后重试。");
      return;
    }
    const acceptance = criteria.split("\n").map((item) => item.trim()).filter(Boolean);
    if (mode === "research" && (acceptance.length > 20 || acceptance.some((item) => item.length > 500))) {
      setError("验收要求最多 20 条，每条不超过 500 字符。");
      return;
    }
    pending.current = true;
    setBusy(true);
    setError("");
    const isCurrent = () => current.current.scopeKey === snapshot.scopeKey && current.current.getClient() === activeClient;
    try {
      const result = mode === "research"
        ? await activeClient.submitDelegation(attempt.current.prepare(snapshot.scopeKey, {
          prompt: text, agent, acceptance, max_steps: maxSteps, timeout_seconds: 300,
          ...(requirements.length ? { depends_on: [...requirements].sort((a, b) => a.task_id < b.task_id ? -1 : 1) } : {}),
          ...(budgetEnabled && !budgetSelection.roots.length ? { chain_limits: { ...capabilities!.chain_budget!.defaults } } : {}),
        }), snapshot.session)
        : await activeClient.submitTask(text);
      if (!isCurrent()) return;
      attempt.current.complete(snapshot.scopeKey);
      setRequirements([]);
      setBudgetEnabled(false);
      if (current.current.value === snapshot.value) current.current.onChange("");
      current.current.onSubmitted(result.id);
    } catch (reason) {
      if (isCurrent()) setError(reason instanceof Error ? reason.message : "任务提交失败，请重试。");
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };

  return { mode, setMode, agent, setAgent, criteria, setCriteria, maxSteps, setMaxSteps,
    requirements, setRequirements, dependencyCandidates: dependencyCandidates(props.tasks || [], props.session),
    budgetEnabled, setBudgetEnabled, inheritedBudget: chainBudgetSelection(requirements, props.tasks || [], props.session),
    capabilities, busy, error, setError, capabilityError, submit };
}
