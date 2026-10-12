import { useCallback, useEffect, useState } from "react";

import { apiError, clearDraft } from "./intake.js";

// The uploads waiting for review, and what can be done with them. They live on the
// server (so they survive a reload) and are not part of the library until saved.
export default function useIntake(apiBase, onLibraryChanged) {
  const [items, setItems] = useState([]);
  const [options, setOptions] = useState({ types: [], shelves: [] });

  const refresh = useCallback(async () => {
    const response = await fetch(`${apiBase}/intake`);
    if (!response.ok) throw new Error(`Could not list the uploads waiting for review (${response.status})`);
    const data = await response.json();
    setItems(data.items || []);
    return data.items || [];
  }, [apiBase]);

  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  const loadOptions = useCallback(async () => {
    try {
      const response = await fetch(`${apiBase}/intake/options`);
      if (response.ok) setOptions(await response.json());
    } catch (_error) {
      // the form still works without suggestions
    }
  }, [apiBase]);

  // Upload files one at a time so each is read before the next arrives. Never throws:
  // a file that fails is reported and the rest carry on.
  const stage = useCallback(async (files, onProgress) => {
    const outcome = { staged: 0, duplicates: [], failures: [], firstId: null };
    for (const [index, file] of files.entries()) {
      onProgress?.(file.name, index + 1, files.length);
      try {
        const form = new FormData();
        form.append("file", file, file.name);
        const response = await fetch(`${apiBase}/intake`, { method: "POST", body: form });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(apiError(data, response.status));
        if (data.duplicate) outcome.duplicates.push({ filename: file.name, ...data.duplicate });
        else {
          outcome.staged += 1;
          outcome.firstId ??= data.id;
        }
      } catch (error) {
        outcome.failures.push({ filename: file.name, message: error.message });
      }
    }
    try {
      await refresh();
    } catch (_error) {
      // the next open of the review screen will try again
    }
    return outcome;
  }, [apiBase, refresh]);

  const save = useCallback(async (id, body, { refreshLibrary = true } = {}) => {
    const response = await fetch(`${apiBase}/intake/${id}/save`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(apiError(data, response.status));
    clearDraft(id);
    setItems((current) => current.filter((item) => item.id !== id));
    if (refreshLibrary) onLibraryChanged?.();
    return data;
  }, [apiBase, onLibraryChanged]);

  const discard = useCallback(async (id) => {
    const response = await fetch(`${apiBase}/intake/${id}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) throw new Error(apiError(await response.json().catch(() => ({})), response.status));
    clearDraft(id);
    setItems((current) => current.filter((item) => item.id !== id));
  }, [apiBase]);

  return { items, options, refresh, loadOptions, stage, save, discard };
}
