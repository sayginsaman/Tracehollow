"use client";

import {
  Activity as ActivityIcon,
  CircleAlert,
  CircleCheck,
  FileText,
  FolderOpen,
  ListChecks,
  MessageSquareText,
  Plus,
  ServerCrash,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import type { SystemStatus } from "@/lib/api-types";
import { CHECK_LABELS, formatUtcShort } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Activity, CaseSummary, Page, ProcessingJob, QueryRun } from "@/lib/workspace-types";

import { CaseStatusBadge, CaseTags } from "../cases/case-status";
import { ProcessingStatusBadge, jobSummary } from "../cases/ProcessingImports";
import {
  ButtonLink,
  EmptyState,
  ErrorNotice,
  LoadingState,
  PageHeader,
  Panel,
  RunOutcomeBadge,
  StatusBadge,
  SyntheticBadge,
  plural,
  runOutcome,
} from "../ui";

const RECENT_CASES = 6;
const ACTIVITY_LIMIT = 8;

interface AttentionItem {
  key: string;
  icon: LucideIcon;
  tone: "warn" | "bad";
  title: string;
  detail: string;
  href: string;
  time?: string | null;
}

function jobLabel(job: ProcessingJob): string {
  return job.job_type === "whatsapp_export" ? "WhatsApp export" : "PDF document";
}

/** Items that need an analyst decision or explain a failure, newest first. */
export function attentionItems(activity: Activity, failedDeletions: CaseSummary[], system: SystemStatus | null): AttentionItem[] {
  const titles = new Map(activity.cases.map((item) => [item.id, item.title]));
  const items: AttentionItem[] = [];
  if (system && !system.ready) {
    const failing = Object.entries(system.checks)
      .filter(([, check]) => check.status !== "ok")
      .map(([name]) => CHECK_LABELS[name] ?? name);
    items.push({
      key: "system",
      icon: ServerCrash,
      tone: "bad",
      title: "Required services are unavailable",
      detail: failing.join(", ") || "See Environment status",
      href: "/status",
    });
  }
  for (const job of activity.jobs_needing_input) {
    items.push({
      key: `input-${job.id}`,
      icon: MessageSquareText,
      tone: "warn",
      title: `${jobLabel(job)} needs your answer`,
      detail: `${job.needs_input?.question ?? "Processing is waiting for a decision."} · ${titles.get(job.case_id) ?? "Case"}`,
      href: `/cases/${job.case_id}/imports`,
      time: job.updated_at,
    });
  }
  for (const job of activity.processing_jobs) {
    if (job.status !== "failed") continue;
    items.push({
      key: `job-${job.id}`,
      icon: FileText,
      tone: "bad",
      title: `${jobLabel(job)} could not be processed`,
      detail: `${job.error_detail ?? "Processing failed."} · ${titles.get(job.case_id) ?? "Case"}`,
      href: `/cases/${job.case_id}/imports`,
      time: job.finished_at ?? job.updated_at,
    });
  }
  for (const run of activity.runs) {
    const outcome = runOutcome(run);
    if (run.status !== "failed" && run.status !== "partial") continue;
    items.push({
      key: `run-${run.id}`,
      icon: ListChecks,
      tone: outcome.tone === "bad" ? "bad" : "warn",
      title: `${run.saved_query_name ?? "Query"} #${run.run_number}: ${outcome.label.toLowerCase()}`,
      detail: titles.get(run.case_id) ?? "Case",
      href: `/cases/${run.case_id}/runs/${run.id}`,
      time: run.finished_at ?? run.queued_at,
    });
  }
  for (const item of failedDeletions) {
    items.push({
      key: `deletion-${item.id}`,
      icon: Trash2,
      tone: "bad",
      title: "Case deletion failed",
      detail: item.title,
      href: "/cases",
      time: item.updated_at,
    });
  }
  return items;
}

type ActivityRow = { kind: "run"; at: string; run: QueryRun } | { kind: "job"; at: string; job: ProcessingJob };

