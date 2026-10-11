import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAutosaver } from "./autosave.js";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

describe("autosave", () => {
  it("turns a burst of edits into one save after the last one", async () => {
    const save = vi.fn().mockResolvedValue();
    const states = [];
    const saver = createAutosaver({ save, delay: 1000, onState: (s) => states.push(s) });
    saver.markDirty();
    await vi.advanceTimersByTimeAsync(600);
    saver.markDirty();
    await vi.advanceTimersByTimeAsync(600);
    expect(save).not.toHaveBeenCalled(); // the wait restarted with the second edit
    await vi.advanceTimersByTimeAsync(500);
    expect(save).toHaveBeenCalledTimes(1);
    expect(states).toEqual(["unsaved", "unsaved", "saving", "saved"]);
    expect(saver.hasUnsaved()).toBe(false);
  });

  it("saves at once when closing, without waiting out the delay", async () => {
    const save = vi.fn().mockResolvedValue();
    const saver = createAutosaver({ save, delay: 60_000 });
    saver.markDirty();
    expect(saver.hasUnsaved()).toBe(true);
    await saver.flush();
    expect(save).toHaveBeenCalledTimes(1);
    expect(saver.hasUnsaved()).toBe(false);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(save).toHaveBeenCalledTimes(1); // the cancelled timer does not save again
  });

  it("does nothing when there is nothing to save", async () => {
    const save = vi.fn();
    await createAutosaver({ save }).flush();
    expect(save).not.toHaveBeenCalled();
  });

  it("waits for a save in flight, then saves an edit made during it", async () => {
    const first = deferred();
    const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue();
    const saver = createAutosaver({ save, delay: 100 });
    saver.markDirty();
    await vi.advanceTimersByTimeAsync(100);
    expect(save).toHaveBeenCalledTimes(1);
    saver.markDirty(); // an edit while the first write is still running
    expect(saver.state()).toBe("saving");
    let closed = false;
    const closing = saver.flush().then(() => { closed = true; });
    await vi.advanceTimersByTimeAsync(0);
    expect(closed).toBe(false); // closing waits
    first.resolve();
    await closing;
    expect(save).toHaveBeenCalledTimes(2); // and the late edit was saved too
    expect(saver.hasUnsaved()).toBe(false);
  });

  it("keeps the edits and tells the reader when a save fails, and retries", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue();
    const saver = createAutosaver({ save, delay: 100 });
    saver.markDirty();
    await expect(saver.flush()).rejects.toThrow("offline");
    expect(saver.state()).toBe("error");
    expect(saver.hasUnsaved()).toBe(true); // still unsaved: closing must not pretend otherwise
    await saver.flush();
    expect(saver.state()).toBe("saved");
    expect(save).toHaveBeenCalledTimes(2);
  });
});
