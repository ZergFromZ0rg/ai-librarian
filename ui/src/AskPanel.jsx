import React, { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import ResultCard from "./ResultCard.jsx";
import { streamAsk } from "./askStream.js";

const STORAGE_KEY = "ai-librarian.ask.conversation";

function loadConversation() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (_error) {
    // corrupt or unavailable storage — start fresh
  }
  return [];
}

// Wrap bracketed citation numbers ("[1]", "[2][3]") in a styled <span> so they
// stand out from the prose. Presentational only.
function citationRehype() {
  const pattern = /\[\d+\](?:\[\d+\])*/g;
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
        tagName: "span",
        properties: { className: ["cite"] },
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

export default function AskPanel({ apiBase, onViewSource, indexedCount }) {
  const [conversation, setConversation] = useState(loadConversation);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const scrollRef = useRef(null);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(conversation));
    } catch (_error) {
      // best effort
    }
  }, [conversation]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [conversation]);

  const patchLast = useCallback((mutate) => {
    setConversation((prev) => {
      if (prev.length === 0) return prev;
      const next = prev.slice();
      const last = { ...next[next.length - 1] };
      mutate(last);
      next[next.length - 1] = last;
      return next;
    });
  }, []);

  const ask = useCallback(
    async (event) => {
      event.preventDefault();
      const clean = question.trim();
      if (!clean || busy) return;
      setBusy(true);
      setError("");
      setQuestion("");

      const history = conversation
        .filter((turn) => turn.content && !turn.error)
        .map((turn) => ({ role: turn.role, content: turn.content }));

      setConversation((prev) => [
        ...prev,
        { role: "user", content: clean },
        { role: "assistant", content: "", sources: null, pending: true },
      ]);

      try {
        await streamAsk(
          `${apiBase}/ask`,
          { question: clean, history },
          {
            onEvent: (evt) => {
              if (evt.type === "token") {
                patchLast((turn) => {
                  turn.content += evt.text;
                });
              } else if (evt.type === "sources") {
                patchLast((turn) => {
                  turn.sources = evt.results || [];
                  turn.lowConfidence = Boolean(evt.low_confidence);
                  turn.pending = false;
                });
              } else if (evt.type === "error") {
                patchLast((turn) => {
                  turn.error = evt.detail;
                  turn.pending = false;
                });
              }
            },
          },
        );
      } catch (err) {
        setError(err.message);
        patchLast((turn) => {
          turn.error = err.message;
          turn.pending = false;
        });
      } finally {
        setBusy(false);
        patchLast((turn) => {
          turn.pending = false;
        });
      }
    },
    [apiBase, busy, conversation, patchLast, question],
  );

  function reset() {
    setConversation([]);
    setError("");
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch (_error) {
      // best effort
    }
  }

  return (
    <>
      <div className="chat-header ask-header">
        <div>
          <h2>Ask your library</h2>
          <p>Answers are written from your documents and cite the passages they draw on.</p>
        </div>
        {conversation.length > 0 && (
          <button type="button" className="secondary" onClick={reset} disabled={busy}>
            New conversation
          </button>
        )}
      </div>

      <div className="messages ask-thread" ref={scrollRef} aria-live="polite">
        {conversation.length === 0 && (
          <div className="empty-state">
            <div>
              <strong>Ask a question about your reading.</strong>
              Every answer is grounded in the retrieved passages and links back to the source pages.
            </div>
          </div>
        )}

        {conversation.map((turn, index) =>
          turn.role === "user" ? (
            <div className="ask-turn user" key={index}>
              {turn.content}
            </div>
          ) : (
            <div className="ask-turn assistant" key={index}>
              {turn.content && (
                <div className="ask-answer">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[citationRehype]} skipHtml>
                    {turn.content}
                  </ReactMarkdown>
                </div>
              )}
              {turn.pending && !turn.content && <div className="ask-thinking">Reading the sources…</div>}
              {turn.error && <div className="status-message error">{turn.error}</div>}
              {turn.lowConfidence && (
                <div className="status-message">
                  Nothing in your library scored as a clear match — treat this answer with care.
                </div>
              )}
              {turn.sources && turn.sources.length > 0 && (
                <div className="ask-sources">
                  <div className="ask-sources-label">Sources</div>
                  {turn.sources.map((source, position) => (
                    <ResultCard
                      key={`${source.document_id}-${source.chunk_id}-${position}`}
                      result={source}
                      index={position + 1}
                      onViewSource={onViewSource}
                    />
                  ))}
                </div>
              )}
            </div>
          ),
        )}
      </div>

      {error && <div className="status-message error ask-error">{error}</div>}

      <form className="question-box" onSubmit={ask}>
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form.requestSubmit();
            }
          }}
          placeholder="Ask a question about your library…"
          aria-label="Question"
        />
        <button className="primary" type="submit" disabled={!question.trim() || busy || indexedCount === 0}>
          {busy ? "Answering…" : "Ask"}
        </button>
      </form>
    </>
  );
}
