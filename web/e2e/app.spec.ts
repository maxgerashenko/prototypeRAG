// The voice app at /web/ — every flow of the "Voice Chat Bot" design (DEC-42) plus every
// behaviour of the plain-HTML version it replaced (main's web/mic-test.html and its
// tests/test_ui.py), against a mocked backend shaped like app/api/businesses.py,
// app/api/conversations.py and /voice/browser in push-to-talk mode.

import { expect, test, type Page } from "@playwright/test";
import { biz, convo, mockApi, mockVoice, hasSound, type ApiState, type VoiceOptions, type VoiceServer } from "./mocks";

const MIN = 60_000;
// tests that assert date labels pin the page clock here (UTC, see playwright.config.ts)
const FIXED = Date.UTC(2026, 9, 4, 15, 0);

const BAKERY = biz("b1", "Corner Bakery", "https://www.cornerbakery.example/", 3, "cornerbakery.example");
const AUTO = biz("b2", "Harbor Auto Repair", "https://www.harborauto.example/service", 1); // no domain: host of website
const FLORIST = biz("b3", "Bloom & Stem", "https://bloomstem.example/", 5, "bloomstem.example");
const PETS = biz("b4", "Happy Paws Grooming", null, 0);
const XSS = biz("b5", "<img src=x onerror=window.__xss=1>", "https://evil.example/", 0);

const greet = "Hi, you've reached us. How can I help?";
const florist = Array.from({ length: 5 }, (_, i) =>
  convo(`f${i}`, `Florist question ${i + 1}`, [greet, `Question ${i + 1}?`, `Answer ${i + 1}.`], (i + 1) * 30 * MIN));

function data(now = Date.now()): Partial<ApiState> {
  return {
    businesses: [FLORIST, BAKERY, PETS, AUTO, XSS],
    conversations: {
      b1: [
        convo("c1", "When are you open on Sunday", [greet, "When are you open on Sunday?", "From 8am to 2pm.", "Thanks!", "Anything else?"], 2 * MIN, 252, now),
        convo("c2", "Do you take reservations", [greet, "Do you take reservations?", "Yes, online or by phone."], 26 * 60 * MIN, 95, now, "twilio"),
        convo("c3", "Gift cards", [greet, "How much is a gift card?"], 40 * 24 * 60 * MIN, 95, now),
      ],
      b2: [convo("a1", "Oil change price", [greet, "How much is an oil change?", "About forty dollars."])],
      b3: florist,
    },
  };
}

// each test's mocked /voice/browser (must exist before page.goto, see mocks.ts)
let voice: VoiceServer;
test.beforeEach(async ({ page }) => {
  voice = await mockVoice(page);
});

const card = (page: Page, name: string) => page.getByTestId("biz-card").filter({ hasText: name });
const pick = (page: Page, name: string) => card(page, name).locator(".biz-pick").click();
const callButton = (page: Page) => page.locator(".call-btn");
const status = (page: Page) => page.getByTestId("call-status");
const ptt = (page: Page) => page.getByRole("button", { name: "Push to talk — hold to speak" });
const sent = (type: string) => () => voice.texts.filter((t) => t.type === type).length;

async function openApp(page: Page, init: Partial<ApiState> = data()) {
  const api = await mockApi(page, init);
  await page.goto("/web/");
  return api;
}

async function startCall(page: Page, name = "Corner Bakery", voiceOpts: VoiceOptions = {}) {
  voice.configure(voiceOpts);
  await pick(page, name);
  await callButton(page).click();
  await expect(page.locator("[data-screen=call]")).toBeVisible();
  return voice;
}

async function holdSpace(page: Page) {
  await page.keyboard.down(" ");
  await expect.poll(sent("ptt_start")).toBe(1);
}

