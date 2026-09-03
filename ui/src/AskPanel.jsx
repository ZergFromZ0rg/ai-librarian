import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import ResultCard from "./ResultCard.jsx";
import { streamAsk } from "./askStream.js";

const CONVO_KEY = "ai-librarian.ask.conversation";
const MODEL_KEY = "ai-librarian.ask.model";

const PROVIDER_LABELS = {
  ollama: "Local (Ollama)",
  anthropic: "Claude",
  openai: "OpenAI",
  google: "Gemini",
};

function loadStored(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw != null) return key === CONVO_KEY ? JSON.parse(raw) : raw;
  } catch (_error) {
    // corrupt or unavailable storage
  }
  return fallback;
}

function saveStored(key, value) {
  try {
    window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch (_error) {
    // best effort
  }
}

// Wrap bracketed citation numbers ("[1]", "[2][3]") in a styled <span>.
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
  const [conversation, setConversation] = useState(() => loadStored(CONVO_KEY, []));
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState(() => loadStored(MODEL_KEY, ""));
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const scrollRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/ask/models`)
      .then((response) => (response.ok ? response.json() : { models: [] }))
      .then((data) => {
        if (cancelled) return;
        const list = data.models || [];
        setModels(list);
        setSelectedModel((current) => {
          if (current && list.some((m) => m.id === current)) return current;
          return data.default || list[0]?.id || "";
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  useEffect(() => {
    if (selectedModel) saveStored(MODEL_KEY, selectedModel);
  }, [selectedModel]);

  useEffect(() => {
    saveStored(CONVO_KEY, conversation);
  }, [conversation]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [conversation]);

  const grouped = useMemo(() => {
    const byProvider = new Map();
    for (const model of models) {
      if (!byProvider.has(model.provider)) byProvider.set(model.provider, []);
      byProvider.get(model.provider).push(model);
    }
    return [...byProvider.entries()];
  }, [models]);

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
        { role: "assistant", content: "", sources: null, pending: true, model: selectedModel },
      ]);

      try {
        await streamAsk(
          `${apiBase}/ask`,
          { question: clean, history, model: selectedModel || undefined },
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
                  turn.usedModel = evt.model;
                  turn.documents = evt.documents;
                  turn.relevantCount = evt.relevant_count;
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
    [apiBase, busy, conversation, patchLast, question, selectedModel],
  );

  function reset() {
    setConversation([]);
    setError("");
    try {
      window.localStorage.removeItem(CONVO_KEY);
    } catch (_error) {
      // best effort
    }
  }

  const modelLabel = (id) => models.find((m) => m.id === id)?.label || id;

  if (models.length === 0) {
    return (
      <div className="empty-state">
        <div>
          <strong>No models available.</strong>
          Start Ollama on the server, or add an API key (Claude, OpenAI, Gemini), then
          reload.
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="chat-header ask-header">
        <div>
          <h2>Ask your library</h2>
          <p>Answers are written from your documents and cite the passages they draw on.</p>
        </div>
        <div className="ask-header-controls">
          <label className="ask-model-select">
            <span>Model</span>
            <select
              value={selectedModel}
              disabled={busy}
              onChange={(event) => setSelectedModel(event.target.value)}
            >
              {grouped.map(([provider, list]) => (
                <optgroup key={provider} label={PROVIDER_LABELS[provider] || provider}>
                  {list.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.label}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          {conversation.length > 0 && (
            <button type="button" className="secondary" onClick={reset} disabled={busy}>
              New conversation
            </button>
          )}
        </div>
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
                  <div className="ask-sources-label">
                    {[
                      `${turn.sources.length} passage${turn.sources.length === 1 ? "" : "s"}`,
                      turn.documents ? `${turn.documents} document${turn.documents === 1 ? "" : "s"}` : null,
                      turn.relevantCount > turn.sources.length ? `${turn.relevantCount} relevant matches` : null,
                      turn.usedModel ? modelLabel(turn.usedModel) : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
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
