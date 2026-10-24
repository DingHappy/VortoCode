import type { ConversationLocationStatus } from "../hooks/useConversationLocation";
import "./ConversationLocation.css";

export function ConversationLocation({ status, onLatest, onRetry }: {
  status: ConversationLocationStatus | null; onLatest: () => void; onRetry: () => void;
}) {
  if (!status) return null;
  return <div className="conversation-location" aria-label="原消息定位">
    <span role="status">{status === "locating" ? "正在定位这条动态的原消息…"
      : status === "located" ? "已定位这条动态的原消息" : "已打开原对话，当前历史中暂未定位到这条消息。"}</span>
    <div>{status === "missing" && <button onClick={onRetry}>重试定位</button>}<button onClick={onLatest}>回到最新</button></div>
  </div>;
}
