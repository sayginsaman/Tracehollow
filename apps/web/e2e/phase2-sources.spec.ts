import { expect, test } from "@playwright/test";

/**
 * Phase 2 in a real browser against scripts/verify-phase2.sh's stack: the Sources screen, then
 * a public web page collected from the controlled fixture-site container and its evidence.
 * No real public source is contacted.
 */

const username = process.env.TRACEHOLLOW_E2E_USERNAME ?? "";
const password = process.env.TRACEHOLLOW_E2E_PASSWORD ?? "";
const pageUrl = process.env.TRACEHOLLOW_E2E_FIXTURE_PAGE ?? "http://fixture-site:8080/web/ornek";

test.skip(!username || !password, "Set TRACEHOLLOW_E2E_USERNAME and TRACEHOLLOW_E2E_PASSWORD (see e2e/README.md)");

test("analyst compares sources and collects a public page with provenance", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/overview$/);

  await test.step("sources show how each collector reaches data", async () => {
    await page.getByRole("link", { name: "Sources", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Sources", level: 1 })).toBeVisible();
    await expect(page.getByText("Public web page", { exact: true })).toBeVisible();
    await expect(page.getByText(/Direct request\./).first()).toBeVisible();
    await expect(page.getByText(/Platform probe\./).first()).toBeVisible();
    await expect(page.getByText("Fixture-tested: not live-verified").first()).toBeVisible();
    await expect(page.getByText("Personal access token", { exact: true })).toBeVisible();
  });

  await test.step("create a case and collect the page", async () => {
    await page.getByRole("link", { name: "Cases", exact: true }).click();
    await page.getByRole("button", { name: "New case" }).click();
    await page.getByLabel("Title").fill(`Kaynak toplama ${Date.now()}`);
    await page.getByLabel("Purpose").fill("Browser verification of public-source collection (controlled fixture).");
    await page.getByRole("button", { name: "Create case" }).click();
    await page.waitForURL(/\/cases\/[0-9a-f-]{36}$/);
    await page.getByRole("link", { name: "Queries & runs", exact: true }).click();
    await page.getByLabel("Source").selectOption("public_web.page");
    await expect(page.getByText(/Direct request:/)).toBeVisible();
    await page.getByLabel("Name", { exact: true }).fill("Basın duyurusu");
    await page.getByLabel("Input value").fill(pageUrl);
    await page.getByRole("button", { name: "Save query" }).click();
    await page.locator("li", { hasText: "Basın duyurusu" }).getByRole("button", { name: "Run", exact: true }).click();
    await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/);
    await expect(page.getByText("Completed.")).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText(/Direct request: Tracehollow requests the address/)).toBeVisible();
  });

  await test.step("open the extracted text and its original snapshot", async () => {
    await page.getByRole("link", { name: /^Web page text:/ }).click();
    await expect(page.getByLabel("Evidence content preview")).toContainText("İzmir şubesi 2026-09-10 tarihinde");
    await expect(page.getByLabel("Evidence content preview")).not.toContainText("SYSTEM OVERRIDE");
    await expect(page.getByText(/Collected by a connector: Direct request/)).toBeVisible();
    await page.getByRole("link", { name: "Original snapshot" }).click();
    await expect(page.getByLabel("Evidence content preview")).toContainText("<script>");
    await expect(page.getByText("HTTP status")).toBeVisible();
  });

  expect(pageErrors).toEqual([]);
});
