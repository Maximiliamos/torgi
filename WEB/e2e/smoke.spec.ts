import { expect, test } from "@playwright/test";

test("list search filter detail and API failure smoke", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("main")).toBeVisible();
  const search = page.locator('input[placeholder*="Название"]');
  await search.fill("земля");
  await page.waitForTimeout(350);
  const firstRow = page.locator(".lotRow").first();
  if (await firstRow.count()) {
    await firstRow.click();
    await expect(page.locator(".detailPanel")).toBeVisible();
    await page.locator(".detailPanel .iconButton").click();
  }
  await page.route("**/api/lots**", route => route.abort());
  await page.getByRole("button", { name: /Обновить/i }).click();
  await expect(page.locator(".errorBox")).toBeVisible();
});
