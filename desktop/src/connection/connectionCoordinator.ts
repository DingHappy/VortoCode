// 本机 / 常驻电脑 / 远端项目三种连接之间切换的协调规则，收在一处、可单测。
// 以前散在 App.tsx 的几个 ref 里，由各调用方自己查标志；切换前要弹确认框时，
// 两次切换可以先后通过检查再一起进来。这里把三条规则钉住：
//   1. 同一时刻只允许一个切换（切项目、切范围、连常驻电脑），进门时再查一次；
//   2. 本机引擎启动只允许一个，启动中拒绝会先断开连接的切换；
//   3. 连接被更新的连接取代不算失败，只有真失败才记下来给调用方提示。
// 连接对象本身仍由 SessionConnection 管代次；这里只管「谁在切、连在哪」。

/** 只测通不通（不切换）：通返回 null，不通返回原因。 */
export type Reachability = () => Promise<string | null>;

/** 常驻电脑自动重连的退避：10 秒、30 秒、1 分钟，之后每 2 分钟。 */
export const RESIDENT_RETRY_DELAYS = [10_000, 30_000, 60_000, 120_000];
export function residentRetryDelay(attempt: number): number {
  return RESIDENT_RETRY_DELAYS[Math.min(Math.max(attempt, 0), RESIDENT_RETRY_DELAYS.length - 1)];
}

/**
 * 后台重连一次：先只测通不通，不通就把原因交出去、什么都不动（不断开本机，不清当前对话）；
 * 通了才走完整切换。返回是否切过去了。
 */
export async function retryResident(deps: {
  reachable: Reachability;
  connect: () => Promise<boolean>;
  onUnreachable: (reason: string) => void;
}): Promise<boolean> {
  const reason = await deps.reachable();
  if (reason) {
    deps.onUnreachable(reason);
    return false;
  }
  return deps.connect();
}

type Ref<T> = { current: T };

export interface CoordinatorListener {
  /** 切换开始/结束：驱动界面上的「正在切换」状态 */
  switching?: (active: boolean) => void;
  /** 本机引擎启动开始/结束 */
  starting?: (active: boolean) => void;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export class ConnectionCoordinator {
  private switchingNow = false;
  private startingNow = false;
  /** 当前连着的远端登记 id；连本机时为 null */
  readonly remote: Ref<string | null> = { current: null };
  /** 当前连着的远端若是常驻助理就记下它的 id；与 remote 相等才算「在常驻电脑上」 */
  readonly assistantHost: Ref<string | null> = { current: null };
  /** 最近一次连接是真的失败了（而不是被更新的连接取代）：只有这时才提示用户；记下原因 */
  private failure: string | null = null;

  constructor(private listener: CoordinatorListener = {}) {}

  listen(listener: CoordinatorListener) { this.listener = listener; }

  get switching() { return this.switchingNow; }
  get starting() { return this.startingNow; }
  /** 本机在切换或引擎在启动：这时不该再发起会断开连接的操作 */
  get busy() { return this.switchingNow || this.startingNow; }
  get onAssistantHost() { return Boolean(this.remote.current) && this.remote.current === this.assistantHost.current; }
  get lastConnectFailed() { return this.failure !== null; }
  /** 最近一次真失败的原因；回落提示会盖掉横幅，原因得留在这里给后面的提示用 */
  get lastConnectError() { return this.failure; }

  /** 连接开始：清掉上一轮的失败记录 */
  connectBegan() { this.failure = null; }
  /** 当前这轮连接真失败了（调用方已确认没被取代） */
  connectFailed(reason = "连接失败") { this.failure = reason; }

  /** 切换的单飞门：已有切换在跑就返回 false，不执行 body。 */
  async exclusiveSwitch(body: () => Promise<boolean>): Promise<boolean> {
    if (this.switchingNow) return false;
    this.switchingNow = true;
    this.listener.switching?.(true);
    try {
      return await body();
    } finally {
      this.switchingNow = false;
      this.listener.switching?.(false);
    }
  }

  /** 本机引擎启动的单飞门：已在启动就返回 false。 */
  async exclusiveStart(body: () => Promise<boolean>): Promise<boolean> {
    if (this.startingNow) return false;
    this.startingNow = true;
    this.listener.starting?.(true);
    try {
      return await body();
    } finally {
      this.startingNow = false;
      this.listener.starting?.(false);
    }
  }

  /** 等本机的切换和启动都走完；超时仍忙返回 false。 */
  async waitUntilIdle(timeoutMs = 15_000, stepMs = 200, wait: (ms: number) => Promise<void> = sleep): Promise<boolean> {
    for (let waited = 0; this.busy && waited < timeoutMs; waited += stepMs) await wait(stepMs);
    return !this.busy;
  }
}

export interface AssistantPlacement<Host extends { id: string; name: string }> {
  /** 已登记的常驻电脑；没有就直接用本机 */
  host?: Host;
  /** 连上返回 true，连不上返回原因 */
  connectHost: (host: Host) => Promise<true | string>;
  /** 当前不在本机通用工作区（连着远端或在项目里），需要先切回来 */
  awayFromLocalGeneral: () => boolean;
  switchToLocalGeneral: () => Promise<boolean>;
  /** 常驻电脑连不上：把原因交给界面常驻显示（横幅会被随后的本机连接清掉） */
  onHostFailure: (host: Host, reason: string) => void;
}

/**
 * 助理住在哪：优先常驻电脑（合上笔记本也照常干活）；没登记或连不上，退回本机通用工作区。
 * 返回落点：常驻电脑的 id、本机（null），或两边都没落下（undefined）。
 */
export async function placeAssistant<Host extends { id: string; name: string }>(
  deps: AssistantPlacement<Host>,
): Promise<string | null | undefined> {
  if (deps.host) {
    const result = await deps.connectHost(deps.host);
    if (result === true) return deps.host.id;
    deps.onHostFailure(deps.host, result);
  }
  if (deps.awayFromLocalGeneral() && !await deps.switchToLocalGeneral()) return undefined;
  return null;
}
