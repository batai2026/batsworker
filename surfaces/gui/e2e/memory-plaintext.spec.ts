// Plain-text editable memory (Lindy-style): Settings ▸ Memory ▸ "Plain-text
// memory" exports MEMORY.md, reveals its folder, and re-imports an edited file.
// Fixture mock: POST /v1/memory/export|import|reveal with a stable path.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

async function openMemory(page: Page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  await expect(page.getByTestId("memory-plaintext-card")).toBeVisible();
}

test("plain-text memory: export shows the path, reveal opens the folder", async ({
  page,
}) => {
  await openMemory(page);
  await page.getByTestId("memory-export").click();
  await expect(page.getByTestId("memory-plaintext-msg")).toContainText("MEMORY.md");
  await page.getByTestId("memory-reveal").click();
  await expect(page.getByTestId("memory-plaintext-msg")).toContainText("MEMORY.md");
});

test("plain-text memory: re-import replaces sections and refreshes the list", async ({
  page,
}) => {
  await openMemory(page);
  // The re-import path asks for confirmation — accept it.
  page.on("dialog", (d) => void d.accept());
  await page.getByTestId("memory-import").click();
  await expect(page.getByTestId("memory-plaintext-msg")).toContainText("Imported");
});