import React, { useCallback, useEffect, useState } from "react";

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
// off a recursive ingest job. The mount itself can be broad (a whole home
// directory) — "Set as library folder" narrows which subfolder auto-ingest
// scans and where this browser opens by default, entirely from here, with no
// docker-compose/.env edit.
export default function LibraryBrowser({ apiBase, onImported, onJob }) {
  const [path, setPath] = useState(null); // null = not yet resolved to the starting folder
  const [libraryRoot, setLibraryRootState] = useState("");
  const [hostPath, setHostPath] = useState("");
  const [pathInput, setPathInput] = useState("");
  const [settingRoot, setSettingRoot] = useState(false);
  const [tree, setTree] = useState(null);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState({});

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

  // On first mount, open straight to the designated library folder (if one
  // has been set) instead of the top of a possibly much broader mount.
  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/library/root`)
      .then((response) => response.json())
      .then((data) => {
        if (cancelled) return;
        setLibraryRootState(data.path || "");
        setHostPath(data.host_path || "");
        setPathInput(data.path ? (data.host_path ? `${data.host_path}/${data.path}` : data.path) : "");
        loadTree(data.path || "");
      })
      .catch(() => loadTree(""));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase]);

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

  const setLibraryFolder = useCallback(
    async (targetPath) => {
      setNotice("");
      setError("");
      setSettingRoot(true);
      try {
        const response = await fetch(`${apiBase}/library/root`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: targetPath }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
        setLibraryRootState(data.path || "");
        setPathInput(data.path ? (hostPath ? `${hostPath}/${data.path}` : data.path) : "");
        setNotice(data.path ? `Library folder set to “${data.path}”.` : "Library folder reset to the whole mount.");
        await importEntry(data.path || ".");
        loadTree(data.path || "");
      } catch (rootError) {
        setError(rootError.message);
      } finally {
        setSettingRoot(false);
      }
    },
    [apiBase, hostPath, importEntry, loadTree],
  );

  const entries = tree?.entries || [];
  const dirs = entries.filter((entry) => entry.type === "dir");
  const files = entries.filter((entry) => entry.type === "file");
  const atLibraryRoot = path === libraryRoot;

  return (
    <div className="library-browser">
      <form
        className="library-path-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!settingRoot) setLibraryFolder(pathInput.trim());
        }}
      >
        <label htmlFor="library-path-input">Library path</label>
        <div className="field-row">
          <input
            id="library-path-input"
            value={pathInput}
            onChange={(event) => setPathInput(event.target.value)}
            placeholder={hostPath ? `${hostPath}/Books` : "Books/PDFs"}
            disabled={settingRoot}
          />
          <button className="primary" type="submit" disabled={settingRoot}>
            {settingRoot ? "Setting…" : "Set"}
          </button>
        </div>
        <p className="muted">
          {hostPath
            ? `Paste the folder's exact path as shown on this server (e.g. ${hostPath}/Books), or a path relative to it.`
            : "A path relative to the mounted library folder."}{" "}
          Currently: <strong>{libraryRoot ? `/${libraryRoot}` : "the whole mounted folder"}</strong>
          {libraryRoot && (
            <>
              {" · "}
              <button type="button" className="link" onClick={() => setLibraryFolder("")}>
                reset to whole mount
              </button>
            </>
          )}
        </p>
      </form>

      {path !== null && !atLibraryRoot && (
        <div className="library-root-bar">
          <button type="button" className="link" onClick={() => setLibraryFolder(path)}>
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
      {notice && !error && <div className="status-message">{notice}</div>}

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
                disabled={Boolean(busy[entryPath])}
                onClick={() => setLibraryFolder(entryPath)}
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
