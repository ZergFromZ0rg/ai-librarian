import { describe, expect, it } from "vitest";
import { bundleIn, isNewVersion, newVersionAvailable, runningBundle } from "./newVersion.js";
import { isStaleBundleError } from "./ErrorBoundary.jsx";

const page = (hash) => `<!doctype html><html><head><script type="module" crossorigin src="/assets/index-${hash}.js"></script><link rel="stylesheet" href="/assets/index-x.css"></head></html>`;
const scripts = (...srcs) => srcs.map((src) => ({ getAttribute: () => src }));

describe("noticing an updated app", () => {
  it("finds the main script in a page and in the running document", () => {
    expect(bundleIn(page("AbC-123_x"))).toBe("/assets/index-AbC-123_x.js");
    expect(bundleIn("<html>dev</html>")).toBeNull();
    expect(bundleIn(null)).toBeNull();
    expect(runningBundle(scripts("/other.js", "/assets/index-OLD.js"))).toBe("/assets/index-OLD.js");
    expect(runningBundle(scripts("/src/main.jsx"))).toBeNull();
  });

  it("is new only when both are known and they differ", () => {
    expect(isNewVersion("/assets/index-a.js", "/assets/index-b.js")).toBe(true);
    expect(isNewVersion("/assets/index-a.js", "/assets/index-a.js")).toBe(false);
    expect(isNewVersion(null, "/assets/index-b.js")).toBe(false);
    expect(isNewVersion("/assets/index-a.js", null)).toBe(false);
  });

  const reply = (body, ok = true) => async () => ({ ok, text: async () => body });

  it("reports an update when the server serves a different build", async () => {
    expect(await newVersionAvailable({ fetcher: reply(page("NEW")), running: "/assets/index-OLD.js" })).toBe(true);
    expect(await newVersionAvailable({ fetcher: reply(page("OLD")), running: "/assets/index-OLD.js" })).toBe(false);
  });

  it("never mistakes a failure for an update", async () => {
    expect(await newVersionAvailable({ fetcher: reply("", false), running: "/assets/index-OLD.js" })).toBe(false);
    expect(await newVersionAvailable({ fetcher: async () => { throw new Error("offline"); }, running: "/assets/index-OLD.js" })).toBe(false);
    expect(await newVersionAvailable({ fetcher: reply("<html>not a build</html>"), running: "/assets/index-OLD.js" })).toBe(false);
    expect(await newVersionAvailable({ fetcher: reply(page("NEW")), running: null })).toBe(false);
  });
});

describe("telling a stale page from a real bug", () => {
  it("recognises the browsers' messages for a script that is gone", () => {
    for (const message of [
      "Failed to fetch dynamically imported module: http://x/assets/PdfReader-abc.js",
      "error loading dynamically imported module",
      "Importing a module script failed.",
      "Unable to preload CSS for /assets/PdfReader.css",
    ]) expect(isStaleBundleError(new TypeError(message))).toBe(true);
    expect(isStaleBundleError(new Error("Cannot read properties of undefined (reading 'title')"))).toBe(false);
    expect(isStaleBundleError(null)).toBe(false);
  });
});
