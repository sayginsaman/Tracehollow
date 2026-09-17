import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { describeSchedule, describeUsage, occurrenceSummary, scheduleTimeRule } from "@/lib/monitoring";
import type { BudgetUsage, ChangeSetDetail, Monitor, Occurrence } from "@/lib/workspace-types";
import { TEST_CASE, VIEWER_CASE, mockApi, renderInCase } from "@/test/workspace";

import { ChangeSetView } from "./ChangeSetView";
import { MonitorDetailView } from "./MonitorDetailView";
import { MonitorsView } from "./MonitorsView";

vi.mock("next/navigation", async () => {
  const actual = await vi.importActual<typeof import("next/navigation")>("next/navigation");
  return { ...actual, useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }), usePathname: () => "/" };
});

const API = `/api/v1/cases/${TEST_CASE.id}`;

afterEach(() => {
  vi.restoreAllMocks();
});

const USAGE: BudgetUsage = {
  scope_type: "monitor",
  metric: "requests",
  period: "day",
  period_start: "2026-09-17T00:00:00Z",
  period_end: "2026-09-18T00:00:00Z",
  limit_units: 100,
  reserved_units: 2,
  consumed_units: 30,
  estimated_units: 5,
  remaining_units: 63,
  exhausted: false,
  denied_requests: 0,
};

function occurrence(overrides: Partial<Occurrence> = {}): Occurrence {
  return {
    id: "occ1",
    kind: "scheduled",
    scheduled_for: "2026-09-17T09:00:00Z",
    status: "dispatched",
    skip_reason: null,
    missed_slots: 0,
    config_version: 1,
    query_run_id: "run1",
    run_status: "completed",
    run_error_code: null,
    connector_outcomes: ["success"],
    changes: [{ change_set_id: "cs1", connector_id: "rss", status: "changes_detected", counts: { new: 2, changed: 1, not_observed: 0, conflicting: 0, unknown: 0 } }],
    dispatched_by: "scheduler",
    created_at: "2026-09-17T09:00:01Z",
    ...overrides,
  };
}

function monitor(overrides: Partial<Monitor> = {}): Monitor {
  return {
    id: "m1",
    case_id: TEST_CASE.id,
    saved_query_id: "q1",
    saved_query_name: "Feed watch",
    name: "Daily feed",
    description: "",
    status: "enabled",
    status_reason: null,
    status_changed_at: "2026-09-16T10:00:00Z",
    schedule: { kind: "daily", time: "09:00" },
    timezone: "Europe/Istanbul",
    missed_run_policy: "run_latest",
    connector_ids: ["rss"],
    scope: { max_pages: 2, max_items_per_page: 50 },
    limits: { max_requests_per_run: 20, max_items_per_run: 100, max_run_seconds: 600 },
    budget: { period: "day", max_requests: 100, max_provider_units: null },
    retention: { keep_last_runs: null, max_age_days: null },
    notify: { on_change: true, on_failure: true, on_budget_exhausted: true, on_completion: false, recipients: "case_analysts" },
    config_version: 1,
    collects_live: true,
    query_changed: false,
    authorized_by: "analyst",
    next_run_at: "2026-09-18T06:00:00Z",
    last_scheduled_for: "2026-09-17T06:00:00Z",
    consecutive_failures: 0,
    active_run_id: null,
    last_occurrence: occurrence(),
    budget_usage: [USAGE],
    actions: [],
    created_at: "2026-09-16T10:00:00Z",
    updated_at: "2026-09-16T10:00:00Z",
    ...overrides,
  };
}

describe("monitoring helpers", () => {
  it("describes schedules, time rules, usage and skipped occurrences in words", () => {
    expect(describeSchedule({ kind: "interval", every_minutes: 360 }, "UTC")).toBe("Every 6 hours");
    expect(describeSchedule({ kind: "weekly", time: "08:30", weekdays: [0, 4] }, "Europe/Istanbul")).toBe("Mon, Fri at 08:30 (Europe/Istanbul)");
    expect(scheduleTimeRule({ kind: "interval", every_minutes: 60 })).toMatch("elapsed time");
    expect(scheduleTimeRule({ kind: "daily", time: "02:30" })).toMatch("runs once, the first time");
    expect(describeUsage(USAGE)).toBe("Monitor: 37 of 100 requests used today (UTC); 5 estimated; 2 in flight");
    expect(occurrenceSummary(occurrence({ status: "skipped", skip_reason: "overlap", query_run_id: null }))).toBe(
      "Skipped: the previous run was still queued or running",
    );
  });
});