test.describe("1 · pick a business", () => {
  test("lists businesses with monogram, host and chat count", async ({ page }) => {
    await openApp(page);
    await expect(page.getByRole("heading", { name: "Talk about a business, out loud" })).toBeVisible();
    await expect(page.getByText("Voice assistant")).toBeVisible();
    await expect(page.getByTestId("biz-card")).toHaveCount(5);
    await expect(card(page, "Corner Bakery")).toContainText("CB");
    await expect(card(page, "Corner Bakery")).toContainText("cornerbakery.example · 3 chats");
    await expect(card(page, "Harbor Auto Repair")).toContainText("harborauto.example · 1 chat"); // www. dropped
    await expect(card(page, "Happy Paws Grooming")).toContainText("No website · No chats yet");
    await expect(callButton(page)).toBeDisabled();
    await expect(callButton(page)).toHaveText("Select a business to call");
    await expect(page.locator(".halo")).toHaveCount(0);
  });

  test("search filters by name or host; no-match message", async ({ page }) => {
    await openApp(page);
    const search = page.getByRole("textbox", { name: "Search businesses" });
    await search.fill("BAKE");
    await expect(page.getByTestId("biz-card")).toHaveText([/Corner Bakery/]);
    await search.fill("bloomstem");
    await expect(page.getByTestId("biz-card")).toHaveText([/Bloom & Stem/]);
    await search.fill("zzz");
    await expect(page.getByText("No businesses match your search.")).toBeVisible();
    await search.fill("");
    await expect(page.getByTestId("biz-card")).toHaveCount(5);
  });

  test("Space typed in the search box types a space, never starts a call", async ({ page }) => {
    await openApp(page);
    await pick(page, "Corner Bakery"); // selected: Space outside the box would call
    const search = page.getByRole("textbox", { name: "Search businesses" });
    await search.focus();
    await page.keyboard.press(" ");
    await expect(search).toHaveValue(" ");
    await page.waitForTimeout(300);
    expect(voice.url()).toBe("");
  });

  test("the selected business stays visible while searching", async ({ page }) => {
    await openApp(page);
    await pick(page, "Corner Bakery");
    await page.getByRole("textbox", { name: "Search businesses" }).fill("dental");
    await expect(page.getByTestId("biz-card")).toHaveText([/Corner Bakery/]);
    await expect(page.getByText("No businesses match your search.")).toHaveCount(0);
  });

  test("selecting expands recent conversations and arms the call button", async ({ page }) => {
    await page.clock.setFixedTime(FIXED);
    const api = await openApp(page, data(FIXED));
    const bakery = card(page, "Corner Bakery");
    const button = bakery.locator(".biz-pick");
    await expect(button).toHaveAttribute("aria-pressed", "false");
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await expect(button).toHaveAttribute("aria-expanded", "true");
    await expect(bakery).toHaveClass(/\bon\b/);
    await expect(bakery.locator(".radio svg")).toBeVisible();
    await expect(bakery.getByText("Recent conversations")).toBeVisible();
    const rows = bakery.locator(".recent-row");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText("When are you open on Sunday");
    await expect(rows.nth(0)).toContainText("Today · 2:58 PM · 4:12 · 5 msgs");
    await expect(rows.nth(1)).toContainText("Yesterday · 1:00 PM · 1:35 · 3 msgs");
    await expect(rows.nth(2)).toContainText("Aug 25 · 3:00 PM · 1:35 · 2 msgs");
    await expect(bakery.getByRole("button", { name: /See all/ })).toHaveCount(0); // exactly 3
    await expect(bakery.locator(".badge-new")).toHaveCount(0);
    expect(api.requests).toContain("/businesses/b1/conversations");

    await expect(callButton(page)).toBeEnabled();
    await expect(callButton(page)).toHaveText("Call about Corner Bakery");
    await expect(page.locator(".halo")).toHaveCount(1);
    await expect(page.locator(".spacer")).toHaveCount(1);
  });

  test("only one business is selected; picking it again unselects", async ({ page }) => {
    await openApp(page);
    await pick(page, "Corner Bakery");
    await pick(page, "Harbor Auto Repair");
    await expect(page.locator(".biz-card.on")).toHaveText([/Harbor Auto Repair/]);
    await expect(callButton(page)).toHaveText("Call about Harbor Auto Repair");
    await pick(page, "Harbor Auto Repair");
    await expect(page.locator(".biz-card.on")).toHaveCount(0);
    await expect(callButton(page)).toBeDisabled();
  });

  test("more than 3 conversations: 'See all N' opens the list", async ({ page }) => {
    await openApp(page);
    await pick(page, "Bloom & Stem");
    await expect(card(page, "Bloom & Stem").locator(".recent-row")).toHaveCount(3);
    await card(page, "Bloom & Stem").getByRole("button", { name: "See all 5 conversations" }).click();
    await expect(page.locator("[data-screen=convos]")).toBeVisible();
  });

  test("a business without conversations says so", async ({ page }) => {
    await openApp(page);
    await pick(page, "Happy Paws Grooming");
    await expect(page.getByText("No conversations yet. Start a call below and it will show up here.")).toBeVisible();
  });

  test("selecting scrolls the card to the top of the list", async ({ page }) => {
    await openApp(page);
    await pick(page, "Harbor Auto Repair");
    await expect.poll(() => page.locator(".biz-list").evaluate((el) => el.scrollTop)).toBeGreaterThan(50);
  });

  test("a recent row opens its transcript; Back returns to the business list", async ({ page }) => {
    await openApp(page);
    await pick(page, "Corner Bakery");
    await page.locator(".recent-row").first().click();
    await expect(page.locator("[data-screen=transcript]")).toBeVisible();
    await page.getByRole("button", { name: "Back" }).click();
    await expect(page.locator("[data-screen=select]")).toBeVisible();
    await expect(card(page, "Corner Bakery")).toHaveClass(/\bon\b/); // selection kept
  });

  test("Space calls the selected business; Backspace outside a call does nothing", async ({ page }) => {
    await openApp(page);
    await page.keyboard.press(" ");
    await page.keyboard.press("Backspace");
    await expect(page.locator("[data-screen=select]")).toBeVisible();
    expect(voice.url()).toBe("");
    await pick(page, "Corner Bakery");
    await page.keyboard.press(" ");
    await voice.connected;
    expect(voice.url()).toContain("business_id=b1");
  });

  test("server text is shown as text, never as HTML", async ({ page }) => {
    await openApp(page);
    await pick(page, XSS.name);
    await expect(card(page, XSS.name).locator(".biz-name")).toHaveText(XSS.name);
    await expect(callButton(page)).toHaveText(`Call about ${XSS.name}`);
    expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
  });

  test("loading, error with retry, and empty states", async ({ page }) => {
    const api = await mockApi(page, { ...data(), fail: new Set(["businesses"]) });
    await page.goto("/web/");
    await expect(page.getByRole("alert")).toContainText("Couldn’t load businesses (HTTP 500");
    api.fail.clear();
    api.businesses = [];
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByText("No businesses yet. Crawl one first:")).toBeVisible();
  });

  test("conversations that fail to load show an error in the card", async ({ page }) => {
    await openApp(page, { ...data(), fail: new Set(["conversations"]) });
    await pick(page, "Corner Bakery");
    await expect(card(page, "Corner Bakery").getByRole("alert")).toContainText("Couldn’t load conversations");
  });

  test("/web/ serves the app (index.html)", async ({ page }) => {
    await openApp(page);
    await expect(page).toHaveTitle("Voice Assistant");
  });
});

