import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test.describe.serial("local clinician workflow", () => {
  let firstName;

  test("all pages load and direct navigation survives reload", async ({ page }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    for (const [path, heading] of [["/", "Risk queue"], ["/patients", "Patients"],
      ["/care-plans", "Care plans"], ["/operations", "System status"], ["/chat", "Patient chat"]]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
      await page.reload();
      await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    }
    expect(errors).toEqual([]);
  });

  test("search, risk filters, pagination, and chart history", async ({ page }) => {
    await page.goto("/patients");
    await expect(page.locator(".patient-episode").first()).toBeVisible();
    await page.getByRole("button", { name: "Load more patients" }).click();
    await expect(page.locator(".patient-episode")).toHaveCount(100);
    await page.getByRole("combobox").selectOption("high");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.locator(".patient-episode").first()).toContainText("high");
    const name = await page.locator(".patient-episode .queue-card-name").first().innerText();
    await page.getByRole("searchbox").fill(name);
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.locator(".patient-episode").first()).toContainText(name);
    await page.getByRole("button", { name: "View history" }).first().click();
    await expect(page.getByRole("heading", { name: "Indexed chart history" })).toBeVisible();
    await expect(page.locator(".patient-history-list")).toBeVisible();
    await page.getByRole("button", { name: "Hide history" }).first().click();
    await expect(page.locator(".patient-history")).toHaveCount(0);
    await page.getByRole("searchbox").fill("no-such-patient-zzzz");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.getByText("No patient episodes match these filters.")).toBeVisible();
  });

  test("generate, reject, edit, approve, download, follow up, and remind", async ({ page }) => {
    await page.goto("/");
    const card = page.locator(".queue-card").first();
    firstName = await card.locator(".queue-card-name").innerText();
    await card.click();
    await expect(page.getByRole("button", { name: "Reject", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Reject", exact: true }).click();
    await expect(page.locator(".report-status.rejected")).toContainText("Draft rejected.");
    await page.getByRole("button", { name: "Edit rejected draft" }).click();
    await page.locator(".plan-editor").fill("Clinician edited plan: schedule follow-up within seven days.");
    await page.getByRole("button", { name: "Approve edited plan" }).click();
    await expect(page.locator(".report-preview")).toContainText("Clinician edited plan");
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download", exact: true }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/care-transition-report.*\.md/);
    const markdown = await readFile(await download.path(), "utf8");
    expect(markdown).toContain("Clinician edited plan: schedule follow-up within seven days.");
    expect(markdown).toContain(firstName);
    await page.getByRole("combobox").selectOption("scheduled");
    await page.getByPlaceholder("Note", { exact: true }).fill("Appointment scheduled by clinician.");
    await page.getByRole("button", { name: "Save status" }).click();
    await expect(page.getByText(/Last status: scheduled/)).toBeVisible();
    await page.locator('input[type="datetime-local"]').fill("2026-12-01T10:00");
    await page.getByPlaceholder("Reminder message").fill("Confirm follow-up attendance.");
    await page.getByRole("button", { name: "Schedule reminder" }).click();
    await expect(page.getByText(/Reminder scheduled for/)).toBeVisible();
    await page.reload();
    await page.locator(".queue-card").filter({ hasText: firstName }).first().click();
    await expect(page.locator(".decision-badge.approved")).toBeVisible();
    await expect(page.locator(".report-preview")).toContainText("Clinician edited plan");
    await expect(page.getByText(/Last status: scheduled/)).toBeVisible();
  });

  test("saved approvals and reports remain available with AI down and cache cleared", async ({ page, request }) => {
    await request.post("/__test__/restart");
    await request.post("/__test__/outage/true");
    await page.goto("/");
    await page.locator(".queue-card").filter({ hasText: firstName }).first().click();
    await expect(page.locator(".report-preview")).toContainText("Clinician edited plan");
    await expect(page.getByText(/Last status: scheduled/)).toBeVisible();
    await page.locator(".queue-card").nth(2).click();
    await expect(page.locator(".error-message")).toContainText("AI generation is unavailable");
    await expect(page.locator(".error-message")).toContainText("Request ID:");
    await page.getByRole("link", { name: "Patients", exact: true }).click();
    await expect(page.locator(".patient-episode").first()).toBeVisible();
    await page.getByRole("button", { name: "View history" }).first().click();
    await expect(page.locator(".patient-history-list")).toBeVisible();
    await request.post("/__test__/outage/false");
  });

  test("care plans, filters, and background precomputation", async ({ page }) => {
    await page.goto("/care-plans");
    await expect(page.locator(".queue-card").first()).toBeVisible();
    await page.getByRole("button", { name: "Approved", exact: true }).click();
    await expect(page.locator(".queue-card").first()).toContainText(firstName);
    await page.locator(".queue-card").first().click();
    await expect(page.locator(".plan-rendered")).toBeVisible();
    await page.getByRole("button", { name: "All", exact: true }).click();
    await page.getByRole("button", { name: /Precompute High-Risk Plans/ }).click();
    await expect(page.getByText(/cached plans ready/)).toBeVisible({ timeout: 60000 });
  });

  test("chat lookup, identifier guardrail, and visible provider outage", async ({ page, request }) => {
    await page.goto("/chat");
    const input = page.getByPlaceholder("Ask about a patient...");
    await input.fill(`Review ${firstName}`);
    await input.press("Enter");
    await expect(page.locator(".chat-message.assistant").last()).toContainText("Patient lookup completed");
    await input.fill("patient_id 1234567890abcdef");
    await input.press("Enter");
    await expect(page.locator(".chat-message.assistant").last()).toContainText("Patient IDs are not accepted");
    await request.post("/__test__/outage/true");
    await input.fill("Explain the discharge workflow");
    await input.press("Enter");
    await expect(page.locator(".chat-message.assistant").last()).toContainText("AI generation is unavailable");
    await request.post("/__test__/outage/false");
    await input.fill("Explain the discharge workflow");
    await input.press("Enter");
    await expect(page.locator(".chat-message.assistant").last()).toContainText("Patient lookup completed");
  });

  test("rapid patient selection keeps the latest episode on screen", async ({ page }) => {
    let delayed = false;
    let release;
    let intercepted;
    const held = new Promise((resolve) => { release = resolve; });
    const started = new Promise((resolve) => { intercepted = resolve; });
    let finished;
    const delivered = new Promise((resolve) => { finished = resolve; });
    await page.route("**/api/patients/*/assessment?*", async (route) => {
      if (!delayed) {
        delayed = true;
        const response = await route.fetch();
        const body = await response.json();
        intercepted();
        await held;
        await route.fulfill({ response, json: { ...body, draft_plan: "STALE PATIENT PLAN MUST NEVER APPEAR" } });
        finished();
        return;
      }
      await route.continue();
    });
    await page.goto("/");
    const cards = page.locator(".queue-card");
    const secondName = await cards.nth(1).locator(".queue-card-name").innerText();
    await cards.first().click();
    await started;
    await cards.nth(1).click();
    await expect(page.locator(".panel-gold .panel-subtitle")).toContainText(secondName);
    await expect(page.locator(".panel-gold .plan-rendered").first()).toBeVisible();
    release();
    await delivered;
    // Wait for a later browser task so React can process the stale response.
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(page.locator(".panel-gold .panel-subtitle")).toContainText(secondName);
    await expect(page.locator(".panel-gold")).not.toContainText("STALE PATIENT PLAN MUST NEVER APPEAR");
  });

  test("operations displays health, audit events, and persisted handoffs", async ({ page }) => {
    await page.goto("/operations");
    await expect(page.getByText("API ok", { exact: true })).toBeVisible();
    await expect(page.getByText("care_plan_decision_recorded", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("scheduled", { exact: true }).first()).toBeVisible();
  });
});