describe("MonitorsView", () => {
  it("lists next run, last result, budget use and monitors that need attention", async () => {
    mockApi({
      [`${API}/monitors?`]: {
        items: [
          monitor(),
          monitor({
            id: "m2",
            name: "Paused feed",
            status: "paused",
            status_reason: "query_changed",
            next_run_at: null,
            actions: [{ code: "query_changed", message: "The saved query changed. Review it and resume to adopt the change." }],
          }),
        ],
        total: 2,
        limit: 100,
        offset: 0,
      },
      [`${API}/saved-queries?`]: { items: [], total: 0, limit: 100, offset: 0 },
      "/api/v1/connectors": [],
    });
    renderInCase(<MonitorsView />);
    expect(await screen.findByRole("link", { name: "Daily feed" })).toHaveAttribute("href", `/cases/${TEST_CASE.id}/monitors/m1`);
    expect(screen.getAllByText("Daily at 09:00 (Europe/Istanbul) · Feed watch")).toHaveLength(2);
    expect(screen.getAllByText("2 new, 1 changed")).toHaveLength(2);
    expect(screen.getByText("The saved query changed. Review it and resume to adopt the change.")).toBeInTheDocument();
    expect(screen.queryByText("Paused: saved query changed")).toBeNull();
    expect(screen.getByText("1 monitor needs attention")).toBeInTheDocument();
    expect(screen.getAllByText(/Monitor: 37 of 100 requests used today/)).toHaveLength(2);
    expect(screen.getByRole("button", { name: "New monitor" })).toBeInTheDocument();
  });

  it("offers no monitor changes to a viewer", async () => {
    mockApi({
      [`${API}/monitors?`]: { items: [monitor()], total: 1, limit: 100, offset: 0 },
      [`${API}/saved-queries?`]: { items: [], total: 0, limit: 100, offset: 0 },
      "/api/v1/connectors": [],
    });
    renderInCase(<MonitorsView />, VIEWER_CASE);
    await screen.findByRole("link", { name: "Daily feed" });
    expect(screen.queryByRole("button", { name: "New monitor" })).toBeNull();
  });
});

describe("MonitorDetailView", () => {
  it("requires confirming recurring external collection and adopting query changes before resuming", async () => {
    const paused = monitor({ status: "paused", status_reason: "query_changed", query_changed: true, next_run_at: null });
    const fetchMock = mockApi({
      [`${API}/monitors/m1/occurrences`]: { items: [occurrence({ status: "skipped", skip_reason: "query_changed", query_run_id: null, run_status: null, changes: [] })], total: 1, limit: 20, offset: 0 },
      [`${API}/monitors/m1/subscriptions`]: [],
      [`${API}/monitors/m1`]: paused,
      [`${API}/notification-destinations`]: [],
      [`${API}/saved-queries?`]: { items: [], total: 0, limit: 100, offset: 0 },
      "/api/v1/connectors": [],
    });
    const user = userEvent.setup();
    renderInCase(<MonitorDetailView monitorId="m1" />);

    const resume = await screen.findByRole("button", { name: "Adopt query changes and resume" });
    expect(resume).toBeDisabled();
    expect(screen.getByText("Skipped: the saved query changed")).toBeInTheDocument();
    expect(screen.getAllByText(/a time repeated when clocks go back runs once, the first time/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("checkbox", { name: /contacts external sources daily at 09:00/i }));
    expect(resume).toBeEnabled();
    await user.click(resume);
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/monitors/m1/resume"));
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ acknowledge_recurring_collection: true, adopt_query_changes: true });
    });
  });

  it("shows a viewer the history without run, pause or configuration controls", async () => {
    mockApi({
      [`${API}/monitors/m1/occurrences`]: { items: [occurrence()], total: 1, limit: 20, offset: 0 },
      [`${API}/monitors/m1`]: monitor(),
      "/api/v1/connectors": [],
    });
    renderInCase(<MonitorDetailView monitorId="m1" />, VIEWER_CASE);
    expect(await screen.findByRole("link", { name: /^Changes\s*:\s*2 new, 1 changed$/ })).toHaveAttribute("href", `/cases/${TEST_CASE.id}/changes/cs1`);
    expect(screen.queryByRole("button", { name: "Run now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    expect(screen.queryByText("Edit configuration")).toBeNull();
    expect(screen.getByText(/Your role in this case is viewer/)).toBeInTheDocument();
  });
});

describe("ChangeSetView", () => {
  it("explains incomplete comparisons and marks evidence removed by retention", async () => {
    const detail: ChangeSetDetail = {
      id: "cs1",
      monitor_id: "m1",
      occurrence_id: "occ1",
      query_run_id: "run2",
      connector_run_id: "cr2",
      connector_id: "rss",
      connector_version: "1.0.0",
      baseline_query_run_id: "run1",
      baseline_connector_run_id: "cr1",
      status: "unknown",
      coverage_complete: false,
      baseline_coverage_complete: true,
      counts: { new: 1, changed: 0, not_observed: 0, conflicting: 0, unknown: 1 },
      limitations: ["This collection is incomplete (status partial, outcome rate_limited): items not collected are not reported as removed."],
      truncated: false,
      created_at: "2026-09-17T09:05:00Z",
      events: {
        items: [
          {
            id: "e1",
            kind: "unknown",
            observation_type: "feed_item",
            source_object_id: "item-7",
            field: null,
            previous_value: "Başlık",
            current_value: null,
            entity_id: null,
            previous_observation_id: "o1",
            current_observation_id: null,
            previous_evidence_id: "ev1",
            current_evidence_id: null,
            previous_evidence_available: false,
            current_evidence_available: null,
            note: "Not collected this time; coverage was incomplete.",
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      },
    };
    mockApi({ [`${API}/change-sets/cs1`]: detail });
    renderInCase(<ChangeSetView changeSetId="cs1" />);
    expect(await screen.findByText(/absence cannot be judged/)).toBeInTheDocument();
    expect(screen.getByText(/items not collected are not reported as removed/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Change events" });
    expect(within(table).getByText("Başlık")).toBeInTheDocument();
    expect(within(table).getByText("Before: removed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unknown (1)" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("link", { name: "Open baseline run" })).toHaveAttribute("href", `/cases/${TEST_CASE.id}/runs/run1`);
  });
});
