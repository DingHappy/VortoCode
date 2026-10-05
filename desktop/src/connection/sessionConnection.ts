import type { ProtocolEvent } from "../types";

type SessionClient = {
  connect: (sid: string, onEvent: (event: ProtocolEvent) => void) => Promise<void>;
  disconnect: () => Promise<void>;
};
type ClientRef<T> = { current: T | null };

/** Generation bookkeeping; the injected ref remains the sole client owner. */
export class SessionConnection<T extends SessionClient> {
  private generation = 0;
  constructor(private readonly clientRef: ClientRef<T>) {}

  disconnect = async () => {
    const generation = ++this.generation;
    const client = this.clientRef.current;
    this.clientRef.current = null;
    await client?.disconnect().catch(() => undefined);
    return generation === this.generation;
  };

  begin = () => {
    const generation = ++this.generation;
    let attached: T | null = null;
    const current = () => generation === this.generation
      && (!attached || this.clientRef.current === attached);

    const attach = async (create: () => T, sid: string, onEvent: (event: ProtocolEvent) => void) => {
      await this.clientRef.current?.disconnect().catch(() => undefined);
      if (!current()) return null;
      const client = create();
      attached = client;
      this.clientRef.current = client;
      try {
        await client.connect(sid, (event) => {
          if (current()) onEvent(event);
        });
      } catch (error) {
        if (current()) throw error;
        await client.disconnect().catch(() => undefined);
        return null;
      }
      if (!current()) {
        await client.disconnect().catch(() => undefined);
        return null;
      }
      return client;
    };
    return { attach, current };
  };
}
