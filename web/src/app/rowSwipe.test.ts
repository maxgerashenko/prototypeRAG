import { describe, expect, it } from "vitest";
import { ROW_MARGIN, ROW_MAX, ROW_REVEAL, ROW_SLOP, rowDecision, rowFires, rowOffset } from "./rowSwipe";

describe("rowDecision", () => {
  it("waits inside the slop", () => expect(rowDecision(-4, 3)).toBe("wait"));
  it("swipes on a clear leftward, horizontal move", () => expect(rowDecision(-30, 6)).toBe("swipe"));
  it("ignores vertical moves", () => expect(rowDecision(-5, 30)).toBe("none"));
  it("ignores rightward moves", () => expect(rowDecision(30, 2)).toBe("none"));
});

describe("rowOffset / rowFires", () => {
  it("excludes the slop and clamps", () => {
    expect(rowOffset(-ROW_SLOP)).toBe(0);
    expect(rowOffset(-50)).toBe(50 - ROW_SLOP);
    expect(rowOffset(-1000)).toBe(ROW_MAX);
    expect(rowOffset(20)).toBe(0);
  });
  it("fires only at the action width plus the margin", () => {
    expect(rowFires(ROW_REVEAL)).toBe(false);
    expect(rowFires(ROW_REVEAL + ROW_MARGIN - 1)).toBe(false);
    expect(rowFires(ROW_REVEAL + ROW_MARGIN)).toBe(true);
  });
});
