import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TEST_CASE, renderInCase, renderWithSession } from "@/test/workspace";

import { AppShell } from "./AppShell";
import { breadcrumbs, caseNavigation, isCurrent } from "./navigation";

const navigation = vi.hoisted(() => ({ pathname: "/overview" }));

vi.mock("next/navigation", async () => {
  const actual = await vi.importActual<typeof import("next/navigation")>("next/navigation");
  return { ...actual, useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }), usePathname: () => navigation.pathname };
});

const BASE = `/cases/${TEST_CASE.id}`;

describe("breadcrumbs", () => {
  it("names global pages, the current case and a detail record", () => {
    expect(breadcrumbs("/sources", null, null)).toEqual([{ label: "Sources" }]);
    expect(breadcrumbs(BASE, "Örnek vaka", null)).toEqual([{ label: "Cases", href: "/cases" }, { label: "Örnek vaka" }]);
    expect(breadcrumbs(`${BASE}/imports`, "Örnek vaka", null)).toEqual([
      { label: "Cases", href: "/cases" },
      { label: "Örnek vaka", href: BASE },
      { label: "Imports", href: undefined },
    ]);
    // Runs belong to Queries & runs; an unnamed detail falls back to a generic label.
    expect(breadcrumbs(`${BASE}/runs/r1`, "Örnek vaka", null).slice(2)).toEqual([
      { label: "Queries & runs", href: `${BASE}/queries` },
      { label: "Run" },
    ]);
    expect(breadcrumbs(`${BASE}/evidence/e1`, "Örnek vaka", "Kayıt özeti").at(-1)).toEqual({ label: "Kayıt özeti" });
  });

  it("marks run pages as part of Queries & runs and keeps the case overview exact", () => {
    const groups = caseNavigation(BASE);
    const items = groups.flatMap((group) => group.items);
    const queries = items.find((item) => item.label === "Queries & runs");
    const overview = items.find((item) => item.label === "Case overview");
    if (!queries || !overview) throw new Error("navigation items missing");
    expect(isCurrent(queries, `${BASE}/runs/r1`)).toBe(true);
    expect(isCurrent(overview, `${BASE}/evidence`)).toBe(false);
    expect(groups.map((group) => group.label)).toEqual(["", "Collect", "Examine", "Analyze", "Report"]);
  });
});

describe("AppShell", () => {
  it("shows global navigation only outside a case", () => {
    navigation.pathname = "/overview";
    renderWithSession(
      <AppShell>
        <p>Page</p>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).queryByRole("link", { name: "Evidence" })).toBeNull();
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Overview");
  });

  it("keeps the current case, its status and grouped sections visible inside a case", () => {
    navigation.pathname = `${BASE}/runs/r1`;
    renderInCase(
      <AppShell>
        <p>Page</p>
      </AppShell>,
      { ...TEST_CASE, title: "Örnek İnceleme", status: "archived" },
    );
    const group = screen.getByRole("group", { name: "Örnek İnceleme" });
    expect(within(group).getByText("Archived")).toBeInTheDocument();
    expect(within(group).getByRole("link", { name: "Queries & runs" })).toHaveAttribute("aria-current", "page");
    expect(within(group).getByRole("link", { name: "Evidence" })).toHaveAttribute("href", `${BASE}/evidence`);
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Örnek İnceleme" })).toHaveAttribute("href", BASE);
  });

  it("opens the navigation drawer, closes it with Escape and returns focus to the menu button", async () => {
    navigation.pathname = "/sources";
    renderWithSession(
      <AppShell>
        <p>Page</p>
      </AppShell>,
    );
    const user = userEvent.setup();
    const menu = screen.getByRole("button", { name: "Open navigation" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    await user.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(menu).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(menu).toHaveFocus());
  });
});
