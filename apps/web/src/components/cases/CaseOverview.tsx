"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { CaseDetail, Page, QueryRun } from "@/lib/workspace-types";

import { Button, EmptyState, ErrorNotice, Field, KeyValue, LoadingState, RunStatusBadge, Section, SyntheticBadge, TextArea, TextInput } from "../ui";
import { useCase } from "./CaseContext";
import { parseTags } from "./CaseList";
import { NotesPanel } from "./NotesPanel";

export function CaseOverview() {
  const { caseDetail, apiBase, base, writable, setCase, refreshCase } = useCase();
  const { mutate } = useSession();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runs = useResource<Page<QueryRun>>(`${apiBase}/runs?limit=5`);

  // Counts may have changed on other tabs since the case layout loaded.
  useEffect(() => {
    void refreshCase();
  }, [refreshCase]);

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
      setEditing(false);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function changeLifecycle(action: "archive" | "restore") {
    setBusy(true);
    setError(null);
    try {
      setCase(await mutate<CaseDetail>(`${apiBase}/${action}`));
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  const counts = caseDetail.counts;
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <Section
          title="Case details"
          actions={
            <>
              {writable && !editing ? <Button onClick={() => setEditing(true)}>Edit</Button> : null}
              {caseDetail.status === "active" ? (
                <Button onClick={() => void changeLifecycle("archive")} disabled={busy}>
                  Archive
                </Button>
              ) : null}
              {caseDetail.status === "archived" ? (
                <Button variant="primary" onClick={() => void changeLifecycle("restore")} disabled={busy}>
                  Restore
                </Button>
              ) : null}
            </>
          }
        >
          {error ? <p role="alert" className="mb-3 text-sm text-bad">{error}</p> : null}
          {editing ? (
            <form onSubmit={save} className="space-y-3">
              <Field label="Title" htmlFor="edit-title">
                <TextInput id="edit-title" name="title" defaultValue={caseDetail.title} required maxLength={200} />
              </Field>
              <Field label="Purpose" htmlFor="edit-purpose">
                <TextArea id="edit-purpose" name="purpose" defaultValue={caseDetail.purpose} rows={3} />
              </Field>
              <Field label="Scope" htmlFor="edit-scope">
                <TextArea id="edit-scope" name="scope" defaultValue={caseDetail.scope} rows={3} />
              </Field>
              <Field label="Tags" htmlFor="edit-tags" hint="Comma-separated">
                <TextInput id="edit-tags" name="tags" defaultValue={caseDetail.tags.join(", ")} />
              </Field>
              <div className="flex gap-2">
                <Button type="submit" variant="primary" disabled={busy} aria-busy={busy}>
                  Save changes
                </Button>
                <Button onClick={() => setEditing(false)}>Cancel</Button>
              </div>
            </form>
          ) : (
            <KeyValue
              items={[
                ["Purpose", caseDetail.purpose ? <span className="whitespace-pre-wrap">{caseDetail.purpose}</span> : "—"],
                ["Scope", caseDetail.scope ? <span className="whitespace-pre-wrap">{caseDetail.scope}</span> : "—"],
                ["Created", formatUtc(caseDetail.created_at)],
                ["Last updated", formatUtc(caseDetail.updated_at)],
                ["Archived", formatUtc(caseDetail.archived_at)],
              ]}
            />
          )}
        </Section>

        <Section
          title="Recent executions"
          actions={<Link href={`${base}/queries`} className="text-sm text-accent hover:underline">All queries and runs</Link>}
        >
          {runs.state === "error" ? <ErrorNotice error={runs.error} onRetry={() => void runs.reload()} /> : null}
          {runs.state === "loading" && !runs.data ? <LoadingState /> : null}
          {runs.data && runs.data.items.length === 0 ? <EmptyState>No executions yet.</EmptyState> : null}
          {runs.data && runs.data.items.length > 0 ? (
            <ul className="divide-y divide-line text-sm">
              {runs.data.items.map((run) => (
                <li key={run.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <Link href={`${base}/runs/${run.id}`} className="text-accent hover:underline">
                    {run.saved_query_name ?? "Deleted query"} · run #{run.run_number}
                  </Link>
                  <span className="flex items-center gap-2">
                    {run.synthetic ? <SyntheticBadge /> : null}
                    <RunStatusBadge status={run.status} />
                    <span className="font-mono text-xs text-muted">{formatUtc(run.queued_at)}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </Section>

        <NotesPanel title="Case notes" />
      </div>

      <Section title="Records" description="Current totals from the database.">
        <dl className="grid grid-cols-2 gap-3 text-sm">
          {(
            [
              ["Entities", counts.entities, "/entities"],
              ["Relationships", counts.relationships, "/relationships"],
              ["Evidence", counts.evidence, "/evidence"],
              ["Notes", counts.notes, ""],
              ["Saved queries", counts.saved_queries, "/queries"],
              ["Executions", counts.query_runs, "/queries"],
              ["Active executions", counts.active_runs, "/queries"],
            ] as const
          ).map(([label, value, segment]) => (
            <div key={label} className="rounded-md border border-line p-2">
              <dt className="text-xs text-muted">{label}</dt>
              <dd className="text-lg font-semibold">
                {segment ? (
                  <Link href={`${base}${segment}`} className="hover:underline">
                    {value}
                  </Link>
                ) : (
                  value
                )}
              </dd>
            </div>
          ))}
        </dl>
      </Section>
    </div>
  );
}
