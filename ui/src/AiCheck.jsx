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
import { apiError, applyAiPatch, isBookType, payloadFromForm } from "./intake.js";
import { loadStored, saveStored } from "./storage.js";

// The reader's own model, and whether an online catalogue may be asked, as this server offers
// them; the choices are remembered per browser. Only asked once a check is possible.
function useCheckSetup(apiBase, enabled) {
  const [setup, setSetup] = useState(null);
  const [options, setOptions] = useState(() => parseOptions(loadStored(CHECK_OPTIONS_KEY, null, true)));

  useEffect(() => saveStored(CHECK_OPTIONS_KEY, options), [options]);

  useEffect(() => {
    if (!enabled || setup) return undefined;
    const controller = new AbortController();
    fetch(`${apiBase}/ai-check/models`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("models"))))
      .then(setSetup)
      .catch(() => {});
    return () => controller.abort();
  }, [apiBase, enabled, setup]);

  const models = setup?.models || [];
  const onlineAllowed = setup ? setup.online_allowed : true;
  return {
    setup, options, setOptions, models, onlineAllowed,
    model: models.includes(options.model) ? options.model : setup?.default || "",
    online: options.online && onlineAllowed,
    sources: (setup?.providers || []).map((name) => (name === "googlebooks" ? "Google Books" : "Open Library")).join(" and ") || "Open Library",
  };
}

async function postJson(url, body) {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(apiError(data, response.status));
  return data;
}

// One request at a time, with its error shown in the card.
function useBusy() {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
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
  return { busy, error, message, setError, setMessage, run };
}

// The card itself: how to check, what the check found, and what to do with it.
function CheckCard({ check, reason, result, busy, error, message, locked, lockedText, intro, onCheck, onApply, onDismiss, onUndo }) {
  const { setup, options, setOptions, models, onlineAllowed, model, online, sources } = check;
  const suggestions = result?.suggestions || [];
  const applied = onUndo ? appliedCount(result) : 0;
  const working = Boolean(busy);

  return (
    <section className="book-editor-card aicheck" aria-labelledby="aicheck-heading">
      <div className="book-card-heading">
        <h2 id="aicheck-heading">Check with AI</h2>
        <span className="muted">Nothing changes until you apply it</span>
      </div>

      {reason ? (
        <p className="muted">{reason}</p>
      ) : (
        <>
          <p className="settings-note">{intro}</p>
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
            <button type="button" className="button" disabled={working || (setup && !models.length && !online)} onClick={onCheck}>
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
          {suggestions.length > 0 && locked && <p className="book-inline-error" role="alert">{lockedText}</p>}
          {suggestions.length > 0 && (
            <>
              <div className="aicheck-bulk">
                <button type="button" className="button small" disabled={working || locked} onClick={() => onApply(suggestions)}>
                  {busy === "apply" ? "Applying…" : `Apply all (${suggestions.length})`}
                </button>
                <button type="button" className="text-button" disabled={working} onClick={() => onDismiss(suggestions)}>Dismiss all</button>
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
                        <button type="button" className="text-button" disabled={working || locked} onClick={() => onApply([item])}>Apply</button>
                        <button type="button" className="text-button" disabled={working} onClick={() => onDismiss([item])}>Dismiss</button>
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
              <button type="button" className="text-button" disabled={working || locked} onClick={onUndo}>
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

// On a book's page: the check of a library book. Its result is kept by the server, applied
// changes can be undone, and nothing is applied over unsaved edits in the book form.
export default function AiCheck({ apiBase, doc, locked, onDocument }) {
  const id = doc.document_id;
  const [status, setStatus] = useState(null);
  const { busy, error, message, setError, setMessage, run } = useBusy();

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

  const check = useCheckSetup(apiBase, Boolean(status) && !status.reason);
  const url = `${apiBase}/documents/${id}/ai-check`;

  const act = (verb, ids) => run(verb, async () => {
    const data = await postJson(`${url}/${verb}`, ids ? { ids } : undefined);
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
  return (
    <CheckCard
      check={check}
      reason={status.reason}
      result={status.result}
      busy={busy}
      error={error}
      message={message}
      locked={locked}
      lockedText="Save or discard your unsaved edits first, so applying a change cannot overwrite them."
      intro="Reads this book's opening pages and checks its title, author and details against what the book itself says. Your own model does the reading; you decide what to keep."
      onCheck={() => run("check", async () => setStatus(await postJson(url, { online: check.online, model: check.model || null })))}
      onApply={(items) => act("apply", items.map((item) => item.id))}
      onDismiss={(items) => act("dismiss", items.map((item) => item.id))}
      onUndo={() => act("undo")}
    />
  );
}

// On the "Add to library" screen: the check of an upload that is not in the library yet. It works
// from what the form shows now, and applying a suggestion fills the form (nothing is recorded, so
// there is nothing to undo; the reader can still change any field before saving).
export function StagedAiCheck({ apiBase, item, form, types, result, onResult, onForm }) {
  const eligible = ["pdf", "epub"].includes(item.format) && item.state === "ready" && isBookType(form.type);
  const check = useCheckSetup(apiBase, eligible);
  const { busy, error, message, setMessage, run } = useBusy();
  if (!eligible) return null;

  const leave = (items, text) => {
    const gone = new Set(items.map((entry) => entry.id));
    onResult({ ...result, suggestions: result.suggestions.filter((entry) => !gone.has(entry.id)) });
    setMessage(text);
  };

  return (
    <CheckCard
      check={check}
      reason={null}
      result={result}
      busy={busy}
      error={error}
      message={message}
      locked={false}
      intro="Reads this file's opening pages and checks the title, author and details against what the book itself says. Suggestions fill in the form above; you can still change anything before you save."
      onCheck={() => run("check", async () => {
        const data = await postJson(`${apiBase}/intake/${item.id}/ai-check`, { ...payloadFromForm(form), online: check.online, model: check.model || null });
        onResult(data.result);
      })}
      onApply={(items) => {
        onForm(items.reduce((next, entry) => applyAiPatch(next, entry.patch, types), form));
        leave(items, `Filled in ${items.length} ${items.length === 1 ? "change" : "changes"} above.`);
      }}
      onDismiss={(items) => leave(items, `Dismissed ${items.length}.`)}
    />
  );
}
