// Pull-to-refresh (feature-pull-to-refresh.md): touch gestures are driven through the Chrome
// DevTools protocol (Input.dispatchTouchEvent), since Playwright has no touch-drag API.
// The picker reloads the page (and vibrates once at the threshold); the conversations list and
// the transcript refetch in place; nothing happens during a call.

import { expect, test, type CDPSession, type Page } from "@playwright/test";
import type { LiveCallInfo, LiveFeedMessage } from "../src/api";
import { biz, convo, mockApi, mockVoice, type ApiState } from "./mocks";

test.use({ hasTouch: true });

const BAKERY = biz("b1", "Corner Bakery", "https://www.cornerbakery.example/", 4, "cornerbakery.example");
const greet = "Hi, you've reached the bakery.";
const c = (id: string, title: string) => convo(id, title, [greet, "Are you open?", "Yes."]);

function data(): Partial<ApiState> {
  return {
    businesses: [BAKERY],
    conversations: { b1: [c("c1", "Sunday hours"), c("c2", "Gift cards"), c("c3", "Parking"), c("c4", "Catering")] },
  };
}

type Win = { __vib?: number[]; __marker?: number };

async function setup(page: Page, init: Partial<ApiState> = data(), waitCards = true) {
  await page.addInitScript(() => {
    (window as unknown as Win).__vib = [];
    navigator.vibrate = (p: unknown) => {
      (window as unknown as Win).__vib!.push(p as number);
      return true;
    };
  });
  await mockVoice(page);
  const api = await mockApi(page, init);
  await page.goto("/web/");
  if (waitCards) await expect(page.getByTestId("biz-card")).toHaveCount(1);
  return api;
}

const vib = (page: Page) => page.evaluate(() => (window as unknown as Win).__vib!);
const mark = (page: Page) => page.evaluate(() => ((window as unknown as Win).__marker = 42));
const marker = (page: Page) => page.evaluate(() => (window as unknown as Win).__marker);
const count = (api: ApiState, path: string) => api.requests.filter((r) => r === path).length;

/** A finger: down at (x, y), then moves down in 20 px steps. end() lifts it. */
class Finger {
  private y: number;
  constructor(private cdp: CDPSession, private x: number, y: number) {
    this.y = y;
  }
  private send(type: "touchStart" | "touchMove" | "touchEnd", points: { x: number; y: number }[]) {
    return this.cdp.send("Input.dispatchTouchEvent", { type, touchPoints: points });
  }
  down() {
    return this.send("touchStart", [{ x: this.x, y: this.y }]);
  }
  async move(dy: number) {
    const target = this.y + dy;
    const dir = Math.sign(dy);
    while (this.y !== target) {
      this.y = dir > 0 ? Math.min(target, this.y + 20) : Math.max(target, this.y - 20);
      await this.send("touchMove", [{ x: this.x, y: this.y }]);
    }
  }
  end() {
    return this.send("touchEnd", []);
  }
}

async function finger(page: Page, selector: string, dy = 0) {
  const box = (await page.locator(selector).first().boundingBox())!;
  const f = new Finger(await page.context().newCDPSession(page), box.x + box.width / 2, box.y + 40);
  await f.down();
  if (dy) await f.move(dy);
  return f;
}

const layerTransform = (page: Page) => page.locator(".ptr-layer").evaluate((el) => (el as HTMLElement).style.transform);

