import { describe, expect, it } from "vitest";

import { prepareMath } from "./markdown.js";

describe("prepareMath", () => {
  it("turns OCR LaTeX delimiters into remark-math syntax", () => {
    expect(prepareMath("angle \\(\\angle 1>\\angle 2\\) holds")).toBe("angle $$\\angle 1>\\angle 2$$ holds");
    expect(prepareMath("so \\[a^2+b^2=c^2\\] done")).toBe("so \n\n$$\na^2+b^2=c^2\n$$\n\n done");
  });

  it("shows Unicode-only $$ blocks from the layout pass as plain text", () => {
    expect(prepareMath("$$ A1 →C1 ∴(A1 ∧A2) $$")).toBe("A1 →C1 ∴(A1 ∧A2)");
    expect(prepareMath("$$ \\frac{1}{2} $$")).toBe("$$ \\frac{1}{2} $$");
  });

  it("leaves prices alone", () => {
    expect(prepareMath("It cost $5 and then $10.")).toBe("It cost $5 and then $10.");
  });
});
