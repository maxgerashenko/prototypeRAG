// /web/voice-debug.html — the raw mode-C voice debug page: always transmitting (server VAD
// decides turns), every server event printed as a log line.

import { expect, test, type Page } from "@playwright/test";
import { hasSound, mockVoice, type VoiceServer } from "./mocks";

let voice: VoiceServer;
test.beforeEach(async ({ page }) => {
  voice = await mockVoice(page, { accept: false });
  await page.goto("/web/voice-debug.html");
});

const talk = (page: Page) => page.locator("#talk");
const lines = (page: Page) => page.locator("#log .line");

test("page structure", async ({ page }) => {
  await expect(page).toHaveTitle("Voice Debug");
  await expect(page.getByLabel("Business ID (UUID):")).toHaveAttribute("placeholder", "Paste UUID here");
  await expect(page.locator(".hint")).toContainText("Mode C: mic → WebSocket /voice/browser (PCM16, 16 kHz)");
  await expect(talk(page)).toHaveText("Talk");
  await expect(page.locator("#status")).toHaveText("");
});

test("Talk without a business id does nothing", async ({ page }) => {
  await talk(page).click();
  await page.waitForTimeout(300);
  await expect(talk(page)).toHaveText("Talk");
  expect(voice.url()).toBe("");
});

test("a full call: every event is logged, audio streams continuously", async ({ page }) => {
  await page.fill("#business_id", "  abcd-1234  ");
  await talk(page).click();
  const ws = await voice.connected;
  expect(voice.url()).toMatch(/\/voice\/browser\?business_id=abcd-1234$/);
  await expect(talk(page)).toHaveText("Hang up");
  await expect(talk(page)).toHaveClass("live");
  await expect(page.locator("#status")).toHaveText("listening…");

  for (const ev of [
    { type: "conversation_id", id: "c-1" },
    { type: "transcript", text: "hello <b>there</b>" },
    { type: "reply", text: "Hi! How can I help?" },
    { type: "latency", stt_ms: 120, llm_first_ms: 300.5 },
    { type: "clear" },
    { type: "error", message: "boom" },
    { type: "unknown_event" },
  ]) ws.send(JSON.stringify(ev));
  voice.sendAudio(1600);
  await expect(lines(page)).toHaveText([
    "conversation c-1",
    "hello <b>there</b>",
    "Hi! How can I help?",
    "stt_ms=120  llm_first_ms=300.5",
    "[barge-in: playback cleared]",
    "Error: boom",
  ]);
  await expect(lines(page)).toHaveClass(["line meta", "line user", "line assistant", "line meta", "line meta", "line meta"]);

  await page.waitForTimeout(2500);
  expect(voice.frames.length).toBeGreaterThan(50);
  expect(voice.frames.every((f) => f.byteLength === 640)).toBe(true); // 20 ms PCM16 at 16 kHz
  expect(voice.frames.some(hasSound)).toBe(true); // mic audio, not the app's hold-to-talk silence

  await voice.close(4404, "unknown business");
  await expect(lines(page).last()).toHaveText("[closed 4404 unknown business]");
  await expect(talk(page)).toHaveText("Talk");
  await expect(talk(page)).not.toHaveClass("live");
  await expect(page.locator("#status")).toHaveText("");
});

test("hang up closes the socket and logs it", async ({ page }) => {
  await page.fill("#business_id", "abcd");
  await talk(page).click();
  await voice.connected;
  await expect(talk(page)).toHaveText("Hang up");
  await talk(page).click();
  await expect(talk(page)).toHaveText("Talk");
  await expect(lines(page).last()).toHaveText(/^\[closed \d+\]$/);
  await expect.poll(voice.closedByClient).toBe(true);
});

test("microphone denied: error line, back to Talk", async ({ page }) => {
  await page.evaluate(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
  });
  await page.fill("#business_id", "abcd");
  await talk(page).click();
  await expect(lines(page)).toHaveText(["Error: Permission denied"]);
  await expect(talk(page)).toHaveText("Talk");
  expect(voice.url()).toBe("");
});

test("clicking again while the mic is starting cancels instead of starting a second call", async ({ page }) => {
  await page.evaluate(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = (c) => new Promise((r) => setTimeout(r, 500)).then(() => original(c));
  });
  await page.fill("#business_id", "abcd");
  await talk(page).click();
  await expect(talk(page)).toHaveText("Talk"); // like the legacy page: "Hang up" only once mic + socket are up
  await talk(page).click();
  await page.waitForTimeout(1200);
  await expect(talk(page)).toHaveText("Talk");
  expect(voice.url()).toBe("");
});
