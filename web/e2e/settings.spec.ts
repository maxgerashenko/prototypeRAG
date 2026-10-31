// Business settings (DEC-52, DEC-53): phone number picker, first greeting, top questions.
// Backend mocked as in mocks.ts (greeting / top-questions / number routes, GET /twilio/numbers).

import { expect, test, type Page } from "@playwright/test";
import type { TwilioNumber } from "../src/api";
import { biz, mockApi, mockVoice, type ApiState } from "./mocks";

const N1 = "+15550001111";
const N2 = "+15550002222";
const N3 = "+15550003333";
const N4 = "+15550004444";

const BAKERY = { ...biz("b1", "Corner Bakery", "https://www.cornerbakery.example/", 0, "cornerbakery.example"), phone_numbers: [N1] };
const AUTO = { ...biz("b2", "Harbor Auto Repair", "https://www.harborauto.example/", 0, "harborauto.example"), phone_numbers: [N3] };
const PETS = biz("b3", "Happy Paws Grooming", null, 0);

const num = (number: string, friendly_name: string, over: Partial<TwilioNumber> = {}): TwilioNumber => ({
  number, friendly_name, voice: true, sms: true, routed: true, business_id: null, business_name: null, ...over,
});

function init(over: Partial<ApiState> = {}): Partial<ApiState> {
  return {
    businesses: [BAKERY, AUTO, PETS],
    conversations: { b1: [], b2: [], b3: [] },
    greeting: { text: "Welcome to the bakery!", is_default: false, cached: true, cached_at: new Date().toISOString(), max_length: 300 },
    questions: {
      total_questions: 12, conversations: 5, days: 30,
      questions: [
        { question: "When are you open on Sunday?", count: 6, percent: 50, answer: "From 8am to 2pm." },
        { question: "Do you take reservations?", count: 4, percent: 33, answer: null },
        { question: "Is there parking?", count: 2, percent: 17, answer: "Yes, behind the shop." },
      ],
    },
    numbers: {
      error: null,
      numbers: [
        num(N1, "(555) 000-1111", { business_id: "b1", business_name: "Corner Bakery" }),
        num(N2, "(555) 000-2222"),
        num(N3, "Support line", { business_id: "b2", business_name: "Harbor Auto Repair", sms: false }),
        num(N4, "(555) 000-4444", { routed: false }),
      ],
    },
    ...over,
  };
}

test.beforeEach(async ({ page }) => {
  await mockVoice(page);
});

const pick = (page: Page, name: string) => page.getByTestId("biz-card").filter({ hasText: name }).locator(".biz-pick").click();
const row = (page: Page) => page.getByTestId("settings-row");
const back = (page: Page) => page.getByRole("button", { name: "Back to businesses" });
const greetingBox = (page: Page) => page.getByRole("textbox", { name: "First greeting message" });
const chip = (page: Page) => page.getByTestId("greeting-chip");
const sheet = (page: Page) => page.getByTestId("number-sheet");

async function openSettings(page: Page, over: Partial<ApiState> = {}, name = "Corner Bakery") {
  const api = await mockApi(page, init(over));
  await page.goto("/web/");
  await pick(page, name);
  await row(page).click();
  await expect(page.locator("[data-screen=settings]")).toBeVisible();
  return api;
}

async function swipeRight(page: Page, dx = 150) {
  const cdp = await page.context().newCDPSession(page);
  const send = (type: "touchStart" | "touchMove" | "touchEnd", points: { x: number; y: number }[]) =>
    cdp.send("Input.dispatchTouchEvent", { type, touchPoints: points });
  await send("touchStart", [{ x: 60, y: 400 }]);
  for (let x = 60; x <= 60 + dx; x += 10) await send("touchMove", [{ x, y: 400 }]);
  await send("touchEnd", []);
}

test.describe("settings row on the open card", () => {
  test("shows the number and the greeting state; only on the open card", async ({ page }) => {
    await mockApi(page, init());
    await page.goto("/web/");
    await expect(row(page)).toHaveCount(0);
    await pick(page, "Corner Bakery");
    await expect(row(page)).toContainText("Business settings");
    await expect(row(page)).toContainText("+1 (555) 000-1111 · Greeting cached");
  });

  test("no number and a greeting that is not cached", async ({ page }) => {
    await mockApi(page, init({ greeting: { text: "", is_default: true, cached: false, cached_at: null, max_length: 300 } }));
    await page.goto("/web/");
    await pick(page, "Happy Paws Grooming");
    await expect(row(page)).toContainText("No number · Greeting not cached");
  });
});

