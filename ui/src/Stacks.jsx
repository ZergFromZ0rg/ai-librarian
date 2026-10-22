import React, { useEffect, useMemo, useRef, useState } from "react";

import ActionProgress from "./ActionProgress.jsx";
import Cover from "./Cover.jsx";
import { starText } from "./ratings.js";
import { MarkControls } from "./DocMarks.jsx";
import IndexActivity from "./IndexActivity.jsx";
import LibraryExplore from "./LibraryExplore.jsx";
import { libraryBooks, shelfList } from "./libraryExplore.js";
import LibraryStats from "./LibraryStats.jsx";
import NotesPanel from "./NotesPanel.jsx";
import VocabularyPanel from "./VocabularyPanel.jsx";
import { ingestActionProgress, isActiveIngestJob, reindexActionProgress } from "./actionProgress.js";
import { displayTitle } from "./storage.js";

function StatusBadge({ status }) {
  return <span className={`badge badge-${status}`}>{status}</span>;
}

function CollectionBar({ collections, activeId, onSelect, onCreate, onUpdate, onDelete, busy, scan }) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [autoScan, setAutoScan] = useState(true);
  const [managing, setManaging] = useState(false);
  const active = collections.find((collection) => collection.id === activeId);

  async function submit(event) {
    event.preventDefault();
    if (!name.trim() || !path.trim()) return;
    const created = await onCreate({ name: name.trim(), path: path.trim(), autoScan });
    if (created) {
      onSelect(created.id);
      setName("");
      setPath("");
      setAutoScan(true);
      setAdding(false);
    }
  }

  // Closed, collections are one slim row of chips so the books stay the first
  // thing on the page; "Manage" opens the folder details, pause/resume, add
  // and remove.
  return (
    <section className={`collection-set${managing ? " managing" : ""}`} aria-label="Collections">
      <div className="collection-chips" role="group" aria-label="Choose collection">
        <span className="eyebrow">Collections</span>
        {collections.map((collection) => (
          <button
            key={collection.id}
            type="button"
            className={`collection-chip${collection.id === activeId ? " active" : ""}`}
            aria-pressed={collection.id === activeId}
            onClick={() => onSelect(collection.id)}
            title={`/${collection.path || ""} · ${collection.auto_scan ? "watching" : "paused"}`}
          >
            <span className={`collection-dot${collection.auto_scan ? " on" : ""}`} aria-hidden="true" />
            {collection.name}
            <span className="collection-chip-count" title="Books in this collection (papers and documents are in Documents)">{collection.book_count ?? collection.document_count ?? 0}</span>
          </button>
        ))}
        <span className="collection-tools">
          {scan.progress ? (
            <ActionProgress progress={scan.progress} compact />
          ) : (
            <button type="button" className="text-button" disabled={scan.disabled} onClick={scan.onScan} title="Scan the selected collection now.">
              {scan.starting ? "Starting scan…" : "Scan now"}
            </button>
          )}
          <button type="button" className="text-button" aria-expanded={managing} onClick={() => { setManaging((open) => !open); if (managing) setAdding(false); }}>
            {managing ? "Done" : "Manage"}
          </button>
        </span>
      </div>
      {managing && <>
      <div className="collection-set-head">
        <p>Keep books, work, and reference folders distinct without moving a file.</p>
        <div className="collection-set-actions">
          {active && !active.legacy && (
            <button type="button" className="text-button danger" onClick={() => onDelete(active)} title="Stop tracking this folder; its documents stay indexed">
              Remove “{active.name}”
            </button>
          )}
          <button type="button" className="button" onClick={() => setAdding((open) => !open)}>
            {adding ? "Close" : "+ Add collection"}
          </button>
        </div>
      </div>
      <div className="collection-cards" role="group" aria-label="Collection details">
        {collections.map((collection) => (
          <div key={collection.id} className={`collection-card${collection.id === activeId ? " active" : ""}`}>
            <button
              type="button"
              aria-pressed={collection.id === activeId}
              className="collection-card-select"
              onClick={() => onSelect(collection.id)}
            >
              <span className="collection-card-name">{collection.name}</span>
              <span className="collection-card-path">/{collection.path || ""}</span>
              <span className="collection-card-meta">
                {collection.document_count || 0} indexed · {collection.auto_scan ? "watching" : "paused"}
              </span>
            </button>
            {!collection.legacy && (
              <button
                type="button"
                aria-pressed={collection.auto_scan}
                aria-label={collection.auto_scan ? `Pause scanning ${collection.name}` : `Resume scanning ${collection.name}`}
                className="collection-watch"
                onClick={() => onUpdate(collection, { auto_scan: !collection.auto_scan })}
                title={collection.auto_scan ? "Pause passive scanning" : "Resume passive scanning"}
              >
                {collection.auto_scan ? "●" : "○"}
              </button>
            )}
          </div>
        ))}
      </div>
      {adding && (
        <form className="collection-form" onSubmit={submit}>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Name, e.g. Books" aria-label="Collection name" />
          <input value={path} onChange={(event) => setPath(event.target.value)} placeholder="Folder inside the mounted library" aria-label="Collection folder" />
          <label className="collection-auto"><input type="checkbox" checked={autoScan} onChange={(event) => setAutoScan(event.target.checked)} /> Watch passively</label>
          <button className="button primary" type="submit" disabled={busy || !name.trim() || !path.trim()}>{busy ? "Adding…" : "Add & index"}</button>
        </form>
      )}
      </>}
    </section>
  );
}

