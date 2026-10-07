import { describe, expect, it, vi } from "vitest";
import type { ProtocolEvent } from "../types";
import { SessionConnection } from "./sessionConnection";

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function client(gate = deferred()) {
  let emit = (_event: ProtocolEvent) => {};
  return { gate, emit: (event: ProtocolEvent) => emit(event),
    connect: vi.fn(async (_sid: string, callback: (event: ProtocolEvent) => void) => {
      emit = callback;
      await gate.promise;
    }), disconnect: vi.fn(async () => {}) };
}

describe("session connection ownership", () => {
  it("accepts only the latest handshake and suppresses old stream events", async () => {
    const first = client(), second = client();
    const ref = { current: null as typeof first | null };
    const connection = new SessionConnection(ref);
    const onEvent = vi.fn();
    const older = connection.begin();
    const oldAttach = older.attach(() => first, "one", onEvent);
    await Promise.resolve();
    const newer = connection.begin();
    const newAttach = newer.attach(() => second, "two", onEvent);
    await Promise.resolve();
    await Promise.resolve();
    second.gate.resolve();
    expect(await newAttach).toBe(second);
    first.emit({ type: "task_update", data: { id: "old" } });
    second.emit({ type: "task_update", data: { id: "new" } });
    first.gate.resolve();
    expect(await oldAttach).toBeNull();
    expect(older.current()).toBe(false);
    expect(newer.current()).toBe(true);
    expect(ref.current).toBe(second);
    expect(onEvent.mock.calls).toEqual([[{ type: "task_update", data: { id: "new" } }]]);
  });

  it("a stale failure cannot clear or fail the new connection", async () => {
    const first = client(), second = client();
    const ref = { current: null as typeof first | null };
    const connection = new SessionConnection(ref);
    const oldAttach = connection.begin().attach(() => first, "one", vi.fn());
    await Promise.resolve();
    const newer = connection.begin();
    const newAttach = newer.attach(() => second, "two", vi.fn());
    await Promise.resolve();
    await Promise.resolve();
    second.gate.resolve();
    await newAttach;
    first.gate.reject(new Error("old handshake failed"));
    expect(await oldAttach).toBeNull();
    expect(ref.current).toBe(second);
    expect(newer.current()).toBe(true);
  });

  it("invalidating while disconnecting prevents a pending attempt from attaching", async () => {
    const previous = client();
    const leaving = deferred();
    previous.disconnect = vi.fn(() => leaving.promise);
    const ref = { current: previous };
    const connection = new SessionConnection(ref);
    const create = vi.fn(() => client());
    const attempt = connection.begin();
    const attaching = attempt.attach(create, "one", vi.fn());
    const stopping = connection.disconnect();
    leaving.resolve();
    await stopping;
    expect(await attaching).toBeNull();
    expect(create).not.toHaveBeenCalled();
  });

  it("late disconnect completion cannot mark a newer connection as disconnected", async () => {
    const previous = client(), next = client();
    const gate = deferred();
    previous.disconnect = vi.fn(() => gate.promise);
    const ref = { current: previous as typeof previous | null };
    const connection = new SessionConnection(ref);
    const leaving = connection.disconnect();
    expect(ref.current).toBeNull();
    const arriving = connection.begin().attach(() => next, "next", vi.fn());
    await Promise.resolve();
    next.gate.resolve();
    expect(await arriving).toBe(next);
    gate.resolve();
    expect(await leaving).toBe(false);
    expect(ref.current).toBe(next);
  });

  it("a current connection failure still reaches the caller", async () => {
    const source = client();
    const ref = { current: null as typeof source | null };
    const connection = new SessionConnection(ref);
    const attempt = connection.begin();
    const attaching = attempt.attach(() => source, "one", vi.fn());
    await Promise.resolve();
    source.gate.reject(new Error("authentication failed"));
    await expect(attaching).rejects.toThrow("authentication failed");
    expect(attempt.current()).toBe(true);
  });
});
