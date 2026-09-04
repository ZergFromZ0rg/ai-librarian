import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import ResultCard from "./ResultCard.jsx";
import { streamAsk } from "./askStream.js";

const ACTIVE_KEY = "ai-librarian.ask.active";
const MODEL_KEY = "ai-librarian.ask.model";
const THOROUGH_KEY = "ai-librarian.ask.thorough";
const KEYS_KEY = "ai-librarian.ask.keys";

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

// Scroll the nth source card of a given turn into view and flash it.
function focusSource(turnIndex, n) {
  const el = document.getElementById(`ask-src-${turnIndex}-${n}`);
  if (!el) return;
  // Instant, not smooth: smooth scrollIntoView silently no-ops in some
  // embedded/automated browser contexts. The flash is the "you moved" cue.
  el.scrollIntoView({ block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth; // restart the animation if it is still running
  el.classList.add("flash");
}

export default function AskPanel({ apiBase, onViewSource, indexedCount }) {
  const [conversation, setConversation] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(() => loadStored(ACTIVE_KEY, "") || null);
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
  const savedRef = useRef("[]"); // JSON of what the server last has, so autosave is a no-op after a load

  const refreshList = useCallback(async () => {
    try {
      const response = await fetch(`${apiBase}/conversations`);
      if (!response.ok) return;
      const data = await response.json();
      setConversations(data.conversations || []);
    } catch (_error) {
      // list is a convenience; failing to load it is not fatal
    }
  }, [apiBase]);

  const loadConversation = useCallback(
    async (id) => {
      if (!id) {
        setConversation([]);
        setActiveId(null);
        savedRef.current = "[]";
        saveStored(ACTIVE_KEY, "");
        return;
      }
      try {
        const response = await fetch(`${apiBase}/conversations/${id}`);
        if (!response.ok) throw new Error("not found");
        const data = await response.json();
        const messages = data.messages || [];
        setConversation(messages);
        setActiveId(id);
        savedRef.current = JSON.stringify(messages);
        saveStored(ACTIVE_KEY, id);
        if (data.model) setSelectedModel(data.model);
      } catch (_error) {
        loadConversation(null);
      }
    },
    [apiBase],
  );

  useEffect(() => {
    refreshList();
  }, [refreshList]);

  useEffect(() => {
    const stored = loadStored(ACTIVE_KEY, "");
    if (stored) loadConversation(stored);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  // or pasted into the browser.
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
    for (const provider of CLOUD_PROVIDERS) {
      const usable = keyedProviders.has(provider.id) || !!apiKeys[provider.id];
      for (const name of provider.models) {
        add({ id: `${provider.id}:${name}`, label: name, provider: provider.id, usable });
      }
    }
    return out;
  }, [serverModels, apiKeys]);

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
    saveStored(KEYS_KEY, apiKeys);
  }, [apiKeys]);

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
            setActiveId(created.id);
            saveStored(ACTIVE_KEY, created.id);
          }
        }
        refreshList();
      } catch (_error) {
        savedRef.current = "[]"; // let the next turn retry the save
      }
    })();
  }, [busy, conversation, activeId, apiBase, selectedModel, refreshList]);

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

  function startNew() {
    setError("");
    loadConversation(null);
  }

  async function deleteActive() {
    if (!activeId) return;
    try {
      await fetch(`${apiBase}/conversations/${activeId}`, { method: "DELETE" });
    } catch (_error) {
      // best effort
    }
    startNew();
    refreshList();
  }

  const modelLabel = (id) => models.find((m) => m.id === id)?.label || id;
  const noModels = usableModels.length === 0;

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
                      <option
                        key={model.id}
                        value={model.id}
                        disabled={!model.usable}
                        title={model.usable ? undefined : "Add this provider's API key to use it"}
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
          <button
            type="button"
            className="secondary"
            onClick={() => setShowKeys((v) => !v)}
            aria-expanded={showKeys}
          >
            API keys
          </button>
          <label className="ask-model-select">
            <span>Chat</span>
            <select
              value={activeId || ""}
              disabled={busy}
              onChange={(event) =>
                event.target.value ? loadConversation(event.target.value) : startNew()
              }
            >
              <option value="">＋ New conversation</option>
              {conversations.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title || "Untitled"}
                </option>
              ))}
            </select>
          </label>
          {activeId && (
            <button type="button" className="link" onClick={deleteActive} disabled={busy}>
              Delete
            </button>
          )}
          {!activeId && conversation.length > 0 && (
            <button type="button" className="secondary" onClick={startNew} disabled={busy}>
              New
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
                            className="cite"
                            title={`Jump to source ${n}`}
                            onClick={() => focusSource(index, n)}
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
                    <div
                      className="ask-src"
                      id={`ask-src-${index}-${position + 1}`}
                      key={`${source.document_id}-${source.chunk_id}-${position}`}
                    >
                      <ResultCard result={source} index={position + 1} onViewSource={onViewSource} />
                    </div>
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