test.describe("navigation", () => {
  test("Back returns to the list with the business still open", async ({ page }) => {
    await openSettings(page);
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await expect(page.locator(".biz-head .ellipsis")).toHaveText("Corner Bakery · cornerbakery.example");
    await back(page).click();
    await expect(page.getByTestId("settings-row")).toBeVisible();
    await expect(page.getByTestId("biz-card").filter({ hasText: "Corner Bakery" }).locator(".biz-pick")).toHaveAttribute("aria-expanded", "true");
  });

  test("Esc: closes the number picker first, then leaves Settings", async ({ page }) => {
    await openSettings(page);
    await page.getByRole("button", { name: "Change" }).click();
    await expect(sheet(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(sheet(page)).toHaveCount(0);
    await expect(page.locator("[data-screen=settings]")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-screen=settings]")).toHaveCount(0);
    await expect(row(page)).toBeVisible();
  });

  test("Esc in the filter field closes the picker only", async ({ page }) => {
    await openSettings(page);
    await page.getByRole("button", { name: "Change" }).click();
    await page.getByRole("textbox", { name: "Filter by area code or name" }).focus();
    await page.keyboard.press("Escape");
    await expect(sheet(page)).toHaveCount(0);
    await expect(page.locator("[data-screen=settings]")).toBeVisible();
  });

  test.describe("swipe", () => {
    test.use({ hasTouch: true });
    test("swipe right: closes the picker first, then goes back", async ({ page }) => {
      await openSettings(page);
      await page.getByRole("button", { name: "Change" }).click();
      await swipeRight(page);
      await expect(sheet(page)).toHaveCount(0);
      await expect(page.locator("[data-screen=settings]")).toBeVisible();
      await swipeRight(page);
      await expect(page.locator("[data-screen=settings]")).toHaveCount(0);
      await expect(row(page)).toBeVisible();
    });
  });
});

const greetToggle = (page: Page) => page.getByRole("button", { name: /^First greeting/ });
async function openGreeting(page: Page, over: Partial<ApiState> = {}) {
  const api = await openSettings(page, over);
  await greetToggle(page).click();
  return api;
}

test.describe("first greeting", () => {
  test("collapsed: chip, the full spoken text read-only and the footer, no Save button", async ({ page }) => {
    await openSettings(page);
    await expect(greetToggle(page)).toHaveAttribute("aria-expanded", "false");
    await expect(chip(page)).toHaveText("Cached");
    await expect(page.getByTestId("greeting-read")).toHaveText("Hi, I am an AI assistant. Welcome to the bakery!");
    await expect(page.locator(".greet-note")).toContainText("22/300 · Cached");
    await expect(greetingBox(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Save/ })).toHaveCount(0);
  });

  test("tapping the read-only text opens the editor", async ({ page }) => {
    await openSettings(page);
    await page.getByTestId("greeting-read").click();
    await expect(greetingBox(page)).toBeVisible();
    await expect(greetToggle(page)).toHaveAttribute("aria-expanded", "true");
  });

  test("expanding shows the fixed legal sentence above the input and the edit controls", async ({ page }) => {
    await openSettings(page);
    await greetToggle(page).click();
    await expect(greetToggle(page)).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("greeting-legal")).toHaveText("Hi, I am an AI assistant.");
    await expect(greetingBox(page)).toHaveValue("Welcome to the bakery!");
    await expect(page.getByRole("button", { name: "Saved" })).toBeVisible();
    await greetToggle(page).click();
    await expect(greetingBox(page)).toHaveCount(0);
  });

  test("shows the saved text, the fixed hint and the Cached chip", async ({ page }) => {
    await openGreeting(page);
    await expect(greetingBox(page)).toHaveValue("Welcome to the bakery!");
    await expect(page.getByTestId("greeting-hint")).toHaveText(
      "The first sentence is added to every call by the app. Your text follows it.");
    await expect(chip(page)).toHaveText("Cached");
    await expect(page.getByRole("button", { name: "Saved" })).toBeDisabled();
    await expect(greetingBox(page)).toHaveAttribute("maxlength", "300");
  });

  test("editing: Unsaved changes, Reset, then Save & cache -> Cached", async ({ page }) => {
    const api = await openGreeting(page);
    await greetingBox(page).fill("Welcome! Ask us anything.");
    await expect(chip(page)).toHaveText("Unsaved changes");
    await page.getByRole("button", { name: "Reset" }).click();
    await expect(greetingBox(page)).toHaveValue("Welcome to the bakery!");
    await expect(chip(page)).toHaveText("Cached");

    api.delay["greeting-save"] = 400;
    await greetingBox(page).fill("Welcome! Ask us anything.");
    await page.getByRole("button", { name: "Save & cache" }).click();
    await expect(chip(page)).toHaveText("Caching…");
    await expect(greetingBox(page)).toBeDisabled();
    await expect(chip(page)).toHaveText("Cached");
    await expect(greetToggle(page)).toHaveAttribute("aria-expanded", "false"); // folds back after a good save
    await expect(page.getByTestId("greeting-read")).toContainText("Welcome! Ask us anything.");
    expect(api.greeting.text).toBe("Welcome! Ask us anything.");
    expect(api.requests).toContain("PUT /businesses/b1/greeting");
  });

  test("empty text is saved (the greeting becomes just the disclosure)", async ({ page }) => {
    const api = await openGreeting(page);
    await greetingBox(page).fill("");
    await page.getByRole("button", { name: "Save & cache" }).click();
    await expect(chip(page)).toHaveText("Cached");
    expect(api.greeting.text).toBe("");
    expect(api.greeting.is_default).toBe(true);
  });

  test("a successful save folds the card back; a failed one stays open", async ({ page }) => {
    const api = await openGreeting(page);
    await greetingBox(page).fill("Welcome! Ask us anything.");
    await page.getByRole("button", { name: "Save & cache" }).click();
    await expect(greetToggle(page)).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("greeting-read")).toHaveText("Hi, I am an AI assistant. Welcome! Ask us anything.");
    expect(api.greeting.text).toBe("Welcome! Ask us anything.");
  });

  test("not cached yet: Not cached chip and a Cache greeting button", async ({ page }) => {
    await openGreeting(page, { greeting: { text: "Hello!", is_default: false, cached: false, cached_at: null, max_length: 300 } });
    await expect(chip(page)).toHaveText("Not cached");
    await page.getByRole("button", { name: "Cache greeting" }).click();
    await expect(chip(page)).toHaveText("Cached");
  });

  test("a 422 for text over the limit shows the server's message and keeps the draft", async ({ page }) => {
    const api = await openGreeting(page);
    api.greeting = { ...api.greeting, max_length: 5 }; // the server now refuses what the field still allows
    await greetingBox(page).fill("Far too long for the server");
    await page.getByRole("button", { name: "Save & cache" }).click();
    await expect(page.getByRole("alert")).toHaveText("Greeting is too long (max 5 characters).");
    await expect(greetingBox(page)).toHaveValue("Far too long for the server");
    await expect(chip(page)).toHaveText("Unsaved changes");
  });

  test("a greeting that fails to load shows the error", async ({ page }) => {
    await openSettings(page, { fail: new Set(["greeting"]) });
    await expect(page.getByRole("alert").filter({ hasText: "Couldn’t load the greeting" })).toBeVisible();
    await expect(greetingBox(page)).toHaveCount(0);
  });
});

