// The voice app at /web/ — every flow of the "Voice Chat Bot" design (DEC-38), against a
// mocked backend: pick a business, read past conversations, call with hold-to-talk, end.

import { expect, test, type Page } from "@playwright/test";
import { biz, convo, hasSound, mockApi, mockVoice, type ApiState, type VoiceOptions, type VoiceServer } from "./mocks";

const MIN = 60_000;
// tests that assert date labels pin the page clock here (UTC, see playwright.config.ts)
const FIXED = Date.UTC(2026, 9, 4, 15, 0);
const BAKERY = biz("b1", "Corner Bakery", "Bakery", 3);
const AUTO = biz("b2", "Harbor Auto Repair", "Auto repair", 2);
const FLORIST = biz("b3", "Bloom & Stem", "Florist", 5);
const PETS = biz("b4", "Happy Paws Grooming", "Pet grooming", 0);

const greet = "Hi, you've reached us. How can I help?";
const florist = Array.from({ length: 5 }, (_, i) =>
  convo(`f${i}`, `Florist question ${i + 1}`, [greet, `Question ${i + 1}?`, `Answer ${i + 1}.`], (i + 1) * 30 * MIN));

function data(now = Date.now()): Partial<ApiState> {
  return {
    businesses: [FLORIST, BAKERY, PETS, AUTO],
    conversations: {
      b1: [
        convo("c1", "When are you open on Sunday", [greet, "When are you open on Sunday?", "From 8am to 2pm.", "Thanks!", "Anything else?"], 2 * MIN, 252, now),
        convo("c2", "Do you take reservations", [greet, "Do you take reservations?", "Yes, online or by phone."], 26 * 60 * MIN, 95, now),
        convo("c3", "Gift cards", [greet, "How much is a gift card?"], 40 * 24 * 60 * MIN, 95, now),
      ],
      b2: [convo("a1", "Oil change price", [greet, "How much is an oil change?", "About forty dollars."])],
      b3: florist,
    },
  };
}

const card = (page: Page, name: string) => page.getByTestId("biz-card").filter({ hasText: name });
const callButton = (page: Page) => page.locator(".call-btn");
const status = (page: Page) => page.getByTestId("call-status");

// each test's mocked /voice/browser (must exist before page.goto, see mocks.ts)
let voice: VoiceServer;
test.beforeEach(async ({ page }) => {
  voice = await mockVoice(page);
});

async function openApp(page: Page, init: Partial<ApiState> = data()) {
  const api = await mockApi(page, init);
  await page.goto("/web/");
  return api;
}

async function startCall(page: Page, name = "Corner Bakery", voiceOpts: VoiceOptions = {}) {
  voice.configure(voiceOpts);
  await card(page, name).getByRole("button", { name: new RegExp(name) }).click();
  await callButton(page).click();
  await expect(page.locator("[data-screen=call]")).toBeVisible();
  return voice;
}

