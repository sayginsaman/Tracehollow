import { expect, test } from "@playwright/test";

/**
 * Phase 3 AI workflow in a real browser against a running stack: import evidence, wait for
 * indexing, ask a question, open a citation and read the exact supporting passage. Uses only
 * synthetic data. With the synthetic fixture provider the answer must be labelled synthetic.
 */

const username = process.env.TRACEHOLLOW_E2E_USERNAME ?? "";
const password = process.env.TRACEHOLLOW_E2E_PASSWORD ?? "";

test.skip(!username || !password, "Set TRACEHOLLOW_E2E_USERNAME and TRACEHOLLOW_E2E_PASSWORD (see e2e/README.md)");

const REGISTRY =
  "Kayıt özeti: ornek.example alan adı, İstanbul merkezli Örnek A.Ş. tarafından 2026-09-01 tarihinde tescil edildi.";

test("analyst asks a cited question and opens the supporting passage", async ({ page }) => {
  test.setTimeout(600_000);
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const title = `AI e2e ${Date.now()}`;

  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/cases$/);

  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Purpose").fill("Browser verification of cited AI answers.");
  await page.getByRole("button", { name: "Create case" }).click();
  await page.waitForURL(/\/cases\/[0-9a-f-]{36}$/);
  const caseUrl = page.url();

  await page.getByRole("link", { name: "Evidence", exact: true }).click();
  await page.getByLabel("Paste content").fill(REGISTRY);
  await page.getByLabel("Name for pasted content").fill("registry.txt");
  await page.getByLabel("Title").fill("Registry extract");
  await page.getByLabel("Import origin (required)").fill("Synthetic registry extract written for the browser test");
  await page.getByRole("button", { name: "Import evidence" }).click();
  await expect(page.getByRole("table").getByRole("link", { name: "Registry extract" })).toBeVisible();

  await page.goto(`${caseUrl}/ai`);
  await expect(page.getByRole("status", { name: "AI processing location" })).toContainText("Local processing only");
  await expect(page.getByText("Indexed: 1")).toBeVisible({ timeout: 300_000 });

  await page.getByRole("button", { name: "New conversation" }).click();
  await page.waitForURL(/\/ai\/conversations\/[0-9a-f-]{36}$/);
  await page.getByRole("textbox", { name: "Question" }).fill("ornek.example alan adı hangi tarihte kim tarafından tescil edildi?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText(/Answered from cited case material|Partially answered/)).toBeVisible({ timeout: 300_000 });

  const synthetic = await page.getByText("Synthetic model").count();
  const status = await page.request.get("/api/v1/ai/status");
  const aiStatus = (await status.json()) as { synthetic: boolean };
  expect(synthetic > 0).toBe(aiStatus.synthetic);

  await page.getByRole("button", { name: /Open citation E\d+: Evidence: Registry extract/ }).first().click();
  const panel = page.getByRole("complementary", { name: "Citation details" });
  const passage = panel.getByLabel("Cited passage in context");
  await expect(passage).toBeVisible();
  const highlighted = (await passage.locator("mark").innerText()).trim();
  expect(highlighted.length).toBeGreaterThan(3);
  expect(REGISTRY).toContain(highlighted);
  await expect(panel.getByText("SHA-256 verified before display")).toBeVisible();
  await panel.getByRole("link", { name: "Registry extract" }).click();
  await expect(page.getByLabel("Evidence content preview")).toContainText(highlighted);

  expect(pageErrors).toEqual([]);
});
