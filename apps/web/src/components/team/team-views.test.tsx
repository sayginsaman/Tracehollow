import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Account, AppNotification, Destination, Member } from "@/lib/workspace-types";
import { SESSION, TEST_CASE, VIEWER_CASE, VIEWER_SESSION, mockApi, renderInCase, renderWithSession } from "@/test/workspace";

import { AccountsView } from "../admin/AccountsView";
import { DestinationsView } from "../admin/DestinationsView";
import { CaseSettings } from "../cases/CaseSettings";
import { ReportBuilder } from "../cases/ReportBuilder";
import { NotificationsView } from "../notifications/NotificationsView";
import { AppShell } from "../shell/AppShell";
import { CaseMembersView } from "./CaseMembersView";
import { MembersManager } from "./MembersManager";

const navigation = vi.hoisted(() => ({ pathname: "/overview" }));
vi.mock("next/navigation", async () => {
  const actual = await vi.importActual<typeof import("next/navigation")>("next/navigation");
  return { ...actual, useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }), usePathname: () => navigation.pathname };
});

const API = `/api/v1/cases/${TEST_CASE.id}`;

afterEach(() => {
  vi.restoreAllMocks();
});

function member(overrides: Partial<Member>): Member {
  return {
    user_id: "u1",
    username: "analyst",
    account_role: "analyst",
    account_active: true,
    membership_role: "analyst",
    effective_role: "analyst",
    added_at: "2026-09-15T10:00:00Z",
    updated_at: "2026-09-15T10:00:00Z",
    ...overrides,
  };
}

function bodyOf(fetchMock: ReturnType<typeof mockApi>, suffix: string, method = "POST"): unknown {
  const call = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith(suffix) && (init?.method ?? "GET") === method);
  return call ? JSON.parse(String(call[1]?.body)) : undefined;
}

