import React, { useEffect, useMemo, useState } from "react";

import ActionProgress from "./ActionProgress.jsx";
import {
  SORT_OPTIONS_KEY,
  describeChange,
  hasSortResult,
  modelNote,
  skippedLine,
  sortActionProgress,
  sortHeadline,
  sortSummary,
  sourceLabel,
  undoableCount,
} from "./aiSort.js";
import { loadStored, saveStored } from "./storage.js";

// How many rows a list shows before "Show all"; a first run over a big library
// can change hundreds of books.
const ROW_LIMIT = 40;

function loadOptions() {
  const stored = loadStored(SORT_OPTIONS_KEY, null, true);
  return {
    online: stored?.online === true,
    includeChecked: stored?.includeChecked === true,
    model: typeof stored?.model === "string" ? stored.model : "",
  };
}

function ChangeLine({ change }) {
  const { label, text } = describeChange(change);
  return (
    <li className="aisort-change">
      <span className="aisort-field">{label}</span>
      <span className="aisort-text">{text}</span>
      {change.source && <span className="aisort-source" title={change.reason}>{sourceLabel(change.source)}</span>}
    </li>
  );
}

function AppliedList({ items, onOpen }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, ROW_LIMIT);
  return (
    <>
      <ul className="aisort-list">
        {shown.map((item) => (
          <li key={item.id} className={`aisort-row${item.undone ? " undone" : ""}`}>
            <div className="aisort-row-head">
              <button type="button" className="aisort-book" onClick={() => onOpen({ document_id: item.document_id })}>{item.title}</button>
              {item.undone && <span className="aisort-tag">Put back</span>}
              {item.via === "review" && !item.undone && <span className="aisort-tag">You approved</span>}
            </div>
            <ul className="aisort-changes">
              {item.changes.map((change) => <ChangeLine key={`${change.field}:${change.after}`} change={change} />)}
            </ul>
          </li>
        ))}
      </ul>
      {items.length > ROW_LIMIT && !all && (
        <button type="button" className="text-button" onClick={() => setAll(true)}>Show all {items.length.toLocaleString()}</button>
      )}
    </>
  );
}

