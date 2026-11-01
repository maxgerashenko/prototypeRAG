// Group sessions on the Bookings screen: create (per-hour + button), edit, remove
// attendees, delete. Backend mocked as in mocks.ts. The clock is pinned to 2026-11-05 10:30
// (page time zone is UTC) and frozen, so waits are real, not fake timers.
// Hours 9-17 open -> the column starts at 8:00, 60 px per hour; free future hours today: 11, 12, 15, 16, 17
// (10:00 is past, 13:00-14:30 booked).

import { expect, test, type Page } from "@playwright/test";
import { biz, booking, convo, mockApi, mockVoice, session, sessionWith, type ApiState } from "./mocks";

test.use({ hasTouch: true });

const NOW = new Date("2026-11-05T10:30:00Z");
const BAKERY = biz("b1", "Corner Bakery", "https://www.cornerbakery.example/", 0, "cornerbakery.example");
const AI = booking({ id: "a1", start_min: 600, end_min: 660, name: "Ann Lee", service: "Haircut", by_assistant: true });
const MANUAL = booking({ id: "m1", start_min: 780, end_min: 870, name: "Bo Chen", service: "Color" });
const S15 = { start_min: 900, end_min: 960 }; // 3 PM
const ANN = { id: "k1", name: "Ann Park", phone: "+15550001111", by_assistant: true, conversation_id: "c-ann" };
const BEN = { id: "k2", name: "Ben Ortiz", phone: null, by_assistant: false, conversation_id: null };

function init(over: Partial<ApiState> = {}): Partial<ApiState> {
  return { businesses: [BAKERY], conversations: { b1: [convo("c-ann", "Booked the 3 PM class", ["Hello, how can I help?", "Book me into yoga"])] }, bookings: [AI, MANUAL], bookingHours: { open: 9, close: 17 }, ...over };
}

test.beforeEach(async ({ page }) => {
  await mockVoice(page);
  await page.clock.setFixedTime(NOW);
});

const sheet = (page: Page) => page.getByTestId("session-sheet");
const blocks = (page: Page) => page.getByTestId("session-block");
const cal = (page: Page) => page.getByTestId("bk-cal");
const title = (page: Page) => page.getByTestId("bk-day-title");
const notice = (page: Page) => page.locator(".bk-notice");

async function openBookings(page: Page, over: Partial<ApiState> = {}) {
  const api = await mockApi(page, init(over));
  await page.goto("/web/");
  await page.locator(".biz-pick").click();
  await page.getByTestId("bookings-row").click();
  await expect(page.locator("[data-screen=bookings]")).toBeVisible();
  return api;
}

const hourAdd = (page: Page, h: number) => page.getByTestId(`hour-add-${h}`);

test("the + on an empty hour opens the create sheet with that hour preselected", async ({ page }) => {
  await openBookings(page);
  await hourAdd(page, 12).click();
  await expect(sheet(page)).toBeVisible();
  await expect(sheet(page).getByRole("heading", { name: "New session" })).toBeVisible();
  await expect(sheet(page).locator("select")).toHaveValue("12");
});

test("a + sits on free future hours only: not on booked, session or past hours, none on a past day", async ({ page }) => {
  await openBookings(page, { sessions: [session({ id: "s15", ...S15 })] });
  await expect(page.locator(".bk-hour-add")).toHaveCount(4);
  for (const h of [11, 12, 16, 17]) await expect(hourAdd(page, h)).toHaveCount(1);
  for (const h of [9, 10, 13, 14, 15]) await expect(hourAdd(page, h)).toHaveCount(0);
  await expect(hourAdd(page, 12)).toHaveAttribute("aria-label", "Add session at 12 PM");
  await page.getByRole("button", { name: "Previous day" }).click();
  await expect(title(page)).not.toHaveText(/Today/);
  await expect(page.locator(".bk-hour-add")).toHaveCount(0);
});

test("a + is a transparent hour-sized hit area with a faded glyph centred in the event area", async ({ page }) => {
  await openBookings(page);
  const btn = hourAdd(page, 12);
  await expect(btn).toBeVisible();
  await page.waitForTimeout(450); // let the day column's slide-in finish before measuring
  const css = await btn.evaluate((el) => {
    const c = getComputedStyle(el);
    return { bg: c.backgroundColor, border: c.borderTopWidth, glyph: Number(getComputedStyle(el.firstElementChild!).opacity) };
  });
  expect(css.bg).toBe("rgba(0, 0, 0, 0)");
  expect(css.border).toBe("0px");
  expect(css.glyph).toBeLessThan(1);
  const box = (await btn.boundingBox())!;
  const col = (await page.locator(".bk-inner").boundingBox())!;
  const row = col.y + (12 - 8) * 60;
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(Math.abs(box.height - 56)).toBeLessThan(1);
  expect(Math.abs(box.x - (col.x + 62))).toBeLessThan(3);
  expect(Math.abs(box.x + box.width - (col.x + col.width - 10))).toBeLessThan(3);
  expect(Math.abs(box.y - (row + 2))).toBeLessThan(2);
  const g = (await btn.locator("span").boundingBox())!; // the glyph is centred in it
  expect(Math.abs(g.x + g.width / 2 - (box.x + box.width / 2))).toBeLessThan(2);
  expect(Math.abs(g.y + g.height / 2 - (row + 30))).toBeLessThan(2);
});

