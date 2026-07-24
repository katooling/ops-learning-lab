import { expect, test } from "@playwright/test";

async function tabTo(page, locator, limit = 80) {
  for (let attempt = 0; attempt < limit; attempt += 1) {
    if (await locator.evaluate((element) => element === document.activeElement)) {
      return;
    }
    await page.keyboard.press("Tab");
  }
  throw new Error(`Keyboard focus did not reach ${await locator.getAttribute("id")}`);
}

test("keyboard Promotion completes without horizontal overflow at 320px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto("/");

  const updateLink = page.getByRole("link", { name: /^update-/ });
  await tabTo(page, updateLink);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Review staged content" })).toBeVisible();

  const accept = page.getByRole("radio", {
    name: "Accept sanitized content",
  }).first();
  const reject = page.getByRole("radio", { name: "Reject" }).nth(1);
  await expect(accept).not.toBeChecked();
  await expect(reject).not.toBeChecked();
  await expect(page.getByRole("checkbox")).toHaveCount(2);
  await expect(page.getByRole("checkbox").first()).not.toBeChecked();
  await expect(page.getByRole("checkbox").nth(1)).not.toBeChecked();

  const previewButton = page.getByRole("button", {
    name: "Preview without writing",
  });
  await tabTo(page, previewButton);
  await page.keyboard.press("Enter");
  await expect(page.locator("#target-pack-id")).toBeFocused();
  await expect(page).toHaveURL(/\/updates\/update-[0-9a-f]{20}$/);

  const packId = page.getByLabel("Pack ID");
  await page.keyboard.type("codex-etl");
  await tabTo(page, page.getByLabel("Pack title"));
  await page.keyboard.type("Synthetic Codex ETL");

  await tabTo(page, accept);
  await page.keyboard.press("Space");
  await tabTo(page, page.getByLabel("Independently written accepted text").first());
  await page.keyboard.type("Reviewed normalized synthetic cost is non-negative.");
  await tabTo(page, page.getByLabel("Accepted fact status").first());
  await page.keyboard.press("c");
  await expect(page.getByLabel("Accepted fact status").first()).toHaveValue("current");
  await tabTo(page, page.getByLabel("History decision").first());
  await page.keyboard.press("a");
  await expect(page.getByLabel("History decision").first()).toHaveValue("add");
  await tabTo(page, page.getByLabel("I removed private-only details").first());
  await page.keyboard.press("Space");

  const secondAccept = page.getByRole("radio", {
    name: "Accept sanitized content",
  }).nth(1);
  await tabTo(page, secondAccept);
  await page.keyboard.press("ArrowRight");
  await expect(reject).toBeFocused();
  await expect(reject).toBeChecked();
  await tabTo(
    page,
    page.getByLabel("Structured rejection reason (required when rejecting)").nth(1),
  );
  await page.keyboard.press("n");
  await page.keyboard.press("n");
  await expect(
    page.getByLabel("Structured rejection reason (required when rejecting)").nth(1),
  ).toHaveValue("unsupported");

  await tabTo(page, previewButton);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Preview Promotion" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Deterministic change summary" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Removed" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Retained" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Generalized" })).toBeVisible();

  const confirmation = page.getByRole("checkbox", {
    name: "Promote exactly this reviewed content",
  });
  await expect(confirmation).not.toBeChecked();
  await tabTo(page, confirmation);
  await page.keyboard.press("Space");
  const promoteButton = page.getByRole("button", { name: "Promote atomically" });
  await tabTo(page, promoteButton);
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL("/packs/codex-etl");
  await expect(
    page.getByRole("heading", { name: "Synthetic Codex ETL" }),
  ).toBeVisible();
  await expect(page.getByText("Promotion history")).toBeVisible();
  await expect(page.getByText("browser-private-canary-6f103")).toHaveCount(0);

  const noOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  );
  expect(noOverflow).toBe(true);
});
