import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { formatQuota } from "@/lib/connectors";
import { formatUtcShort } from "@/lib/messages";

import { caseListPath } from "../cases/CaseList";
import { jobFacts } from "../cases/ProcessingImports";
import { ProvenanceBadge, StatusBadge, evidenceProvenance, runOutcome } from "./status";
import { Tabs } from "./tabs";

describe("runOutcome", () => {
  it("states each outcome in plain language and never calls a blocked run a success", () => {
    expect(runOutcome({ status: "completed", connector_outcomes: ["findings"] }).label).toBe("Completed with findings");
    expect(runOutcome({ status: "completed", connector_outcomes: ["no_findings"] }).label).toBe("Completed, no findings");
    expect(runOutcome({ status: "partial", connector_outcomes: ["partial"] })).toMatchObject({ label: "Partial results", tone: "warn" });
    expect(runOutcome({ status: "failed", connector_outcomes: ["authentication_required"] })).toMatchObject({
      label: "Access or setup required",
      tone: "warn",
    });
    expect(runOutcome({ status: "failed", connector_outcomes: ["rate_limited"] }).label).toBe("Rate limited");
    expect(runOutcome({ status: "failed", connector_outcomes: ["parse_error"] })).toMatchObject({ label: "Failed", tone: "bad" });
    expect(runOutcome({ status: "canceled" }).label).toBe("Canceled");
    // Older API responses without outcomes still get an honest label.
    expect(runOutcome({ status: "completed" }).label).toBe("Completed");
  });
});

describe("evidenceProvenance", () => {
  it("separates collected material, imports, extracted text, OCR text and synthetic data", () => {
    expect(evidenceProvenance({ acquisition_method: "connector_collection" }).label).toBe("Collected");
    expect(evidenceProvenance({ acquisition_method: "connector_collection", derived_from_evidence_id: "e1" }).label).toBe("Extracted from collection");
    expect(evidenceProvenance({ acquisition_method: "authorized_import" }).label).toBe("Authorized import");
    expect(evidenceProvenance({ acquisition_method: "authorized_import", collection_metadata: { text_origin: "embedded_text_layer" } }).label).toBe(
      "Extracted text",
    );
    expect(evidenceProvenance({ acquisition_method: "authorized_import", collection_metadata: { text_origin: "ocr" } }).label).toBe("OCR text");
    expect(evidenceProvenance({ acquisition_method: "authorized_import", collection_metadata: { derivation: "whatsapp_chat_text" } }).label).toBe(
      "Extracted text",
    );
    expect(evidenceProvenance({ acquisition_method: "authorized_import", collection_metadata: { derivation: "whatsapp_attachment" } }).label).toBe(
      "Import attachment",
    );
    expect(evidenceProvenance({ acquisition_method: "synthetic_fixture" })).toMatchObject({ label: "Synthetic fixture", synthetic: true });
  });

  it("renders status with an icon and words, not colour alone", () => {
    const { container } = render(
      <>
        <StatusBadge tone="bad" label="Hash mismatch" />
        <ProvenanceBadge evidence={{ acquisition_method: "authorized_import", collection_metadata: { text_origin: "ocr" } }} />
      </>,
    );
    expect(screen.getByText("Hash mismatch")).toBeInTheDocument();
    expect(screen.getByText("OCR text")).toBeInTheDocument();
    expect(container.querySelectorAll("svg[aria-hidden='true']")).toHaveLength(2);
  });
});

describe("Tabs", () => {
  it("moves between tabs with the arrow keys and keeps one tab in the tab order", async () => {
    function Harness() {
      const [value, setValue] = useState<"a" | "b" | "c">("a");
      return (
        <Tabs
          label="Sections"
          idPrefix="t"
          value={value}
          onChange={setValue}
          items={[
            { value: "a", label: "First" },
            { value: "b", label: "Second", count: 2, ariaLabel: "Second (2)" },
            { value: "c", label: "Third" },
          ]}
        />
      );
    }
    render(<Harness />);
    const user = userEvent.setup();
    const first = screen.getByRole("tab", { name: "First" });
    expect(first).toHaveAttribute("tabindex", "0");
    first.focus();
    await user.keyboard("{ArrowRight}");
    const second = screen.getByRole("tab", { name: "Second (2)" });
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(second).toHaveFocus();
    expect(first).toHaveAttribute("tabindex", "-1");
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Third" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(first).toHaveFocus();
  });
});

describe("formatting helpers", () => {
  it("formats list times in UTC to the minute", () => {
    expect(formatUtcShort("2026-09-15T06:05:59Z")).toBe("15 Sept 2026, 06:05 UTC");
    expect(formatUtcShort(null)).toBe("—");
  });

  it("shows provider quota fields as readable pairs instead of raw JSON", () => {
    const text = formatQuota({ provider: "youtube_data_api_v3", exhausted: true, units_this_request: 1, nested: { a: 1 } });
    expect(text).toBe("provider: youtube_data_api_v3 · exhausted: yes · units this request: 1.");
    expect(text).not.toContain("{");
  });

  it("builds the case list query with search, tag and sort", () => {
    expect(caseListPath({ status: "active", query: "altyapı", tag: "", sort: "updated_desc", offset: 0 })).toBe(
      "/api/v1/cases?limit=20&offset=0&status=active&q=altyap%C4%B1",
    );
    expect(caseListPath({ status: "", query: "", tag: "infra", sort: "title_asc", offset: 20 })).toBe(
      "/api/v1/cases?limit=20&offset=20&tag=infra&sort=title_asc",
    );
  });

  it("explains unknown timezones, date order, missing attachments and unavailable OCR", () => {
    const base = {
      id: "j1",
      case_id: "c1",
      evidence_id: "e1",
      options: { timezone: "unknown" },
      needs_input: null,
      attempts: 1,
      error_code: null,
      error_detail: null,
      cancel_requested_at: null,
      created_at: "2026-09-16T10:00:00Z",
      updated_at: "2026-09-16T10:00:00Z",
      started_at: null,
      finished_at: null,
    };
    const chat = jobFacts({
      ...base,
      job_type: "whatsapp_export",
      status: "partial",
      result: {
        messages: 2,
        timezone: "unknown",
        date_order: { order: "day_first", basis: "inferred", explanation: "Line 3 has a first number above 12." },
        attachments: { present: 1, missing: 1, omitted_by_export: 0 },
      },
    });
    expect(chat.join(" ")).toMatch(/Local time only on the timeline, never on the UTC timeline/);
    expect(chat.join(" ")).toMatch(/Day first \(31\/12\/2024\) \(inferred\)/);
    expect(chat.join(" ")).toMatch(/1 referenced but missing from the import/);
    const pdf = jobFacts({ ...base, job_type: "document_text", status: "partial", result: { ocr: { status: "unavailable" } } });
    expect(pdf).toEqual(["OCR is not available in this worker, so pages without a text layer produced no text."]);
  });
});
