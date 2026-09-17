import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RetentionPolicy } from "@/lib/workspace-types";
import { TEST_CASE, mockApi, renderInCase } from "@/test/workspace";

import { RetentionPanel } from "./CasePolicies";
import { EvidenceDetailView } from "./EvidenceDetailView";
import { StixImportForm } from "./StixImportForm";

vi.mock("next/navigation", async () => {
  const actual = await vi.importActual<typeof import("next/navigation")>("next/navigation");
  return { ...actual, useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }), usePathname: () => "/" };
});

const API = `/api/v1/cases/${TEST_CASE.id}`;

afterEach(() => {
  vi.restoreAllMocks();
});

function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("StixImportForm", () => {
  it("lists the objects that made a strict import fail", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      respond(422, {
        detail: {
          code: "unsupported_objects",
          message: "The bundle contains unsupported objects",
          objects: [{ id: "malware--0c7b5b88-8ff7-4a4d-aa9d-feb398cd0061", type: "malware", reason: "unsupported type" }],
        },
      }),
    );
    const user = userEvent.setup();
    renderInCase(<StixImportForm apiBase={API} base={`/cases/${TEST_CASE.id}`} onImported={vi.fn()} />);
    await user.upload(screen.getByLabelText("Bundle file"), new File(['{"type":"bundle"}'], "bundle.json", { type: "application/json" }));
    await user.type(screen.getByLabelText("Origin and authorization"), "Shared by the partner CERT for this case");
    await user.click(screen.getByRole("checkbox", { name: /Reject the whole bundle/ }));
    await user.click(screen.getByRole("button", { name: "Import bundle" }));
    expect(await screen.findByText("The bundle contains unsupported objects.")).toBeInTheDocument();
    expect(screen.getByText("malware--0c7b5b88-8ff7-4a4d-aa9d-feb398cd0061")).toBeInTheDocument();
    const form = fetchMock.mock.calls[0]?.[1]?.body as FormData;
    expect(form.get("on_unsupported")).toBe("reject");
    expect(form.get("import_origin")).toBe("Shared by the partner CERT for this case");
  });

  it("refuses oversized bundles before uploading", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    renderInCase(<StixImportForm apiBase={API} base={`/cases/${TEST_CASE.id}`} onImported={vi.fn()} />);
    const big = new File(["x"], "big.json", { type: "application/json" });
    Object.defineProperty(big, "size", { value: 6 * 1024 * 1024 });
    await user.upload(screen.getByLabelText("Bundle file"), big);
    await user.click(screen.getByRole("button", { name: "Import bundle" }));
    expect(screen.getByText("The bundle is larger than 5 MiB.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("RetentionPanel", () => {
  it("previews removals and activates only with the typed case title", async () => {
    const policy: RetentionPolicy = {
      active: false,
      collected_results_max_age_days: null,
      imported_evidence_max_age_days: null,
      version: 0,
      activated_at: null,
      last_applied_at: null,
      monitor_rules: [],
    };
    const fetchMock = mockApi({
      [`${API}/retention/preview`]: {
        executions: 3,
        imported_originals: 0,
        evidence_records: 7,
        stored_bytes: 2048,
        observations: 12,
        relationship_references: 1,
        index_chunks: 9,
        ai_citations_affected: 2,
        protected_baselines: 1,
        deferred: { active_runs: 1 },
        oldest: null,
        newest: null,
        not_removed: ["Backups, downloaded exports and reports, and notifications already delivered."],
      },
      [`${API}/retention/jobs`]: { items: [], total: 0, limit: 5, offset: 0 },
      [`${API}/retention`]: policy,
    });
    const user = userEvent.setup();
    renderInCase(<RetentionPanel />);
    await user.type(await screen.findByLabelText("Remove collected results older than (days)"), "30");
    await user.click(screen.getByRole("button", { name: "Preview what would be removed" }));
    expect(await screen.findByText("7 (2.0 KiB)")).toBeInTheDocument();
    expect(screen.getByText("Active runs: 1")).toBeInTheDocument();
    expect(screen.getByText(/downloaded exports and reports/)).toBeInTheDocument();
    const activate = screen.getByRole("button", { name: "Activate and apply now" });
    expect(activate).toBeDisabled();
    await user.type(screen.getByLabelText(`Type the case title to activate: ${TEST_CASE.title}`), TEST_CASE.title);
    await user.click(activate);
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input, init]) => String(input) === `${API}/retention` && init?.method === "PUT");
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ collected_results_max_age_days: 30, imported_evidence_max_age_days: null, confirm_title: TEST_CASE.title });
    });
  });
});

describe("EvidenceDetailView", () => {
  it("shows the retention tombstone instead of an error", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input).endsWith("/evidence/ev1")
        ? respond(410, {
            detail: {
              code: "evidence_expired_by_retention",
              message: "This evidence was removed by the case retention policy on 2026-09-17.",
              expired_at: "2026-09-17T03:00:00Z",
              rule: "collected_results_max_age",
              sha256: "ab".repeat(32),
            },
          })
        : respond(410, { detail: "evidence_expired_by_retention" }),
    );
    renderInCase(<EvidenceDetailView evidenceId="ev1" />);
    expect(await screen.findByRole("heading", { name: "Evidence removed by retention" })).toBeInTheDocument();
    expect(screen.getByText("ab".repeat(32))).toBeInTheDocument();
    expect(screen.getByText(/backups or downloaded exports, are not affected/)).toBeInTheDocument();
  });
});
