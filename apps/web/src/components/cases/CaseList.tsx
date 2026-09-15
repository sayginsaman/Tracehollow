"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { CaseDeletion, CaseDetail, CaseSummary, Page } from "@/lib/workspace-types";

import { StatusBadge } from "../StatusBadge";
import { Button, EmptyState, ErrorNotice, Field, LoadingState, Pagination, Section, TextArea, TextInput } from "../ui";

const PAGE_SIZE = 20;

const STATUS_FILTERS = [
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
  { value: "deletion_failed", label: "Deletion failed" },
  { value: "", label: "All" },
];

export function caseStatusBadge(status: string) {
  const tones = { active: "ok", archived: "neutral", deleting: "warn", deletion_failed: "bad" } as const;
  const labels = { active: "Active", archived: "Archived", deleting: "Deleting", deletion_failed: "Deletion failed" };
  return (
    <StatusBadge
      tone={tones[status as keyof typeof tones] ?? "neutral"}
      label={labels[status as keyof typeof labels] ?? status}
    />
  );
}

export function parseTags(raw: string): string[] {
  return raw
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

export function CaseList() {
  const router = useRouter();
  const { mutate } = useSession();
  const [status, setStatus] = useState("active");
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (status) params.set("status", status);
  if (appliedQuery) params.set("q", appliedQuery);
  const cases = useResource<Page<CaseSummary>>(`/api/v1/cases?${params.toString()}`);
  const deletions = useResource<Page<CaseDeletion>>("/api/v1/case-deletions?limit=5");

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setCreating(true);
    setFormError(null);
    try {
      const created = await mutate<CaseDetail>("/api/v1/cases", {
        body: {
          title: String(form.get("title") ?? ""),
          purpose: String(form.get("purpose") ?? ""),
          scope: String(form.get("scope") ?? ""),
          tags: parseTags(String(form.get("tags") ?? "")),
        },
      });
      router.push(`/cases/${created.id}`);
    } catch (error) {
      setFormError(describeError(error));
      setCreating(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Cases</h1>
        <p className="mt-1 text-sm text-muted">
          Investigations you own. Every record inside a case is access-controlled on the server.
        </p>
      </div>

      <Section title="New case" description="Record why the case exists and what is in scope before collecting anything.">
        <form onSubmit={create} className="grid gap-3 md:grid-cols-2">
          {formError ? (
            <div className="md:col-span-2" role="alert">
              <p className="rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-sm text-bad">{formError}</p>
            </div>
          ) : null}
          <Field label="Title" htmlFor="case-title">
            <TextInput id="case-title" name="title" required maxLength={200} />
          </Field>
          <Field label="Tags" htmlFor="case-tags" hint="Comma-separated, for example: infrastructure, phishing">
            <TextInput id="case-tags" name="tags" maxLength={1000} />
          </Field>
          <Field label="Purpose" htmlFor="case-purpose">
            <TextArea id="case-purpose" name="purpose" rows={2} maxLength={10000} />
          </Field>
          <Field label="Scope" htmlFor="case-scope" hint="Targets and sources you are authorized to examine.">
            <TextArea id="case-scope" name="scope" rows={2} maxLength={10000} />
          </Field>
          <div className="md:col-span-2">
            <Button type="submit" variant="primary" disabled={creating} aria-busy={creating}>
              {creating ? "Creating…" : "Create case"}
            </Button>
          </div>
        </form>
      </Section>

      <Section
        title="Your cases"
        actions={
          <form
            role="search"
            onSubmit={(event) => {
              event.preventDefault();
              setOffset(0);
              setAppliedQuery(query.trim());
            }}
            className="flex gap-2"
          >
            <label htmlFor="case-search" className="sr-only">
              Search cases
            </label>
            <input
              id="case-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search title or purpose"
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
            <Button type="submit">Search</Button>
          </form>
        }
      >
        <div role="group" aria-label="Filter by status" className="mb-3 flex flex-wrap gap-2">
          {STATUS_FILTERS.map((filter) => (
            <Button
              key={filter.label}
              aria-pressed={status === filter.value}
              variant={status === filter.value ? "primary" : "secondary"}
              onClick={() => {
                setStatus(filter.value);
                setOffset(0);
              }}
            >
              {filter.label}
            </Button>
          ))}
        </div>
        {cases.state === "error" ? <ErrorNotice error={cases.error} onRetry={() => void cases.reload()} /> : null}
        {cases.state === "loading" && !cases.data ? <LoadingState label="Loading cases…" /> : null}
        {cases.data && cases.data.items.length === 0 ? (
          <EmptyState>{appliedQuery ? "No cases match this search." : "No cases in this view yet. Create one above."}</EmptyState>
        ) : null}
        {cases.data && cases.data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Cases</caption>
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="py-2 pr-4 font-medium">Title</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Status</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Tags</th>
                  <th scope="col" className="py-2 font-medium">Updated</th>
                </tr>
              </thead>
              <tbody>
                {cases.data.items.map((item) => (
                  <tr key={item.id} className="border-t border-line align-top">
                    <td className="py-2 pr-4">
                      {item.status === "deleting" || item.status === "deletion_failed" ? (
                        <span className="font-medium">{item.title}</span>
                      ) : (
                        <Link href={`/cases/${item.id}`} className="font-medium text-accent hover:underline">
                          {item.title}
                        </Link>
                      )}
                      {item.purpose ? <p className="line-clamp-2 text-xs text-muted">{item.purpose}</p> : null}
                    </td>
                    <td className="py-2 pr-4">{caseStatusBadge(item.status)}</td>
                    <td className="py-2 pr-4 text-xs">{item.tags.join(", ") || "—"}</td>
                    <td className="py-2 font-mono text-xs">{formatUtc(item.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={cases.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
          </div>
        ) : null}
      </Section>

      <DeletionJobs deletions={deletions} />
    </div>
  );
}

function DeletionJobs({ deletions }: { deletions: ReturnType<typeof useResource<Page<CaseDeletion>>> }) {
  const { mutate } = useSession();
  const [error, setError] = useState<string | null>(null);
  if (deletions.state === "loading" && !deletions.data) return null;
  if (deletions.data && deletions.data.items.length === 0) return null;
  const active = deletions.data?.items.some((job) => job.status === "queued" || job.status === "running");

  async function retry(id: string) {
    setError(null);
    try {
      await mutate(`/api/v1/case-deletions/${id}/retry`);
      await deletions.reload();
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  return (
    <Section
      title="Deletion jobs"
      description="Deleted cases are removed together with their evidence files. Only the job record below is kept; it contains no case content."
      actions={<Button onClick={() => void deletions.reload()}>{active ? "Refresh progress" : "Refresh"}</Button>}
    >
      {deletions.state === "error" ? <ErrorNotice error={deletions.error} /> : null}
      {error ? <p role="alert" className="mb-2 text-sm text-bad">{error}</p> : null}
      <ul className="divide-y divide-line text-sm">
        {deletions.data?.items.map((job) => (
          <li key={job.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span>
              <span className="font-mono text-xs">case {job.case_id.slice(0, 8)}</span>{" "}
              <span className="text-muted">requested {formatUtc(job.requested_at)}</span>
              {job.progress_note ? <span className="block text-xs text-muted">{job.progress_note}</span> : null}
              {job.error_code ? <span className="block text-xs text-bad">Error: {job.error_code}</span> : null}
              {job.status === "completed" ? (
                <span className="block text-xs text-muted">
                  Removed {job.removed_counts.evidence_objects ?? 0} evidence record(s),{" "}
                  {job.removed_counts.evidence_files ?? 0} file(s), {job.removed_counts.entities ?? 0} entit(ies)
                </span>
              ) : null}
            </span>
            <span className="flex items-center gap-2">
              <StatusBadge
                tone={job.status === "completed" ? "ok" : job.status === "failed" ? "bad" : "neutral"}
                label={job.status === "completed" ? "Completed" : job.status === "failed" ? "Failed" : "In progress"}
              />
              {job.status === "failed" ? <Button onClick={() => void retry(job.id)}>Retry</Button> : null}
            </span>
          </li>
        ))}
      </ul>
    </Section>
  );
}
