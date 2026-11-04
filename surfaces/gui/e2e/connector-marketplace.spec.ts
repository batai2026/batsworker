// Connector/plugin marketplace (Claude-Marketplace-style): the Connectors page's
// "⋮ Marketplace" modal offers curated MCP servers (Add → appears in Custom · MCP)
// and community plugins (Install/Uninstall → installed badge flips).
// Fixture mock: GET /v1/connectors/marketplace + plugin install/uninstall.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

async function openConnectors(page: Page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
  await expect(page.getByTestId("add-custom-server")).toBeVisible();
}

test("marketplace: add a curated MCP server and install/uninstall a plugin", async ({
  page,
}) => {
  await openConnectors(page);
  await page.getByTestId("open-marketplace").click();
  await expect(page.getByTestId("marketplace-modal")).toBeVisible();

  // MCP tab: adding Granola lands it in Custom · MCP (via the standard /v1/mcp POST).
  await expect(page.getByTestId("marketplace-add-granola")).toBeVisible();
  await page.getByTestId("marketplace-add-granola").click();
  await expect(page.getByText("Added")).toBeVisible();

  // Plugins tab: install flips to Installed; uninstall restores.
  await page.getByRole("button", { name: "Community plugins", exact: true }).click();
  await expect(page.getByTestId("marketplace-install-intercom")).toBeVisible();
  await page.getByTestId("marketplace-install-intercom").click();
  await expect(page.getByText("Installed")).toBeVisible();
  await page.getByTestId("marketplace-uninstall-intercom").click();
  await expect(page.getByTestId("marketplace-install-intercom")).toBeVisible();
});

test("marketplace: empty/error states surface honestly", async ({ page }) => {
  await openConnectors(page);
  await page.getByTestId("open-marketplace").click();
  await expect(page.getByTestId("marketplace-modal")).toBeVisible();
  const modal = page.getByTestId("marketplace-modal");
  // The curated MCP list renders both servers.
  await expect(modal.getByText("Granola", { exact: true })).toBeVisible();
  await expect(modal.getByText("Linear", { exact: true })).toBeVisible();
  // Switching to plugins shows the community cards.
  await page.getByRole("button", { name: "Community plugins", exact: true }).click();
  await expect(page.getByText("Intercom", { exact: true })).toBeVisible();
  await expect(page.getByText("Webflow", { exact: true })).toBeVisible();
});