test("clicking near the left edge of an empty hour (not on the glyph) opens the sheet at that hour", async ({ page }) => {
  await openBookings(page);
  const box = (await hourAdd(page, 12).boundingBox())!;
  await page.mouse.click(box.x + 6, box.y + box.height / 2);
  await expect(sheet(page).getByRole("heading", { name: "New session" })).toBeVisible();
  await expect(sheet(page).locator("select")).toHaveValue("12");
});

test("a swipe that starts on a + changes the day and opens no sheet", async ({ page }) => {
  await openBookings(page);
  const box = (await hourAdd(page, 12).boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 120, y + 4, { steps: 8 });
  await page.mouse.up();
  await expect(title(page)).not.toHaveText("Today, Nov 5");
  await expect(sheet(page)).toHaveCount(0);
});

test("the per-hour + opens the sheet at that hour and past days have none", async ({ page }) => {
  await openBookings(page);
  await hourAdd(page, 11).click();
  await expect(sheet(page).getByRole("heading", { name: "New session" })).toBeVisible();
  await expect(sheet(page).locator("select")).toHaveValue("11");
  await page.getByRole("button", { name: "Close session" }).click({ position: { x: 20, y: 20 } });
  await expect(sheet(page)).toHaveCount(0);
  await page.getByRole("button", { name: "Previous day" }).click();
  await expect(title(page)).not.toHaveText(/Today/);
  await expect(page.locator(".bk-hour-add")).toHaveCount(0);
  await page.getByRole("button", { name: "Next day" }).click();
  await expect(hourAdd(page, 11)).toBeAttached();
});

test("create: name and max attendees, the block shows name and 0/8, the sheet closes", async ({ page }) => {
  const api = await openBookings(page);
  await hourAdd(page, 11).click();
  const add = sheet(page).getByRole("button", { name: "Add session" });
  await expect(add).toBeDisabled(); // no name yet
  await sheet(page).getByPlaceholder("e.g. Sauna social").fill("Zumba");
  await expect(add).toBeEnabled();
  await sheet(page).getByLabel("Attendees", { exact: true }).fill("8");
  await add.click();
  await expect(sheet(page)).toHaveCount(0);
  await expect(blocks(page)).toHaveCount(1);
  await expect(blocks(page)).toContainText("Zumba");
  await expect(blocks(page)).toContainText("0/8");
  expect(api.sessionRequests[0]).toMatchObject({
    method: "POST", body: { date: "2026-11-05", hour: 11, name: "Zumba", max_attendees: 8 },
  });
});

test("a server 409 shows the error in the sheet and keeps it open", async ({ page }) => {
  const api = await openBookings(page, { sessionPostFail: { status: 409, detail: "There is already a session at that hour." } });
  await hourAdd(page, 11).click();
  await sheet(page).getByPlaceholder("e.g. Sauna social").fill("Zumba");
  await sheet(page).getByRole("button", { name: "Add session" }).click();
  await expect(sheet(page).getByRole("alert")).toHaveText("There is already a session at that hour.");
  await expect(sheet(page)).toBeVisible();
  await expect(blocks(page)).toHaveCount(0);
  expect(api.sessions).toEqual([]);
  // the next try works (the failure was for one POST only)
  await sheet(page).getByRole("button", { name: "Add session" }).click();
  await expect(sheet(page)).toHaveCount(0);
  await expect(blocks(page)).toHaveCount(1);
});

test("private: a lock beside the slider closes the session to the public; the seat limit stays", async ({ page }) => {
  const api = await openBookings(page);
  await hourAdd(page, 11).click();
  await sheet(page).getByPlaceholder("e.g. Sauna social").fill("Retreat");
  const seats = sheet(page).getByLabel("Attendees", { exact: true });
  await expect(seats).toHaveAttribute("min", "1");
  await expect(seats).toHaveAttribute("max", "30");
  await expect(seats).toHaveAttribute("step", "1");
  await seats.fill("12");
  await expect(sheet(page).locator(".ss-seats-value")).toHaveText("12");
  const sw = sheet(page).getByRole("switch", { name: /Private/ });
  await expect(sw).toHaveAttribute("aria-checked", "false");
  await sw.click();
  await expect(sw).toHaveAttribute("aria-checked", "true");
  await expect(seats).toBeEnabled(); // private still needs a max
  await expect(seats).toHaveValue("12");
  // one row: slider, number and lock share the same line
  const [sy, ly] = [(await seats.boundingBox())!.y, (await sw.boundingBox())!.y];
  expect(Math.abs(sy - ly)).toBeLessThan(20);
  await sheet(page).getByRole("button", { name: "Add session" }).click();
  await expect(blocks(page)).toContainText("Retreat");
  await expect(blocks(page)).toContainText("0/12");
  await expect(blocks(page).locator("svg")).toHaveCount(1); // the lock replaces the people icon
  await expect(blocks(page)).toHaveAttribute("aria-label", /0 of 12 booked · Private/);
  expect(api.sessionRequests[0]).toMatchObject({ body: { name: "Retreat", max_attendees: 12, private: true } });
  // public again from the edit sheet
  await blocks(page).click();
  const sw2 = sheet(page).getByRole("switch", { name: /Private/ });
  await expect(sw2).toHaveAttribute("aria-checked", "true");
  await sw2.click();
  await expect.poll(() => api.sessionRequests.find((r) => r.method === "PATCH")).toMatchObject({ body: { private: false } }); // autosaved after 2 s
});

