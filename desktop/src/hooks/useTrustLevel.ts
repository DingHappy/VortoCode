// 工作区授权档位（沿用 useJournal 的 hook 模式与三条红线）。
//
// 档位跟着工作区走（后端存在 .vortocode/trust.json），所以连接、换项目（repoRoot）、
// 打开设置都要重新拉——这三个由 App 传入，effect 依赖与迁移前一致。
import { useEffect, useState } from "react";
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

  useEffect(() => {
    if (connection !== "connected") {
      setTrust(null);
      return;
    }
    void clientRef.current?.getTrust()
      .then(setTrust)
      .catch(() => setTrust(null));   // 旧 runtime 没有这个接口时静默降级：不显示档位卡
  }, [clientRef, connection, repoRoot, settingsOpen]);

  const changeTrustLevel = async (level: TrustLevel) => {
    const client = clientRef.current;
    if (!client || trustBusy || trust?.level === level) return;
    setTrustBusy(true);
    try {
      const next = await client.setTrust(level);
      setTrust(next);
      onBanner(next.level === next.effective
        ? `授权级别已设为「${TRUST_LEVEL_TEXT[next.level]}」`
        : `已选「${TRUST_LEVEL_TEXT[next.level]}」，当前会话最高到「${TRUST_LEVEL_TEXT[next.effective]}」`);
    } catch (error) {
      onBanner(errorText(error, "授权级别保存失败"));
    } finally {
      setTrustBusy(false);
    }
  };

  return { trust, trustBusy, changeTrustLevel };
}
