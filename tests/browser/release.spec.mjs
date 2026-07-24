import { spawn, spawnSync } from "node:child_process";
import {
  cpSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { expect, test } from "@playwright/test";


const CANARY = "release-private-canary-8f2c47c70dd1";
const REPO = resolve(import.meta.dirname, "../..");
const PYTHONPATH = `${join(REPO, "src")}:${REPO}`;


function runCli(home, request, ...arguments_) {
  const result = spawnSync(
    "python3",
    ["-m", "ops_learning_lab", ...arguments_, "--home", home],
    {
      cwd: REPO,
      encoding: "utf8",
      env: { ...process.env, PYTHONPATH },
      input: request === null ? undefined : JSON.stringify(request),
    },
  );
  expect(
    result.status,
    `CLI failed\nstdout: ${result.stdout}\nstderr: ${result.stderr}`,
  ).toBe(0);
  return {
    output: `${result.stdout}${result.stderr}`,
    value: JSON.parse(result.stdout),
  };
}


async function waitUntilReady(origin) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`${origin}/health`);
      if (response.ok) {
        return;
      }
    } catch {
      // The child has not bound the loopback socket yet.
    }
    await new Promise((resolve_) => setTimeout(resolve_, 50));
  }
  throw new Error("release fixture server did not become ready");
}


async function availablePort() {
  const probe = createServer();
  await new Promise((resolve_, reject) => {
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", resolve_);
  });
  const address = probe.address();
  if (address === null || typeof address === "string") {
    probe.close();
    throw new Error("failed to allocate a loopback test port");
  }
  await new Promise((resolve_, reject) => {
    probe.close((error) => (error ? reject(error) : resolve_()));
  });
  return address.port;
}


async function startShell(home, canaryFile, port) {
  const child = spawn(
    "python3",
    [
      "release_server.py",
      "--home",
      home,
      "--canary-file",
      canaryFile,
      "--port",
      String(port),
    ],
    {
      cwd: import.meta.dirname,
      env: { ...process.env, PYTHONPATH },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  const logs = [];
  child.stdout.on("data", (chunk) => logs.push(chunk.toString("utf8")));
  child.stderr.on("data", (chunk) => logs.push(chunk.toString("utf8")));
  await waitUntilReady(`http://127.0.0.1:${port}`);
  return { child, logs };
}


async function stopShell(shell) {
  if (shell.child.exitCode !== null) {
    return;
  }
  shell.child.kill("SIGTERM");
  await new Promise((resolve_, reject) => {
    const timeout = setTimeout(
      () => reject(new Error("release fixture server did not stop")),
      5_000,
    );
    shell.child.once("exit", () => {
      clearTimeout(timeout);
      resolve_();
    });
  });
}


async function tabTo(page, locator, limit = 100) {
  for (let attempt = 0; attempt < limit; attempt += 1) {
    if (await locator.evaluate((element) => element === document.activeElement)) {
      return;
    }
    await page.keyboard.press("Tab");
  }
  throw new Error("keyboard focus did not reach the expected control");
}


async function choose(page, locator) {
  await tabTo(page, locator);
  await page.keyboard.press("Space");
  await expect(locator).toBeChecked();
}


async function reachTry(page, { begin = false } = {}) {
  if (begin) {
    const beginButton = page.getByRole("button", { name: "Begin lesson" });
    await tabTo(page, beginButton);
    await page.keyboard.press("Enter");
  }
  await expect(page.getByRole("heading", { name: "Map", exact: true })).toBeVisible();
  await tabTo(page, page.getByRole("button", { name: "I have traced the map" }));
  await page.keyboard.press("Enter");

  const predictions = page.getByRole("group", {
    name: "Your prediction",
  }).getByRole("radio");
  await tabTo(page, predictions.first());
  await page.keyboard.press("Space");
  await page.keyboard.press("ArrowDown");
  await expect(predictions.nth(1)).toBeChecked();
  await tabTo(page, page.getByLabel("Confidence before the result"));
  await page.keyboard.press("4");
  await tabTo(page, page.getByRole("button", { name: "Lock prediction" }));
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Try", exact: true })).toBeVisible();
}


async function finishFromTry(page) {
  await tabTo(page, page.getByRole("button", { name: "Run the pipeline" }));
  await page.keyboard.press("Enter");
  const supports = page.getByRole("radio", { name: "Supports the claim" });
  const rejects = page.getByRole("radio", {
    name: "Reject as insufficient or misleading",
  });
  for (let index = 0; index < 4; index += 1) {
    await choose(page, supports.nth(index));
    if (index === 3) {
      await page.keyboard.press("ArrowDown");
      await expect(rejects.nth(index)).toBeChecked();
    }
  }
  await tabTo(
    page,
    page.getByRole("button", { name: "Submit evidence decisions" }),
  );
  await page.keyboard.press("Enter");

  await choose(
    page,
    page.getByRole("radio", {
      name: "The rule reported the failure but allowed processing.",
    }),
  );
  await tabTo(page, page.getByLabel("Your explanation"));
  await page.keyboard.type(
    "The non-blocking rule reported the duplicate and the downstream write continued.",
  );
  await tabTo(page, page.getByLabel("What remains uncertain?"));
  await page.keyboard.type(
    "This synthetic run cannot prove that a real external invoice is correct.",
  );
  await tabTo(page, page.getByLabel("Confidence after the evidence"));
  await page.keyboard.press("5");
  await tabTo(page, page.getByRole("button", { name: "Complete attempt" }));
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Review", exact: true })).toBeVisible();
}


async function finishQualifyingAttempt(page, options = {}) {
  await reachTry(page, options);
  await finishFromTry(page);
}


function allFiles(root) {
  const paths = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) {
      paths.push(...allFiles(path));
    } else if (entry.isFile()) {
      paths.push(path);
    }
  }
  return paths;
}


