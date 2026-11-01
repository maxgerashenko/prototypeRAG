// Bookings screen (day view, month calendar, booking sheet). Backend mocked as in mocks.ts
// (GET /businesses/{id}/bookings). The clock is pinned to 2026-11-05 10:30 (page time zone is UTC).

import { expect, test, type Page } from "@playwright/test";
import { biz, booking, mockApi, mockVoice, type ApiState } from "./mocks";

test.use({ hasTouch: true });

const NOW = new Date("2026-11-05T10:30:00Z");
const BAKERY = biz("b1", "Corner Bakery", "https://www.cornerbakery.example/", 0, "cornerbakery.example");

const AI = booking({ id: "a1", start_min: 600, end_min: 660, name: "Ann Lee", service: "Haircut", by_assistant: true });
const MANUAL = booking({
  id: "m1", start_min: 780, end_min: 870, name: "Bo Chen", service: "Color", party_size: 2, phone: "+15550001111", notes: "Allergic to ammonia",
});
const LATER = booking({ id: "l1", date: "2026-11-12", start_min: 540, end_min: 600, name: "Cy Diaz" });

function init(over: Partial<ApiState> = {}): Partial<ApiState> {
  return {
    businesses: [BAKERY], conversations: { b1: [] },
    bookings: [AI, MANUAL, LATER], bookingHours: { open: 9, close: 17 },
    ...over,
  };
}

test.beforeEach(async ({ page }) => {
  await mockVoice(page);
  await page.clock.setFixedTime(NOW);
});

const row = (page: Page) => page.getByTestId("bookings-row");
const title = (page: Page) => page.getByTestId("bk-day-title");
const blocks = (page: Page) => page.getByTestId("booking-block");
const sheet = (page: Page) => page.getByTestId("booking-sheet");
const cal = (page: Page) => page.getByTestId("bk-cal");
const screen = (page: Page) => page.locator("[data-screen=bookings]");

async function openBookings(page: Page, over: Partial<ApiState> = {}) {
  const api = await mockApi(page, init(over));
  await page.goto("/web/");
  await page.locator(".biz-pick").click();
  await row(page).click();
  await expect(screen(page)).toBeVisible();
  return api;
}

/** Touch drag by CDP from (x, y) by dx; true touch input, so pointer events fire as on a phone. */
async function drag(page: Page, x: number, y: number, dx: number) {
  const cdp = await page.context().newCDPSession(page);
  const send = (type: "touchStart" | "touchMove" | "touchEnd", points: { x: number; y: number }[]) =>
    cdp.send("Input.dispatchTouchEvent", { type, touchPoints: points });
  const step = dx > 0 ? 10 : -10;
  await send("touchStart", [{ x, y }]);
  for (let d = 0; Math.abs(d) <= Math.abs(dx); d += step) await send("touchMove", [{ x: x + d, y }]);
  await send("touchEnd", []);
}

/** Top and height in px of an absolutely placed element in the 60 px per hour grid (its inline
 * style: a bounding box would catch the block's rise-in animation). */
const offsetInDay = (el: ReturnType<Page["locator"]>) =>
  el.evaluate((e) => ({ top: parseFloat((e as HTMLElement).style.top), height: parseFloat((e as HTMLElement).style.height || "0") }));

test("the open card's Bookings row shows today's count", async ({ page }) => {
  await mockApi(page, init());
  await page.goto("/web/");
  await expect(row(page)).toHaveCount(0);
  await page.locator(".biz-pick").click();
  await expect(row(page)).toContainText("Today · 2 bookings");
});

test("header: Bookings title, business and host subtitle, no Back button; Esc returns to the list", async ({ page }) => {
  await openBookings(page);
  await expect(page.getByRole("heading", { name: "Bookings" })).toBeVisible();
  await expect(page.locator(".biz-head .ellipsis")).toHaveText("Corner Bakery · cornerbakery.example");
  await expect(title(page)).toHaveText("Today, Nov 5");
  await page.keyboard.press("Escape");
  await expect(screen(page)).toHaveCount(0);
  await expect(row(page)).toBeVisible();
});

test("blocks sit at the right top and height; assistant and manual look different", async ({ page }) => {
  await openBookings(page);
  await expect(blocks(page)).toHaveCount(2);
  await expect(page.getByTestId("bk-summary")).toHaveText("2 bookings · 1 by assistant");
  // hours 9-17 -> the column starts at 8:00; top = (start - 480) + 2, height = duration - 4
  const ai = blocks(page).nth(0), manual = blocks(page).nth(1);
  await expect(ai).toHaveClass(/\bai\b/);
  await expect(manual).toHaveClass(/\bmanual\b/);
  expect(await offsetInDay(ai)).toEqual({ top: 122, height: 56 });
  expect(await offsetInDay(manual)).toEqual({ top: 302, height: 86 });
});