test.describe("top questions", () => {
  test("summary, first answer open, one open at a time, missing answer text", async ({ page }) => {
    await openSettings(page);
    await expect(page.getByText("12 questions · 5 calls · 30 days")).toBeVisible();
    const items = page.locator(".q-btn");
    await expect(items).toHaveCount(3);
    await expect(items.nth(0)).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText("From 8am to 2pm.")).toBeVisible();
    await items.nth(1).click();
    await expect(items.nth(0)).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("No answer was given in these calls.")).toBeVisible();
    await expect(page.getByText("From 8am to 2pm.")).toHaveCount(0);
    await items.nth(1).click(); // tap again closes
    await expect(page.locator(".q-answer")).toHaveCount(0);
  });

  test("no questions yet", async ({ page }) => {
    await openSettings(page, { questions: { total_questions: 0, conversations: 0, days: 30, questions: [] } });
    await expect(page.getByText("No questions yet. Callers’ questions will show up here.")).toBeVisible();
  });

  test("a failed load shows the error", async ({ page }) => {
    await openSettings(page, { fail: new Set(["questions"]) });
    await expect(page.getByRole("alert").filter({ hasText: "Couldn’t load the questions" })).toBeVisible();
  });
});

test.describe("number picker", () => {
  test("current number with its capabilities; Change opens the list", async ({ page }) => {
    await openSettings(page);
    await expect(page.getByTestId("settings-number")).toHaveText("+1 (555) 000-1111");
    await expect(page.locator(".num-sub").first()).toHaveText("Twilio"); // capabilities are known once the list has loaded
    await page.getByRole("button", { name: "Change" }).click();
    await expect(page.getByRole("dialog", { name: "Choose a phone number" })).toBeVisible();
    await expect(page.locator(".num-opt")).toHaveCount(4);
    await expect(page.getByRole("button", { name: "Select a number" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(page.locator(".num-sub").first()).toHaveText("Twilio · Voice & SMS");
  });

  test("no number: Choose; the filter matches digits and names", async ({ page }) => {
    await openSettings(page, {}, "Happy Paws Grooming");
    await expect(page.getByText("No number assigned")).toBeVisible();
    await page.getByRole("button", { name: "Choose" }).click();
    const filter = page.getByRole("textbox", { name: "Filter by area code or name" });
    await filter.fill("2222");
    await expect(page.locator(".num-opt")).toHaveCount(1);
    await filter.fill("support");
    await expect(page.locator(".num-opt")).toHaveCount(1);
    await expect(page.locator(".num-opt")).toContainText("+1 (555) 000-3333");
    await filter.fill("zzz");
    await expect(page.getByText("No numbers match.")).toBeVisible();
  });

  test("another business's number says Used by X and the button says Move", async ({ page }) => {
    await openSettings(page);
    await page.getByRole("button", { name: "Change" }).click();
    const taken = page.locator(".num-opt").filter({ hasText: "+1 (555) 000-3333" });
    await expect(taken).toContainText("Used by Harbor Auto Repair");
    await expect(taken).toContainText("Voice");
    await expect(taken).not.toContainText("SMS");
    await expect(page.locator(".num-opt").filter({ hasText: "+1 (555) 000-4444" })).toContainText("Not routed to this app");
    await taken.click();
    await expect(taken).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: "Move from Harbor Auto Repair" })).toBeEnabled();
    // the business's own number can't be picked again
    const own = page.locator(".num-opt").filter({ hasText: "+1 (555) 000-1111" });
    await expect(own).toBeDisabled();
    await expect(own).toContainText("Current number");
  });

  test("pressing anywhere on the number panel opens the picker", async ({ page }) => {
    await openSettings(page);
    await page.getByTestId("settings-number").click();
    await expect(page.getByRole("dialog", { name: "Choose a phone number" })).toBeVisible();
  });

  test("assigning a free number updates the screen and the card", async ({ page }) => {
    const api = await openSettings(page);
    api.delay.assign = 300;
    await page.getByRole("button", { name: "Change" }).click();
    await page.locator(".num-opt").filter({ hasText: "+1 (555) 000-2222" }).click();
    await page.getByRole("button", { name: "Assign +1 (555) 000-2222" }).click();
    await expect(page.getByRole("button", { name: "Assigning…" })).toBeDisabled();
    await page.getByRole("button", { name: "Close number picker" }).click({ force: true }); // ignored while assigning
    await expect(sheet(page)).toBeVisible();
    await expect(sheet(page)).toHaveCount(0);
    await expect(page.getByTestId("settings-number")).toHaveText("+1 (555) 000-2222");
    expect(api.requests).toContain("PUT /businesses/b1/number");
    await back(page).click();
    await expect(row(page)).toContainText("+1 (555) 000-2222");
  });

  test("moving a number from another business takes it off that business", async ({ page }) => {
    await openSettings(page, {}, "Happy Paws Grooming");
    await page.getByRole("button", { name: "Choose" }).click();
    await page.locator(".num-opt").filter({ hasText: "+1 (555) 000-3333" }).click();
    await page.getByRole("button", { name: "Move from Harbor Auto Repair" }).click();
    await expect(sheet(page)).toHaveCount(0);
    await expect(page.getByTestId("settings-number")).toHaveText("+1 (555) 000-3333");
    await back(page).click();
    await expect(page.getByTestId("biz-card").filter({ hasText: "Happy Paws Grooming" }).getByTestId("biz-num")).toHaveText("+1 (555) 000-3333");
    await expect(page.getByTestId("biz-card").filter({ hasText: "Harbor Auto Repair" }).getByTestId("biz-num")).toHaveCount(0);
  });

  test("a failed assignment shows the error and keeps the picker open", async ({ page }) => {
    await openSettings(page, { fail: new Set(["assign"]) });
    await page.getByRole("button", { name: "Change" }).click();
    await page.locator(".num-opt").filter({ hasText: "+1 (555) 000-2222" }).click();
    await page.getByRole("button", { name: "Assign +1 (555) 000-2222" }).click();
    await expect(sheet(page).getByRole("alert")).toHaveText("HTTP 500");
    await expect(sheet(page)).toBeVisible();
    await expect(page.getByTestId("settings-number")).toHaveText("+1 (555) 000-1111");
  });

  test("numbers that fail to load show the error", async ({ page }) => {
    await openSettings(page, { fail: new Set(["numbers"]) });
    await page.getByRole("button", { name: "Change" }).click();
    await expect(sheet(page).getByRole("alert")).toContainText("Couldn’t load numbers");
  });

  test("Twilio not configured: the account error from the API is shown", async ({ page }) => {
    await openSettings(page, { numbers: { numbers: [], error: "Twilio is not configured" } });
    await page.getByRole("button", { name: "Change" }).click();
    await expect(sheet(page).getByRole("alert")).toContainText("Twilio is not configured");
  });
});
