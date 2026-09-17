"use client";

import { ChevronRight, FileText, FileUp, ListChecks, MessageSquareText, PenLine, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { describeError, formatUtcShort } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { CaseDetail, Page, ProcessingJob, QueryRun } from "@/lib/workspace-types";

import {
  Button,
  ButtonLink,
  EmptyState,
  ErrorNotice,
  Field,
  FormError,
  KeyValue,
  LoadingState,
  LongValue,
  PageHeader,
  Panel,
  RunOutcomeBadge,
  SubHeading,
  SyntheticBadge,
  TextArea,
  TextInput,
  Timestamp,
  plural,
} from "../ui";
import { useCase } from "./CaseContext";
import { CaseStatusBadge, CaseTags, parseTags } from "./case-status";
import { NotesPanel } from "./NotesPanel";
import { ProcessingStatusBadge, jobSummary } from "./ProcessingImports";

function EditDetails({ onDone }: { onDone: () => void }) {
  const { caseDetail, apiBase, setCase } = useCase();
  const { mutate } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      setCase(
        await mutate<CaseDetail>(apiBase, {
          method: "PATCH",
          body: {
            title: String(form.get("title") ?? ""),
            purpose: String(form.get("purpose") ?? ""),
            scope: String(form.get("scope") ?? ""),
            tags: parseTags(String(form.get("tags") ?? "")),
          },
        }),
      );
      onDone();
    } catch (caught) {
      setError(describeError(caught));
      setBusy(false);
    }
  }

  return (
    <Panel title="Edit case details">
      <form onSubmit={save} className="space-y-4">
        <FormError message={error} />
        <Field label="Title" htmlFor="edit-title">
          <TextInput id="edit-title" name="title" defaultValue={caseDetail.title} required maxLength={200} />
        </Field>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Purpose" htmlFor="edit-purpose">
            <TextArea id="edit-purpose" name="purpose" defaultValue={caseDetail.purpose} rows={4} maxLength={10000} />
          </Field>
          <Field label="Scope" htmlFor="edit-scope">
            <TextArea id="edit-scope" name="scope" defaultValue={caseDetail.scope} rows={4} maxLength={10000} />
          </Field>
        </div>
        <Field label="Tags" htmlFor="edit-tags" hint="Comma-separated">
          <TextInput id="edit-tags" name="tags" defaultValue={caseDetail.tags.join(", ")} maxLength={1000} />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy} busy={busy}>
            Save changes
          </Button>
          <Button onClick={onDone} disabled={busy}>
            Cancel
          </Button>
        </div>
      </form>
    </Panel>
  );
}

type Row = { kind: "run"; at: string; run: QueryRun } | { kind: "job"; at: string; job: ProcessingJob };

