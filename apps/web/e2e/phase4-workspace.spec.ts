import { expect, test } from "@playwright/test";

/**
 * Phase 4 in a real browser against scripts/verify-phase4.sh --e2e: a WhatsApp export imported
 * through the form with the date-order question answered in the UI, the timeline sections, an
 * entity comparison, a report preview in a sandboxed frame and the Sources capability matrix.
 * Uses only synthetic data and creates its own case.
 */

const username = process.env.TRACEHOLLOW_E2E_USERNAME ?? "";
const password = process.env.TRACEHOLLOW_E2E_PASSWORD ?? "";

test.skip(!username || !password, "Set TRACEHOLLOW_E2E_USERNAME and TRACEHOLLOW_E2E_PASSWORD (see e2e/README.md)");

const CHAT = [
  "03/04/2024, 09:15 - Ayşe Yılmaz: Merhaba, toplantı saat kaçta?",
  "İkinci satır <script>alert(1)</script>",
  "04/04/2024, 10:00 - Can: Yarın 14:00.",
  "",
].join("\n");

test("analyst imports a chat export, reviews times, compares entities and previews a report", async ({ page }) => {
  const pageErrors: string[] = [];
  const dialogs: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("dialog", (dialog) => {
    dialogs.push(dialog.message());
    void dialog.dismiss();
  });

  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/cases$/);

  await test.step("sources separate official and unofficial access", async () => {
    await page.getByRole("link", { name: "Sources", exact: true }).click();
    await expect(page.getByText("Official API: professional account discovery").first()).toBeVisible();
    await expect(page.getByText(/Excluded\. Excluded: private-profile content/)).toBeVisible();
    await expect(page.getByText(/Not implemented\. Not implemented: MTProto clients/)).toBeVisible();
  });

  await test.step("create a case and import a WhatsApp export", async () => {
    await page.getByRole("link", { name: "Cases", exact: true }).click();
    await page.getByLabel("Title").fill(`Sohbet incelemesi ${Date.now()}`);
    await page.getByLabel("Purpose").fill("Browser verification of authorized imports (synthetic).");
    await page.getByRole("button", { name: "Create case" }).click();
    await page.waitForURL(/\/cases\/[0-9a-f-]{36}$/);
    await page.getByRole("link", { name: "Evidence", exact: true }).click();
    const form = page.getByRole("form", { name: "Import a WhatsApp chat export" });
    await form.getByLabel("Export file").setInputFiles({ name: "WhatsApp Chat with Synthetic.txt", mimeType: "text/plain", buffer: Buffer.from(CHAT) });
    await form.getByLabel("Import origin (required)").fill("Synthetic export written by the browser test");
    await form.getByRole("button", { name: "Import chat export" }).click();
    await expect(form.getByText(/queued processing/)).toBeVisible();
  });

  await test.step("answer the date-order question", async () => {
    const jobs = page.getByRole("region", { name: "Processing jobs" });
    await expect(async () => {
      await jobs.getByRole("button", { name: "Refresh" }).click();
      await expect(jobs.getByText("Needs your input")).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 90_000 });
    await expect(jobs.getByText("03/04/2024")).toBeVisible();
    await jobs.getByLabel("Day first (31/12/2024)").check();
    await jobs.getByRole("button", { name: "Continue processing" }).click();
    await expect(async () => {
      await jobs.getByRole("button", { name: "Refresh" }).click();
      await expect(jobs.getByText("2 messages, 0 system events")).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 90_000 });
    // The status badge pairs its label with a glyph, so match the label inside it.
    await expect(jobs.getByText(/Completed$/)).toBeVisible();
  });

  await test.step("the timeline keeps local times apart from UTC", async () => {
    await page.getByRole("link", { name: "Timeline", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Timeline" })).toBeVisible();
    await page.getByRole("tab", { name: /Local time only/ }).click();
    await expect(page.getByText("Local time, timezone unknown").first()).toBeVisible();
    await expect(page.getByText("As written: 03/04/2024, 09:15")).toBeVisible();
    await expect(page.getByText(/İkinci satır <script>alert\(1\)<\/script>/)).toBeVisible();
  });

  await test.step("compare two analyst entities without merging them", async () => {
    await page.getByRole("link", { name: "Entities", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Add entity" })).toBeVisible();
    for (const name of ["Örnek hesap A", "Örnek hesap B"]) {
      await page.getByLabel("Display name").fill(name);
      await page.getByRole("button", { name: "Add entity" }).click();
      await expect(page.getByRole("link", { name })).toBeVisible();
    }
    await page.getByRole("link", { name: "Compare", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Compare entities" })).toBeVisible();
    await page.getByLabel(/Örnek hesap A/).check();
    await page.getByLabel(/Örnek hesap B/).check();
    await expect(page.getByText(/never merges entities automatically/)).toBeVisible();
  });

  await test.step("preview a report in a sandboxed frame", async () => {
    await page.getByRole("link", { name: "Reports", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Build a report" })).toBeVisible();
    const entities = page.getByRole("group", { name: "Entities", exact: true });
    await entities.getByLabel(/Örnek hesap A/).check();
    await expect(entities.getByText("1 selected", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Preview report" }).click();
    await expect(page.getByText(/Entities: 1/)).toBeVisible();
    const frame = page.getByTitle("Report preview");
    await expect(frame).toHaveAttribute("sandbox", "");
    await expect(page.frameLocator('iframe[title="Report preview"]').getByText("Selected entities")).toBeVisible();
    await expect(page.getByRole("button", { name: "Download HTML" })).toBeEnabled();
  });

  expect(pageErrors).toEqual([]);
  expect(dialogs).toEqual([]);
});