function ReviewList({ items, busy, onApply, onDismiss, onOpen }) {
  const [selected, setSelected] = useState(() => new Set());
  const [all, setAll] = useState(false);

  // A suggestion that was applied or dismissed is no longer there to be selected.
  useEffect(() => {
    setSelected((current) => {
      const ids = new Set(items.map((item) => item.id));
      const next = new Set([...current].filter((id) => ids.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [items]);

  const toggle = (id) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const everything = items.length > 0 && items.every((item) => selected.has(item.id));
  const shown = all ? items : items.slice(0, ROW_LIMIT);
  const chosen = [...selected];
  const working = busy === "apply" || busy === "dismiss";

  return (
    <>
      <div className="aisort-bulk">
        <label className="settings-check">
          <input
            type="checkbox"
            checked={everything}
            ref={(el) => { if (el) el.indeterminate = selected.size > 0 && !everything; }}
            onChange={(event) => setSelected(event.target.checked ? new Set(items.map((item) => item.id)) : new Set())}
          />
          <span>Select all</span>
        </label>
        <button type="button" className="button small" disabled={working || !chosen.length} onClick={() => onApply(chosen)}>
          {busy === "apply" ? "Applying…" : `Apply selected${chosen.length ? ` (${chosen.length})` : ""}`}
        </button>
        <button type="button" className="text-button" disabled={working || !chosen.length} onClick={() => onDismiss(chosen)}>
          Dismiss selected
        </button>
      </div>
      <ul className="aisort-list">
        {shown.map((item) => {
          const { label, text } = describeChange(item);
          return (
            <li key={item.id} className="aisort-row aisort-suggestion">
              <input type="checkbox" checked={selected.has(item.id)} onChange={() => toggle(item.id)} aria-label={`Select ${item.title}`} />
              <div className="aisort-suggestion-body">
                <button type="button" className="aisort-book" onClick={() => onOpen({ document_id: item.document_id })}>{item.title}</button>
                <div className="aisort-change">
                  <span className="aisort-field">{label}</span>
                  <span className="aisort-text">{text}</span>
                  <span className="aisort-source">{sourceLabel(item.source)}</span>
                </div>
                {item.reason && <div className="aisort-reason">{sourceLabel(item.reason)}.</div>}
              </div>
              <div className="aisort-row-actions">
                <button type="button" className="text-button" disabled={working} onClick={() => onApply([item.id])}>Apply</button>
                <button type="button" className="text-button" disabled={working} onClick={() => onDismiss([item.id])}>Dismiss</button>
              </div>
            </li>
          );
        })}
      </ul>
      {items.length > ROW_LIMIT && !all && (
        <button type="button" className="text-button" onClick={() => setAll(true)}>Show all {items.length.toLocaleString()}</button>
      )}
    </>
  );
}

// The Library's "AI sort files" panel. `sort` is the state from useAiSort.
export default function AiSortPanel({ apiBase, sort, onOpenDocument, onClose }) {
  const { report, busy, error, message, stopping, running } = sort;
  const [setup, setSetup] = useState(null);
  const [setupError, setSetupError] = useState(false);
  const [options, setOptions] = useState(loadOptions);
  const [reopened, setReopened] = useState(false);

  useEffect(() => saveStored(SORT_OPTIONS_KEY, options), [options]);

  // What this server can offer: the reader's own models, and whether looking
  // books up online is allowed at all (OFFLINE mode forbids it).
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiBase}/library/ai-sort/models`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("models"))))
      .then(setSetup)
      .catch((requestError) => { if (requestError.name !== "AbortError") setSetupError(true); });
    return () => controller.abort();
  }, [apiBase, running]);

  const models = setup?.models || [];
  const model = models.includes(options.model) ? options.model : setup?.default || "";
  const onlineAllowed = setup ? setup.online_allowed : true;
  const online = options.online && onlineAllowed;
  const sources = useMemo(
    () => (setup?.providers || []).map((name) => (name === "googlebooks" ? "Google Books" : "Open Library")).join(" and ") || "Open Library",
    [setup],
  );
  const progress = sortActionProgress(report);
  const result = hasSortResult(report);
  const suggestions = report?.suggestions || [];
  const applied = report?.applied || [];
  const canUndo = undoableCount(report) > 0 && !running;
  const skipped = skippedLine(report);
  const note = setup ? modelNote({ models, ollamaConfigured: setup.ollama_configured }) : "";

  // After a run the results come first; the options fold away behind "Sort again".
  const showOptions = !result || reopened;

  function start() {
    setReopened(false);
    sort.start({ online, model: model || null, include_checked: options.includeChecked });
  }

  return (
    <section className="aisort" id="ai-sort-panel" aria-label="AI sort files">
      <header className="aisort-head">
        <div>
          <h3>AI sort files</h3>
          <p>
            Goes through your books and fixes titles and authors. It settles what the rules are sure of, can look each book up in
            an online catalogue, and asks your own Ollama model which catalogue entry is the same book and who wrote it. Anything
            it is not sure of waits for you below. Books you edited yourself are never touched, and every change can be undone.
          </p>
        </div>
        <button type="button" className="text-button" onClick={onClose}>Close</button>
      </header>

      {error && <div className="notice error" role="alert">{error}</div>}
      {message && <div className="notice success" role="status">{message}</div>}
      {setupError && !setup && <div className="notice warn">Could not ask the server which models are available.</div>}

      {running ? (
        <div className="aisort-run">
          <ActionProgress progress={progress} />
          <button type="button" className="button" disabled={stopping || busy === "cancel"} onClick={sort.cancel}>
            {stopping ? "Stopping after this book…" : "Stop"}
          </button>
        </div>
      ) : showOptions && (
        <div className="aisort-options">
          <label className="settings-check">
            <input
              type="checkbox"
              checked={online}
              disabled={!onlineAllowed}
              onChange={(event) => setOptions((current) => ({ ...current, online: event.target.checked }))}
            />
            <span>Look books up in an online catalogue</span>
          </label>
          <p className="settings-note">
            {onlineAllowed
              ? `Sends each book's title and author to ${sources}. Without it, a book can only be corrected by the rules and your model, and a model alone is never enough to change an author.`
              : "Offline mode is on, so nothing is sent to an online catalogue."}
          </p>
          <label className="aisort-model">
            <span>Model</span>
            <select
              className="input"
              value={model}
              disabled={!models.length}
              onChange={(event) => setOptions((current) => ({ ...current, model: event.target.value }))}
            >
              {models.length === 0 && <option value="">None available</option>}
              {models.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <p className="settings-note">{note || "Runs on your own machine through Ollama; the book text never leaves it."}</p>
          <label className="settings-check">
            <input
              type="checkbox"
              checked={options.includeChecked}
              onChange={(event) => setOptions((current) => ({ ...current, includeChecked: event.target.checked }))}
            />
            <span>Check books again that an earlier sort already looked at</span>
          </label>
          <div className="aisort-start">
            <button type="button" className="button primary" disabled={busy === "start"} onClick={start}>
              {busy === "start" ? "Starting…" : "Start sort"}
            </button>
          </div>
        </div>
      )}

      {result && (
        <div className="aisort-result">
          <div className="aisort-result-head">
            <div>
              <strong>{sortHeadline(report)}</strong>
              <p>{sortSummary(report)}</p>
              {skipped && <p className="aisort-skipped">Left out: {skipped}.</p>}
              {report.state === "interrupted" && <p className="aisort-skipped">The server restarted partway through. What it had done is kept.</p>}
            </div>
            <div className="aisort-result-actions">
              {!reopened && (
                <button type="button" className="button" onClick={() => setReopened(true)}>Sort again</button>
              )}
              {canUndo && (
                <button type="button" className="button" disabled={busy === "undo"} onClick={sort.undo} title="Put every book this sort changed back as it was, unless you have changed it since.">
                  {busy === "undo" ? "Undoing…" : `Undo ${undoableCount(report)} ${undoableCount(report) === 1 ? "change" : "changes"}`}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {suggestions.length > 0 && (
        <section className="aisort-section" aria-label="To review">
          <h4>To review <span className="tab-count">{suggestions.length}</span></h4>
          <p className="settings-note">One source on its own was not enough to change these. Apply the ones that look right.</p>
          <ReviewList items={suggestions} busy={busy} onApply={sort.apply} onDismiss={sort.dismiss} onOpen={onOpenDocument} />
        </section>
      )}

      {applied.length > 0 && (
        <section className="aisort-section" aria-label="Changed">
          <h4>Changed <span className="tab-count">{applied.length}</span></h4>
          <AppliedList items={applied} onOpen={onOpenDocument} />
        </section>
      )}

      {(report?.notes || []).length > 0 && (
        <details className="aisort-notes">
          <summary>Notes from the sort ({report.notes.length})</summary>
          <ul>{report.notes.map((text, index) => <li key={index}>{text}</li>)}</ul>
        </details>
      )}
    </section>
  );
}
