import { describe, expect, it } from "vitest";
import { diaryMonths, isHalfStar, monthCounts, ratingNumber, starText } from "./ratings.js";

describe("half-star ratings", () => {
  it("writes stars with a half", () => {
    expect(starText(3.5)).toBe("★★★½");
    expect(starText(0.5)).toBe("½");
    expect(starText(5)).toBe("★★★★★");
    expect(starText(null)).toBe("");
    expect(starText(2.7)).toBe("");
  });
  it("accepts only half steps from 0.5 to 5", () => {
    expect([0.5, 1, 4.5, 5].every(isHalfStar)).toBe(true);
    expect([0, 5.5, 2.25, NaN].some(isHalfStar)).toBe(false);
  });
  it("formats the number without a trailing .0", () => {
    expect(ratingNumber(4)).toBe("4");
    expect(ratingNumber(3.5)).toBe("3.5");
  });
});

describe("diary", () => {
  const books = [
    { title: "B", reading_status: "read", finished_at: "2025-03-09" },
    { title: "A", reading_status: "read", finished_at: "2025-03-20" },
    { title: "C", reading_status: "read", finished_at: "2024-12-01" },
    { title: "D", reading_status: "read" },
    { title: "E", reading_status: "reading", finished_at: "2025-03-01" },
  ];
  it("groups finished, dated books by month, newest first", () => {
    const groups = diaryMonths(books);
    expect(groups.map((group) => group.label)).toEqual(["March 2025", "December 2024"]);
    expect(groups[0].entries.map((doc) => doc.title)).toEqual(["A", "B"]);
    expect(groups[0]).toMatchObject({ pages: 0, average: 0 });
  });
  it("counts finishes per month of a year", () => {
    expect(monthCounts(books, 2025)[2].value).toBe(2);
    expect(monthCounts(books, 2024)[11].value).toBe(1);
  });
});
