import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { makeMatchHighlighter } from "./highlight.js";

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

// One retrieved passage. `index` (1-based) shows a citation tag and is set when
// the card backs a numbered citation in an Ask answer.
export default function ResultCard({ result, index, onViewSource }) {
  const highlighter = makeMatchHighlighter(result.matched);
  const pages =
    result.page_end && result.page_end !== result.page
      ? `pages ${result.page}–${result.page_end}`
      : `page ${result.page}`;
  const score = result.rerank_score ?? result.score;

  return (
    <article className="search-result">
      <div className="search-result-meta">
        {index != null && <span className="citation-tag">[{index}]</span>}
        <strong>{result.document}</strong>
        <span>{pages}</span>
        {typeof score === "number" && <span>score {score.toFixed(3)}</span>}
        <button type="button" className="source-link" onClick={() => onViewSource(sourceFromResult(result))}>
          view source ↗
        </button>
      </div>
      {result.lead_in && <p className="search-result-leadin">…{result.lead_in}</p>}
      <div className="search-result-text">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={highlighter ? [highlighter] : []}
          skipHtml
        >
          {result.text}
        </ReactMarkdown>
      </div>
    </article>
  );
}
