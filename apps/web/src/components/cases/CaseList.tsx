"use client";

import { FolderOpen, Plus, RotateCw, Search, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { describeError, formatUtc, formatUtcShort } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { CaseDeletion, CaseDetail, CaseSummary, Page } from "@/lib/workspace-types";

import {
  ActionError,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  FormError,
  LoadingState,
  Notice,
  PageHeader,
  Pagination,
  Panel,
  SegmentedFilter,
  Select,
  StatusBadge,
  TextArea,
  TextInput,
  Toolbar,
} from "../ui";
import { CaseStatusBadge, CaseTags, SYNTHETIC_DEMO_TAG, parseTags } from "./case-status";

export { parseTags } from "./case-status";

const PAGE_SIZE = 20;
export const DELETION_POLL_INTERVAL_MS = 2000;

const STATUS_FILTERS = [
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
  { value: "deletion_failed", label: "Deletion failed" },
  { value: "", label: "All" },
];

const SORTS = [
  { value: "updated_desc", label: "Recently updated" },
  { value: "updated_asc", label: "Least recently updated" },
  { value: "created_desc", label: "Newest" },
  { value: "created_asc", label: "Oldest" },
  { value: "title_asc", label: "Title, A to Z" },
  { value: "title_desc", label: "Title, Z to A" },
];

export function caseListPath(options: { status: string; query: string; tag: string; sort: string; offset: number }): string {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(options.offset) });
  if (options.status) params.set("status", options.status);
  if (options.query) params.set("q", options.query);
  if (options.tag) params.set("tag", options.tag);
  if (options.sort !== "updated_desc") params.set("sort", options.sort);
  return `/api/v1/cases?${params.toString()}`;
}

function NewCaseForm({ onCancel }: { onCancel: () => void }) {
  const router = useRouter();
  const { mutate } = useSession();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    titleInput.current?.focus();
  }, []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setCreating(true);
    setError(null);
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
    } catch (caught) {
      setError(describeError(caught));
      setCreating(false);
    }
  }

  return (
    <Panel
      id="new-case"
      title="New case"
      description="Record why the case exists and what is in scope before collecting anything. Both stay visible on the case overview and in reports."
    >
      <form onSubmit={create} className="space-y-4">
        <FormError message={error} />
        <Field label="Title" htmlFor="case-title">
          <TextInput ref={titleInput} id="case-title" name="title" required maxLength={200} />
        </Field>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Purpose" htmlFor="case-purpose" hint="The question this case should answer.">
            <TextArea id="case-purpose" name="purpose" rows={3} maxLength={10000} />
          </Field>
          <Field label="Scope" htmlFor="case-scope" hint="Targets and sources you are authorized to examine.">
            <TextArea id="case-scope" name="scope" rows={3} maxLength={10000} />
          </Field>
        </div>
        <Field label="Tags" htmlFor="case-tags" hint="Comma-separated, for example: infrastructure, phishing">
          <TextInput id="case-tags" name="tags" maxLength={1000} />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={creating} busy={creating}>
            {creating ? "Creating…" : "Create case"}
          </Button>
          <Button onClick={onCancel} disabled={creating}>
            Cancel
          </Button>
        </div>
      </form>
    </Panel>
  );
}

