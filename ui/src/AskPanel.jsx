import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import ResultCard from "./ResultCard.jsx";
import { streamAsk } from "./askStream.js";

const MODEL_KEY = "ai-librarian.ask.model";
const THOROUGH_KEY = "ai-librarian.ask.thorough";

// A turn is worth persisting once its assistant reply has finished streaming.
function isComplete(conv) {
  const last = conv[conv.length - 1];
  return last && last.role === "assistant" && last.content && !last.pending;
}

const PROVIDER_LABELS = {
  ollama: "Local (Ollama)",
  anthropic: "Claude",
  openai: "OpenAI",
  google: "Gemini",
};

// Cloud providers the reader can add a key for, and the models each offers
// (kept in step with the server's generation._CLOUD defaults). Exported for
// Settings, which owns the actual key-entry form now.
export const CLOUD_PROVIDERS = [
  { id: "anthropic", label: "Anthropic (Claude)", placeholder: "sk-ant-…", models: ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"] },
  { id: "openai", label: "OpenAI (GPT)", placeholder: "sk-…", models: ["gpt-5.1", "gpt-5.1-mini"] },
  { id: "google", label: "Google (Gemini)", placeholder: "AIza…", models: ["gemini-2.5-pro", "gemini-2.5-flash"] },
];

function loadStored(key, fallback, parse = false) {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw != null) return parse ? JSON.parse(raw) : raw;
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
    <div className="ask-turn assistant">
      {turn.content && (
        <div className="ask-answer">
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
        <div className="ask-thinking">{turn.progress || "Reading the sources…"}</div>
      )}
      {turn.error && <div className="status-message error">{turn.error}</div>}
      {turn.lowConfidence && (
        <div className="status-message">
          Nothing in your library scored as a clear match — treat this answer with care.
        </div>
      )}
      {turn.sources && turn.sources.length > 0 && (
        <div className="ask-sources-label">
          {[
            `${turn.sources.length} passage${turn.sources.length === 1 ? "" : "s"}`,
            turn.documents ? `${turn.documents} document${turn.documents === 1 ? "" : "s"}` : null,
            turn.relevantCount > turn.sources.length ? `${turn.relevantCount} relevant matches` : null,
            turn.usedModel ? modelLabel(turn.usedModel) : null,
          ]
            .filter(Boolean)
            .join(" · ")}
          {" — click a "}
          <span className="citation-tag">[n]</span>
          {" in the answer above to view its source."}
        </div>
      )}
      {openSource && (
        <div className="ask-src" ref={sourceRef}>
          <ResultCard result={openSource} index={turn.openCitation} onViewSource={onViewSource} />
        </div>
      )}
    </div>
  );
}

