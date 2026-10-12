import React, { useEffect, useState } from "react";

import ActionProgress from "./ActionProgress.jsx";
import {
  CHECK_OPTIONS_KEY,
  appliedCount,
  checkNotes,
  checkSummary,
  describeChange,
  modelNote,
  parseOptions,
  sourceLabel,
} from "./aiCheck.js";
import { apiError } from "./intake.js";
import { loadStored, saveStored } from "./storage.js";

// "Check with AI" on a book's page: the rules, your own model and (if you allow it) an
// online catalogue look at this one book and say what they would change. Nothing is
// written until you apply it, and an applied change can be undone.
export default function AiCheck({ apiBase, doc, locked, onDocument }) {
  const id = doc.document_id;
  const [status, setStatus] = useState(null);
  const [setup, setSetup] = useState(null);
  const [options, setOptions] = useState(() => parseOptions(loadStored(CHECK_OPTIONS_KEY, null, true)));
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => saveStored(CHECK_OPTIONS_KEY, options), [options]);

  useEffect(() => {
    const controller = new AbortController();
    setStatus(null);
    setError("");
    setMessage("");
    fetch(`${apiBase}/documents/${id}/ai-check`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("status"))))
      .then(setStatus)
      .catch((failure) => { if (failure.name !== "AbortError") setStatus({ reason: "The check is not available right now.", result: null }); });
    return () => controller.abort();
  }, [apiBase, id]);

  // Which models and whether the online catalogue is allowed; only asked once it matters.
  const eligible = Boolean(status) && !status.reason;
  useEffect(() => {
    if (!eligible || setup) return undefined;
    const controller = new AbortController();
    fetch(`${apiBase}/library/ai-sort/models`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("models"))))
      .then(setSetup)
      .catch(() => {});
    return () => controller.abort();
  }, [apiBase, eligible, setup]);

  const models = setup?.models || [];
  const model = models.includes(options.model) ? options.model : setup?.default || "";
  const onlineAllowed = setup ? setup.online_allowed : true;
  const online = options.online && onlineAllowed;
  const sources = (setup?.providers || []).map((name) => (name === "googlebooks" ? "Google Books" : "Open Library")).join(" and ") || "Open Library";

  async function post(path, body) {
    const response = await fetch(`${apiBase}/documents/${id}/ai-check${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(apiError(data, response.status));
    return data;
  }

  async function run(label, action) {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }

  const check = () => run("check", async () => {
    const data = await post("", { online, model: model || null });
    setStatus(data);
  });

  const act = (verb, ids) => run(verb, async () => {
    const data = await post(`/${verb}`, ids ? { ids } : undefined);
    setStatus((current) => ({ ...current, result: data.result }));
    if (data.document) onDocument(data.document);
    if (verb === "apply") {
      setMessage(`Applied ${data.applied} ${data.applied === 1 ? "change" : "changes"}.`);
      if (data.failed?.length) setError(`Could not apply: ${data.failed.join("; ")}`);
    } else if (verb === "dismiss") {
      setMessage(`Dismissed ${data.dismissed}.`);
    } else {
      setMessage(`Undid ${data.restored} ${data.restored === 1 ? "change" : "changes"}.${data.skipped?.length ? ` Left alone: ${data.skipped.join("; ")}.` : ""}`);
    }
  });

  if (!status) return null;
  const result = status.result;
  const suggestions = result?.suggestions || [];
  const applied = appliedCount(result);
  const working = Boolean(busy);

  return (
    <section className="book-editor-card aicheck" aria-labelledby="aicheck-heading">
      <div className="book-card-heading">
        <h2 id="aicheck-heading">Check with AI</h2>
        <span className="muted">Nothing changes until you apply it</span>
      </div>

      {status.reason ? (
        <p className="muted">{status.reason}</p>
      ) : (
        <>
          <p className="settings-note">
            Reads this book's opening pages and checks its title, author and details against what the book itself says.
            Your own model does the reading; you decide what to keep.
          </p>
          <div className="aicheck-options">
            <label className="aicheck-model">
              <span>Model</span>
              <select className="input" value={model} disabled={!models.length || working} onChange={(event) => setOptions((current) => ({ ...current, model: event.target.value }))}>
                {!models.length && <option value="">None available</option>}
                {models.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </label>
            <label className="settings-check">
              <input type="checkbox" checked={online} disabled={!onlineAllowed || working} onChange={(event) => setOptions((current) => ({ ...current, online: event.target.checked }))} />
              <span>Also look it up online</span>
            </label>
          </div>
          <p className="settings-note">
            {onlineAllowed ? `Looking it up online sends this book's title and author to ${sources}.` : "Offline mode is on, so nothing is sent to an online catalogue."}
            {setup && !models.length ? ` ${modelNote({ models, ollamaConfigured: setup.ollama_configured })}` : ""}
          </p>
          <div className="aicheck-run">
            <button type="button" className="button" disabled={working || (setup && !models.length && !online)} onClick={check}>
              {busy === "check" ? "Checking…" : result ? "Check again" : "Check this book"}
            </button>
          </div>
        </>
      )}

      {busy === "check" && (
        <ActionProgress progress={{ label: "Checking…", detail: model ? `Asking ${model}. This can take a minute.` : "Looking it up…", percent: null }} />
      )}
      {error && <p className="book-inline-error" role="alert">{error}</p>}
      {message && <p className="book-inline-success" role="status">{message}</p>}

      {result && (
        <div className="aicheck-result">
          <p className="aicheck-summary">{checkSummary(result)}</p>
          {suggestions.length > 0 && locked && (
            <p className="book-inline-error" role="alert">Save or discard your unsaved edits first, so applying a change cannot overwrite them.</p>
          )}
          {suggestions.length > 0 && (
            <>
              <div className="aicheck-bulk">
                <button type="button" className="button small" disabled={working || locked} onClick={() => act("apply", suggestions.map((item) => item.id))}>
                  {busy === "apply" ? "Applying…" : `Apply all (${suggestions.length})`}
                </button>
                <button type="button" className="text-button" disabled={working} onClick={() => act("dismiss", suggestions.map((item) => item.id))}>Dismiss all</button>
              </div>
              <ul className="aicheck-list">
                {suggestions.map((item) => {
                  const { label, text } = describeChange(item);
                  return (
                    <li key={item.id} className="aicheck-row">
                      <div className="aicheck-change">
                        <span className="aicheck-field">{label}</span>
                        <span className="aicheck-text">{text}</span>
                      </div>
                      <div className="aicheck-why">{[sourceLabel(item.source), sourceLabel(item.reason)].filter(Boolean).join(" · ")}</div>
                      <div className="aicheck-actions">
                        <button type="button" className="text-button" disabled={working || locked} onClick={() => act("apply", [item.id])}>Apply</button>
                        <button type="button" className="text-button" disabled={working} onClick={() => act("dismiss", [item.id])}>Dismiss</button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          {applied > 0 && (
            <p className="aicheck-applied">
              {applied} {applied === 1 ? "change" : "changes"} applied from this check.{" "}
              <button type="button" className="text-button" disabled={working || locked} onClick={() => act("undo")}>
                {busy === "undo" ? "Undoing…" : "Undo"}
              </button>
            </p>
          )}
          {checkNotes(result).length > 0 && (
            <ul className="aicheck-notes">{checkNotes(result).map((note) => <li key={note}>{note}</li>)}</ul>
          )}
        </div>
      )}
    </section>
  );
}
