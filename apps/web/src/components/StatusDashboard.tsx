"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import type { SessionInfo, SystemStatus, WorkerCheck, WorkerStatus } from "@/lib/api-types";
import { ApiError, apiRequest } from "@/lib/client-api";
import { CHECK_LABELS, CHECK_STATUS_LABELS, describeError, formatUtc } from "@/lib/messages";

import { StatusBadge, type Tone } from "./StatusBadge";
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
  broker_unavailable: "Unknown — broker unavailable",
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
    <div>
      <div className="space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">Environment status</h1>
            <p className="mt-1 text-sm text-muted">
              Live checks of the services this installation depends on.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={refreshing}
            aria-busy={refreshing}
            className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-canvas disabled:opacity-60"
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>


        <section aria-labelledby="api-heading" className="rounded-lg border border-line bg-surface">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 id="api-heading" className="font-semibold">
              API dependencies
            </h2>
            {system.state === "ready" ? (
              <StatusBadge tone={system.data.ready ? "ok" : "bad"} label={system.data.ready ? "Ready" : "Not ready"} />
            ) : null}
          </div>
          <SystemTable system={system} />
        </section>

        <section aria-labelledby="worker-heading" className="rounded-lg border border-line bg-surface">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 id="worker-heading" className="font-semibold">
              Background worker
            </h2>
            {worker.state === "ready" ? (
              <StatusBadge tone={WORKER_TONES[worker.data.status]} label={WORKER_LABELS[worker.data.status]} />
            ) : null}
          </div>
          <div className="space-y-4 px-4 py-3 text-sm">
            <p className="text-muted">
              Worker health is reported separately from API readiness: the API can be ready while no
              worker is running.
            </p>
            {worker.state === "loading" ? <p>Checking workers…</p> : null}
            {worker.state === "error" ? <p className="text-bad">{worker.message}</p> : null}
            {worker.state === "ready" ? (
              <div>
                <p>{worker.data.note}</p>
                {worker.data.workers.length > 0 ? (
                  <ul className="mt-2 list-inside list-disc font-mono text-xs">
                    {worker.data.workers.map((node) => (
                      <li key={node.name}>{node.name}</li>
                    ))}
                  </ul>
                ) : null}
                <p className="mt-2 text-xs text-muted">Checked {formatUtc(worker.data.checked_at)}</p>
              </div>
            ) : null}
            <WorkerCheckPanel
              csrfToken={session.csrf_token}
              onUnauthorized={handleUnauthorized}
              onFinished={() => void load()}
            />
            <WorkerHistory history={history} />
          </div>
        </section>

        <section aria-labelledby="scope-heading" className="rounded-lg border border-line bg-surface px-4 py-3 text-sm">
          <h2 id="scope-heading" className="font-semibold">
            About this build
          </h2>
          <p className="mt-1 text-muted">
            This build provides cases, entities, relationships, notes, text/JSON evidence imports, saved
            queries and exports. The only connector is a clearly labelled synthetic fixture: live
            public-source collection and AI features are not available yet.
          </p>
          <p className="mt-2 text-xs text-muted">
            Session expires {formatUtc(session.expires_at)} (or after inactivity).
          </p>
        </section>
      </div>
    </div>
  );
}

function SystemTable({ system }: { system: Loadable<SystemStatus> }) {
  if (system.state === "loading") return <p className="px-4 py-3 text-sm">Checking dependencies…</p>;
  if (system.state === "error") return <p className="px-4 py-3 text-sm text-bad">{system.message}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">API dependency checks</caption>
        <thead className="text-xs uppercase tracking-wide text-muted">
          <tr>
            <th scope="col" className="px-4 py-2 font-medium">
              Dependency
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Status
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Detail
            </th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(system.data.checks).map(([name, check]) => (
            <tr key={name} className="border-t border-line">
              <th scope="row" className="px-4 py-2 font-medium">
                {CHECK_LABELS[name] ?? name}
              </th>
              <td className="px-4 py-2">
                <StatusBadge tone={check.status === "ok" ? "ok" : "bad"} label={CHECK_STATUS_LABELS[check.status] ?? check.status} />
              </td>
              <td className="px-4 py-2 font-mono text-xs text-muted">{check.detail ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t border-line px-4 py-2 text-xs text-muted">
        API {system.data.api_version} ({system.data.environment}) · checked {formatUtc(system.data.checked_at)}
      </p>
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
    <div>
      <h3 className="text-sm font-medium">Recent connectivity checks</h3>
      <p className="text-xs text-muted">Stored in PostgreSQL; they remain after restarts.</p>
      <ul className="mt-2 divide-y divide-line rounded-md border border-line">
        {history.data.map((check) => (
          <li key={check.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
            <span className="font-mono text-xs">{formatUtc(check.requested_at)}</span>
            <span className="flex items-center gap-2">
              {check.worker_hostname ? (
                <span className="font-mono text-xs text-muted">{check.worker_hostname}</span>
              ) : null}
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
