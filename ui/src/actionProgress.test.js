import { describe, expect, it } from "vitest";

import { ingestActionProgress, isActiveIngestJob, reindexActionProgress } from "./actionProgress.js";

describe("folder ingest progress", () => {
  it("distinguishes queueing, folder discovery, and actual indexing progress", () => {
    expect(ingestActionProgress({ job_id: "j", state: "queued", files: [] })).toMatchObject({ label: "Queued…", percent: null });
    expect(ingestActionProgress({ job_id: "j", state: "processing", total_files: null, files: [] })).toMatchObject({ label: "Scanning folder…", percent: null });
    expect(
      ingestActionProgress({
        job_id: "j",
        state: "indexing",
        total_files: 4,
        files: [{ status: "indexed" }, { status: "duplicate" }, { status: "queued" }, { status: "error" }],
      }),
    ).toEqual({ label: "Indexing 3 of 4…", detail: "1 failed", percent: 75 });
  });

  it("stops presenting terminal jobs as active", () => {
    expect(isActiveIngestJob({ job_id: "j", state: "done" })).toBe(false);
    expect(ingestActionProgress({ job_id: "j", state: "partial", total_files: 2, files: [] })).toBeNull();
  });
});

describe("reindex progress", () => {
  it("reports request queueing and document completion separately", () => {
    expect(reindexActionProgress({ phase: "queueing", requestedIds: ["a", "b"], attempted: 1 })).toMatchObject({
      label: "Queueing 1 of 2…",
      percent: 50,
    });
    expect(reindexActionProgress({ phase: "indexing", queuedIds: ["a", "b", "c"], complete: 2, failed: 1 })).toEqual({
      label: "Reindexing 2 of 3…",
      detail: "1 failed",
      percent: 67,
      tone: "error",
    });
  });
});
