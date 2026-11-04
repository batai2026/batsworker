// Demo → skill (Watch-and-Learn style): Settings ▸ Skills ▸ "Record a demo"
// turns pasted steps into a staged SKILL.md the user reviews before installing.
// Fixture mock: POST /v1/skills/demo stages, /v1/skills/upload/confirm installs
// with source=demo.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

async function openSkills(page: Page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add skill" })).toBeVisible();
}

test("record a demo: paste steps → generate → review → install", async ({ page }) => {
  await openSkills(page);

  // Open the Add skill menu and choose "Record a demo".
  await page.getByRole("button", { name: "Add skill" }).click();
  await page.getByRole("menuitem", { name: /Record a demo/ }).click();

  // Fill the demo form.
  await page.getByLabel("Name").fill("weekly-report");
  await page.getByLabel("Description").fill("Write the Monday status report");
  await page
    .getByLabel("Steps — one per line")
    .fill("Gather last week's updates from Notion\nWrite the report, under 300 words");
  await page.getByRole("button", { name: "Generate skill" }).click();

  // The staged skill appears on the review card; confirm installs it.
  await expect(page.getByText("Review before installing")).toBeVisible();
  await expect(page.getByText("weekly-report").first()).toBeVisible();
  await page.getByRole("button", { name: "Install skill" }).click();

  // The skill now shows in the list with the "demo" provenance badge.
  await expect(page.getByText("weekly-report").first()).toBeVisible();
  await expect(page.getByText("demo")).toBeVisible();
});

test("record a demo: generate is gated until name, description, and steps exist", async ({
  page,
}) => {
  await openSkills(page);
  await page.getByRole("button", { name: "Add skill" }).click();
  await page.getByRole("menuitem", { name: /Record a demo/ }).click();

  const generate = page.getByRole("button", { name: "Generate skill" });
  await expect(generate).toBeDisabled();

  await page.getByLabel("Name").fill("triage");
  await page.getByLabel("Description").fill("Triage a checkout alert");
  await expect(generate).toBeDisabled(); // steps still empty

  await page.getByLabel("Steps — one per line").fill("Inspect recent deploys\nDraft the timeline");
  await expect(generate).toBeEnabled();
});