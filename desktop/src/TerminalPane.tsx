import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { GatewayClient } from "./gateway";
import type { TerminalSession } from "./types";
import { errorText } from "./lib/errorText";

type TerminalPaneProps = {
  client: GatewayClient | null;
  connected: boolean;
  onNotice: (message: string) => void;
};

export function TerminalPane({ client, connected, onNotice }: TerminalPaneProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const clientRef = useRef(client);
  const activeIdRef = useRef("");
  const offsetRef = useRef(0);
  const pollBusyRef = useRef(false);
  const inputRef = useRef("");
  const inputTimerRef = useRef<number | null>(null);
  const writeChainRef = useRef<Promise<unknown>>(Promise.resolve());
  const resizeTimerRef = useRef<number | null>(null);
  const [sessions, setSessions] = useState<TerminalSession[]>([]);
  const [activeId, setActiveId] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  clientRef.current = client;
  activeIdRef.current = activeId;
  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeId) ?? null,
    [activeId, sessions],
  );

  const mergeSession = useCallback((next: TerminalSession) => {
    setSessions((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
  }, []);

  const sendBufferedInput = useCallback(() => {
    inputTimerRef.current = null;
    const currentClient = clientRef.current;
    const terminalId = activeIdRef.current;
    const data = inputRef.current;
    inputRef.current = "";
    if (!currentClient || !terminalId || !data) return;
    writeChainRef.current = writeChainRef.current
      .catch(() => undefined)
      .then(() => currentClient.writeTerminal(terminalId, data))
      .then(mergeSession)
      .catch((reason: unknown) => setError(errorText(reason, "终端输入失败")));
  }, [mergeSession]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const terminal = new Terminal({
      allowProposedApi: false,
      convertEol: false,
      cursorBlink: true,
      cursorStyle: "bar",
      fontFamily: '"DM Mono", "SFMono-Regular", Menlo, Consolas, monospace',
      fontSize: 12,
      lineHeight: 1.25,
      scrollback: 5_000,
      theme: {
        background: "#0c0f14",
        foreground: "#c6ced9",
        cursor: "#ff845d",
        selectionBackground: "#454d5d99",
        black: "#151922",
        brightBlack: "#596273",
        red: "#ed8585",
        green: "#83d6aa",
        yellow: "#e1be73",
        blue: "#82aef0",
        magenta: "#c89bea",
        cyan: "#72cad4",
        white: "#dce1e8",
      },
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(host);
    fit.fit();
    terminalRef.current = terminal;
    fitRef.current = fit;

    const dataSubscription = terminal.onData((data) => {
      inputRef.current += data;
      if (inputTimerRef.current == null) {
        inputTimerRef.current = window.setTimeout(sendBufferedInput, 18);
      }
    });
    const resizeObserver = new ResizeObserver(() => {
      fit.fit();
      if (resizeTimerRef.current != null) window.clearTimeout(resizeTimerRef.current);
      resizeTimerRef.current = window.setTimeout(() => {
        const currentClient = clientRef.current;
        const terminalId = activeIdRef.current;
        if (!currentClient || !terminalId) return;
        void currentClient.resizeTerminal(terminalId, terminal.cols, terminal.rows)
          .then(mergeSession)
          .catch(() => undefined);
      }, 120);
    });
    resizeObserver.observe(host);

    return () => {
      dataSubscription.dispose();
      resizeObserver.disconnect();
      if (inputTimerRef.current != null) window.clearTimeout(inputTimerRef.current);
      if (resizeTimerRef.current != null) window.clearTimeout(resizeTimerRef.current);
      terminal.dispose();
      terminalRef.current = null;
      fitRef.current = null;
    };
  }, [mergeSession, sendBufferedInput]);

  useEffect(() => {
    if (!connected || !client) {
      setSessions([]);
      setActiveId("");
      return;
    }
    void client.listTerminals().then((items) => {
      setSessions(items);
      const preferred = items.find((item) => item.status === "running") ?? items[0];
      if (preferred) setActiveId((current) => current || preferred.id);
    }).catch(() => undefined);
  }, [client, connected]);

  useEffect(() => {
    offsetRef.current = 0;
    terminalRef.current?.reset();
    if (!activeId || !client || !connected) return;

    const poll = async () => {
      if (pollBusyRef.current) return;
      pollBusyRef.current = true;
      try {
        const snapshot = await client.readTerminal(activeId, offsetRef.current);
        if (activeIdRef.current !== activeId) return;
        if (snapshot.dropped) {
          terminalRef.current?.write("\r\n\x1b[33m[较早的终端输出已从缓冲区移除]\x1b[0m\r\n");
        }
        if (snapshot.output) terminalRef.current?.write(snapshot.output);
        offsetRef.current = snapshot.offset ?? offsetRef.current;
        mergeSession(snapshot);
      } catch (reason) {
        setError(errorText(reason, "读取终端失败"));
      } finally {
        pollBusyRef.current = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 140);
    return () => window.clearInterval(timer);
  }, [activeId, client, connected, mergeSession]);

  const createTerminal = async () => {
    if (!client || !connected || creating) return;
    setCreating(true);
    setError("");
    try {
      fitRef.current?.fit();
      const terminal = terminalRef.current;
      const created = await client.createTerminal(terminal?.cols ?? 100, terminal?.rows ?? 28);
      mergeSession(created);
      setActiveId(created.id);
      window.setTimeout(() => terminalRef.current?.focus(), 40);
      onNotice("已创建隔离的本地 PTY；结构化测试仍在“任务运行”中记录");
    } catch (reason) {
      setError(errorText(reason, "创建终端失败"));
    } finally {
      setCreating(false);
    }
  };

  const stopTerminal = async () => {
    if (!client || !activeSession || activeSession.status !== "running") return;
    try {
      mergeSession(await client.stopTerminal(activeSession.id));
    } catch (reason) {
      setError(errorText(reason, "停止终端失败"));
    }
  };

  return (
    <section className="terminal-pane">
      <div className="terminal-toolbar">
        <div className="terminal-tabs" aria-label="终端会话">
          {sessions.map((session, index) => (
            <button
              className={session.id === activeId ? "active" : ""}
              key={session.id}
              onClick={() => setActiveId(session.id)}
            >
              <i className={session.status} />终端 {index + 1}
            </button>
          ))}
        </div>
        <div className="terminal-actions">
          {activeSession?.status === "running" && <button onClick={() => void stopTerminal()}>停止</button>}
          <button className="primary" onClick={() => void createTerminal()} disabled={!connected || creating}>
            {creating ? "创建中…" : "+ 新建终端"}
          </button>
        </div>
      </div>
      {activeSession && (
        <div className="terminal-meta">
          <span>PID {activeSession.pid}</span>
          <span>{activeSession.shell.split("/").pop()}</span>
          <span>{activeSession.sandbox?.isolated ? `沙箱 · ${String(activeSession.sandbox.backend || "on")}` : "宿主机交互"}</span>
          {activeSession.code != null && <span>退出码 {activeSession.code}</span>}
        </div>
      )}
      {error && <div className="terminal-error">{error}</div>}
      <div className={`terminal-stage ${activeSession ? "ready" : "empty"}`}>
        {!activeSession && <div className="terminal-placeholder"><strong>真实交互终端</strong><p>创建 PTY 后可直接输入命令、使用 Ctrl+C，并随窗口自动调整尺寸。</p></div>}
        <div className="terminal-host" ref={hostRef} />
      </div>
    </section>
  );
}
