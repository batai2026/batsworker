// i18n anti-rot tests: the zh dictionary must actually drive the rendered UI,
// and the Settings switcher must flip it live. A dict key that drifts from its
// source string falls back to English silently — these specs catch that class
// of regression on the app chrome (the Python checker covers the full key set).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test.describe("i18n", () => {
  test("zh-CN renders the chrome translated, with no English fallback", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("ow.lang", "zh-CN"));
    await page.goto("/");
    await expect(page.getByText("新会话")).toBeVisible(); // sidebar "New session"
    await expect(page.getByText("最近", { exact: true })).toBeVisible(); // "Recent" header
    await expect(page.getByText("New session")).toHaveCount(0);
    await expect(page.getByText("Waiting for agent...")).toHaveCount(0);
  });

  test("the Settings language switcher flips the UI without a reload", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("account-row").click();
    await page.getByTestId("account-menu").getByText("Settings", { exact: true }).click();
    await expect(page.getByText("Language", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "中文（简体）" }).click();
    await expect(page.getByRole("heading", { name: "通用" })).toBeVisible(); // "General" panel head
    await expect(page.getByRole("button", { name: "通用" })).toBeVisible(); // the sub-nav tab
    await expect(page.getByText("界面语言", { exact: true })).toBeVisible(); // "Language" card label
    expect(await page.evaluate(() => localStorage.getItem("ow.lang"))).toBe("zh-CN");
  });
});
