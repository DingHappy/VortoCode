import { useId, useState } from "react";
import { createPortal } from "react-dom";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { feedbackMailto, sendFeedback, type FeedbackDraft } from "../lib/feedback";
import { countUsage } from "../lib/telemetry";
import "./FeedbackDialog.css";

/** 「问题反馈」按钮：在 App 里直接写、直接发（原来是打开邮件客户端）。发不出去时仍可改用邮件。 */
export function FeedbackButton({ title }: { title?: string }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" onClick={() => setOpen(true)} title={title}>问题反馈</button>
    {open && <FeedbackDialog onClose={() => setOpen(false)} />}
  </>;
}

export function FeedbackDialog({ onClose, send = sendFeedback }: {
  onClose: () => void; send?: (draft: FeedbackDraft) => Promise<number>;
}) {
  const dialog = useDialogSurface(), titleId = useId();
  const [draft, setDraft] = useState<FeedbackDraft>({ text: "", contact: "", attachLog: true });
  const [state, setState] = useState<{ busy: boolean; error: string; sent: number | null }>({ busy: false, error: "", sent: null });
  const submit = async () => {
    if (state.busy || !draft.text.trim()) return;
    setState({ busy: true, error: "", sent: null });
    try { const id = await send(draft); countUsage("feedback.sent"); setState({ busy: false, error: "", sent: id }); }
    catch (reason) { setState({ busy: false, error: reason instanceof Error ? reason.message : String(reason), sent: null }); }
  };
  const content = <dialog ref={dialog} className="feedback-dialog" aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <h2 id={titleId}>问题反馈</h2>
    {state.sent !== null ? <>
      <p>已收到，谢谢！{state.sent ? `编号 #${state.sent}。` : ""}我们会尽快看。</p>
      <div className="feedback-actions"><button type="button" className="primary" autoFocus onClick={onClose}>好的</button></div>
    </> : <>
      <label className="feedback-field"><span>遇到了什么问题？怎么出现的？</span>
        <textarea autoFocus rows={6} maxLength={4000} value={draft.text} disabled={state.busy}
          placeholder="比如：点「更新」后一直停在 0%，等了五分钟"
          onChange={event => setDraft({ ...draft, text: event.target.value })} /></label>
      <label className="feedback-field"><span>联系方式（选填）</span>
        <input type="text" maxLength={200} value={draft.contact} disabled={state.busy} placeholder="邮箱或微信，方便我们回复"
          onChange={event => setDraft({ ...draft, contact: event.target.value })} /></label>
      <label className="feedback-check"><input type="checkbox" checked={draft.attachLog} disabled={state.busy}
        onChange={event => setDraft({ ...draft, attachLog: event.target.checked })} />
        <span>附上最近的诊断日志<small>连接、更新、报错这类记录，已隐去密钥；不含对话和文件内容。</small></span></label>
      {state.error && <p role="alert" className="feedback-error">{state.error}</p>}
      <div className="feedback-actions">
        <button type="button" className="link" onClick={() => void openUrl(feedbackMailto())}>改用邮件发送</button>
        <span />
        <button type="button" onClick={onClose} disabled={state.busy}>取消</button>
        <button type="button" className="primary" onClick={() => void submit()} disabled={state.busy || !draft.text.trim()}>
          {state.busy ? "正在发送…" : "发送"}</button>
      </div>
    </>}
  </dialog>;
  return typeof document === "undefined" ? content : createPortal(content, document.body);
}
