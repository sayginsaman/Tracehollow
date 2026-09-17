"use client";

import { Ban, Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { collectionMode, describeProgress, formatQuota, observationLabel } from "@/lib/connectors";
import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import { TERMINAL_RUN_STATUSES, type Evidence, type Observation, type Page, type QueryRunDetail } from "@/lib/workspace-types";

import { RunChanges } from "../monitors/ChangeSetView";
import { usePageCrumb } from "../shell/ShellContext";
import {
  ActionError,
  Button,
  Disclosure,
  ErrorNotice,
  KeyValue,
  LoadingState,
  Mono,
  Notice,
  OutcomeBadge,
  PageHeader,
  Panel,
  ProvenanceBadge,
  RunOutcomeBadge,
  RunStatusBadge,
  SyntheticBadge,
  Tag,
  Timestamp,
  humanize,
  plural,
  runOutcome,
} from "../ui";
import { useCase } from "./CaseContext";

export const POLL_INTERVAL_MS = 1500;

const ACCESS_OUTCOMES = ["authentication_required", "access_denied"];

export function describeRunState(
  run: Pick<QueryRunDetail, "status" | "cancel_requested_at" | "dispatch_status"> & { connector_runs?: { outcome: string | null }[] },
): string {
  const outcomes = (run.connector_runs ?? []).map((connector) => connector.outcome);
  if (run.status === "queued" && run.dispatch_status === "pending") {
    return "Queued. The broker could not be reached yet; the dispatcher will retry automatically.";
  }
  if (run.status === "queued") return "Queued and waiting for a worker.";
  if (run.status === "running" && run.cancel_requested_at) return "Cancellation requested; stopping after the current page.";
  if (run.status === "running") return "Running. Collected pages are saved as they complete.";
  if (run.status === "partial") return "Finished with incomplete coverage. Collected data is usable but not complete.";
  if (run.status === "failed" && outcomes.some((outcome) => outcome && ACCESS_OUTCOMES.includes(outcome))) {
    return "Failed because the source needs access or configuration. No usable data was collected; see connector outcomes.";
  }
  if (run.status === "failed" && outcomes.includes("rate_limited")) {
    return "Failed because the source limited the request rate. No usable data was collected; see connector outcomes.";
  }
  if (run.status === "failed") return "Failed. No usable data was collected; see connector outcomes.";
  if (run.status === "canceled") return "Canceled. Pages collected before cancellation are kept.";
  return "Completed.";
}

function snapshotValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function RunDetailView({ runId }: { runId: string }) {
  const { apiBase, base, writable, refreshCase, can } = useCase();
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
  const name = run.data ? (run.data.saved_query_name ?? run.data.parameters_snapshot.saved_query_name ?? "Query") : null;
  usePageCrumb(run.data ? `${name} · run #${run.data.run_number}` : null);

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
  if (!run.data) return <LoadingState label="Loading execution…" rows={6} />;
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
  const mode = collectionMode(String(snapshot.collection_mode ?? ""));
  const outcome = runOutcome({ status: data.status, connector_outcomes: data.connector_runs.map((connector) => connector.outcome) });
  const noticeTone = data.status === "completed" ? "ok" : data.status === "queued" || data.status === "running" ? "neutral" : outcome.tone === "bad" ? "bad" : "warn";

  return (
    <div className="space-y-6">
      <PageHeader
        title={`${name} · run #${data.run_number}`}
        meta={
          <>
            <RunOutcomeBadge run={{ status: data.status, connector_outcomes: data.connector_runs.map((connector) => connector.outcome) }} />
            {data.synthetic ? <SyntheticBadge /> : <Tag>{mode.label}</Tag>}
            <span>
              Queued <Timestamp value={data.queued_at} />
            </span>
          </>
        }
        actions={
          <>
            {active && can("queries.run") ? (
              <Button variant="danger" icon={Ban} onClick={() => void cancel()} disabled={busy || Boolean(data.cancel_requested_at)}>
                {data.cancel_requested_at ? "Cancellation requested" : "Cancel execution"}
              </Button>
            ) : null}
            {!active && writable && data.saved_query_id ? (
              <Button variant="primary" icon={Play} onClick={() => void rerun()} disabled={busy} busy={busy}>
                Run again (new execution)
              </Button>
            ) : null}
          </>
        }
      />

      <div aria-live="polite">
        <Notice tone={noticeTone}>{describeRunState(data)}</Notice>
      </div>
      <ActionError message={error} />
      {data.error_code === "budget_exhausted" ? (
        <Notice tone="warn" title="Stopped by a budget">
          A request budget for this case, monitor or run was used up, so no further requests were sent. Pages collected before the limit are kept; coverage is
          incomplete and no item is treated as removed.
        </Notice>
      ) : null}
      {data.error_code === "authorization_revoked" ? (
        <Notice tone="warn" title="Stopped because access changed">
          The account that authorized this execution no longer has analyst access to the case, so no further work was done. Collected pages are kept.
        </Notice>
      ) : null}
      {data.results_expired_at ? (
        <Notice tone="neutral" title="Collected results removed by retention">
          The case retention policy removed this run&apos;s evidence and observations <Timestamp value={data.results_expired_at} />. The execution record, its
          outcomes and a tombstone remain; citations to removed records say so.
        </Notice>
      ) : null}
      <p className="max-w-[72ch] text-sm text-muted">
        {data.synthetic
          ? "This execution used the synthetic fixture connector. Its results are generated test data, not observations of any real account, domain or person."
          : `${mode.label}: ${mode.explanation} Candidate accounts and other matches are leads to review, not identity assertions.`}
      </p>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-6">
          <Panel title="Connector outcomes" description="Each source reports an explicit outcome; failures and incomplete coverage are never shown as empty success." flush>
            <ul className="divide-y divide-line">
              {data.connector_runs.map((connector) => {
                const maxPages = Number(connector.coverage.max_pages ?? 0);
                const percent = maxPages ? Math.min(100, Math.round((connector.pages_completed / maxPages) * 100)) : 0;
                const progress = describeProgress(connector.coverage);
                return (
                  <li key={connector.id} className="space-y-3 px-4 py-4 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <Mono className="text-ink">
                        {connector.connector_id} {connector.connector_version}
                      </Mono>
                      <span className="flex flex-wrap items-center gap-1.5">
                        <RunStatusBadge status={connector.status} />
                        <OutcomeBadge outcome={connector.outcome} />
                      </span>
                    </div>
                    {connector.coverage_note ? <p className="max-w-[72ch] text-read text-ink">{connector.coverage_note}</p> : null}
                    {maxPages ? (
                      <div>
                        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
                          <span>
                            {connector.pages_completed} of up to {plural(maxPages, "page")} · {plural(connector.items_collected, "item")}
                          </span>
                          <span>
                            {plural(connector.fetch_attempts, "fetch attempt")}, {plural(connector.retries, "retry", "retries")}
                          </span>
                        </div>
                        <div
                          role="progressbar"
                          aria-label="Pages collected"
                          aria-valuemin={0}
                          aria-valuemax={maxPages}
                          aria-valuenow={connector.pages_completed}
                          className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-sunken"
                        >
                          <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${percent}%` }} />
                        </div>
                      </div>
                    ) : null}
                    {progress ? (
                      <p className="text-xs text-muted" aria-live="polite">
                        {progress}
                      </p>
                    ) : null}
                    {connector.last_error_code ? (
                      <p className="rounded-md bg-sunken px-3 py-2 text-sm text-ink">
                        <span className="font-medium">Last source error: {humanize(connector.last_error_code)}.</span>
                        {connector.last_error_detail ? ` ${connector.last_error_detail}` : ""}
                        {connector.retry_after_seconds !== null ? ` The source asked to retry after ${Math.round(connector.retry_after_seconds)} seconds.` : ""}
                      </p>
                    ) : null}
                    <p className="text-xs text-muted">Quota and cost: {formatQuota(connector.quota_usage)}</p>
                  </li>
                );
              })}
            </ul>
          </Panel>

          {TERMINAL_RUN_STATUSES.includes(data.status) ? <RunChanges runId={runId} /> : null}

          <Panel title="Evidence collected by this run" flush>
            {evidence.state === "error" ? (
              <div className="p-4">
                <ErrorNotice error={evidence.error} />
              </div>
            ) : null}
            {evidence.data && evidence.data.items.length === 0 ? (
              <p className="px-4 py-4 text-sm text-muted">{active ? "No pages saved yet." : "This run saved no evidence."}</p>
            ) : null}
            <ul className="divide-y divide-line">
              {evidence.data?.items.map((item) => (
                <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                  <Link href={`${base}/evidence/${item.id}`} className="min-w-0 break-words text-accent hover:underline">
                    {item.title}
                  </Link>
                  <ProvenanceBadge evidence={item} />
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Observations" flush>
            {observations.state === "error" ? (
              <div className="p-4">
                <ErrorNotice error={observations.error} />
              </div>
            ) : null}
            {observations.data && observations.data.items.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No observations.</p> : null}
            <ul className="divide-y divide-line">
              {observations.data?.items.map((observation) => {
                const label = observationLabel(observation);
                return (
                  <li key={observation.id} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-2.5 text-sm">
                    {observation.entity_id ? (
                      <Link href={`${base}/entities/${observation.entity_id}`} className="min-w-0 break-words text-accent hover:underline">
                        {label.primary}
                      </Link>
                    ) : observation.evidence_id ? (
                      <Link href={`${base}/evidence/${observation.evidence_id}`} className="min-w-0 break-words text-accent hover:underline">
                        {label.primary}
                      </Link>
                    ) : (
                      <span className="min-w-0 break-words">{label.primary}</span>
                    )}
                    <span className="text-xs text-muted">{label.secondary}</span>
                  </li>
                );
              })}
            </ul>
          </Panel>
        </div>

        <div className="min-w-0 space-y-6 lg:sticky lg:top-20">
          <Panel title="Execution">
            <KeyValue
              compact
              items={[
                ["Queued", <Timestamp key="queued" value={data.queued_at} />],
                ["Started", <Timestamp key="started" value={data.started_at} fallback="Not started" />],
                ["Finished", <Timestamp key="finished" value={data.finished_at} fallback={active ? "Still running" : "Not recorded"} />],
                ...(data.cancel_requested_at ? ([["Cancel requested", <Timestamp key="cancel" value={data.cancel_requested_at} />]] as [string, React.ReactNode][]) : []),
                ["Dispatch", data.dispatch_status ? humanize(data.dispatch_status) : "Not recorded"],
                [
                  "Execution error",
                  data.error_code === "internal_error"
                    ? "Internal error inside Tracehollow; the source outcome is unknown"
                    : data.error_code === "worker_lost"
                      ? "Stopped after repeated worker interruptions"
                      : data.error_code === "budget_exhausted"
                        ? "Stopped by a budget"
                        : data.error_code === "authorization_revoked"
                          ? "Stopped: authorizing account lost access"
                          : data.error_code
                            ? humanize(data.error_code)
                        : "None",
                ],
                ["Evidence records", String(data.evidence_count)],
                ["Observations", String(data.observation_count)],
                ["New entities", String(data.entity_count)],
                ["New relationships", String(data.relationship_count)],
              ]}
            />
          </Panel>
          <Panel title="Parameter snapshot" description="Captured when the run was created; later edits to the saved query do not change it.">
            <KeyValue
              compact
              items={[
                ["Input", <span key="input">{humanize(String(snapshot.input_type ?? ""))}: <Mono className="text-ink">{String(snapshot.input_value ?? "")}</Mono></span>],
                ...Object.entries(snapshot.parameters ?? {}).map(([key, value]) => [humanize(key), snapshotValue(value)] as [string, React.ReactNode]),
                ...Object.entries(snapshot.limits ?? {}).map(([key, value]) => [humanize(key), snapshotValue(value)] as [string, React.ReactNode]),
                ["Connectors", (snapshot.connectors ?? []).map((connector) => `${connector.id} ${connector.version}`).join(", ") || "Not recorded"],
                ["Captured", <Timestamp key="captured" value={snapshot.captured_at ?? null} />],
              ]}
            />
            <Disclosure summary="Raw snapshot" className="mt-4">
              <pre className="max-h-72 overflow-auto font-mono text-code whitespace-pre-wrap break-words text-ink">{JSON.stringify(snapshot, null, 2)}</pre>
            </Disclosure>
          </Panel>
        </div>
      </div>
    </div>
  );
}
