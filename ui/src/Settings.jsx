import React, { useEffect, useRef, useState } from "react";

import { CLOUD_PROVIDERS } from "./AskPanel.jsx";

// A single popover for the handful of things that are configured once and
// then forgotten, rather than touched every session: theme, cloud API keys,
// and which folder auto-ingest watches. Closes on Escape or an outside click.
export default function Settings({
  theme,
  onThemeChange,
  apiKeys,
  setApiKeys,
  ollamaModels,
  setOllamaModels,
  libraryRoot,
  hostPath,
  settingRoot,
  rootNotice,
  rootError,
  onSetLibraryFolder,
  onClose,
}) {
  const popoverRef = useRef(null);
  const [pathInput, setPathInput] = useState(() =>
    libraryRoot ? (hostPath ? `${hostPath}/${libraryRoot}` : libraryRoot) : "",
  );
  const [keyDraft, setKeyDraft] = useState({});
  const [ollamaDraft, setOllamaDraft] = useState("");

  useEffect(() => {
    setPathInput(libraryRoot ? (hostPath ? `${hostPath}/${libraryRoot}` : libraryRoot) : "");
  }, [libraryRoot, hostPath]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    const onClick = (event) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target)) onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [onClose]);

  function saveKey(providerId) {
    const value = (keyDraft[providerId] || "").trim();
    if (!value) return;
    setApiKeys({ ...apiKeys, [providerId]: value });
    setKeyDraft((d) => ({ ...d, [providerId]: "" }));
  }

  function removeKey(providerId) {
    const next = { ...apiKeys };
    delete next[providerId];
    setApiKeys(next);
  }

  function addOllamaModel() {
    const name = ollamaDraft.trim();
    if (!name || ollamaModels.includes(name)) return;
    setOllamaModels([...ollamaModels, name]);
    setOllamaDraft("");
  }

  function removeOllamaModel(name) {
    setOllamaModels(ollamaModels.filter((existing) => existing !== name));
  }

  return (
    <div className="settings-popover" ref={popoverRef} role="dialog" aria-label="Settings">
      <div className="settings-head">
        <strong>Settings</strong>
        <button type="button" className="link" onClick={onClose}>
          Done
        </button>
      </div>

      <section className="settings-section">
        <h3>Appearance</h3>
        <div className="settings-theme-row">
          <button
            type="button"
            className={`settings-theme-option${theme === "light" ? " active" : ""}`}
            onClick={() => onThemeChange("light")}
          >
            ☼ Light
          </button>
          <button
            type="button"
            className={`settings-theme-option${theme === "dark" ? " active" : ""}`}
            onClick={() => onThemeChange("dark")}
          >
            ☾ Dark
          </button>
        </div>
      </section>

      <section className="settings-section">
        <h3>Library folder</h3>
        <p className="settings-note">
          Which folder auto-ingest scans, and where Browse opens by default. Recursively imports
          every PDF underneath, in place — nothing is copied.
        </p>
        <form
          className="field-row"
          onSubmit={(event) => {
            event.preventDefault();
            if (!settingRoot) onSetLibraryFolder(pathInput.trim());
          }}
        >
          <input
            value={pathInput}
            onChange={(event) => setPathInput(event.target.value)}
            placeholder={hostPath ? `${hostPath}/Books` : "Books/PDFs"}
            disabled={settingRoot}
          />
          <button className="primary" type="submit" disabled={settingRoot}>
            {settingRoot ? "Setting…" : "Set"}
          </button>
        </form>
        <p className="settings-note">
          Must be inside {hostPath ? <strong>{hostPath}</strong> : "the folder mounted at /library"}{" "}
          — that's the only part of this server's disk the app is allowed to see. A different
          folder needs its mount widened in <code>.env</code> (<code>LIBRARY_PATH</code>) and a
          restart first.
        </p>
        <p className="settings-note">
          Currently: <strong>{libraryRoot ? `/${libraryRoot}` : "the whole mounted folder"}</strong>
          {libraryRoot && (
            <>
              {" · "}
              <button type="button" className="link" onClick={() => onSetLibraryFolder("")}>
                reset to whole mount
              </button>
            </>
          )}
        </p>
        {rootError && <div className="status-message error">{rootError}</div>}
        {rootNotice && !rootError && <div className="status-message">{rootNotice}</div>}
      </section>

      <section className="settings-section">
        <h3>Local (Ollama)</h3>
        <p className="settings-note">
          Models already pulled on the server's Ollama show up automatically. Add one here if
          you've pulled a model there that hasn't been picked up yet, or want to name one directly.
        </p>
        <form
          className="field-row"
          onSubmit={(event) => {
            event.preventDefault();
            addOllamaModel();
          }}
        >
          <input
            value={ollamaDraft}
            onChange={(event) => setOllamaDraft(event.target.value)}
            placeholder="e.g. llama3.1:8b"
          />
          <button className="primary" type="submit" disabled={!ollamaDraft.trim()}>
            Add
          </button>
        </form>
        {ollamaModels.length > 0 && (
          <ul className="settings-model-list">
            {ollamaModels.map((name) => (
              <li key={name}>
                <span>{name}</span>
                <button type="button" className="link" onClick={() => removeOllamaModel(name)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="settings-section">
        <h3>Cloud API keys</h3>
        <p className="settings-note">
          Keys stay in this browser (localStorage) and are sent only with each Ask question. They
          are never stored on the server.
        </p>
        {CLOUD_PROVIDERS.map((provider) => (
          <div className="ask-keys-row" key={provider.id}>
            <span className="ask-keys-label">{provider.label}</span>
            <input
              type="password"
              autoComplete="off"
              placeholder={apiKeys[provider.id] ? "•••••• saved" : provider.placeholder}
              value={keyDraft[provider.id] || ""}
              onChange={(event) => setKeyDraft((d) => ({ ...d, [provider.id]: event.target.value }))}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  saveKey(provider.id);
                }
              }}
            />
            <button type="button" className="secondary" onClick={() => saveKey(provider.id)}>
              Save
            </button>
            {apiKeys[provider.id] && (
              <button type="button" className="link" onClick={() => removeKey(provider.id)}>
                Remove
              </button>
            )}
          </div>
        ))}
      </section>
    </div>
  );
}
