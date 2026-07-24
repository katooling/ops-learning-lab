import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

import { expect, test } from "@playwright/test";


test("standalone file is semantic, keyboard navigable, and offline", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 320, height: 720 });
  const output = testInfo.outputPath("standalone-fixture");
  const artifact = execFileSync(
    "python3",
    ["export_fixture.py", output],
    {
      cwd: testInfo.config.testDir,
      encoding: "utf8",
      env: {
        ...process.env,
        PYTHONPATH: "../../src:../..",
      },
    },
  ).trim();
  const externalRequests = [];
  page.on("request", (request) => {
    if (/^https?:/.test(request.url())) {
      externalRequests.push(request.url());
    }
  });

  await page.goto(pathToFileURL(artifact).href);

  await expect(
    page.getByRole("heading", { level: 1, name: "Synthetic ETL evidence" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Reviewed artifact identity" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Concepts" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Accepted facts" }),
  ).toBeVisible();
  await expect(page.locator("header")).toHaveCount(1);
  await expect(page.locator("main")).toHaveCount(1);
  await expect(page.locator("#artifact-identity code")).toHaveCount(7);
  const records = page.locator(".records > li");
  await expect(records).toHaveCount(3);
  await expect(records.nth(0)).toContainText("event-001");
  await expect(records.nth(1)).toContainText("event-001");
  await expect(records.nth(2)).toContainText("event-002");
  await expect(
    page.getByRole("heading", { name: "Deterministic result" }),
  ).toBeVisible();
  await expect(page.getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByText("7 cents", { exact: true })).toBeVisible();
  await expect(page.getByText("5 cents", { exact: true })).toBeVisible();

  const skipLink = page.getByRole("link", { name: "Skip to learning content" });
  await page.keyboard.press("Tab");
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#content")).toBeFocused();

  expect(externalRequests).toEqual([]);
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});