test("edit: rename and change max (min = booked), Save updates the block", async ({ page }) => {
  const api = await openBookings(page, { sessions: [sessionWith([ANN, BEN, { ...BEN, id: "k3", name: "Cy Diaz" }], { id: "s15", ...S15 })] });
  await expect(blocks(page)).toContainText("3/8");
  await blocks(page).click();
  await expect(sheet(page).getByRole("dialog", { name: "Session" })).toBeVisible();
  await expect(sheet(page)).toContainText("3 of 8 booked");
  await expect(sheet(page).getByRole("button", { name: "Save" })).toHaveCount(0); // never a Save button
  const seats = sheet(page).getByLabel("Attendees", { exact: true });
  await seats.fill("1"); // the slider cannot go below the number booked
  await expect(seats).toHaveValue("3");
  await expect(sheet(page).locator(".ss-seats-value")).toHaveText("3");
  await sheet(page).locator("label.ss-field input").fill("Pilates");
  await expect(sheet(page)).toContainText("3 of 3 booked", { timeout: 6000 }); // autosaved after 2 s, no Save button
  await expect(sheet(page).locator(".ss-chip.full")).toHaveText("FULL");
  await expect(blocks(page)).toContainText("Pilates");
  await expect(blocks(page)).toContainText("3/3");
  expect(api.sessionRequests.find((r) => r.method === "PATCH")).toMatchObject({ body: { name: "Pilates", max_attendees: 3 } });
});

