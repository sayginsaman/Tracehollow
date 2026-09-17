/**
 * Captures every user-facing route of a running Tracehollow stack at several viewport widths and
 * reports page-level horizontal overflow. For design reviews against a disposable stack filled by
 * scripts/seed_demo_workspace.py; it signs in, reads and never changes records.
 *
 *   TRACEHOLLOW_SCREENS_BASE_URL=http://localhost:3150 TRACEHOLLOW_SCREENS_USERNAME=demo-analyst \
 *   TRACEHOLLOW_SCREENS_PASSWORD=... node scripts/capture-screens.mjs <output-dir> [widths] [route-filter]
 */
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { chromium } from "@playwright/test";

const base = process.env.TRACEHOLLOW_SCREENS_BASE_URL ?? "http://localhost:3150";
const username = process.env.TRACEHOLLOW_SCREENS_USERNAME ?? "";
const password = process.env.TRACEHOLLOW_SCREENS_PASSWORD ?? "";
const [outDir = "screens", widthArg = "1440,1280,768,390", filter = ""] = process.argv.slice(2);
const widths = widthArg.split(",").map(Number);
const colorScheme = process.env.TRACEHOLLOW_SCREENS_COLOR_SCHEME ?? "light";
// 2 with a 720px width emulates 200% browser zoom on a 1440px screen.
const deviceScaleFactor = Number(process.env.TRACEHOLLOW_SCREENS_SCALE ?? "1");
const pageErrors = [];

if (!username || !password) {
  console.error("Set TRACEHOLLOW_SCREENS_USERNAME and TRACEHOLLOW_SCREENS_PASSWORD.");
  process.exit(2);
}

const browser = await chromium.launch({ executablePath: process.env.TRACEHOLLOW_E2E_CHROMIUM_EXECUTABLE || undefined });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme, reducedMotion: "reduce", deviceScaleFactor });
const page = await context.newPage();
page.on("pageerror", (error) => pageErrors.push(`${page.url()}: ${error.message}`));

async function json(pathname) {
  const response = await page.request.get(new URL(pathname, base).toString());
  if (!response.ok()) throw new Error(`${pathname}: HTTP ${response.status()}`);
  return response.json();
}

await page.goto(`${base}/login`);
await page.waitForLoadState("networkidle");
const routes = [{ name: "login", url: "/login", signedOut: true }];
await page.getByLabel("Username").fill(username);
await page.getByLabel("Password").fill(password);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL((url) => !url.pathname.startsWith("/login"));

const cases = (await json("/api/v1/cases?limit=100")).items;
const main = cases.find((item) => item.title.startsWith("[Synthetic demo] Örnek Lojistik"));
if (!main) throw new Error("Seed the demo workspace first (scripts/seed_demo_workspace.py).");
const api = `/api/v1/cases/${main.id}`;
const c = `/cases/${main.id}`;
const entities = (await json(`${api}/entities?limit=100`)).items;
const evidence = (await json(`${api}/evidence?limit=100`)).items;
const runs = (await json(`${api}/runs?limit=100`)).items;
const conversations = (await json(`${api}/ai/conversations?limit=10`)).items;
const byTitle = (text) => evidence.find((item) => (item.title ?? "").includes(text));
const byOutcome = (status) => runs.find((item) => item.status === status);
const domain = entities.find((item) => item.entity_type === "domain");
const instagram = entities.find((item) => item.display_name.endsWith("on Instagram"));
const manual = entities.find((item) => item.display_name === "ornek.magaza elsewhere");

routes.push(
  { name: "home", url: "/" },
  { name: "cases", url: "/cases" },
  { name: "case-overview", url: c },
  { name: "entities", url: `${c}/entities` },
  { name: "entity-detail", url: `${c}/entities/${domain.id}` },
  { name: "relationships", url: `${c}/relationships` },
  { name: "graph", url: `${c}/graph`, settle: 2500 },
  { name: "evidence", url: `${c}/evidence` },
  { name: "evidence-text", url: `${c}/evidence/${byTitle("Kayıt özeti").id}` },
  { name: "evidence-chat", url: `${c}/evidence/${byTitle("chat text").id}` },
  { name: "evidence-ocr", url: `${c}/evidence/${evidence.find((item) => item.collection_metadata?.text_origin === "ocr").id}` },
  { name: "queries", url: `${c}/queries` },
  { name: "run-completed", url: `${c}/runs/${byOutcome("completed").id}` },
  { name: "run-partial", url: `${c}/runs/${byOutcome("partial").id}` },
  { name: "run-failed", url: `${c}/runs/${byOutcome("failed").id}` },
  { name: "imports", url: `${c}/imports` },
  { name: "timeline", url: `${c}/timeline` },
  {
    name: "compare",
    url: instagram && manual ? `${c}/compare?entity_id=${instagram.id}&entity_id=${manual.id}` : `${c}/compare`,
  },
  { name: "ai", url: `${c}/ai` },
  ...(conversations[0] ? [{ name: "ai-conversation", url: `${c}/ai/conversations/${conversations[0].id}` }] : []),
  { name: "reports", url: `${c}/reports` },
  { name: "case-settings", url: `${c}/settings` },
  { name: "sources", url: "/sources" },
  { name: "status", url: "/status" },
  { name: "preferences", url: "/preferences" },
  { name: "overview", url: "/overview" },
  { name: "not-found", url: "/cases/00000000-0000-4000-8000-000000000000" },
);

await mkdir(outDir, { recursive: true });
const report = [];
for (const route of routes.filter((item) => !filter || item.name.includes(filter))) {
  const target = route.signedOut ? await (await browser.newContext({ colorScheme, reducedMotion: "reduce" })).newPage() : page;
  for (const width of widths) {
    await target.setViewportSize({ width, height: width < 500 ? 844 : 900 });
    const response = await target.goto(`${base}${route.url}`, { waitUntil: "networkidle" });
    await target.waitForTimeout(route.settle ?? 600);
    const metrics = await target.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      finalPath: location.pathname,
      title: document.title,
    }));
    const file = path.join(outDir, `${route.name}-${width}.jpg`);
    await target.screenshot({ path: file, fullPage: true, type: "jpeg", quality: 72 });
    report.push({ route: route.name, width, status: response?.status(), ...metrics, overflow: metrics.scrollWidth > metrics.innerWidth });
    console.log(`${route.name} @${width}: ${response?.status()} ${metrics.finalPath}${metrics.scrollWidth > metrics.innerWidth ? ` OVERFLOW ${metrics.scrollWidth}px` : ""}`);
  }
}
await writeFile(path.join(outDir, "report.json"), JSON.stringify({ pages: report, pageErrors }, null, 2));
if (pageErrors.length > 0) console.log(`Page errors:\n${pageErrors.join("\n")}`);
await browser.close();
