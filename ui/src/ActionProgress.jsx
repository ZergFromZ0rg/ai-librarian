import React from "react";

export default function ActionProgress({ progress, compact = false }) {
  if (!progress) return null;
  const hasPercent = Number.isFinite(progress.percent);
  return (
    <div
      className={`action-progress${compact ? " compact" : ""}${progress.tone ? ` ${progress.tone}` : ""}`}
      role="status"
      aria-live="polite"
      aria-label={`${progress.label}${progress.detail ? `. ${progress.detail}` : ""}`}
    >
      <span className="action-progress-copy">
        <strong>{progress.label}</strong>
        {progress.detail && <span>{progress.detail}</span>}
      </span>
      <span className={`action-progress-track${hasPercent ? "" : " indeterminate"}`} aria-hidden="true">
        <span className="action-progress-fill" style={hasPercent ? { width: `${progress.percent}%` } : undefined} />
      </span>
    </div>
  );
}