test.describe("2 · a business's previous conversations", () => {
  test("header, start call, cards with preview, meta, Back and Esc", async ({ page }) => {
    await openApp(page);
    await pick(page, "Bloom & Stem");
    await page.getByRole("button", { name: "See all 5 conversations" }).click();
    const screen = page.locator("[data-screen=convos]");
    await expect(screen.getByRole("heading", { level: 1 })).toHaveText("Bloom & Stem");
    await expect(screen.locator(".big-mono")).toHaveText("BS");
    await expect(screen.locator(".biz-head")).toContainText("bloomstem.example");
    await expect(screen.locator(".section-head")).toContainText("Previous conversations5");
    const cards = screen.locator(".convo-card");
    await expect(cards).toHaveCount(5);
    await expect(cards.first()).toContainText("Florist question 1");
    await expect(cards.first().locator(".convo-preview")).toHaveText("Answer 1.");
    await expect(cards.first().locator(".convo-meta")).toContainText(/1:35.*3 messages/);
    await page.getByRole("button", { name: "Back to businesses" }).click();
    await expect(page.locator("[data-screen=select]")).toBeVisible();

    await page.getByRole("button", { name: "See all 5 conversations" }).click();
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-screen=select]")).toBeVisible();
  });

  test("preview starts with 'You: ' when the caller spoke last; card opens transcript", async ({ page }) => {
    const init = data();
    init.conversations!.b3 = [...florist.slice(0, 3), convo("f9", "Hung up early", [greet, "Hello?"]), florist[4]];
    await openApp(page, init);
    await pick(page, "Bloom & Stem");
    await page.getByRole("button", { name: /See all/ }).click();
    const early = page.locator(".convo-card").filter({ hasText: "Hung up early" });
    await expect(early.locator(".convo-preview")).toHaveText("You: Hello?");
    await early.click();
    await expect(page.locator("[data-screen=transcript] h1")).toHaveText("Hung up early");
    await page.getByRole("button", { name: "Back" }).click();
    await expect(page.locator("[data-screen=convos]")).toBeVisible(); // returns where it came from
  });

  test("call source: green phone for Twilio calls, blue globe for web calls", async ({ page }) => {
    const init = data();
    init.conversations!.b1.push(convo("c4", "Parking", [greet, "Where can I park?"], 50 * 24 * 60 * MIN));
    await openApp(page, init);
    await pick(page, "Corner Bakery");
    const rows = page.locator(".recent-row");
    await expect(rows.nth(0).locator(".src-badge")).toHaveClass(/src-web/);
    await expect(rows.nth(0).getByRole("img", { name: "Web" })).toBeVisible();
    await expect(rows.nth(1).locator(".src-badge")).toHaveClass(/src-twilio/);
    await expect(rows.nth(1).getByRole("img", { name: "Phone" })).toBeVisible();
    // the colours themselves: --call (green) and --accent (blue) from app.css
    const color = (i: number) => rows.nth(i).locator(".src-icon").evaluate((el) => getComputedStyle(el).color);
    expect(await color(0)).toBe("rgb(138, 180, 248)");
    expect(await color(1)).toBe("rgb(127, 214, 168)");

    await page.getByRole("button", { name: "See all 4 conversations" }).click();
    const cards = page.locator(".convo-card");
    await expect(cards.nth(0).locator(".convo-top .src-badge")).toHaveClass(/src-web/);
    await expect(cards.nth(1).locator(".convo-top .src-badge")).toHaveClass(/src-twilio/);
    await cards.nth(1).click();
    await expect(page.locator("[data-screen=transcript] .tr-title .src-badge")).toHaveClass(/src-twilio/);
  });

  test("'Start new call' and Space call this business", async ({ page }) => {
    await openApp(page);
    await pick(page, "Bloom & Stem");
    await page.getByRole("button", { name: /See all/ }).click();
    await page.keyboard.press(" ");
    await voice.connected;
    expect(voice.url()).toContain("business_id=b3");
    expect(voice.url()).not.toContain("continue_from");
    await expect(page.locator(".biz-chip")).toContainText("Bloom & Stem");
  });

  test("'Start new call' button calls this business", async ({ page }) => {
    await openApp(page);
    await pick(page, "Bloom & Stem");
    await page.getByRole("button", { name: /See all/ }).click();
    await page.getByRole("button", { name: "Start new call" }).click();
    await expect(page.locator("[data-screen=call]")).toBeVisible();
    expect(Object.fromEntries(new URL(voice.url()).searchParams)).toEqual({ business_id: "b3", mode: "ptt" });
  });

  test("card meta: when, duration and message count", async ({ page }) => {
    await page.clock.setFixedTime(FIXED);
    await openApp(page, data(FIXED));
    await startCall(page, "Corner Bakery"); // nobody speaks: no NEW card afterwards
    await page.getByRole("button", { name: "End call" }).click();
    await page.getByRole("button", { name: "Back to conversations" }).click();
    const cards = page.locator("[data-screen=convos] .convo-card");
    await expect(cards).toHaveCount(3);
    await expect(cards.nth(0).locator(".convo-meta")).toHaveText("Today · 2:58 PM4:125 messages");
    await expect(cards.nth(1).locator(".convo-meta")).toHaveText("Yesterday · 1:00 PM1:353 messages");
    await expect(cards.nth(2).locator(".convo-meta")).toHaveText("Aug 25 · 3:00 PM1:352 messages");
    await expect(cards.nth(2).locator(".convo-preview")).toHaveText("You: How much is a gift card?");
    await expect(page.locator(".badge-new")).toHaveCount(0);
  });

  test("a business without conversations shows the empty state", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Happy Paws Grooming");
    await page.getByRole("button", { name: "End call" }).click();
    await page.getByRole("button", { name: "Back to conversations" }).click();
    const screen = page.locator("[data-screen=convos]");
    await expect(screen.locator("h1")).toHaveText("Happy Paws Grooming");
    await expect(screen.locator(".biz-head")).toContainText("No website");
    await expect(screen.locator(".section-head")).toHaveText("Previous conversations0");
    await expect(screen.locator(".convo-card")).toHaveCount(0);
    await expect(screen.locator(".empty")).toContainText("No conversations yet");
    await expect(screen.locator(".empty")).toContainText("Start a call and it will show up here when you hang up.");
  });

  test("conversations that fail to load show an error on the list", async ({ page }) => {
    await openApp(page, { ...data(), fail: new Set(["conversations"]) });
    await startCall(page, "Corner Bakery");
    await page.getByRole("button", { name: "End call" }).click();
    await page.getByRole("button", { name: "Back to conversations" }).click();
    await expect(page.locator("[data-screen=convos]").getByRole("alert")).toContainText("Couldn’t load conversations (HTTP 500");
  });
});

