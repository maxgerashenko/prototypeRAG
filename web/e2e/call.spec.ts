// /web/call.html — Twilio Voice SDK call page (mode B). A real call needs a Twilio account,
// so these tests stop before the SDK reaches Twilio: business picker and token errors.

import { expect, test } from "@playwright/test";
import { biz } from "./mocks";

test("lists businesses and reports a missing Twilio setup", async ({ page }) => {
  await page.route("**/businesses", (route) =>
    route.fulfill({ json: [biz("b1", "Zebra Spa", null), biz("b2", "Bathhouse", "https://abathhouse.com")] }),
  );
  let tokenRequests = 0;
  await page.route("**/twilio/token", (route) => {
    tokenRequests++;
    return route.fulfill({ status: 503, json: { detail: "Twilio not configured: set TWILIO_ACCOUNT_SID" } });
  });
  await page.goto("/web/call.html");

  await expect(page.locator("#business option")).toHaveText(["Zebra Spa", "Bathhouse"]);
  await expect(page.locator("#mute")).toBeDisabled();
  await expect(page.locator("#hangup")).toBeDisabled();

  await page.selectOption("#business", "b2");
  await page.click("#call");
  await expect(page.locator("#log .error")).toContainText("Twilio not configured: set TWILIO_ACCOUNT_SID");
  await expect(page.locator("#status")).toHaveText("idle");
  await expect(page.locator("#call")).toBeEnabled();
  expect(tokenRequests).toBe(1);
});

test("shows an error when businesses can't load", async ({ page }) => {
  await page.route("**/businesses", (route) => route.fulfill({ status: 500, body: "x" }));
  await page.goto("/web/call.html");
  await expect(page.locator("#log .error")).toContainText("could not load businesses");
  await expect(page.locator("#call")).toBeDisabled();
});