test.describe("1 · pick a business", () => {
  test("lists businesses with monogram, category and chat count", async ({ page }) => {
    await openApp(page);
    await expect(page.getByRole("heading", { name: "Talk about a business, out loud" })).toBeVisible();
    await expect(page.getByText("Voice assistant")).toBeVisible();
    await expect(page.getByTestId("biz-card")).toHaveCount(4);
    await expect(card(page, "Corner Bakery")).toContainText("CB");
    await expect(card(page, "Corner Bakery")).toContainText("Bakery · 3 chats");
    await expect(card(page, "Harbor Auto Repair")).toContainText("Auto repair · 2 chats");
    await expect(card(page, "Happy Paws Grooming")).toContainText("Pet grooming · No chats yet");
    await expect(callButton(page)).toBeDisabled();
    await expect(callButton(page)).toHaveText("Select a business to call");
    await expect(page.locator(".halo")).toHaveCount(0);
  });

  test("search filters by name or category; no-match message", async ({ page }) => {
    await openApp(page);
    const search = page.getByRole("textbox", { name: "Search businesses" });
    await search.fill("BAKE");
    await expect(page.getByTestId("biz-card")).toHaveCount(1);
    await search.fill("florist");
    await expect(page.getByTestId("biz-card")).toHaveText([/Bloom & Stem/]);
    await search.fill("zzz");
    await expect(page.getByText("No businesses match your search.")).toBeVisible();
    await search.fill("");
    await expect(page.getByTestId("biz-card")).toHaveCount(4);
  });

  test("the selected business stays visible while searching", async ({ page }) => {
    await openApp(page);
    await card(page, "Corner Bakery").locator(".biz-pick").click();
    await page.getByRole("textbox", { name: "Search businesses" }).fill("dental");
    await expect(page.getByTestId("biz-card")).toHaveText([/Corner Bakery/]);
    await expect(page.getByText("No businesses match your search.")).toHaveCount(0);
  });

  test("selecting expands recent conversations and arms the call button", async ({ page }) => {
    await page.clock.setFixedTime(FIXED);
    const api = await openApp(page, data(FIXED));
    const bakery = card(page, "Corner Bakery");
    const pick = bakery.locator(".biz-pick");
    await expect(pick).toHaveAttribute("aria-pressed", "false");
    await pick.click();
    await expect(pick).toHaveAttribute("aria-pressed", "true");
    await expect(pick).toHaveAttribute("aria-expanded", "true");
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
    await card(page, "Corner Bakery").locator(".biz-pick").click();
    await card(page, "Harbor Auto Repair").locator(".biz-pick").click();
    await expect(page.locator(".biz-card.on")).toHaveText([/Harbor Auto Repair/]);
    await expect(callButton(page)).toHaveText("Call about Harbor Auto Repair");
    await card(page, "Harbor Auto Repair").locator(".biz-pick").click();
    await expect(page.locator(".biz-card.on")).toHaveCount(0);
    await expect(callButton(page)).toBeDisabled();
  });

  test("more than 3 conversations: 'See all N' opens the list", async ({ page }) => {
    await openApp(page);
    const florist = card(page, "Bloom & Stem");
    await florist.locator(".biz-pick").click();
    await expect(florist.locator(".recent-row")).toHaveCount(3);
    await florist.getByRole("button", { name: "See all 5 conversations" }).click();
    await expect(page.locator("[data-screen=convos]")).toBeVisible();
  });

  test("a business without conversations says so", async ({ page }) => {
    await openApp(page);
    await card(page, "Happy Paws Grooming").locator(".biz-pick").click();
    await expect(page.getByText("No conversations yet. Start a call below and it will show up here.")).toBeVisible();
  });

  test("selecting scrolls the card to the top of the list", async ({ page }) => {
    await openApp(page);
    const list = page.locator(".biz-list");
    await card(page, "Harbor Auto Repair").locator(".biz-pick").click();
    await expect.poll(async () => list.evaluate((el) => el.scrollTop)).toBeGreaterThan(50);
  });

  test("a recent row opens its transcript; Back returns to the business list", async ({ page }) => {
    await openApp(page);
    await card(page, "Corner Bakery").locator(".biz-pick").click();
    await page.locator(".recent-row").first().click();
    await expect(page.locator("[data-screen=transcript]")).toBeVisible();
    await page.getByRole("button", { name: "Back" }).click();
    await expect(page.locator("[data-screen=select]")).toBeVisible();
    await expect(card(page, "Corner Bakery")).toHaveClass(/\bon\b/); // selection kept
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
    await card(page, "Corner Bakery").locator(".biz-pick").click();
    await expect(card(page, "Corner Bakery").getByRole("alert")).toContainText("Couldn’t load conversations");
  });

  test("/ redirects-to path /web/ serves the app (index.html)", async ({ page }) => {
    await openApp(page);
    await expect(page).toHaveTitle("Voice Assistant");
  });
});

