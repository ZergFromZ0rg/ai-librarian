import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { makeMatchHighlighter } from "./highlight.js";
import { displayTitle, relevancePercent } from "./storage.js";

// The shape SourceViewer expects, built from a /search or /ask result.
export function sourceFromResult(result) {
  return {
    documentId: result.document_id,
    documentName: result.document,
    page: result.page,
    matched: result.matched,
    snippet: result.text,
  };
}

// One retrieved passage, laid out like an excerpt card: where it's from
// (document, pages) and how strongly it matched on top, the passage itself
// below with the exact sub-passage that won retrieval highlighted.
//
// `index` (1-based) shows a citation tag and is set when the card backs a
// numbered citation in an Ask answer; `ordinal` numbers a search finding.
export default function ResultCard({ result, index, ordinal, onViewSource, onScope }) {
  const highlighter = makeMatchHighlighter(result.matched);
  const pages =
    result.page_end && result.page_end !== result.page ? `pp. ${result.page}–${result.page_end}` : `p. ${result.page}`;
  const score = result.rerank_score ?? result.score;
  const percent = result.rerank_score != null ? relevancePercent(result.rerank_score) : null;

  return (
    <article className="finding">
      <header className="finding-head">
        {index != null && <span className="citation-tag">[{index}]</span>}
        {ordinal != null && <span className="finding-ordinal">{String(ordinal).padStart(2, "0")}</span>}
        <div className="finding-source">
          <button
            type="button"
            className="finding-title"
            title={result.document}
            onClick={() => onViewSource(sourceFromResult(result))}
          >
            {displayTitle(result.document)}
          </button>
          <span className="finding-pages">{pages}</span>
        </div>
        {percent != null && (
          <span className="meter" title={`Reranker score ${score.toFixed(2)}`}>
            <span className="meter-track">
              <span className="meter-fill" style={{ width: `${percent}%` }} />
            </span>
            <span className="meter-value">{score.toFixed(1)}</span>
          </span>
        )}
      </header>
      {result.lead_in && <p className="finding-leadin">…{result.lead_in}</p>}
      <div className="finding-text prose">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={highlighter ? [highlighter] : []} skipHtml>
          {result.text}
        </ReactMarkdown>
      </div>
      <footer className="finding-actions">
        <button type="button" className="text-button" onClick={() => onViewSource(sourceFromResult(result))}>
          View page ↗
        </button>
        {onScope && (
          <button type="button" className="text-button" onClick={() => onScope(result)}>
            Search within this document
          </button>
        )}
      </footer>
    </article>
  );
}
