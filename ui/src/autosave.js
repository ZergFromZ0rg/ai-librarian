// Saves edits as they happen, and again for certain when the reader closes.
//
// `save` does the actual write (and may be slow). Edits call markDirty(); a
// save runs `delay` ms after the last one, so a burst of strokes is one write.
// An edit made while a save is in flight is saved straight after it. flush()
// is what closing waits on: it cancels the wait, lets any save in flight
// finish, saves what is still unsaved, and rejects if that fails, so the
// reader is told instead of silently losing the change.
//
// State for the UI: "saved" | "unsaved" | "saving" | "error".
export function createAutosaver({ save, delay = 1500, onState = () => {} }) {
  let timer = null;
  let dirty = false;
  let running = null;
  let state = "saved";
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
          } catch (error) {
            dirty = true;
            setState("error");
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
    hasUnsaved: () => dirty || running !== null,
    state: () => state,
    cancel: () => clearTimeout(timer),
  };
}
