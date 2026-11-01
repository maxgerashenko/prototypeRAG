// Swipe right = Back on mobile: the screen nudges at most 20 px while the finger moves, and a
// release past the threshold goes back one screen. Touch is driven through CDP like pull.spec.ts.

import { expect, test, type Page } from "@playwright/test";
import { biz, convo, mockApi, mockVoice } from "./mocks";

test.use({ hasTouch: true });

const BAKERY = biz("b1", "Corner Bakery", "https://www.cornerbakery.example/", 2, "cornerbakery.example");

async function openList(page: Page) {
  await mockVoice(page);
  await mockApi(page, {
    businesses: [BAKERY],
    conversations: { b1: [convo("c1", "Sunday hours", ["Hi", "Open?", "Yes."]), convo("c2", "Gift cards", ["Hi", "Cards?", "Yes."])] },
  });
  await page.goto("/web/");
  await page.locator(".biz-pick").click();
  await page.getByTestId("conversations-row").click();
  await expect(page.locator("[data-screen=convos] h1")).toBeVisible();
}

async function swipe(page: Page, dx: number, release = true) {
  const cdp = await page.context().newCDPSession(page);
  const send = (type: "touchStart" | "touchMove" | "touchEnd", points: { x: number; y: number }[]) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: points });
  await send("touchStart", [{ x: 60, y: 400 }]);
  for (let x = 60; x <= 60 + dx; x += 10) await send("touchMove", [{ x, y: 400 }]);
  if (release) await send("touchEnd", []);
  return () => send("touchEnd", []);
}

test("a long swipe right goes back, the screen nudged at most 20 px", async ({ page }) => {
  await openList(page);
  const end = await swipe(page, 150, false);
  const tx = await page.getByTestId("vapp").evaluate((el) => el.style.transform);
  expect(tx).toBe("translateX(20px)");
  await end();
  await expect(page.getByTestId("biz-card")).toHaveCount(1); // back on the picker
});

test("a short swipe springs back without going back", async ({ page }) => {
  await openList(page);
  await swipe(page, 50);
  await expect(page.locator("[data-screen=convos] h1")).toBeVisible();
  expect(await page.getByTestId("vapp").evaluate((el) => el.style.transform)).toBe("");
});

test("the picker ignores the swipe", async ({ page }) => {
  await mockVoice(page);
  await mockApi(page, { businesses: [BAKERY], conversations: { b1: [] } });
  await page.goto("/web/");
  await expect(page.getByTestId("biz-card")).toHaveCount(1);
  await swipe(page, 150);
  await expect(page.getByTestId("biz-card")).toHaveCount(1);
  expect(await page.getByTestId("vapp").evaluate((el) => el.style.transform)).toBe("");
});

test("a tap with a little finger drift still selects the card", async ({ page }) => {
  await mockVoice(page);
  await mockApi(page, { businesses: [BAKERY], conversations: { b1: [] } });
  await page.goto("/web/");
  const box = (await page.getByTestId("biz-card").boundingBox())!;
  const cdp = await page.context().newCDPSession(page);
  const send = (type: "touchStart" | "touchMove" | "touchEnd", pts: { x: number; y: number }[]) =>
    cdp.send("Input.dispatchTouchEvent", { type, touchPoints: pts });
  const x = box.x + 60, y = box.y + 20;
  await send("touchStart", [{ x, y }]);
  await send("touchMove", [{ x: x + 8, y: y + 2 }]);
  await send("touchMove", [{ x: x + 12, y: y + 3 }]);
  await send("touchEnd", []);
  await expect(page.getByTestId("biz-card")).toHaveClass(/\bon\b/);
});
