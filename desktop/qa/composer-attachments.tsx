import { useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AttachmentChips, AttachmentReadStatus } from "../src/components/ComposerPanels";
import { ComposerCommands } from "../src/components/ComposerCommands";
import { useAttachments } from "../src/hooks/useAttachments";
import { readAttachment, type AttachmentReader } from "../src/lib/attachmentDraft";
import { reportComposerFailure } from "../src/lib/composerFailure";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/styles/shell.css";
import "./composer-attachments.css";

function Fixture() {
  const [owner, setOwner] = useState("本机 / 对话 A"), [notice, setNotice] = useState("");
  const [prompt, setPrompt] = useState("请看看这些附件"), [sent, setSent] = useState(0), [blocked, setBlocked] = useState(0);
  const [reads, setReads] = useState(0), [cancelled, setCancelled] = useState(0);
  const [narrow, setNarrow] = useState(new URLSearchParams(location.search).has("narrow"));
  const [light, setLight] = useState(new URLSearchParams(location.search).has("light"));
  const automatic = useRef(false), gates = useRef<(() => void)[]>([]), batch = useRef(0);
  const reader = useMemo<AttachmentReader>(() => (file, kind) => {
    setReads(value => value + 1);
    const operation = readAttachment(file, kind);
    const result = operation.result.then(data => new Promise<string>(resolve => {
      const release = () => resolve(data);
      if (automatic.current) release(); else gates.current.push(release);
    }));
    return { result, cancel: () => { setCancelled(value => value + 1); operation.cancel(); } };
  }, []);
  const draft = useAttachments(setNotice, owner, reader);
  const delivery = useRef<{ current: () => boolean; restore: () => boolean; text: string } | null>(null);
  document.documentElement.dataset.theme = light ? "light" : "dark";
  const changePrompt = (text: string) => { draft.markComposerEdited(); setPrompt(text); };
  const addImages = (count: number) => {
    automatic.current = false; batch.current++;
    const bytes = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jA1sAAAAASUVORK5CYII="), char => char.charCodeAt(0));
    void draft.addFiles(Array.from({ length: count }, (_, index) => new File([bytes], `样例-${batch.current}-${index + 1}.png`, { type: "image/png" })));
  };
  const send = () => {
    if (draft.isReadingAttachments()) { setBlocked(value => value + 1); setNotice("附件还在读取，请读完后再发送，或取消本次附件选择"); return; }
    const receipt = draft.takeAttachments(draft.attachments);
    if (!receipt) return;
    delivery.current = { current: draft.isCurrentAttachments, restore: receipt.restore, text: prompt };
    setPrompt(""); setSent(value => value + 1); setNotice(`合成发送：${receipt.items.length} 个附件，未连接任何运行端`);
  };
  const fail = () => {
    const receipt = delivery.current;
    if (!receipt) return;
    reportComposerFailure({ current: receipt.current(), restore: receipt.restore, recover: () => setPrompt(receipt.text),
      markFailed: () => setNotice("合成发送失败"), notify: setNotice, message: "这条消息没确认送达" });
  };
  return <main className={`qa-attachment ${narrow ? "narrow" : ""}`}>
    <h1>附件归属与读取 · 合成验收</h1><p>生产 Hook、FileReader 与输入组件；仅本地样例，不连接模型或运行端。</p>
    <div className="qa-attachment-controls">
      <button onClick={() => addImages(4)}>添加四张图片</button><button onClick={() => addImages(3)}>再加三张图片</button>
      <button onClick={() => { automatic.current = true; gates.current.splice(0).forEach(release => release()); }}>完成全部读取</button>
      <button onClick={() => { automatic.current = false; void draft.addFiles([new File(["本地文本样例"], "样例说明.md", { type: "text/markdown" })]); }}>添加文本样例</button>
      <button onClick={() => setOwner(value => value.endsWith("A") ? "本机 / 对话 B" : "本机 / 对话 A")}>切换对话</button>
      <button onClick={() => setOwner(value => value.startsWith("本机") ? "远端 / 对话 A" : "本机 / 对话 A")}>切换运行端</button>
      <button onClick={fail}>模拟发送失败</button><button onClick={() => setNarrow(!narrow)}>{narrow ? "恢复宽栏" : "窄栏 360"}</button>
      <button onClick={() => setLight(!light)}>切换{light ? "深色" : "浅色"}</button>
    </div>
    <p role="status">当前：{owner}；{notice}</p>
    <section className="composer qa-attachment-composer">
      <AttachmentChips items={draft.attachments} onRemove={draft.removeAttachment}/>
      <AttachmentReadStatus pending={draft.attachmentsReading} onCancel={draft.cancelAttachmentReads}/>
      <ComposerCommands prompt={prompt} onPrompt={changePrompt} project={false} disabled={false} onAction={() => {}} onSend={send}>
        <div className="composer-footer"><span>本地合成会话</span><button className="composer-send" aria-label="发送合成消息" disabled={draft.attachmentsReading > 0 || (!prompt.trim() && !draft.attachments.length)} onClick={send}>↑</button></div>
      </ComposerCommands>
    </section>
    <output aria-label="附件验收量测">待读取 {draft.attachmentsReading}；已就绪 {draft.attachments.length}；本地读取 {reads}；取消 {cancelled}；合成发送 {sent}；回车拦截 {blocked}；模型与网络调用 0</output>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