test.describe("2 · a business's previous conversations", () => {
  test("header, start call, cards with preview, meta and Back", async ({ page }) => {
    await openApp(page);
    await card(page, "Bloom & Stem").locator(".biz-pick").click();
    await page.getByRole("button", { name: "See all 5 conversations" }).click();
    const screen = page.locator("[data-screen=convos]");
    await expect(screen.getByRole("heading", { level: 1 })).toHaveText("Bloom & Stem");
    await expect(screen.locator(".big-mono")).toHaveText("BS");
    await expect(screen).toContainText("Florist");
    await expect(screen.locator(".section-head")).toContainText("Previous conversations5");
    const cards = screen.locator(".convo-card");
    await expect(cards).toHaveCount(5);
    await expect(cards.first()).toContainText("Florist question 1");
    await expect(cards.first().locator(".convo-preview")).toHaveText("Answer 1.");
    await expect(cards.first().locator(".convo-meta")).toContainText(/1:35.*3 messages/);
    await page.getByRole("button", { name: "Back to businesses" }).click();
    await expect(page.locator("[data-screen=select]")).toBeVisible();
  });

  test("preview starts with 'You: ' when the caller spoke last; card opens transcript", async ({ page }) => {
    const init = data();
    init.conversations!.b3 = [...florist.slice(0, 3), convo("f9", "Hung up early", [greet, "Hello?"]), florist[4]];
    await openApp(page, init);
    await card(page, "Bloom & Stem").locator(".biz-pick").click();
    await page.getByRole("button", { name: /See all/ }).click();
    const early = page.locator(".convo-card").filter({ hasText: "Hung up early" });
    await expect(early.locator(".convo-preview")).toHaveText("You: Hello?");
    await early.click();
    await expect(page.locator("[data-screen=transcript] h1")).toHaveText("Hung up early");
    await page.getByRole("button", { name: "Back" }).click();
    await expect(page.locator("[data-screen=convos]")).toBeVisible(); // returns where it came from
  });

  test("'Start new call' calls this business", async ({ page }) => {
    await openApp(page);
    await card(page, "Bloom & Stem").locator(".biz-pick").click();
    await page.getByRole("button", { name: /See all/ }).click();
    await page.getByRole("button", { name: "Start new call" }).click();
    await voice.connected;
    expect(voice.url()).toContain("business_id=b3");
    await expect(page.locator(".biz-chip")).toContainText("Bloom & Stem");
  });
});

test.describe("3 · read a past conversation", () => {
  test("title, meta, start/end pills and timed bubbles", async ({ page }) => {
    await page.clock.setFixedTime(FIXED);
    await openApp(page, data(FIXED));
    await card(page, "Corner Bakery").locator(".biz-pick").click();
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
  });

  test("a missing conversation shows an error", async ({ page }) => {
    await openApp(page, { ...data(), fail: new Set(["detail"]) });
    await card(page, "Corner Bakery").locator(".biz-pick").click();
    await page.locator(".recent-row").first().click();
    await expect(page.getByRole("alert")).toContainText("Couldn’t load this conversation (HTTP 500");
  });

  test("'Continue in a new call' starts a call for the same business", async ({ page }) => {
    await openApp(page);
    await card(page, "Corner Bakery").locator(".biz-pick").click();
    await page.locator(".recent-row").first().click();
    await page.getByRole("button", { name: "Continue in a new call" }).click();
    await voice.connected;
    expect(voice.url()).toContain("business_id=b1");
    await expect(page.locator("[data-screen=call]")).toBeVisible();
  });
});

