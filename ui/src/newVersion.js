// Spotting that the app was updated while this page stayed open. Every build
// names its main script after its contents (/assets/index-<hash>.js), and the
// server only keeps the newest build's files, so a page left open across an
// update holds names that no longer exist: the next piece it tries to load fails.
// Comparing the script this page runs with the one the server now serves lets
// the page say so before that happens.

const BUNDLE = /\/assets\/index-[\w-]+\.js/;

// The main script of a page's HTML, or null (the dev server has no hashed script).
export function bundleIn(html) {
  return String(html || "").match(BUNDLE)?.[0] || null;
}

// The main script this page is running.
export function runningBundle(scripts = typeof document === "undefined" ? [] : document.scripts) {
  for (const script of scripts) {
    const found = (script.getAttribute?.("src") || "").match(BUNDLE)?.[0];
    if (found) return found;
  }
  return null;
}

export function isNewVersion(running, served) {
  return Boolean(running && served && running !== served);
}

// Asks the server which build it serves now. Resolves to false on any failure:
// a flaky connection must never look like an update.
export async function newVersionAvailable({ fetcher = fetch, base = "/", running = runningBundle() } = {}) {
  if (!running) return false;
  try {
    const response = await fetcher(base, { cache: "no-store" });
    if (!response.ok) return false;
    return isNewVersion(running, bundleIn(await response.text()));
  } catch (_error) {
    return false;
  }
}
