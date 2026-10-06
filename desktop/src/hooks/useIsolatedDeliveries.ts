// 对话式 dev_isolated 交付的审查视图（沿用 useJournal 的 hook 模式与三条红线）。
//
// 全部读写都只碰本域状态；isolatedDeliveryRequestRef 是请求代数，任何新请求或清空都 +1，
// 让在途的旧响应落地时被丢弃。refreshIsolatedDeliveries / resetIsolatedDeliveries 被 App 的
// useCallback（协议事件、refreshAllForScope、disconnect）依赖，必须恒等稳定。
import { useCallback, useRef, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import { errorText } from "../lib/errorText";
import type { IsolatedDelivery, IsolatedDeliveryDiff, IsolatedDeliverySnapshot } from "../types";

export function useIsolatedDeliveries(clientRef: RefObject<GatewayClient | null>) {
  const [isolatedDeliveries, setIsolatedDeliveries] = useState<IsolatedDelivery[]>([]);
  const [isolatedDelivery, setIsolatedDelivery] = useState<IsolatedDeliverySnapshot | null>(null);
  const [isolatedDeliveryDiff, setIsolatedDeliveryDiff] = useState<IsolatedDeliveryDiff | null>(null);
  const [isolatedDeliveryPath, setIsolatedDeliveryPath] = useState("");
  const [isolatedDeliveryError, setIsolatedDeliveryError] = useState("");
  const [isolatedDeliveryLoading, setIsolatedDeliveryLoading] = useState(false);
  const isolatedDeliveryRequestRef = useRef(0);

  const refreshIsolatedDeliveries = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    const generation = ++isolatedDeliveryRequestRef.current;
    setIsolatedDelivery(null);
    setIsolatedDeliveryDiff(null);
    setIsolatedDeliveryPath("");
    setIsolatedDeliveries([]);
    setIsolatedDeliveryLoading(true);
    try {
      const deliveries = await client.listIsolatedDeliveries();
      if (generation === isolatedDeliveryRequestRef.current && clientRef.current === client) {
        setIsolatedDeliveries(deliveries);
        setIsolatedDeliveryError("");
      }
    } catch (error) {
      if (generation === isolatedDeliveryRequestRef.current && clientRef.current === client) {
        setIsolatedDeliveryError(errorText(error, "读取隔离交付失败"));
      }
    } finally {
      if (generation === isolatedDeliveryRequestRef.current) setIsolatedDeliveryLoading(false);
    }
  }, [clientRef]);

  const openIsolatedDelivery = async (id: string, preferredPath = "") => {
    const client = clientRef.current;
    if (!client) return;
    const generation = ++isolatedDeliveryRequestRef.current;
    setIsolatedDeliveryError("");
    setIsolatedDelivery(null);
    setIsolatedDeliveryDiff(null);
    try {
      const snapshot = await client.getIsolatedDelivery(id);
      if (generation !== isolatedDeliveryRequestRef.current) return;
      setIsolatedDelivery(snapshot);
      const path = snapshot.files.find((file) => file.path === preferredPath)?.path ?? snapshot.files[0]?.path ?? "";
      setIsolatedDeliveryPath(path);
      if (path) {
        const diff = await client.getIsolatedDeliveryDiff(id, path);
        if (generation === isolatedDeliveryRequestRef.current) setIsolatedDeliveryDiff(diff);
      }
    } catch (error) {
      if (generation === isolatedDeliveryRequestRef.current) setIsolatedDeliveryError(errorText(error, "读取隔离交付失败"));
    }
  };

  const loadIsolatedDeliveryDiff = async (path: string) => {
    const client = clientRef.current;
    if (!client || !isolatedDelivery) return;
    const generation = ++isolatedDeliveryRequestRef.current;
    setIsolatedDeliveryPath(path);
    setIsolatedDeliveryDiff(null);
    try {
      const diff = await client.getIsolatedDeliveryDiff(isolatedDelivery.id, path);
      if (generation === isolatedDeliveryRequestRef.current) {
        setIsolatedDeliveryDiff(diff);
        setIsolatedDeliveryError("");
      }
    } catch (error) {
      if (generation === isolatedDeliveryRequestRef.current) setIsolatedDeliveryError(errorText(error, "读取隔离分支 diff 失败"));
    }
  };

  /** 离开工作区时清空（与迁移前 disconnect 内的块一致：错误提示保留）。 */
  const resetIsolatedDeliveries = useCallback(() => {
    ++isolatedDeliveryRequestRef.current;
    setIsolatedDeliveries([]);
    setIsolatedDelivery(null);
    setIsolatedDeliveryDiff(null);
    setIsolatedDeliveryPath("");
    setIsolatedDeliveryLoading(false);
  }, []);

  return {
    isolatedDeliveries, isolatedDelivery, isolatedDeliveryDiff, isolatedDeliveryPath,
    isolatedDeliveryError, isolatedDeliveryLoading,
    refreshIsolatedDeliveries, openIsolatedDelivery, loadIsolatedDeliveryDiff, resetIsolatedDeliveries,
  };
}
