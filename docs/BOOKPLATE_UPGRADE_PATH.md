# Bookplate-inspired upgrade path

This document is the canonical product and implementation roadmap for bringing
the strongest ideas from [Bookplate](https://github.com/LeoPhh/bookplate) into
AI Librarian. It is written to survive across development chats: start each new
chat by reading this file, checking the repository state, and selecting the
first unfinished milestone whose dependencies are complete.

AI Librarian is not intended to become a Bookplate clone. Bookplate is the
reference for catalogue ergonomics, visual library browsing, reading history,
statistics, notes, vocabulary, and portable backups. AI Librarian remains a
local-first knowledge system whose defining strengths are document extraction,
hybrid full-text and semantic retrieval, passage-level citations, and grounded
questions across a collection.

## How to use this roadmap across chats

At the beginning of a work session:

1. Read this document and `README.md`.
2. Run `git status --short --branch` and do not overwrite unrelated changes.
3. Confirm the current milestone in the status table below.
4. Inspect the implementation before assuming this document is current.
5. Implement one reviewable vertical slice, including migrations, API, UI,
   tests, documentation, and accessibility where those layers apply.
6. Update this document's status, decision log, and handoff block before ending.

Use these status values:

- `DONE` — shipped and verified.
- `ACTIVE` — the next agreed implementation target.
- `READY` — scoped and unblocked.
- `BLOCKED` — requires a named prerequisite or product decision.
- `LATER` — intentionally outside the near-term path.

Do not mark an entire release `DONE` when only its schema or UI shell exists.
All acceptance criteria for that release must pass.

## Product principles

1. **Source files stay authoritative.** Never rename, move, or rewrite imported
   books and documents. Catalogue metadata is an overlay in `library.db`.
2. **Local-first by default.** Core reading, notes, metadata editing, export,
   search, and Ask work without a hosted service. External catalogue lookup is
   optional and clearly attributed.
3. **Suggestions require review.** Catalogue and AI metadata may propose values;
   the reader chooses what to apply. User-edited metadata always wins during
   rescans and reindexing.
4. **One book record.** Imported files and PDF-less owned books should converge
   on a common book model rather than accumulating parallel feature sets.
5. **Searchable personal context.** Reviews, notes, highlights, vocabulary, and
   reading metadata should enrich search and Ask without being confused with
   quoted source text.
6. **Portable data.** User-created data must be exportable in documented,
   readable formats. Regenerable caches do not belong in the canonical export.
7. **Incremental migrations.** Existing SQLite installations upgrade in place;
   migrations are additive and safe to repeat.
8. **Accessible controls.** All editing workflows must work with keyboard and
   screen-reader semantics, including rating, cropping, filters, and dialogs.

## Current baseline

Release 1 is implemented on the `mac-local-work` branch as commit `e6acbcb`.

- Stable per-book route: `#book/<document_id>`.
- Editable title, author, subject, library type, ownership, and read state.
- Optional one-to-five-star rating and written review.
- JPEG, PNG, and WebP cover upload by picker, drag-and-drop, or paste; custom
  covers are normalized and can be restored to the generated source thumbnail.
- Source format and original-file access for PDF, EPUB, and other indexed types.
- Per-book Markdown notes and source-linked quotations.
- Notes are indexed separately while retaining their source document and page.
- Search results and Ask citations can be saved to notes.
- EPUB ingestion with embedded title and author where available.
- Existing content-hash deduplication, collections, shelves, semantic search,
  grounded Ask, OCR, and source citations remain intact.

Known architectural debt:

- The legacy `owned_books` table remains for one compatibility release, though
  canonical fileless books now live in `documents` with imported sources.
- `read_at` remains as a compatibility mirror for older clients while the
  four-state reading lifecycle is canonical.
- `subject` carries more meaning than one field should: topic, genre, and shelf
  suggestions can become conflated.
- Notes use Markdown source plus preview rather than an inline editor.
- Custom covers are backed up only when the operator copies the data directory.

## Release sequence

| Release | Outcome | Status | Depends on |
| --- | --- | --- | --- |
| 1. Book identity | Book pages, metadata, covers, reviews, ownership, linked notes | DONE | — |
| 2A. Book model | Unified metadata and reading lifecycle | DONE | Release 1 |
| 2B. Catalogue assist | Open Library lookup, ISBN import, cover candidates | READY | 2A schema |
| 2C. Cover studio | 2:3 crop, compression, catalogue fallback | READY | 2B |
| 3A. Library controls | Faceted filters, sorting, notes markers | ACTIVE | 2A |
| 3B. Ledger | Dense sortable catalogue table | READY | 3A |
| 3C. Statistics | Drill-down reading and collection analytics | READY | 2A, 3A |
| 4A. Reading progress | Started/finished dates and resumable positions | READY | 2A |
| 4B. Reading notebook | Inline notes, images, highlights | READY | export foundation |
| 4C. Vocabulary | Definitions linked to books and passages | READY | 4B |
| 5. Portability | Versioned ZIP export/import and restore validation | READY | 2A; before note images |
| 6. Operations | Release images, health, migrations, backup UX | READY | 5 |
| 7. Accounts/sync | Optional multi-user isolation and cross-device state | LATER | explicit product decision |

The labels indicate dependency order, not a requirement to make one enormous
pull request. Each subsection below should normally be one or more small PRs.

## Release 2A — Unified book model and reading lifecycle

**Status:** `DONE`. Existing `read_at` values migrate to `reading_status` and
`finished_at`; all book records support the catalogue fields below, four
reading states, editable dates, covers, reviews, shelves, and linked notes.

### User outcome

A book has the same metadata and actions whether it came from an EPUB/PDF or
was entered as a physical book without a file. The reader can distinguish books
they plan to read, are reading, have completed, or abandoned.

### Data model

Add the following nullable columns to `documents` through the existing additive
migration mechanism:

- `isbn_10`, `isbn_13`
- `subtitle`
- `description`
- `publisher`
- `published_year`
- `page_count`
- `language`
- `genres_json` — normalized JSON string array initially; do not prematurely
  introduce a join table unless filtering performance requires it.
- `acquisition_source` — controlled values such as `book_store`, `kindle`,
  `audiobook`, `borrowed`, `second_hand`, `gifted`, `library`, `other`.
- `reading_status` — `to_read`, `reading`, `read`, `abandoned`.
- `started_at`, `finished_at`
- `metadata_source` and `metadata_source_id`

Retain `read_at` during migration. Backfill `reading_status = 'read'` and
`finished_at = read_at` where `read_at` exists. New writes should keep `read_at`
compatible until all consumers move to the new fields.

The implemented migration path is:

1. Fileless entries are normal `documents` rows with
   `record_type = 'standalone'`, an empty stored-file sentinel, and
   `indexing_status = 'catalogued'`, which the indexing workers never claim.
2. Every legacy `owned_books` row copies idempotently into `documents` using
   its existing 12-character ID. Existing notes become the initial review.
3. The fileless-books UI reads the main document list and opens the same book
   page and editor as source-backed books.
4. File, Read, Ask, reindex, and OCR actions explicitly exclude standalone
   records. Custom covers and linked notes remain available.
5. The old table and `/owned-books` response aliases remain for one release.
   Remove them only after export/import covers the unified model.

### API and UI

- Extend document GET/PATCH responses with the richer fields.
- Use strict enums and reject invalid dates, years, ISBNs, and negative counts.
- Keep partial PATCH semantics; omitted fields must never clear stored values.
- Replace Read/Unread with a four-state reading control.
- Show started and finished dates only when relevant, while allowing corrections.
- Add the richer fields to `BookDetails` without making the primary form feel
  like a database record. Put publication data in a collapsible secondary area.
- Clearly label metadata sourced from a catalogue versus edited by the reader.

### Acceptance criteria

- Existing installations migrate without reindexing source content.
- Existing `read_at` values appear as completed books.
- A fileless book supports the same metadata, cover, rating, review, status, and
  notes actions as a book with a source file, except Read source and Ask.
- Rescanning and reindexing do not overwrite reader-edited fields.
- API validation and migration behavior have regression tests.

## Release 2B — Catalogue metadata assistance

### User outcome

The reader can search by ISBN, title, or author, inspect matches, and selectively
apply trusted metadata without leaving the book editor.

### Service design

- Add a server-side catalogue adapter. Begin with Open Library; keep the adapter
  interface provider-neutral so an iTunes cover fallback can be added cleanly.
- The browser must call AI Librarian, not third-party image hosts directly.
- Add request timeouts, a descriptive user agent/contact setting, bounded retry,
  and a small disk or SQLite cache with expiry.
- Normalize provider responses into one internal candidate shape.
- Never send reviews, notes, filenames, library contents, or reading history to
  a catalogue provider. Only transmit the explicit title/author/ISBN query.
- Treat catalogue HTML/text as untrusted data. Store plain normalized fields.

Proposed endpoints:

```text
GET  /catalogue/search?q=...&limit=10
GET  /catalogue/isbn/{isbn}
POST /documents/{id}/metadata-preview
POST /documents/{id}/metadata-apply
GET  /catalogue/cover?provider=...&id=...
```

`metadata-preview` returns current and proposed values plus their provenance.
`metadata-apply` accepts an explicit list of fields selected by the reader. It
must not silently apply every field returned by the provider.

### UI workflow

1. Open **Find book information** from the book page.
2. Search using prefilled title/author or scan/type an ISBN.
3. Review a compact result list with cover, title, author, year, edition, ISBN,
   language, and publisher.
4. Select one edition.
5. Compare current and suggested values field by field.
6. Apply selected metadata, then continue to cover cropping.

### Acceptance criteria

- Search works by ISBN and ordinary title/author text.
- A provider outage leaves manual editing fully usable.
- Applying a result changes only selected fields.
- Catalogue covers are proxied with content-type, byte-size, and pixel limits.
- Responses are cached and provider rate limits are respected.
- Tests use fixtures/mocks and never depend on the live provider.

## Release 2C — Cover studio

### User outcome

Every book can have a clean, consistently proportioned cover whether the image
comes from the source file, a catalogue, upload, drag-and-drop, or clipboard.

### Scope

- Add an accessible crop dialog locked to a 2:3 output ratio.
- Support pan, zoom, keyboard adjustment, reset, cancel, and preview.
- Downscale and encode to a maximum of 600 × 900 JPEG before upload where
  practical; retain server-side validation and normalization as the authority.
- Show multiple catalogue cover candidates when available.
- Try Open Library first, then an iTunes artwork adapter when the first provider
  has no usable image.
- Preserve **Restore original cover** and add **Use generated cover**.
- Generate deterministic cloth-style covers for books without source artwork.
  Their colour should derive from stable book identity, not array position.

### Acceptance criteria

- Portrait, landscape, oversized, transparent, and rotated inputs produce a
  valid 2:3 cover.
- Cancel never changes the stored cover.
- Cover changes propagate immediately to all visible library views.
- Keyboard users can complete or cancel the workflow.
- Corrupt and deceptive image payloads are rejected server-side.

## Release 3A — Search, filter, sort, and notes markers

### User outcome

Readers can reduce a large library to the books they care about without learning
query syntax.

### Scope

- One free-text catalogue search across title, author, description, genre,
  ISBN, review, and personal notes. Keep in-book passage search as a separate,
  clearly named action.
- Filter chips or a filter panel for reading status, ownership, rating, format,
  genre, author, source, collection, and presence of notes.
- Sort by recently added, title, author, rating, finished date, and page count.
- Encode active filters in the URL so a view can be bookmarked and shared on the
  same installation.
- Show a bookmark ribbon or equally strong marker on every book with notes.
- Display the result count and provide one-click **Clear filters**.

Filtering should initially happen client-side over the existing document list
unless profiling shows a need for server-side queries. Keep filter logic in a
pure module with unit tests so Covers, Ledger, and Statistics share semantics.

### Acceptance criteria

- All library views return the same set for the same filter state.
- Filter and sort state survives opening and closing a book.
- Empty states name the active constraints and provide a reset action.
- Search includes personal note text without presenting source quotations as
  if the reader wrote them.

## Release 3B — Ledger view

### User outcome

The library has a compact, sortable inventory suitable for auditing metadata and
managing hundreds or thousands of books.

### Scope

- Add a third library layout: Covers, List, Ledger.
- Columns: title, author, status, rating, format, ownership, page count, finished
  date, notes marker, and collection/shelf.
- Permit column sorting and sensible narrow-screen column reduction.
- End with a count row that reflects active filters.
- Clicking the title opens the book; row actions use the same implementations as
  cover-card actions.
- Avoid editing directly inside every cell in the first version. Open the normal
  book editor so validation and unsaved-change handling remain centralized.

### Acceptance criteria

- Table semantics, header associations, and keyboard focus order are correct.
- Sorting is stable and handles missing values consistently.
- A thousand-row fixture remains responsive on a typical laptop.
- Ledger counts match Covers and List for every tested filter combination.

## Release 3C — Statistics with drill-down

### User outcome

Statistics help the reader discover books and patterns rather than merely
displaying totals.

### Initial metrics

- Total books and owned books.
- To read, reading, read, and abandoned counts.
- Books and pages finished this year.
- Average rating, excluding unrated books.
- Books finished by month/year.
- Rating distribution.
- Top genres, authors, formats, acquisition sources, and collections.

Every chart element must drill down into the exact books behind it. The drill-
down uses the shared filter model from 3A and opens book details from its results.

Use lightweight SVG/CSS or an existing small dependency only after evaluating
bundle cost and accessibility. Provide an equivalent textual/table view for
screen readers and precise values.

### Acceptance criteria

- Aggregations have unit tests for missing dates, missing pages, and unrated books.
- Every displayed aggregate reconciles with its drill-down list.
- Dates use the configured/local timezone consistently.
- No statistic claims pages read when `page_count` is unknown.

## Release 4A — Reading progress

### User outcome

The application remembers what the reader is currently reading and can resume
supported formats across sessions.

### Scope

- Store `progress_percent`, `progress_locator`, and `progress_updated_at`.
- Build **Continue reading** and **Recently added** sections.
- For EPUB, store a robust locator such as CFI plus percentage when the reader is
  integrated. For PDF, start with page number plus total-page percentage.
- Use newest-update-wins semantics.
- Transition `to_read → reading` when meaningful progress begins; never mark a
  book read automatically without an explicit preference or confirmation.
- Keep manual status and dates editable.

An embedded reader is a separate architectural decision. A first release can
track the last source page opened and expose resume links without adopting a
large reader library.

### Acceptance criteria

- Progress resumes after restart and across browser sessions against one server.
- Stale updates cannot overwrite a newer position.
- Removing/resetting progress does not delete notes or reading history.
- Unsupported formats degrade to status/date tracking.

## Release 4B — Reading notebook

### User outcome

Notes feel immediate and visual while remaining portable Markdown and preserving
the exact distinction between source quotation and personal interpretation.

### Scope

- Introduce an inline rich-text/Markdown editor that stores Markdown, not opaque
  editor JSON.
- Support pasted/dropped images with immediate previews.
- Resize large images client-side and validate again server-side.
- Store note images under stable IDs and serve them through authenticated/local
  API routes rather than arbitrary filesystem paths.
- Garbage-collect images removed from saved Markdown.
- On cancel, delete images uploaded only for the abandoned edit session.
- Show a note excerpt and image thumbnails on the book page.
- Add highlight, quotation, thought, and general-note types while retaining
  source document/page/quote fields.

Portability Release 5's archive format should be specified before note images
ship, so images never become trapped in an undocumented store.

### Acceptance criteria

- Markdown round-trips without semantic loss for supported constructs.
- Save and cancel have deterministic image cleanup behavior.
- Orphan cleanup cannot delete images referenced by another note.
- Notes and images appear in export/import and remain linked afterward.
- Note text remains searchable and usable by Ask with provenance labels.

## Release 4C — Vocabulary registry

### User outcome

Readers can retain unfamiliar words encountered in a book and later rediscover
them by word, definition, or source.

### Data and API

Create a `vocabulary` table with:

- `word_id`, `word`, `normalized_word`
- `definition`, `part_of_speech`, `example`
- `document_id`, `source_page`, `source_quote`
- `definition_source`, `created_at`, `updated_at`

Support manual definitions first. Add an optional server-side Wiktionary adapter
with caching and a choose-the-sense workflow. Never overwrite a reader-edited
definition during refresh.

### UI

- Add a word from a book page, source passage, or global Vocabulary page.
- Search, sort, and filter by originating book.
- Show a book's vocabulary on its notebook page.
- Let Ask include vocabulary only when the request or selected scope calls for it.

### Acceptance criteria

- Manual use works while offline.
- Dictionary failure always leaves manual entry available.
- Duplicate normalized words can be linked to multiple books without losing
  book-specific context.
- Vocabulary is included in export/import.

## Release 5 — Portable export, import, and recovery

### User outcome

The reader can download one archive, inspect its human-readable contents, and
restore it into a fresh compatible installation.

### Archive format

Use a versioned ZIP with a manifest, for example:

```text
ai-librarian-export/
  manifest.json
  books.json
  reviews.json
  notes/
    <note-id>.md
  vocabulary.json
  covers/
    <book-id>.jpg
  note-images/
    <image-id>.jpg
  source-files/              # optional, explicit export choice
```

The manifest records archive schema version, app version, creation time, counts,
checksums, whether source files are included, and the originating installation's
timezone. Exclude model files, vector indexes, thumbnails that can be regenerated,
logs, secrets, tokens, provider cache, and transient jobs.

### Import behavior

- Validate the ZIP centrally to prevent path traversal, decompression bombs,
  unsupported versions, oversized entries, and checksum mismatches.
- Present a dry-run summary before changing state.
- Support a fresh restore first; add merge semantics only with an explicit,
  tested identity policy.
- Apply database changes in a transaction and stage files before final placement.
- Rebuild Qdrant from restored canonical content rather than exporting its files.
- Produce a readable import report with created, skipped, conflicted, and failed
  records.

### Acceptance criteria

- Export then import into a fresh installation preserves all canonical fields,
  covers, notes, note images, and vocabulary.
- A failed import leaves the existing installation unchanged.
- Archives are documented sufficiently for a person to recover Markdown and
  images without running AI Librarian.
- Large-library export streams data rather than loading the entire archive in RAM.

## Release 6 — Operations and release discipline

### Scope

- Publish versioned multi-architecture Docker images after CI passes.
- Surface application and schema versions in the UI and health endpoint.
- Add a migration smoke test that upgrades representative old databases.
- Add backup reminders and a **Verify backup** action that validates an archive
  without importing it.
- Use hashed frontend asset names and no-cache HTML to avoid stale deployments.
- Document update, rollback, storage layout, and disaster recovery.
- Add an end-to-end smoke test for health, migration, library load, book edit,
  note creation, export, and clean restart.

### Acceptance criteria

- A tagged release can be installed and upgraded using documented commands.
- Migration failure prevents startup with an actionable log and does not leave a
  partially migrated database.
- The browser cannot load incompatible old assets after an upgrade.
- The restore drill is exercised in CI or a repeatable release script.

## Release 7 — Optional accounts and synchronization

This is deliberately deferred. AI Librarian currently fits a trusted single-user
server. Authentication changes every data query, storage path, export, cache,
note image, and Ask boundary, so it should happen only after a clear decision to
support multiple independent libraries.

If approved later, require:

- owner-scoped records and storage paths;
- migration of existing data to an initial owner;
- secure sessions, password reset, rate limiting, CSRF protection, and audit;
- explicit sharing semantics rather than accidental global visibility;
- per-user export/delete and quotas;
- tests proving every read and mutation is owner-scoped.

For remote single-user access in the meantime, prefer a private network or an
authenticated TLS reverse proxy rather than building partial application auth.

## Cross-cutting implementation rules

### Database migrations

- Migrations must be idempotent and exercised against both new and old schemas.
- Never make source reindexing a prerequisite for a metadata-only migration.
- Add indexes only for measured query paths.
- Preserve unknown future export fields where practical, or reject versions with
  a clear compatibility message.

### API consistency

- Partial updates change only explicitly supplied fields.
- Use stable IDs; titles and filenames are display values, not identities.
- Return structured validation errors suitable for inline UI display.
- Bound text, files, pixel counts, query length, result count, and remote timeouts.
- New external integrations go through the service, with provider provenance.

### Search and Ask

- Personal writing and source quotations remain separately labeled.
- Metadata changes update catalogue/search fields without unnecessary extraction.
- Deleting a source may preserve personal notes, but the unavailable source link
  must be explicit.
- Ask answers must cite source content and label personal annotations when used.

### UI and accessibility

- Preserve stable book URLs and back-navigation state.
- Warn before discarding unsaved changes.
- Dialogs trap focus, restore focus on close, and respond to Escape where safe.
- Icons have accessible names; colour is never the only state indicator.
- Desktop and mobile layouts are verified for every new workflow.

### Testing

For each vertical slice, add the smallest meaningful combination of:

- migration and database tests;
- API validation and persistence tests;
- pure UI-state tests for filtering/aggregation;
- production frontend build;
- lint and `git diff --check`;
- a browser smoke test for the completed user path.

External-provider tests use recorded normalized fixtures or mocks. CI must not
depend on Open Library, iTunes, Wiktionary, or a hosted model being available.

## Explicit non-goals for the near term

- Book-store downloading or piracy-source integrations.
- Social reviews, public profiles, activity feeds, or recommendation networks.
- DRM removal or modification of source ebook files.
- Replacing Qdrant or the existing retrieval system merely to match Bookplate's
  architecture.
- A full EPUB reader before progress, export, and the unified book model exist.
- Multi-user accounts without a separately approved security design.

## Decision log

Add decisions here when implementation makes a choice future chats should not
reopen without new evidence.

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-10-03 | Preserve AI Librarian's retrieval/Ask architecture and adopt Bookplate features as product patterns, not a rewrite. | The projects solve overlapping but different primary problems. |
| 2026-10-03 | Release 1 stores reader metadata as an overlay and never rewrites source files. | Imported folders may be read-only and source files must remain authoritative. |
| 2026-10-03 | Personal note text and quoted source text remain separate fields. | Search and Ask must preserve authorship and provenance. |
| 2026-10-03 | Release 2 starts with the unified metadata/status model before catalogue lookup. | Catalogue data needs stable destination fields and validation. |
| 2026-10-03 | Use `documents` as the canonical table for both source-backed and fileless books, distinguished by `record_type`. | It gives every book one editor and feature set while a non-queued `catalogued` state keeps fileless records out of extraction. |
| 2026-10-03 | Specify portable export before shipping pasted note images. | User-created images need a supported recovery path from their first release. |
| 2026-10-04 | Keep long-running library actions attached to the control that started them, with exact folder totals and document-level reindex progress. | A global notice alone made Scan, Import, Set as library, and Reindex appear finished while work was still running. |
| 2026-10-04 | Show indexing work in a dedicated Activity tab using durable document states plus ephemeral worker stages. | The library needs a compact, trustworthy queue; extraction remains indeterminate until the parser can report real page progress, while embedding reports exact completed passages. |
| 2026-10-04 | Make Library a persistent sidebar destination with its own main surface. | Keeping the full catalogue below every empty chat made the research workspace feel like a long landing page and obscured the boundary between conversations and library management. |
| 2026-10-04 | Keep saved library views in browser storage while document metadata, progress, notes, and vocabulary remain server-owned. | Views are UI preferences; reader-created records need durable SQLite storage and cross-browser access through the local server. |
| 2026-10-04 | Opening a page updates resumable progress and moves `to_read` to `reading`, but reaching 100% does not mark a book finished. | Completion and its date are reader assertions, while page position can be recorded automatically. |
| 2026-10-04 | Store vocabulary in a dedicated table linked to an optional book and page. | A word may recur in several books with different context, and deleting a source must not erase the reader's definition. |

## Current handoff

To resume in another chat, use this prompt:

> Continue the Bookplate-inspired upgrade path. Read
> `docs/BOOKPLATE_UPGRADE_PATH.md` and `README.md`, inspect the current branch
> and working tree, then work on the `ACTIVE` milestone. Preserve unrelated
> changes, implement one complete vertical slice, run the roadmap's relevant
> checks, and update the roadmap status, decision log, and current handoff before
> stopping.

**Next milestone:** finish Release 3A with URL-encoded filter state, linked-note
markers/search, and shared result semantics for the future Ledger view.

**Completed release:** Release 2A. Rich metadata, reading lifecycle, and
fileless books now share the canonical document record and book page.

**Maintenance completed after 2A:** library Scan, recursive Import, Set as
library, and Reindex actions now replace their initiating controls with live,
accessible progress until the underlying work settles. Folder jobs expose a
stable `total_files` denominator, bulk reindex follows each queued document,
and collection scan toggles use real keyboard-accessible buttons. The Library
also has a Steam-style Activity tab with processing, queued, attention, and
finished groups; filters; file size and format; pages and passages; real worker
stages; queue position; completed history; retry; and OCR approval. Extraction
uses an indeterminate bar because the parser does not yet expose page-level
progress; embedding progress is based on actual indexed passage batches. The
Library now opens as a dedicated, persistent sidebar destination instead of
being appended below an empty Search/Ask workspace; chat and search actions
return to the research surface while book overlays return to their origin.

**Bookplate slices completed after 2A:** the Library now opens on a faceted
Browse tab with title/author/ISBN/description/review/genre search, status,
ownership, rating, format, collection, genre, and indexing filters; stable
sorting; result counts; progress indicators; and browser-persisted named views.
The Statistics tab provides clickable lifecycle, rating, format, genre, author,
acquisition, collection, year, page, and average-rating summaries that drill
back into Browse. Book records store durable current page, percent, and update
time; the source viewer records the last page after a debounce, book pages
resume there, and starting progress transitions a to-read book to reading
without asserting completion. The existing linked Markdown notes are presented
as the Reading notebook. A durable, offline Vocabulary registry supports CRUD,
word/definition search, book filtering, optional source page/quote context, and
per-book vocabulary on the book page. These are useful vertical slices; their
parent releases remain incomplete where their full acceptance criteria still
call for URL state, note markers, images/highlight types, export/import, or
dictionary integration.

**First recommended slice:** finish the active shared filter model by adding
linked-note presence to document summaries, URL state, and note markers in
Browse, then reuse that exact model for the Release 3B Ledger.

**Validation baseline:** Release 2A has migration, compatibility API, fileless
book lifecycle, and shelf regression coverage in addition to the full backend
suite, UI tests, Ruff, production build, and browser smoke checks. The 3A/3C/4
vertical slices pass 276 backend tests and 43 UI tests, build in the production
container, migrate the attached 146-document library in place, and expose a
healthy live service with the durable vocabulary and progress schema.
