import React, { useState } from "react";

import { displayTitle } from "./storage.js";

// A document's first page as a cover, or a plain labelled placeholder when there's no
// image to show. `documentId` null means the file isn't in the library yet.
// `children` are laid over the cover (e.g. read/owned flags).
export default function Cover({ apiBase, documentId, filename, width = 320, className = "", children }) {
  const [failed, setFailed] = useState(false);
  const showImage = documentId && !failed;
  return (
    <div className={`cover${showImage ? "" : " cover-blank"} ${className}`}>
      {showImage ? (
        <img
          src={`${apiBase}/documents/${documentId}/thumbnail?w=${width}`}
          alt=""
          loading="lazy"
          draggable="false"
          onError={() => setFailed(true)}
        />
      ) : (
        <>
          <span className="cover-blank-kind">PDF</span>
          <span className="cover-blank-title">{displayTitle(filename)}</span>
        </>
      )}
      {children}
    </div>
  );
}
