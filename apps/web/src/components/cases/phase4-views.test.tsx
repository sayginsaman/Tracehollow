import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TEST_CASE, mockApi, renderInCase, renderWithSession } from "@/test/workspace";
import type { Capability, Comparison, ProcessingJob, ReportPreview, TimelineData, TimelineItem } from "@/lib/workspace-types";

import { CapabilityMatrix, capabilityAvailability } from "../sources/CapabilityMatrix";
import { CompareView, comparisonPath, toggleSelection } from "./CompareView";
import { ProcessingJobsPanel, buildProcessingUpload, jobSummary } from "./ProcessingImports";
import { EMPTY_REPORT, ReportBuilder, reportRequest } from "./ReportBuilder";
import { TimelineView, displayTime } from "./TimelineView";

const API = `/api/v1/cases/${TEST_CASE.id}`;

afterEach(() => {
  vi.restoreAllMocks();
});

function job(overrides: Partial<ProcessingJob> = {}): ProcessingJob {
  return {
    id: "job1",
    case_id: TEST_CASE.id,
    evidence_id: "ev1",
    job_type: "whatsapp_export",
    status: "completed",
    options: { date_order: "auto", timezone: "unknown" },
    result: {},
    needs_input: null,
    attempts: 1,
    error_code: null,
    error_detail: null,
    cancel_requested_at: null,
    created_at: "2026-09-16T10:00:00Z",
    updated_at: "2026-09-16T10:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

describe("processing imports", () => {
  it("validates uploads before sending anything", () => {
    const file = new File(["03/04/2024, 09:15 - Ayşe: Merhaba"], "chat.txt", { type: "text/plain" });
    expect(buildProcessingUpload({ file: null, importOrigin: "x", title: "", sourceReference: "", extra: {} }).problem).toMatch("Choose a file");
    expect(buildProcessingUpload({ file, importOrigin: " a", title: "", sourceReference: "", extra: {} }).problem).toMatch("import origin");
    const { form, problem } = buildProcessingUpload({
      file,
      importOrigin: "Provided by the account holder under consent",
      title: "",
      sourceReference: "",
      extra: { timezone: "unknown", date_order: "auto" },
    });
    expect(problem).toBeNull();
    expect(form?.get("timezone")).toBe("unknown");
    expect(form?.get("title")).toBeNull();
  });

  it("summarises jobs factually", () => {
    expect(jobSummary(job({ status: "needs_input" }))).toBe("Waiting for the date order.");
    expect(jobSummary(job({ result: { messages: 1, system_events: 2 } }))).toBe("1 message, 2 system events");
    expect(jobSummary(job({ job_type: "document_text", result: { document_state: "image_only", pages_total: 3, pages_processed: 3 } }))).toBe(
      "Image only · 3 of 3 pages",
    );
  });

  it("asks for the date order and submits the analyst's choice", async () => {
    const waiting = job({
      status: "needs_input",
      needs_input: {
        field: "date_order",
        question: "Which date order does this export use?",
        basis: "ambiguous",
        explanation: "Every date could be read either way.",
        samples: ["03/04/2024"],
        choices: ["day_first", "month_first", "year_first"],
      },
    });
    const fetchMock = mockApi({
      [`${API}/processing-jobs?`]: { items: [waiting], total: 1, limit: 25, offset: 0 },
      [`${API}/processing-jobs/job1/input`]: { ...waiting, status: "queued" },
    });
    renderInCase(<ProcessingJobsPanel apiBase={API} base={`/cases/${TEST_CASE.id}`} writable />);
    expect(await screen.findByText("Needs your input")).toBeInTheDocument();
    expect(screen.getByText("03/04/2024")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Continue processing" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose the date order.");
    await user.click(screen.getByLabelText("Day first (31/12/2024)"));
    await user.click(screen.getByRole("button", { name: "Continue processing" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/processing-jobs/job1/input"));
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ date_order: "day_first" });
    });
  });
});

describe("timeline", () => {
  const item: TimelineItem = {
    observation_id: "o1",
    observation_type: "whatsapp_message",
    time: null,
    time_basis: "local_time_without_timezone",
    local_time: "2024-04-13T08:01:00",
    timestamp_text: "13/04/2024, 08:01",
    collected_at: "2026-09-16T10:00:00Z",
    source_published_at: null,
    summary: "Fotoğraf ektedir",
    source_label: "Ayşe Yılmaz",
    source_object_id: "line:2",
    entity_id: null,
    entity_name: null,
    evidence_id: "ev1",
    evidence_title: "Chat export",
    acquisition_method: "authorized_import",
    connector_id: null,
    connector_run_id: null,
    location: { line_start: 2 },
    notes: ["Local wall-clock time from the source without a timezone; it cannot be placed on the UTC timeline."],
  };

  it("never presents a local time as UTC", () => {
    expect(displayTime(item)).toBe("2024-04-13 08:01:00 (local)");
    expect(displayTime({ ...item, time: "2024-04-13T05:01:00Z", time_basis: "event_time" })).toContain("UTC");
  });

  it("shows section counts, the time basis and the source location", async () => {
    const data: TimelineData = {
      section: "local_time_only",
      items: [item],
      total: 1,
      limit: 50,
      offset: 0,
      sections: { dated: 0, local_time_only: 1, undated: 3 },
    };
    mockApi({ [`${API}/timeline`]: data, [`${API}/entities`]: { items: [], total: 0, limit: 100, offset: 0 } });
    renderInCase(<TimelineView />);
    expect(await screen.findByRole("tab", { name: "Local time only (1)" })).toBeInTheDocument();
    expect(screen.getByText("Local time, timezone unknown")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Chat export (line 2)" })).toHaveAttribute("href", `/cases/${TEST_CASE.id}/evidence/ev1`);
    expect(screen.getByText(/cannot be placed on the UTC timeline/)).toBeInTheDocument();
  });
});

describe("comparison", () => {
  it("limits the selection and needs two entities", () => {
    expect(toggleSelection(["a", "b", "c", "d"], "e")).toEqual(["a", "b", "c", "d"]);
    expect(toggleSelection(["a", "b"], "a")).toEqual(["b"]);
    expect(comparisonPath(API, ["a"])).toBeNull();
    expect(comparisonPath(API, ["a", "b"])).toBe(`${API}/entity-comparison?entity_id=a&entity_id=b`);
  });

  it("shows shared identifiers as leads and unknown absences as unknown", async () => {
    const comparison: Comparison = {
      entities: [
        { id: "e1", display_name: "ornek-dev on GitHub", entity_type: "platform_account", origin: "observed", observation_count: 3, linked_evidence_count: 0, event_time_span: null, published_span: null, collected_span: null, coverage: [] },
        { id: "e2", display_name: "ornek-dev elsewhere", entity_type: "platform_account", origin: "analyst_assertion", observation_count: 0, linked_evidence_count: 0, event_time_span: null, published_span: null, collected_span: null, coverage: [] },
      ],
      identifiers: [{ kind: "shared", identifier_type: "username", platform: "github.com", values: ["ornek-dev"], entity_ids: ["e1", "e2"] }],
      relationships: [],
      shared_neighbours: [],
      changes: [],
      changes_truncated: false,
      absences: [
        { entity_id: "e1", connector_id: "github.account", later_run_outcome: "partial", later_run_stopped_reason: "unavailable", items: ["github_repository:1"], items_total: 1, interpretation: "unknown_later_collection_incomplete", note: "The later collection failed, was partial or stopped early, so whether these items still exist is unknown." },
      ],
      conflicts: [],
      unresolved: ["ornek-dev on GitHub, ornek-dev elsewhere share the username 'ornek-dev' with no accepted relationship between them."],
      merge_policy: "Tracehollow never merges entities automatically.",
    };
    mockApi({
      [`${API}/entities`]: {
        items: [
          { id: "e1", display_name: "ornek-dev on GitHub", entity_type: "platform_account" },
          { id: "e2", display_name: "ornek-dev elsewhere", entity_type: "platform_account" },
        ],
        total: 2,
        limit: 100,
        offset: 0,
      },
      [`${API}/entity-comparison`]: comparison,
    });
    renderInCase(<CompareView />);
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText(/ornek-dev on GitHub/));
    await user.click(screen.getByLabelText(/ornek-dev elsewhere/));
    expect(await screen.findByText("Tracehollow never merges entities automatically.")).toBeInTheDocument();
    expect(screen.getByText("Shared")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(screen.getByText(/whether these items still exist is unknown/)).toBeInTheDocument();
  });
});

describe("reports", () => {
  it("sends only the selection and one redaction term per line", () => {
    const body = reportRequest({ ...EMPTY_REPORT, compareEntityIds: ["a"], redactTerms: "Ayşe Yılmaz\n\nx\n+1 202-555-0143 " });
    expect(body.comparison_entity_ids).toEqual([]);
    expect(body.redact_terms).toEqual(["Ayşe Yılmaz", "+1 202-555-0143"]);
    expect(body.entity_ids).toEqual([]);
  });

  it("previews the report in a sandboxed frame before download is enabled", async () => {
    const preview: ReportPreview = {
      counts: { entities: 1, bundled_evidence: 1 },
      redactions_applied: 2,
      credential_like_values_removed: 1,
      warnings: ["AI-generated answers are included; they are labelled and not analyst findings."],
      size_bytes: 2048,
      html: "<!doctype html><title>Report</title><p>&lt;script&gt;</p>",
    };
    const fetchMock = mockApi({
      [`${API}/reports/selectable`]: {
        entities: [{ id: "e1", label: "Örnek A.Ş.", detail: "organization · analyst_assertion" }],
        relationships: [],
        evidence: [],
        ai_answers: [],
        notes: [],
      },
      [`${API}/reports/html/preview`]: preview,
    });
    renderInCase(<ReportBuilder />);
    const user = userEvent.setup();
    const [entityCheckbox] = await screen.findAllByLabelText(/Örnek A.Ş./, { selector: "input" });
    if (!entityCheckbox) throw new Error("entity checkbox missing");
    await user.click(entityCheckbox);
    const download = screen.getByRole("button", { name: "Download HTML" });
    expect(download).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Preview report" }));
    const frame = await screen.findByTitle("Report preview");
    expect(frame).toHaveAttribute("sandbox", "");
    expect(frame.getAttribute("srcdoc")).toContain("&lt;script&gt;");
    const request = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/reports/html/preview"));
    expect(JSON.parse(String(request?.[1]?.body)).entity_ids).toEqual(["e1"]);
    expect(screen.getByText(/AI-generated answers are included/)).toBeInTheDocument();
    expect(download).toBeEnabled();
  });
});

describe("capability matrix", () => {
  const base: Capability = {
    name: "official_business_discovery",
    label: "Official API: professional account discovery",
    status: "implemented",
    access_method: "official_api",
    provider: "Meta Instagram Graph API v25.0 (Business Discovery)",
    collection_mode: "third_party_api",
    account_types: ["Instagram Business accounts"],
    content_types: ["public profile fields"],
    returned_fields: ["id"],
    unavailable_fields: ["personal (non-professional) accounts"],
    stable_identifiers: ["Instagram user ID (id)"],
    pagination: "Media edge cursors",
    session_requirements: "A Facebook User access token",
    restrictions: "Only professional accounts",
    cost_quota: "No per-request charge",
    verification_status: "fixture_tested",
    last_live_verification: null,
    credential_names: ["access_token", "ig_user_id"],
    reason: null,
    references: [],
    available: false,
    blocked_reason: "Blocked until credentials are configured: access_token, ig_user_id.",
  };

  it("explains every unavailable capability", () => {
    expect(capabilityAvailability(base)).toEqual({ tone: "warn", text: base.blocked_reason });
    expect(capabilityAvailability({ ...base, available: true, blocked_reason: null }).text).toBe("Available");
    expect(capabilityAvailability({ ...base, status: "excluded", reason: "Outside the access rules." }).text).toBe("Excluded. Outside the access rules.");
  });

  it("separates official and unofficial access and lists fields never returned", () => {
    renderWithSession(
      <CapabilityMatrix
        capabilities={[
          base,
          { ...base, name: "instaloader_session", label: "Unofficial client with a logged-in session", status: "not_implemented", access_method: "unofficial_client", verification_status: null, reason: "Needs a logged-in session." },
        ]}
      />,
    );
    const [official, unofficial] = screen.getAllByRole("listitem");
    if (!official || !unofficial) throw new Error("capability items missing");
    expect(within(official).getByText("Official API")).toBeInTheDocument();
    expect(within(official).getByText(/Blocked until credentials are configured/)).toBeInTheDocument();
    expect(within(official).getByText("personal (non-professional) accounts")).toBeInTheDocument();
    expect(within(unofficial).getByText("Unofficial client")).toBeInTheDocument();
    expect(within(unofficial).getByText("Not implemented. Needs a logged-in session.")).toBeInTheDocument();
  });
});