test("remove an attendee: Keep cancels, Remove removes, the count drops and the caller is texted", async ({ page }) => {
  const api = await openBookings(page, { sessions: [sessionWith([ANN, BEN], { id: "s15", ...S15 })] });
  await blocks(page).click();
  const rows = sheet(page).getByTestId("attendee-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0).getByRole("link", { name: "+1 (555) 000-1111" })).toHaveAttribute("href", "tel:+15550001111");
  const x = rows.nth(0).getByRole("button", { name: "Remove Ann Park" }); // the faded x at the right of the row
  await expect(x).toBeVisible();
  // a caller booked in a conversation: mic + blue number, no "Voice assistant" label
  const num0 = rows.nth(0).locator(".ss-phone");
  await expect(num0).toHaveClass(/conv/);
  expect(await num0.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 145, 157)"); // number grey at rest (--misc)
  expect(await num0.locator("svg").evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 180, 248)"); // mic icon always blue
  await expect(rows.nth(0).getByText("Voice assistant")).toHaveCount(0);
  await expect(rows.nth(0).locator(".ss-hint")).toHaveText("See conversation");
  expect(await rows.nth(0).locator(".ss-hint").evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 145, 157)");
  const xb = (await x.boundingBox())!, rb = (await rows.nth(0).boundingBox())!;
  expect(xb.width).toBeGreaterThanOrEqual(44);
  expect(Math.abs(xb.y + xb.height / 2 - (rb.y + rb.height / 2))).toBeLessThan(2); // vertically centred
  expect(Math.abs(rb.x + rb.width - (xb.x + xb.width / 2) - 31)).toBeLessThan(3); // centre 31px from the right edge = middle of the word on the open 62px block
  expect(await x.locator("span").evaluate((el) => getComputedStyle(el).borderTopWidth)).toBe("1px");
  expect(await x.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe("rgba(0, 0, 0, 0)"); // transparent
  expect(await x.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 145, 157)"); // --faint grey, like the list chevrons
  await x.click();
  await expect(rows.nth(0).getByRole("group", { name: "Remove Ann Park?" })).toBeVisible();
  await expect(rows.nth(0)).not.toContainText("Remove Ann Park?"); // the confirm is just the two buttons, no sentence
  await rows.nth(0).getByRole("button", { name: "Keep" }).click();
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("Ann Park");
  expect(api.sessionRequests).toEqual([]);

  await rows.nth(0).getByRole("button", { name: "Remove Ann Park" }).click();
  await rows.nth(0).getByRole("button", { name: "Remove", exact: true }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.nth(0)).toContainText("Ben Ortiz");
  await expect(sheet(page)).toContainText("1 of 8 booked");
  await expect(blocks(page)).toContainText("1/8");
  await expect(notice(page)).toHaveText("Ann Park texted");
  await expect(notice(page)).toBeVisible();
  expect(api.sessionRequests[0]).toMatchObject({ method: "DELETE", path: "/businesses/b1/sessions/s15/attendees/k1" });
});

test("delete a session with attendees: confirm names the callers, then the block is gone and a notice shows", async ({ page }) => {
  const api = await openBookings(page, { sessions: [sessionWith([ANN, BEN], { id: "s15", ...S15 })] });
  await blocks(page).click();
  await sheet(page).getByRole("button", { name: "Delete session" }).click();
  await expect(sheet(page)).toContainText("Delete this session? 2 callers will be cancelled and texted.");
  await sheet(page).getByRole("button", { name: "Keep" }).click(); // Keep goes back
  await expect(sheet(page).getByRole("button", { name: "Delete session" })).toBeVisible();
  expect(api.sessionRequests).toEqual([]);
  await sheet(page).getByRole("button", { name: "Delete session" }).click();
  await sheet(page).getByRole("button", { name: "Delete", exact: true }).click();
  await expect(sheet(page)).toHaveCount(0);
  await expect(blocks(page)).toHaveCount(0);
  await expect(notice(page)).toHaveText("1 caller texted, 1 without a phone");
  expect(api.sessionRequests[0]).toMatchObject({ method: "DELETE", path: "/businesses/b1/sessions/s15" });
});

test("an ended session opens read-only", async ({ page }) => {
  await openBookings(page, { sessions: [sessionWith([ANN], { id: "s9", start_min: 540, end_min: 600 })] }); // 9 AM, over
  await blocks(page).click();
  await expect(sheet(page).getByText("Ended")).toBeVisible();
  await expect(sheet(page).locator("label.ss-field input")).toHaveJSProperty("readOnly", true);
  await expect(sheet(page).getByLabel("Attendees", { exact: true })).toBeDisabled();
  await expect(sheet(page).getByRole("button", { name: "Delete session" })).toHaveCount(0);
  await expect(sheet(page).getByRole("button", { name: "Remove" })).toHaveCount(0);
  await expect(sheet(page).getByRole("button", { name: "Save" })).toHaveCount(0);
  await expect(sheet(page).getByRole("link", { name: "+1 (555) 000-1111" })).toBeVisible(); // the phone stays
});

test("Esc and Back close the sheet before the calendar; arrows do nothing while a sheet is open", async ({ page }) => {
  await openBookings(page);
  await page.getByRole("button", { name: "Choose date" }).click();
  await expect(cal(page)).toBeVisible();
  await hourAdd(page, 11).click();
  await expect(sheet(page)).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowLeft");
  await expect(title(page)).toHaveText("Today, Nov 5");
  await page.keyboard.press("Escape");
  await expect(sheet(page)).toHaveCount(0);
  await expect(cal(page)).toBeVisible(); // the calendar is still open
  await page.keyboard.press("ArrowRight"); // no sheet: the day changes
  await expect(title(page)).not.toHaveText("Today, Nov 5");
  await page.keyboard.press("ArrowLeft");
  await expect(title(page)).toHaveText("Today, Nov 5");
  await page.getByRole("button", { name: "Choose date" }).click(); // changing the day closed the calendar
  await expect(cal(page)).toBeVisible();

  // Back: sheet first, then the calendar, then leave
  await hourAdd(page, 11).click();
  await expect(sheet(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet(page)).toHaveCount(0);
  await expect(cal(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(cal(page)).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.locator("[data-screen=bookings]")).toHaveCount(0);
});

/** Drag down from the top of the day column in 20 px steps (CDP touch), without lifting. */
async function pullDown(page: Page, dy: number) {
  const box = (await page.getByTestId("bk-day").boundingBox())!;
  const x = box.x + box.width * 0.6;
  let y = box.y + 40;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  for (const end = y + dy; y < end; ) {
    y = Math.min(end, y + 20);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y }] });
  }
  return () => cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}

const monthGets = (api: ApiState) => api.bookingRequests.length;

test("pull down on the day column reloads the month; a short pull does nothing", async ({ page }) => {
  const api = await openBookings(page);
  await expect(page.getByTestId("bk-day")).toBeVisible();
  const before = monthGets(api);
  const shortEnd = await pullDown(page, 60); // damped 26: below the threshold
  await shortEnd();
  await expect(page.getByTestId("ptr")).toHaveCount(0);
  expect(monthGets(api)).toBe(before);

  const end = await pullDown(page, 220);
  await expect(page.getByTestId("ptr")).toBeVisible();
  await end();
  await expect.poll(() => monthGets(api)).toBe(before + 1);
  await expect(page.getByTestId("ptr")).toHaveCount(0);
  await expect(sheet(page)).toHaveCount(0);
});

// ---- swipe the sheet down to close ----
const panel = (page: Page) => sheet(page).locator(".sheet-panel");

async function openCreate(page: Page) {
  await openBookings(page);
  await hourAdd(page, 12).click();
  await expect(panel(page)).toBeVisible();
  await page.waitForTimeout(450); // the slide-in animation
}

/** CDP touch drag from (x, y) down by dy in 20 px steps; returns the lift. */
async function touchDown(page: Page, x: number, y: number, dy: number) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  for (let d = 0; d < dy; ) {
    d = Math.min(dy, d + 20);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: y + d }] });
  }
  return () => cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}

const shift = (page: Page) => panel(page).evaluate((el) => (el as HTMLElement).style.transform);

