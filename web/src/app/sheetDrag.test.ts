import { describe, expect, it } from "vitest";
import { canStartDrag, dragDecision, shouldClose } from "./sheetDrag";

describe("canStartDrag", () => {
  it("needs a panel at the top and a non-field target", () => {
    expect(canStartDrag(0, false)).toBe(true);
    expect(canStartDrag(12, false)).toBe(false);
    expect(canStartDrag(0, true)).toBe(false);
  });
});

describe("dragDecision", () => {
  it("waits inside the slop", () => expect(dragDecision(3, 5)).toBe("wait"));
  it("starts on a clear downward move", () => expect(dragDecision(4, 20)).toBe("drag"));
  it("never starts on an upward move", () => expect(dragDecision(0, -20)).toBe("none"));
  it("never starts on a mostly horizontal move", () => {
    expect(dragDecision(30, 12)).toBe("none");
    expect(dragDecision(-30, 12)).toBe("none");
  });
});

describe("shouldClose", () => {
  it("closes past the distance", () => expect(shouldClose(120, 900)).toBe(true));
  it("springs back on a short slow drag", () => expect(shouldClose(60, 600)).toBe(false));
  it("closes on a fast flick", () => expect(shouldClose(50, 80)).toBe(true));
  it("ignores a tiny fast twitch", () => expect(shouldClose(10, 5)).toBe(false));
});
