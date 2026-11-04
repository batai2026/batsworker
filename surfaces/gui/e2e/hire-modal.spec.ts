// Role-based "hire" landing pages (Lindy-style): Settings ▸ Coworkers ▸
// "Hire by role" opens a role directory; expanding a card shows what it does /
// checks in before / never / works with, and "Meet the coworker →" deep-links
// to that persona's detail page.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

async function openPersonas(page: Page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Coworkers", exact: true }).click();
  await expect(page.getByTestId("install-disclosure")).toBeVisible();
}

test("hire by role: browse roles, expand a card, and meet the coworker", async ({
  page,
}) => {
  await openPersonas(page);
  await page.getByTestId("open-hire").click();
  await expect(page.getByTestId("hire-modal")).toBeVisible();

  // Expand the Security Reviewer card — the landing copy is visible.
  await page.getByTestId("hire-role-security").click();
  const detail = page.getByTestId("hire-detail-security");
  await expect(detail).toContainText("What it does");
  await expect(detail).toContainText("Checks in");
  await expect(detail).toContainText("Never");
  await expect(detail).toContainText("Works with");

  // "Meet the coworker →" opens the Security Coworker persona detail page.
  await page.getByTestId("hire-open-security").click();
  await expect(page.getByText("Security Coworker")).toBeVisible();
  await expect(page.getByText("Find and fix security issues — scan, triage, PR")).toBeVisible();
});