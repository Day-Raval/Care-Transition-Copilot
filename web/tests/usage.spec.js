import { test, expect } from "@playwright/test";

test("repeated approval clicks submit one decision for the selected episode", async ({ page }) => {
  await page.goto("/");
  const cards = page.locator(".queue-card");
  const name = await cards.nth(10).locator(".queue-card-name").innerText();
  await cards.nth(10).click();
  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await expect(approve).toBeVisible();
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const writes = [];
  await page.route("**/api/patients/*/decision?*", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    writes.push(route.request());
    await held;
    await route.continue();
  });
  try {
    // Two real pointer clicks; the second must hit a disabled control.
    await approve.click({ clickCount: 2 });
    await expect(approve).toBeDisabled();
    await expect(page.getByRole("button", { name: "Reject", exact: true })).toBeDisabled();
    await cards.nth(11).click();
    await expect(page.locator(".panel-gold .panel-subtitle")).toContainText(name);
  } finally {
    release();
  }
  await expect(page.locator(".decision-badge.approved")).toBeVisible();
  await expect(page.locator(".report-preview")).toBeVisible();
  expect(writes).toHaveLength(1);
  const decision = await page.request.get(writes[0].url(), {
    headers: { "X-API-Key": "browser-fixture-key-0123456789" },
  });
  expect(decision.ok()).toBeTruthy();
  expect((await decision.json()).decision).toBe("approved");
});

test("failed assessment connection shows an error and selecting again recovers", async ({ page }) => {
  await page.goto("/");
  const card = page.locator(".queue-card").nth(12);
  await page.route("**/api/patients/*/assessment?*", (route) => route.abort("connectionfailed"));
  await card.click();
  await expect(page.locator(".error-message")).toContainText("Could not reach the app");
  await expect(page.locator(".error-message")).toContainText("Request ID:");
  await expect(page.getByText("Drafting plan…", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);
  await page.unroute("**/api/patients/*/assessment?*");
  await card.click();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
  await expect(page.locator(".error-message")).toHaveCount(0);
});

test("failed decision save preserves the draft and allows a successful retry", async ({ page }) => {
  await page.goto("/");
  await page.locator(".queue-card").nth(13).click();
  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await expect(approve).toBeVisible();
  const draft = await page.locator(".panel-gold .plan-rendered").first().innerText();
  await page.route("**/api/patients/*/decision?*", (route) => (
    route.request().method() === "POST" ? route.abort("connectionfailed") : route.continue()
  ));
  await approve.click();
  await expect(page.locator(".error-message")).toContainText("Could not reach the app");
  await expect(approve).toBeEnabled();
  await expect(page.locator(".panel-gold .plan-rendered").first()).toHaveText(draft, { useInnerText: true });
  await expect(page.locator(".decision-badge.approved")).toHaveCount(0);
  await expect(page.locator(".report-preview")).toHaveCount(0);
  await page.unroute("**/api/patients/*/decision?*");
  await approve.click();
  await expect(page.locator(".decision-badge.approved")).toBeVisible();
  await expect(page.locator(".report-preview")).toBeVisible();
  await expect(page.locator(".error-message")).toHaveCount(0);
});
