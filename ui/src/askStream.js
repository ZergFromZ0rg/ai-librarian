// Server-Sent Events parsing for the /ask stream.
//
// The endpoint emits one JSON object per `data:` frame:
//   {"type":"token","text":"..."}          incremental answer text
//   {"type":"sources","results":[...],"low_confidence":bool}   sent once at the end
//   {"type":"error","detail":"..."}         generation failed after the stream opened

// Pull every complete frame out of `buffer`, returning the parsed events plus
// the trailing partial frame to carry into the next read.
export function parseSseChunk(buffer) {
  const events = [];
  let rest = buffer;
  let sep;
  while ((sep = rest.indexOf("\n\n")) !== -1) {
    const frame = rest.slice(0, sep);
    rest = rest.slice(sep + 2);
    for (const line of frame.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const payload = trimmed.slice(5).trim();
      if (!payload) continue;
      try {
        events.push(JSON.parse(payload));
      } catch {
        // A malformed frame is skipped rather than breaking the stream.
      }
    }
  }
  return { events, rest };
}

// POST to `url` and invoke `onEvent` for each parsed SSE event as it arrives.
// Resolves when the stream ends; rejects if the request itself fails.
export async function streamAsk(url, body, { headers, signal, onEvent } = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(headers || {}) },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    let detail = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch {
      // no JSON body
    }
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseChunk(buffer);
    buffer = rest;
    for (const event of events) onEvent?.(event);
  }
}
