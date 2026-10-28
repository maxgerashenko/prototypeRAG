import { describe, expect, it } from "vitest";
import * as S from "./swipeBack";

const run = (actions: S.SwipeAction[], from: S.SwipeState = S.SWIPE_IDLE) => actions.reduce(S.swipeReducer, from);
const start = (x = 100, y = 300, enabled = true, touches = 1): S.SwipeAction => ({ type: "start", x, y, enabled, touches });
const move = (x: number, y: number): S.SwipeAction => ({ type: "move", x, y, touches: 1 });

describe("swipeBack", () => {
  it("ignores a start when disabled or multi-touch", () => {
    expect(run([start(100, 300, false)])).toBe(S.SWIPE_IDLE);
    expect(run([start(100, 300, true, 2)])).toBe(S.SWIPE_IDLE);
  });
  it("a small move stays undecided", () => {
    expect(run([start(), move(105, 302)]).phase).toBe("tracking");
  });
  it("a tap with a little finger drift is never a swipe", () => {
    expect(run([start(), move(112, 303), move(114, 301)]).phase).toBe("tracking");
  });
  it("a vertical drag is not a swipe", () => {
    expect(run([start(), move(110, 340)])).toBe(S.SWIPE_IDLE);
  });
  it("a leftward drag is not a swipe", () => {
    expect(run([start(), move(60, 300)])).toBe(S.SWIPE_IDLE);
  });
  it("a rightward drag moves the screen at most SWIPE_HINT_MAX px", () => {
    const s = run([start(), move(150, 305)]);
    expect(s.phase).toBe("swiping");
    expect(S.swipeOffset(s)).toBe(8);
    expect(S.swipeOffset(run([start(), move(150, 300), move(600, 300)]))).toBe(S.SWIPE_HINT_MAX);
  });
  it("arms past the threshold and fires the crossing once", () => {
    const a = run([start(), move(150, 300)]);
    const b = run([move(200, 300)], a);
    expect(S.isSwipeArmed(a)).toBe(false);
    expect(S.isSwipeArmed(b)).toBe(true);
    expect(S.swipeCrossed(a, b)).toBe(true);
    expect(S.swipeCrossed(b, run([move(210, 300)], b))).toBe(false);
  });
  it("once swiping, vertical wobble does not cancel", () => {
    expect(run([start(), move(150, 300), move(160, 380)]).phase).toBe("swiping");
  });
  it("dragging back left shrinks the offset to 0; end and cancel reset", () => {
    const s = run([start(), move(150, 300), move(90, 300)]);
    expect(S.swipeOffset(s)).toBe(0);
    expect(run([start(), move(150, 300), { type: "end" }])).toBe(S.SWIPE_IDLE);
    expect(run([start(), move(150, 300), { type: "cancel" }])).toBe(S.SWIPE_IDLE);
  });
  it("a second finger cancels", () => {
    expect(run([start(), move(150, 300), { type: "move", x: 160, y: 300, touches: 2 }])).toBe(S.SWIPE_IDLE);
  });
});
