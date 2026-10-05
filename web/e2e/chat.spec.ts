// /web/chat.html — the RAG chat test page (POST /chat, sources under each answer).
// Same behaviour as the pre-React page; checked side by side when it was migrated (DEC-41).

import { expect, test, type Page, type Route } from "@playwright/test";

interface Step {
  answer?: string;
  conv?: string;
  sources?: unknown[];
  noSources?: boolean;
  status?: number;
  abort?: boolean;
  delay?: number;
}

const SOURCES = [
  { chunk_id: "a", section_heading: "Hours", score: 0.81234, source: "page" },
  { chunk_id: "b", section_heading: null, score: 0.5, source: "custom_reply" },
  { chunk_id: "c", section_heading: "", score: 1, source: "page" },
];

async function setup(page: Page, steps: Step[] = []) {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/chat", async (route: Route) => {
    bodies.push(JSON.parse(route.request().postData() ?? "{}"));
    expect(route.request().headers()["content-type"]).toBe("application/json");
    const step = steps.shift() ?? { answer: "default" };
    await new Promise((r) => setTimeout(r, step.delay ?? 50));
    if (step.abort) return route.abort();
    if (step.status) return route.fulfill({ status: step.status, body: "x" });
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        answer: step.answer, conversation_id: step.conv ?? "conv-1",
        ...(step.noSources ? {} : { sources: step.sources ?? [] }),
      }),
    });
  });
  await page.goto("/web/chat.html");
  return bodies;
}

const messages = (page: Page) => page.locator("#messages > div");
const ask = async (page: Page, q: string, via: "enter" | "click" = "enter") => {
  await page.fill("#question", q);
  if (via === "enter") await page.press("#question", "Enter");
  else await page.click("#send");
};

test("page structure", async ({ page }) => {
  await setup(page);
  await expect(page).toHaveTitle("RAG Chat Test");
  await expect(page.getByLabel("Business ID (UUID):")).toHaveAttribute("placeholder", "Paste UUID here");
  await expect(page.locator("#question")).toHaveAttribute("placeholder", "Ask a question...");
  await expect(page.locator("#question")).toHaveAttribute("autocomplete", "off");
  await expect(page.locator("#send")).toHaveText("Send");
  await expect(messages(page)).toHaveCount(0);
});

test("nothing is sent without a business id or question, or on Shift+Enter", async ({ page }) => {
  const bodies = await setup(page);
  await ask(page, "hi");
  await page.click("#send");
  await page.fill("#business_id", "b1");
  await ask(page, "   ");
  await page.fill("#question", "hi");
  await page.press("#question", "Shift+Enter");
  await page.waitForTimeout(200);
  expect(bodies).toHaveLength(0);
  await expect(messages(page)).toHaveCount(0);
});

test("Enter sends trimmed input; loading state; sources list", async ({ page }) => {
  const bodies = await setup(page, [{ answer: "Open <b>10am</b>.", sources: SOURCES, delay: 400 }]);
  await page.fill("#business_id", "  b1  ");
  await ask(page, "  When open?  ");
  await expect(page.locator("#send")).toHaveText("Thinking...");
  await expect(page.locator("#send")).toBeDisabled();
  await expect(page.locator("#question")).toHaveValue("");
  await expect(messages(page).first()).toHaveClass("message user");
  await expect(messages(page).first()).toHaveText("When open?");

  const answer = page.locator(".message.assistant");
  await expect(answer).toContainText("Open <b>10am</b>."); // shown as text, not HTML
  await expect(page.locator("#send")).toHaveText("Send");
  await expect(page.locator("#send")).toBeEnabled();
  expect(bodies).toEqual([{ business_id: "b1", question: "When open?", conversation_id: null }]);

  const details = answer.locator("details.sources");
  await expect(details.locator("summary")).toHaveText("Sources (3)");
  await expect(details).not.toHaveAttribute("open");
  await details.locator("summary").click();
  await expect(details.locator(".source-item")).toHaveText([
    "Hours | score: 0.812 | source: page",
    "(no heading) | score: 0.500 | source: custom_reply",
    "(no heading) | score: 1.000 | source: page",
  ]);
});

test("Send button; conversation id is reused, also after switching business", async ({ page }) => {
  const bodies = await setup(page, [{ answer: "one", conv: "A" }, { answer: "two", conv: "B" }, { answer: "three" }]);
  await page.fill("#business_id", "b1");
  await ask(page, "q1", "click");
  await expect(page.locator(".message.assistant")).toHaveCount(1);
  await ask(page, "q2", "click");
  await expect(page.locator(".message.assistant")).toHaveCount(2);
  await page.fill("#business_id", "b2");
  await ask(page, "q3", "click");
  await expect(page.locator(".message.assistant")).toHaveCount(3);
  expect(bodies.map((b) => [b.business_id, b.conversation_id])).toEqual([["b1", null], ["b1", "A"], ["b2", "B"]]);
});

test("no sources box when sources are missing or empty", async ({ page }) => {
  await setup(page, [{ answer: "a", noSources: true }, { answer: "b", sources: [] }]);
  await page.fill("#business_id", "b1");
  await ask(page, "x");
  await expect(page.locator(".message.assistant")).toHaveCount(1);
  await ask(page, "y");
  await expect(page.locator(".message.assistant")).toHaveCount(2);
  await expect(page.locator("details")).toHaveCount(0);
});

test("HTTP and network errors; the conversation id survives them", async ({ page }) => {
  const bodies = await setup(page, [{ answer: "ok", conv: "Z" }, { status: 500 }, { abort: true }, { answer: "ok2" }]);
  await page.fill("#business_id", "b1");
  for (let i = 1; i <= 4; i++) {
    await ask(page, `q${i}`);
    await expect(messages(page)).toHaveCount(i * 2);
  }
  await expect(page.locator(".message.error")).toHaveText(["Error: HTTP 500: Internal Server Error", "Error: Failed to fetch"]);
  expect(bodies.map((b) => b.conversation_id)).toEqual([null, "Z", "Z", "Z"]);
});

test("scrolls to the newest message", async ({ page }) => {
  await setup(page, Array.from({ length: 8 }, (_, i) => ({ answer: "answer ".repeat(30) + i, delay: 5 })));
  await page.fill("#business_id", "b1");
  for (let i = 0; i < 8; i++) {
    await ask(page, `q${i}`);
    await expect(page.locator(".message.assistant")).toHaveCount(i + 1);
  }
  await expect
    .poll(() => page.locator("#messages").evaluate((el) => el.scrollHeight - el.clientHeight - el.scrollTop))
    .toBeLessThanOrEqual(1);
});

test("Enter while an answer is loading does not send twice", async ({ page }) => {
  const bodies = await setup(page, [{ answer: "slow", delay: 600 }]);
  await page.fill("#business_id", "b1");
  await ask(page, "first");
  await ask(page, "second");
  await expect(page.locator(".message.assistant")).toHaveText("slow");
  expect(bodies.map((b) => b.question)).toEqual(["first"]);
  await expect(page.locator("#question")).toHaveValue("second");
});
