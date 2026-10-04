import React, { useState, useEffect } from "react";

import { displayTitle } from "./storage.js";

// A document's first page as a cover, or a plain labelled placeholder when there's no
// image to show. `documentId` null means the file isn't in the library yet.
// `children` are laid over the cover (e.g. read/owned flags).
export default function Cover({ apiBase, documentId, filename, fileType = "pdf", width = 320, className = "", children }) {
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => { setRevision(Date.now()); setFailed(false); };
    window.addEventListener("book-cover-changed", refresh);
    return () => window.removeEventListener("book-cover-changed", refresh);
  }, []);
  useEffect(() => setFailed(false), [documentId]);
  const showImage = documentId && !failed;
  return (
    <div className={`cover${showImage ? "" : " cover-blank"} ${className}`}>
      {showImage ? (
        <img
          src={`${apiBase}/documents/${documentId}/thumbnail?w=${width}&v=${revision}`}
          alt=""
          loading="lazy"
          draggable="false"
          onError={() => setFailed(true)}
        />
      ) : (
        <>
          <span className="cover-blank-kind">{({ book: "BOOK", epub: "EPUB", word: "WORD", excel: "EXCEL", powerpoint: "SLIDES", markdown: "MD", text: "TEXT", csv: "CSV" })[fileType] || "PDF"}</span>
          <span className="cover-blank-title">{displayTitle(filename)}</span>
        </>
      )}
      {children}
    </div>
  );
}
