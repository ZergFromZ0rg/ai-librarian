import { describe, expect, it } from "vitest";
import { CLOTH_COUNT, clothColor, hashSeed } from "./clothCovers.js";

describe("cloth covers", () => {
  it("gives the same binding to the same book every time", () => {
    expect(clothColor("a1b2c3d4e5f6")).toEqual(clothColor("a1b2c3d4e5f6"));
    expect(hashSeed("a1b2c3d4e5f6")).toBe(hashSeed("a1b2c3d4e5f6"));
  });

  it("spreads books across the whole palette with a darker spine edge", () => {
    const seen = new Set();
    for (let index = 0; index < 400; index += 1) {
      const colour = clothColor(`book-${index}`);
      expect(colour.bg).toMatch(/^#[0-9a-f]{6}$/);
      expect(colour.edge).not.toBe(colour.bg);
      seen.add(colour.bg);
    }
    expect(seen.size).toBe(CLOTH_COUNT);
  });

  it("copes with a missing seed", () => {
    expect(clothColor(undefined).bg).toMatch(/^#[0-9a-f]{6}$/);
  });
});
