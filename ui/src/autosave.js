// Saves edits as they happen, and again for certain when the reader closes.
//
// `save` does the actual write (and may be slow). Edits call markDirty(); a
// save runs `delay` ms after the last one, so a burst of strokes is one write.
// An edit made while a save is in flight is saved straight after it. flush()
// is what closing waits on: it cancels the wait, lets any save in flight
// finish, saves what is still unsaved, and rejects if that fails, so the
// reader is told instead of silently losing the change.
//
// A save that fails is tried again by itself after each of `retryDelays` (the
// last one repeats until it works), unless `canRetry(error)` says the failure
// will not go away on its own (the file is refused, or was changed elsewhere).
// Coming back online can cut the wait short with retryNow().
//
// State for the UI: "saved" | "unsaved" | "saving" | "retrying" | "error".
export function createAutosaver({ save, delay = 1500, retryDelays = [], canRetry = () => true, onState = () => {} }) {
  let timer = null;
  let dirty = false;
  let running = null;
  let state = "saved";
  let failures = 0;
  const setState = (next) => {
    state = next;
    onState(next);
  };

  function run() {
    if (running) return running;
    running = (async () => {
      try {
        while (dirty) {
          dirty = false;
          setState("saving");
          try {
            await save();
            failures = 0;
          } catch (error) {
            dirty = true;
            failures += 1;
            if (retryDelays.length && canRetry(error)) {
              clearTimeout(timer);
              timer = setTimeout(() => run().catch(() => {}), retryDelays[Math.min(failures, retryDelays.length) - 1]);
              setState("retrying");
            } else {
              setState("error");
            }
            throw error;
          }
        }
        setState("saved");
      } finally {
        running = null;
      }
    })();
    return running;
  }

  return {
    markDirty() {
      dirty = true;
      if (state !== "saving") setState("unsaved");
      clearTimeout(timer);
      timer = setTimeout(() => run().catch(() => {}), delay);
    },
    async flush() {
      clearTimeout(timer);
      if (running) await running.catch(() => {});
      if (dirty) await run();
    },
    // Try again now rather than at the end of the wait (a no-op unless a retry is pending).
    retryNow() {
      if (state !== "retrying") return;
      clearTimeout(timer);
      run().catch(() => {});
    },
    // Forget what is unsaved, for when the reader chose to throw it away.
    discard() {
      clearTimeout(timer);
      dirty = false;
      failures = 0;
      if (!running) setState("saved");
    },
    hasUnsaved: () => dirty || running !== null,
    state: () => state,
    cancel: () => clearTimeout(timer),
  };
}
