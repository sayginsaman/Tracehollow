"use client";

import { RotateCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import type { SessionInfo, SystemStatus, WorkerCheck, WorkerStatus } from "@/lib/api-types";
import { ApiError, apiRequest } from "@/lib/client-api";
import { CHECK_LABELS, CHECK_STATUS_LABELS, describeError } from "@/lib/messages";

import { Button, DataTable, LoadingState, Mono, PageHeader, Panel, StatusBadge, SubHeading, Td, Th, Timestamp, Tr, type Tone } from "./ui";
import { WorkerCheckPanel } from "./WorkerCheckPanel";

type Loadable<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; data: T };

const WORKER_TONES: Record<WorkerStatus["status"], Tone> = {
  online: "ok",
  offline: "bad",
  broker_unavailable: "warn",
};

const WORKER_LABELS: Record<WorkerStatus["status"], string> = {
  online: "Online",
  offline: "Offline",
  broker_unavailable: "Unknown: broker unavailable",
};

export function StatusDashboard({ session }: { session: SessionInfo }) {
  const router = useRouter();
  const [system, setSystem] = useState<Loadable<SystemStatus>>({ state: "loading" });
  const [worker, setWorker] = useState<Loadable<WorkerStatus>>({ state: "loading" });
  const [history, setHistory] = useState<Loadable<WorkerCheck[]>>({ state: "loading" });
  const [refreshing, setRefreshing] = useState(false);

  const handleUnauthorized = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        router.replace("/login");
        router.refresh();
        return true;
      }
      return false;
    },
    [router],
  );

  // Previously loaded results stay visible while a refresh is in flight.
  const load = useCallback(async () => {
    setRefreshing(true);
    const settle = async <T,>(path: string, set: (value: Loadable<T>) => void) => {
      try {
        set({ state: "ready", data: await apiRequest<T>(path) });
      } catch (error) {
        if (!handleUnauthorized(error)) set({ state: "error", message: describeError(error) });
      }
    };
    await Promise.all([
      settle<SystemStatus>("/api/v1/system/status", setSystem),
      settle<WorkerStatus>("/api/v1/system/worker", setWorker),
      settle<WorkerCheck[]>("/api/v1/system/worker-checks?limit=5", setHistory),
    ]);
    setRefreshing(false);
  }, [handleUnauthorized]);

  useEffect(() => {
    // Initial load of live status after mount; state updates happen when requests settle.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Environment status"
        description="Live checks of the services this installation depends on. Nothing here collects data."
        actions={
          <Button icon={RotateCw} onClick={() => void load()} disabled={refreshing} busy={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Panel
          title="API dependencies"
          flush
          actions={system.state === "ready" ? <StatusBadge tone={system.data.ready ? "ok" : "bad"} label={system.data.ready ? "Ready" : "Not ready"} /> : null}
        >
          {system.state === "loading" ? <LoadingState label="Checking dependencies…" className="p-4" /> : null}
          {system.state === "error" ? <p className="px-4 py-3 text-sm text-bad">{system.message}</p> : null}
          {system.state === "ready" ? (
            <>
              <DataTable caption="API dependency checks">
                <thead>
                  <tr>
                    <Th>Dependency</Th>
                    <Th>Status</Th>
                    <Th>Detail</Th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(system.data.checks).map(([name, check]) => (
                    <Tr key={name}>
                      <Td className="font-medium text-ink">{CHECK_LABELS[name] ?? name}</Td>
                      <Td>
                        <StatusBadge tone={check.status === "ok" ? "ok" : "bad"} label={CHECK_STATUS_LABELS[check.status] ?? check.status} />
                      </Td>
                      <Td className="text-muted">{check.detail ? <Mono>{check.detail}</Mono> : "None"}</Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
              <p className="border-t border-line px-4 py-2.5 text-xs text-muted">
                API {system.data.api_version} ({system.data.environment}) · checked <Timestamp value={system.data.checked_at} />
              </p>
            </>
          ) : null}
        </Panel>

        <Panel
          title="Background worker"
          description="Reported separately from API readiness: the API can be ready while no worker is running."
          actions={worker.state === "ready" ? <StatusBadge tone={WORKER_TONES[worker.data.status]} label={WORKER_LABELS[worker.data.status]} /> : null}
        >
          <div className="space-y-4 text-sm">
            {worker.state === "loading" ? <LoadingState label="Checking workers…" rows={2} /> : null}
            {worker.state === "error" ? <p className="text-bad">{worker.message}</p> : null}
            {worker.state === "ready" ? (
              <div className="space-y-1.5">
                <p className="text-ink">{worker.data.note}</p>
                {worker.data.workers.length > 0 ? (
                  <ul className="flex flex-wrap gap-1.5">
                    {worker.data.workers.map((node) => (
                      <li key={node.name}>
                        <Mono className="rounded bg-sunken px-1.5 py-0.5">{node.name}</Mono>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p className="text-xs text-muted">
                  Checked <Timestamp value={worker.data.checked_at} />
                </p>
              </div>
            ) : null}
            <WorkerCheckPanel csrfToken={session.csrf_token} onUnauthorized={handleUnauthorized} onFinished={() => void load()} />
            <WorkerHistory history={history} />
          </div>
        </Panel>
      </div>

      <Panel title="About this installation">
        <div className="max-w-[72ch] space-y-2 text-sm text-muted">
          <p>
            Connectors, their access methods and verification status are listed on{" "}
            <Link href="/sources" className="text-accent underline">
              Sources
            </Link>
            . AI processing location and model status are shown on each case&apos;s AI page. Implementation and verification status is recorded in
            docs/STATUS.md.
          </p>
          <p className="text-xs">
            Your session expires <Timestamp value={session.expires_at} /> or after inactivity.
          </p>
        </div>
      </Panel>
    </div>
  );
}

function WorkerHistory({ history }: { history: Loadable<WorkerCheck[]> }) {
  if (history.state === "loading") return null;
  if (history.state === "error") return <p className="text-bad">{history.message}</p>;
  if (history.data.length === 0) {
    return <p className="text-muted">No connectivity checks have been recorded yet.</p>;
  }
  return (
    <div className="space-y-2">
      <div>
        <SubHeading>Recent connectivity checks</SubHeading>
        <p className="text-xs text-muted">Stored in PostgreSQL; they remain after restarts.</p>
      </div>
      <ul className="divide-y divide-line rounded-md border border-line">
        {history.data.map((check) => (
          <li key={check.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
            <span className="text-muted">
              <Timestamp value={check.requested_at} />
            </span>
            <span className="flex items-center gap-2">
              {check.worker_hostname ? <Mono className="text-muted">{check.worker_hostname}</Mono> : null}
              <StatusBadge
                tone={check.status === "completed" ? "ok" : check.status === "queued" ? "neutral" : "bad"}
                label={check.status === "dispatch_failed" ? "Dispatch failed" : check.status === "completed" ? "Completed" : "Queued"}
              />
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
