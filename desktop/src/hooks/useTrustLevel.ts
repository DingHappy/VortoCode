// 工作区授权档位（沿用 useJournal 的 hook 模式与三条红线）。
//
// 档位跟着工作区走（后端存在 .vortocode/trust.json），所以连接、换项目（repoRoot）、
// 打开设置都要重新拉——这三个由 App 传入，effect 依赖与迁移前一致。
import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import { errorText } from "../lib/errorText";
import type { ConnectionState, TrustLevel, TrustStatus } from "../types";

const TRUST_LEVEL_TEXT: Record<TrustLevel, string> = {
  ask: "每次确认",
  reads: "只读免确认",
  full: "完全信任",
};

export function useTrustLevel(
  clientRef: RefObject<GatewayClient | null>,
  connection: ConnectionState,
  repoRoot: string,
  settingsOpen: boolean,
  onBanner: (text: string) => void,
) {
  const [trust, setTrust] = useState<TrustStatus | null>(null);
  const [trustBusy, setTrustBusy] = useState(false);
  const generation = useRef(0);

  useEffect(() => {
    const ticket = ++generation.current;
    const client = clientRef.current;
    setTrust(null);
    setTrustBusy(false);
    if (connection !== "connected") {
      return;
    }
    const current = () => generation.current === ticket && clientRef.current === client;
    void client?.getTrust()
      .then((value) => { if (current()) setTrust(value); })
      .catch(() => { if (current()) setTrust(null); });
    return () => { ++generation.current; };
  }, [clientRef, connection, repoRoot, settingsOpen]);

  const changeTrustLevel = async (level: TrustLevel) => {
    const client = clientRef.current;
    if (!client || connection !== "connected" || !trust || trustBusy || trust.level === level) return;
    const ticket = ++generation.current; // invalidate any older GET as well as prior workspace callbacks
    const current = () => generation.current === ticket && clientRef.current === client;
    setTrustBusy(true);
    try {
      const next = await client.setTrust(level);
      if (!current()) return;
      setTrust(next);
      onBanner(next.level === next.effective
        ? `授权级别已设为「${TRUST_LEVEL_TEXT[next.level]}」`
        : `已选「${TRUST_LEVEL_TEXT[next.level]}」，当前会话最高到「${TRUST_LEVEL_TEXT[next.effective]}」`);
    } catch (error) {
      if (current()) onBanner(errorText(error, "授权级别保存失败"));
    } finally {
      if (current()) setTrustBusy(false);
    }
  };

  return { trust, trustBusy, changeTrustLevel };
}
