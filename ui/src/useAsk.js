import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { streamAsk } from "./askStream.js";
import { loadStored, saveStored } from "./storage.js";

const MODEL_KEY = "ai-librarian.ask.model";

export const PROVIDER_LABELS = {
  ollama: "Local (Ollama)",
  anthropic: "Claude",
  openai: "OpenAI",
  google: "Gemini",
};

// Cloud providers the reader can add a key for, and the models each offers
// (kept in step with the server's generation._CLOUD defaults). Settings owns
// the actual key-entry form.
export const CLOUD_PROVIDERS = [
  { id: "anthropic", label: "Anthropic (Claude)", placeholder: "sk-ant-…", models: ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"] },
  { id: "openai", label: "OpenAI (GPT)", placeholder: "sk-…", models: ["gpt-5.1", "gpt-5.1-mini"] },
  { id: "google", label: "Google (Gemini)", placeholder: "AIza…", models: ["gemini-2.5-pro", "gemini-2.5-flash"] },
];

// A turn is worth persisting once its assistant reply has finished streaming.
function isComplete(conv) {
  const last = conv[conv.length - 1];
  return last && last.role === "assistant" && last.content && !last.pending;
}

// Ask mode's conversation state, split out of the view so the research
// console (which owns the question box for both Search and Ask) can drive it.
//
// `activeId`/`onActiveIdChange` are controlled from above (History and the
// top bar switch/create/delete conversations); this hook loads whichever
// conversation that id points at and streams new turns into it. `onSwitch`
// fires on a real switch so the caller can clear its draft question box.
//
// Everything asynchronous here — a streamed answer, a save, a load — can
// finish after the reader has already moved to another chat. So the
// conversation carries a `session` number, bumped on every switch or reset,
// and every late arrival checks it first: a token, save result or load for a
// session that's no longer current is simply dropped. That's what keeps one
// chat's answer from landing in another, and a background save from pulling
// the reader back to a chat they've left.
export default function useAsk({ apiBase, activeId, onActiveIdChange, onConversationsChanged, apiKeys, ollamaModels, onSwitch }) {
  const sessionRef = useRef(0);
  const [chat, setChat] = useState({ session: 0, id: activeId || null, messages: [], loading: Boolean(activeId) });
  const [serverModels, setServerModels] = useState([]);
  const [serverDefault, setServerDefault] = useState("");
  const [selectedModel, setSelectedModel] = useState(() => loadStored(MODEL_KEY, ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const savedRef = useRef({ session: 0, json: "[]" }); // what the server holds for this session
  const creatingRef = useRef(null); // session whose first save (POST) is in flight
  const adoptRef = useRef(null); // id we just minted ourselves: don't reload it
  const streamControllerRef = useRef(null); // in-flight /ask request
  const onSwitchRef = useRef(onSwitch);
  onSwitchRef.current = onSwitch;

  useEffect(() => () => streamControllerRef.current?.abort(), []);

  // Start a fresh session for `id` (null = a new, empty chat): stop any
  // answer still streaming, and show nothing from the previous chat.
  const startSession = useCallback((id) => {
    streamControllerRef.current?.abort();
    streamControllerRef.current = null;
    sessionRef.current += 1;
    const session = sessionRef.current;
    savedRef.current = { session, json: "[]" };
    creatingRef.current = null;
    setBusy(false);
    setError("");
    setChat({ session, id, messages: [], loading: Boolean(id) });
    onSwitchRef.current?.();
    return session;
  }, []);

  // Follow `activeId`. The one change we don't treat as a switch is the id
  // our own first save just minted — that's the same chat gaining an id.
  useEffect(() => {
    if (activeId && activeId === adoptRef.current) {
      adoptRef.current = null;
      return undefined;
    }
    const session = startSession(activeId || null);
    if (!activeId) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`${apiBase}/conversations/${activeId}`);
        if (!response.ok) throw new Error("not found");
        const data = await response.json();
        if (cancelled || sessionRef.current !== session) return;
        const messages = data.messages || [];
        savedRef.current = { session, json: JSON.stringify(messages) };
        setChat((current) => (current.session === session ? { ...current, messages, loading: false } : current));
        if (data.model) setSelectedModel(data.model);
      } catch (_error) {
        if (!cancelled && sessionRef.current === session) onActiveIdChange(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId, apiBase, onActiveIdChange, startSession]);

  // "+ New chat": always a clean slate, even when the current chat is itself
  // a new one that hasn't been saved yet (its id is already null, so the
  // effect above wouldn't fire on its own).
  const newChat = useCallback(() => {
    adoptRef.current = null;
    if (activeId) onActiveIdChange(null);
    else startSession(null);
  }, [activeId, onActiveIdChange, startSession]);

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

  const grouped = useMemo(() => {
    const byProvider = new Map();
    for (const model of models) {
      if (!byProvider.has(model.provider)) byProvider.set(model.provider, []);
      byProvider.get(model.provider).push(model);
    }
    return [...byProvider.entries()];
  }, [models]);

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

  // Persist a finished turn: create the conversation on its first answer,
  // replace it after each later one. Only for the current session, and only
  // one create at a time — a turn that finishes while the create is still in
  // flight is saved by the PUT that follows once the id arrives.
  useEffect(() => {
    const { session, id, messages } = chat;
    if (busy || session !== sessionRef.current || messages.length === 0 || !isComplete(messages)) return;
    if (creatingRef.current === session) return;
    const json = JSON.stringify(messages);
    if (savedRef.current.session === session && savedRef.current.json === json) return;
    const body = JSON.stringify({ messages, model: selectedModel || undefined });
    savedRef.current = { session, json };
    (async () => {
      try {
        if (id) {
          await fetch(`${apiBase}/conversations/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body,
          });
        } else {
          creatingRef.current = session;
          const created = await (
            await fetch(`${apiBase}/conversations`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body,
            })
          ).json();
          if (creatingRef.current === session) creatingRef.current = null;
          // Still on this chat: it just gained an id. Moved on: leave the
          // saved chat in History and don't drag the reader back to it.
          if (created.id && sessionRef.current === session) {
            adoptRef.current = created.id;
            setChat((current) => (current.session === session ? { ...current, id: created.id } : current));
            onActiveIdChange(created.id);
          }
        }
        onConversationsChanged?.();
      } catch (_error) {
        if (creatingRef.current === session) creatingRef.current = null;
        if (savedRef.current.session === session) savedRef.current = { session, json: "[]" }; // retry next turn
      }
    })();
  }, [busy, chat, apiBase, selectedModel, onActiveIdChange, onConversationsChanged]);

  // Change this session's messages; a no-op once the reader has moved on.
  const patchSession = useCallback((session, update) => {
    setChat((current) => (current.session === session ? { ...current, messages: update(current.messages) } : current));
  }, []);

  const patchLast = useCallback(
    (session, mutate) =>
      patchSession(session, (messages) => {
        if (messages.length === 0) return messages;
        const next = messages.slice();
        const last = { ...next[next.length - 1] };
        mutate(last);
        next[next.length - 1] = last;
        return next;
      }),
    [patchSession],
  );

  // `options`: { mode: "quick"|"thorough", topK, minScore (null = server
  // default for the mode), documentId (scope to one document) }.
  const ask = useCallback(
    async (question, options = {}) => {
      const clean = question.trim();
      if (!clean || busy || chat.loading) return false;
      const session = sessionRef.current;
      setBusy(true);
      setError("");

      const history = chat.messages
        .filter((turn) => turn.content && !turn.error)
        .map((turn) => ({ role: turn.role, content: turn.content }));

      patchSession(session, (messages) => [
        ...messages,
        { role: "user", content: clean },
        { role: "assistant", content: "", sources: null, pending: true, model: selectedModel },
      ]);

      const controller = new AbortController();
      streamControllerRef.current = controller;
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
            mode: options.mode || "quick",
            ...(options.topK ? { top_k: options.topK } : {}),
            ...(typeof options.minScore === "number" ? { min_score: options.minScore } : {}),
            ...(options.documentId ? { document_id: options.documentId } : {}),
            ...(Object.keys(providerKeys).length ? { provider_keys: providerKeys } : {}),
          },
          {
            signal: controller.signal,
            onEvent: (evt) => {
              if (evt.type === "token") {
                patchLast(session, (turn) => {
                  turn.content += evt.text;
                });
              } else if (evt.type === "progress") {
                patchLast(session, (turn) => {
                  turn.progress = evt.text;
                });
              } else if (evt.type === "sources") {
                patchLast(session, (turn) => {
                  turn.sources = evt.results || [];
                  turn.lowConfidence = Boolean(evt.low_confidence);
                  turn.usedModel = evt.model;
                  turn.documents = evt.documents;
                  turn.relevantCount = evt.relevant_count;
                  turn.pending = false;
                });
              } else if (evt.type === "error") {
                patchLast(session, (turn) => {
                  turn.error = evt.detail;
                  turn.pending = false;
                });
              }
            },
          },
        );
      } catch (err) {
        // AbortError: the reader switched chats (startSession aborted us).
        if (err.name !== "AbortError" && sessionRef.current === session) {
          setError(err.message);
          patchLast(session, (turn) => {
            turn.error = err.message;
            turn.pending = false;
          });
        }
      } finally {
        if (sessionRef.current === session) {
          setBusy(false);
          patchLast(session, (turn) => {
            turn.pending = false;
          });
        }
        if (streamControllerRef.current === controller) streamControllerRef.current = null;
      }
      return true;
    },
    [apiBase, apiKeys, busy, chat, patchLast, patchSession, selectedModel],
  );

  // Clicking an already-open citation closes it; clicking a different one
  // swaps to it. Only one source shown at a time per turn, kept minimal.
  const toggleCitation = useCallback((turnIndex, n) => {
    setChat((current) => ({
      ...current,
      messages: current.messages.map((turn, i) =>
        i === turnIndex ? { ...turn, openCitation: turn.openCitation === n ? null : n } : turn,
      ),
    }));
  }, []);

  const modelLabel = useCallback((id) => models.find((m) => m.id === id)?.label || id, [models]);

  return {
    conversation: chat.messages,
    loading: chat.loading,
    busy,
    error,
    models,
    grouped,
    noModels: usableModels.length === 0,
    selectedModel,
    setSelectedModel,
    ask,
    newChat,
    toggleCitation,
    modelLabel,
  };
}
