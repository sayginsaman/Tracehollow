import { expect, test } from "@playwright/test";

/**
 * The redesigned shell in a real browser: Overview, case context in the sidebar and breadcrumbs,
 * a PDF imported through the Imports page, preferences (theme) and returning from settings to the
 * investigation, and the navigation drawer on a phone-sized viewport. Uses only synthetic data,
 * needs a worker for PDF processing, and deletes the case it creates.
 */

const username = process.env.TRACEHOLLOW_E2E_USERNAME ?? "";
const password = process.env.TRACEHOLLOW_E2E_PASSWORD ?? "";

test.skip(!username || !password, "Set TRACEHOLLOW_E2E_USERNAME and TRACEHOLLOW_E2E_PASSWORD (see e2e/README.md)");

/** A one-page PDF with a text layer, assembled byte by byte (no PDF library in the web app). */
function syntheticPdf(lines: string[]): Buffer {
  const content = lines.map((line, index) => `BT /F1 20 Tf 72 ${700 - index * 32} Td (${line}) Tj ET`).join("\n");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
    `<< /Length ${Buffer.byteLength(content)} >>\nstream\n${content}\nendstream`,
  ];
  let body = "%PDF-1.7\n";
  const offsets: number[] = [];
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(body));
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = Buffer.byteLength(body);
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets) body += `${String(offset).padStart(10, "0")} 00000 n \n`;
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, "latin1");
}

test("analyst moves between overview, case work, imports and settings without losing the case", async ({ page }) => {
  test.setTimeout(240_000);
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const title = `Gezinme incelemesi ${Date.now()}`;

  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/overview$/);
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Needs attention" })).toBeVisible();

  await test.step("create a case from the overview and see its context", async () => {
    await page.getByRole("link", { name: "New case" }).click();
    await page.getByLabel("Title").fill(title);
    await page.getByLabel("Purpose").fill("Browser verification of navigation and PDF imports (synthetic).");
    await page.getByRole("button", { name: "Create case" }).click();
    await page.waitForURL(/\/cases\/[0-9a-f-]{36}$/);
    await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
    const caseNav = page.getByRole("group", { name: title });
    await expect(caseNav.getByRole("link", { name: "Case overview" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Cases" })).toBeVisible();
  });
  const caseUrl = page.url();

  await test.step("import a PDF and read the extracted text with its page marker", async () => {
    await page.getByRole("link", { name: "Imports", exact: true }).click();
    await page.getByRole("tab", { name: "PDF document" }).click();
    await expect(page.getByText("64 MiB and 500 pages per document")).toBeVisible();
    const form = page.getByRole("form", { name: "Import a PDF document" });
    await form.getByLabel("PDF file").setInputFiles({ name: "rapor.pdf", mimeType: "application/pdf", buffer: syntheticPdf(["Synthetic annual report", "Hosting moved in March"]) });
    await form.getByLabel("Import origin (required)").fill("Synthetic PDF written by the browser test");
    await form.getByRole("button", { name: "Import document" }).click();
    await expect(form.getByText(/queued processing/)).toBeVisible();
    const jobs = page.getByRole("region", { name: "Processing jobs" });
    await expect(async () => {
      await jobs.getByRole("button", { name: "Refresh" }).click();
      await expect(jobs.getByText("Text extracted · 1 of 1 pages")).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 120_000 });
    await expect(jobs.getByText(/Completed$/)).toBeVisible();
    await page.getByRole("table").getByRole("link", { name: /extracted text$/ }).click();
    await expect(page.getByText("Extracted text").first()).toBeVisible();
    const preview = page.getByLabel("Evidence content preview");
    await expect(preview).toContainText("[Page 1]");
    await expect(preview).toContainText("Hosting moved in March");
    await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Evidence" })).toHaveAttribute("href", `${new URL(caseUrl).pathname}/evidence`);
  });

  await test.step("change a preference, visit configuration and return to the investigation", async () => {
    await page.getByRole("link", { name: "Preferences", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Preferences", level: 1 })).toBeVisible();
    // Outside a case the sidebar no longer shows case sections.
    await expect(page.getByRole("link", { name: "Case overview" })).toHaveCount(0);
    await page.getByLabel(/^Dark/).check();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.getByLabel(/^Match the operating system/).check();
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);

    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Environment status", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Environment status", level: 1 })).toBeVisible();
    await page.getByRole("link", { name: "Cases", exact: true }).click();
    await page.getByRole("link", { name: title }).click();
    await page.waitForURL(caseUrl);
    await page.getByRole("link", { name: "Case settings", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Case settings", level: 1 })).toBeVisible();
    await page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: title }).click();
    await page.waitForURL(caseUrl);
    await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
  });

  await test.step("use the navigation drawer on a phone-sized screen", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("link", { name: "Timeline", exact: true })).toBeHidden();
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("button", { name: "Close navigation" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("link", { name: "Timeline", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Timeline", level: 1 })).toBeVisible();
    await expect(page.getByRole("link", { name: "Timeline", exact: true })).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.setViewportSize({ width: 1400, height: 1000 });
  });

  await test.step("delete the case", async () => {
    await page.goto(`${caseUrl}/settings`);
    await page.getByLabel(`Type the case title to confirm: ${title}`).fill(title);
    await page.getByRole("button", { name: "Delete this case" }).click();
    await page.waitForURL(/\/cases\?deletion=requested$/);
    await expect(page.getByText("Deletion requested.")).toBeVisible();
  });

  expect(pageErrors).toEqual([]);
});
