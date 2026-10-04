import React, { useCallback, useEffect, useMemo, useState } from "react";

import {
  ACTIVITY_FILTERS,
  filterActivity,
  formatBytes,
  groupActivity,
  itemFacts,
  relativeTime,
  stageLabel,
} from "./indexActivity.js";
import { displayTitle } from "./storage.js";

const GROUPS = [
  ["processing", "Processing now"],
  ["queued", "Up next"],
  ["attention", "Needs attention"],
  ["finished", "Finished"],
];

function ActivitySummary({ summary }) {
  const stats = [
    ["Processing", summary.indexing || 0, "active"],
    ["Queued", summary.queued || 0, "queued"],
    ["Finished", summary.indexed || 0, "finished"],
    ["Needs attention", summary.error || 0, "attention"],
  ];
  return (
    <div className="activity-summary" aria-label="Indexing queue summary">
      <div className="activity-summary-main">
        <span className="activity-pulse" aria-hidden="true" />
        <div>
          <strong>{summary.active ? `${summary.active} books in the pipeline` : "Indexing is caught up"}</strong>
          <span>{summary.active ? `${formatBytes(summary.active_bytes)} waiting or in progress` : "Every finished book is ready to search"}</span>
        </div>
      </div>
      <div className="activity-summary-stats">
        {stats.map(([label, value, tone]) => (
          <span className={`activity-stat ${tone}`} key={label}><strong>{value.toLocaleString()}</strong>{label}</span>
        ))}
      </div>
    </div>
  );
}

function ActivityRow({ item, busy, onOpen, onRetry, onApproveOcr }) {
  const percent = Number.isFinite(item.progress) ? Math.max(0, Math.min(100, item.progress)) : null;
  const active = ["indexing", "queued"].includes(item.indexing_status);
  const finishedTime = relativeTime(item.indexed_at || item.updated_at);
  const activityTime = relativeTime(item.started_at || item.updated_at || item.uploaded_at);
  const source = item.source_path || item.filename;
  const title = item.title || displayTitle(item.filename);
  return (
    <article className={`activity-row activity-${item.indexing_status}`}>
      <div className="activity-format" aria-hidden="true">{(item.file_type || "doc").slice(0, 4).toUpperCase()}</div>
      <div className="activity-book">
        <div className="activity-book-line">
          <strong title={title}>{title}</strong>
          <span className={`activity-stage stage-${item.stage}`}>{stageLabel(item)}</span>
        </div>
        {item.author && <div className="activity-author">{item.author}</div>}
        <div className="activity-source" title={source}>{source}</div>
        <div className="activity-facts">
          {itemFacts(item).map((fact) => <span key={fact}>{fact}</span>)}
          {item.queue_position && <span>Queue position {item.queue_position}</span>}
          {item.indexing_status === "indexing" && activityTime && <span>Started {activityTime}</span>}
          {item.indexing_status === "queued" && activityTime && <span>Queued {activityTime}</span>}
          {!active && finishedTime && <span>{item.indexing_status === "indexed" ? "Finished" : "Updated"} {finishedTime}</span>}
        </div>
        {(active || item.indexing_status === "indexed") && (
          <div
            className={`activity-progress${percent === null ? " indeterminate" : ""}`}
            role="progressbar"
            aria-label={`${title}: ${stageLabel(item)}`}
            aria-valuemin={0}
            aria-valuemax={100}
            {...(percent === null ? {} : { "aria-valuenow": percent })}
          >
            <span style={percent === null ? undefined : { width: `${percent}%` }} />
          </div>
        )}
        {item.indexing_error && <p className="activity-error-message">{item.indexing_error}</p>}
      </div>
      <div className="activity-row-side">
        {percent !== null && active && <strong className="activity-percent">{percent}%</strong>}
        <div className="activity-actions">
          {item.indexing_status === "indexed" && <button type="button" className="text-button" onClick={() => onOpen(item)}>Open</button>}
          {item.stage === "awaiting_ocr" && <button type="button" className="button small primary" disabled={busy} onClick={() => onApproveOcr(item)}>{busy ? "Starting…" : "Approve OCR"}</button>}
          {item.indexing_status === "error" && item.stage !== "awaiting_ocr" && <button type="button" className="button small" disabled={busy} onClick={() => onRetry(item)}>{busy ? "Queueing…" : "Retry"}</button>}
        </div>
      </div>
    </article>
  );
}

export default function IndexActivity({ apiBase, onOpenDocument, onRetry, onApproveOcr, onChanged }) {
  const [payload, setPayload] = useState({ items: [], summary: {} });
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const refresh = useCallback(async (signal) => {
    try {
      const response = await fetch(`${apiBase}/index-activity`, { signal });
      if (!response.ok) throw new Error(`Activity request failed (${response.status})`);
      setPayload(await response.json());
      setError("");
    } catch (requestError) {
      if (requestError.name !== "AbortError") setError(requestError.message || "Could not load indexing activity.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    const interval = window.setInterval(() => refresh(controller.signal), 2000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [refresh]);

  async function run(item, action) {
    setBusyId(item.document_id);
    try {
      await action();
      onChanged?.();
      await refresh();
    } finally {
      setBusyId("");
    }
  }

  const filtered = useMemo(() => filterActivity(payload.items || [], filter), [payload.items, filter]);
  const groups = useMemo(() => groupActivity(filtered), [filtered]);

  return (
    <section className="activity-dashboard" aria-label="Indexing activity">
      <ActivitySummary summary={payload.summary || {}} />
      <div className="activity-toolbar">
        <div className="segmented small" role="radiogroup" aria-label="Filter indexing activity">
          {ACTIVITY_FILTERS.map(([value, label]) => (
            <button key={value} type="button" role="radio" aria-checked={filter === value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{label}</button>
          ))}
        </div>
        <span className="muted">Live worker status · refreshes automatically</span>
      </div>
      {error && <div className="notice error">{error} <button type="button" className="text-button" onClick={() => refresh()}>Retry</button></div>}
      {loading && !payload.items.length && <p className="muted">Loading indexing activity…</p>}
      {!loading && !filtered.length && <div className="empty"><strong>Nothing in this view</strong><p>Choose another filter to see the rest of the library.</p></div>}
      {GROUPS.map(([key, label]) => groups[key].length > 0 && (
        <section className={`activity-group group-${key}`} key={key}>
          <header><h3>{label}</h3><span>{groups[key].length.toLocaleString()}</span></header>
          <div className="activity-list">
            {groups[key].map((item) => (
              <ActivityRow
                key={item.document_id}
                item={item}
                busy={busyId === item.document_id}
                onOpen={onOpenDocument}
                onRetry={(target) => run(target, () => onRetry([target.document_id]))}
                onApproveOcr={(target) => run(target, () => onApproveOcr(target))}
              />
            ))}
          </div>
        </section>
      ))}
    </section>
  );
}
