import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TEST_CASE, mockApi, renderInCase } from "@/test/workspace";
import type { EvidenceDetail, GraphData, RelationshipDetail } from "@/lib/workspace-types";

import { EvidenceDetailView } from "./EvidenceDetailView";
import { GraphView } from "./GraphView";

vi.mock("./GraphCanvas", () => ({
  GraphCanvas: () => <div data-testid="graph-canvas" />,
}));

const API = `/api/v1/cases/${TEST_CASE.id}`;

const graph: GraphData = {
  nodes: [
    { id: "e1", label: "Örnek A.Ş.", entity_type: "organization", origin: "analyst_assertion" },
    { id: "e2", label: "ornek.example", entity_type: "domain", origin: "observed" },
  ],
  edges: [
    { id: "r1", source: "e1", target: "e2", predicate: "owns", origin: "analyst_assertion", review_status: "accepted" },
  ],
  focus_entity_id: null,
  depth: 1,
  max_nodes: 60,
  truncated: true,
  total_entities: 250,
  total_relationships: 400,
};

const relationship: RelationshipDetail = {
  id: "r1",
  case_id: TEST_CASE.id,
  source: { id: "e1", display_name: "Örnek A.Ş.", entity_type: "organization" },
  target: { id: "e2", display_name: "ornek.example", entity_type: "domain" },
  predicate: "owns",
  origin: "analyst_assertion",
  review_status: "accepted",
  description: "",
  valid_from: null,
  valid_to: null,
  created_by_query_run_id: null,
  reference_count: 1,
  created_at: "2026-09-15T10:00:00Z",
  updated_at: "2026-09-15T10:00:00Z",
  references: [
    {
      id: "ref1",
      stance: "supports",
      note: null,
      evidence_id: "ev1",
      evidence_title: "Registry extract",
      evidence_acquisition_method: "authorized_import",
      evidence_sha256: "a".repeat(64),
      observation_id: null,
      observation_type: null,
      observation_collected_at: null,
      query_run_id: null,
      created_at: "2026-09-15T10:00:00Z",
    },
  ],
  decisions: [
    {
      id: "d1",
      decision_type: "review_status",
      previous_value: "unreviewed",
      new_value: "accepted",
      rationale: "Registry confirms",
      decided_at: "2026-09-15T11:00:00Z",
    },
  ],
};

describe("GraphView", () => {
  it("states truncation and reveals origin and supporting evidence for a selected edge", async () => {
    mockApi({
      [`${API}/graph`]: graph,
      [`${API}/entities`]: { items: [], total: 0, limit: 100, offset: 0 },
      [`${API}/relationships/r1`]: relationship,
      [`${API}/evidence`]: { items: [], total: 0, limit: 100, offset: 0 },
    });
    renderInCase(<GraphView />);

    expect(await screen.findByText(/Showing 2 of 250 entities/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "owns" }));

    const panel = await screen.findByRole("complementary", { name: "Relationship details" });
    expect(await within(panel).findByText("Registry extract")).toHaveAttribute("href", `/cases/${TEST_CASE.id}/evidence/ev1`);
    expect(within(panel).getAllByText("Analyst assertion").length).toBeGreaterThan(0);
    expect(within(panel).getByText("Supports", { selector: "span" })).toBeInTheDocument();
    expect(within(panel).getByText(/Registry confirms/)).toBeInTheDocument();
  });
});

describe("EvidenceDetailView", () => {
  it("renders hostile content as inert text and surfaces integrity failures", async () => {
    const hostile = "<script>window.__pwned = true</script><img src=x onerror=alert(1)>";
    const detail: EvidenceDetail = {
      evidence: {
        id: "ev1",
        case_id: TEST_CASE.id,
        kind: "text",
        title: "Suspicious page",
        original_filename: "page.html",
        content_type: "text/plain; charset=utf-8",
        size_bytes: hostile.length,
        sha256: "b".repeat(64),
        acquisition_method: "authorized_import",
        import_origin: "Provided by reporter",
        source_reference: null,
        source_published_at: null,
        source_published_at_original: null,
        collected_at: "2026-09-15T10:00:00Z",
        created_at: "2026-09-15T10:00:00Z",
        connector_id: null,
        connector_version: null,
        query_run_id: null,
        connector_run_id: null,
        page_index: null,
        description: "",
        synthetic: false,
      },
      integrity: { status: "evidence_hash_mismatch", checked_at: "2026-09-15T10:05:00Z" },
      linked_entities: [],
      linked_relationships: [],
      observation_count: 0,
      duplicate_of: [],
    };
    mockApi({
      [`${API}/evidence/ev1/preview`]: {
        evidence_id: "ev1",
        kind: "text",
        encoding: "utf-8",
        text: hostile,
        pretty_json: null,
        truncated: false,
        preview_bytes: hostile.length,
        size_bytes: hostile.length,
      },
      [`${API}/evidence/ev1`]: detail,
      [`${API}/observations`]: { items: [], total: 0, limit: 20, offset: 0 },
      [`${API}/notes`]: { items: [], total: 0, limit: 50, offset: 0 },
    });
    const { container } = renderInCase(<EvidenceDetailView evidenceId="ev1" />);

    const preview = await screen.findByLabelText("Evidence content preview");
    expect(preview.textContent).toBe(hostile);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
    expect(screen.getByText("Hash mismatch")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/do not match this record/);
    expect(screen.getByRole("link", { name: "Download original" })).toHaveAttribute("href", `${API}/evidence/ev1/content`);
  });
});
