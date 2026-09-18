import React, { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import ResultCard from "./ResultCard.jsx";
import { displayTitle } from "./storage.js";

// Turn each bracketed citation number ("[1]", "[2][3]" -> two) into its own
// <sup class="cite" data-n="N"> so it can be made clickable in `components`.
function citationRehype() {
  const pattern = /\[(\d+)\]/g;
  const split = (value) => {
    pattern.lastIndex = 0;
    if (!pattern.test(value)) return null;
    pattern.lastIndex = 0;
    const pieces = [];
    let last = 0;
    let match;
    while ((match = pattern.exec(value)) !== null) {
      if (match.index > last) pieces.push({ type: "text", value: value.slice(last, match.index) });
      pieces.push({
        type: "element",
        tagName: "sup",
        properties: { className: ["cite"], dataN: match[1] },
        children: [{ type: "text", value: match[0] }],
      });
      last = match.index + match[0].length;
    }
    if (last < value.length) pieces.push({ type: "text", value: value.slice(last) });
    return pieces;
  };
  const walk = (node) => {
    if (!node.children) return;
    const next = [];
    for (const child of node.children) {
      if (child.type === "element") walk(child);
      if (child.type === "text") {
        const pieces = split(child.value);
        if (pieces) {
          next.push(...pieces);
          continue;
        }
      }
      next.push(child);
    }
    node.children = next;
  };
  return (tree) => walk(tree);
}

// One assistant turn. Split out (rather than inlined in the .map() below) so
// opening a citation can scroll+flash the newly-revealed card via its own
// effect — that only works cleanly with a ref scoped to this one turn.
function AssistantTurn({ turn, index, onViewSource, onToggleCitation, modelLabel }) {
  const sourceRef = useRef(null);

  useEffect(() => {
    if (turn.openCitation == null || !sourceRef.current) return;
    const el = sourceRef.current;
    // Instant, not smooth: smooth scrollIntoView silently no-ops in some
    // embedded/automated browser contexts. The flash is the "you moved" cue.
    el.scrollIntoView({ block: "nearest" });
    el.classList.remove("flash");
    void el.offsetWidth; // restart the animation if it is still running
    el.classList.add("flash");
  }, [turn.openCitation]);

  const openSource = turn.openCitation != null ? turn.sources?.[turn.openCitation - 1] : null;

  return (
    <div className="turn assistant">
      <div className="turn-label">
        <span>Answer</span>
        {turn.usedModel && <span className="turn-model">{modelLabel(turn.usedModel)}</span>}
      </div>
      {turn.content && (
        <div className="answer prose">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[citationRehype]}
            skipHtml
            components={{
              sup: ({ node, children }) => {
                const n = Number(node?.properties?.dataN);
                if (!n) return <sup>{children}</sup>;
                return (
                  <button
                    type="button"
                    className={`cite${turn.openCitation === n ? " open" : ""}`}
                    title={`${turn.openCitation === n ? "Hide" : "Show"} source ${n}`}
                    onClick={() => onToggleCitation(index, n)}
                  >
                    {children}
                  </button>
                );
              },
            }}
          >
            {turn.content}
          </ReactMarkdown>
        </div>
      )}
      {turn.pending && !turn.content && (
        <div className="thinking">
          <span className="thinking-dots" aria-hidden="true"><i /><i /><i /></span>
          {turn.progress || "Reading the sources…"}
        </div>
      )}
      {turn.error && <div className="notice error">{turn.error}</div>}
      {turn.lowConfidence && (
        <div className="notice warn">
          Nothing in your library scored as a clear match — treat this answer with care.
        </div>
      )}
      {turn.sources && turn.sources.length > 0 && (
        <div className="turn-footnote">
          {[
            `${turn.sources.length} passage${turn.sources.length === 1 ? "" : "s"}`,
            turn.documents ? `${turn.documents} document${turn.documents === 1 ? "" : "s"}` : null,
            turn.relevantCount > turn.sources.length ? `${turn.relevantCount} relevant matches` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
          {" — click a "}
          <span className="citation-tag">[n]</span>
          {" to read its source."}
        </div>
      )}
      {openSource && (
        <div className="turn-source" ref={sourceRef}>
          <button type="button" className="text-button turn-source-close" onClick={() => onToggleCitation(index, turn.openCitation)}>
            × Hide source
          </button>
          <ResultCard result={openSource} index={turn.openCitation} onViewSource={onViewSource} />
        </div>
      )}
    </div>
  );
}

// The side rail in Ask mode: the latest answer's numbered sources, each a
// shortcut to opening that citation under the answer.
export function AskRail({ conversation, onToggleCitation }) {
  let turnIndex = -1;
  for (let i = conversation.length - 1; i >= 0; i -= 1) {
    if (conversation[i].role === "assistant" && conversation[i].sources?.length) {
      turnIndex = i;
      break;
    }
  }
  if (turnIndex < 0) return null;
  const turn = conversation[turnIndex];
  return (
    <aside className="rail">
      <section className="rail-section">
        <h3 className="eyebrow">Cited sources</h3>
        <ol className="rail-citations">
          {turn.sources.map((source, i) => (
            <li key={`${source.document_id}-${source.chunk_id}`}>
              <button
                type="button"
                className={`rail-citation${turn.openCitation === i + 1 ? " active" : ""}`}
                onClick={() => onToggleCitation(turnIndex, i + 1)}
                title={source.document}
              >
                <span className="citation-tag">[{i + 1}]</span>
                <span className="rail-citation-name">{displayTitle(source.document)}</span>
                <span className="rail-citation-page">p. {source.page}</span>
              </button>
            </li>
          ))}
        </ol>
      </section>
    </aside>
  );
}

export default function AskThread({ conversation, onViewSource, onToggleCitation, modelLabel, noModels, loading }) {
  const lastUserRef = useRef(null);
  const count = conversation.length;

  // Bring each follow-up question into view once, as it's asked — not on
  // every streamed token, which would fight the reader scrolling back up. A
  // conversation's first question already sits at the top of the page.
  useEffect(() => {
    if (count > 2) lastUserRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    else window.scrollTo({ top: 0 });
  }, [count]);

  if (loading) {
    return (
      <div className="workspace-status">
        <span className="spinner" aria-hidden="true" /> Opening chat…
      </div>
    );
  }

  if (noModels) {
    return (
      <div className="empty">
        <strong>No models available.</strong>
        <p>Pull a model with Ollama on the server, or add a cloud API key in Settings.</p>
      </div>
    );
  }

  const lastUserIndex = conversation.map((turn) => turn.role).lastIndexOf("user");

  return (
    <div className="thread" aria-live="polite">
      {conversation.map((turn, index) =>
        turn.role === "user" ? (
          <div className="turn user" key={index} ref={index === lastUserIndex ? lastUserRef : undefined}>
            <div className="turn-label">Question</div>
            <p className="question-text">{turn.content}</p>
          </div>
        ) : (
          <AssistantTurn
            key={index}
            turn={turn}
            index={index}
            onViewSource={onViewSource}
            onToggleCitation={onToggleCitation}
            modelLabel={modelLabel}
          />
        ),
      )}
    </div>
  );
}
