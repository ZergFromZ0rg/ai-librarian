import React, { useCallback, useEffect, useLayoutEffect, useState } from "react";

import { PROVIDER_LABELS } from "./useAsk.js";
import { displayTitle, loadStored, saveStored } from "./storage.js";

const OPTIONS_KEY = "ai-librarian.console.options-open";
const BREADTH_LABELS = { 0: "any breadth", 1: "1 per doc", 3: "≤3 per doc" };

// Named zones along the reranker-score scale, so the threshold reads as an
// intent ("strict") rather than a bare logit. The number is still shown.
const FLOOR_PRESETS = [
  { value: -6, label: "Lenient" },
  { value: -4, label: "Broad" },
  { value: -2, label: "Balanced" },
  { value: 0, label: "Strict" },
  { value: 2, label: "Exacting" },
];
const FLOOR_MIN = -8;
const FLOOR_MAX = 4;

function floorLabel(value) {
  return FLOOR_PRESETS.reduce((best, preset) =>
    Math.abs(preset.value - value) < Math.abs(best.value - value) ? preset : best,
  ).label;
}

function formatScore(value) {
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(1)}`;
}

function Stepper({ label, value, min, max, onChange, disabled, hint }) {
  const set = (next) => onChange(Math.max(min, Math.min(max, next)));
  return (
    <div className="control" title={hint}>
      <span className="control-label">{label}</span>
      <div className="stepper">
        <button type="button" onClick={() => set(value - 1)} disabled={disabled || value <= min} aria-label={`Fewer ${label}`}>
          −
        </button>
        <input
          type="number"
          min={min}
          max={max}
          value={value}
          disabled={disabled}
          aria-label={label}
          onChange={(event) => {
            const next = Number.parseInt(event.target.value, 10);
            if (!Number.isNaN(next)) set(next);
          }}
        />
        <button type="button" onClick={() => set(value + 1)} disabled={disabled || value >= max} aria-label={`More ${label}`}>
          +
        </button>
      </div>
    </div>
  );
}

function Segmented({ label, options, value, onChange, disabled, hint }) {
  return (
    <div className="control" title={hint}>
      <span className="control-label">{label}</span>
      <div className="segmented" role="radiogroup" aria-label={label}>
        {options.map((option) => (
          <button
            type="button"
            key={String(option.value)}
            role="radio"
            aria-checked={value === option.value}
            className={value === option.value ? "active" : ""}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            title={option.hint}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// `value` null = let the server pick (only offered when `autoLabel` is set).
function Threshold({ value, onChange, disabled, autoLabel, fallback }) {
  const auto = value == null;
  const shown = auto ? fallback : value;
  return (
    <div className="control control-threshold" title="Passages whose reranker score falls below this floor are left out. Lower finds more, higher only the clearest matches.">
      <span className="control-label">
        Relevance floor
        {autoLabel && (
          <label className="auto-toggle">
            <input type="checkbox" checked={auto} disabled={disabled} onChange={(event) => onChange(event.target.checked ? null : fallback)} />
            auto
          </label>
        )}
      </span>
      <div className={`threshold${auto ? " is-auto" : ""}`}>
        <input
          type="range"
          min={FLOOR_MIN}
          max={FLOOR_MAX}
          step={0.5}
          value={shown}
          disabled={disabled || auto}
          onChange={(event) => onChange(Number(event.target.value))}
          aria-label="Relevance floor"
          aria-valuetext={auto ? autoLabel : `${floorLabel(shown)} (${formatScore(shown)})`}
          style={{ "--fill": `${((shown - FLOOR_MIN) / (FLOOR_MAX - FLOOR_MIN)) * 100}%` }}
        />
        <span className="threshold-readout">
          {auto ? autoLabel : (
            <>
              {floorLabel(shown)} <span className="mono">{formatScore(shown)}</span>
            </>
          )}
        </span>
      </div>
    </div>
  );
}

// The research console: one question box shared by Search (ranked passages)
// and Ask (a written, cited answer), with each mode's retrieval parameters
// laid out underneath as an instrument strip rather than hidden in settings.
export default function Console({
  inputRef,
  docked,
  mode,
  onModeChange,
  askEnabled,
  query,
  onQueryChange,
  onSubmit,
  busy,
  blockedReason,
  scope,
  onClearScope,
  searchParams,
  onSearchParams,
  askParams,
  onAskParams,
  ask,
  defaults,
  onReset,
}) {
  // Grow the box with its content (up to the CSS max-height), shrink it back
  // when cleared — and re-measure when its width changes or the web fonts
  // arrive, since either rewraps the text (a measurement taken mid-entrance
  // or in the fallback font leaves the empty box several lines tall).
  const fit = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
    // Only scroll once the text outgrows the box's max-height.
    el.style.overflowY = el.scrollHeight > el.clientHeight + 1 ? "auto" : "hidden";
  }, [inputRef]);

  useLayoutEffect(fit, [query, docked, fit]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return undefined;
    let width = el.clientWidth;
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      if (el.clientWidth !== width) {
        width = el.clientWidth;
        fit();
      }
    });
    observer?.observe(el);
    document.fonts?.ready.then(fit).catch(() => {});
    return () => observer?.disconnect();
  }, [inputRef, fit]);

  // The retrieval settings stay folded away behind a small toggle until the
  // reader wants them; the toggle shows a one-line summary so the current
  // settings are never a mystery. Remembered per browser.
  const [optionsOpen, setOptionsOpen] = useState(() => loadStored(OPTIONS_KEY, "") === "1");
  useEffect(() => saveStored(OPTIONS_KEY, optionsOpen ? "1" : "0"), [optionsOpen]);

  const isAsk = mode === "ask" && askEnabled;
  const summary = isAsk
    ? [
        ask.noModels ? null : ask.modelLabel(ask.selectedModel),
        askParams.mode === "thorough" ? "thorough" : `quick · ${askParams.topK} sources`,
        askParams.minScore == null ? "auto floor" : `${floorLabel(askParams.minScore).toLowerCase()} floor`,
      ]
        .filter(Boolean)
        .join(" · ")
    : [
        `${searchParams.topK} passages`,
        `${floorLabel(searchParams.minScore).toLowerCase()} floor`,
        BREADTH_LABELS[searchParams.perDoc],
      ].join(" · ");
  const canSubmit = query.trim() && !busy && !blockedReason && !(isAsk && ask.noModels);
  const placeholder = isAsk
    ? scope
      ? `Ask about ${displayTitle(scope.documentName)}…`
      : "Ask a question of your library…"
    : scope
      ? `Search within ${displayTitle(scope.documentName)}…`
      : "Search concepts, passages, names, formulas…";

  return (
    <form
      className={`console${docked ? " docked" : ""}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit) onSubmit();
      }}
    >
      <div className="console-top">
        {askEnabled ? (
          <div className="mode-switch" role="tablist" aria-label="Mode">
            <button
              type="button"
              role="tab"
              aria-selected={!isAsk}
              className={!isAsk ? "active" : ""}
              onClick={() => onModeChange("search")}
            >
              <span className="mode-glyph" aria-hidden="true">§</span> Search
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={isAsk}
              className={isAsk ? "active" : ""}
              onClick={() => onModeChange("ask")}
            >
              <span className="mode-glyph" aria-hidden="true">¶</span> Ask
            </button>
          </div>
        ) : (
          <span className="eyebrow">Search</span>
        )}
        {scope && (
          <span className="scope-chip" title={scope.documentName}>
            <span className="scope-chip-label">within</span>
            <span className="scope-chip-name">{displayTitle(scope.documentName)}</span>
            <button type="button" onClick={onClearScope} aria-label="Search the whole library">
              ×
            </button>
          </span>
        )}
        <button
          type="button"
          className={`options-toggle${optionsOpen ? " open" : ""}`}
          onClick={() => setOptionsOpen((open) => !open)}
          aria-expanded={optionsOpen}
          aria-controls="console-options"
          title={optionsOpen ? "Hide options" : "Show options"}
        >
          {!optionsOpen && <span className="options-summary">{summary}</span>}
          <span className="options-label">Options</span>
          <span className="options-chevron" aria-hidden="true">▾</span>
        </button>
        {docked && (
          <button type="button" className="text-button console-reset" onClick={onReset}>
            {isAsk ? "+ New chat" : "Clear results"}
          </button>
        )}
      </div>

      <div className="console-bar">
        <span className="console-glyph" aria-hidden="true">{isAsk ? "?" : "⌕"}</span>
        <textarea
          ref={inputRef}
          rows={1}
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form.requestSubmit();
            }
          }}
          placeholder={placeholder}
          aria-label={isAsk ? "Question" : "Search query"}
        />
        <button className="console-submit" type="submit" disabled={!canSubmit} aria-label={isAsk ? "Ask" : "Search"}>
          {busy ? <span className="spinner" aria-hidden="true" /> : <span aria-hidden="true">→</span>}
          <span className="console-submit-label">{busy ? (isAsk ? "Answering" : "Searching") : isAsk ? "Ask" : "Search"}</span>
        </button>
      </div>

      <div id="console-options" className={`console-options${optionsOpen ? " open" : ""}`} inert={!optionsOpen}>
        <div className="console-options-inner">
          <div className="console-controls">
            {isAsk ? (
              <>
                {!ask.noModels && (
                  <div className="control">
                    <span className="control-label">Model</span>
                    <select value={ask.selectedModel} disabled={busy} onChange={(event) => ask.setSelectedModel(event.target.value)}>
                      {ask.grouped.map(([provider, list]) => (
                        <optgroup key={provider} label={PROVIDER_LABELS[provider] || provider}>
                          {list.map((model) => (
                            <option
                              key={model.id}
                              value={model.id}
                              disabled={!model.usable}
                              title={model.usable ? undefined : "Add this provider's API key in Settings to use it"}
                            >
                              {model.label}
                              {model.usable ? "" : " — needs API key"}
                            </option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                  </div>
                )}
                <Segmented
                  label="Depth"
                  value={askParams.mode}
                  disabled={busy}
                  onChange={(value) => onAskParams({ ...askParams, mode: value })}
                  options={[
                    { value: "quick", label: "Quick", hint: "One grounded pass over the best passages." },
                    { value: "thorough", label: "Thorough", hint: "Read a wider set of passages, grouped by document, and synthesise across them. Slower." },
                  ]}
                />
                {askParams.mode === "quick" && (
                  <Stepper
                    label="Sources"
                    value={askParams.topK}
                    min={1}
                    max={20}
                    disabled={busy}
                    onChange={(value) => onAskParams({ ...askParams, topK: value })}
                    hint="The fewest passages the answer is written from. Larger cloud models may read more on their own."
                  />
                )}
                <Threshold
                  value={askParams.minScore}
                  disabled={busy}
                  onChange={(value) => onAskParams({ ...askParams, minScore: value })}
                  autoLabel={askParams.mode === "thorough" ? `Auto ${formatScore(defaults.thorough)}` : `Auto ${formatScore(defaults.search)}`}
                  fallback={askParams.mode === "thorough" ? defaults.thorough : defaults.search}
                />
              </>
            ) : (
              <>
                <Stepper
                  label="Passages"
                  value={searchParams.topK}
                  min={1}
                  max={50}
                  disabled={busy}
                  onChange={(value) => onSearchParams({ ...searchParams, topK: value })}
                  hint="How many ranked passages to return."
                />
                <Threshold
                  value={searchParams.minScore}
                  disabled={busy}
                  onChange={(value) => onSearchParams({ ...searchParams, minScore: value })}
                />
                <Segmented
                  label="Breadth"
                  value={searchParams.perDoc}
                  disabled={busy}
                  onChange={(value) => onSearchParams({ ...searchParams, perDoc: value })}
                  hint="Prefer spreading passages across documents. Backfills from the same document when there aren't enough others."
                  options={[
                    { value: 0, label: "Any", hint: "Rank purely by relevance." },
                    { value: 3, label: "≤3/doc", hint: "At most three passages per document, where possible." },
                    { value: 1, label: "1/doc", hint: "One passage per document, where possible — a survey of sources." },
                  ]}
                />
              </>
            )}
          </div>
        </div>
      </div>
      {blockedReason && !docked && <p className="console-hint">{blockedReason}</p>}
    </form>
  );
}