test.describe("4 · connecting", () => {
  test("shows the business and can be cancelled", async ({ page }) => {
    await openApp(page);
    voice.configure({ accept: false }); // never confirms
    await card(page, "Corner Bakery").locator(".biz-pick").click();
    await callButton(page).click();
    const screen = page.locator("[data-screen=connecting]");
    await expect(screen.getByRole("heading", { name: "Connecting…" })).toBeVisible();
    await expect(screen).toContainText("Starting a call about Corner Bakery");
    await voice.connected;
    await screen.getByRole("button", { name: "Cancel" }).click();
    await expect(page.locator("[data-screen=select]")).toBeVisible();
    await expect.poll(voice.closedByClient).toBe(true);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("an unknown business (4404) returns with the reason", async ({ page }) => {
    await openApp(page);
    voice.configure({ accept: false });
    await card(page, "Corner Bakery").locator(".biz-pick").click();
    await callButton(page).click();
    await voice.connected;
    await voice.close(4404, "unknown business");
    await expect(page.locator(".banner")).toContainText("Couldn’t start the call: unknown business");
    await page.getByRole("button", { name: "Dismiss" }).click();
    await expect(page.locator(".banner")).toHaveCount(0);
  });

  test("a denied microphone returns with the browser's error", async ({ page }) => {
    await openApp(page);
    await page.evaluate(() => {
      navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
    });
    await card(page, "Corner Bakery").locator(".biz-pick").click();
    await callButton(page).click();
    await expect(page.locator(".banner")).toContainText("Couldn’t start the call: Permission denied");
    await expect(page.locator("[data-screen=select]")).toBeVisible();
  });
});

test.describe("5 · live call with hold-to-talk", () => {
  test("header, greeting, timer", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: "Hi, you've reached Corner Bakery." });
    await expect(page.locator(".live-pill")).toContainText("Live");
    await expect(page.locator(".biz-chip")).toHaveText("CBCorner Bakery");
    await expect(page.locator(".msg").first()).toContainText("Hi, you've reached Corner Bakery.Assistant · 0:00");
    await expect(page.getByTestId("call-timer")).toHaveText("0:00");
    await expect(page.getByTestId("call-timer")).toHaveText("0:02", { timeout: 4000 });
  });

  test("mic audio goes out only while the button is held", async ({ page }) => {
    await openApp(page);
    const voice = await startCall(page);
    await page.waitForTimeout(1500);
    const idle = voice.frames.length;
    expect(idle).toBeGreaterThan(20); // frames keep flowing, so the server VAD sees silence
    expect(voice.frames.every((f) => f.byteLength === 640 && !hasSound(f))).toBe(true);

    const ptt = page.getByRole("button", { name: "Push to talk — hold to speak" });
    await ptt.hover();
    await page.mouse.down();
    await page.waitForTimeout(2500); // the fake mic beeps about once a second
    await page.mouse.up();
    const held = voice.frames.slice(idle);
    expect(held.some(hasSound)).toBe(true);

    const after = voice.frames.length;
    await page.waitForTimeout(800);
    expect(voice.frames.slice(after + 2).every((f) => !hasSound(f))).toBe(true);
  });

  test("a full turn: listening → thinking → speaking → idle", async ({ page }) => {
    await openApp(page);
    const voice = await startCall(page, "Corner Bakery", { greeting: null });
    await expect(status(page)).toHaveText("Hold the button to talk");
    await expect(page.getByText("Hold to talk")).toBeVisible();

    const ptt = page.getByRole("button", { name: "Push to talk — hold to speak" });
    await ptt.hover();
    await page.mouse.down();
    await expect(status(page)).toHaveText("Listening to you…");
    await expect(ptt).toHaveAttribute("aria-pressed", "true");
    await expect(ptt).toContainText("Release to send");
    await expect(page.getByTestId("listening")).toBeVisible();
    await expect(page.locator(".orb-wrap.call .orb-ring")).toHaveCount(2);

    await page.mouse.up();
    await expect(status(page)).toHaveText("Thinking…");
    await expect(page.getByTestId("thinking")).toBeVisible();
    await expect(page.getByTestId("listening")).toHaveCount(0);

    voice.send({ type: "transcript", text: "Do you have gluten free bread?" });
    await expect(page.locator(".msg[data-role=user]")).toContainText("Do you have gluten free bread?");
    voice.send({ type: "reply", text: "Yes, every morning." });
    voice.sendAudio(8000); // 0.5 s
    voice.send({ type: "reply", text: "It sells out by noon." });
    await expect(status(page)).toHaveText("Assistant is speaking");
    await expect(page.getByTestId("thinking")).toHaveCount(0);
    await expect(page.locator(".msg[data-role=bot]").last()).toContainText("Yes, every morning. It sells out by noon.");
    voice.send({ type: "latency", stt_ms: 120, total_ms: 900 });
    await expect(status(page)).toHaveText("Hold the button to talk", { timeout: 3000 });
  });

  test("keyboard: holding Space talks, releasing sends", async ({ page }) => {
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    const ptt = page.getByRole("button", { name: "Push to talk — hold to speak" });
    await ptt.focus();
    await page.keyboard.down(" ");
    await expect(status(page)).toHaveText("Listening to you…");
    await page.keyboard.down(" "); // auto-repeat is ignored
    await page.keyboard.up(" ");
    await expect(status(page)).toHaveText("Thinking…");
  });

  test("pressing while the assistant speaks interrupts it", async ({ page }) => {
    await openApp(page);
    const voice = await startCall(page, "Corner Bakery", { greeting: null });
    voice.send({ type: "reply", text: "A long answer" });
    voice.sendAudio(48000); // 3 s
    await expect(status(page)).toHaveText("Assistant is speaking");
    await page.getByRole("button", { name: "Push to talk — hold to speak" }).hover();
    await page.mouse.down();
    await expect(status(page)).toHaveText("Listening to you…");
    await page.mouse.up();
  });

  test("a release with no speech heard shows a hint", async ({ page }) => {
    test.slow();
    await openApp(page);
    await startCall(page, "Corner Bakery", { greeting: null });
    await page.getByRole("button", { name: "Push to talk — hold to speak" }).hover();
    await page.mouse.down();
    await page.mouse.up();
    await expect(status(page)).toHaveText("Thinking…");
    await expect(status(page)).toHaveText("Didn’t catch that. Hold the button while you speak.", { timeout: 9000 });
  });

  test("a server error shows as a notice", async ({ page }) => {
    await openApp(page);
    const voice = await startCall(page, "Corner Bakery", { greeting: null });
    voice.send({ type: "error", message: "RuntimeError('TTS failed')" });
    await expect(page.locator(".pill.error")).toHaveText("Error: RuntimeError('TTS failed')");
  });

  test("the context menu is suppressed on the talk button", async ({ page }) => {
    await openApp(page);
    await startCall(page);
    const prevented = await page.getByRole("button", { name: "Push to talk — hold to speak" }).evaluate((el) => {
      const e = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
      el.dispatchEvent(e);
      return e.defaultPrevented;
    });
    expect(prevented).toBe(true);
  });
});

