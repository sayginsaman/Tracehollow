import { expect, test, type Page } from "@playwright/test";

/**
 * Phase 5 in a real browser against scripts/verify-phase5.sh --e2e: an analyst creates a paused
 * monitor for the controlled feed, runs it and opens the change history, adds a viewer; the viewer
 * sees the case read-only; the administrator reaches accounts, case access and destinations but
 * not the case content. Uses only synthetic data and creates its own case.
 */

const analyst = { username: process.env.TRACEHOLLOW_E2E_USERNAME ?? "", password: process.env.TRACEHOLLOW_E2E_PASSWORD ?? "" };
const admin = { username: process.env.TRACEHOLLOW_E2E_ADMIN_USERNAME ?? "", password: process.env.TRACEHOLLOW_E2E_ADMIN_PASSWORD ?? "" };
const viewerName = process.env.TRACEHOLLOW_E2E_VIEWER_USERNAME ?? "";
// Every non-administrator acceptance account shares the analyst's generated password.
const viewer = { username: viewerName, password: analyst.password };
const FEED = "http://fixture-site:8080/monitor/feed";

test.skip(
  !analyst.username || !analyst.password || !admin.username || !admin.password || !viewerName,
  "Set the analyst, administrator and viewer variables (see e2e/README.md)",
);

async function signIn(page: Page, account: { username: string; password: string }) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(account.username);
  await page.getByLabel("Password").fill(account.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/overview$/);
}

async function signOut(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL(/\/login/);
}

test("analyst monitors a feed, viewer reads it, administrator manages access without case content", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const title = `İzleme tarayıcı ${Date.now()}`;
  let casePath = "";

  await signIn(page, analyst);

  await test.step("create a case and a saved query for the controlled feed", async () => {
    await page.getByRole("link", { name: "Cases", exact: true }).click();
    await page.getByRole("button", { name: "New case" }).click();
    await page.getByLabel("Title").fill(title);
    await page.getByLabel("Purpose").fill("Browser verification of monitoring (controlled fixture).");
    await page.getByRole("button", { name: "Create case" }).click();
    await page.waitForURL(/\/cases\/[0-9a-f-]{36}$/);
    casePath = new URL(page.url()).pathname;
    await page.getByRole("link", { name: "Queries & runs", exact: true }).click();
    await page.getByLabel("Source").selectOption("rss.feed");
    await page.getByLabel("Name", { exact: true }).fill("İzleme akışı");
    await page.getByLabel("Input value").fill(FEED);
    await page.getByRole("button", { name: "Save query" }).click();
    await expect(page.locator("li", { hasText: "İzleme akışı" })).toBeVisible();
  });

  await test.step("create a paused monitor; enabling needs the recurring-collection confirmation", async () => {
    await page.getByRole("link", { name: "Monitors", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Monitors", level: 1 })).toBeVisible();
    await expect(page.getByText("No monitors yet")).toBeVisible();
    await page.getByRole("button", { name: "New monitor" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill("Akış izleme");
    await page.getByLabel("Enable now").check();
    const create = page.getByRole("button", { name: "Create and enable" });
    await expect(create).toBeDisabled();
    await page.getByLabel("Enable now").uncheck();
    await page.getByRole("button", { name: "Create paused monitor" }).click();
    const row = page.getByRole("listitem").filter({ has: page.getByRole("link", { name: "Akış izleme" }) });
    await expect(row.getByText("Paused", { exact: true })).toBeVisible();
    await expect(row.getByText(/^Created paused\. Enable it/)).toBeVisible();
    await page.getByRole("link", { name: "Akış izleme" }).click();
  });

  await test.step("run it and see the baseline in the occurrence history", async () => {
    await expect(page.getByRole("heading", { name: "Akış izleme", level: 1 })).toBeVisible();
    await expect(page.getByText("Wall-clock time", { exact: false }).or(page.getByText("elapsed time", { exact: false })).first()).toBeVisible();
    await page.getByRole("button", { name: "Run now" }).click();
    await expect(async () => {
      await page.reload();
      await expect(page.getByRole("link", { name: /^Baseline/ })).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 120_000 });
    await page.getByRole("link", { name: /^Baseline/ }).click();
    await expect(page.getByRole("heading", { name: "Changes since the previous collection" })).toBeVisible();
    await expect(page.getByText(/becomes the baseline/)).toBeVisible();
  });

  await test.step("add the viewer as a member", async () => {
    await page.goto(`${casePath}/members`);
    await page.getByLabel("Add an account").fill(viewer.username);
    await page.getByRole("button", { name: "Add member" }).click();
    await expect(page.getByRole("table", { name: "Case members" }).getByText(viewer.username)).toBeVisible();
    await expect(page.getByText("Last analyst")).toBeVisible();
  });

  await test.step("notifications inbox and keyboard access to the bell", async () => {
    await page.getByRole("link", { name: /^Notifications/ }).first().focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Notifications", level: 1 })).toBeVisible();
  });

  await signOut(page);
  await signIn(page, viewer);

  await test.step("the viewer reads the case without change controls", async () => {
    await page.goto(casePath);
    await expect(page.getByText("View only")).toBeVisible();
    await page.goto(`${casePath}/monitors`);
    await expect(page.getByRole("link", { name: "Akış izleme" })).toBeVisible();
    await expect(page.getByRole("button", { name: "New monitor" })).toHaveCount(0);
    await page.goto(`${casePath}/settings`);
    await expect(page.getByText("Exports are for analysts")).toBeVisible();
    await expect(page.getByRole("button", { name: "Delete this case" })).toHaveCount(0);
    await page.goto(`${casePath}/reports`);
    await expect(page.getByText("Exports are for analysts")).toBeVisible();
  });

  await signOut(page);
  await signIn(page, admin);

  await test.step("the administrator manages access but cannot open the case", async () => {
    await page.getByRole("link", { name: "Accounts", exact: true }).click();
    await expect(page.getByRole("table", { name: "Accounts" }).getByText(viewer.username)).toBeVisible();
    await page.getByRole("link", { name: "Case access", exact: true }).click();
    await expect(page.getByText(title)).toBeVisible();
    await page.getByRole("link", { name: "Notification destinations", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Notification destinations", level: 1 })).toBeVisible();
    await page.goto(casePath);
    await expect(page.getByText(/does not exist, or you do not have access to it/)).toBeVisible();
  });

  expect(pageErrors).toEqual([]);
});
