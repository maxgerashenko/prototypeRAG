// Mobile browser detection for the voice app (used to enable hands-free mode).

/** What the check looks at, so tests can pass a fake browser. */
export interface BrowserEnv {
  userAgent: string;
  platform: string;
  maxTouchPoints: number;
  coarsePointer: boolean; // matchMedia("(pointer: coarse)").matches
}

/** Reads the real browser: navigator.userAgent / platform / maxTouchPoints and
 * window.matchMedia("(pointer: coarse)").matches. Must not throw if matchMedia is missing
 * (coarsePointer false then). */
export function browserEnv(): BrowserEnv {
  const userAgent = typeof navigator !== "undefined" ? navigator.userAgent ?? "" : "";
  const platform = typeof navigator !== "undefined" ? navigator.platform ?? "" : "";
  const maxTouchPoints = typeof navigator !== "undefined" ? navigator.maxTouchPoints ?? 0 : 0;
  let coarsePointer = false;
  if (typeof window !== "undefined" && window.matchMedia) {
    try {
      coarsePointer = window.matchMedia("(pointer: coarse)").matches;
    } catch {
      coarsePointer = false;
    }
  }
  return { userAgent, platform, maxTouchPoints, coarsePointer };
}

/** True on a phone or tablet browser (Safari, Chrome, ...). True when ANY of:
 *  - the primary pointer is coarse (a finger), or
 *  - the user agent contains iPhone, iPad, iPod or Android (case-insensitive), or
 *  - iPadOS in desktop mode: platform is "MacIntel" and maxTouchPoints > 1.
 * A desktop Mac (MacIntel, maxTouchPoints 0), Windows and Linux desktops with a mouse
 * are false. */
export function isMobileBrowser(env: BrowserEnv = browserEnv()): boolean {
  const ua = env.userAgent.toLowerCase();
  const isTouchDevice =
    env.coarsePointer ||
    ua.includes("iphone") ||
    ua.includes("ipad") ||
    ua.includes("ipod") ||
    ua.includes("android");
  const isIpadDesktop = env.platform === "MacIntel" && env.maxTouchPoints > 1;
  return isTouchDevice || isIpadDesktop;
}
