import React, { useCallback, useEffect, useRef, useState } from "react";

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

function breadcrumbs(path) {
  const crumbs = [{ label: "Library", path: "" }];
  if (!path) return crumbs;
  const parts = path.split("/");
  parts.forEach((part, index) => {
    crumbs.push({ label: part, path: parts.slice(0, index + 1).join("/") });
  });
  return crumbs;
}

// A Finder-style walk of the mounted /library volume. Folders and PDFs only;
// importing references the file in place (no copy) and, for a folder, kicks
// off a recursive ingest job. Which folder this opens to by default, and the
// "Set as library" shortcut below, both defer to `libraryRoot` — the actual
// root-designation form lives in Settings now, this just consumes it.
export default function LibraryBrowser({ apiBase, onImported, onJob, libraryRoot, settingRoot, onSetLibraryFolder }) {
  const [path, setPath] = useState(null); // null = not yet resolved to the starting folder
  const [tree, setTree] = useState(null);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState({});
  const openedRef = useRef(false);

  const loadTree = useCallback(
    async (target) => {
      setStatus("loading");
      setError("");
      try {
        const response = await fetch(`${apiBase}/library/tree?path=${encodeURIComponent(target)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
        setTree(data);
        setPath(data.path);
        setStatus("ready");
      } catch (loadError) {
        setError(loadError.message);
        setStatus("error");
      }
    },
    [apiBase],
  );

  // Open straight to the designated library folder once its value has
  // resolved (if one has been set), instead of the top of a possibly much
  // broader mount. Only ever fires once, on that first resolution — a later
  // root change (from Settings) shouldn't yank the user back while browsing.
  useEffect(() => {
    if (openedRef.current || libraryRoot === null) return;
    openedRef.current = true;
    loadTree(libraryRoot || "");
  }, [libraryRoot, loadTree]);

  const importEntry = useCallback(
    async (entryPath) => {
      setBusy((current) => ({ ...current, [entryPath]: true }));
      setError("");
      try {
        const response = await fetch(`${apiBase}/library/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: entryPath }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
        if (data.job_id) onJob?.(data);
        onImported?.();
        return true;
      } catch (importError) {
        setError(importError.message);
        return false;
      } finally {
        setBusy((current) => {
          const next = { ...current };
          delete next[entryPath];
          return next;
        });
      }
    },
    [apiBase, onImported, onJob],
  );

  async function setAsLibrary(entryPath) {
    const result = await onSetLibraryFolder(entryPath);
    if (result?.ok) loadTree(entryPath);
  }

  const entries = tree?.entries || [];
  const dirs = entries.filter((entry) => entry.type === "dir");
  const files = entries.filter((entry) => entry.type === "file");
  const atLibraryRoot = path === libraryRoot;

  return (
    <div className="library-browser">
      {path !== null && !atLibraryRoot && (
        <div className="library-root-bar">
          <button type="button" className="link" disabled={settingRoot} onClick={() => setAsLibrary(path)}>
            Set current folder as library
          </button>
        </div>
      )}

      <nav className="breadcrumb" aria-label="Folder path">
        {breadcrumbs(path || "").map((crumb, index, all) => (
          <React.Fragment key={crumb.path}>
            {index > 0 && <span className="breadcrumb-sep">/</span>}
            {index === all.length - 1 ? (
              <span className="breadcrumb-current">{crumb.label}</span>
            ) : (
              <button type="button" className="link" onClick={() => loadTree(crumb.path)}>
                {crumb.label}
              </button>
            )}
          </React.Fragment>
        ))}
      </nav>

      {error && <div className="status-message error">{error}</div>}

      {status === "loading" && <p className="muted">Loading…</p>}

      {status === "ready" && entries.length === 0 && (
        <p className="muted">This folder has no sub-folders or PDF files.</p>
      )}

      <div className="file-list">
        {dirs.map((entry) => {
          const entryPath = path ? `${path}/${entry.name}` : entry.name;
          return (
            <div className="file-row" key={`dir-${entry.name}`}>
              <button type="button" className="file-name" onClick={() => loadTree(entryPath)}>
                <span className="file-icon">📁</span>
                <span className="file-label">{entry.name}</span>
              </button>
              <span className="file-detail">
                {entry.pdf_count == null ? "—" : `${entry.pdf_count} PDF${entry.pdf_count === 1 ? "" : "s"}`}
              </span>
              <button
                type="button"
                className="link"
                disabled={Boolean(busy[entryPath]) || settingRoot}
                onClick={() => setAsLibrary(entryPath)}
                title="Make this the library folder auto-ingest scans"
              >
                Set as library
              </button>
              <button
                type="button"
                className="secondary"
                disabled={Boolean(busy[entryPath])}
                onClick={() => importEntry(entryPath)}
              >
                {busy[entryPath] ? "Queueing…" : "Import all"}
              </button>
            </div>
          );
        })}

        {files.map((entry) => {
          const entryPath = path ? `${path}/${entry.name}` : entry.name;
          return (
            <div className="file-row" key={`file-${entry.name}`}>
              <span className="file-name static">
                <span className="file-icon">📄</span>
                <span className="file-label">{entry.name}</span>
              </span>
              <span className="file-detail">{formatSize(entry.size)}</span>
              {entry.indexed ? (
                <span className="badge indexed">Indexed</span>
              ) : (
                <button
                  type="button"
                  className="secondary"
                  disabled={Boolean(busy[entryPath])}
                  onClick={() => importEntry(entryPath)}
                >
                  {busy[entryPath] ? "Importing…" : "Import"}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
