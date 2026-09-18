import React, { useEffect, useMemo, useState } from "react";

import Cover from "./Cover.jsx";
import ResultCard from "./ResultCard.jsx";
import { displayTitle } from "./storage.js";

// Search findings: the ranked passages, plus a side rail that shows which
// documents they came from (and filters to one) and a record of the query as
// it was run — so a result set can be read, and reproduced, as evidence.
export default function SearchResults({ apiBase, run, onViewSource, onScope }) {
  const [onlyDoc, setOnlyDoc] = useState(null);

  useEffect(() => setOnlyDoc(null), [run]);

  const sources = useMemo(() => {
    const byDoc = new Map();
    for (const result of run?.results || []) {
      const entry = byDoc.get(result.document_id) || { id: result.document_id, name: result.document, count: 0, best: -Infinity };
      entry.count += 1;
      entry.best = Math.max(entry.best, result.rerank_score ?? -Infinity);
      byDoc.set(result.document_id, entry);
    }
    return [...byDoc.values()];
  }, [run]);

  if (!run) return null;

  if (run.status === "running") {
    return (
      <div className="workspace-status">
        <span className="spinner" aria-hidden="true" /> Searching…
      </div>
    );
  }

  if (run.status === "error") {
    return <div className="notice error">{run.error}</div>;
  }

  const { results, params } = run;
  const visible = onlyDoc ? results.filter((result) => result.document_id === onlyDoc) : results;

  return (
    <div className="findings-layout">
      <div className="findings">
        <div className="findings-head">
          <h2>
            {results.length ? (
              <>
                {results.length} passage{results.length === 1 ? "" : "s"}
                <span className="muted"> from {sources.length} document{sources.length === 1 ? "" : "s"}</span>
              </>
            ) : (
              "No passages cleared the floor"
            )}
          </h2>
          <p className="findings-query">“{run.query}”</p>
        </div>

        {results.length === 0 && (
          <div className="empty">
            <p>
              Nothing in {run.scope ? "this document" : "your library"} matched this closely enough. Try rephrasing, lowering the
              relevance floor, or adding a document that covers the topic.
            </p>
          </div>
        )}

        {results.length > 0 && run.lowConfidence && (
          <div className="notice warn">
            <strong>Low confidence.</strong> Nothing scored as a clear match — these are the closest passages available, not
            necessarily an answer.
          </div>
        )}

        {onlyDoc && (
          <div className="filter-bar">
            Showing {visible.length} from <strong>{displayTitle(sources.find((s) => s.id === onlyDoc)?.name)}</strong>
            <button type="button" className="text-button" onClick={() => setOnlyDoc(null)}>
              Show all
            </button>
          </div>
        )}

        {visible.map((result) => (
          <ResultCard
            key={`${result.document_id}-${result.chunk_id}`}
            result={result}
            ordinal={results.indexOf(result) + 1}
            onViewSource={onViewSource}
            onScope={run.scope ? undefined : onScope}
          />
        ))}
      </div>

      <aside className="rail">
        {sources.length > 0 && (
          <section className="rail-section">
            <h3 className="eyebrow">Sources</h3>
            <ul className="rail-sources">
              {sources.map((source) => (
                <li key={source.id}>
                  <button
                    type="button"
                    className={`rail-source${onlyDoc === source.id ? " active" : ""}`}
                    onClick={() => setOnlyDoc(onlyDoc === source.id ? null : source.id)}
                    title={source.name}
                  >
                    <Cover apiBase={apiBase} documentId={source.id} filename={source.name} width={160} className="cover-mini" />
                    <span className="rail-source-name">{displayTitle(source.name)}</span>
                    <span className="rail-source-count">{source.count}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section className="rail-section">
          <h3 className="eyebrow">Query</h3>
          <dl className="record">
            <dt>Scope</dt>
            <dd>{run.scope ? displayTitle(run.scope.documentName) : "Whole library"}</dd>
            <dt>Passages</dt>
            <dd>{params.topK} requested</dd>
            <dt>Floor</dt>
            <dd className="mono">{params.minScore.toFixed(1)}</dd>
            <dt>Breadth</dt>
            <dd>{params.perDoc ? `≤${params.perDoc} per document` : "any"}</dd>
            <dt>Time</dt>
            <dd className="mono">{run.elapsed < 1000 ? `${Math.round(run.elapsed)} ms` : `${(run.elapsed / 1000).toFixed(1)} s`}</dd>
          </dl>
        </section>
      </aside>
    </div>
  );
}
