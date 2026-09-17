import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProcessingJob } from "@/lib/workspace-types";
import { TEST_CASE, renderInCase } from "@/test/workspace";

import { JOB_POLL_INTERVAL_MS, ProcessingJobsPanel } from "./ProcessingImports";

const API = `/api/v1/cases/${TEST_CASE.id}`;

const running: ProcessingJob = {
  id: "job1",
  case_id: TEST_CASE.id,
  evidence_id: "ev1",
  job_type: "document_text",
  status: "running",
  options: { ocr: "if_needed" },
  result: {},
  needs_input: null,
  attempts: 1,
  error_code: null,
  error_detail: null,
  cancel_requested_at: null,
  created_at: "2026-09-16T10:00:00Z",
  updated_at: "2026-09-16T10:00:00Z",
  started_at: "2026-09-16T10:00:01Z",
  finished_at: null,
};

describe("ProcessingJobsPanel", () => {
  it("follows running jobs without a manual refresh and reports when they finish", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const completed: ProcessingJob = {
      ...running,
      status: "completed",
      result: { document_state: "text_extracted", pages_total: 2, pages_processed: 2 },
      finished_at: "2026-09-16T10:00:05Z",
    };
    let calls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (!url.startsWith(`${API}/processing-jobs?`)) return new Response("{}", { status: 404 });
      calls += 1;
      const body = { items: [calls === 1 ? running : completed], total: 1, limit: 25, offset: 0 };
      return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    });
    const onSettled = vi.fn();
    try {
      renderInCase(<ProcessingJobsPanel apiBase={API} base={`/cases/${TEST_CASE.id}`} writable onSettled={onSettled} />);
      expect(await screen.findByText("Processing")).toBeInTheDocument();
      expect(onSettled).not.toHaveBeenCalled();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS);
      });
      expect(await screen.findByText("Text extracted · 2 of 2 pages")).toBeInTheDocument();
      await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS * 3);
      });
      // Finished jobs are not polled.
      expect(calls).toBe(2);
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
    }
  });
});
