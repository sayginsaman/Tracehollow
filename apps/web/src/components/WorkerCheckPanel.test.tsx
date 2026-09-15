import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkerCheck } from "@/lib/api-types";

import { POLL_INTERVAL_MS, WAIT_LIMIT_MS, WorkerCheckPanel } from "./WorkerCheckPanel";

const queued: WorkerCheck = {
  id: "7f1c3c1e-0000-4000-8000-000000000001",
  status: "queued",
  requested_at: "2026-09-15T06:00:00.000Z",
  dispatched_at: "2026-09-15T06:00:00.010Z",
  completed_at: null,
  worker_hostname: null,
  error_code: null,
};

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("WorkerCheckPanel", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends the CSRF token and reports the completing worker", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(respond(202, queued))
      .mockResolvedValueOnce(
        respond(200, {
          ...queued,
          status: "completed",
          completed_at: "2026-09-15T06:00:00.042Z",
          worker_hostname: "worker@abc123",
        }),
      );
    render(<WorkerCheckPanel csrfToken="csrf-123" onUnauthorized={() => false} />);

    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Run check" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });

    const init = fetchMock.mock.calls[0]?.[1];
    expect((init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-123");
    expect(await screen.findByText(/Completed by/)).toHaveTextContent("worker@abc123 in 42 ms");
  });

  it("states truthfully when no worker answers in time", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => respond(200, queued));
    render(<WorkerCheckPanel csrfToken="csrf" onUnauthorized={() => false} />);

    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Run check" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAIT_LIMIT_MS + POLL_INTERVAL_MS);
    });

    expect(await screen.findByText(/No worker processed the check/)).toBeInTheDocument();
    expect(screen.queryByText(/Completed by/)).not.toBeInTheDocument();
  });

  it("reports a broker outage when dispatch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      respond(503, { ...queued, status: "dispatch_failed", error_code: "broker_unavailable" }),
    );
    render(<WorkerCheckPanel csrfToken="csrf" onUnauthorized={() => false} />);

    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Run check" }));

    expect(await screen.findByText(/Redis broker is unavailable/)).toBeInTheDocument();
  });
});
