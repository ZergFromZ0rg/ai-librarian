import { useCallback, useEffect, useRef, useState } from "react";

import { isSortRunning } from "./aiSort.js";

const POLL_MS = 1500;
// The server accepts at most this many suggestions in one apply or dismiss.
const BATCH = 500;

// The last AI sort run, as the server keeps it, plus the things the panel can do
// with it. The run itself lives on the server (it carries on if the page is
// closed), so this only reads the report back, and polls while one is going.
export default function useAiSort(apiBase, onChanged) {
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [stopping, setStopping] = useState(false);
  const wasRunning = useRef(false);

  const refresh = useCallback(async (signal) => {
    try {
      const response = await fetch(`${apiBase}/library/ai-sort`, { signal });
      if (!response.ok) throw new Error(`AI sort request failed (${response.status})`);
      setReport(await response.json());
    } catch (requestError) {
      if (requestError.name !== "AbortError") setError(requestError.message || "Could not load the AI sort.");
    }
  }, [apiBase]);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const running = isSortRunning(report);
  useEffect(() => {
    if (!running) return undefined;
    const controller = new AbortController();
    const interval = window.setInterval(() => refresh(controller.signal), POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [running, refresh]);

  // A run that ends has changed books the page already loaded, so read them again.
  useEffect(() => {
    if (wasRunning.current && !running) {
      setStopping(false);
      onChanged?.();
    }
    wasRunning.current = running;
  }, [running]);

  const call = useCallback(async (path, body, label) => {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${apiBase}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
      return data;
    } catch (requestError) {
      setError(requestError.message || "That did not work.");
      return null;
    } finally {
      setBusy("");
    }
  }, [apiBase]);

  const start = useCallback(async (options) => {
    const data = await call("/library/ai-sort", options, "start");
    if (data) {
      setStopping(false);
      setReport(data);
    } else {
      await refresh(); // e.g. a run started from another tab: show it
    }
  }, [call, refresh]);

  const cancel = useCallback(async () => {
    const data = await call("/library/ai-sort/cancel", null, "cancel");
    if (data?.cancelling) setStopping(true);
    await refresh();
  }, [call, refresh]);

  // Apply or dismiss suggestions, in as many requests as the server allows.
  const review = useCallback(async (verb, ids) => {
    let done = 0;
    const failed = [];
    for (let at = 0; at < ids.length; at += BATCH) {
      const data = await call(`/library/ai-sort/${verb}`, { ids: ids.slice(at, at + BATCH) }, verb);
      if (!data) break;
      done += (verb === "apply" ? data.applied : data.dismissed) || 0;
      failed.push(...(data.failed || []));
    }
    if (done) {
      const noun = done === 1 ? "suggestion" : "suggestions";
      setMessage(verb === "apply" ? `Applied ${done} ${noun}.` : `Dismissed ${done} ${noun}.`);
    }
    if (failed.length) setError(`Could not apply: ${failed.join("; ")}`);
    await refresh();
    if (verb === "apply" && done) onChanged?.();
  }, [call, refresh, onChanged]);

  const undo = useCallback(async () => {
    const data = await call("/library/ai-sort/undo", null, "undo");
    if (data) {
      const noun = data.restored === 1 ? "change" : "changes";
      setMessage(`Undid ${data.restored} ${noun}.${data.skipped?.length ? ` Left alone: ${data.skipped.join("; ")}.` : ""}`);
    }
    await refresh();
    onChanged?.();
  }, [call, refresh, onChanged]);

  return {
    report, busy, error, message, stopping, running,
    start, cancel, undo,
    apply: (ids) => review("apply", ids),
    dismiss: (ids) => review("dismiss", ids),
  };
}