test.describe("3 · read a past conversation", () => {
  test("title, meta, start/end pills and timed bubbles", async ({ page }) => {
    await page.clock.setFixedTime(FIXED);
    await openApp(page, data(FIXED));
    await pick(page, "Corner Bakery");
    await page.locator(".recent-row").first().click();
    const screen = page.locator("[data-screen=transcript]");
    await expect(screen.locator("h1")).toHaveText("When are you open on Sunday");
    await expect(screen.locator(".tr-meta")).toHaveText("Today · 2:58 PM·4:12·5 messages");
    await expect(screen.locator(".pill").first()).toHaveText("Call started · Today · 2:58 PM");
    await expect(screen.locator(".pill").last()).toHaveText("Call ended · 4:12");
    await expect(screen.getByRole("button", { name: "Back" })).toContainText("Corner Bakery");
    const msgs = screen.locator(".msg");
    await expect(msgs).toHaveCount(5);
    await expect(msgs.nth(0)).toHaveAttribute("data-role", "bot");
    await expect(msgs.nth(0)).toContainText(`${greet}Assistant · 0:02`);
    await expect(msgs.nth(1)).toHaveAttribute("data-role", "user");
    await expect(msgs.nth(1)).toContainText("When are you open on Sunday?You · 0:21");
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-screen=select]")).toBeVisible();
  });

  test("a conversation that fails to load shows an error", async ({ page }) => {
    await openApp(page, { ...data(), fail: new Set(["detail"]) });
    await pick(page, "Corner Bakery");
    await page.locator(".recent-row").first().click();
    await expect(page.getByRole("alert")).toContainText("Couldn’t load this conversation (HTTP 500");
  });

  test("'Continue in a new call' continues this conversation (continue_from, push-to-talk)", async ({ page }) => {
    await openApp(page);
    await pick(page, "Corner Bakery");
    await page.locator(".recent-row").first().click();
    await page.getByRole("button", { name: "Continue in a new call" }).click();
    await voice.connected;
    const url = new URL(voice.url());
    expect(url.pathname).toBe("/voice/browser");
    expect(Object.fromEntries(url.searchParams)).toEqual({ business_id: "b1", mode: "ptt", continue_from: "c1" });
    await expect(page.locator("[data-screen=call]")).toBeVisible();
  });

  test("Space on a transcript continues it too", async ({ page }) => {
    await openApp(page);
    await pick(page, "Corner Bakery");
    await page.locator(".recent-row").nth(1).click();
    await expect(page.locator("[data-screen=transcript] h1")).toHaveText("Do you take reservations");
    await page.keyboard.press(" ");
    await voice.connected;
    expect(voice.url()).toContain("continue_from=c2");
  });
});

