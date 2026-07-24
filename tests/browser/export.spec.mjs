import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

import { expect, test } from "@playwright/test";


test("standalone file is semantic, keyboard navigable, and offline", async ({
  page,
}, testInfo) => {
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
  await expect(page.locator("#artifact-identity code")).toHaveCount(5);

  const skipLink = page.getByRole("link", { name: "Skip to learning content" });
  await page.keyboard.press("Tab");
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#content")).toBeFocused();

  expect(externalRequests).toEqual([]);
});
