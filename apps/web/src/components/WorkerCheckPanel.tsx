"use client";

import { CircleCheck, CircleX, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { WorkerCheck } from "@/lib/api-types";
import { ApiError, apiRequest } from "@/lib/client-api";
import { describeError } from "@/lib/messages";

import { Button, Mono, SubHeading } from "./ui";

export const POLL_INTERVAL_MS = 1000;
export const WAIT_LIMIT_MS = 20_000;

type RunState =
  | { phase: "idle" }
  | { phase: "running"; check: WorkerCheck | null }
  | { phase: "completed"; check: WorkerCheck }
  | { phase: "timed_out"; check: WorkerCheck }
  | { phase: "failed"; message: string };

function isWorkerCheck(value: unknown): value is WorkerCheck {
  return typeof value === "object" && value !== null && "id" in value && "status" in value;
}

function elapsedMs(check: WorkerCheck): number | null {
  if (!check.completed_at) return null;
  return Math.max(0, new Date(check.completed_at).getTime() - new Date(check.requested_at).getTime());
}

export function WorkerCheckPanel({
  csrfToken,
  onUnauthorized,
  onFinished,
}: {
  csrfToken: string;
  onUnauthorized: (error: unknown) => boolean;
  onFinished?: () => void;
}) {
  const [run, setRun] = useState<RunState>({ phase: "idle" });
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;
    return () => {
      cancelled.current = true;
    };
  }, []);

  async function start() {
    setRun({ phase: "running", check: null });
    let check: WorkerCheck;
    try {
      check = await apiRequest<WorkerCheck>("/api/v1/system/worker-checks", { method: "POST", csrfToken });
    } catch (error) {
      if (onUnauthorized(error)) return;
      if (error instanceof ApiError && error.status === 503 && isWorkerCheck(error.payload)) {
        setRun({ phase: "failed", message: "The check could not be queued because the Redis broker is unavailable." });
      } else {
        setRun({ phase: "failed", message: describeError(error) });
      }
      onFinished?.();
      return;
    }

    setRun({ phase: "running", check });
    const deadline = Date.now() + WAIT_LIMIT_MS;
    while (!cancelled.current && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      try {
        check = await apiRequest<WorkerCheck>(`/api/v1/system/worker-checks/${check.id}`);
      } catch (error) {
        if (onUnauthorized(error)) return;
        setRun({ phase: "failed", message: describeError(error) });
        onFinished?.();
        return;
      }
      if (check.status === "completed") {
        setRun({ phase: "completed", check });
        onFinished?.();
        return;
      }
    }
    if (!cancelled.current) {
      setRun({ phase: "timed_out", check });
      onFinished?.();
    }
  }

  const running = run.phase === "running";
  return (
    <div className="space-y-3 rounded-md border border-line p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 basis-64">
          <SubHeading>Broker-to-worker connectivity check</SubHeading>
          <p className="mt-0.5 text-xs text-muted">
            Queues a minimal task through Redis; a worker records completion in PostgreSQL. It does not collect any data.
          </p>
        </div>
        <Button variant="primary" icon={Play} onClick={() => void start()} disabled={running} busy={running}>
          {running ? "Checking…" : "Run check"}
        </Button>
      </div>
      <div aria-live="polite" className="text-sm">
        {run.phase === "running" ? <p className="text-muted">Queued. Waiting for a worker to process the check…</p> : null}
        {run.phase === "completed" ? (
          <p className="flex items-center gap-2 text-ok">
            <CircleCheck aria-hidden="true" className="size-4 shrink-0" />
            <span>
              Completed by <Mono>{run.check.worker_hostname ?? "a worker"}</Mono>
              {elapsedMs(run.check) !== null ? ` in ${elapsedMs(run.check)} ms` : ""}.
            </span>
          </p>
        ) : null}
        {run.phase === "timed_out" ? (
          <p className="flex items-start gap-2 text-bad">
            <CircleX aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            No worker processed the check within {WAIT_LIMIT_MS / 1000} seconds. It stays queued and will complete if a worker starts.
          </p>
        ) : null}
        {run.phase === "failed" ? (
          <p className="flex items-start gap-2 text-bad">
            <CircleX aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {run.message}
          </p>
        ) : null}
      </div>
    </div>
  );
}
