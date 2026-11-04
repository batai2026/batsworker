// Security workflow tab (Claude-Security-style): the editable threat model and
// the finding closure loop are exposed in Settings ▸ Security. The mock keeps a
// mutable threat-model string and findings list so edit/save and
// log → status → delete round-trip through the real UI.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

async function openSecurityPanel(page: Page) {
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Security", exact: true }).click();
  await expect(page.getByTestId("threat-model-card")).toBeVisible();
}

async function openSecurity(page: Page) {
  await page.goto("/");
  await openSecurityPanel(page);
}

test("threat model: edit, save, and the change survives a reload", async ({ page }) => {
  await openSecurity(page);
  const input = page.getByTestId("threat-model-input");
  await expect(input).toContainText("In scope");
  await input.fill("# My Model\n\n## In scope\n- Only payment paths.\n");
  await page.getByTestId("threat-model-save").click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await page.reload();
  await openSecurityPanel(page);
  await expect(page.getByTestId("threat-model-input")).toHaveValue(
    "# My Model\n\n## In scope\n- Only payment paths.\n",
  );
});

test("finding closure loop: log → status → delete", async ({ page }) => {
  await openSecurity(page);
  await expect(page.getByTestId("findings-empty")).toBeVisible();

  // Log a finding.
  await page.getByTestId("finding-title").fill("SQL injection in reports module");
  await page.getByTestId("finding-severity").selectOption("high");
  await page.getByTestId("finding-add").click();
  const row = page.getByTestId("finding-F-1");
  await expect(row).toBeVisible();
  await expect(row).toContainText("SQL injection in reports module");
  await expect(row).toContainText("high");

  // Move it through the loop to closed.
  await page.getByTestId("finding-status-F-1-closed").click();
  await expect(page.getByTestId("finding-status-F-1-closed")).toHaveClass(/accent/);

  // Delete it.
  await page.getByTestId("finding-delete-F-1").click();
  await expect(page.getByTestId("finding-F-1")).toHaveCount(0);
  await expect(page.getByTestId("findings-empty")).toBeVisible();
});

test("sandbox: toggle, save, and the choice survives a reload", async ({ page }) => {
  await openSecurity(page);
  const card = page.getByTestId("sandbox-card");
  await expect(card).toBeVisible();

  // Enable the sandbox and customize the image.
  await page.getByTestId("sandbox-enabled").check();
  await page.getByTestId("sandbox-image").fill("python:3.13-slim");
  await page.getByTestId("sandbox-save").click();
  await expect(page.getByText("Saved.")).toBeVisible();

  // The mock persists the settings → a reload keeps the toggle on.
  await page.reload();
  await openSecurityPanel(page);
  await expect(page.getByTestId("sandbox-enabled")).toBeChecked();
  await expect(page.getByTestId("sandbox-image")).toHaveValue("python:3.13-slim");
});

test("egress policy: add, list, and remove an approved domain", async ({ page }) => {
  await openSecurity(page);
  const card = page.getByTestId("egress-card");
  await expect(card).toBeVisible();
  await expect(page.getByTestId("egress-empty")).toBeVisible();

  // Add a domain with a reason.
  await page.getByTestId("egress-domain").fill("api.acme.com");
  await page.getByTestId("egress-reason").fill("needed for the drill");
  await page.getByTestId("egress-add").click();
  const row = page.getByTestId("egress-api.acme.com");
  await expect(row).toBeVisible();
  await expect(row).toContainText("api.acme.com");
  await expect(row).toContainText("needed for the drill");

  // Remove it → the empty state returns.
  await page.getByTestId("egress-remove-api.acme.com").click();
  await expect(page.getByTestId("egress-empty")).toBeVisible();
});