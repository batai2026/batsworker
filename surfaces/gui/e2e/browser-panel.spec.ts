// Built-in browser panel (right rail): a live view into the Playwright session
// the worker drives. The mock starts with a closed browser; clicking "Screenshot"
// flips it open and renders the screenshot + URL + title in the panel.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("browser panel: opens a live view and takes a screenshot", async ({ page }) => {
  await page.goto("/");
  // Open a live session (the fake agent answers any message).
  await page.getByPlaceholder(/Ask the coworker/).fill("open the alerts page");
  await page.getByRole("button", { name: "Send" }).click();

  // The rail renders the Browser section; it starts closed.
  await page.getByTestId("rail-toggle-browser").click();
  await expect(page.getByTestId("browser-closed")).toBeVisible();

  // Take a screenshot → the mock flips the browser open with a data-URL shot.
  await page.getByTestId("browser-shot-btn").click();
  await expect(page.getByTestId("browser-panel")).toBeVisible();
  await expect(page.getByTestId("browser-screenshot")).toBeVisible();
  await expect(page.getByText("https://example.com/alerts")).toBeVisible();
  await expect(page.getByText("Example Alerts")).toBeVisible();

  // Close it → back to the closed state.
  await page.getByTestId("browser-close-btn").click();
  await expect(page.getByTestId("browser-closed")).toBeVisible();
});