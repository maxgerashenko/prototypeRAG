// Phone calls the system takes show up live in the voice app (DEC-46): GET /voice/feed is
// mocked as a Server-Sent Events stream. Each response is one batch of feed messages; the
// page's EventSource reconnects after it (retry: 50 ms) and gets the next batch once the
// test releases it, like a reconnect that starts with a fresh snapshot.

import { expect, test, type Page } from "@playwright/test";
import type { LiveCallInfo, LiveEvent, LiveFeedMessage } from "../src/api";
import { biz, convo, mockApi, mockVoice } from "./mocks";

const BAKERY = biz("b1", "Corner Bakery", "https://www.cornerbakery.example/", 1, "cornerbakery.example");
const greet = "Hi, you've reached Corner Bakery. I'm an AI assistant. How can I help?";

interface Feed {
  /** Serve the next batch to the page's next (re)connect. */
  send: (msgs: LiveFeedMessage[]) => void;
}

async function mockFeed(page: Page): Promise<Feed> {
  const batches: LiveFeedMessage[][] = [];
  const waiting: ((msgs: LiveFeedMessage[]) => void)[] = [];
  await page.route("**/voice/feed", async (route) => {
    const msgs = batches.shift() ?? (await new Promise<LiveFeedMessage[]>((r) => waiting.push(r)));
    const body = "retry: 50\n\n" + msgs.map((m) => `data: ${JSON.stringify(m)}\n\n`).join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });
  return {
    send: (msgs) => {
      const w = waiting.shift();
      if (w) w(msgs);
      else batches.push(msgs);
    },
  };
}

const call = (startedAgoMs = 5000): LiveCallInfo => ({
  id: "MZ1", business_id: "b1", conversation_id: "phone-1", caller: "+15550001111", started_at: Date.now() - startedAgoMs,
});
// what the server has for the call so far, for the snapshot that starts each reconnect
let history: LiveEvent[] = [];
test.beforeEach(() => {
  history = [];
});
const ev = (e: Record<string, unknown>) => {
  const event = { ...e, t: Date.now() } as LiveEvent;
  history.push(event);
  return { type: "call_event", call_id: "MZ1", event } as LiveFeedMessage;
};
const snapshot = (live = true): LiveFeedMessage =>
  ({ type: "snapshot", calls: live ? [{ ...call(), events: [...history] }] : [] });

async function openApp(page: Page) {
  await mockVoice(page);
  await mockApi(page, {
    businesses: [BAKERY],
    conversations: { b1: [convo("phone-1", "Sunday hours", [greet, "Are you open on Sunday?", "Yes, from 8 to 2."])] },
  });
  const feed = await mockFeed(page);
  await page.goto("/web/");
  await expect(page.getByTestId("biz-card")).toHaveCount(1);
  return feed;
}

const status = (page: Page) => page.getByTestId("call-status");
const phoneScreen = (page: Page) => page.locator("[data-screen=phone-call]");

test("a phone call shows live on the call screen, then its summary", async ({ page }) => {
  const feed = await openApp(page);
  feed.send([
    snapshot(false),
    { type: "call_started", call: call() },
    ev({ type: "reply", text: greet }),
    ev({ type: "playback", active: true }),
  ]);
  await expect(phoneScreen(page)).toBeVisible();
  await expect(page.locator(".live-pill")).toContainText("Phone");
  await expect(page.getByTestId("call-timer")).toHaveText(/^0:0[5-9]$/); // the call's own clock
  await expect(page.locator(".biz-chip")).toContainText("Corner Bakery");
  await expect(page.getByTestId("phone-caller")).toHaveText("+1 555 000 11 11");
  await expect(page.getByText(greet)).toBeVisible();
  await expect(status(page)).toHaveText("Assistant is speaking");
  await expect(page.getByRole("button", { name: "Push to talk — hold to speak" })).toHaveCount(0);

  // reconnect: the snapshot carries the whole call so far, then the turn goes on
  ev({ type: "playback", active: false }); // happened while the page was reconnecting
  feed.send([snapshot(), ev({ type: "transcript", text: "Are you open on Sunday?" })]);
  await expect(page.locator("[data-role=user]")).toContainText("Are you open on Sunday?");
  await expect(page.locator("[data-role=user]")).toContainText("Caller ·");
  await expect(status(page)).toHaveText("Thinking…");

  feed.send([
    snapshot(),
    ev({ type: "reply", text: "Yes, from 8 to 2." }),
    ev({ type: "latency", tts_first_audio_ms: 1200 }),
    ev({ type: "playback", active: true }),
    ev({ type: "playback", active: false }),
  ]);
  await expect(page.getByText("Yes, from 8 to 2.")).toBeVisible();
  await expect(status(page)).toHaveText("The caller’s turn");

  // hang-up on the phone side
  feed.send([snapshot(), { type: "call_ended", call_id: "MZ1", t: Date.now() }]);
  await expect(page.locator("[data-screen=ended]")).toBeVisible();
  await expect(page.getByTestId("ended-messages")).toHaveText("3");
  await page.getByRole("button", { name: "Read this conversation" }).click();
  await expect(page.getByText("Yes, from 8 to 2.")).toBeVisible();
});

test("Close stops watching; the pill brings the call back; a call that ended meanwhile ends", async ({ page }) => {
  const feed = await openApp(page);
  feed.send([snapshot(false), { type: "call_started", call: call() }, ev({ type: "reply", text: greet })]);
  await expect(phoneScreen(page)).toBeVisible();

  await page.getByRole("button", { name: "Close" }).click();
  await expect(page.getByTestId("biz-card")).toHaveCount(1);
  const pill = page.getByTestId("phone-live");
  await expect(pill).toContainText("Phone call");
  await expect(pill).toContainText("Corner Bakery");
  await expect(pill).toContainText("+1 555 000 11 11");

  await pill.click();
  await expect(phoneScreen(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(pill).toBeVisible();

  // the feed reconnects and the call is gone: it ended while we weren't connected
  feed.send([snapshot(false)]);
  await expect(pill).toHaveCount(0);
});

test("a phone call doesn't take over the user's own call", async ({ page }) => {
  const feed = await openApp(page);
  await page.getByTestId("biz-card").locator(".biz-pick").click();
  await page.locator(".call-btn").click();
  await expect(page.locator("[data-screen=call]")).toBeVisible();

  feed.send([snapshot(false), { type: "call_started", call: call() }]);
  await page.waitForTimeout(300);
  await expect(page.locator("[data-screen=call]")).toBeVisible();
  await expect(page.getByTestId("phone-live")).toHaveCount(0);

  await page.keyboard.press("Escape"); // hang up ours -> the phone call is shown
  await expect(phoneScreen(page)).toBeVisible();
});
