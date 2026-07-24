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

async function chooseWithKeyboard(page, locator) {
  await tabTo(page, locator);
  await page.keyboard.press("Space");
  await expect(locator).toBeChecked();
}

async function completeLesson(
  page,
  {
    predictionIndex,
    evidenceVerdicts,
    resetBeforeProve = false,
    begin = true,
  },
) {
  if (begin) {
    const beginButton = page.getByRole("button", { name: "Begin lesson" });
    await tabTo(page, beginButton);
    await page.keyboard.press("Enter");
  }
  await expect(page.getByRole("heading", { name: "Map", exact: true })).toBeVisible();
  await expect(page.getByText("7 cents", { exact: true })).toHaveCount(0);

  const mapDone = page.getByRole("button", { name: "I have traced the map" });
  await tabTo(page, mapDone);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Predict", exact: true })).toBeVisible();
  await expect(page.getByText("7 cents", { exact: true })).toHaveCount(0);

  const predictionChoices = page.getByRole("group", {
    name: "Your prediction",
  }).getByRole("radio");
  await tabTo(page, predictionChoices.first());
  await page.keyboard.press("Space");
  for (let index = 0; index < predictionIndex; index += 1) {
    await page.keyboard.press("ArrowDown");
  }
  await expect(predictionChoices.nth(predictionIndex)).toBeChecked();
  const predictionConfidence = page.getByLabel("Confidence before the result");
  await tabTo(page, predictionConfidence);
  await page.keyboard.press("4");
  const lockPrediction = page.getByRole("button", { name: "Lock prediction" });
  await tabTo(page, lockPrediction);
  await page.keyboard.press("Enter");

  await expect(page.getByRole("heading", { name: "Try", exact: true })).toBeVisible();
  await expect(page.getByText("7 cents", { exact: true })).toHaveCount(0);
  const checkpointUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(checkpointUrl);
  await expect(page.getByRole("heading", { name: "Try", exact: true })).toBeVisible();
  await expect(
    page.getByText("Seed", { exact: true }).locator("xpath=following-sibling::dd[1]"),
  ).toHaveText("7");
  const run = page.getByRole("button", { name: "Run the pipeline" });
  await tabTo(page, run);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Prove", exact: true })).toBeVisible();
  await expect(page.getByText("7 cents", { exact: true })).toBeVisible();

  if (resetBeforeProve) {
    const reset = page.getByRole("button", { name: "Reset this scenario" });
    await tabTo(page, reset);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Try", exact: true })).toBeVisible();
    await tabTo(page, page.getByRole("button", { name: "Run the pipeline" }));
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Prove", exact: true })).toBeVisible();
    await expect(page.getByText("7 cents", { exact: true })).toBeVisible();
  }

  const supportChoices = page.getByRole("radio", { name: "Supports the claim" });
  const rejectChoices = page.getByRole("radio", {
    name: "Reject as insufficient or misleading",
  });
  for (let index = 0; index < evidenceVerdicts.length; index += 1) {
    await chooseWithKeyboard(page, supportChoices.nth(index));
    if (evidenceVerdicts[index] === "rejects") {
      await page.keyboard.press("ArrowDown");
      await expect(rejectChoices.nth(index)).toBeChecked();
    }
  }
  const submitEvidence = page.getByRole("button", {
    name: "Submit evidence decisions",
  });
  await tabTo(page, submitEvidence);
  await page.keyboard.press("Enter");

  await expect(page.getByRole("heading", { name: "Explain", exact: true })).toBeVisible();
  await chooseWithKeyboard(
    page,
    page.getByRole("radio", {
      name: "The rule reported the failure but allowed processing.",
    }),
  );
  const explanation = page.getByLabel("Your explanation");
  await tabTo(page, explanation);
  await page.keyboard.type(
    "The uniqueness rule reported the duplicate but allowed the downstream write.",
  );
  const uncertainty = page.getByLabel("What remains uncertain?");
  await tabTo(page, uncertainty);
  await page.keyboard.type(
    "This synthetic result does not prove any real provider invoice is correct.",
  );
  const confidenceAfter = page.getByLabel("Confidence after the evidence");
  await tabTo(page, confidenceAfter);
  await page.keyboard.press("5");
  const complete = page.getByRole("button", { name: "Complete attempt" });
  await tabTo(page, complete);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Review", exact: true })).toBeVisible();
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

  const lesson = page.getByRole("link", {
    name: "Prove what a green ETL run does not prove",
  });
  await tabTo(page, lesson);
  await page.keyboard.press("Enter");
  await expect(
    page.getByText("Map → Predict → Try → Prove → Explain → Review."),
  ).toBeVisible();
  await expect(page.getByText("7 cents", { exact: true })).toHaveCount(0);

  const beginFirst = page.getByRole("button", { name: "Begin lesson" });
  await tabTo(page, beginFirst);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Map", exact: true })).toBeVisible();
  const originalAttemptUrl = page.url();
  const restart = page.getByRole("button", {
    name: "Restart this whole attempt",
  });
  await tabTo(page, restart);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Map", exact: true })).toBeVisible();
  expect(page.url()).not.toBe(originalAttemptUrl);
  const restartedAttemptUrl = page.url();
  await page.goto(originalAttemptUrl);
  await expect(page.getByRole("heading", { name: "Reset attempt" })).toBeVisible();
  await expect(page.getByText("This attempt is read-only.", { exact: false })).toBeVisible();
  await expect(page.locator("form")).toHaveCount(0);
  await page.reload();
  await expect(page.locator("form")).toHaveCount(0);
  await page.goto(restartedAttemptUrl);

  await completeLesson(page, {
    predictionIndex: 0,
    evidenceVerdicts: ["supports", "supports", "supports", "supports"],
    resetBeforeProve: true,
    begin: false,
  });
  await expect(page.getByText("Mastery: Introduced")).toBeVisible();
  await expect(page.getByText("prediction incorrect")).toBeVisible();
  await expect(page.getByText("evidence insufficient")).toBeVisible();

  const packLink = page.getByRole("link", { name: "Accepted packs" });
  await tabTo(page, packLink);
  await page.keyboard.press("Enter");
  await tabTo(
    page,
    page.getByRole("link", { name: "Synthetic Codex ETL" }),
  );
  await page.keyboard.press("Enter");
  await tabTo(
    page,
    page.getByRole("link", {
      name: "Prove what a green ETL run does not prove",
    }),
  );
  await page.keyboard.press("Enter");
  await completeLesson(page, {
    predictionIndex: 1,
    evidenceVerdicts: ["supports", "supports", "supports", "rejects"],
  });
  await expect(page.getByText("Mastery: Demonstrated")).toBeVisible();
  await expect(page.getByText("No qualification gaps.")).toBeVisible();
  await expect(page.getByText("Review due now.", { exact: false })).toBeVisible();
  await expect(page.getByText("browser-private-canary-6f103")).toHaveCount(0);

  await page.goto("/learn/codex-etl/lesson-codex-etl-quality");
  await expect(page.getByRole("heading", { name: "Attempt history" })).toBeVisible();
  await expect(page.getByText(/learning — reset/)).toHaveCount(1);
  await expect(page.getByText(/learning — completed/)).toHaveCount(2);
  const beginReview = page.getByRole("button", { name: "Begin due review" });
  await tabTo(page, beginReview);
  await page.keyboard.press("Enter");
  await completeLesson(page, {
    predictionIndex: 1,
    evidenceVerdicts: ["supports", "supports", "supports", "rejects"],
    begin: false,
  });
  await expect(page.getByText("Mastery: Retained")).toBeVisible();
  await expect(
    page.getByText("A later qualifying review proved this outcome again."),
  ).toBeVisible();

  const noOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  );
  expect(noOverflow).toBe(true);
});
