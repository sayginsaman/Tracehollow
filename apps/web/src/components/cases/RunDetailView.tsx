"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import { TERMINAL_RUN_STATUSES, type Evidence, type Observation, type Page, type QueryRunDetail } from "@/lib/workspace-types";

import {
  Button,
  EmptyState,
  ErrorNotice,
  KeyValue,
  LoadingState,
  Mono,
  Notice,
  OutcomeBadge,
  RunStatusBadge,
  Section,
  SyntheticBadge,
  humanize,
} from "../ui";
import { useCase } from "./CaseContext";

export const POLL_INTERVAL_MS = 1500;

export function describeRunState(run: Pick<QueryRunDetail, "status" | "cancel_requested_at" | "dispatch_status">): string {
  if (run.status === "queued" && run.dispatch_status === "pending") {
    return "Queued. The broker could not be reached yet; the dispatcher will retry automatically.";
  }
  if (run.status === "queued") return "Queued and waiting for a worker.";
  if (run.status === "running" && run.cancel_requested_at) return "Cancellation requested; stopping after the current page.";
  if (run.status === "running") return "Running. Collected pages are saved as they complete.";
  if (run.status === "partial") return "Finished with incomplete coverage. Collected data is usable but not complete.";
  if (run.status === "failed") return "Failed. No usable data was collected; see connector outcomes.";
  if (run.status === "canceled") return "Canceled. Pages collected before cancellation are kept.";
  return "Completed.";
}