test.describe("4 · connecting", () => {
  test("shows the business; Cancel, Esc and Backspace cancel", async ({ page }) => {
    await openApp(page);
    voice.configure({ accept: false }); // never confirms
    await pick(page, "Corner Bakery");
    await callButton(page).click();
    const screen = page.locator("[data-screen=connecting]");
    await expect(screen.getByRole("heading", { name: "Connecting…" })).toBeVisible();
    await expect(screen).toContainText("Starting a call about Corner Bakery");
    await voice.connected;
    expect(new URL(voice.url()).searchParams.get("mode")).toBe("ptt");
    await screen.getByRole("button", { name: "Cancel" }).click();
    await expect(page.locator("[data-screen=select]")).toBeVisible();
    await expect.poll(voice.closedByClient).toBe(true);
    await expect(page.getByRole("alert")).toHaveCount(0);

    for (const key of ["Escape", "Backspace"]) {
      await callButton(page).click();
      await expect(page.locator("[data-screen=connecting]")).toBeVisible();
      await page.keyboard.press(key);
      await expect(page.locator("[data-screen=select]")).toBeVisible();
    }
  });

  test("an unknown business (4404) returns to the list with 'Unknown business'", async ({ page }) => {
    await openApp(page);
    voice.configure({ accept: false });
    await pick(page, "Corner Bakery");
    await callButton(page).click();
    await voice.connected;
    await voice.close(4404, "unknown business");
    await expect(page.locator(".banner")).toContainText("Unknown business");
    await page.getByRole("button", { name: "Dismiss" }).click();
    await expect(page.locator(".banner")).toHaveCount(0);
  });

  test("another close while connecting says 'Connection failed'", async ({ page }) => {
    await openApp(page);
    voice.configure({ accept: false });
    await pick(page, "Corner Bakery");
    await callButton(page).click();
    await voice.connected;
    await voice.close(1011, "internal error");
    await expect(page.locator(".banner")).toContainText("Connection failed: internal error");
  });

  test("a denied microphone returns with the browser's error", async ({ page }) => {
    await openApp(page);
    await page.evaluate(() => {
      navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
    });
    await pick(page, "Corner Bakery");
    await callButton(page).click();
    await expect(page.locator(".banner")).toContainText("Couldn’t start the call: Permission denied");
    await expect(page.locator("[data-screen=select]")).toBeVisible();
    expect(voice.url()).toBe("");
  });
});