describe("MembersManager", () => {
  it("protects the last analyst and adds members with a role", async () => {
    const fetchMock = mockApi({
      [`${API}/members`]: [
        member({ user_id: "u1", username: "analyst" }),
        member({ user_id: "u2", username: "okur", account_role: "viewer", membership_role: "viewer", effective_role: "viewer" }),
        member({ user_id: "u3", username: "ayrılan", account_active: false }),
      ],
      "/api/v1/accounts": [{ id: "u4", username: "zeynep", role: "viewer" }],
    });
    const user = userEvent.setup();
    renderWithSession(<MembersManager membersPath={`${API}/members`} canManage accountSearch currentUserId="u1" />);

    const table = await screen.findByRole("table", { name: "Case members" });
    const rows = within(table).getAllByRole("row");
    expect(within(rows[1]!).getByText("Last analyst")).toBeInTheDocument();
    expect(within(rows[1]!).getByRole("combobox", { name: "Role of analyst" })).toBeDisabled();
    expect(within(rows[2]!).getByRole("option", { name: "Analyst" })).toBeDisabled();
    expect(within(rows[3]!).getByText("No access")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Add an account"), "zeynep");
    const form = screen.getByRole("button", { name: "Add member" }).closest("form") as HTMLFormElement;
    // A viewer account can only be a viewer member.
    await waitFor(() => expect(within(form).getByText("Viewer account")).toBeInTheDocument());
    expect(within(form).getByRole("option", { name: "Analyst" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Add member" }));
    await waitFor(() => expect(bodyOf(fetchMock, `${API}/members`)).toEqual({ username: "zeynep", role: "viewer" }));
  });

  it("shows a viewer the member list without controls", async () => {
    mockApi({ [`${API}/members`]: [member({})] });
    renderInCase(<CaseMembersView />, VIEWER_CASE, VIEWER_SESSION);
    expect(await screen.findByRole("table", { name: "Case members" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});

describe("AccountsView", () => {
  it("warns when a role change leaves cases without an active analyst", async () => {
    const account: Account = {
      id: "u2",
      username: "eski-analist",
      role: "analyst",
      is_active: true,
      created_at: "2026-09-10T10:00:00Z",
      last_login_at: null,
      locked_until: null,
      case_count: 2,
      analyst_case_count: 1,
    };
    const fetchMock = mockApi({
      "/api/v1/admin/accounts/u2": { account: { ...account, role: "viewer" }, cases_without_active_analyst: ["c1"] },
      "/api/v1/admin/accounts?": { items: [account], total: 1, limit: 50, offset: 0 },
    });
    const user = userEvent.setup();
    renderWithSession(<AccountsView />);
    await user.selectOptions(await screen.findByRole("combobox", { name: "Account role of eski-analist" }), "viewer");
    expect(await screen.findByText("1 case has no active analyst now")).toBeInTheDocument();
    expect(bodyOf(fetchMock, "/api/v1/admin/accounts/u2", "PATCH")).toEqual({ role: "viewer" });
  });

  it("is not offered to accounts without administration permission", () => {
    renderWithSession(<AccountsView />, VIEWER_SESSION);
    expect(screen.getByText("Administrator access required")).toBeInTheDocument();
  });
});

describe("DestinationsView", () => {
  const destination: Destination = {
    id: "d1",
    name: "SOC receiver",
    url: "https://hooks.ornek.example/tracehollow",
    host: "hooks.ornek.example",
    enabled: false,
    event_types: ["change_detected"],
    max_per_minute: 30,
    signing: true,
    subscriptions: 0,
    last_delivery_at: null,
    last_status: null,
    consecutive_failures: 0,
    adapter_enabled: true,
    created_at: "2026-09-17T10:00:00Z",
    updated_at: "2026-09-17T10:00:00Z",
  };

  it("explains that external notifications are off", async () => {
    mockApi({
      "/api/v1/admin/notification-destinations/status": { adapter_enabled: false, setting: "TRACEHOLLOW_NOTIFICATIONS_EXTERNAL_ENABLED" },
      "/api/v1/admin/notification-destinations": [],
    });
    renderWithSession(<DestinationsView />);
    expect(await screen.findByText("External notifications are off")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New destination" })).toBeNull();
  });

  it("enables a destination only after the payload preview and the typed host", async () => {
    const fetchMock = mockApi({
      "/api/v1/admin/notification-destinations/status": { adapter_enabled: true, setting: "TRACEHOLLOW_NOTIFICATIONS_EXTERNAL_ENABLED" },
      "/api/v1/admin/notification-destinations/d1/preview": {
        method: "POST",
        url: destination.url,
        headers: { "content-type": "application/json" },
        body: { event_id: "e1", summary: { new: 2 } },
        notes: ["Receivers must deduplicate on event_id: a delivery can arrive more than once."],
      },
      "/api/v1/admin/notification-destinations/d1/enable": { ...destination, enabled: true },
      "/api/v1/admin/notification-destinations/d1/deliveries": { items: [], total: 0, limit: 25, offset: 0 },
      "/api/v1/admin/notification-destinations": [destination],
    });
    const user = userEvent.setup();
    renderWithSession(<DestinationsView />);
    const enable = await screen.findByRole("button", { name: "Enable deliveries" });
    await user.type(screen.getByLabelText("Type hooks.ornek.example to confirm"), "hooks.ornek.example");
    expect(enable).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Preview payload" }));
    expect(await screen.findByText(/deduplicate on event_id/)).toBeInTheDocument();
    expect(enable).toBeEnabled();
    await user.click(enable);
    await waitFor(() => expect(bodyOf(fetchMock, "/d1/enable")).toEqual({ confirm_host: "hooks.ornek.example" }));
  });
});

describe("NotificationsView", () => {
  it("lists unread notifications with their case and marks one read", async () => {
    const notification: AppNotification = {
      id: "n1",
      case_id: TEST_CASE.id,
      case_title: "Örnek vaka",
      monitor_id: "m1",
      event_id: "e1",
      event_type: "change_detected",
      severity: "info",
      title: "Daily feed: 2 new, 1 changed",
      body: "Compared with the previous comparable collection.",
      link: `/cases/${TEST_CASE.id}/changes/cs1`,
      created_at: "2026-09-17T09:05:00Z",
      read_at: null,
    };
    const fetchMock = mockApi({
      "/api/v1/notifications/n1/read": { ...notification, read_at: "2026-09-17T10:00:00Z" },
      "/api/v1/notifications?": { items: [notification], total: 1, limit: 25, offset: 0 },
    });
    const user = userEvent.setup();
    renderWithSession(<NotificationsView />);
    expect(await screen.findByRole("link", { name: "Daily feed: 2 new, 1 changed" })).toHaveAttribute("href", `/cases/${TEST_CASE.id}/changes/cs1`);
    expect(screen.getByText("· Örnek vaka")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Mark read" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/v1/notifications/n1/read" && init?.method === "POST")).toBe(true));
  });
});

describe("viewer role", () => {
  it("replaces exports, reports and deletion with an explanation", async () => {
    mockApi({
      [`${API}/budgets`]: { budgets: [], usage: [], measurement: "Requests are counted when Tracehollow sends them." },
      [`${API}/retention/jobs`]: { items: [], total: 0, limit: 5, offset: 0 },
      [`${API}/retention`]: {
        active: false,
        collected_results_max_age_days: null,
        imported_evidence_max_age_days: null,
        version: 0,
        activated_at: null,
        last_applied_at: null,
        monitor_rules: [],
      },
    });
    renderInCase(<CaseSettings />, VIEWER_CASE, VIEWER_SESSION);
    expect(await screen.findByText("Exports are for analysts")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Download JSON export" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete this case" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Archive case" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add limit" })).toBeNull();
    expect(await screen.findByText("Off")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Preview what would be removed" })).toBeNull();

    renderInCase(<ReportBuilder />, VIEWER_CASE, VIEWER_SESSION);
    expect(screen.getAllByText("Exports are for analysts")).toHaveLength(2);
  });

  it("shows the view-only badge, and administration only to administrators", async () => {
    mockApi({ "/api/v1/notifications/unread-count": { unread: 3 } });
    navigation.pathname = `/cases/${TEST_CASE.id}`;
    renderInCase(
      <AppShell>
        <p>Page</p>
      </AppShell>,
      VIEWER_CASE,
      VIEWER_SESSION,
    );
    expect(screen.getByText("View only")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Notifications, 3 unread" })).toHaveAttribute("href", "/notifications");
    expect(screen.queryByRole("link", { name: "Accounts" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Audit log" })).toBeNull();
  });

  it("lists administration pages for an administrator outside a case", () => {
    mockApi({ "/api/v1/notifications/unread-count": { unread: 0 } });
    navigation.pathname = "/admin/accounts";
    renderWithSession(
      <AppShell>
        <p>Page</p>
      </AppShell>,
      SESSION,
    );
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Accounts" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Notification destinations" })).toBeInTheDocument();
  });
});