export function CaseOverview() {
  const { caseDetail, apiBase, base, writable, refreshCase } = useCase();
  const [editing, setEditing] = useState(false);
  const runs = useResource<Page<QueryRun>>(`${apiBase}/runs?limit=6`);
  const jobs = useResource<Page<ProcessingJob>>(`${apiBase}/processing-jobs?limit=6`);
  const waiting = useResource<Page<ProcessingJob>>(`${apiBase}/processing-jobs?status=needs_input&limit=5`);

  // Counts may have changed on other pages since the case layout loaded.
  useEffect(() => {
    void refreshCase();
  }, [refreshCase]);

  const counts = caseDetail.counts;
  const empty = counts.evidence === 0 && counts.entities === 0 && counts.query_runs === 0;
  const rows: Row[] = [
    ...(runs.data?.items ?? []).map((run) => ({ kind: "run" as const, at: run.queued_at, run })),
    ...(jobs.data?.items ?? []).map((job) => ({ kind: "job" as const, at: job.created_at, job })),
  ]
    .sort((a, b) => b.at.localeCompare(a.at))
    .slice(0, 8);
  const troubled = (runs.data?.items ?? []).filter((run) => run.status === "failed" || run.status === "partial");
  const failedJobs = (jobs.data?.items ?? []).filter((job) => job.status === "failed");
  const attentionCount = (waiting.data?.items.length ?? 0) + troubled.length + failedJobs.length;

  const records: [string, number, string][] = [
    ["Evidence", counts.evidence, "/evidence"],
    ["Entities", counts.entities, "/entities"],
    ["Relationships", counts.relationships, "/relationships"],
    ["Saved queries", counts.saved_queries, "/queries"],
    ["Runs", counts.query_runs, "/queries"],
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={caseDetail.title}
        meta={
          <>
            <CaseStatusBadge status={caseDetail.status} />
            <CaseTags tags={caseDetail.tags} />
            <span className="tabular-nums">Updated {formatUtcShort(caseDetail.updated_at)}</span>
          </>
        }
        actions={
          writable ? (
            <>
              {!editing ? (
                <Button icon={PenLine} onClick={() => setEditing(true)}>
                  Edit details
                </Button>
              ) : null}
              <ButtonLink href={`${base}/imports`} icon={FileUp}>
                Import
              </ButtonLink>
              <ButtonLink href={`${base}/queries?new=1`} variant="primary" icon={Plus}>
                New query
              </ButtonLink>
            </>
          ) : undefined
        }
      />

      {editing ? <EditDetails onDone={() => setEditing(false)} /> : null}

      <div className="flex flex-col gap-6 lg:grid lg:grid-cols-3 lg:items-start">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          {empty && writable ? (
            <EmptyState
              title="This case has no material yet"
              action={
                <>
                  <ButtonLink href={`${base}/queries?new=1`} variant="primary" icon={ListChecks}>
                    Define a query
                  </ButtonLink>
                  <ButtonLink href={`${base}/imports`} icon={FileUp}>
                    Import material
                  </ButtonLink>
                  <ButtonLink href={`${base}/entities`} icon={Plus}>
                    Add an entity
                  </ButtonLink>
                </>
              }
            >
              Collect from public or authorized sources with a saved query, import text, JSON, chat exports or PDFs you are
              authorized to use, or record the entities you already know about.
            </EmptyState>
          ) : null}

          <Panel title="Purpose and scope">
            <div className="grid gap-5 md:grid-cols-2">
              <div>
                <SubHeading>Purpose</SubHeading>
                <p className="mt-1 max-w-[72ch] text-read whitespace-pre-wrap break-words text-ink">
                  {caseDetail.purpose || <span className="text-sm text-muted">No purpose recorded.</span>}
                </p>
              </div>
              <div>
                <SubHeading>Scope</SubHeading>
                <p className="mt-1 max-w-[72ch] text-read whitespace-pre-wrap break-words text-ink">
                  {caseDetail.scope || <span className="text-sm text-muted">No scope recorded.</span>}
                </p>
              </div>
            </div>
          </Panel>

          <Panel
            title="Recent activity"
            description="Query runs and imports that needed processing, newest first."
            flush
            actions={
              <Link href={`${base}/queries`} className="text-sm text-accent hover:underline">
                All runs
              </Link>
            }
          >
            {runs.state === "error" ? (
              <div className="p-4">
                <ErrorNotice error={runs.error} onRetry={() => void runs.reload()} />
              </div>
            ) : null}
            {!runs.data && runs.state !== "error" ? <LoadingState label="Loading activity…" className="p-4" /> : null}
            {runs.data && rows.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No runs or processed imports yet.</p> : null}
            {rows.length > 0 ? (
              <ul className="divide-y divide-line">
                {rows.map((row) =>
                  row.kind === "run" ? (
                    <li key={`run-${row.run.id}`} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5 px-4 py-3">
                      <div className="flex min-w-0 flex-1 basis-64 gap-3">
                        <ListChecks aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
                        <Link href={`${base}/runs/${row.run.id}`} className="min-w-0 font-medium break-words text-accent hover:underline">
                          {row.run.saved_query_name ?? row.run.parameters_snapshot.saved_query_name ?? "Deleted query"} · run #{row.run.run_number}
                        </Link>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {row.run.synthetic ? <SyntheticBadge /> : null}
                        <RunOutcomeBadge run={row.run} />
                        <span className="text-xs text-muted tabular-nums">{formatUtcShort(row.at)}</span>
                      </div>
                    </li>
                  ) : (
                    <li key={`job-${row.job.id}`} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5 px-4 py-3">
                      <div className="flex min-w-0 flex-1 basis-64 gap-3">
                        <FileText aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
                        <div className="min-w-0">
                          <Link href={`${base}/imports`} className="font-medium text-accent hover:underline">
                            {row.job.job_type === "whatsapp_export" ? "WhatsApp export" : "PDF document"}
                          </Link>
                          {jobSummary(row.job) ? <p className="text-xs text-muted">{jobSummary(row.job)}</p> : null}
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

          <NotesPanel title="Case notes" />
        </div>

        {/* On small screens the side column dissolves so Needs attention can come first. */}
        <div className="contents min-w-0 lg:block lg:space-y-6">
          <Panel title="Needs attention" flush className="max-lg:order-first">
            {!runs.data || !jobs.data || !waiting.data ? (
              <LoadingState label="Checking for items that need attention…" rows={2} className="p-4" />
            ) : attentionCount === 0 ? (
              <p className="px-4 py-4 text-sm text-muted">
                Nothing is waiting for you.{counts.active_runs > 0 ? ` ${plural(counts.active_runs, "run")} in progress.` : ""}
              </p>
            ) : (
              <ul className="divide-y divide-line text-sm">
                {waiting.data.items.map((job) => (
                  <li key={job.id}>
                    <Link href={`${base}/imports`} className="flex gap-3 px-4 py-3 hover:bg-sunken">
                      <MessageSquareText aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
                      <span className="min-w-0">
                        <span className="block font-medium text-ink">WhatsApp export needs your answer</span>
                        <span className="block text-xs text-muted">{job.needs_input?.question ?? "Processing is waiting for a decision."}</span>
                      </span>
                    </Link>
                  </li>
                ))}
                {failedJobs.map((job) => (
                  <li key={job.id}>
                    <Link href={`${base}/imports`} className="flex gap-3 px-4 py-3 hover:bg-sunken">
                      <FileText aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-bad" />
                      <span className="min-w-0">
                        <span className="block font-medium text-ink">Import could not be processed</span>
                        <span className="line-clamp-2 block text-xs break-words text-muted">{job.error_detail ?? "Processing failed."}</span>
                      </span>
                    </Link>
                  </li>
                ))}
                {troubled.map((run) => (
                  <li key={run.id}>
                    <Link href={`${base}/runs/${run.id}`} className="flex items-start gap-3 px-4 py-3 hover:bg-sunken">
                      <ListChecks aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium break-words text-ink">
                          {run.saved_query_name ?? "Query"} · run #{run.run_number}
                        </span>
                        <span className="mt-1 block">
                          <RunOutcomeBadge run={run} />
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Records" flush>
            <ul className="divide-y divide-line text-sm">
              {records.map(([label, value, segment]) => (
                <li key={label}>
                  <Link href={`${base}${segment}`} className="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-sunken">
                    <span className="text-ink">{label}</span>
                    <span className="flex items-center gap-1 text-muted tabular-nums">
                      {value}
                      <ChevronRight aria-hidden="true" className="size-4" />
                    </span>
                  </Link>
                </li>
              ))}
              <li className="flex items-center justify-between gap-3 px-4 py-2.5">
                <span className="text-ink">Notes</span>
                <span className="pr-5 text-muted tabular-nums">{counts.notes}</span>
              </li>
            </ul>
          </Panel>

          <Panel title="Case record">
            <KeyValue
              compact
              items={[
                ["Created", <Timestamp key="created" value={caseDetail.created_at} />],
                ["Last updated", <Timestamp key="updated" value={caseDetail.updated_at} />],
                ...(caseDetail.archived_at ? ([["Archived", <Timestamp key="archived" value={caseDetail.archived_at} />]] as [string, React.ReactNode][]) : []),
                ["Case ID", <LongValue key="id" value={caseDetail.id} copyLabel="Copy case ID" />],
              ]}
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}