test("opening hours: hours outside them are hatched, none are without hours", async ({ page }) => {
  await openBookings(page);
  // view 8:00-18:00; closed = 8:00 and 17:00
  await expect(page.getByTestId("bk-closed")).toHaveCount(2);
  await page.unrouteAll();
  await mockApi(page, init({ bookingHours: null }));
  await page.reload();
  await page.locator(".biz-pick").click();
  await row(page).click();
  await expect(blocks(page)).toHaveCount(2);
  await expect(page.getByTestId("bk-closed")).toHaveCount(0);
});

test("now line: at 10:30 today, gone on another day", async ({ page }) => {
  await openBookings(page);
  const line = page.getByTestId("now-line");
  await expect(line).toBeVisible();
  expect((await offsetInDay(line)).top).toBe(150); // (630 - 480)
  await page.getByRole("button", { name: "Next day" }).click();
  await expect(title(page)).toHaveText("Fri, Nov 6");
  await expect(line).toHaveCount(0);
});

test("arrows change the day; a month is requested only when crossing into a new one", async ({ page }) => {
  const api = await openBookings(page);
  const nov = () => api.bookingRequests.filter((r) => r.includes("start=2026-11-01&end=2026-11-30")).length;
  const oct = () => api.bookingRequests.filter((r) => r.includes("start=2026-10-01&end=2026-10-31")).length;
  await page.getByRole("button", { name: "Next day" }).click();
  await expect(title(page)).toHaveText("Fri, Nov 6");
  await page.getByRole("button", { name: "Previous day" }).click();
  await page.getByRole("button", { name: "Previous day" }).click();
  await expect(title(page)).toHaveText("Wed, Nov 4");
  expect(oct()).toBe(0);
  const novBefore = nov();
  expect(novBefore).toBeGreaterThan(0);
  for (let i = 0; i < 4; i++) await page.getByRole("button", { name: "Previous day" }).click();
  await expect(title(page)).toHaveText("Sat, Oct 31");
  await expect.poll(oct).toBe(1);
  expect(nov()).toBe(novBefore);
  await page.getByRole("button", { name: "Next day" }).click(); // back into the cached month: no new request
  await expect(title(page)).toHaveText("Sun, Nov 1");
  expect(oct()).toBe(1);
  expect(nov()).toBe(novBefore);
});

test("Today pill shows away from today and jumps back", async ({ page }) => {
  await openBookings(page);
  const pill = page.getByRole("button", { name: "Today", exact: true });
  await expect(pill).toHaveCount(0);
  await page.getByRole("button", { name: "Next day" }).click();
  await expect(pill).toBeVisible();
  await pill.click();
  await expect(title(page)).toHaveText("Today, Nov 5");
  await expect(pill).toHaveCount(0);
});

test("calendar: opens on the month, dots on booking days, picking a day jumps there", async ({ page }) => {
  await openBookings(page);
  await page.getByRole("button", { name: "Choose date" }).click();
  await expect(cal(page)).toBeVisible();
  await expect(cal(page).locator(".bk-cal-title")).toHaveText("November 2026");
  await expect(cal(page).locator(".bk-dot.on")).toHaveCount(2); // Nov 5 and Nov 12
  await cal(page).getByRole("button", { name: "Thursday, November 12, 2026" }).click();
  await expect(cal(page)).toHaveCount(0);
  await expect(title(page)).toHaveText("Thu, Nov 12");
  await expect(blocks(page)).toHaveCount(1);
  await expect(blocks(page)).toContainText("Cy Diaz");
});

test("tapping a block opens the sheet; Close, the scrim and Esc close it", async ({ page }) => {
  await openBookings(page);
  await blocks(page).nth(1).click();
  await expect(sheet(page)).toBeVisible();
  await expect(sheet(page)).toContainText("Booked manually");
  await expect(sheet(page)).toContainText("Bo Chen");
  await expect(sheet(page)).toContainText("Color");
  await expect(sheet(page)).toContainText("1 PM – 2:30 PM");
  await expect(sheet(page)).toContainText("90 min");
  await sheet(page).getByRole("button", { name: "Close", exact: true }).click();
  await expect(sheet(page)).toHaveCount(0);

  await blocks(page).nth(0).click();
  await expect(sheet(page)).toContainText("Booked by assistant on a call");
  await page.getByRole("button", { name: "Close booking" }).click({ position: { x: 5, y: 5 } });
  await expect(sheet(page)).toHaveCount(0);

  await blocks(page).nth(0).click();
  await page.keyboard.press("Escape");
  await expect(sheet(page)).toHaveCount(0);
});

