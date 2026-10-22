import React, { useState } from "react";

import { niceCeiling } from "./libraryRail.js";

// Books finished per month (or pages), as a line: the quiet shape of a year's
// reading. One series in the app's accent, so there is no legend; the heading
// names it. Hover or focus a month to read its numbers; the same figures sit in
// a table for screen readers.
const W = 280;
const H = 132;
const PLOT = { left: 30, right: 10, top: 10, bottom: 110 };

export default function MonthLine({ months, metric }) {
  const [hover, setHover] = useState(null);
  const values = months.map((month) => month[metric]);
  const top = niceCeiling(Math.max(0, ...values));
  const x = (index) => PLOT.left + (index / (months.length - 1)) * (W - PLOT.left - PLOT.right);
  const y = (value) => PLOT.bottom - (value / top) * (PLOT.bottom - PLOT.top);
  const points = values.map((value, index) => [x(index), y(value)]);
  const line = points.map(([px, py], index) => `${index ? "L" : "M"}${px.toFixed(1)} ${py.toFixed(1)}`).join(" ");
  const area = `${line} L${points[points.length - 1][0].toFixed(1)} ${PLOT.bottom} L${points[0][0].toFixed(1)} ${PLOT.bottom} Z`;
  const ticks = top % 2 === 0 ? [0, top / 2, top] : [0, top];
  const shown = hover == null ? null : months[hover];
  const unit = metric === "books" ? "book" : "page";
  return <div className="month-chart">
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${metric === "books" ? "Books" : "Pages"} finished each month over the last ${months.length} months`} onPointerLeave={() => setHover(null)}>
      {ticks.map((tick) => <g key={tick}>
        <line className="month-grid" x1={PLOT.left} x2={W - PLOT.right} y1={y(tick)} y2={y(tick)} />
        <text className="month-axis" x={PLOT.left - 6} y={y(tick) + 3.5} textAnchor="end">{tick.toLocaleString()}</text>
      </g>)}
      <path className="month-area" d={area} />
      <path className="month-line" d={line} />
      {hover != null && <line className="month-cross" x1={points[hover][0]} x2={points[hover][0]} y1={PLOT.top} y2={PLOT.bottom} />}
      {points.map(([px, py], index) => <circle key={months[index].key} className={`month-dot${hover === index ? " on" : ""}`} cx={px} cy={py} r={hover === index ? 5 : 4} />)}
      {months.map((month, index) => ((months.length - 1 - index) % 3 === 0) && <text key={month.key} className="month-axis" x={x(index)} y={H - 6} textAnchor="middle">{month.label}</text>)}
      {points.map(([px], index) => <rect key={`hit-${months[index].key}`} className="month-hit" x={px - (W - PLOT.left - PLOT.right) / (months.length - 1) / 2} y={0} width={(W - PLOT.left - PLOT.right) / (months.length - 1)} height={H} onPointerEnter={() => setHover(index)} />)}
    </svg>
    {shown && <div className={`month-tip${hover < 3 ? " left" : hover > months.length - 4 ? " right" : ""}`} style={{ left: `${(points[hover][0] / W) * 100}%`, top: `${(points[hover][1] / H) * 100}%` }}>
      <strong>{shown.label} {shown.year}</strong>
      <span>{shown[metric].toLocaleString()} {unit}{shown[metric] === 1 ? "" : "s"}{metric === "books" && shown.pages ? ` · ${shown.pages.toLocaleString()} pages` : ""}</span>
    </div>}
    <table className="sr-only">
      <caption>{metric === "books" ? "Books" : "Pages"} finished each month</caption>
      <thead><tr><th scope="col">Month</th><th scope="col">Books</th><th scope="col">Pages</th></tr></thead>
      <tbody>{months.map((month) => <tr key={month.key}><th scope="row">{month.label} {month.year}</th><td>{month.books}</td><td>{month.pages}</td></tr>)}</tbody>
    </table>
  </div>;
}