test("picker: slides down over the icon, then a release reloads the page", async ({ page }) => {
  await setup(page);
  await page.locator(".biz-pick").click();
  await mark(page);
  const f = await finger(page, ".biz-list", 200);
  await expect(page.getByTestId("ptr")).toBeVisible(); // the picker shows the spinner too
  expect(await layerTransform(page)).toMatch(/^translateY\(/);
  const reloaded = page.waitForEvent("load");
  await f.end();
  await reloaded;
  expect(await marker(page)).toBeUndefined(); // a fresh document
  await expect(page.getByTestId("biz-card")).toHaveCount(1);
  await expect(page.locator(".call-btn")).toBeDisabled(); // the selection is gone
});

test("picker: vibrates exactly once on crossing the threshold, before the release", async ({ page }) => {
  await setup(page);
  const f = await finger(page, ".biz-list");
  await f.move(100); // raw dy 100 -> damped 46: below the 64 threshold
  expect(await vib(page)).toEqual([]);
  await f.move(120); // raw 220 -> past the threshold
  await f.move(40);
  expect(await vib(page)).toEqual([15]);
  await f.end();
});

test("picker: a short pull springs back, with no reload and no vibration", async ({ page }) => {
  await setup(page);
  await mark(page);
  const f = await finger(page, ".biz-list", 60);
  expect(await layerTransform(page)).toMatch(/^translateY\(/);
  await f.end();
  await expect.poll(() => layerTransform(page)).toBe("none");
  await page.waitForTimeout(300);
  expect(await marker(page)).toBe(42);
  expect(await vib(page)).toEqual([]);
});

test("conversations: a release refetches the list in place", async ({ page }) => {
  const api = await setup(page);
  await page.locator(".biz-pick").click();
  await page.getByTestId("conversations-row").click();
  const screen = page.locator("[data-screen=convos]");
  await expect(screen.locator(".convo-card")).toHaveCount(4);
  api.conversations.b1.unshift(c("c5", "Brand new question"));
  const bizBefore = count(api, "/businesses");
  const listBefore = count(api, "/businesses/b1/conversations");
  await mark(page);
  const headTop = async () => (await screen.locator(".biz-head").boundingBox())!.y;
  const before = await headTop();
  const f = await finger(page, "[data-screen=convos] .convo-list", 200);
  await expect(page.getByTestId("ptr")).toBeVisible();
  expect(await headTop()).toBe(before); // only the list slides, not the screen
  expect(await screen.locator(".convo-list").evaluate((el) => el.getBoundingClientRect().top)).toBeGreaterThan(
    await screen.locator(".section-head").evaluate((el) => el.getBoundingClientRect().bottom),
  );
  await f.end();
  await expect(page.getByTestId("ptr")).toBeVisible(); // spinner stays at least 0.5 s
  await expect(screen.locator(".convo-card")).toHaveCount(5);
  expect(count(api, "/businesses")).toBe(bizBefore + 1);
  expect(count(api, "/businesses/b1/conversations")).toBe(listBefore + 1);
  expect(await marker(page)).toBe(42); // no navigation
  await expect(page.getByTestId("ptr")).toHaveCount(0);
  await expect(screen).toBeVisible();
});

test("transcript: a release refetches the conversation, the title never flashes Loading", async ({ page }) => {
  const api = await setup(page);
  await page.locator(".biz-pick").click();
  await page.getByTestId("conversations-row").click();
  await page.locator(".convo-card").first().click();
  const screen = page.locator("[data-screen=transcript]");
  await expect(screen.locator("h1")).toHaveText("Sunday hours");
  await page.evaluate(() => {
    (window as unknown as { __titles: string[] }).__titles = [];
    new MutationObserver(() => {
      (window as unknown as { __titles: string[] }).__titles.push(document.querySelector("[data-screen=transcript] h1")?.textContent ?? "");
    }).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  const before = count(api, "/businesses/b1/conversations/c1");
  await mark(page);
  const f = await finger(page, "[data-screen=transcript]", 200);
  await expect(page.getByTestId("ptr")).toBeVisible();
  await f.end();
  await expect.poll(() => count(api, "/businesses/b1/conversations/c1")).toBe(before + 1);
  await expect(page.getByTestId("ptr")).toHaveCount(0);
  expect(await marker(page)).toBe(42);
  await expect(screen.locator("h1")).toHaveText("Sunday hours");
  const titles = await page.evaluate(() => (window as unknown as { __titles: string[] }).__titles);
  expect(titles.filter((t) => t.includes("Loading"))).toEqual([]);
});

test("a pull that starts while the list is scrolled down does nothing", async ({ page }) => {
  const many = Array.from({ length: 30 }, (_, i) => c(`x${i}`, `Question ${i}`));
  const api = await setup(page, { businesses: [BAKERY], conversations: { b1: many } });
  await page.locator(".biz-pick").click();
  await page.getByTestId("conversations-row").click();
  const list = page.locator("[data-screen=convos] .convo-list");
  await expect(list.locator(".convo-card").first()).toBeVisible();
  await list.evaluate((el) => (el.scrollTop = 300));
  expect(await list.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
  const before = api.requests.length;
  await mark(page);
  const f = await finger(page, "[data-screen=convos] .convo-list", 200);
  await expect(page.getByTestId("ptr")).toHaveCount(0);
  await f.end();
  await page.waitForTimeout(700);
  expect(api.requests.length).toBe(before);
  expect(await marker(page)).toBe(42);
});

test("a pull that starts on the header while the list is scrolled down does nothing", async ({ page }) => {
  const many = Array.from({ length: 30 }, (_, i) => c(`x${i}`, `Question ${i}`));
  const api = await setup(page, { businesses: [BAKERY], conversations: { b1: many } });
  await page.locator(".biz-pick").click();
  await page.getByTestId("conversations-row").click();
  const list = page.locator("[data-screen=convos] .convo-list");
  await expect(list.locator(".convo-card").first()).toBeVisible();
  await list.evaluate((el) => (el.scrollTop = 300));
  const before = api.requests.length;
  const f = await finger(page, "[data-screen=convos] .biz-head", 200);
  await expect(page.getByTestId("ptr")).toHaveCount(0);
  await f.end();
  await page.waitForTimeout(700);
  expect(api.requests.length).toBe(before);
});

test("Back from a transcript restores the list scroll position", async ({ page }) => {
  const many = Array.from({ length: 30 }, (_, i) => c(`x${i}`, `Question ${i}`));
  await setup(page, { businesses: [BAKERY], conversations: { b1: many } });
  await page.locator(".biz-pick").click();
  await page.getByTestId("conversations-row").click();
  const list = page.locator("[data-screen=convos] .convo-list");
  await expect(list.locator(".convo-card").first()).toBeVisible();
  await list.evaluate((el) => (el.scrollTop = 600));
  await expect.poll(() => list.evaluate((el) => el.scrollTop)).toBe(600);
  await list.locator(".convo-card").nth(12).click();
  await expect(page.locator("[data-screen=transcript]")).toBeVisible();
  await page.getByRole("button", { name: "Back" }).click();
  await expect(page.locator("[data-screen=convos] .convo-list")).toBeVisible();
  await expect.poll(() => page.locator("[data-screen=convos] .convo-list").evaluate((el) => el.scrollTop)).toBe(600);
});

test("a tap on a card still selects it", async ({ page }) => {
  await setup(page);
  await expect(page.locator(".call-btn")).toBeDisabled();
  const f = await finger(page, ".biz-pick"); // one touchStart + touchEnd, no move
  await f.end();
  await expect(page.locator(".call-btn")).toBeEnabled();
});

test("a pull that starts on a business card does not pick or open it", async ({ page }) => {
  await setup(page);
  const card = page.locator(".biz-pick");
  await expect(page.locator(".call-btn")).toBeDisabled();
  const f = await finger(page, ".biz-pick", 200);
  const reloaded = page.waitForEvent("load");
  await f.end();
  await reloaded; // the pull still reloads the picker
  await expect(card).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".call-btn")).toBeDisabled();
});

test("during a call: a pull does nothing", async ({ page }) => {
  const api = await setup(page);
  await page.locator(".biz-pick").click();
  await page.locator(".call-btn").click();
  await expect(page.locator("[data-screen=call]")).toBeVisible();
  const before = api.requests.length;
  await mark(page);
  const f = await finger(page, "[data-screen=call]", 250);
  await expect(page.getByTestId("ptr")).toHaveCount(0);
  await expect(page.getByTestId("ptr-behind")).toHaveCount(0);
  await f.end();
  await page.waitForTimeout(700);
  expect(await marker(page)).toBe(42);
  expect(api.requests.length).toBe(before);
  await expect(page.locator("[data-screen=call]")).toBeVisible();
});

test("while a phone call is in progress: a pull on the picker does nothing", async ({ page }) => {
  const info: LiveCallInfo = { id: "MZ1", business_id: "b1", conversation_id: "phone-1", caller: "+15550001111", started_at: Date.now() };
  await page.route("**/voice/feed", async (route) => {
    const msgs: LiveFeedMessage[] = [{ type: "snapshot", calls: [{ ...info, events: [] }] }];
    await route.fulfill({
      status: 200, contentType: "text/event-stream",
      body: "retry: 300\n\n" + msgs.map((m) => `data: ${JSON.stringify(m)}\n\n`).join(""),
    });
  });
  await setup(page, data(), false);
  await expect(page.locator("[data-screen=phone-call]")).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();
  await expect(page.getByTestId("phone-live")).toBeVisible();
  await mark(page);
  const f = await finger(page, ".biz-list", 250);
  await expect(page.getByTestId("ptr-behind")).toHaveCount(0);
  expect(await layerTransform(page)).toBe("none");
  await f.end();
  await page.waitForTimeout(700);
  expect(await marker(page)).toBe(42);
  expect(await vib(page)).toEqual([]);
});
