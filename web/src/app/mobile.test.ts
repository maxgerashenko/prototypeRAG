import { describe, expect, it } from "vitest";
import { isMobileBrowser, browserEnv, type BrowserEnv } from "./mobile";

const baseEnv: BrowserEnv = {
  userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36",
  platform: "MacIntel",
  maxTouchPoints: 0,
  coarsePointer: false,
};

const env = (over: Partial<BrowserEnv> = {}): BrowserEnv => ({ ...baseEnv, ...over });

describe("isMobileBrowser", () => {
  it.each([
    ["desktop Mac Chrome", {}, false],
    ["Windows desktop Chrome", { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36", platform: "Win32" }, false],
    ["Linux desktop Firefox", { userAgent: "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0", platform: "Linux x86_64" }, false],
    ["iPhone Safari UA", { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1", platform: "iPhone", maxTouchPoints: 5, coarsePointer: true }, true],
    ["iPhone Chrome (CriOS) UA", { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/115.0.5804.89 Mobile/15E148 Safari/604.1", platform: "iPhone", maxTouchPoints: 5, coarsePointer: true }, true],
    ["Android Chrome UA", { userAgent: "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36", platform: "Linux armv8l", maxTouchPoints: 5, coarsePointer: true }, true],
    ["iPod touch UA", { userAgent: "Mozilla/5.0 (iPod touch; CPU iPhone OS 15_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6 Mobile/15E148 Safari/604.1", platform: "iPod" }, true],
    ["Android UA alone (no touch signals)", { userAgent: "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/115.0.0.0 Mobile Safari/537.36", platform: "Linux armv8l" }, true],
    ["Mac with maxTouchPoints 2 is iPadOS", { maxTouchPoints: 2 }, true],
    ["iPad Safari UA", { userAgent: "Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1", platform: "iPad", maxTouchPoints: 5, coarsePointer: true }, true],
    ["iPadOS desktop mode", { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15", platform: "MacIntel", maxTouchPoints: 5, coarsePointer: true }, true],
    ["coarse pointer alone", { coarsePointer: true }, true],
    ["Mac maxTouchPoints 0, mouse", { maxTouchPoints: 0, coarsePointer: false }, false],
    ["Mac maxTouchPoints 1, coarsePointer false", { maxTouchPoints: 1, coarsePointer: false }, false],
  ])("%s -> %s", (_name, over, expected) => {
    expect(isMobileBrowser(env(over))).toBe(expected);
  });
});

describe("browserEnv", () => {
  it("returns correct shape", () => {
    const real = browserEnv();
    expect(real).toMatchObject({
      userAgent: expect.any(String),
      platform: expect.any(String),
      maxTouchPoints: expect.any(Number),
      coarsePointer: expect.any(Boolean),
    });
  });
});