test("dragging the grip down with the mouse past the threshold closes the sheet", async ({ page }) => {
  await openCreate(page);
  const g = (await panel(page).locator(".sheet-grip").boundingBox())!;
  const x = g.x + g.width / 2;
  const y = g.y + g.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 140, { steps: 10 });
  expect(await shift(page)).toMatch(/translateY\(1[0-9.]+px\)/); // follows the pointer
  await page.mouse.up();
  await expect(sheet(page)).toHaveCount(0);
});

test("a short, slow drag of the grip springs back and the sheet stays", async ({ page }) => {
  await openCreate(page);
  const g = (await panel(page).locator(".sheet-grip").boundingBox())!;
  const x = g.x + g.width / 2;
  const y = g.y + g.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 60, { steps: 6 });
  await page.waitForTimeout(300);
  await page.mouse.up();
  await expect.poll(() => shift(page)).toBe("");
  await expect(sheet(page)).toBeVisible();
});

test("a touch drag down on the panel past the threshold closes the sheet", async ({ page }) => {
  await openCreate(page);
  const h = (await panel(page).getByRole("heading", { name: "New session" }).boundingBox())!;
  const end = await touchDown(page, h.x + 20, h.y + 4, 160);
  expect(await shift(page)).toMatch(/translateY\(/);
  await end();
  await expect(sheet(page)).toHaveCount(0);
});

test("a short touch drag springs back; a drag that starts in the name input does not move the sheet", async ({ page }) => {
  await openCreate(page);
  const h = (await panel(page).getByRole("heading", { name: "New session" }).boundingBox())!;
  const end = await touchDown(page, h.x + 20, h.y + 4, 50);
  await page.waitForTimeout(300);
  await end();
  await expect.poll(() => shift(page)).toBe("");
  await expect(sheet(page)).toBeVisible();

  const inp = (await sheet(page).getByPlaceholder("e.g. Sauna social").boundingBox())!;
  const end2 = await touchDown(page, inp.x + 30, inp.y + inp.height / 2, 160);
  expect(await shift(page)).toBe("");
  await end2();
  await expect(sheet(page)).toBeVisible();
  expect(await shift(page)).toBe("");
});

// ---- swipe an attendee row left to remove ----
async function rowSwipe(page: Page, idx: number, dx: number, hold = true) {
  const b = (await sheet(page).getByTestId("attendee-slide").nth(idx).boundingBox())!;
  const x = b.x + b.width - 40;
  const y = b.y + b.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - dx, y, { steps: 10 });
  if (hold) await page.mouse.up();
}

test("swiping an attendee row part-way springs back and sends nothing; the red Remove shows while dragging", async ({ page }) => {
  const api = await openBookings(page, { sessions: [sessionWith([ANN, BEN], { id: "s15", ...S15 })] });
  await blocks(page).click();
  await page.waitForTimeout(450);
  const slide = sheet(page).getByTestId("attendee-slide").first();
  const textX = () => slide.locator(".ss-who").evaluate((el) => el.getBoundingClientRect().x);
  const textW = () => slide.locator(".ss-who").evaluate((el) => el.getBoundingClientRect().width);
  const x0 = await textX();
  const w0 = await textW();
  await rowSwipe(page, 0, 66, false); // offset 58: past the x (17-45px from the edge), short of firing (70)
  const rv = sheet(page).locator(".ss-reveal").first();
  const sb = (await slide.boundingBox())!;
  const rvBox = (await rv.boundingBox())!;
  expect(rvBox.width).toBeGreaterThan(40); // grows in from the right edge
  const xBox = (await slide.getByRole("button", { name: /^Remove / }).boundingBox())!; // the faded x sits under the growing block
  expect(xBox.x).toBeGreaterThanOrEqual(rvBox.x - 1);
  expect(Math.abs(rvBox.x + rvBox.width - (sb.x + sb.width))).toBeLessThan(2);
  expect(await textX()).toBeCloseTo(x0, 0); // the row text does not move at all
  expect(await textW()).toBeCloseTo(w0, 0);
  const style = (el: Element) => { const c = getComputedStyle(el); return [c.color, c.fontSize, c.fontWeight].join("|"); };
  const delStyle = await page.evaluate(() => {
    const d = document.createElement("button");
    d.className = "ss-delete";
    document.body.append(d);
    const c = getComputedStyle(d);
    const r = [c.color, c.fontSize, c.fontWeight].join("|");
    d.remove();
    return r;
  });
  expect(await rv.evaluate(style)).toBe(delStyle); // same text colour as the Delete session button
  expect(await rv.evaluate((el) => { const c = getComputedStyle(el); return [c.borderTopWidth, c.borderTopLeftRadius].join("|"); })).toBe("0px|0px"); // flat, no round border
  await page.mouse.up();
  await expect.poll(async () => (await sheet(page).getByTestId("attendee-reveal").first().boundingBox())!.width).toBe(0);
  await expect(sheet(page).getByTestId("attendee-row")).toHaveCount(2);
  expect(api.sessionRequests).toEqual([]);
});

