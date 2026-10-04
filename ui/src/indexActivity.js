export const ACTIVITY_FILTERS = [
  ["all", "All"],
  ["active", "Active"],
  ["queued", "Queued"],
  ["attention", "Needs attention"],
  ["finished", "Finished"],
];

export function activityBucket(item) {
  if (item.indexing_status === "indexing") return "processing";
  if (item.indexing_status === "queued") return "queued";
  if (item.indexing_status === "error") return "attention";
  return "finished";
}

export function filterActivity(items, filter) {
  if (filter === "all") return items;
  if (filter === "active") {
    return items.filter((item) => ["indexing", "queued"].includes(item.indexing_status));
  }
  return items.filter((item) => activityBucket(item) === filter);
}

export function groupActivity(items) {
  const buckets = { processing: [], queued: [], attention: [], finished: [] };
  for (const item of items) buckets[activityBucket(item)].push(item);
  buckets.finished.sort((a, b) => String(b.indexed_at || b.updated_at || "").localeCompare(String(a.indexed_at || a.updated_at || "")));
  return buckets;
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "Size unavailable";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** unit;
  return `${unit > 0 && value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function relativeTime(value, now = Date.now()) {
  const timestamp = Date.parse(value || "");
  if (!Number.isFinite(timestamp)) return "";
  const seconds = Math.max(0, Math.round((now - timestamp) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export function stageLabel(item) {
  if (item.stage === "embedding") {
    if (Number.isFinite(item.completed_units) && Number.isFinite(item.total_units)) {
      return `Building search index · ${item.completed_units.toLocaleString()} of ${item.total_units.toLocaleString()} passages`;
    }
    return "Building search index";
  }
  return {
    queued: item.queue_position ? `Queued · #${item.queue_position}` : "Queued",
    extracting: "Reading pages and extracting text",
    finalizing: "Finishing metadata and shelf",
    complete: "Ready to search",
    awaiting_ocr: "Waiting for OCR approval",
    failed: "Indexing stopped",
    catalogued: "Catalogue record",
  }[item.stage] || item.stage || "Waiting";
}

export function itemFacts(item) {
  const facts = [formatBytes(item.size_bytes), (item.file_type || "file").toUpperCase()];
  if (item.pages) facts.push(`${item.pages.toLocaleString()} pages`);
  const passages = item.retrieval_units || item.chunks;
  if (passages) facts.push(`${passages.toLocaleString()} passages`);
  return facts;
}
