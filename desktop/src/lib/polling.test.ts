import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { startPolling } from "./polling";

class Visibility extends EventTarget {
  hidden = false;
  setHidden(hidden: boolean) {
    this.hidden = hidden;
    this.dispatchEvent(new Event("visibilitychange"));
  }
}

const stops: Array<() => void> = [];
beforeEach(() => vi.useFakeTimers());
afterEach(() => { stops.splice(0).forEach(stop => stop()); vi.useRealTimers(); });
function poll(read: () => Promise<boolean | void>, visibility = new Visibility()) {
  const stop = startPolling(read, { intervalMs: 100, hiddenIntervalMs: 1_000, maxBackoffMs: 400, visibility });
  stops.push(stop);
  return { stop, visibility };
}

it("waits for a slow request to finish before scheduling another, including foreground changes", async () => {
  let resolve!: () => void;
  const read = vi.fn().mockImplementationOnce(() => new Promise<void>(done => { resolve = done; }))
    .mockResolvedValue(true);
  const { visibility } = poll(read);
  await vi.advanceTimersByTimeAsync(100);
  visibility.setHidden(true); visibility.setHidden(false);
  await vi.advanceTimersByTimeAsync(5_000);
  expect(read).toHaveBeenCalledTimes(1);
  resolve();
  await vi.advanceTimersByTimeAsync(99);
  expect(read).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(read).toHaveBeenCalledTimes(2);
});

it("slows down hidden windows and refreshes immediately on return", async () => {
  const read = vi.fn().mockResolvedValue(true);
  const visibility = new Visibility(); visibility.hidden = true;
  poll(read, visibility);
  await vi.advanceTimersByTimeAsync(999);
  expect(read).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(read).toHaveBeenCalledTimes(1);
  visibility.setHidden(false);
  await vi.advanceTimersByTimeAsync(0);
  expect(read).toHaveBeenCalledTimes(2);
  visibility.setHidden(true);
  await vi.advanceTimersByTimeAsync(999);
  expect(read).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(read).toHaveBeenCalledTimes(3);
});

it("backs off failures up to a bound and resets the delay after success", async () => {
  const read = vi.fn().mockResolvedValueOnce(false).mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(false).mockResolvedValue(true);
  poll(read);
  await vi.advanceTimersByTimeAsync(100); // failed; next in 200
  await vi.advanceTimersByTimeAsync(199);
  expect(read).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1); // error; next in 400
  await vi.advanceTimersByTimeAsync(400); // failed; stays at 400
  expect(read).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(399);
  expect(read).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(1); // success; next in 100
  expect(read).toHaveBeenCalledTimes(4);
  await vi.advanceTimersByTimeAsync(100);
  expect(read).toHaveBeenCalledTimes(5);
});

it("stopping during a read prevents future polling and removes the visibility listener", async () => {
  let resolve!: () => void;
  const read = vi.fn(() => new Promise<void>(done => { resolve = done; }));
  const { stop, visibility } = poll(read);
  await vi.advanceTimersByTimeAsync(100);
  stop(); resolve();
  visibility.setHidden(true); visibility.setHidden(false);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(read).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it("clears a scheduled retry when stopped", async () => {
  const read = vi.fn().mockRejectedValue(new Error("offline"));
  const { stop } = poll(read);
  await vi.advanceTimersByTimeAsync(100);
  stop();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(read).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it("can do the first read immediately without overlapping the next scheduled read", async () => {
  const read = vi.fn().mockResolvedValue(true);
  const stop = startPolling(read, { intervalMs: 100, hiddenIntervalMs: 1_000, immediate: true, visibility: new Visibility() });
  stops.push(stop);
  await vi.advanceTimersByTimeAsync(0);
  expect(read).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(99);
  expect(read).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(read).toHaveBeenCalledTimes(2);
});