// Every indexed document (uploads included, which have no folder), as a
// table: status, size, where it came from, and the maintenance actions.
function Catalogue({ apiBase, documents, counts, reindexing, reindexProgress, onReindex, onRemove, onOpenDocument, onScope, onPatch, onApproveOcr }) {
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("recent");
  const [selected, setSelected] = useState(() => new Set());
  const [openNotes, setOpenNotes] = useState(null);

  const needle = filter.trim().toLowerCase();
  const rows = useMemo(() => {
    const list = documents.filter(
      (doc) => !needle || [doc.title, doc.author, doc.subject, doc.filename].some((value) => (value || "").toLowerCase().includes(needle)) || (doc.source_path || "").toLowerCase().includes(needle),
    );
    if (sort === "name") list.sort((a, b) => (a.title || a.filename).localeCompare(b.title || b.filename));
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
  const reindexStatus = reindexActionProgress(reindexProgress);

  return (
    <div className="catalogue">
      <p className="index-summary">
        <span><strong>{counts.indexed.toLocaleString()}</strong> indexed</span>
        {counts.catalogued > 0 && <span><strong>{counts.catalogued.toLocaleString()}</strong> catalogue-only</span>}
        {counts.pending > 0 && <span><strong>{counts.pending.toLocaleString()}</strong> in progress</span>}
        {counts.error > 0 && <span className="danger-text"><strong>{counts.error.toLocaleString()}</strong> failed</span>}
        <span><strong>{counts.passages.toLocaleString()}</strong> passages</span>
      </p>
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
        {reindexStatus ? (
          <ActionProgress progress={reindexStatus} compact />
        ) : (
          <>
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
          </>
        )}
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
            // A large scan parked until the reader signs off on its OCR run.
            const awaitingOcr = doc.indexing_status === "error" && doc.ocr_pages > 0 && !doc.ocr_approved;
            const hasError = Boolean(doc.indexing_error);
            const hasNotes = Boolean(doc.extraction_notes);
            const notesOpen = openNotes === doc.document_id;
            const retrying = reindexing && reindexProgress?.requestedIds?.includes(doc.document_id);
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
                      <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.filename} fileType={doc.file_type} width={160} className="cover-mini" />
                      <span>
                        <span className="catalogue-name">{doc.title || displayTitle(doc.filename)}</span>
                        <span className="catalogue-book-meta">{[doc.file_type?.toUpperCase(), doc.rating ? starText(doc.rating) : null, doc.owned == null ? null : doc.owned ? "Owned" : "Not owned"].filter(Boolean).join(" · ")}</span>
                        <span className="catalogue-path">{doc.source_path ? `↪ ${doc.source_path}` : "uploaded"}</span>
                      </span>
                    </button>
                  </td>
                  <td>
                    {awaitingOcr ? (
                      <span className="badge badge-awaiting" title={doc.indexing_error}>needs OCR</span>
                    ) : (
                      <StatusBadge status={doc.indexing_status} />
                    )}
                    {(hasError || hasNotes) && (
                      <button
                        type="button"
                        className={`notes-toggle${hasError ? " error" : ""}`}
                        onClick={() => setOpenNotes(notesOpen ? null : doc.document_id)}
                        aria-expanded={notesOpen}
                      >
                        {awaitingOcr ? "why" : hasError ? "error" : "notes"} {notesOpen ? "▴" : "▾"}
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
                    {awaitingOcr ? (
                      <button type="button" className="text-button accent" onClick={() => onApproveOcr(doc)} title={doc.indexing_error}>
                        Approve OCR ({doc.ocr_pages} pp.)
                      </button>
                    ) : (
                      doc.indexing_status === "error" && (retrying ? (
                        <span className="action-tip" role="status">Retrying…</span>
                      ) : (
                        <button type="button" className="text-button" disabled={reindexing} onClick={() => onReindex([doc.document_id])}>
                          Retry
                        </button>
                      ))
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
                      {hasError && <div className={`notice${awaitingOcr ? "" : " error"}`}>{doc.indexing_error}</div>}
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

// The library: the books you have browse and filter, your notes, vocabulary and
// statistics, and the index of everything already processed — plus the ways in
// (upload, rescan) and the progress of whatever is being processed.
export default function Stacks(props) {
  const { apiBase, documents, documentsLoaded = true, collections: allCollections = [], onUpload, uploading, intakeCount = 0, onReviewUploads, onRescan, onCreateCollection, onUpdateCollection, onDeleteCollection, onImported, onOpenDocument, scanStarting, collectionBusy, notice, job, sectionRef } = props;
  // Built-in libraries (Notes) have no folder to scan, rename or remove.
  const collections = useMemo(() => allCollections.filter((collection) => !collection.builtin), [allCollections]);
  const noteCount = allCollections.find((collection) => collection.builtin)?.document_count || 0;
  const [tab, setTab] = useState("browse");
  const [browsePreset, setBrowsePreset] = useState(null);
  const [activeCollectionId, setActiveCollectionId] = useState("");
  const fileInput = useRef(null);
  const sourceDocuments = useMemo(
    () => documents.filter((document) => document.record_type !== "standalone"),
    [documents],
  );

  useEffect(() => {
    if (!collections.length) return;
    if (!collections.some((collection) => collection.id === activeCollectionId)) {
      setActiveCollectionId(collections[0].id);
    }
  }, [activeCollectionId, collections]);

  const activeCollection = collections.find((collection) => collection.id === activeCollectionId) || collections[0] || null;

  const counts = useMemo(() => {
    const out = { indexed: 0, pending: 0, error: 0, catalogued: 0, passages: 0 };
    for (const doc of documents) {
      if (doc.indexing_status === "indexed") out.indexed += 1;
      else if (doc.indexing_status === "error") out.error += 1;
      else if (doc.indexing_status === "catalogued") out.catalogued += 1;
      else out.pending += 1;
      out.passages += doc.chunks || 0;
    }
    return out;
  }, [documents]);

  const books = useMemo(() => libraryBooks(documents).length, [documents]);
  const shelves = useMemo(() => shelfList(documents).shelves.length, [documents]);
  const jobProgress = ingestActionProgress(job);
  const scanProgress = job?.uiOrigin === "scan" ? jobProgress : null;

  return (
    <section className="stacks" ref={sectionRef} id="stacks">
      <header className="stacks-head">
        <div className="stacks-title">
          <div className="stacks-title-row">
            <h2>Library</h2>
            {(counts.pending > 0 || counts.error > 0) && (
              <button type="button" className={`index-pill${counts.error > 0 ? " attention" : ""}`} onClick={() => setTab("activity")} title="See what is being indexed, and what failed">
                <span className="index-pill-dot" aria-hidden="true" />
                {[counts.pending > 0 ? `${counts.pending.toLocaleString()} indexing` : null, counts.error > 0 ? `${counts.error.toLocaleString()} need attention` : null].filter(Boolean).join(" · ")}
              </button>
            )}
          </div>
          <p className="stacks-sub">
            {books.toLocaleString()} {books === 1 ? "book" : "books"}{shelves > 0 ? ` on ${shelves.toLocaleString()} ${shelves === 1 ? "shelf" : "shelves"}` : ""}
          </p>
        </div>
        <div className="stacks-head-actions">
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept=".pdf,.epub,.docx,.xlsx,.pptx,.txt,.md,.csv"
            onChange={(event) => {
              onUpload(event.target.files);
              event.target.value = "";
            }}
          />
          {intakeCount > 0 && (
            <button type="button" className="button" onClick={onReviewUploads} title="Uploads that have been read and are waiting for you to check and save them">
              Review uploads <span className="tab-count">{intakeCount}</span>
            </button>
          )}
          <button type="button" className="button primary add-books" disabled={uploading} onClick={() => fileInput.current?.click()}>
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
            {uploading ? "Adding…" : "Add books"}
          </button>
        </div>
      </header>

      <CollectionBar
        collections={collections}
        activeId={activeCollection?.id || ""}
        onSelect={setActiveCollectionId}
        onCreate={onCreateCollection}
        onUpdate={onUpdateCollection}
        onDelete={onDeleteCollection}
        busy={collectionBusy || isActiveIngestJob(job)}
        scan={{ progress: scanProgress, starting: scanStarting, disabled: scanStarting || isActiveIngestJob(job), onScan: () => onRescan(activeCollection) }}
      />

      {(notice?.text || jobProgress) && (
        <div className="stacks-status">
          {notice?.text && <div className={`notice ${notice.tone !== "neutral" ? notice.tone : ""}`}>{notice.text}</div>}
          {jobProgress && <ActionProgress progress={jobProgress} />}
        </div>
      )}

      <div className="stacks-tabs">
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "browse"} className={tab === "browse" ? "active" : ""} onClick={() => setTab("browse")}>
            Browse
          </button>
          <button type="button" role="tab" aria-selected={tab === "notes"} className={tab === "notes" ? "active" : ""} onClick={() => setTab("notes")}>
            Notes <span className="tab-count">{noteCount}</span>
          </button>
          <button type="button" role="tab" aria-selected={tab === "vocabulary"} className={tab === "vocabulary" ? "active" : ""} onClick={() => setTab("vocabulary")}>
            Vocabulary
          </button>
          <button type="button" role="tab" aria-selected={tab === "statistics"} className={tab === "statistics" ? "active" : ""} onClick={() => setTab("statistics")}>
            Statistics
          </button>
          <button type="button" role="tab" aria-selected={tab === "activity"} className={tab === "activity" ? "active" : ""} onClick={() => setTab("activity")}>
            Activity <span className="tab-count">{counts.pending + counts.error}</span>
          </button>
          <button type="button" role="tab" aria-selected={tab === "catalogue"} className={tab === "catalogue" ? "active" : ""} onClick={() => setTab("catalogue")}>
            Index <span className="tab-count">{sourceDocuments.length}</span>
          </button>
        </div>
      </div>

      {tab === "browse" && <LibraryExplore apiBase={apiBase} documents={documents} collections={allCollections} onOpenDocument={onOpenDocument} onNavigate={setTab} onPatch={props.onPatch} loaded={documentsLoaded} preset={browsePreset} />}
      {tab === "notes" && <NotesPanel apiBase={apiBase} onChanged={onImported} onOpenSource={(id) => onOpenDocument({ document_id: id })} />}
      {tab === "vocabulary" && <VocabularyPanel apiBase={apiBase} documents={documents} onOpenDocument={onOpenDocument} />}
      {tab === "statistics" && <LibraryStats documents={documents} collections={allCollections} onExplore={(preset) => { setBrowsePreset({ ...preset }); setTab("browse"); }} />}
      {tab === "activity" && <IndexActivity apiBase={apiBase} onOpenDocument={onOpenDocument} onRetry={props.onReindex} onApproveOcr={props.onApproveOcr} onChanged={onImported} />}
      {tab === "catalogue" && <Catalogue {...props} documents={sourceDocuments} counts={counts} />}
    </section>
  );
}