export function activityRows(activity: Activity, limit = ACTIVITY_LIMIT): ActivityRow[] {
  const rows: ActivityRow[] = [
    ...activity.runs.map((run) => ({ kind: "run" as const, at: run.queued_at, run })),
    ...activity.processing_jobs.map((job) => ({ kind: "job" as const, at: job.created_at, job })),
  ];
  return rows.sort((a, b) => b.at.localeCompare(a.at)).slice(0, limit);
}

export function WorkspaceOverview() {
  const { session } = useSession();
  const activity = useResource<Activity>(`/api/v1/activity?limit=${ACTIVITY_LIMIT}`);
  const cases = useResource<Page<CaseSummary>>(`/api/v1/cases?status=active&limit=${RECENT_CASES}`);
  const failedDeletions = useResource<Page<CaseSummary>>("/api/v1/cases?status=deletion_failed&limit=5");
  const system = useResource<SystemStatus>("/api/v1/system/status");

  const titles = new Map(activity.data?.cases.map((item) => [item.id, item.title]) ?? []);
  const attention = activity.data ? attentionItems(activity.data, failedDeletions.data?.items ?? [], system.data) : [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description={<>Signed in as {session.user.username}. Recent work across the cases you can open.</>}
        actions={
          <>
            <ButtonLink href="/cases" icon={FolderOpen}>
              All cases
            </ButtonLink>
            <ButtonLink href="/cases?new=1" variant="primary" icon={Plus}>
              New case
            </ButtonLink>
          </>
        }
      />

      <div className="grid items-start gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-start-3 lg:row-start-1">
          <Panel title="Needs attention" flush>
            {activity.state === "error" && !activity.data ? (
              <div className="p-4">
                <ErrorNotice error={activity.error} onRetry={() => void activity.reload()} />
              </div>
            ) : null}
            {!activity.data && activity.state !== "error" ? <LoadingState label="Loading items that need attention…" className="p-4" /> : null}
            {activity.data && attention.length === 0 ? (
              <p className="flex items-center gap-2 px-4 py-4 text-sm text-muted">
                <CircleCheck aria-hidden="true" className="size-4 text-ok" />
                Nothing is waiting for a decision, and no recent run or import failed.
              </p>
            ) : null}
            {attention.length > 0 ? (
              <ul className="divide-y divide-line">
                {attention.map((item) => {
                  const Icon = item.icon;
                  return (
                    <li key={item.key}>
                      <Link href={item.href} className="flex gap-3 px-4 py-3 transition-colors hover:bg-sunken">
                        <Icon aria-hidden="true" className={item.tone === "bad" ? "mt-0.5 size-4 shrink-0 text-bad" : "mt-0.5 size-4 shrink-0 text-warn"} />
                        <span className="min-w-0 flex-1">
                          <span className="block font-medium break-words text-ink">{item.title}</span>
                          <span className="mt-0.5 line-clamp-2 block text-xs break-words text-muted">{item.detail}</span>
                          {item.time ? <span className="mt-0.5 block text-xs text-muted tabular-nums">{formatUtcShort(item.time)}</span> : null}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </Panel>

          <Panel title="Environment" actions={<Link href="/status" className="text-sm text-accent hover:underline">Details</Link>}>
            {system.state === "error" && !system.data ? <ErrorNotice error={system.error} onRetry={() => void system.reload()} /> : null}
            {!system.data && system.state !== "error" ? <LoadingState label="Checking services…" rows={2} /> : null}
            {system.data ? (
              <div className="space-y-2 text-sm">
                {system.data.ready ? (
                  <StatusBadge tone="ok" label="Required services respond" />
                ) : (
                  <StatusBadge tone="bad" icon={CircleAlert} label="Some services are unavailable" />
                )}
                {activity.data ? (
                  <p className="flex items-center gap-2 text-muted">
                    <ActivityIcon aria-hidden="true" className="size-4 shrink-0" />
                    In progress: {plural(activity.data.active_runs, "run")}, {plural(activity.data.active_processing_jobs, "processing job")}
                  </p>
                ) : null}
              </div>
            ) : null}
          </Panel>
        </div>

        <div className="space-y-6 lg:col-span-2 lg:row-start-1">
          <Panel title="Recent cases" flush actions={<Link href="/cases" className="text-sm text-accent hover:underline">View all</Link>}>
            {cases.state === "error" && !cases.data ? (
              <div className="p-4">
                <ErrorNotice error={cases.error} onRetry={() => void cases.reload()} />
              </div>
            ) : null}
            {!cases.data && cases.state !== "error" ? <LoadingState label="Loading cases…" className="p-4" /> : null}
            {cases.data && cases.data.items.length === 0 ? (
              <div className="p-4">
                <EmptyState
                  icon={FolderOpen}
                  title="No active cases"
                  action={
                    <ButtonLink href="/cases?new=1" variant="primary" icon={Plus}>
                      Create a case
                    </ButtonLink>
                  }
                >
                  A case records why an investigation exists and what is in scope. Collection runs, imports, evidence and
                  reports all belong to a case.
                </EmptyState>
              </div>
            ) : null}
            {cases.data && cases.data.items.length > 0 ? (
              <ul className="divide-y divide-line">
                {cases.data.items.map((item) => (
                  <li key={item.id} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5 px-4 py-3">
                    <div className="min-w-0 flex-1 basis-72">
                      <Link href={`/cases/${item.id}`} className="font-medium break-words text-accent hover:underline">
                        {item.title}
                      </Link>
                      {item.purpose ? <p className="mt-0.5 line-clamp-1 text-xs text-muted">{item.purpose}</p> : null}
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <CaseTags tags={item.tags} limit={2} />
                      <CaseStatusBadge status={item.status} />
                      <span className="text-xs text-muted tabular-nums">Updated {formatUtcShort(item.updated_at)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            ) : null}
          </Panel>

          <Panel title="Recent collection and imports" flush>
            {activity.state === "error" && !activity.data ? (
              <div className="p-4">
                <ErrorNotice error={activity.error} onRetry={() => void activity.reload()} />
              </div>
            ) : null}
            {!activity.data && activity.state !== "error" ? <LoadingState label="Loading recent activity…" className="p-4" /> : null}
            {activity.data && activityRows(activity.data).length === 0 ? (
              <p className="px-4 py-4 text-sm text-muted">
                Query runs and imports that need processing (WhatsApp exports, PDFs) appear here once a case has some.
              </p>
            ) : null}
            {activity.data && activityRows(activity.data).length > 0 ? (
              <ul className="divide-y divide-line">
                {activityRows(activity.data).map((row) =>
                  row.kind === "run" ? (
                    <li key={`run-${row.run.id}`} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5 px-4 py-3">
                      <div className="flex min-w-0 flex-1 basis-72 gap-3">
                        <ListChecks aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
                        <div className="min-w-0">
                          <Link href={`/cases/${row.run.case_id}/runs/${row.run.id}`} className="font-medium break-words text-accent hover:underline">
                            {row.run.saved_query_name ?? row.run.parameters_snapshot.saved_query_name ?? "Query"} · run #{row.run.run_number}
                          </Link>
                          <p className="mt-0.5 text-xs break-words text-muted">
                            Query run in {titles.get(row.run.case_id) ?? "a case"}
                          </p>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {row.run.synthetic ? <SyntheticBadge /> : null}
                        <RunOutcomeBadge run={row.run} />
                        <span className="text-xs text-muted tabular-nums">{formatUtcShort(row.at)}</span>
                      </div>
                    </li>
                  ) : (
                    <li key={`job-${row.job.id}`} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5 px-4 py-3">
                      <div className="flex min-w-0 flex-1 basis-72 gap-3">
                        <FileText aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
                        <div className="min-w-0">
                          <Link href={`/cases/${row.job.case_id}/imports`} className="font-medium break-words text-accent hover:underline">
                            {jobLabel(row.job)}
                          </Link>
                          <p className="mt-0.5 text-xs break-words text-muted">
                            {[jobSummary(row.job), `Import in ${titles.get(row.job.case_id) ?? "a case"}`].filter(Boolean).join(" · ")}
                          </p>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <ProcessingStatusBadge status={row.job.status} />
                        <span className="text-xs text-muted tabular-nums">{formatUtcShort(row.at)}</span>
                      </div>
                    </li>
                  ),
                )}
              </ul>
            ) : null}
          </Panel>
        </div>
      </div>
    </div>
  );
}