test("swiping an attendee row fully left removes the caller", async ({ page }) => {
  const api = await openBookings(page, { sessions: [sessionWith([ANN, BEN], { id: "s15", ...S15 })] });
  await blocks(page).click();
  await page.waitForTimeout(450);
  await rowSwipe(page, 0, 200);
  expect(api.sessionRequests).toEqual([]); // a full swipe only asks...
  await expect(sheet(page).getByRole("group", { name: "Remove Ann Park?" })).toBeVisible();
  await sheet(page).getByRole("group", { name: "Remove Ann Park?" }).getByRole("button", { name: "Remove", exact: true }).click(); // ...Keep / Remove
  await expect(sheet(page).getByTestId("attendee-row")).toHaveCount(1);
  await expect(sheet(page)).toBeVisible(); // the sheet did not close
  expect(api.sessionRequests[0]).toMatchObject({ method: "DELETE", path: "/businesses/b1/sessions/s15/attendees/k1" });
});

test("rows of an ended session do not swipe", async ({ page }) => {
  const api = await openBookings(page, { sessions: [sessionWith([{ ...ANN, conversation_id: null }], { id: "s9", start_min: 540, end_min: 600 })] });
  await blocks(page).click();
  await page.waitForTimeout(450);
  await rowSwipe(page, 0, 200);
  await expect(sheet(page).getByTestId("attendee-row")).toHaveCount(1);
  expect(api.sessionRequests).toEqual([]);
});

// ---- a full session: 10 callers ----
const TEN = Array.from({ length: 10 }, (_, i) => ({
  id: `c${i + 1}`, name: `Caller ${i + 1}`, phone: i % 3 === 2 ? null : `+1555000${1000 + i}`, by_assistant: i % 2 === 0, conversation_id: null,
}));