export function CaseList({ startCreating = false, deletionRequested = false }: { startCreating?: boolean; deletionRequested?: boolean }) {
  const [creating, setCreating] = useState(startCreating);
  const [status, setStatus] = useState("active");
  const [sort, setSort] = useState("updated_desc");
  const [tag, setTag] = useState("");
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const newCaseButton = useRef<HTMLButtonElement>(null);

  const cases = useResource<Page<CaseSummary>>(caseListPath({ status, query: appliedQuery, tag, sort, offset }));
  const deletions = useResource<Page<CaseDeletion>>("/api/v1/case-deletions?limit=5");
  const filtered = Boolean(appliedQuery || tag);

  function resetPage<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value);
      setOffset(0);
    };
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Cases"
        description="Investigations you are a member of. Every record inside a case is access-controlled on the server."
        actions={
          <Button
            ref={newCaseButton}
            variant={creating ? "secondary" : "primary"}
            icon={Plus}
            aria-expanded={creating}
            aria-controls="new-case-region"
            onClick={() => setCreating(!creating)}
          >
            New case
          </Button>
        }
      />

      {deletionRequested ? (
        <Notice tone="ok" live>
          Deletion requested. Progress is listed under Deletion jobs below.
        </Notice>
      ) : null}

      <div id="new-case-region">
        {creating ? (
          <NewCaseForm
            onCancel={() => {
              setCreating(false);
              newCaseButton.current?.focus();
            }}
          />
        ) : null}
      </div>

      <Panel title="Your cases" flush>
        <div className="space-y-3 border-b border-line px-4 py-3">
          <Toolbar>
            <form
              role="search"
              onSubmit={(event) => {
                event.preventDefault();
                setOffset(0);
                setAppliedQuery(query.trim());
              }}
              className="flex min-w-0 flex-1 basis-64 gap-2"
            >
              <label htmlFor="case-search" className="sr-only">
                Search cases
              </label>
              <div className="relative min-w-0 flex-1">
                <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
                <TextInput
                  id="case-search"
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search title or purpose"
                  className="pl-8"
                />
              </div>
              <Button type="submit">Search</Button>
            </form>
            <div className="flex items-center gap-2">
              <label htmlFor="case-sort" className="text-sm whitespace-nowrap text-muted">
                Sort by
              </label>
              <Select id="case-sort" value={sort} onChange={(event) => resetPage(setSort)(event.target.value)} className="w-48">
                {SORTS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </div>
          </Toolbar>
          <div className="flex flex-wrap items-center gap-2">
            <SegmentedFilter label="Filter by status" options={STATUS_FILTERS} value={status} onChange={resetPage(setStatus)} />
            {appliedQuery ? (
              <Button
                size="sm"
                variant="ghost"
                icon={X}
                onClick={() => {
                  setQuery("");
                  setAppliedQuery("");
                  setOffset(0);
                }}
              >
                Clear search “{appliedQuery}”
              </Button>
            ) : null}
            {tag ? (
              <Button size="sm" variant="ghost" icon={X} onClick={() => resetPage(setTag)("")}>
                Clear tag “{tag}”
              </Button>
            ) : null}
          </div>
        </div>

        {cases.state === "error" ? (
          <div className="p-4">
            <ErrorNotice error={cases.error} onRetry={() => void cases.reload()} />
          </div>
        ) : null}
        {cases.state === "loading" && !cases.data ? <LoadingState label="Loading cases…" className="p-4" /> : null}
        {cases.data && cases.data.items.length === 0 ? (
          <div className="p-4">
            {filtered ? (
              <EmptyState compact>No cases in this view match the search or tag.</EmptyState>
            ) : (
              <EmptyState
                icon={FolderOpen}
                title={status === "active" ? "No active cases" : "No cases in this view"}
                action={
                  status === "active" && !creating ? (
                    <Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>
                      Create a case
                    </Button>
                  ) : undefined
                }
              >
                {status === "active"
                  ? "A case holds the purpose, scope, collection runs, imports, evidence and reports of one investigation."
                  : "Choose another status filter to see other cases."}
              </EmptyState>
            )}
          </div>
        ) : null}
        {cases.data && cases.data.items.length > 0 ? (
          <>
            <ul className="divide-y divide-line">
              {cases.data.items.map((item) => {
                const unavailable = item.status === "deleting" || item.status === "deletion_failed";
                return (
                  <li key={item.id} className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2 px-4 py-3.5">
                    <div className="min-w-0 flex-1 basis-80">
                      {unavailable ? (
                        <span className="font-medium break-words text-ink">{item.title}</span>
                      ) : (
                        <Link href={`/cases/${item.id}`} className="font-medium break-words text-accent hover:underline">
                          {item.title}
                        </Link>
                      )}
                      {item.purpose ? <p className="mt-0.5 line-clamp-2 max-w-[72ch] text-sm text-muted">{item.purpose}</p> : null}
                      {item.tags.length > 0 ? (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {item.tags.map((value) => (
                            <button
                              key={value}
                              type="button"
                              onClick={() => resetPage(setTag)(value)}
                              aria-label={`Show cases tagged ${value}`}
                              className="rounded"
                            >
                              <CaseTags tags={[value]} />
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 flex-col items-start gap-1 sm:items-end">
                      <CaseStatusBadge status={item.status} />
                      <span className="text-xs text-muted tabular-nums" title={formatUtc(item.updated_at)}>
                        Updated {formatUtcShort(item.updated_at)}
                      </span>
                      {item.tags.includes(SYNTHETIC_DEMO_TAG) ? <span className="sr-only">Synthetic demonstration case</span> : null}
                    </div>
                  </li>
                );
              })}
            </ul>
            <Pagination total={cases.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} className="border-t border-line" />
          </>
        ) : null}
      </Panel>

      <DeletionJobs deletions={deletions} onSettled={cases.reload} />
    </div>
  );
}

function DeletionJobs({
  deletions,
  onSettled,
}: {
  deletions: ReturnType<typeof useResource<Page<CaseDeletion>>>;
  onSettled: () => void;
}) {
  const { mutate } = useSession();
  const [error, setError] = useState<string | null>(null);
  const active = Boolean(deletions.data?.items.some((job) => job.status === "queued" || job.status === "running"));
  const { reload } = deletions;
  const wasActive = useRef(false);

  // Poll while a job is queued or running, then refresh the case list once it settles.
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void reload(), DELETION_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [active, reload]);

  useEffect(() => {
    if (wasActive.current && !active) onSettled();
    wasActive.current = active;
  }, [active, onSettled]);

  if (deletions.state === "loading" && !deletions.data) return null;
  if (deletions.data && deletions.data.items.length === 0) return null;

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
    <Panel
      title="Deletion jobs"
      description="Deleted cases are removed together with their evidence files. Only the job record below is kept; it contains no case content."
      actions={
        <Button size="sm" icon={RotateCw} onClick={() => void deletions.reload()}>
          {active ? "Refresh progress" : "Refresh"}
        </Button>
      }
      flush
    >
      {deletions.state === "error" ? (
        <div className="p-4">
          <ErrorNotice error={deletions.error} />
        </div>
      ) : null}
      <ActionError message={error} className="m-4" />
      <ul className="divide-y divide-line text-sm">
        {deletions.data?.items.map((job) => (
          <li key={job.id} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-4 py-3">
            <span className="min-w-0">
              <span className="font-mono text-code">case {job.case_id.slice(0, 8)}</span>{" "}
              <span className="text-muted">requested {formatUtcShort(job.requested_at)}</span>
              {job.progress_note ? <span className="block text-xs text-muted">{job.progress_note}</span> : null}
              {job.error_code ? <span className="block text-xs text-bad">Error: {job.error_code}</span> : null}
              {job.status === "completed" ? (
                <span className="block text-xs text-muted">
                  Removed {job.removed_counts.evidence_objects ?? 0} evidence record(s), {job.removed_counts.evidence_files ?? 0} file(s),{" "}
                  {job.removed_counts.entities ?? 0} entit(ies)
                </span>
              ) : null}
            </span>
            <span className="flex items-center gap-2">
              <StatusBadge
                tone={job.status === "completed" ? "ok" : job.status === "failed" ? "bad" : "neutral"}
                label={job.status === "completed" ? "Completed" : job.status === "failed" ? "Failed" : "In progress"}
              />
              {job.status === "failed" ? (
                <Button size="sm" onClick={() => void retry(job.id)}>
                  Retry
                </Button>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
