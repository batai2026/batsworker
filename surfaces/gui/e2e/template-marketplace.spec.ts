// Automation template marketplace (Cursor-Automations-style): the backend-served
// catalog renders as a searchable card grid on the Automations page; picking a
// template expands a configure pane and "Create automation" instantiates it in
// one step. Fixture mock: GET /v1/automations/templates + POST .../instantiate.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

async function openAutomations(page: Page) {
  await page.goto("/");
  await page.getByTestId("nav-automations").click();
  await expect(page.getByText("Recurring tasks OpenWorker runs on a schedule.")).toBeVisible();
}

test("template marketplace: search, configure a channel template, and create", async ({
  page,
}) => {
  await openAutomations(page);

  // The marketplace card is always rendered above the quickstart.
  await expect(page.getByTestId("template-marketplace")).toBeVisible();
  await expect(page.getByTestId("marketplace-template-github")).toBeVisible();

  // Search narrows the grid.
  await page.getByTestId("tmpl-search").fill("news");
  await expect(page.getByTestId("marketplace-template-news")).toBeVisible();
  await expect(page.getByTestId("marketplace-template-github")).toHaveCount(0);
  await page.getByTestId("tmpl-search").fill("");

  // GitHub digest needs a repo + Slack channel; create is gated until both are set.
  await page.getByTestId("marketplace-template-github").click();
  const cfg = page.getByTestId("tmpl-configure");
  await expect(cfg).toBeVisible();
  await expect(page.getByTestId("tmpl-create")).toBeDisabled();
  await page.getByTestId("tmpl-repo").fill("acme/worker");
  await page.getByTestId("tmpl-channel").fill("#dev");
  await expect(page.getByTestId("tmpl-create")).toBeEnabled();

  // Create → the server instantiates and the Automations page opens the task detail.
  await page.getByTestId("tmpl-create").click();
  // The overview (card grid + configure pane) unmounts once the detail opens; wait
  // for that so the title lookup can't still resolve to the two overview headings.
  await expect(page.getByTestId("tmpl-configure")).toHaveCount(0);
  // The detail renders in its own <main>; scope to it so the sidebar's
  // "GitHub digest" row doesn't make the text lookup ambiguous. Target the
  // heading — the instructions body also starts with the template title.
  const detail = page.getByRole("main").last();
  await expect(detail.getByRole("heading", { name: "GitHub digest" })).toBeVisible();
  await expect(detail.getByText(/acme\/worker/)).toBeVisible();
  await expect(detail.getByText("#dev")).toBeVisible();
});

test("template marketplace: channel templates refuse to create without a channel", async ({
  page,
}) => {
  await openAutomations(page);
  await page.getByTestId("marketplace-template-github").click();
  await expect(page.getByTestId("tmpl-create")).toBeDisabled();

  // A no-connector template (news) has no required fields.
  await page.getByTestId("marketplace-template-news").click();
  await expect(page.getByTestId("tmpl-create")).toBeEnabled();
  await page.getByTestId("tmpl-create").click();
  // Overview unmounts on detail open; wait so the title lookup is unambiguous.
  await expect(page.getByTestId("tmpl-configure")).toHaveCount(0);
  await expect(
    page.getByRole("main").last().getByRole("heading", { name: "Morning news briefing" }),
  ).toBeVisible();
});