test("Esc closes the sheet first, then the calendar, then leaves the screen", async ({ page }) => {
  await openBookings(page);
  await page.getByRole("button", { name: "Choose date" }).click();
  await blocks(page).nth(0).click({ force: true });
  await expect(sheet(page)).toBeVisible();
  await expect(cal(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet(page)).toHaveCount(0);
  await expect(cal(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(cal(page)).toHaveCount(0);
  await expect(screen(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(screen(page)).toHaveCount(0);
  await expect(row(page)).toBeVisible();
});

test.describe("swipe", () => {
  test("left and right on the day column change the day", async ({ page }) => {
    await openBookings(page);
    const col = (await page.getByTestId("bk-day").boundingBox())!;
    await drag(page, col.x + 300, col.y + 20, -150);
    await expect(title(page)).toHaveText("Fri, Nov 6");
    await drag(page, col.x + 100, col.y + 20, 150);
    await expect(title(page)).toHaveText("Today, Nov 5");
    await drag(page, col.x + 100, col.y + 20, 150);
    await expect(title(page)).toHaveText("Wed, Nov 4");
    await expect(screen(page)).toBeVisible(); // a right swipe on the column is not swipe-back
  });

  test("a swipe that starts on a block changes the day and does not open the sheet", async ({ page }) => {
    await openBookings(page);
    const b = (await blocks(page).nth(0).boundingBox())!;
    await drag(page, b.x + b.width - 40, b.y + b.height / 2, -150);
    await expect(title(page)).toHaveText("Fri, Nov 6");
    await page.waitForTimeout(500);
    await expect(sheet(page)).toHaveCount(0);
  });

  test("a swipe right on the header goes back to the list", async ({ page }) => {
    await openBookings(page);
    const h = (await page.locator(".biz-head").boundingBox())!;
    await drag(page, 60, h.y + h.height / 2, 150);
    await expect(screen(page)).toHaveCount(0);
    await expect(row(page)).toBeVisible();
  });
});

test("loading shows a skeleton, then the day", async ({ page }) => {
  await mockApi(page, init({ delay: { bookings: 1200 } }));
  await page.goto("/web/");
  await page.locator(".biz-pick").click();
  await row(page).click();
  await expect(page.getByRole("status").filter({ hasText: "Loading bookings" })).toBeVisible();
  await expect(blocks(page)).toHaveCount(0);
  await expect(blocks(page)).toHaveCount(2);
  await expect(page.getByRole("status").filter({ hasText: "Loading bookings" })).toHaveCount(0);
});

test("a failed load shows the error; Try again loads the day", async ({ page }) => {
  const api = await openBookings(page, { fail: new Set(["bookings"]) });
  const alert = page.getByRole("alert").filter({ hasText: "Couldn’t load bookings" });
  await expect(alert).toBeVisible();
  api.fail.delete("bookings");
  await alert.getByRole("button", { name: "Try again" }).click();
  await expect(alert).toHaveCount(0);
  await expect(blocks(page)).toHaveCount(2);
});

test("a booking added for a later month shows after reopening the screen", async ({ page }) => {
  const api = await openBookings(page);
  await page.getByRole("button", { name: "Choose date" }).click();
  await cal(page).getByRole("button", { name: "Next month" }).click();
  await expect(cal(page).locator(".bk-cal-title")).toHaveText("December 2026");
  await expect(cal(page).locator(".bk-dot.on")).toHaveCount(0);
  await page.keyboard.press("Escape"); // closes the calendar
  await page.keyboard.press("Escape");
  await expect(row(page)).toBeVisible();
  api.bookings.push(booking({ id: "d1", date: "2026-12-10", start_min: 540, end_min: 600, name: "Di Evans" }));
  await row(page).click();
  await page.getByRole("button", { name: "Choose date" }).click();
  await cal(page).getByRole("button", { name: "Next month" }).click();
  await expect(cal(page).locator(".bk-dot.on")).toHaveCount(1);
  await cal(page).getByRole("button", { name: "Thursday, December 10, 2026" }).click();
  await expect(blocks(page)).toContainText("Di Evans");
});