test("one private Codex extract becomes retained, portable public learning", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 320, height: 720 });
  const root = mkdtempSync(join(tmpdir(), "opslearn-release-"));
  const home = join(root, "learning-home");
  const portable = join(root, "portable");
  const canaryFile = join(root, "privacy-canary.txt");
  const commandOutputs = [];
  const responseBodies = [];
  const shellLogs = [];
  const port = await availablePort();
  const origin = `http://127.0.0.1:${port}`;
  writeFileSync(canaryFile, CANARY, { encoding: "utf8", mode: 0o600 });

  page.on("response", (response) => {
    if (!response.url().startsWith(origin)) {
      return;
    }
    responseBodies.push(
      response
        .body()
        .then((body) => body.toString("utf8"))
        .catch(() => ""), // Redirects legitimately have no response body.
    );
  });

  let shell = null;
  try {
    const initialized = runCli(home, null, "init");
    commandOutputs.push(initialized.output);

    const source = {
      kind: "task_turns_extract",
      task_id: "synthetic-release-task",
      turn_ids: ["turn-18", "turn-19"],
      observed_at: "2026-07-24T12:00:00Z",
      text: [
        CANARY,
        "Codex ETL usage cost.",
        "Claim [current]: Synthetic normalized cost should be non-negative.",
      ].join("\n"),
    };
    const captured = runCli(
      home,
      { schema_version: 1, mode: "capture", source },
      "codex-import",
    );
    commandOutputs.push(captured.output);
    expect(captured.value.status).toBe("staged");
    expect(captured.value.lesson_started).toBe(false);

    shell = await startShell(home, canaryFile, port);
    await page.goto(`${origin}${captured.value.review_path}`);
    await expect(
      page.getByRole("heading", { name: "Review staged content" }),
    ).toBeVisible();
    await page.getByLabel("Pack ID").fill("codex-etl");
    await page.getByLabel("Pack title").fill("Synthetic Codex ETL");
    await choose(
      page,
      page.getByRole("radio", { name: "Accept sanitized content" }),
    );
    await page
      .getByLabel("Independently written accepted text")
      .fill("Reviewed normalized synthetic cost is non-negative.");
    await page.getByLabel("Accepted fact status").selectOption("current");
    await page.getByLabel("History decision").selectOption("add");
    await choose(page, page.getByLabel("I removed private-only details"));
    await tabTo(
      page,
      page.getByRole("button", { name: "Preview without writing" }),
    );
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("heading", { name: "Preview Promotion" }),
    ).toBeVisible();
    await choose(
      page,
      page.getByRole("checkbox", {
        name: "Promote exactly this reviewed content",
      }),
    );
    await tabTo(page, page.getByRole("button", { name: "Promote atomically" }));
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(`${origin}/packs/codex-etl`);

    await tabTo(
      page,
      page.getByRole("link", {
        name: "Prove what a green ETL run does not prove",
      }),
    );
    await page.keyboard.press("Enter");
    await reachTry(page, { begin: true });
    const restoredAttemptPath = new URL(page.url()).pathname;

    await stopShell(shell);
    shellLogs.push(...shell.logs);
    shell = await startShell(home, canaryFile, port);
    await page.reload();
    await expect(page).toHaveURL(`${origin}${restoredAttemptPath}`);
    await expect(page.getByRole("heading", { name: "Try", exact: true })).toBeVisible();

    const learned = runCli(
      home,
      {
        schema_version: 1,
        mode: "learn",
        source,
        selected_pack_id: "codex-etl",
      },
      "codex-import",
    );
    commandOutputs.push(learned.output);
    expect(learned.value.status).toBe("learning_ready");
    expect(learned.value.learning_disposition).toBe("resumed");
    expect(learned.value.learning_path).toBe(restoredAttemptPath);

    await finishFromTry(page);
    await expect(page.getByText("Mastery: Demonstrated")).toBeVisible();
    await expect(page.getByText("Review due now.", { exact: false })).toBeVisible();

    await page.goto(`${origin}/learn/codex-etl/lesson-codex-etl-quality`);
    await tabTo(page, page.getByRole("button", { name: "Begin due review" }));
    await page.keyboard.press("Enter");
    await finishQualifyingAttempt(page);
    await expect(page.getByText("Mastery: Retained")).toBeVisible();
    const retainingAttemptPath = new URL(page.url()).pathname;
    const bundleSha256 = await page
      .locator("dt", { hasText: "Bundle snapshot" })
      .locator("xpath=following-sibling::dd[1]/code")
      .textContent();
    expect(bundleSha256).toMatch(/^[0-9a-f]{64}$/);

    await page.goto(`${origin}/learn/codex-etl/lesson-codex-etl-quality`);
    await expect(page.getByText("Retained", { exact: true })).toBeVisible();
    const retainingLink = page.getByRole("link", {
      name: "Open retaining review attempt",
    });
    await expect(retainingLink).toHaveAttribute("href", retainingAttemptPath);

    const approved = runCli(
      home,
      null,
      "export-approve",
      "--bundle-sha256",
      bundleSha256,
    );
    commandOutputs.push(approved.output);
    const exported = runCli(
      home,
      null,
      "export",
      "--bundle-sha256",
      bundleSha256,
      "--canary-file",
      canaryFile,
    );
    commandOutputs.push(exported.output);
    expect(exported.value.privacy_status).toBe("passed");

    mkdirSync(portable);
    const artifact = join(home, "exports", exported.value.relative_path);
    const portableArtifact = join(portable, "index.html");
    cpSync(artifact, portableArtifact);

    await stopShell(shell);
    shellLogs.push(...shell.logs);
    shell = null;
    await page.context().setOffline(true);
    const externalRequests = [];
    page.on("request", (request) => {
      if (/^https?:/.test(request.url())) {
        externalRequests.push(request.url());
      }
    });
    await page.goto(pathToFileURL(portableArtifact).href);
    await expect(
      page.getByRole("heading", { level: 1, name: "Synthetic Codex ETL" }),
    ).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(
      page.getByRole("link", { name: "Skip to learning content" }),
    ).toBeFocused();
    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
    expect(externalRequests).toEqual([]);

    const publishableFiles = ["packs", "snapshots", "exports"].flatMap(
      (directory) => allFiles(join(home, directory)),
    );
    for (const path of publishableFiles) {
      expect(readFileSync(path).includes(Buffer.from(CANARY))).toBe(false);
    }
    expect(
      allFiles(join(home, "private")).some((path) =>
        readFileSync(path).includes(Buffer.from(CANARY)),
      ),
      "the canary must genuinely exist inside the private boundary",
    ).toBe(true);
    expect(readFileSync(portableArtifact).includes(Buffer.from(CANARY))).toBe(false);
    expect(commandOutputs.join("\n")).not.toContain(CANARY);
    expect((await Promise.all(responseBodies)).join("\n")).not.toContain(CANARY);
    expect(shellLogs.join("\n")).not.toContain(CANARY);
  } finally {
    await page.context().setOffline(false);
    if (shell !== null) {
      await stopShell(shell);
    }
    rmSync(root, { recursive: true, force: true });
  }
});