test.describe("6 · call ended", () => {
  test("end call → summary → read it → back to the list with the call marked NEW", async ({ page }) => {
    const api = await openApp(page);
    const voice = await startCall(page, "Corner Bakery", { conversationId: "new-call", greeting: "Hello!" });
    voice.send({ type: "transcript", text: "Are you open?" });
    voice.send({ type: "reply", text: "Yes." });
    await expect(page.locator(".msg")).toHaveCount(3);
    voice.send({ type: "error", message: "not counted" });

    // the backend has saved it by the time the lists are reloaded
    const saved = convo("new-call", "Are you open", ["Hello!", "Are you open?", "Yes."], 0, 3);
    api.conversations.b1 = [saved, ...api.conversations.b1!];
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

    await page.getByRole("button", { name: "Back" }).click();
    const bakery = card(page, "Corner Bakery");
    await expect(bakery).toContainText("Bakery · 4 chats");
    await expect(bakery.locator(".recent-row").first()).toContainText("Are you openNEW");
    await bakery.getByRole("button", { name: "See all 4 conversations" }).click();
    await expect(page.locator(".convo-card.new")).toHaveCount(1);
    await expect(page.locator(".convo-card.new")).toContainText("NEW");
  });

  test("'Back to conversations' returns to the business list", async ({ page }) => {
    await openApp(page);
    await startCall(page);
    await page.getByRole("button", { name: "End call" }).click();
    await page.getByRole("button", { name: "Back to conversations" }).click();
    await expect(page.locator("[data-screen=select]")).toBeVisible();
    await expect(card(page, "Corner Bakery")).toHaveClass(/\bon\b/);
  });

  test("a call the server closes shows the reason", async ({ page }) => {
    await openApp(page);
    const voice = await startCall(page);
    await voice.close(1011, "internal error");
    await expect(page.locator("[data-screen=ended]")).toContainText("The call was closed by the server: internal error");
  });
});
