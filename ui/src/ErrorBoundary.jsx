import React from "react";
import { createPortal } from "react-dom";

// A render that throws used to unmount the whole app: the page went blank and
// stayed blank, even after going back, until it was refreshed. This catches the
// failure where it happens, so only the part that broke is replaced by a notice
// and everything around it keeps working.

// What the browser says when a script the page asks for later is gone from the
// server: the usual sign that the app was updated while this page stayed open.
const STALE = /dynamically imported module|importing a module script failed|error loading dynamically|unable to preload|chunkloaderror|failed to fetch dynamically/i;

export function isStaleBundleError(error) {
  return STALE.test(`${error?.name || ""} ${error?.message || error || ""}`);
}

function Notice({ error, stale, title, layer, reset, onClose }) {
  const reload = () => window.location.reload();
  const card = (
    <div className={`failure-card failure-${layer}`} role="alert">
      <h2>{stale ? "A newer version is ready" : title}</h2>
      <p>
        {stale
          ? "AI Librarian was updated while this page was open, so a part of it can no longer load. Reload the page to get the new version; your library is untouched."
          : "Something went wrong while showing this. Nothing in your library was changed."}
      </p>
      {!stale && error?.message && <p className="failure-detail">{String(error.message).slice(0, 240)}</p>}
      <div className="failure-actions">
        <button type="button" className="button primary" onClick={reload}>Reload page</button>
        {!stale && reset && <button type="button" className="button" onClick={reset}>Try again</button>}
        {onClose && <button type="button" className="text-button" onClick={() => { reset?.(); onClose(); }}>Close</button>}
      </div>
    </div>
  );
  // An overlay sits above the app (and the PDF reader) so it can always be seen and closed.
  // On the body, not inside the app: the app is made inert while a book page is open.
  return layer === "overlay" ? createPortal(<div className="failure-layer">{card}</div>, document.body) : card;
}

// `layer`: "overlay" (a card over the app, for book pages, the reader and other
// windows), "inline" (a card in place of a section) or "page" (the whole app).
// `resetKey` clears the failure when it changes, so moving to another book, tab
// or page starts clean. `onClose` adds a Close button that also leaves the window.
export default class ErrorBoundary extends React.Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error(`${this.props.title || "The app"} failed to render`, error, info?.componentStack);
  }

  componentDidUpdate(previous) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const { title = "This could not be shown", layer = "inline", onClose } = this.props;
    return <Notice error={error} stale={isStaleBundleError(error)} title={title} layer={layer} reset={this.reset} onClose={onClose} />;
  }
}
