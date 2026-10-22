import { test, expect } from "@playwright/test";
import { spawn, execFile } from "node:child_process";
import { once } from "node:events";
import { promisify } from "node:util";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";

test("approval, report, and follow-up survive a real API process restart", async ({ page, request }) => {
  test.setTimeout(420000);
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const python = process.env.CARE_TRANSITION_TEST_PYTHON || path.join(root,
    process.platform === "win32" ? ".venv-local/Scripts/python.exe" : ".venv-local/bin/python");
  const folder = await mkdtemp(path.join(os.tmpdir(), "copilot-browser-restart-"));
  const portReservation = createServer();
  portReservation.listen(0, "127.0.0.1");
  await once(portReservation, "listening");
  const port = portReservation.address().port;
  await new Promise((resolve) => portReservation.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  let server;
  let output = "";
  async function start() {
    server = spawn(python, ["-m", "tests.web_server"], {
      cwd: root,
      env: { ...process.env, BROWSER_TEST_ROOT: folder, BROWSER_TEST_PORT: String(port) },
      stdio: ["ignore", "pipe", "pipe"],
    });
    server.stdout.on("data", (chunk) => { output += chunk; });
    server.stderr.on("data", (chunk) => { output += chunk; });
    server.on("error", (error) => { output += error.message; });
    await expect.poll(async () => {
      if (server.exitCode !== null) throw new Error(output);
      try { return (await request.get(`${origin}/api/ready`, { timeout: 1000 })).status(); }
      catch { return 0; }
    }, { timeout: 180000, message: "Isolated restart server must become ready" }).toBe(200);
  }
  async function stop() {
    if (server && server.exitCode === null && server.signalCode === null) {
      const closed = once(server, "close");
      let stopped = false;
      try {
        const response = await request.post(`${origin}/__test__/shutdown`, { timeout: 5000 });
        stopped = response.ok();
      } catch { /* A failed startup still needs process cleanup. */ }
      if (!stopped && process.platform === "win32") {
        // Windows virtualenv Python launches a child interpreter; stop both.
        await promisify(execFile)("taskkill", ["/PID", String(server.pid), "/T", "/F"]);
      } else if (!stopped) {
        server.kill();
      }
      await closed;
    }
  }
  try {
    await start();
    await page.goto(origin);
    const card = page.locator(".queue-card").first();
    const name = await card.locator(".queue-card-name").innerText();
    await card.click();
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(page.locator(".report-preview")).toBeVisible();
    const report = await page.locator(".report-preview .plan-rendered").innerText();
    await page.getByRole("combobox").selectOption("scheduled");
    await page.getByPlaceholder("Note", { exact: true }).fill("Persist across a process restart.");
    await page.getByRole("button", { name: "Save status" }).click();
    await expect(page.getByText(/Last status: scheduled/)).toBeVisible();
    const oldPid = server.pid;
    await stop();
    await start();
    expect(server.pid).not.toBe(oldPid);
    expect((await request.post(`${origin}/__test__/outage/true`)).ok()).toBeTruthy();
    await page.reload();
    await page.locator(".queue-card").filter({ hasText: name }).first().click();
    await expect(page.locator(".decision-badge.approved")).toBeVisible();
    await expect(page.locator(".report-preview .plan-rendered")).toHaveText(report, { useInnerText: true });
    await expect(page.getByText(/Last status: scheduled/)).toBeVisible();
    await expect(page.getByPlaceholder("Note", { exact: true })).toHaveValue("Persist across a process restart.");
    const downloading = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download", exact: true }).click();
    const download = await downloading;
    expect(await readFile(await download.path(), "utf8")).toContain(name);
    await expect(page.locator(".error-message")).toHaveCount(0);
  } finally {
    await stop();
    await rm(folder, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
});