test("a session with 10 callers lists them all, scrolls, and swipe / x removal work on any row", async ({ page }) => {
  const api = await openBookings(page, { sessions: [sessionWith(TEN, { id: "s15", max_attendees: 10, ...S15 })] });
  await blocks(page).click();
  await page.waitForTimeout(450);
  const rows = sheet(page).getByTestId("attendee-row");
  await expect(rows).toHaveCount(10);
  await expect(sheet(page)).toContainText("10 of 10 booked");
  await expect(blocks(page)).toContainText("10/10");
  await expect(blocks(page)).toContainText("FULL");
  for (let i = 0; i < 10; i++) await expect(rows.nth(i).getByRole("button", { name: `Remove Caller ${i + 1}` })).toHaveCount(1); // each row has its x

  // the panel scrolls (rows do not squeeze) and the sheet stays inside the screen
  const box = (await panel(page).boundingBox())!;
  const vh = page.viewportSize()!.height;
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(vh + 1);
  expect(await rows.nth(0).evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(56);

  // swipe the LAST row (scrolled into view): only that caller goes
  await rows.nth(9).scrollIntoViewIfNeeded();
  const idx = 9;
  const b = (await sheet(page).getByTestId("attendee-slide").nth(idx).boundingBox())!;
  await page.mouse.move(b.x + b.width - 40, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width - 240, b.y + b.height / 2, { steps: 10 });
  await page.mouse.up();
  await expect(rows).toHaveCount(10); // a full swipe only asks...
  expect(api.sessionRequests).toEqual([]);
  await rows.nth(9).getByRole("button", { name: "Remove", exact: true }).click(); // ...Keep / Remove
  await expect(rows).toHaveCount(9);
  expect(api.sessionRequests[0]).toMatchObject({ method: "DELETE", path: "/businesses/b1/sessions/s15/attendees/c10" });
  await expect(sheet(page)).toContainText("9 of 10 booked");
  await expect(blocks(page)).not.toContainText("FULL");

  // tap the x on a middle row, confirm: only that caller goes
  await rows.nth(4).getByRole("button", { name: "Remove Caller 5" }).click();
  await expect(rows.nth(4).getByRole("group", { name: "Remove Caller 5?" })).toBeVisible();
  await rows.nth(4).getByRole("button", { name: "Remove", exact: true }).click();
  await expect(rows).toHaveCount(8);
  await expect(sheet(page)).not.toContainText("Caller 5");
  await expect(sheet(page)).toContainText("Caller 4");
  await expect(sheet(page)).toContainText("Caller 6");
  expect(api.sessionRequests.map((r) => r.path)).toEqual([
    "/businesses/b1/sessions/s15/attendees/c10",
    "/businesses/b1/sessions/s15/attendees/c5",
  ]);
});

// ---- tap an attendee row to open the conversation that booked them ----
async function openEdit(page: Page, over: Partial<ApiState> = {}) {
  const api = await openBookings(page, { sessions: [sessionWith([ANN, BEN], { id: "s15", ...S15 })], ...over });
  await blocks(page).click();
  await page.waitForTimeout(450);
  return api;
}
const slideOf = (page: Page, i: number) => sheet(page).getByTestId("attendee-slide").nth(i);

test("tapping an attendee row opens its conversation; Back returns to the open session sheet", async ({ page }) => {
  await openEdit(page);
  const row = slideOf(page, 0);
  await expect(row).toHaveAttribute("role", "button");
  await expect(row).toHaveAttribute("aria-label", "Open conversation with Ann Park");
  expect(await row.evaluate((el) => getComputedStyle(el).cursor)).toBe("pointer");
  await row.click({ position: { x: 20, y: 10 } });
  await expect(page.getByRole("heading", { name: "Booked the 3 PM class" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator("[data-screen=bookings]")).toBeVisible();
  await expect(sheet(page).getByRole("dialog", { name: "Session" })).toBeVisible();
  await expect(sheet(page).getByTestId("attendee-row")).toHaveCount(2);
});

test("an attendee row opens with Enter from the keyboard", async ({ page }) => {
  await openEdit(page);
  await slideOf(page, 0).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Booked the 3 PM class" })).toBeVisible();
});

test("tapping the right end of the row (where the Remove block shows), but not the x, opens it", async ({ page }) => {
  await openEdit(page);
  const b = (await slideOf(page, 0).boundingBox())!;
  await page.mouse.click(b.x + b.width - 3, b.y + 3); // top-right corner: inside the 62px block area, outside the x
  await expect(page.getByRole("heading", { name: "Booked the 3 PM class" })).toBeVisible();
});

test("a row without a conversation is inert; the x and the phone link do not open one", async ({ page }) => {
  await openEdit(page);
  const ben = slideOf(page, 1);
  await expect(ben).not.toHaveAttribute("role", "button");
  await ben.click({ position: { x: 20, y: 10 } });
  await expect(sheet(page).getByRole("dialog", { name: "Session" })).toBeVisible();
  await sheet(page).getByTestId("attendee-row").nth(0).getByRole("button", { name: "Remove Ann Park" }).click();
  await expect(sheet(page).getByTestId("attendee-row").nth(0).getByRole("group", { name: "Remove Ann Park?" })).toBeVisible(); // Keep / Remove, no navigation
  await expect(sheet(page).getByRole("dialog", { name: "Session" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Booked the 3 PM class" })).toHaveCount(0);
});

test("a swipe on a row does not open the conversation", async ({ page }) => {
  await openEdit(page);
  await rowSwipe(page, 0, 60); // short: springs back, no remove
  await expect(sheet(page).getByRole("dialog", { name: "Session" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Booked the 3 PM class" })).toHaveCount(0);
});

test("an ended session's rows still open the conversation", async ({ page }) => {
  await openBookings(page, { sessions: [sessionWith([ANN], { id: "s9", start_min: 540, end_min: 600 })] });
  await blocks(page).click();
  await page.waitForTimeout(450);
  await slideOf(page, 0).click({ position: { x: 20, y: 10 } });
  await expect(page.getByRole("heading", { name: "Booked the 3 PM class" })).toBeVisible();
});

test("numbers are grey at rest and coloured while touched; the phone icon is always green and the mic always blue", async ({ page }) => {
  const CY = { id: "k3", name: "Cy Lee", phone: "+15550002222", by_assistant: false, conversation_id: null };
  await openBookings(page, { sessions: [sessionWith([ANN, CY], { id: "s16", ...S15 })] });
  await blocks(page).click();
  const rows = sheet(page).getByTestId("attendee-row");
  const cy = rows.nth(1).locator("a.ss-phone");
  expect(await cy.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 145, 157)"); // number grey at rest (--misc)
  expect(await cy.locator("svg").evaluate((el) => getComputedStyle(el).color)).toBe("rgb(127, 214, 168)"); // phone icon always green
  await expect(cy).not.toHaveClass(/conv/);
  await expect(cy).toHaveText("+1 (555) 000-2222"); // formatted like the numbers in Settings
  await expect(rows.nth(1).locator(".ss-hint")).toHaveText("Make a call");
  await expect(cy.locator("svg")).toHaveCount(1); // phone icon
  await expect(cy).toHaveAttribute("href", "tel:+15550002222"); // pressing it calls
  // the whole row calls, not just the number: the row is a "Call" button and presses colour the number green
  const slide = rows.nth(1).getByTestId("attendee-slide");
  await expect(slide).toHaveAttribute("role", "button");
  await expect(slide).toHaveAttribute("aria-label", "Call Cy Lee");
  const sb = (await slide.boundingBox())!;
  await page.mouse.move(sb.x + 20, sb.y + 5); // on the row, away from the number
  await page.mouse.down();
  expect(await cy.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(127, 214, 168)"); // while pressed: Phone green
  expect(await rows.nth(1).locator(".ss-hint").evaluate((el) => getComputedStyle(el).color)).toBe("rgb(127, 214, 168)"); // the hint follows
  await expect(rows.nth(1).locator(".ss-sep")).toHaveText("·");
  await page.mouse.move(sb.x + sb.width / 2, sb.y - 40); // slide off the row, then release
  await page.mouse.up();
  expect(await cy.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 145, 157)"); // released: back to grey
  expect(await rows.nth(1).locator(".ss-hint").evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 145, 157)");
  // a conversation caller: the number turns blue only while touched, the mic stays blue
  const ann = rows.nth(0).locator("a.ss-phone"), ab = (await rows.nth(0).getByTestId("attendee-slide").boundingBox())!;
  await page.mouse.move(ab.x + 20, ab.y + 5);
  await page.mouse.down();
  expect(await ann.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 180, 248)");
  expect(await rows.nth(0).locator(".ss-hint").evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 180, 248)");
  await page.mouse.move(ab.x + ab.width / 2, ab.y - 40);
  await page.mouse.up();
  expect(await ann.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(138, 145, 157)");
  await expect(rows.nth(0).locator("a.ss-phone.conv svg")).toHaveCount(1); // mic icon
});

test("a tall session sheet stops at 75% of the screen; only the attendees list scrolls", async ({ page }) => {
  await openBookings(page, { sessions: [sessionWith(TEN, { id: "s15", max_attendees: 10, ...S15 })] });
  await blocks(page).click();
  await page.waitForTimeout(450); // the slide-up animation
  const panel = sheet(page).locator(".ss-panel"), list = sheet(page).locator(".ss-attendees .ss-list");
  const screen = (await page.locator("[data-screen=bookings]").boundingBox())!;
  expect((await panel.boundingBox())!.height).toBeLessThanOrEqual(screen.height * 0.75 + 1);
  expect(await list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true); // 10 rows do not fit
  expect(await panel.evaluate((el) => el.scrollHeight <= el.clientHeight + 1)).toBe(true); // the sheet itself does not scroll
  expect(await sheet(page).locator(".ss-scroll").evaluate((el) => el.scrollHeight <= el.clientHeight + 1)).toBe(true);
  const head = sheet(page).getByRole("heading", { name: "Session" }), name = sheet(page).locator("label.ss-field input");
  const [h0, n0] = [(await head.boundingBox())!.y, (await name.boundingBox())!.y];
  await list.evaluate((el) => { el.scrollTop = el.scrollHeight; });
  expect([(await head.boundingBox())!.y, (await name.boundingBox())!.y]).toEqual([h0, n0]);
  await expect(sheet(page).getByRole("button", { name: "Delete session" })).toBeInViewport();
  await expect(sheet(page).getByRole("switch", { name: /Private/ })).toBeInViewport();
});

/** Touch drag by CDP from (x, y) by dx (true touch input: pointer events as on a phone). */
async function touchDrag(page: Page, x: number, y: number, dx: number) {
  const cdp = await page.context().newCDPSession(page);
  const send = (type: "touchStart" | "touchMove" | "touchEnd", points: { x: number; y: number }[]) =>
    cdp.send("Input.dispatchTouchEvent", { type, touchPoints: points });
  const step = dx > 0 ? 10 : -10;
  await send("touchStart", [{ x, y }]);
  for (let d = 0; Math.abs(d) <= Math.abs(dx); d += step) await send("touchMove", [{ x: x + d, y }]);
  await send("touchEnd", []);
}

test("swiping a session block left part-way springs back; the day does not change and no sheet opens", async ({ page }) => {
  const api = await openBookings(page, { sessions: [sessionWith([ANN], { id: "s15", ...S15 })] });
  const b = (await blocks(page).boundingBox())!;
  await touchDrag(page, b.x + b.width - 20, b.y + b.height / 2, -40);
  await page.waitForTimeout(400);
  await expect(title(page)).toContainText("Today");
  await expect(sheet(page)).toHaveCount(0);
  await expect(blocks(page).getByTestId("session-reveal")).toHaveCSS("width", "0px");
  expect(api.sessionRequests.length).toBe(0);
});

test("swiping a session block fully left asks Keep / Delete inside the block; Keep cancels, Delete removes it", async ({ page }) => {
  const api = await openBookings(page, { sessions: [sessionWith([ANN], { id: "s15", ...S15 })] });
  const b = (await blocks(page).boundingBox())!;
  await touchDrag(page, b.x + b.width - 20, b.y + b.height / 2, -120);
  const group = page.getByRole("group", { name: /Delete .*\?/ });
  await expect(group).toBeVisible();
  await expect(group).toContainText("1 caller texted");
  await expect(title(page)).toContainText("Today"); // the swipe did not change the day
  expect(api.sessionRequests.length).toBe(0); // a swipe never deletes by itself
  await group.getByRole("button", { name: "Keep" }).click();
  await expect(blocks(page)).toContainText("1/8");
  const b2 = (await blocks(page).boundingBox())!;
  await touchDrag(page, b2.x + b2.width - 20, b2.y + b2.height / 2, -120);
  await page.getByRole("group", { name: /Delete .*\?/ }).getByRole("button", { name: "Delete" }).click();
  await expect(blocks(page)).toHaveCount(0);
  expect(api.sessionRequests.find((r) => r.method === "DELETE")).toBeTruthy();
});

test("a swipe on an empty part of the day still changes the day", async ({ page }) => {
  await openBookings(page, { sessions: [sessionWith([], { id: "s15", ...S15 })] });
  const b = (await blocks(page).boundingBox())!;
  await touchDrag(page, b.x + 20, b.y - 80, -150); // above the block, free area
  await expect(title(page)).toHaveText("Fri, Nov 6");
});
