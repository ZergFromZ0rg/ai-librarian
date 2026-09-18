import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import Cover from "./Cover.jsx";
import { CoverFlags, MarkControls } from "./DocMarks.jsx";
import { displayTitle, loadStored, saveStored } from "./storage.js";

const VIEW_KEY = "ai-librarian.stacks.view";

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

function StatusBadge({ status }) {
  return <span className={`badge badge-${status}`}>{status}</span>;
}

// A Finder-style walk of the mounted /library volume, shown as shelves of
// covers. Folders and PDFs only; importing references the file in place (no
// copy) and, for a folder, kicks off a recursive ingest job. It opens on
// `libraryRoot` (the folder auto-ingest watches) rather than the top of a
// possibly much broader mount.
function Shelves({ apiBase, documents, libraryRoot, settingRoot, onSetLibraryFolder, onImported, onJob, onOpenDocument, view }) {
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

  // Only ever fires once, on the root's first resolution — a later root
  // change (from Settings) shouldn't yank the reader back while browsing.
  useEffect(() => {
    if (openedRef.current || libraryRoot === null) return;
    openedRef.current = true;
    loadTree(libraryRoot || "");
  }, [libraryRoot, loadTree]);

  // A file's "indexed" flag comes from the tree listing, which goes stale as
  // imports finish; refresh this folder quietly whenever the catalogue moves.
  const indexedCount = documents.filter((doc) => doc.indexing_status === "indexed").length;
  useEffect(() => {
    if (path === null) return;
    fetch(`${apiBase}/library/tree?path=${encodeURIComponent(path)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (data && data.path === path) setTree(data);
      })
      .catch(() => {});
  }, [indexedCount, documents.length]);

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
      } catch (importError) {
        setError(importError.message);
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

  const byId = useMemo(() => new Map(documents.map((doc) => [doc.document_id, doc])), [documents]);
  const entries = tree?.entries || [];
  const dirs = entries.filter((entry) => entry.type === "dir");
  const files = entries.filter((entry) => entry.type === "file");
  const atLibraryRoot = path === libraryRoot;
  const joinPath = (name) => (path ? `${path}/${name}` : name);

  return (
    <div className="shelves">
      <div className="shelves-bar">
        <nav className="breadcrumb" aria-label="Folder path">
          {breadcrumbs(path || "").map((crumb, index, all) => (
            <React.Fragment key={crumb.path}>
              {index > 0 && <span className="breadcrumb-sep">/</span>}
              {index === all.length - 1 ? (
                <span className="breadcrumb-current">{crumb.label}</span>
              ) : (
                <button type="button" className="text-button" onClick={() => loadTree(crumb.path)}>
                  {crumb.label}
                </button>
              )}
            </React.Fragment>
          ))}
        </nav>
        <div className="shelves-bar-actions">
          {path !== null && !atLibraryRoot && (
            <button type="button" className="text-button" disabled={settingRoot} onClick={() => setAsLibrary(path)} title="Make this the folder auto-ingest watches">
              Set as library folder
            </button>
          )}
          {path !== null && files.some((file) => !file.indexed) && (
            <button type="button" className="button" disabled={Boolean(busy[path || "."])} onClick={() => importEntry(path || ".")}>
              {busy[path || "."] ? "Queueing…" : "Import this folder"}
            </button>
          )}
        </div>
      </div>

      {error && <div className="notice error">{error}</div>}
      {status === "loading" && !tree && <p className="muted">Loading…</p>}
      {status === "ready" && entries.length === 0 && <p className="muted">This folder has no sub-folders or PDF files.</p>}

      {dirs.length > 0 && (
        <div className="folders">
          {dirs.map((entry) => {
            const entryPath = joinPath(entry.name);
            return (
              <div className="folder" key={`dir-${entry.name}`}>
                <button type="button" className="folder-open" onClick={() => loadTree(entryPath)}>
                  <span className="folder-tab" aria-hidden="true" />
                  <span className="folder-name">{entry.name}</span>
                  <span className="folder-count">
                    {entry.pdf_count == null ? "—" : `${entry.pdf_count} PDF${entry.pdf_count === 1 ? "" : "s"}`}
                  </span>
                </button>
                <div className="folder-actions">
                  <button type="button" className="text-button" disabled={Boolean(busy[entryPath])} onClick={() => importEntry(entryPath)}>
                    {busy[entryPath] ? "Queueing…" : "Import all"}
                  </button>
                  <button type="button" className="text-button" disabled={settingRoot} onClick={() => setAsLibrary(entryPath)}>
                    Set as library
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {files.length > 0 && (
        <div className={view === "grid" ? "cover-grid" : "file-rows"}>
          {files.map((entry) => {
            const entryPath = joinPath(entry.name);
            const doc = entry.document_id ? byId.get(entry.document_id) : null;
            const openable = Boolean(doc);
            const action = entry.indexed ? null : (
              <button type="button" className="button" disabled={Boolean(busy[entryPath])} onClick={() => importEntry(entryPath)}>
                {busy[entryPath] ? "Importing…" : "Import"}
              </button>
            );
            if (view === "grid") {
              return (
                <div className={`cover-card${openable ? "" : " unindexed"}`} key={`file-${entry.name}`}>
                  <button type="button" className="cover-card-open" disabled={!openable} onClick={() => onOpenDocument(doc)} title={entry.name}>
                    <Cover apiBase={apiBase} documentId={doc ? doc.document_id : null} filename={entry.name} width={320}>
                      {doc && <CoverFlags doc={doc} />}
                    </Cover>
                  </button>
                  <div className="cover-card-meta">
                    <span className="cover-card-title" title={entry.name}>{displayTitle(entry.name)}</span>
                    <span className="cover-card-detail">
                      {doc ? <StatusBadge status={doc.indexing_status} /> : <span>not in library</span>}
                      <span>{doc?.pages ? `${doc.pages} pp.` : formatSize(entry.size)}</span>
                    </span>
                    {action}
                  </div>
                </div>
              );
            }
            return (
              <div className="file-row" key={`file-${entry.name}`}>
                <button type="button" className="file-row-open" disabled={!openable} onClick={() => onOpenDocument(doc)}>
                  <Cover apiBase={apiBase} documentId={doc ? doc.document_id : null} filename={entry.name} width={160} className="cover-mini" />
                  <span className="file-row-name" title={entry.name}>{displayTitle(entry.name)}</span>
                </button>
                <span className="file-row-detail">{doc?.pages ? `${doc.pages} pp.` : ""}</span>
                <span className="file-row-detail">{formatSize(entry.size)}</span>
                {doc ? <StatusBadge status={doc.indexing_status} /> : action}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Every indexed document (uploads included, which have no folder), as a
// table: status, size, where it came from, and the maintenance actions.
function Catalogue({ apiBase, documents, reindexing, onReindex, onRemove, onOpenDocument, onScope, onPatch }) {
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("recent");
  const [selected, setSelected] = useState(() => new Set());
  const [openNotes, setOpenNotes] = useState(null);

  const needle = filter.trim().toLowerCase();
  const rows = useMemo(() => {
    const list = documents.filter(
      (doc) => !needle || doc.filename.toLowerCase().includes(needle) || (doc.source_path || "").toLowerCase().includes(needle),
    );
    if (sort === "name") list.sort((a, b) => a.filename.localeCompare(b.filename));
    else if (sort === "status") list.sort((a, b) => a.indexing_status.localeCompare(b.indexing_status));
    else list.sort((a, b) => String(b.indexed_at || b.updated_at || "").localeCompare(String(a.indexed_at || a.updated_at || "")));
    return list;
  }, [documents, needle, sort]);

  // Drop selections for documents that have since been removed.
  useEffect(() => {
    setSelected((prev) => {
      const ids = new Set(documents.map((doc) => doc.document_id));
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [documents]);

  const toggle = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (documents.length === 0) {
    return <p className="muted">Nothing indexed yet. Drop PDFs anywhere on the page, or import a folder from Folders.</p>;
  }

  const allSelected = rows.length > 0 && rows.every((doc) => selected.has(doc.document_id));

  return (
    <div className="catalogue">
      <div className="catalogue-bar">
        <input
          type="search"
          className="input"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filter by title or path…"
          aria-label="Filter catalogue"
        />
        <select className="input" value={sort} onChange={(event) => setSort(event.target.value)} aria-label="Sort">
          <option value="recent">Recently indexed</option>
          <option value="name">Title</option>
          <option value="status">Status</option>
        </select>
        <span className="catalogue-bar-spacer" />
        <button
          type="button"
          className="text-button"
          disabled={reindexing || selected.size === 0}
          onClick={() => {
            onReindex([...selected]);
            setSelected(new Set());
          }}
        >
          Reindex selected{selected.size ? ` (${selected.size})` : ""}
        </button>
        <button
          type="button"
          className="text-button"
          disabled={reindexing}
          onClick={() => onReindex(documents.map((doc) => doc.document_id))}
          title="Re-extracts and re-chunks every document from its source PDF — worth doing after an extraction-quality fix."
        >
          Reindex all
        </button>
      </div>

      <table className="catalogue-table">
        <thead>
          <tr>
            <th className="col-check">
              <input
                type="checkbox"
                aria-label="Select all"
                checked={allSelected}
                ref={(el) => {
                  if (el) el.indeterminate = selected.size > 0 && !allSelected;
                }}
                onChange={(event) => setSelected(event.target.checked ? new Set(rows.map((doc) => doc.document_id)) : new Set())}
              />
            </th>
            <th>Title</th>
            <th>Status</th>
            <th>Marks</th>
            <th className="num">Pages</th>
            <th className="num">Passages</th>
            <th className="col-actions" aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {rows.map((doc) => {
            const hasError = Boolean(doc.indexing_error);
            const hasNotes = Boolean(doc.extraction_notes);
            const notesOpen = openNotes === doc.document_id;
            return (
              <React.Fragment key={doc.document_id}>
                <tr className={selected.has(doc.document_id) ? "selected" : ""}>
                  <td className="col-check">
                    <input
                      type="checkbox"
                      checked={selected.has(doc.document_id)}
                      onChange={() => toggle(doc.document_id)}
                      aria-label={`Select ${doc.filename}`}
                    />
                  </td>
                  <td>
                    <button type="button" className="catalogue-title" onClick={() => onOpenDocument(doc)} title={doc.filename}>
                      <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.filename} width={160} className="cover-mini" />
                      <span>
                        <span className="catalogue-name">{displayTitle(doc.filename)}</span>
                        <span className="catalogue-path">{doc.source_path ? `↪ ${doc.source_path}` : "uploaded"}</span>
                      </span>
                    </button>
                  </td>
                  <td>
                    <StatusBadge status={doc.indexing_status} />
                    {(hasError || hasNotes) && (
                      <button
                        type="button"
                        className={`notes-toggle${hasError ? " error" : ""}`}
                        onClick={() => setOpenNotes(notesOpen ? null : doc.document_id)}
                        aria-expanded={notesOpen}
                      >
                        {hasError ? "error" : "notes"} {notesOpen ? "▴" : "▾"}
                      </button>
                    )}
                  </td>
                  <td>
                    <MarkControls doc={doc} compact onPatch={(changes) => onPatch(doc.document_id, changes)} />
                  </td>
                  <td className="num mono">{doc.pages ?? "—"}</td>
                  <td className="num mono">{doc.chunks ?? "—"}</td>
                  <td className="col-actions">
                    {doc.indexing_status === "indexed" && (
                      <button type="button" className="text-button" onClick={() => onScope(doc)}>
                        Search within
                      </button>
                    )}
                    {doc.indexing_status === "error" && (
                      <button type="button" className="text-button" disabled={reindexing} onClick={() => onReindex([doc.document_id])}>
                        Retry
                      </button>
                    )}
                    <button type="button" className="text-button danger" onClick={() => onRemove(doc)}>
                      Remove
                    </button>
                  </td>
                </tr>
                {notesOpen && (
                  <tr className="notes-row">
                    <td />
                    <td colSpan={6}>
                      {hasError && <div className="notice error">{doc.indexing_error}</div>}
                      {hasNotes && <div className="notice">{doc.extraction_notes}</div>}
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
      {rows.length === 0 && <p className="muted">No documents match “{filter}”.</p>}
    </div>
  );
}

// The library below the console — the folders on disk and the index of
// everything already processed — plus the ways in (upload,
// rescan, import) and the progress of whatever is being processed.
export default function Stacks(props) {
  const { apiBase, documents, onUpload, uploading, onRescan, attaching, notice, job, sectionRef } = props;
  const [tab, setTab] = useState("shelves");
  const [view, setView] = useState(() => (loadStored(VIEW_KEY, "grid") === "list" ? "list" : "grid"));
  const fileInput = useRef(null);

  useEffect(() => saveStored(VIEW_KEY, view), [view]);

  const counts = useMemo(() => {
    const out = { indexed: 0, pending: 0, error: 0, passages: 0 };
    for (const doc of documents) {
      if (doc.indexing_status === "indexed") out.indexed += 1;
      else if (doc.indexing_status === "error") out.error += 1;
      else out.pending += 1;
      out.passages += doc.chunks || 0;
    }
    return out;
  }, [documents]);

  const jobProgress = (() => {
    if (!job?.files?.length || ["done", "partial", "error", "interrupted"].includes(job.state)) return null;
    const done = job.files.filter((file) => ["indexed", "duplicate"].includes(file.status)).length;
    return { done, total: job.files.length, percent: Math.round((done / job.files.length) * 100) };
  })();

  return (
    <section className="stacks" ref={sectionRef} id="stacks">
      <header className="stacks-head">
        <div>
          <h2>Library</h2>
          <p className="stacks-stats">
            <span><strong>{counts.indexed}</strong> indexed</span>
            {counts.pending > 0 && <span><strong>{counts.pending}</strong> in progress</span>}
            {counts.error > 0 && <span className="danger-text"><strong>{counts.error}</strong> failed</span>}
            <span><strong>{counts.passages.toLocaleString()}</strong> passages</span>
          </p>
        </div>
        <div className="stacks-head-actions">
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept="application/pdf,.pdf"
            onChange={(event) => {
              onUpload(event.target.files);
              event.target.value = "";
            }}
          />
          <button type="button" className="text-button" disabled={attaching} onClick={onRescan} title="Auto-ingest already scans the library folder on its own — this just forces it now.">
            {attaching ? "Rescanning…" : "Rescan"}
          </button>
          <button type="button" className="button primary" disabled={uploading} onClick={() => fileInput.current?.click()}>
            {uploading ? "Uploading…" : "Upload PDFs"}
          </button>
        </div>
      </header>

      {(notice?.text || jobProgress) && (
        <div className="stacks-status">
          {notice?.text && <div className={`notice ${notice.tone !== "neutral" ? notice.tone : ""}`}>{notice.text}</div>}
          {jobProgress && (
            <div className="progress">
              <div className="progress-label">
                <span>{jobProgress.done}/{jobProgress.total} files ready</span>
                <span className="mono">{jobProgress.percent}%</span>
              </div>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${jobProgress.percent}%` }} />
              </div>
            </div>
          )}
        </div>
      )}

      <div className="stacks-tabs">
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "shelves"} className={tab === "shelves" ? "active" : ""} onClick={() => setTab("shelves")}>
            Folders
          </button>
          <button type="button" role="tab" aria-selected={tab === "catalogue"} className={tab === "catalogue" ? "active" : ""} onClick={() => setTab("catalogue")}>
            Index <span className="tab-count">{documents.length}</span>
          </button>
        </div>
        {tab === "shelves" && (
          <div className="segmented small" role="radiogroup" aria-label="Layout">
            <button type="button" role="radio" aria-checked={view === "grid"} className={view === "grid" ? "active" : ""} onClick={() => setView("grid")}>
              Covers
            </button>
            <button type="button" role="radio" aria-checked={view === "list"} className={view === "list" ? "active" : ""} onClick={() => setView("list")}>
              List
            </button>
          </div>
        )}
      </div>

      {tab === "shelves" ? <Shelves {...props} view={view} /> : <Catalogue {...props} />}
    </section>
  );
}