test.describe("5 · live call with push-to-talk", () => {
  test("header, greeting, timer", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: "Hi, you've reached Corner Bakery." });
    await expect(page.locator(".live-pill")).toContainText("Live");
    await expect(page.locator(".biz-chip")).toHaveText("CBCorner Bakery");
    await expect(page.locator(".msg").first()).toContainText("Hi, you've reached Corner Bakery.Assistant · 0:00");
    await expect(page.getByTestId("call-timer")).toHaveText("0:00");
    await expect(page.getByTestId("call-timer")).toHaveText("0:02", { timeout: 4000 });
  });

  test("ptt_start → mic audio → ptt_end; nothing is sent outside a turn", async ({ page }) => {
    await openApp(page);
    await startCall(page);
    await page.waitForTimeout(1000);
    expect(voice.frames).toHaveLength(0);
    expect(voice.texts).toEqual([]);

    await ptt(page).hover();
    await page.mouse.down();
    await expect.poll(sent("ptt_start")).toBe(1);
    await page.waitForTimeout(2500); // the fake mic beeps about once a second
    await page.mouse.up();
    await expect.poll(sent("ptt_end")).toBe(1);
    expect(voice.frames.length).toBeGreaterThan(50);
    expect(voice.frames.every((f) => f.byteLength === 640)).toBe(true); // 20 ms PCM16 at 16 kHz
    expect(voice.frames.some(hasSound)).toBe(true);

    const after = voice.frames.length;
    await page.waitForTimeout(800);
    expect(voice.frames.length).toBe(after);
    expect(voice.texts).toEqual([{ type: "ptt_start" }, { type: "ptt_end" }]);
  });

  test("a full turn: listening → thinking → speaking → idle, with response time", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    await expect(status(page)).toHaveText("Hold the button to talk");
    await expect(page.getByText("Hold to talk")).toBeVisible();

    await ptt(page).hover();
    await page.mouse.down();
    await expect(status(page)).toHaveText("Listening to you…");
    await expect(ptt(page)).toHaveAttribute("aria-pressed", "true");
    await expect(ptt(page)).toContainText("Release to send");
    await expect(page.getByTestId("listening")).toBeVisible();
    await expect(page.locator(".orb-wrap.call .orb-ring")).toHaveCount(2);

    await page.mouse.up();
    await expect(status(page)).toHaveText("Thinking…");
    await expect(page.getByTestId("thinking")).toBeVisible();
    await expect(page.getByTestId("listening")).toHaveCount(0);

    voice.send({ type: "transcript", text: "What time do you close?" });
    await expect(page.locator(".msg[data-role=user]")).toContainText("What time do you close?");
    voice.send({ type: "reply", text: "We close at 9 pm." });
    voice.sendAudio(8000); // 0.5 s
    voice.send({ type: "reply", text: "Anything else?" });
    await expect(status(page)).toHaveText("Assistant is speaking");
    await expect(page.getByTestId("thinking")).toHaveCount(0);
    const answer = page.locator(".msg[data-role=bot]").last();
    await expect(answer).toContainText("We close at 9 pm. Anything else?");
    voice.send({ type: "latency", stt_ms: 80, llm_first_sentence_ms: 900, tts_first_audio_ms: 1400, total_ms: 1600 });
    await expect(answer.locator(".msg-meta")).toHaveText(/^Assistant · \d:\d\d · 1\.4 s$/);
    await expect(answer.locator(".msg-meta")).toHaveAttribute("title",
      "stt_ms: 80\nllm_first_sentence_ms: 900\ntts_first_audio_ms: 1400\ntotal_ms: 1600");
    await expect(status(page)).toHaveText("Hold the button to talk", { timeout: 3000 });
  });

  test("keyboard: hold Space to talk anywhere on the call screen; auto-repeat ignored", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    await holdSpace(page);
    await expect(status(page)).toHaveText("Listening to you…");
    await page.keyboard.down(" "); // auto-repeat
    await page.keyboard.up(" ");
    await expect.poll(sent("ptt_end")).toBe(1);
    expect(sent("ptt_start")()).toBe(1);
    await expect(status(page)).toHaveText("Thinking…");
  });

  test("leaving the window while holding releases the button", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    await holdSpace(page);
    await page.evaluate(() => window.dispatchEvent(new Event("blur")));
    await expect.poll(sent("ptt_end")).toBe(1);
    await expect(status(page)).toHaveText("Thinking…");
  });

  test("pressing while the assistant speaks interrupts it; clear marks the answer interrupted", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    voice.send({ type: "reply", text: "Starting response" });
    voice.sendAudio(48000); // 3 s
    await expect(status(page)).toHaveText("Assistant is speaking");
    await ptt(page).hover();
    await page.mouse.down();
    await expect(status(page)).toHaveText("Listening to you…");
    voice.send({ type: "clear" }); // the server cancelled its reply
    await expect(page.locator(".msg[data-role=bot] .msg-meta")).toHaveText(/· interrupted$/);
    await page.mouse.up();
  });

  test("no_speech from the server shows main's hint", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    await ptt(page).hover();
    await page.mouse.down();
    await expect.poll(sent("ptt_start")).toBe(1);
    await page.mouse.up();
    await expect.poll(sent("ptt_end")).toBe(1);
    voice.send({ type: "no_speech" });
    await expect(status(page)).toHaveText("Didn't catch that — hold and try again");
  });

  test("if the server never answers a turn, the app stops waiting after 6 s", async ({ page }) => {
    test.slow();
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    await ptt(page).hover();
    await page.mouse.down();
    await page.mouse.up();
    await expect(status(page)).toHaveText("Thinking…");
    await expect(status(page)).toHaveText("Didn't catch that — hold and try again", { timeout: 9000 });
  });

  test("a server error shows as a notice, as text", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    voice.send({ type: "error", message: "<b>boom</b>" });
    await expect(page.locator(".pill.error")).toHaveText("Error: <b>boom</b>");
  });

  test("the context menu is suppressed on the talk button", async ({ page }) => {
    await openApp(page);
    await startCall(page);
    const prevented = await ptt(page).evaluate((el) => {
      const e = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
      el.dispatchEvent(e);
      return e.defaultPrevented;
    });
    expect(prevented).toBe(true);
  });

  test("orb and status line follow the turn: idle, listening, thinking, speaking", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    const orb = page.getByTestId("orb");
    const rings = orb.locator(".orb-ring");
    const bars = (play: string, speed: string) =>
      expect(orb).toHaveAttribute("style", new RegExp(`--bar-speed: ${speed.replace(".", "\\.")}; --bar-play: ${play}`));

    // idle: no rings, bars still, muted status
    await expect(status(page)).toHaveText("Hold the button to talk");
    await expect(status(page)).toHaveClass(/\bidle\b/);
    await expect(rings).toHaveCount(0);
    await bars("paused", "1s");
    await expect(orb).not.toHaveClass(/\bcall\b/);

    // listening: call-coloured orb with rings, fast bars
    await ptt(page).hover();
    await page.mouse.down();
    await expect(status(page)).toHaveClass(/\bcall\b/);
    await expect(orb).toHaveClass(/\bcall\b/);
    await expect(rings).toHaveCount(2);
    await bars("running", ".7s");

    // thinking: assistant colour, no rings, slow bars
    await page.mouse.up();
    await expect(status(page)).toHaveText("Thinking…");
    await expect(status(page)).toHaveClass(/\bbot\b/);
    await expect(orb).not.toHaveClass(/\bcall\b/);
    await expect(rings).toHaveCount(0);
    await bars("running", "1.8s");

    // speaking: rings again
    voice.send({ type: "transcript", text: "Hi" });
    voice.send({ type: "reply", text: "Hello there." });
    voice.sendAudio(32000); // 2 s
    await expect(status(page)).toHaveText("Assistant is speaking");
    await expect(rings).toHaveCount(2);
    await bars("running", ".85s");
  });

  test("keyboard on the focused talk button: Enter holds to talk", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    await ptt(page).focus();
    await page.keyboard.down("Enter");
    await expect.poll(sent("ptt_start")).toBe(1);
    await expect(status(page)).toHaveText("Listening to you…");
    await expect(ptt(page)).toContainText("Release to send");
    await page.keyboard.down("Enter"); // auto-repeat
    await page.keyboard.up("Enter");
    await expect.poll(sent("ptt_end")).toBe(1);
    expect(sent("ptt_start")()).toBe(1);
    await expect(ptt(page)).toContainText("Hold to talk");
  });

  test("a cancelled pointer (e.g. a scroll gesture) releases the talk button", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    await ptt(page).hover();
    await page.mouse.down();
    await expect.poll(sent("ptt_start")).toBe(1);
    await ptt(page).dispatchEvent("pointercancel");
    await expect.poll(sent("ptt_end")).toBe(1);
    await expect(status(page)).toHaveText("Thinking…");
    await page.mouse.up();
  });

  test("the newest message stays in view as the call log grows", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    for (let i = 1; i <= 12; i++) {
      voice.send({ type: "transcript", text: `Question ${i}?` });
      voice.send({ type: "reply", text: `Answer ${i}, with enough words to wrap onto a second line.` });
    }
    await expect(page.locator(".msg")).toHaveCount(24);
    await expect(page.locator(".msg").first()).not.toBeInViewport();
    await expect(page.locator(".msg").last()).toBeInViewport();
    await expect(page.locator(".msg").last()).toContainText("Answer 12");
  });

  test("Esc and Backspace end the call", async ({ page }) => {
    await openApp(page);
    await startCall(page);
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-screen=ended]")).toBeVisible();
    await page.keyboard.press("Escape"); // ended -> the business's conversations
    await expect(page.locator("[data-screen=convos]")).toBeVisible();
    await page.keyboard.press("Escape");
    await pick(page, "Corner Bakery");
    await pick(page, "Corner Bakery");
    await callButton(page).click();
    await expect(page.locator("[data-screen=call]")).toBeVisible();
    await page.keyboard.press("Backspace");
    await expect(page.locator("[data-screen=ended]")).toBeVisible();
  });

  test("the app blocks dev live-reload during a call", async ({ page }) => {
    await openApp(page);
    expect(await page.evaluate(() => window.devReloadBlocked?.())).toBe(false);
    await startCall(page);
    expect(await page.evaluate(() => window.devReloadBlocked?.())).toBe(true);
  });
});

