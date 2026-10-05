// /web/call.html — Twilio Voice SDK call page (mode B). A real call needs a Twilio account,
// so only the parts before the SDK dials are tested: the business picker and how a refused
// token (not localhost / Twilio not configured) is reported.

import { expect, test } from "@playwright/test";
import { biz, mockApi } from "./mocks";

test.beforeEach(async ({ page }) => {
  await mockApi(page, { businesses: [biz("b-1", "Bathhouse", null, 0, "abathhouse.com"), biz("b-2", "Zebra Spa", null)] });
});

test("page structure and business picker", async ({ page }) => {
  await page.goto("/web/call.html");
  await expect(page).toHaveTitle("Twilio Call");
  await expect(page.locator("#business option")).toHaveText(["Bathhouse (abathhouse.com)", "Zebra Spa"]);
  await expect(page.locator("#business")).toHaveValue("b-1");
  await expect(page.locator(".hint")).toContainText("Mode B");
  await expect(page.locator("#talk")).toHaveText("Call");
});

test("a refused token is shown and the page stays ready", async ({ page }) => {
  await page.route("**/twilio/token", (route) =>
    route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Twilio not configured: set TWILIO_API_KEY_SECRET in .env" }) }),
  );
  await page.goto("/web/call.html");
  await page.selectOption("#business", "b-2");
  await page.locator("#talk").click();
  await expect(page.locator("#log .line").last()).toHaveText("Error: Twilio not configured: set TWILIO_API_KEY_SECRET in .env");
  await expect(page.locator("#talk")).toHaveText("Call");
  await expect(page.locator("#business")).toBeEnabled();
});
