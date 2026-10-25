import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { residentDesktop } from "../lib/residentDesktop";

export type ComputerWorkspace = { target: string; id?: number; url?: string; error?: string };
export function validComputerFrame(raw: string): boolean {
  try { const url = new URL(raw); return url.protocol === "http:" && url.hostname === "127.0.0.1"
    && !!url.port && !url.username && !url.password && url.pathname === "/" && !url.search && !url.hash; }
  catch { return false; }
}
export function releaseComputerWorkspace(id: number) {
  void invoke("release_resident_desktop_workspace", { connectionId: id }).catch(() => {});
}

export function useComputerWorkspace(visible: boolean) {
  const [view, setView] = useState<ComputerWorkspace | null>(null);
  const current = useRef<ComputerWorkspace | null>(null);
  const generation = useRef(0);
  const close = useCallback(() => {
    const restore = document.activeElement instanceof HTMLElement && !!document.activeElement.closest(".computer-workspace");
    generation.current++; const previous = current.current; current.current = null; setView(null);
    if (previous?.id !== undefined) releaseComputerWorkspace(previous.id);
    if (restore) requestAnimationFrame(() => {
      if (document.activeElement === document.body) document.querySelector<HTMLElement>(".computer-launcher-main")?.focus();
    });
  }, []);
  const open = useCallback(async () => {
    if (residentDesktop.getSnapshot().busy) return;
    const request = ++generation.current;
    const previous = current.current;
    const waiting = { target: residentDesktop.getSnapshot().status?.target || "远程电脑" };
    current.current = waiting; setView(waiting);
    if (previous?.id !== undefined) releaseComputerWorkspace(previous.id);
    const status = await residentDesktop.act("open_resident_desktop_workspace");
    if (request !== generation.current) {
      if (status?.viewer?.surface === "workspace") releaseComputerWorkspace(status.viewer.id);
      return;
    }
    const next: ComputerWorkspace = status?.viewer?.surface === "workspace" && status.viewerUrl && validComputerFrame(status.viewerUrl)
      ? { target: status.target, id: status.viewer.id, url: status.viewerUrl }
      : { target: waiting.target, error: residentDesktop.getSnapshot().note || "暂时无法打开画面，请检查连接后重试。" };
    if (!next.url && status?.viewer?.surface === "workspace") releaseComputerWorkspace(status.viewer.id);
    current.current = next; setView(next);
  }, []);
  useEffect(() => { if (!visible) close(); }, [visible, close]);
  useEffect(() => () => {
    generation.current++; if (current.current?.id !== undefined) releaseComputerWorkspace(current.current.id);
  }, []);
  return { view, open, close };
}
