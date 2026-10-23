import React, { useEffect, useState } from "react";

// What shows while the reader is being fetched. It is a full-window layer, so it
// has to carry its own way out: without one, a slow or stuck download left a
// blank screen with nothing to click.
export default function ReaderLoading({ title, onClose }) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), 8000);
    return () => clearTimeout(timer);
  }, []);
  return (
    <div className="pdf-reader" role="dialog" aria-modal="true" aria-label="Opening the reader">
      <header className="pdf-reader-bar">
        <button type="button" className="button" onClick={onClose}>Close</button>
        <strong className="pdf-reader-title">{title}</strong>
      </header>
      <p className="pdf-reader-note muted" role="status">
        {slow ? "Still opening the reader. The connection may be slow; you can close this and try again." : "Opening the reader…"}
      </p>
    </div>
  );
}