export function RunDetailView({ runId }: { runId: string }) {
  const { apiBase, base, writable, refreshCase } = useCase();
  const { mutate } = useSession();
  const router = useRouter();
  const run = useResource<QueryRunDetail>(`${apiBase}/runs/${runId}`);
  const evidence = useResource<Page<Evidence>>(`${apiBase}/evidence?query_run_id=${runId}&limit=50`);
  const observations = useResource<Page<Observation>>(`${apiBase}/observations?query_run_id=${runId}&limit=50`);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const status = run.data?.status;
  const active = status !== undefined && !TERMINAL_RUN_STATUSES.includes(status);
  const { reload: reloadRun } = run;
  const { reload: reloadEvidence } = evidence;
  const { reload: reloadObservations } = observations;

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      void reloadRun();
      void reloadEvidence();
      void reloadObservations();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [active, reloadRun, reloadEvidence, reloadObservations]);

  useEffect(() => {
    if (status && TERMINAL_RUN_STATUSES.includes(status)) {
      void reloadEvidence();
      void reloadObservations();
      void refreshCase();
    }
  }, [status, reloadEvidence, reloadObservations, refreshCase]);

  if (run.state === "error" && !run.data) return <ErrorNotice error={run.error} onRetry={() => void run.reload()} />;
  if (!run.data) return <LoadingState label="Loading execution…" />;
  const data = run.data;

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      await mutate(`${apiBase}/runs/${runId}/cancel`);
      await run.reload();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function rerun() {
    if (!data.saved_query_id) return;
    setBusy(true);
    setError(null);
    try {
      const next = await mutate<QueryRunDetail>(`${apiBase}/saved-queries/${data.saved_query_id}/runs`);
      router.push(`${base}/runs/${next.id}`);
    } catch (caught) {
      setError(describeError(caught));
      setBusy(false);
    }
  }

  const snapshot = data.parameters_snapshot;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link href={`${base}/queries`} className="text-sm text-accent hover:underline">
          ← Queries & runs
        </Link>
        <h2 className="text-xl font-semibold">
          {data.saved_query_name ?? snapshot.saved_query_name ?? "Query"} · run #{data.run_number}
        </h2>
        <RunStatusBadge status={data.status} />
        {data.synthetic ? <SyntheticBadge /> : null}
      </div>
      <div aria-live="polite">
        <Notice tone={data.status === "completed" ? "ok" : data.status === "queued" || data.status === "running" ? "neutral" : "warn"}>
          {describeRunState(data)}
        </Notice>
      </div>
      {error ? <p role="alert" className="rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-sm text-bad">{error}</p> : null}
      {data.synthetic ? (
        <p className="text-xs text-muted">
          This execution used the synthetic fixture connector. Its results are generated test data, not observations of
          any real account, domain or person.
        </p>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {active ? (
          <Button variant="danger" onClick={() => void cancel()} disabled={busy || Boolean(data.cancel_requested_at)}>
            {data.cancel_requested_at ? "Cancellation requested" : "Cancel execution"}
          </Button>
        ) : null}
        {!active && writable && data.saved_query_id ? (
          <Button variant="primary" onClick={() => void rerun()} disabled={busy}>
            Run again (new execution)
          </Button>
        ) : null}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Execution">
          <KeyValue
            items={[
              ["Queued", formatUtc(data.queued_at)],
              ["Started", formatUtc(data.started_at)],
              ["Finished", formatUtc(data.finished_at)],
              ["Cancel requested", formatUtc(data.cancel_requested_at)],
              ["Dispatch", data.dispatch_status ? humanize(data.dispatch_status) : "—"],
              ["Evidence records", String(data.evidence_count)],
              ["Observations", String(data.observation_count)],
              ["New entities", String(data.entity_count)],
              ["New relationships", String(data.relationship_count)],
            ]}
          />
        </Section>
        <Section title="Parameter snapshot" description="Captured when the run was created; later edits to the saved query do not change it.">
          <KeyValue
            items={[
              ["Input", `${humanize(String(snapshot.input_type ?? ""))}: ${String(snapshot.input_value ?? "")}`],
              ["Parameters", <Mono key="params">{JSON.stringify(snapshot.parameters ?? {})}</Mono>],
              ["Limits", <Mono key="limits">{JSON.stringify(snapshot.limits ?? {})}</Mono>],
              [
                "Connectors",
                (snapshot.connectors ?? []).map((connector) => `${connector.id} ${connector.version}`).join(", ") || "—",
              ],
              ["Captured", formatUtc(snapshot.captured_at ?? null)],
            ]}
          />
        </Section>
      </div>

      <Section title="Connector outcomes" description="Each source reports an explicit outcome; failures and incomplete coverage are never shown as empty success.">
        <ul className="space-y-3">
          {data.connector_runs.map((connector) => {
            const maxPages = Number(connector.coverage.max_pages ?? 0);
            const percent = maxPages ? Math.min(100, Math.round((connector.pages_completed / maxPages) * 100)) : 0;
            return (
              <li key={connector.id} className="rounded-md border border-line p-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-xs">
                    {connector.connector_id} {connector.connector_version}
                  </span>
                  <span className="flex items-center gap-2">
                    <RunStatusBadge status={connector.status} />
                    <OutcomeBadge outcome={connector.outcome} />
                  </span>
                </div>
                {maxPages ? (
                  <div className="mt-2">
                    <div className="flex justify-between text-xs text-muted">
                      <span>
                        {connector.pages_completed} of up to {maxPages} page(s) · {connector.items_collected} item(s)
                      </span>
                      <span>
                        {connector.fetch_attempts} fetch attempt(s), {connector.retries} retr{connector.retries === 1 ? "y" : "ies"}
                      </span>
                    </div>
                    <div
                      role="progressbar"
                      aria-label="Pages collected"
                      aria-valuemin={0}
                      aria-valuemax={maxPages}
                      aria-valuenow={connector.pages_completed}
                      className="mt-1 h-2 rounded bg-canvas"
                    >
                      <div className="h-2 rounded bg-accent" style={{ width: `${percent}%` }} />
                    </div>
                  </div>
                ) : null}
                {connector.coverage_note ? <p className="mt-2">{connector.coverage_note}</p> : null}
                {connector.last_error_code ? (
                  <p className="mt-1 text-xs text-muted">
                    Last source error: {humanize(connector.last_error_code)}
                    {connector.last_error_detail ? ` — ${connector.last_error_detail}` : ""}
                    {connector.retry_after_seconds !== null ? ` (retry after ${connector.retry_after_seconds}s)` : ""}
                  </p>
                ) : null}
                <p className="mt-1 text-xs text-muted">
                  Quota and cost: {connector.quota_usage ? JSON.stringify(connector.quota_usage) : "not applicable for this connector"}
                </p>
              </li>
            );
          })}
        </ul>
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Evidence collected by this run">
          {evidence.state === "error" ? <ErrorNotice error={evidence.error} /> : null}
          {evidence.data && evidence.data.items.length === 0 ? (
            <EmptyState>{active ? "No pages saved yet." : "This run saved no evidence."}</EmptyState>
          ) : null}
          <ul className="space-y-1 text-sm">
            {evidence.data?.items.map((item) => (
              <li key={item.id}>
                <Link href={`${base}/evidence/${item.id}`} className="text-accent hover:underline">
                  {item.title}
                </Link>
              </li>
            ))}
          </ul>
        </Section>
        <Section title="Observations">
          {observations.state === "error" ? <ErrorNotice error={observations.error} /> : null}
          {observations.data && observations.data.items.length === 0 ? <EmptyState>No observations.</EmptyState> : null}
          <ul className="space-y-1 text-sm">
            {observations.data?.items.map((observation) => (
              <li key={observation.id} className="flex flex-wrap justify-between gap-2">
                {observation.entity_id ? (
                  <Link href={`${base}/entities/${observation.entity_id}`} className="text-accent hover:underline">
                    {String(observation.payload.username ?? observation.source_object_id ?? "entity")}
                  </Link>
                ) : (
                  <span>{observation.source_object_id}</span>
                )}
                <span className="text-xs text-muted">{String(observation.payload.platform ?? "")}</span>
              </li>
            ))}
          </ul>
        </Section>
      </div>
    </div>
  );
}
