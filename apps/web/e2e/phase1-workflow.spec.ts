import { readFile } from "node:fs/promises";

import { expect, test, type Page } from "@playwright/test";

/**
 * Phase 1 main workflow in a real browser against a running stack: case, entities, evidence,
 * relationship review, repeated and canceled executions, graph inspection, export and deletion.
 * All data is synthetic; the only connector is the synthetic fixture.
 */

const username = process.env.TRACEHOLLOW_E2E_USERNAME ?? "";
const password = process.env.TRACEHOLLOW_E2E_PASSWORD ?? "";
const setupToken = process.env.TRACEHOLLOW_E2E_SETUP_TOKEN ?? "";

test.skip(!username || !password, "Set TRACEHOLLOW_E2E_USERNAME and TRACEHOLLOW_E2E_PASSWORD (see e2e/README.md)");

async function signIn(page: Page) {
  await page.goto("/");
  await page.waitForURL(/\/(setup|login|cases)/);
  if (page.url().endsWith("/setup")) {
    expect(setupToken, "fresh installation: TRACEHOLLOW_E2E_SETUP_TOKEN is required").not.toBe("");
    await page.getByLabel("Setup token").fill(setupToken);
    await page.getByLabel("Administrator username").fill(username);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByLabel("Repeat password").fill(password);
    await page.getByRole("button", { name: "Create administrator" }).click();
    await page.waitForURL(/\/login/);
  }
  if (/\/login/.test(page.url())) {
    await page.getByLabel("Username").fill(username);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await page.waitForURL(/\/cases$/);
}

test("analyst works a synthetic case from creation to deletion", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const title = `Örnek altyapı incelemesi ${Date.now()}`;

  await signIn(page);

  await test.step("create a case", async () => {
    await page.getByLabel("Title").fill(title);
    await page.getByLabel("Tags").fill("synthetic-demo, e2e");
    await page.getByLabel("Purpose").fill("Browser verification of the Phase 1 workflow.");
    await page.getByLabel("Scope").fill("Synthetic fixture data and analyst-provided text only.");
    await page.getByRole("button", { name: "Create case" }).click();
    await page.waitForURL(/\/cases\/[0-9a-f-]{36}$/);
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
  });
  const caseUrl = page.url();

  await test.step("add an organization and a domain", async () => {
    await page.getByRole("link", { name: "Entities", exact: true }).click();
    await page.getByLabel("Entity type").selectOption("organization");
    await page.getByLabel("Display name").fill("Örnek A.Ş.");
    await page.getByLabel("Identifier type 1").selectOption("name");
    await page.getByLabel("Identifier value 1").fill("Örnek Anonim Şirketi");
    await page.getByRole("button", { name: "Add entity" }).click();
    await expect(page.getByRole("link", { name: "Örnek A.Ş." })).toBeVisible();
    await page.getByLabel("Entity type").selectOption("domain");
    await page.getByLabel("Display name").fill("ornek.example");
    await page.getByLabel("Identifier type 1").selectOption("domain");
    await page.getByLabel("Identifier value 1").fill("ORNEK.example");
    await page.getByRole("button", { name: "Add entity" }).click();
    await expect(page.getByRole("link", { name: "ornek.example" })).toBeVisible();
  });

  await test.step("import hostile text and JSON evidence", async () => {
    await page.getByRole("link", { name: "Evidence", exact: true }).click();
    await page
      .getByLabel("Paste content")
      .fill(`<img src=x onerror="window.__e2ePwned = true"><script>window.__e2ePwned = true</script> Kayıt: ornek.example`);
    await page.getByLabel("Name for pasted content").fill("../../kayit‮txt.html");
    await page.getByLabel("Title").fill("Registry extract (analyst paste)");
    await page.getByLabel("Import origin (required)").fill("Synthetic registry text written for the browser test");
    await page.getByRole("button", { name: "Import evidence" }).click();
    await expect(page.getByRole("status").first()).toContainText("reduced to a safe display name");

    await page.getByLabel("Content kind").selectOption("json");
    await page.getByLabel("File").setInputFiles({
      name: "whois.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify({ domain: "ornek.example", registrant: "Örnek A.Ş." })),
    });
    await page.getByLabel("Import origin (required)").fill("Synthetic WHOIS-style record");
    await page.getByRole("button", { name: "Import evidence" }).click();
    await expect(page.getByRole("table").getByRole("link", { name: "whois.json" })).toBeVisible();

    await page.getByRole("table").getByRole("link", { name: "Registry extract (analyst paste)" }).click();
    const preview = page.getByLabel("Evidence content preview");
    await expect(preview).toContainText("<script>window.__e2ePwned = true</script>");
    await expect(page.getByText("Hash verified")).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { __e2ePwned?: boolean }).__e2ePwned)).toBeUndefined();
  });

  await test.step("relate the entities with supporting evidence and review the relationship", async () => {
    await page.goto(`${caseUrl}/relationships`);
    await page.getByLabel("Predicate").fill("owns");
    await page.getByLabel("Source entity").selectOption({ label: "Örnek A.Ş. (Organization)" });
    await page.getByLabel("Target entity").selectOption({ label: "ornek.example (Domain)" });
    await page.getByLabel("Supporting evidence").selectOption({ label: "Registry extract (analyst paste)" });
    await page.getByRole("button", { name: "Add relationship" }).click();
    await page.getByRole("button", { name: "owns" }).click();
    const panel = page.getByRole("complementary", { name: "Relationship details" });
    await expect(panel.getByRole("link", { name: "Registry extract (analyst paste)" })).toBeVisible();
    await panel.getByLabel("Review status").selectOption("accepted");
    await panel.getByLabel("Rationale").fill("Registry extract names the organization as registrant.");
    await panel.getByRole("button", { name: "Save decision" }).click();
    await expect(panel.getByText(/Unreviewed → Accepted/)).toBeVisible();
  });

  await test.step("run a saved query twice and inspect both executions", async () => {
    await page.goto(`${caseUrl}/queries`);
    await page.getByLabel("Name", { exact: true }).fill("Username candidates (synthetic)");
    await page.getByLabel("Input value").fill("şule.yılmaz");
    await page.getByLabel("Fixture scenario").selectOption("partial");
    await page.getByRole("button", { name: "Save query" }).click();
    await page.locator("li", { hasText: "Username candidates (synthetic)" }).getByRole("button", { name: "Run", exact: true }).click();
    await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/);
    await expect(page.getByText("Finished with incomplete coverage. Collected data is usable but not complete.")).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByRole("heading", { name: /run #1/ })).toBeVisible();
    const firstRun = page.url();

    await page.getByRole("button", { name: "Run again (new execution)" }).click();
    await page.waitForURL((url) => url.toString() !== firstRun && /\/runs\//.test(url.toString()));
    await expect(page.getByRole("heading", { name: /run #2/ })).toBeVisible();
    await expect(page.getByText("Finished with incomplete coverage. Collected data is usable but not complete.")).toBeVisible({
      timeout: 60_000,
    });

    await page.goto(firstRun);
    await expect(page.getByRole("heading", { name: /run #1/ })).toBeVisible();
    await expect(page.getByText("Synthetic fixture page 1 (run #1, username)")).toBeVisible();
  });

  await test.step("cancel a slow execution and keep collected pages", async () => {
    await page.goto(`${caseUrl}/queries`);
    await page.getByLabel("Name", { exact: true }).fill("Slow synthetic run");
    await page.getByLabel("Input value").fill("slow-subject");
    await page.getByLabel("Fixture scenario").selectOption("slow");
    await page.getByLabel("Maximum pages").fill("10");
    await page.getByRole("button", { name: "Save query" }).click();
    await page.locator("li", { hasText: "Slow synthetic run" }).getByRole("button", { name: "Run", exact: true }).click();
    await page.waitForURL(/\/runs\//);
    await expect(page.getByText("Synthetic fixture page 1 (run #1, username)")).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Cancel execution" }).click();
    await expect(page.getByText("Canceled. Pages collected before cancellation are kept.")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("Synthetic fixture page 1 (run #1, username)")).toBeVisible();
  });

  await test.step("inspect an edge in the relationship graph", async () => {
    await page.goto(`${caseUrl}/graph`);
    await expect(page.getByRole("heading", { name: "Edges in this view" })).toBeVisible();
    await page.getByRole("button", { name: "owns" }).click();
    const panel = page.getByRole("complementary", { name: "Relationship details" });
    await expect(panel.getByText("Analyst assertion").first()).toBeVisible();
    await expect(panel.getByRole("link", { name: "Registry extract (analyst paste)" })).toBeVisible();
  });

  await test.step("download a JSON export with a manifest", async () => {
    await page.goto(`${caseUrl}/settings`);
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("link", { name: "Download JSON export" }).click(),
    ]);
    const exported = JSON.parse(await readFile(await download.path(), "utf8")) as {
      manifest: { case_id: string; synthetic_data_present: boolean; coverage_gaps: { outcome: string }[] };
      data: { evidence: unknown[] };
    };
    expect(caseUrl.endsWith(exported.manifest.case_id)).toBe(true);
    expect(exported.manifest.synthetic_data_present).toBe(true);
    expect(exported.manifest.coverage_gaps.map((gap) => gap.outcome)).toEqual(expect.arrayContaining(["partial", "canceled"]));
    expect(exported.data.evidence.length).toBeGreaterThanOrEqual(4);
  });

  await test.step("delete the case and observe the deletion job", async () => {
    await page.getByLabel(`Type the case title to confirm: ${title}`).fill(title);
    await page.getByRole("button", { name: "Delete this case" }).click();
    await page.waitForURL(/\/cases(\?|$)/);
    const caseId = caseUrl.split("/").pop() ?? "";
    const job = page.getByRole("region", { name: "Deletion jobs" }).getByRole("listitem").filter({ hasText: `case ${caseId.slice(0, 8)}` });
    // The list polls while the job runs; no manual refresh is needed.
    await expect(job.getByText("Completed")).toBeVisible({ timeout: 60_000 });
    await expect(job.getByText(/Removed \d+ evidence record\(s\), \d+ file\(s\)/)).toBeVisible();
    await expect(page.getByRole("link", { name: title })).toHaveCount(0);
    const response = await page.goto(caseUrl);
    expect(response?.status()).toBe(404);
  });

  expect(pageErrors).toEqual([]);
});
