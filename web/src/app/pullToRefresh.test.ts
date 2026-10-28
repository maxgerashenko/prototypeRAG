import { describe, expect, it, vi } from "vitest";
import * as P from "./pullToRefresh";

function run(actions: P.PullAction[], from = P.IDLE): P.PullState {
  return actions.reduce(P.pullReducer, from);
}

describe("pullToRefresh", () => {
  describe("dampen", () => {
    it("returns 0 for negative, zero, or NaN", () => {
      expect(P.dampen(-5)).toBe(0);
      expect(P.dampen(0)).toBe(0);
      expect(P.dampen(NaN)).toBe(0);
    });
    it("applies resistance and caps at PULL_MAX", () => {
      expect(P.dampen(40)).toBe(20);
      expect(P.dampen(1000)).toBe(P.PULL_MAX);
    });
  });

  describe("start", () => {
    it("returns IDLE when disabled, not at top, or multi-touch", () => {
      expect(P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: false, enabled: true, touches: 1 })).toBe(P.IDLE);
      expect(P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: false, touches: 1 })).toBe(P.IDLE);
      expect(P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 2 })).toBe(P.IDLE);
    });
    it("starts tracking on valid input", () => {
      const state = P.pullReducer(P.IDLE, { type: "start", x: 10, y: 20, atTop: true, enabled: true, touches: 1 });
      expect(state).toEqual({ phase: "tracking", startX: 10, startY: 20 });
    });
  });

  describe("move in tracking", () => {
    it("stays tracking for small downward move and returns same object", () => {
      const start = P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 });
      const moved = P.pullReducer(start, { type: "move", x: 0, y: 5, touches: 1 });
      expect(moved).toBe(start);
      expect(moved).toEqual({ phase: "tracking", startX: 0, startY: 0 });
    });
  });

  describe("move in pulling", () => {
    it("transitions to pulling at 20px down with distance 6", () => {
      const start = P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 });
      const pulled = P.pullReducer(start, { type: "move", x: 0, y: 20, touches: 1 });
      expect(pulled).toEqual({ phase: "pulling", startY: 0, distance: 6 });
    });
    it("caps distance at PULL_MAX for large pull", () => {
      const start = P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 });
      const pulled = P.pullReducer(start, { type: "move", x: 0, y: 300, touches: 1 });
      expect(pulled).toEqual({ phase: "pulling", startY: 0, distance: P.PULL_MAX });
    });
  });

  describe("move horizontal or upward", () => {
    it("returns IDLE for horizontal swipe", () => {
      const start = P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 });
      const moved = P.pullReducer(start, { type: "move", x: 30, y: 10, touches: 1 });
      expect(moved).toBe(P.IDLE);
    });
    it("returns IDLE for upward movement", () => {
      const start = P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 });
      const moved = P.pullReducer(start, { type: "move", x: 0, y: -20, touches: 1 });
      expect(moved).toBe(P.IDLE);
    });
  });

  describe("end", () => {
    it("transitions to refreshing when distance >= threshold", () => {
      const state = run([
        { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 },
        { type: "move", x: 0, y: 150, touches: 1 },
        { type: "end" }
      ]);
      expect(state).toEqual({ phase: "refreshing" });
    });
    it("returns to IDLE when distance < threshold", () => {
      const state = run([
        { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 },
        { type: "move", x: 0, y: 50, touches: 1 },
        { type: "end" }
      ]);
      expect(state).toBe(P.IDLE);
    });
  });

  describe("refreshing state", () => {
    it("ignores start, move, end, cancel", () => {
      const refreshing = { phase: "refreshing" as const };
      expect(P.pullReducer(refreshing, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 })).toBe(refreshing);
      expect(P.pullReducer(refreshing, { type: "move", x: 0, y: 10, touches: 1 })).toBe(refreshing);
      expect(P.pullReducer(refreshing, { type: "end" })).toBe(refreshing);
      expect(P.pullReducer(refreshing, { type: "cancel" })).toBe(refreshing);
    });
    it("transitions to IDLE on done", () => {
      const refreshing = { phase: "refreshing" as const };
      expect(P.pullReducer(refreshing, { type: "done" })).toBe(P.IDLE);
    });
  });

  describe("cancel", () => {
    it("returns IDLE when pulling", () => {
      const start = P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 });
      const pulled = P.pullReducer(start, { type: "move", x: 0, y: 100, touches: 1 });
      expect(P.pullReducer(pulled, { type: "cancel" })).toBe(P.IDLE);
    });
  });

  describe("multi-touch during pulling", () => {
    it("returns IDLE on second finger", () => {
      const start = P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 });
      const pulled = P.pullReducer(start, { type: "move", x: 0, y: 100, touches: 1 });
      expect(P.pullReducer(pulled, { type: "move", x: 0, y: 110, touches: 2 })).toBe(P.IDLE);
    });
  });

  describe("finger back above start", () => {
    it("stays pulling with distance 0", () => {
      const start = P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 });
      const pulled = P.pullReducer(start, { type: "move", x: 0, y: 100, touches: 1 });
      const movedUp = P.pullReducer(pulled, { type: "move", x: 0, y: -10, touches: 1 });
      expect(movedUp).toEqual({ phase: "pulling", startY: 0, distance: 0 });
    });
  });

  describe("helpers", () => {
    it("shouldPreventScroll", () => {
      expect(P.shouldPreventScroll(P.IDLE)).toBe(false);
      const pulling = { phase: "pulling" as const, startY: 0, distance: 10 };
      expect(P.shouldPreventScroll(pulling)).toBe(true);
    });
    it("pullProgress", () => {
      expect(P.pullProgress(P.IDLE)).toBe(0);
      const half = { phase: "pulling" as const, startY: 0, distance: P.PULL_THRESHOLD / 2 };
      expect(P.pullProgress(half)).toBe(0.5);
      const max = { phase: "pulling" as const, startY: 0, distance: P.PULL_THRESHOLD };
      expect(P.pullProgress(max)).toBe(1);
      expect(P.pullProgress({ phase: "refreshing" as const })).toBe(1);
    });
    it("isArmed", () => {
      expect(P.isArmed(P.IDLE)).toBe(false);
      const armed = { phase: "pulling" as const, startY: 0, distance: P.PULL_THRESHOLD };
      expect(P.isArmed(armed)).toBe(true);
    });
  });

  describe("move in idle", () => {
    it("returns same object", () => {
      expect(P.pullReducer(P.IDLE, { type: "move", x: 0, y: 10, touches: 1 })).toBe(P.IDLE);
    });
  });

  describe("armedCrossed", () => {
    it("crosses exactly once per threshold crossing", () => {
      const start = P.pullReducer(P.IDLE, { type: "start", x: 0, y: 0, atTop: true, enabled: true, touches: 1 });
      const d1 = P.pullReducer(start, { type: "move", x: 0, y: 40, touches: 1 });
      const d2 = P.pullReducer(d1, { type: "move", x: 0, y: 100, touches: 1 });
      const d3 = P.pullReducer(d2, { type: "move", x: 0, y: 140, touches: 1 });
      const d4 = P.pullReducer(d3, { type: "move", x: 0, y: 160, touches: 1 });
      const d5 = P.pullReducer(d4, { type: "move", x: 0, y: 200, touches: 1 });

      expect(P.armedCrossed(start, d1)).toBe(false);
      expect(P.armedCrossed(d1, d2)).toBe(false);
      expect(P.armedCrossed(d2, d3)).toBe(true);
      expect(P.armedCrossed(d3, d4)).toBe(false);
      expect(P.armedCrossed(d4, d5)).toBe(false);

      const back = P.pullReducer(d5, { type: "move", x: 0, y: 60, touches: 1 });
      const up = P.pullReducer(back, { type: "move", x: 0, y: 180, touches: 1 });
      expect(P.armedCrossed(back, up)).toBe(true);
    });
    it("returns false for invalid transitions", () => {
      expect(P.armedCrossed(P.IDLE, P.IDLE)).toBe(false);
      const armed = { phase: "pulling" as const, startY: 0, distance: P.PULL_THRESHOLD };
      const refreshing = { phase: "refreshing" as const };
      expect(P.armedCrossed(armed, refreshing)).toBe(false);
    });
  });

  describe("vibrateTick", () => {
    it("returns false for undefined or missing vibrate", () => {
      expect(P.vibrateTick(undefined)).toBe(false);
      expect(P.vibrateTick({})).toBe(false);
    });
    it("returns true and calls vibrate once with VIBRATE_MS", () => {
      const mockVibrate = vi.fn(() => true);
      const nav = { vibrate: mockVibrate };
      expect(P.vibrateTick(nav)).toBe(true);
      expect(mockVibrate).toHaveBeenCalledTimes(1);
      expect(mockVibrate).toHaveBeenCalledWith(P.VIBRATE_MS);
    });
    it("returns false when vibrate throws", () => {
      const mockVibrate = vi.fn(() => { throw new Error("fail"); });
      const nav = { vibrate: mockVibrate };
      expect(P.vibrateTick(nav)).toBe(false);
    });
  });

  describe("start from non-idle", () => {
    it("starts a fresh tracking state from tracking", () => {
      const tracking = { phase: "tracking" as const, startX: 0, startY: 0 };
      const next = P.pullReducer(tracking, { type: "start", x: 10, y: 20, atTop: true, enabled: true, touches: 1 });
      expect(next).toEqual({ phase: "tracking", startX: 10, startY: 20 });
    });
    it("starts a fresh tracking state from pulling", () => {
      const pulling = { phase: "pulling" as const, startY: 0, distance: 10 };
      const next = P.pullReducer(pulling, { type: "start", x: 5, y: 15, atTop: true, enabled: true, touches: 1 });
      expect(next).toEqual({ phase: "tracking", startX: 5, startY: 15 });
    });
  });

  describe("move with multi-touch in tracking", () => {
    it("returns IDLE when touches becomes 2", () => {
      const tracking = { phase: "tracking" as const, startX: 0, startY: 0 };
      const next = P.pullReducer(tracking, { type: "move", x: 0, y: 10, touches: 2 });
      expect(next).toBe(P.IDLE);
    });
  });

  describe("end in tracking", () => {
    it("returns IDLE", () => {
      const tracking = { phase: "tracking" as const, startX: 0, startY: 0 };
      const next = P.pullReducer(tracking, { type: "end" });
      expect(next).toBe(P.IDLE);
    });
  });

  describe("move in pulling with unchanged distance", () => {
    it("returns the same object when distance does not change", () => {
      const pulling = { phase: "pulling" as const, startY: 0, distance: 10 };
      const next = P.pullReducer(pulling, { type: "move", x: 0, y: 28, touches: 1 });
      expect(next).toBe(pulling);
    });
  });

  describe("done from idle", () => {
    it("returns the same IDLE object", () => {
      const next = P.pullReducer(P.IDLE, { type: "done" });
      expect(next).toBe(P.IDLE);
    });
  });

  describe("end at exact threshold", () => {
    it("transitions to refreshing when distance equals PULL_THRESHOLD", () => {
      const pulling = { phase: "pulling" as const, startY: 0, distance: P.PULL_THRESHOLD };
      const next = P.pullReducer(pulling, { type: "end" });
      expect(next).toEqual({ phase: "refreshing" });
    });
  });

  describe("cancel and end in idle", () => {
    it("cancel in tracking gives IDLE", () => {
      const tracking = { phase: "tracking" as const, startX: 0, startY: 0 };
      const next = P.pullReducer(tracking, { type: "cancel" });
      expect(next).toBe(P.IDLE);
    });
    it("cancel in idle returns the same object", () => {
      const next = P.pullReducer(P.IDLE, { type: "cancel" });
      expect(next).toBe(P.IDLE);
    });
    it("end in idle returns the same object", () => {
      const next = P.pullReducer(P.IDLE, { type: "end" });
      expect(next).toBe(P.IDLE);
    });
  });
});
