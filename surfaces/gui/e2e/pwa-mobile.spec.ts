// PWA / multi-device smoke (P2): the web build must be usable as a lightweight
// view/approval client on a phone. This spec runs the app under an iPhone 13
// viewport (PWA standalone), sends a message, and confirms the right rail (the
// mobile overlay) is reachable. Lighthouse installability audit is a manual P2
// step documented in docs/design/maturity-roadmap.md.
import { devices, expect } from "@playwright/test";
import { test } from "./fixtures";

// Only the phone emulation (viewport, UA, touch) is wanted here. Spreading the
// whole descriptor also carries `defaultBrowserType: "webkit"`, which swaps the
// harness's Chromium project for WebKit — this repo configures no WebKit project,
// and on Windows that browser fails to start (page fixture times out).
const { defaultBrowserType: _browser, ...iPhone13 } = devices["iPhone 13"];
test.use(iPhone13);

test.beforeEach(async ({ page }) => {
  // The shared fixture seeds the rail open for desktop specs. Under 900px the rail
  // becomes a fixed overlay ~94vw wide, so an open rail covers the composer and the
  // Send button can't be clicked. A phone's real default is collapsed (empty
  // localStorage) — that's the state this smoke test must exercise.
  await page.addInitScript(() => {
    localStorage.setItem("ocw-e2e-rail-default", "1");
    localStorage.removeItem("coworker:rail-hidden:v1");
  });
});

test("mobile viewport: app renders, chat round-trips, rail overlay opens", async ({
  page,
}) => {
  await page.goto("/");

  // The composer is reachable on a phone viewport.
  const composer = page.getByPlaceholder(/Ask the coworker/);
  await expect(composer).toBeVisible();

  // Send a message → the fake agent replies (transcript round-trips).
  await composer.fill("hello from my phone");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("hello from my phone").first()).toBeVisible();

  // The account row (sidebar) is still reachable for navigation. Checked before the
  // overlay opens, since the open rail would cover it.
  await page.getByTestId("account-row").click();
  await expect(page.getByTestId("account-menu")).toBeVisible();
  // Dismiss it through its full-screen backdrop (the menu has no Esc handler).
  await page.mouse.click(8, 8);

  // The right rail (approvals / artifacts / browser) opens as an overlay.
  await page.getByRole("button", { name: "Show side panel" }).click();
  await page.getByTestId("rail-toggle-browser").click();
  await expect(page.getByTestId("browser-closed")).toBeVisible();
});