// `activeId`/`onActiveIdChange` are controlled from above (the sidebar chat
// list owns switching/creating/deleting conversations); this component just
// loads whichever conversation that id points at and streams new turns into
// it. `apiKeys` is likewise owned by Settings now.
export default function AskPanel({ apiBase, onViewSource, indexedCount, activeId, onActiveIdChange, onConversationsChanged, apiKeys, ollamaModels }) {
  const [conversation, setConversation] = useState([]);
  const [serverModels, setServerModels] = useState([]);
  const [serverDefault, setServerDefault] = useState("");
  const [selectedModel, setSelectedModel] = useState(() => loadStored(MODEL_KEY, ""));
  const [thorough, setThorough] = useState(() => loadStored(THOROUGH_KEY, "") === "1");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const scrollRef = useRef(null);
  const savedRef = useRef("[]"); // JSON of what the server last has, so autosave is a no-op after a load
  const skipNextLoadRef = useRef(false); // set right before we already know the new id's content locally

  // Load whichever conversation `activeId` now points at. Skipped once, right
  // after *we* mint a brand-new id from autosave below — we already hold the
  // richer in-progress turn (sources, citations, streaming state) that a
  // server round-trip would flatten back down to bare role/content.
  useEffect(() => {
    if (skipNextLoadRef.current) {
      skipNextLoadRef.current = false;
      return;
    }
    let cancelled = false;
    if (!activeId) {
      setConversation([]);
      savedRef.current = "[]";
      return undefined;
    }
    (async () => {
      try {
        const response = await fetch(`${apiBase}/conversations/${activeId}`);
        if (!response.ok) throw new Error("not found");
        const data = await response.json();
        if (cancelled) return;
        const messages = data.messages || [];
        setConversation(messages);
        savedRef.current = JSON.stringify(messages);
        if (data.model) setSelectedModel(data.model);
      } catch (_error) {
        if (!cancelled) onActiveIdChange(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId, apiBase, onActiveIdChange]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/ask/models`)
      .then((response) => (response.ok ? response.json() : { models: [] }))
      .then((data) => {
        if (cancelled) return;
        setServerModels(data.models || []);
        setServerDefault(data.default || "");
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  // Every model, each tagged `usable`. Ollama models come from the server and
  // are always usable; each cloud provider's catalogue is always shown but only
  // usable when a key exists — server-side (its provider is in the server list)
  // or pasted into the browser. Models typed into Settings under "Local
  // (Ollama)" are just as usable — the reader is asserting they've pulled it.
  const models = useMemo(() => {
    const out = [];
    const seen = new Set();
    const keyedProviders = new Set();
    const add = (m) => {
      if (!seen.has(m.id)) {
        seen.add(m.id);
        out.push(m);
      }
    };
    for (const m of serverModels) {
      add({ ...m, usable: true });
      if (m.provider !== "ollama") keyedProviders.add(m.provider);
    }
    for (const name of ollamaModels || []) {
      add({ id: `ollama:${name}`, label: name, provider: "ollama", usable: true });
    }
    for (const provider of CLOUD_PROVIDERS) {
      const usable = keyedProviders.has(provider.id) || !!apiKeys[provider.id];
      for (const name of provider.models) {
        add({ id: `${provider.id}:${name}`, label: name, provider: provider.id, usable });
      }
    }
    return out;
  }, [serverModels, apiKeys, ollamaModels]);

  const usableModels = useMemo(() => models.filter((m) => m.usable), [models]);

  useEffect(() => {
    setSelectedModel((current) => {
      if (current && usableModels.some((m) => m.id === current)) return current;
      if (serverDefault && usableModels.some((m) => m.id === serverDefault)) return serverDefault;
      return usableModels[0]?.id || "";
    });
  }, [usableModels, serverDefault]);

  useEffect(() => {
    if (selectedModel) saveStored(MODEL_KEY, selectedModel);
  }, [selectedModel]);

  useEffect(() => {
    saveStored(THOROUGH_KEY, thorough ? "1" : "0");
  }, [thorough]);

  // Persist a finished turn to the server: create the conversation on the first
  // one, replace it after each subsequent turn. `savedRef` keeps a load from
  // immediately writing back what it just read.
  useEffect(() => {
    if (busy || conversation.length === 0 || !isComplete(conversation)) return;
    const snapshot = JSON.stringify(conversation);
    if (snapshot === savedRef.current) return;
    savedRef.current = snapshot;
    const body = { messages: conversation, model: selectedModel || undefined };
    (async () => {
      try {
        if (activeId) {
          await fetch(`${apiBase}/conversations/${activeId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
        } else {
          const created = await (
            await fetch(`${apiBase}/conversations`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            })
          ).json();
          if (created.id) {
            skipNextLoadRef.current = true;
            onActiveIdChange(created.id);
          }
        }
        onConversationsChanged?.();
      } catch (_error) {
        savedRef.current = "[]"; // let the next turn retry the save
      }
    })();
  }, [busy, conversation, activeId, apiBase, selectedModel, onActiveIdChange, onConversationsChanged]);

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
        const providerKeys = Object.fromEntries(
          Object.entries(apiKeys).filter(([, v]) => v && v.trim()),
        );
        await streamAsk(
          `${apiBase}/ask`,
          {
            question: clean,
            history,
            model: selectedModel || undefined,
            mode: thorough ? "thorough" : "quick",
            ...(Object.keys(providerKeys).length ? { provider_keys: providerKeys } : {}),
          },
          {
            onEvent: (evt) => {
              if (evt.type === "token") {
                patchLast((turn) => {
                  turn.content += evt.text;
                });
              } else if (evt.type === "progress") {
                patchLast((turn) => {
                  turn.progress = evt.text;
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
    [apiBase, apiKeys, busy, conversation, patchLast, question, selectedModel, thorough],
  );

  const modelLabel = (id) => models.find((m) => m.id === id)?.label || id;
  const noModels = usableModels.length === 0;

  // Clicking an already-open citation closes it; clicking a different one
  // swaps to it. Only one source shown at a time per turn, kept minimal.
  function toggleCitation(turnIndex, n) {
    setConversation((prev) =>
      prev.map((turn, i) => (i === turnIndex ? { ...turn, openCitation: turn.openCitation === n ? null : n } : turn)),
    );
  }

  return (
    <>
      <div className={`chat-header ask-header${conversation.length > 0 ? " compact" : ""}`}>
        {conversation.length === 0 && (
          <div>
            <h2>Ask your library</h2>
            <p>Answers are written from your documents and cite the passages they draw on.</p>
          </div>
        )}
        <div className="ask-header-controls">
          {!noModels && (
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
                      <option
                        key={model.id}
                        value={model.id}
                        disabled={!model.usable}
                        title={model.usable ? undefined : "Add this provider's API key in Settings to use it"}
                      >
                        {model.label}
                        {model.usable ? "" : " — needs API key"}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
          )}
          {!noModels && (
            <label className="ask-thorough" title="Read a wider set of passages, grouped by document, and synthesise across them. Slower.">
              <input
                type="checkbox"
                checked={thorough}
                disabled={busy}
                onChange={(event) => setThorough(event.target.checked)}
              />
              <span>Thorough</span>
            </label>
          )}
        </div>
      </div>

      <div className="messages ask-thread" ref={scrollRef} aria-live="polite">
        {noModels && (
          <div className="empty-state">
            <div>
              <strong>No models available.</strong>
              Pull a model with Ollama on the server, or add a cloud API key in Settings.
            </div>
          </div>
        )}
        {!noModels && conversation.length === 0 && (
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
            <AssistantTurn
              key={index}
              turn={turn}
              index={index}
              onViewSource={onViewSource}
              onToggleCitation={toggleCitation}
              modelLabel={modelLabel}
            />
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
        <button
          className="primary"
          type="submit"
          disabled={!question.trim() || busy || indexedCount === 0 || noModels}
        >
          {busy ? "Answering…" : "Ask"}
        </button>
      </form>
    </>
  );
}
