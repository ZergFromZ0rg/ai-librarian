import { describe, expect, it } from "vitest";

import { parseSseChunk, streamAsk } from "./askStream.js";

describe("parseSseChunk", () => {
  it("parses complete frames and returns the trailing partial", () => {
    const { events, rest } = parseSseChunk(
      'data: {"type":"token","text":"Hello "}\n\ndata: {"type":"token","text":"world"}\n\ndata: {"type":"sou',
    );
    expect(events).toEqual([
      { type: "token", text: "Hello " },
      { type: "token", text: "world" },
    ]);
    expect(rest).toBe('data: {"type":"sou');
  });

  it("reassembles a frame split across two reads", () => {
    const first = parseSseChunk('data: {"type":"token",');
    expect(first.events).toEqual([]);
    const second = parseSseChunk(first.rest + '"text":"hi"}\n\n');
    expect(second.events).toEqual([{ type: "token", text: "hi" }]);
    expect(second.rest).toBe("");
  });

  it("skips a malformed frame without throwing", () => {
    const { events } = parseSseChunk("data: not json\n\ndata: {\"type\":\"error\",\"detail\":\"x\"}\n\n");
    expect(events).toEqual([{ type: "error", detail: "x" }]);
  });
});

describe("streamAsk", () => {
  function fakeResponse(frames) {
    const encoder = new TextEncoder();
    let i = 0;
    return {
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () =>
            i < frames.length
              ? { value: encoder.encode(frames[i++]), done: false }
              : { value: undefined, done: true },
        }),
      },
    };
  }

  it("emits every event across chunk boundaries", async () => {
    globalThis.fetch = async () =>
      fakeResponse([
        'data: {"type":"token","text":"The "}\n\ndata: {"type":"to',
        'ken","text":"absurd"}\n\n',
        'data: {"type":"sources","results":[],"low_confidence":false}\n\n',
      ]);

    const seen = [];
    await streamAsk("/api/ask", { question: "q" }, { onEvent: (e) => seen.push(e) });
    expect(seen).toEqual([
      { type: "token", text: "The " },
      { type: "token", text: "absurd" },
      { type: "sources", results: [], low_confidence: false },
    ]);
  });

  it("throws the server detail on a non-ok response", async () => {
    globalThis.fetch = async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: "Ask mode is disabled on this server." }),
    });
    await expect(streamAsk("/api/ask", {}, {})).rejects.toThrow("disabled");
  });
});
