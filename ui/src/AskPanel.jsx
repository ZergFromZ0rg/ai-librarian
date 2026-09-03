import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import ResultCard from "./ResultCard.jsx";
import { streamAsk } from "./askStream.js";

const CONVO_KEY = "ai-librarian.ask.conversation";
const MODEL_KEY = "ai-librarian.ask.model";
const THOROUGH_KEY = "ai-librarian.ask.thorough";
const KEYS_KEY = "ai-librarian.ask.keys";

const PROVIDER_LABELS = {
  ollama: "Local (Ollama)",
  anthropic: "Claude",
  openai: "OpenAI",
  google: "Gemini",
};

// Cloud providers the reader can add a key for, and the models each offers
// (kept in step with the server's generation._CLOUD defaults).
const CLOUD_PROVIDERS = [
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
  const [conversation, setConversation] = useState(() => loadStored(CONVO_KEY, [], true));
  const [serverModels, setServerModels] = useState([]);
  const [serverDefault, setServerDefault] = useState("");
  const [apiKeys, setApiKeys] = useState(() => loadStored(KEYS_KEY, {}, true) || {});
  const [showKeys, setShowKeys] = useState(false);
  const [selectedModel, setSelectedModel] = useState(() => loadStored(MODEL_KEY, ""));
  const [thorough, setThorough] = useState(() => loadStored(THOROUGH_KEY, "") === "1");
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
        setServerModels(data.models || []);
        setServerDefault(data.default || "");
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  // Server-listed models + a row for each cloud model whose key the browser holds.
  const models = useMemo(() => {
    const merged = [...serverModels];
    const seen = new Set(merged.map((m) => m.id));
    for (const provider of CLOUD_PROVIDERS) {
      if (!apiKeys[provider.id]) continue;
      for (const name of provider.models) {
        const id = `${provider.id}:${name}`;
        if (!seen.has(id)) {
          seen.add(id);
          merged.push({ id, label: name, provider: provider.id });
        }
      }
    }
    return merged;
  }, [serverModels, apiKeys]);

  useEffect(() => {
    setSelectedModel((current) => {
      if (current && models.some((m) => m.id === current)) return current;
      return serverDefault || models[0]?.id || "";
    });
  }, [models, serverDefault]);

  useEffect(() => {
    if (selectedModel) saveStored(MODEL_KEY, selectedModel);
  }, [selectedModel]);

  useEffect(() => {
    saveStored(KEYS_KEY, apiKeys);
  }, [apiKeys]);

  useEffect(() => {
    saveStored(THOROUGH_KEY, thorough ? "1" : "0");
  }, [thorough]);

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
  const noModels = models.length === 0;

  return (
    <>
      <div className="chat-header ask-header">
        <div>
          <h2>Ask your library</h2>
          <p>Answers are written from your documents and cite the passages they draw on.</p>
        </div>
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
                      <option key={model.id} value={model.id}>
                        {model.label}
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
          <button
            type="button"
            className="secondary"
            onClick={() => setShowKeys((v) => !v)}
            aria-expanded={showKeys}
          >
            API keys
          </button>
          {conversation.length > 0 && (
            <button type="button" className="secondary" onClick={reset} disabled={busy}>
              New conversation
            </button>
          )}
        </div>
      </div>

      {showKeys && (
        <ApiKeyPanel apiKeys={apiKeys} setApiKeys={setApiKeys} onClose={() => setShowKeys(false)} />
      )}

      <div className="messages ask-thread" ref={scrollRef} aria-live="polite">
        {noModels && (
          <div className="empty-state">
            <div>
              <strong>No models available.</strong>
              Pull a model with Ollama on the server, or add a cloud API key above.
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
            <div className="ask-turn assistant" key={index}>
              {turn.content && (
                <div className="ask-answer">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[citationRehype]} skipHtml>
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

function ApiKeyPanel({ apiKeys, setApiKeys, onClose }) {
  const [draft, setDraft] = useState({});

  function save(providerId) {
    const value = (draft[providerId] || "").trim();
    if (!value) return;
    setApiKeys({ ...apiKeys, [providerId]: value });
    setDraft((d) => ({ ...d, [providerId]: "" }));
  }

  function remove(providerId) {
    const next = { ...apiKeys };
    delete next[providerId];
    setApiKeys(next);
  }

  return (
    <div className="ask-keys">
      <div className="ask-keys-head">
        <strong>Cloud API keys</strong>
        <button type="button" className="link" onClick={onClose}>
          Done
        </button>
      </div>
      <p className="ask-keys-note">
        Keys stay in this browser (localStorage) and are sent with each question. They are
        never stored on the server.
      </p>
      {CLOUD_PROVIDERS.map((provider) => (
        <div className="ask-keys-row" key={provider.id}>
          <span className="ask-keys-label">{provider.label}</span>
          <input
            type="password"
            autoComplete="off"
            placeholder={apiKeys[provider.id] ? "•••••• saved" : provider.placeholder}
            value={draft[provider.id] || ""}
            onChange={(event) => setDraft((d) => ({ ...d, [provider.id]: event.target.value }))}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                save(provider.id);
              }
            }}
          />
          <button type="button" className="secondary" onClick={() => save(provider.id)}>
            Save
          </button>
          {apiKeys[provider.id] && (
            <button type="button" className="link" onClick={() => remove(provider.id)}>
              Remove
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
