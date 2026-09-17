/**
 * Captures the Phase 5 screens (monitoring, changes, notifications, members, administration, case
 * policies, STIX import) as an analyst, a viewer and an administrator, and reports page-level
 * horizontal overflow and page errors. For design reviews against the disposable stack of
 * scripts/verify-phase5.sh --keep filled by scripts/seed_phase5_demo.py; it signs in and reads.
 *
 *   TRACEHOLLOW_SCREENS_BASE_URL=http://localhost:3160 TRACEHOLLOW_SCREENS_PASSWORD=<demo password> \
 *   TRACEHOLLOW_SCREENS_ADMIN_PASSWORD=<acceptance admin password> \
 *   node scripts/capture-phase5-screens.mjs <output-dir> [widths] [route-filter]
 */
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { chromium } from "@playwright/test";

const base = process.env.TRACEHOLLOW_SCREENS_BASE_URL ?? "http://localhost:3160";
const demoPassword = process.env.TRACEHOLLOW_SCREENS_PASSWORD ?? "";
const adminPassword = process.env.TRACEHOLLOW_SCREENS_ADMIN_PASSWORD ?? "";
const [outDir = "screens-phase5", widthArg = "1440,390", filter = ""] = process.argv.slice(2);
const widths = widthArg.split(",").map(Number);
const colorScheme = process.env.TRACEHOLLOW_SCREENS_COLOR_SCHEME ?? "light";
if (!demoPassword || !adminPassword) {
  console.error("Set TRACEHOLLOW_SCREENS_PASSWORD and TRACEHOLLOW_SCREENS_ADMIN_PASSWORD.");
  process.exit(2);
}

const browser = await chromium.launch({ executablePath: process.env.TRACEHOLLOW_E2E_CHROMIUM_EXECUTABLE || undefined });
const report = [];
const pageErrors = [];
await mkdir(outDir, { recursive: true });

async function session(username, password) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme, reducedMotion: "reduce" });
  const page = await context.newPage();
  page.on("pageerror", (error) => pageErrors.push(`${username} ${page.url()}: ${error.message}`));
  await page.goto(`${base}/login`);
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  const json = async (pathname) => {
    const response = await page.request.get(new URL(pathname, base).toString());
    if (!response.ok()) throw new Error(`${pathname}: HTTP ${response.status()}`);
    return response.json();
  };
  return { page, json, close: () => context.close() };
}

async function capture(page, prefix, routes) {
  for (const route of routes.filter((item) => !filter || item.name.includes(filter))) {
    for (const width of widths) {
      await page.setViewportSize({ width, height: width < 500 ? 844 : 900 });
      const response = await page.goto(`${base}${route.url}`, { waitUntil: "networkidle" });
      await page.waitForTimeout(route.settle ?? 700);
      const metrics = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth, finalPath: location.pathname }));
      const file = path.join(outDir, `${prefix}-${route.name}-${width}.jpg`);
      await page.screenshot({ path: file, fullPage: true, type: "jpeg", quality: 74 });
      const overflow = metrics.scrollWidth > metrics.innerWidth;
      report.push({ role: prefix, route: route.name, width, status: response?.status(), ...metrics, overflow });
      console.log(`${prefix}/${route.name} @${width}: ${response?.status()} ${metrics.finalPath}${overflow ? ` OVERFLOW ${metrics.scrollWidth}px` : ""}`);
    }
  }
}

const analyst = await session("demo-analyst-p5", demoPassword);
const cases = (await analyst.json("/api/v1/cases?limit=100")).items;
const demo = cases.find((item) => item.title.startsWith("[Synthetic demo] Örnek Lojistik duyuru izleme"));
const exchange = cases.find((item) => item.title.startsWith("[Synthetic demo] Paylaşılan STIX"));
if (!demo || !exchange) throw new Error("Seed the demo first (scripts/seed_phase5_demo.py).");
const c = `/cases/${demo.id}`;
const api = `/api/v1/cases/${demo.id}`;
const monitors = (await analyst.json(`${api}/monitors?limit=10`)).items;
const daily = monitors.find((item) => item.name === "Günlük duyuru izleme");
const changeSets = (await analyst.json(`${api}/change-sets?limit=20`)).items;
const detected = changeSets.find((item) => item.status === "changes_detected");
const unknown = changeSets.find((item) => item.status === "unknown");
const runs = (await analyst.json(`${api}/runs?limit=20`)).items;
const partial = runs.find((item) => item.status === "partial");

await capture(analyst.page, "analyst", [
  { name: "monitors", url: `${c}/monitors` },
  { name: "monitor-new", url: `${c}/monitors?new=1` },
  { name: "monitor-detail", url: `${c}/monitors/${daily.id}` },
  { name: "changes-detected", url: `${c}/changes/${detected.id}` },
  { name: "changes-unknown", url: `${c}/changes/${unknown.id}` },
  { name: "run-partial-changes", url: `${c}/runs/${partial.id}` },
  { name: "members", url: `${c}/members` },
  { name: "case-audit", url: `${c}/audit` },
  { name: "case-settings", url: `${c}/settings` },
  { name: "imports-stix", url: `/cases/${exchange.id}/imports` },
  { name: "notifications", url: "/notifications" },
  { name: "preferences", url: "/preferences" },
  { name: "monitors-empty", url: `/cases/${exchange.id}/monitors` },
]);
await analyst.close();

const viewer = await session("demo-viewer-p5", demoPassword);
await capture(viewer.page, "viewer", [
  { name: "case-overview", url: c },
  { name: "monitor-detail", url: `${c}/monitors/${daily.id}` },
  { name: "case-settings", url: `${c}/settings` },
  { name: "reports", url: `${c}/reports` },
  { name: "ai", url: `${c}/ai` },
  { name: "notifications-empty", url: "/notifications" },
]);
await viewer.close();

const admin = await session("acceptance-admin", adminPassword);
await capture(admin.page, "admin", [
  { name: "accounts", url: "/admin/accounts" },
  { name: "case-access", url: "/admin/case-access" },
  { name: "destinations", url: "/admin/notification-destinations" },
  { name: "audit", url: "/admin/audit" },
  { name: "case-not-member", url: c },
]);
await admin.close();

await writeFile(path.join(outDir, "report.json"), JSON.stringify({ pages: report, pageErrors }, null, 2));
if (pageErrors.length) console.log(`Page errors:\n${pageErrors.join("\n")}`);
await browser.close();
