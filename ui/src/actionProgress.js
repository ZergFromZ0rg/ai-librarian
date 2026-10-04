export const TERMINAL_JOB_STATES = new Set(["done", "partial", "error", "interrupted"]);

const TERMINAL_FILE_STATES = new Set(["indexed", "duplicate", "error"]);

export function isActiveIngestJob(job) {
  return Boolean(job?.job_id) && !TERMINAL_JOB_STATES.has(job.state);
}

export function ingestActionProgress(job) {
  if (!isActiveIngestJob(job)) return null;
  if (job.state === "queued") return { label: "Queued…", detail: "Waiting to scan", percent: null };

  const total = Number.isInteger(job.total_files) ? job.total_files : null;
  if (total == null) return { label: "Scanning folder…", detail: "Finding supported files", percent: null };
  if (total === 0) return { label: "Finishing scan…", detail: "No new files found", percent: 100 };

  const finished = (job.files || []).filter((file) => TERMINAL_FILE_STATES.has(file.status)).length;
  const errors = (job.files || []).filter((file) => file.status === "error").length;
  return {
    label: `Indexing ${finished} of ${total}…`,
    detail: errors ? `${errors} failed` : "Preparing search passages",
    percent: Math.round((finished / total) * 100),
  };
}

export function reindexActionProgress(progress) {
  if (!progress) return null;
  const total = progress.requestedIds?.length || 0;
  if (progress.phase === "queueing") {
    const attempted = progress.attempted || 0;
    return {
      label: `Queueing ${attempted} of ${total}…`,
      detail: "Starting reindex",
      percent: total ? Math.round((attempted / total) * 100) : null,
    };
  }

  const queued = progress.queuedIds?.length || 0;
  const complete = Math.max(0, progress.complete || 0);
  const failed = progress.failed || 0;
  if (progress.phase === "done") {
    return {
      label: failed ? `Reindex finished · ${failed} failed` : "Reindex complete",
      detail: `${complete} of ${queued} processed`,
      percent: 100,
      tone: failed ? "error" : "success",
    };
  }
  return {
    label: `Reindexing ${complete} of ${queued}…`,
    detail: failed ? `${failed} failed` : "Rebuilding search passages",
    percent: queued ? Math.round((complete / queued) * 100) : null,
    tone: failed ? "error" : "neutral",
  };
}
