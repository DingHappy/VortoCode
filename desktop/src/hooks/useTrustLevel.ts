// 工作区授权档位（沿用 useJournal 的 hook 模式与三条红线）。
//
// 档位跟着工作区走（后端存在 .vortoone/trust.json），所以连接、换项目（repoRoot）、
// 打开设置都要重新拉——这三个由 App 传入，effect 依赖与迁移前一致。
import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import { errorText } from "../lib/errorText";
import type { ConnectionState, TrustLevel, TrustStatus } from "../types";

const TRUST_LEVEL_TEXT: Record<TrustLevel, string> = {
  ask: "每次确认",
  reads: "读取与事项免确认",
  full: "完全信任",
};

export function useTrustLevel(
  clientRef: RefObject<GatewayClient | null>,
  connection: ConnectionState,
  repoRoot: string,
  settingsOpen: boolean,
  onBanner: (text: string) => void,
  scope = "",
) {
  type Owner = { client: GatewayClient; epoch: number; scope: string };
  const [snapshot, setSnapshot] = useState<(Owner & { value: TrustStatus }) | null>(null);
  const [busyOwner, setBusyOwner] = useState<Owner | null>(null);
  const generation = useRef(0);
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const matches = (owner: Owner | null) => Boolean(owner && owner.client === clientRef.current
    && owner.epoch === owner.client.connectionEpoch && owner.scope === scopeRef.current);
  const trust = matches(snapshot) ? snapshot!.value : null;
  const trustBusy = matches(busyOwner);

  useEffect(() => {
    const ticket = ++generation.current;
    const client = clientRef.current;
    setSnapshot(null);
    setBusyOwner(null);
    if (connection !== "connected" || !client) {
      return;
    }
    const owner = { client, epoch: client.connectionEpoch, scope };
    const current = () => generation.current === ticket && matches(owner);
    void client.getTrust()
      .then(value => { if (current()) setSnapshot({ ...owner, value }); })
      .catch(() => { if (current()) setSnapshot(null); });
    return () => { ++generation.current; };
  }, [clientRef, connection, repoRoot, settingsOpen, scope]);

  const changeTrustLevel = async (level: TrustLevel) => {
    const client = clientRef.current;
    if (!client || connection !== "connected" || !trust || !matches(snapshot) || trustBusy || trust.level === level) return;
    const ticket = ++generation.current; // invalidate any older GET as well as prior workspace callbacks
    const owner = { client, epoch: client.connectionEpoch, scope };
    const current = () => generation.current === ticket && matches(owner);
    setBusyOwner(owner);
    try {
      const next = await client.setTrust(level);
      if (!current()) return;
      setSnapshot({ ...owner, value: next });
      onBanner(next.level === next.effective
        ? `授权级别已设为「${TRUST_LEVEL_TEXT[next.level]}」`
        : `已选「${TRUST_LEVEL_TEXT[next.level]}」，当前会话最高到「${TRUST_LEVEL_TEXT[next.effective]}」`);
    } catch (error) {
      if (current()) onBanner(errorText(error, "授权级别保存失败"));
    } finally {
      if (current()) setBusyOwner(null);
    }
  };

  return { trust, trustBusy, changeTrustLevel };
}
