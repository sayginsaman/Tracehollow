import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Activity, ProcessingJob, QueryRun } from "@/lib/workspace-types";
import { mockApi, renderWithSession } from "@/test/workspace";

import { WorkspaceOverview, activityRows, attentionItems } from "./WorkspaceOverview";

afterEach(() => {
  vi.restoreAllMocks();
});

const CASE_ID = "11111111-1111-4111-8111-111111111111";

function run(overrides: Partial<QueryRun>): QueryRun {
  return {
    id: "r1",
    case_id: CASE_ID,
    saved_query_name: "Hesap adayları",
    saved_query_id: "q1",
    run_number: 1,
    status: "completed",
    parameters_snapshot: {},
    queued_at: "2026-09-16T10:00:00Z",
    started_at: null,
    finished_at: null,
    cancel_requested_at: null,
    error_code: null,
    synthetic: false,
    connector_outcomes: ["findings"],
    evidence_count: 1,
    observation_count: 2,
    dispatch_status: "done",
    ...overrides,
  };
}

const waiting: ProcessingJob = {
  id: "j1",
  case_id: CASE_ID,
  evidence_id: "e1",
  job_type: "whatsapp_export",
  status: "needs_input",
  options: {},
  result: {},
  needs_input: { field: "date_order", question: "Which date order does this export use?", basis: "ambiguous", explanation: "", samples: [], choices: [] },
  attempts: 1,
  error_code: null,
  error_detail: null,
  cancel_requested_at: null,
  created_at: "2026-09-16T11:00:00Z",
  updated_at: "2026-09-16T11:00:00Z",
  started_at: null,
  finished_at: null,
};

const ACTIVITY: Activity = {
  runs: [run({ id: "r2", status: "failed", connector_outcomes: ["authentication_required"], queued_at: "2026-09-16T12:00:00Z" }), run({})],
  active_runs: 0,
  processing_jobs: [waiting],
  active_processing_jobs: 0,
  jobs_needing_input: [waiting],
  cases: [{ id: CASE_ID, title: "Örnek vaka", status: "active" }],
};

describe("workspace overview", () => {
  it("lists only real items that need a decision or explain a failure", () => {
    const items = attentionItems(ACTIVITY, [], { api_version: "1", environment: "test", ready: true, checks: {}, checked_at: "2026-09-16T12:00:00Z" });
    expect(items.map((item) => item.title)).toEqual(["WhatsApp export needs your answer", "Hesap adayları #1: access or setup required"]);
    expect(items[0]?.href).toBe(`/cases/${CASE_ID}/imports`);
    expect(attentionItems({ ...ACTIVITY, runs: [run({})], jobs_needing_input: [], processing_jobs: [] }, [], null)).toEqual([]);
  });

  it("merges runs and imports newest first", () => {
    expect(activityRows(ACTIVITY).map((row) => (row.kind === "run" ? row.run.id : row.job.id))).toEqual(["r2", "j1", "r1"]);
  });

  it("shows recent cases, attention items and environment readiness from the API", async () => {
    mockApi({
      "/api/v1/activity": ACTIVITY,
      "/api/v1/cases?status=active": {
        items: [{ id: CASE_ID, title: "Örnek vaka", purpose: "", scope: "", tags: ["synthetic-demo"], status: "active", created_at: "2026-09-15T10:00:00Z", updated_at: "2026-09-16T10:00:00Z", archived_at: null }],
        total: 1,
        limit: 6,
        offset: 0,
      },
      "/api/v1/cases?status=deletion_failed": { items: [], total: 0, limit: 5, offset: 0 },
      "/api/v1/system/status": { api_version: "1", environment: "test", ready: true, checks: {}, checked_at: "2026-09-16T12:00:00Z" },
    });
    renderWithSession(<WorkspaceOverview />);
    expect(await screen.findByRole("link", { name: "Örnek vaka" })).toHaveAttribute("href", `/cases/${CASE_ID}`);
    expect(screen.getAllByText("Synthetic demo").length).toBeGreaterThan(0);
    expect(await screen.findByText("WhatsApp export needs your answer")).toBeInTheDocument();
    expect(await screen.findByText("Required services respond")).toBeInTheDocument();
    expect(screen.getByText("Access or setup required")).toBeInTheDocument();
  });
});
