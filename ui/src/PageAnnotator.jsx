import React, { useEffect, useMemo, useRef, useState } from "react";

import { clamp01, cleanWord, contextAround, COLORS, isSingleWord, markAt, selectionRects, selectionText, simplifyStroke, wordAt } from "./annotations.js";

const TEXT_BOX_WIDTH = 0.32;

// Keep receiving a drag's events after the pointer leaves the element; a
// pointer that is already gone (or a synthetic one) simply is not captured.
function capture(event) {
  try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* not capturable */ }
}

// One page of a PDF with the reader's marks drawn over it, and the tools that
// make them. The page itself is the server-rendered image; on top sit an SVG of
// marks (all in page space, 0..1) and an invisible layer that turns pointer
// movement into word selections, strokes, and text boxes. The server's word
// boxes make selection exact without a client-side PDF engine.
export default function PageAnnotator({
  apiBase, documentId, page, imageSrc, marks, tool, colors, thickness, textSize, zoom,
  selectedId, savedWords, onLoad, onError, onCreate, onUpdate, onDelete, onSelect, onAddWord,
}) {
  const boxRef = useRef(null);
  const drag = useRef(null);
  const [words, setWords] = useState([]);
  const [pageWidth, setPageWidth] = useState(900);
  const [selection, setSelection] = useState(null);
  const [stroke, setStroke] = useState(null);
  const [draft, setDraft] = useState(null);
  const [editing, setEditing] = useState(null);
  const [moving, setMoving] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setWords([]);
    setSelection(null);
    fetch(`${apiBase}/documents/${documentId}/page/${page}/words`)
      .then((response) => (response.ok ? response.json() : { words: [] }))
      .then((data) => !cancelled && setWords(data.words || []))
      .catch(() => {});
    return () => { cancelled = true; };
  }, [apiBase, documentId, page]);

  useEffect(() => {
    const box = boxRef.current;
    if (!box || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() => setPageWidth(box.getBoundingClientRect().width || 900));
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  useEffect(() => { if (tool !== "select" && tool !== "highlight" && tool !== "underline") setSelection(null); }, [tool]);

  const selectedRects = useMemo(() => (selection ? selectionRects(words, selection.a, selection.b) : []), [words, selection]);
  const selectedText = selection ? selectionText(words, selection.a, selection.b) : "";
  const single = selection && isSingleWord(selectedText);

  function pointOf(event) {
    const rect = boxRef.current.getBoundingClientRect();
    return [clamp01((event.clientX - rect.left) / rect.width), clamp01((event.clientY - rect.top) / rect.height)];
  }

  function markSelection(kind, color, range = selection) {
    if (!range) return null;
    const created = onCreate({
      page, kind, color: color || colors[kind], rects: selectionRects(words, range.a, range.b), text: selectionText(words, range.a, range.b),
    });
    setSelection(null);
    return created;
  }

  function down(event) {
    if (event.button !== 0 || event.target.closest(".page-pop, .page-textmark, .page-textdraft")) return;
    const [x, y] = pointOf(event);
    if (tool === "erase") {
      const hit = markAt(marks, x, y);
      if (hit) onDelete(hit);
      return;
    }
    if (tool === "text") {
      setDraft({ x: Math.min(x, 1 - TEXT_BOX_WIDTH), y, value: "" });
      return;
    }
    capture(event);
    if (tool === "draw") {
      drag.current = { type: "draw", points: [[x, y]] };
      setStroke([[x, y]]);
      return;
    }
    const start = wordAt(words, x, y);
    drag.current = { type: "words", start, end: start, moved: false };
    setSelection(start >= 0 ? { a: start, b: start } : null);
  }

  function move(event) {
    const state = drag.current;
    if (!state) return;
    const [x, y] = pointOf(event);
    if (state.type === "draw") {
      state.points.push([x, y]);
      setStroke([...state.points]);
    } else if (state.type === "words" && state.start >= 0) {
      const index = wordAt(words, x, y);
      if (index >= 0) {
        if (index !== state.start) state.moved = true;
        state.end = index;
        setSelection((current) => (current && current.b === index ? current : { a: state.start, b: index }));
      }
    }
  }

  function up(event) {
    const state = drag.current;
    drag.current = null;
    if (!state) return;
    if (state.type === "draw") {
      const points = simplifyStroke(state.points);
      setStroke(null);
      if (points.length > 1) onCreate({ page, kind: "ink", color: colors.ink, paths: [points], width: thickness });
      return;
    }
    const [x, y] = pointOf(event);
    if (tool === "select" && !state.moved) {
      const hit = markAt(marks, x, y);
      if (hit) {
        setSelection(null);
        onSelect(hit.annotation_id);
        return;
      }
      if (state.start < 0) onSelect(null);
    }
    if (state.start < 0) return;
    if (tool === "highlight" || tool === "underline") markSelection(tool, undefined, { a: state.start, b: state.end });
  }

  function doubleClick(event) {
    if (tool !== "select") return;
    const [x, y] = pointOf(event);
    const index = wordAt(words, x, y);
    if (index >= 0) setSelection({ a: index, b: index });
  }

  function commitDraft() {
    const current = draft;
    setDraft(null);
    const value = (current?.value || "").trim();
    if (!value) return;
    const lines = value.split("\n").length + Math.floor(value.length / 36);
    const height = Math.min(1 - current.y, Math.max(textSize * 1.6, lines * textSize * 1.45));
    onCreate({ page, kind: "text", color: colors.text, text: value, size: textSize, rects: [[current.x, current.y, Math.min(1, current.x + TEXT_BOX_WIDTH), current.y + height]] });
  }

  function startMove(event, mark) {
    if (tool === "erase") { onDelete(mark); return; }
    if (tool !== "select" || editing === mark.annotation_id) return;
    event.stopPropagation();
    capture(event);
    const [x, y] = pointOf(event);
    setMoving({ id: mark.annotation_id, from: [x, y], dx: 0, dy: 0, moved: false });
  }

  function dragMove(event, mark) {
    if (!moving || moving.id !== mark.annotation_id) return;
    const [x, y] = pointOf(event);
    const rect = mark.rects[0];
    const dx = Math.min(Math.max(x - moving.from[0], -rect[0]), 1 - rect[2]);
    const dy = Math.min(Math.max(y - moving.from[1], -rect[1]), 1 - rect[3]);
    setMoving({ ...moving, dx, dy, moved: moving.moved || Math.hypot(dx, dy) > 0.004 });
  }

  function endMove(mark) {
    if (!moving || moving.id !== mark.annotation_id) return;
    const { dx, dy, moved } = moving;
    setMoving(null);
    if (!moved) { onSelect(mark.annotation_id); return; }
    onUpdate(mark.annotation_id, { rects: [mark.rects[0].map((value, i) => (i % 2 ? value + dy : value + dx))] });
  }

  async function copySelection() {
    try { await navigator.clipboard.writeText(selectedText); setCopied(true); window.setTimeout(() => setCopied(false), 1200); } catch { /* clipboard blocked */ }
  }

  const last = selectedRects[selectedRects.length - 1];
  const popBelow = last && last[3] < 0.9;
  const drawing = tool !== "select" && tool !== "erase";

  return (
    <div
      className={`page-box tool-${tool}`}
      ref={boxRef}
      style={{ "--pw": `${pageWidth}px`, width: `calc(min(100%, 900px) * ${zoom})` }}
    >
      <img key={imageSrc} src={imageSrc} alt={`Page ${page}`} draggable={false} onLoad={onLoad} onError={onError} />
      <svg className="page-marks" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
        {marks.map((mark) => {
          const id = mark.annotation_id;
          const chosen = id === selectedId;
          if (mark.kind === "highlight") {
            return mark.rects.map((r, i) => (
              <rect key={`${id}:${i}`} className={`mark-highlight${chosen ? " chosen" : ""}`} x={r[0]} y={r[1]} width={r[2] - r[0]} height={r[3] - r[1]} fill={mark.color} />
            ));
          }
          if (mark.kind === "underline") {
            return mark.rects.map((r, i) => (
              <g key={`${id}:${i}`} className={chosen ? "chosen" : ""}>
                {chosen && <rect className="mark-halo" x={r[0]} y={r[1]} width={r[2] - r[0]} height={r[3] - r[1]} />}
                <line x1={r[0]} x2={r[2]} y1={r[3] - 0.002} y2={r[3] - 0.002} stroke={mark.color} strokeWidth="2" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
              </g>
            ));
          }
          if (mark.kind === "ink") {
            return (mark.paths || []).map((points, i) => (
              <polyline
                key={`${id}:${i}`} className={chosen ? "chosen" : ""} fill="none" stroke={mark.color} strokeLinecap="round" strokeLinejoin="round"
                strokeWidth={Math.max(1, (mark.width || 0.004) * pageWidth)} vectorEffect="non-scaling-stroke" points={points.map((p) => p.join(",")).join(" ")}
              />
            ));
          }
          return null;
        })}
        {stroke && stroke.length > 1 && (
          <polyline fill="none" stroke={colors.ink} strokeLinecap="round" strokeLinejoin="round" strokeWidth={Math.max(1, thickness * pageWidth)} vectorEffect="non-scaling-stroke" points={stroke.map((p) => p.join(",")).join(" ")} />
        )}
        {selectedRects.map((r, i) => (
          <rect key={`sel:${i}`} className="mark-selection" x={r[0]} y={r[1]} width={r[2] - r[0]} height={r[3] - r[1]} />
        ))}
      </svg>
      {marks.filter((mark) => mark.kind === "text").map((mark) => {
        const id = mark.annotation_id;
        const [x0, y0, x1] = mark.rects[0];
        const shift = moving?.id === id ? moving : { dx: 0, dy: 0 };
        const style = { left: `${(x0 + shift.dx) * 100}%`, top: `${(y0 + shift.dy) * 100}%`, width: `${(x1 - x0) * 100}%`, color: mark.color, fontSize: `calc(var(--pw) * ${mark.size || 0.018})` };
        if (editing === id) {
          return (
            <textarea
              key={id} className="page-textmark editing" style={style} defaultValue={mark.text} autoFocus
              onFocus={(event) => event.target.setSelectionRange(event.target.value.length, event.target.value.length)}
              onBlur={(event) => { setEditing(null); if (event.target.value.trim() && event.target.value.trim() !== mark.text) onUpdate(id, { text: event.target.value }); }}
              onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setEditing(null); } }}
            />
          );
        }
        return (
          <div
            key={id} style={style} role="note"
            className={`page-textmark${id === selectedId ? " chosen" : ""}${tool === "select" || tool === "erase" ? " live" : ""}`}
            onPointerDown={(event) => startMove(event, mark)} onPointerMove={(event) => dragMove(event, mark)} onPointerUp={() => endMove(mark)}
            onDoubleClick={() => tool === "select" && setEditing(id)}
          >
            {mark.text}
          </div>
        );
      })}
      {draft && (
        <textarea
          className="page-textmark page-textdraft editing" autoFocus placeholder="Type, then click away"
          style={{ left: `${draft.x * 100}%`, top: `${draft.y * 100}%`, width: `${TEXT_BOX_WIDTH * 100}%`, color: colors.text, fontSize: `calc(var(--pw) * ${textSize})` }}
          value={draft.value} onChange={(event) => setDraft({ ...draft, value: event.target.value })}
          onBlur={commitDraft}
          onKeyDown={(event) => {
            if (event.key === "Escape") { event.stopPropagation(); setDraft(null); }
            else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) event.currentTarget.blur();
          }}
        />
      )}
      <div
        className={`page-layer${drawing ? " drawing" : ""}`}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onDoubleClick={doubleClick}
        onPointerCancel={() => { drag.current = null; setStroke(null); }}
      />
      {selection && !drag.current && tool === "select" && last && (
        <div className="page-pop" style={{ left: `${Math.min(last[2], 0.72) * 100}%`, top: `${(popBelow ? last[3] : last[1]) * 100}%`, transform: popBelow ? "translate(-100%, 6px)" : "translate(-100%, calc(-100% - 6px))" }} role="toolbar" aria-label="Selection">
          {COLORS.highlight.map((color) => (
            <button key={color} type="button" className="swatch" style={{ background: color }} aria-label="Highlight" title="Highlight" onClick={() => markSelection("highlight", color)} />
          ))}
          <button type="button" className="text-button" onClick={() => markSelection("underline")}>Underline</button>
          <button type="button" className="text-button" onClick={async () => { const created = await markSelection("highlight"); if (created) onSelect(created.annotation_id, { comment: true }); }}>Comment</button>
          <button type="button" className="text-button" onClick={copySelection}>{copied ? "Copied" : "Copy"}</button>
          {single && (
            <button
              type="button" className="text-button accent" disabled={savedWords?.has(cleanWord(selectedText)?.toLowerCase())}
              onClick={() => { onAddWord?.({ word: cleanWord(selectedText), page, quote: contextAround(words, selection.a) }); setSelection(null); }}
            >
              {savedWords?.has(cleanWord(selectedText)?.toLowerCase()) ? "In vocabulary" : "+ Vocabulary"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