test.describe("6 · call ended", () => {
  test("end call → summary → read it → back to the list with the call marked NEW", async ({ page }) => {
    const api = await openApp(page);
    await startCall(page, "Corner Bakery", { conversationId: "new-call", greeting: "Hello!" });
    voice.send({ type: "transcript", text: "Are you open?" });
    voice.send({ type: "reply", text: "Yes." });
    await expect(page.locator(".msg")).toHaveCount(3);
    voice.send({ type: "error", message: "not counted" });

    // the backend has saved it by the time the lists are reloaded
    api.conversations.b1 = [convo("new-call", "Are you open", ["Hello!", "Are you open?", "Yes."], 0, 3), ...api.conversations.b1!];
    api.businesses = api.businesses.map((b) => (b.id === "b1" ? { ...b, conversation_count: 4 } : b));

    await page.getByRole("button", { name: "End call" }).click();
    const ended = page.locator("[data-screen=ended]");
    await expect(ended.getByRole("heading", { name: "Call ended" })).toBeVisible();
    await expect(ended).toContainText("Saved to Corner Bakery’s conversations");
    await expect(page.getByTestId("ended-duration")).toHaveText(/^0:0\d$/);
    await expect(page.getByTestId("ended-messages")).toHaveText("3");
    await expect.poll(voice.closedByClient).toBe(true);

    await page.getByRole("button", { name: "Read this conversation" }).click();
    await expect(page.locator("[data-screen=transcript] h1")).toHaveText("Are you open");
    expect(api.requests).toContain("/businesses/b1/conversations/new-call");

    await page.getByRole("button", { name: "Back" }).click(); // to the business's conversations
    await expect(page.locator("[data-screen=convos]")).toBeVisible();
    await expect(page.locator(".convo-card.new")).toHaveCount(1);
    await expect(page.locator(".convo-card.new")).toContainText("Are you openNEW");
    await page.getByRole("button", { name: "Back to businesses" }).click();
    const bakery = card(page, "Corner Bakery");
    await expect(bakery).toContainText("cornerbakery.example · 4 chats");
    await expect(bakery.locator(".recent-row").first()).toContainText("Are you openNEW");
  });

  test("a call nobody spoke in: 'Nothing was said', not readable, not NEW", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: "Hello!" });
    await page.getByRole("button", { name: "End call" }).click();
    await expect(page.getByRole("button", { name: "Nothing was said" })).toBeDisabled();
    await expect(page.getByTestId("ended-messages")).toHaveText("1");
    await page.getByRole("button", { name: "Back to conversations" }).click();
    await expect(page.locator("[data-screen=convos]")).toBeVisible();
    await expect(page.locator(".badge-new")).toHaveCount(0);
  });

  test("'Back to conversations' opens the called business's conversations", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Harbor Auto Repair");
    await page.getByRole("button", { name: "End call" }).click();
    await page.getByRole("button", { name: "Back to conversations" }).click();
    await expect(page.locator("[data-screen=convos] h1")).toHaveText("Harbor Auto Repair");
  });

  test("4404 during a call returns to the list with 'Unknown business'", async ({ page }) => {
    await openApp(page);
    await startCall(page);
    await voice.close(4404, "unknown business");
    await expect(page.locator("[data-screen=select]")).toBeVisible();
    await expect(page.locator(".banner")).toContainText("Unknown business");
  });

  test("another server close during a call shows the reason on the summary", async ({ page }) => {
    await openApp(page);
    await startCall(page);
    await voice.close(1011, "internal error");
    await expect(page.locator("[data-screen=ended]")).toContainText("The call was closed by the server: internal error");
  });
});
