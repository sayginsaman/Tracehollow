"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { ImportAccepted, Page, ProcessingJob, ProcessingJobDetail } from "@/lib/workspace-types";

import { StatusBadge, type Tone } from "../StatusBadge";
import { Button, EmptyState, ErrorNotice, Field, LoadingState, Notice, Select, TextInput, humanize } from "../ui";

export const MAX_PROCESSING_UPLOAD_BYTES = 128 * 1024 * 1024;

export const COMMON_TIMEZONES = [
  "Europe/Istanbul",
  "UTC",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Los_Angeles",
  "Asia/Dubai",
];

const STATUS_TONES: Record<string, Tone> = {
  queued: "neutral",
  running: "neutral",
  needs_input: "warn",
  completed: "ok",
  partial: "warn",
  failed: "bad",
  canceled: "warn",
};

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Processing",
  needs_input: "Needs your input",
  completed: "Completed",
  partial: "Completed with gaps",
  failed: "Failed",
  canceled: "Canceled",
};

export function ProcessingStatusBadge({ status }: { status: string }) {
  return <StatusBadge tone={STATUS_TONES[status] ?? "neutral"} label={STATUS_LABELS[status] ?? humanize(status)} />;
}

const DATE_ORDER_LABELS: Record<string, string> = {
  auto: "Detect from the export (ask me if unclear)",
  day_first: "Day first (31/12/2024)",
  month_first: "Month first (12/31/2024)",
  year_first: "Year first (2024-12-31)",
};

/** Validates the shared fields of a processing upload and builds its multipart body. */
export function buildProcessingUpload(input: {
  file: File | null;
  importOrigin: string;
  title: string;
  sourceReference: string;
  extra: Record<string, string>;
}): { form: FormData | null; problem: string | null } {
  if (!input.file) return { form: null, problem: "Choose a file to import." };
  if (input.file.size === 0) return { form: null, problem: "The chosen file is empty." };
  if (input.file.size > MAX_PROCESSING_UPLOAD_BYTES) {
    return { form: null, problem: "The file is larger than the 128 MiB import limit." };
  }
  if (input.importOrigin.trim().length < 3) {
    return { form: null, problem: "Describe where this material came from and your authorization (import origin)." };
  }
  const form = new FormData();
  form.set("file", input.file, input.file.name);
  form.set("import_origin", input.importOrigin.trim());
  if (input.title.trim()) form.set("title", input.title.trim());
  if (input.sourceReference.trim()) form.set("source_reference", input.sourceReference.trim());
  for (const [key, value] of Object.entries(input.extra)) form.set(key, value);
  return { form, problem: null };
}

/** Short, factual summary of a finished job for lists. */
export function jobSummary(job: ProcessingJob): string {
  const result = job.result ?? {};
  if (job.job_type === "whatsapp_export") {
    if (job.status === "needs_input") return "Waiting for the date order.";
    const messages = Number(result.messages ?? 0);
    const events = Number(result.system_events ?? 0);
    if (!("messages" in result)) return "";
    return `${messages} message${messages === 1 ? "" : "s"}, ${events} system event${events === 1 ? "" : "s"}`;
  }
  const state = typeof result.document_state === "string" ? humanize(result.document_state) : "";
  const pages = result.pages_total !== undefined ? `${String(result.pages_processed ?? 0)} of ${String(result.pages_total)} pages` : "";
  return [state, pages].filter(Boolean).join(" · ");
}

function ImportShell({
  title,
  children,
  onSubmit,
  saving,
  error,
  accepted,
  base,
  submitLabel,
}: {
  title: string;
  children: React.ReactNode;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  saving: boolean;
  error: string | null;
  accepted: ImportAccepted | null;
  base: string;
  submitLabel: string;
}) {
  return (
    <form onSubmit={onSubmit} className="space-y-3" aria-label={title}>
      {error ? (
        <p role="alert" className="rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-sm text-bad">
          {error}
        </p>
      ) : null}
      {accepted ? (
        <Notice tone="ok">
          Stored{" "}
          <Link href={`${base}/evidence/${accepted.evidence.id}`} className="underline">
            {accepted.evidence.title}
          </Link>{" "}
          (SHA-256 {accepted.evidence.sha256.slice(0, 12)}…) and queued processing. Progress appears under Processing jobs.
          {accepted.filename_sanitized ? " The filename was reduced to a safe display name." : ""}
        </Notice>
      ) : null}
      {children}
      <Button type="submit" variant="primary" disabled={saving} aria-busy={saving}>
        {saving ? "Uploading…" : submitLabel}
      </Button>
    </form>
  );
}

function SharedFields({ prefix }: { prefix: string }) {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      <Field label="Import origin (required)" htmlFor={`${prefix}-origin`} hint="Who provided it, how it was obtained and your authorization.">
        <TextInput id={`${prefix}-origin`} name="import_origin" required minLength={3} maxLength={2000} />
      </Field>
      <Field label="Title" htmlFor={`${prefix}-title`} hint="Defaults to the file name.">
        <TextInput id={`${prefix}-title`} name="title" maxLength={300} />
      </Field>
      <Field label="Source reference" htmlFor={`${prefix}-reference`} hint="Recorded only; never fetched.">
        <TextInput id={`${prefix}-reference`} name="source_reference" maxLength={2048} />
      </Field>
    </div>
  );
}

export function WhatsAppImportForm({ apiBase, base, onImported }: { apiBase: string; base: string; onImported: () => void }) {
  const { mutate } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [timezone, setTimezone] = useState("unknown");
  const [customTimezone, setCustomTimezone] = useState("");
  const [dateOrder, setDateOrder] = useState("auto");
  const [error, setError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<ImportAccepted | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const element = event.currentTarget;
    const values = new FormData(element);
    const zone = timezone === "other" ? customTimezone.trim() : timezone;
    if (!zone) {
      setError("Enter an IANA timezone name, or choose Unknown.");
      return;
    }
    const { form, problem } = buildProcessingUpload({
      file,
      importOrigin: String(values.get("import_origin") ?? ""),
      title: String(values.get("title") ?? ""),
      sourceReference: String(values.get("source_reference") ?? ""),
      extra: { timezone: zone, date_order: dateOrder },
    });
    setAccepted(null);
    if (problem || !form) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      setAccepted(await mutate<ImportAccepted>(`${apiBase}/imports/whatsapp`, { form }));
      element.reset();
      setFile(null);
      onImported();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <ImportShell title="Import a WhatsApp chat export" onSubmit={(event) => void submit(event)} saving={saving} error={error} accepted={accepted} base={base} submitLabel="Import chat export">
      <p className="text-sm text-muted">
        Import a chat you are authorized to review, exported from WhatsApp as the chat .txt file or the .zip with media. The
        original is kept unchanged. Sender names are labels from the exporting phone, not verified identities, and no phone
        numbers or accounts are inferred from them.
      </p>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Export file" htmlFor="whatsapp-file" hint=".txt or .zip, up to 128 MiB.">
          <TextInput id="whatsapp-file" type="file" accept=".txt,.zip,text/plain,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        </Field>
        <Field label="Timezone of the exporting phone" htmlFor="whatsapp-timezone" hint="Unknown keeps times as local wall-clock times, off the UTC timeline.">
          <Select id="whatsapp-timezone" value={timezone} onChange={(event) => setTimezone(event.target.value)}>
            <option value="unknown">Unknown</option>
            {COMMON_TIMEZONES.map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
            <option value="other">Other IANA timezone…</option>
          </Select>
        </Field>
        <Field label="Date order" htmlFor="whatsapp-date-order" hint="Exports do not say whether 03/04 is 3 April or 4 March.">
          <Select id="whatsapp-date-order" value={dateOrder} onChange={(event) => setDateOrder(event.target.value)}>
            {Object.entries(DATE_ORDER_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {timezone === "other" ? (
        <Field label="IANA timezone" htmlFor="whatsapp-timezone-custom" hint="For example Asia/Baku.">
          <TextInput id="whatsapp-timezone-custom" value={customTimezone} onChange={(event) => setCustomTimezone(event.target.value)} maxLength={64} />
        </Field>
      ) : null}
      <SharedFields prefix="whatsapp" />
    </ImportShell>
  );
}

export function DocumentImportForm({ apiBase, base, onImported }: { apiBase: string; base: string; onImported: () => void }) {
  const { mutate } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [ocr, setOcr] = useState("if_needed");
  const [error, setError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<ImportAccepted | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const element = event.currentTarget;
    const values = new FormData(element);
    const { form, problem } = buildProcessingUpload({
      file,
      importOrigin: String(values.get("import_origin") ?? ""),
      title: String(values.get("title") ?? ""),
      sourceReference: String(values.get("source_reference") ?? ""),
      extra: { ocr },
    });
    setAccepted(null);
    if (problem || !form) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      setAccepted(await mutate<ImportAccepted>(`${apiBase}/imports/documents`, { form }));
      element.reset();
      setFile(null);
      onImported();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <ImportShell title="Import a PDF document" onSubmit={(event) => void submit(event)} saving={saving} error={error} accepted={accepted} base={base} submitLabel="Import document">
      <p className="text-sm text-muted">
        The PDF is stored unchanged and never rendered in the workspace. Embedded text and OCR text are extracted by the worker
        as separate records with page references; encrypted or unreadable files are reported, not guessed.
      </p>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="PDF file" htmlFor="document-file" hint="Up to 64 MiB.">
          <TextInput id="document-file" type="file" accept=".pdf,application/pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        </Field>
        <Field label="OCR" htmlFor="document-ocr" hint="OCR needs an OCR-enabled worker; otherwise the job says so.">
          <Select id="document-ocr" value={ocr} onChange={(event) => setOcr(event.target.value)}>
            <option value="if_needed">Only for pages without a text layer</option>
            <option value="always">Every page</option>
            <option value="off">Off</option>
          </Select>
        </Field>
      </div>
      <SharedFields prefix="document" />
    </ImportShell>
  );
}

function NeedsInputForm({ job, apiBase, onDone }: { job: ProcessingJob; apiBase: string; onDone: () => void }) {
  const { mutate } = useSession();
  const [choice, setChoice] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  if (!job.needs_input) return null;
  const request = job.needs_input;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!choice) {
      setError("Choose the date order.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await mutate(`${apiBase}/processing-jobs/${job.id}/input`, { body: { date_order: choice } });
      onDone();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="mt-2 space-y-2 rounded-md border border-warn/30 bg-warn-bg p-3 text-sm">
      <p className="font-medium">{request.question}</p>
      <p className="text-muted">{request.explanation}</p>
      {request.samples.length > 0 ? (
        <p>
          Dates as written in the export:{" "}
          {request.samples.map((sample) => (
            <code key={sample} className="mr-2 rounded bg-surface px-1">
              {sample}
            </code>
          ))}
        </p>
      ) : null}
      <fieldset className="flex flex-wrap gap-4">
        <legend className="sr-only">Date order</legend>
        {request.choices.map((value) => (
          <label key={value} className="inline-flex items-center gap-2">
            <input type="radio" name={`date-order-${job.id}`} value={value} checked={choice === value} onChange={() => setChoice(value)} />
            {DATE_ORDER_LABELS[value] ?? humanize(value)}
          </label>
        ))}
      </fieldset>
      {error ? (
        <p role="alert" className="text-bad">
          {error}
        </p>
      ) : null}
      <Button type="submit" variant="primary" disabled={saving} aria-busy={saving}>
        Continue processing
      </Button>
    </form>
  );
}

function JobDetails({ job, apiBase, base }: { job: ProcessingJob; apiBase: string; base: string }) {
  const detail = useResource<ProcessingJobDetail>(`${apiBase}/processing-jobs/${job.id}`);
  const result = job.result ?? {};
  const gaps = Array.isArray(result.gaps) ? (result.gaps as unknown[]).map(String) : [];
  const limitations = Array.isArray(result.limitations) ? (result.limitations as unknown[]).map(String) : [];
  return (
    <div className="mt-2 space-y-2 text-sm">
      {gaps.length > 0 ? (
        <div>
          <p className="font-medium text-warn">Gaps</p>
          <ul className="list-disc pl-5">
            {gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {limitations.length > 0 ? (
        <div>
          <p className="font-medium">Limitations</p>
          <ul className="list-disc pl-5 text-muted">
            {limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {detail.state === "error" ? <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} /> : null}
      {detail.data ? (
        <div>
          <p className="text-muted">
            {detail.data.observation_count} observation{detail.data.observation_count === 1 ? "" : "s"} ·{" "}
            {detail.data.derived_evidence_total} derived record{detail.data.derived_evidence_total === 1 ? "" : "s"}
          </p>
          <ul className="mt-1 space-y-0.5">
            {detail.data.derived_evidence.slice(0, 20).map((item) => (
              <li key={item.id}>
                <Link href={`${base}/evidence/${item.id}`} className="text-accent hover:underline">
                  {item.title}
                </Link>{" "}
                <span className="text-xs text-muted">({item.kind})</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function ProcessingJobsPanel({
  apiBase,
  base,
  evidenceId,
  writable,
}: {
  apiBase: string;
  base: string;
  evidenceId?: string;
  writable: boolean;
}) {
  const { mutate } = useSession();
  const params = new URLSearchParams({ limit: "25" });
  if (evidenceId) params.set("evidence_id", evidenceId);
  const jobs = useResource<Page<ProcessingJob>>(`${apiBase}/processing-jobs?${params.toString()}`);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function cancel(job: ProcessingJob) {
    setActionError(null);
    try {
      await mutate(`${apiBase}/processing-jobs/${job.id}/cancel`, {});
      await jobs.reload();
    } catch (caught) {
      setActionError(describeError(caught));
    }
  }

  async function reprocess(job: ProcessingJob) {
    setActionError(null);
    try {
      await mutate(`${apiBase}/evidence/${job.evidence_id}/processing`, {
        body: { job_type: job.job_type, ...job.options },
      });
      await jobs.reload();
    } catch (caught) {
      setActionError(describeError(caught));
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <Button onClick={() => void jobs.reload()}>Refresh</Button>
      </div>
      {actionError ? <p role="alert" className="text-sm text-bad">{actionError}</p> : null}
      {jobs.state === "error" ? <ErrorNotice error={jobs.error} onRetry={() => void jobs.reload()} /> : null}
      {jobs.state === "loading" && !jobs.data ? <LoadingState label="Loading processing jobs…" /> : null}
      {jobs.data && jobs.data.items.length === 0 ? <EmptyState>No processing jobs.</EmptyState> : null}
      <ul className="divide-y divide-line">
        {jobs.data?.items.map((job) => {
          const active = ["queued", "running", "needs_input"].includes(job.status);
          return (
            <li key={job.id} className="py-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <ProcessingStatusBadge status={job.status} />
                <span className="font-medium">{job.job_type === "whatsapp_export" ? "WhatsApp export" : "PDF document"}</span>
                <span className="text-muted">{jobSummary(job)}</span>
                <span className="ml-auto font-mono text-xs text-muted">{formatUtc(job.finished_at ?? job.created_at)}</span>
              </div>
              {job.error_detail ? <p className={`mt-1 text-sm ${job.status === "failed" ? "text-bad" : "text-warn"}`}>{job.error_detail}</p> : null}
              <div className="mt-1 flex flex-wrap gap-2">
                {!evidenceId ? (
                  <Link href={`${base}/evidence/${job.evidence_id}`} className="text-sm text-accent hover:underline">
                    Original
                  </Link>
                ) : null}
                <Button onClick={() => setExpanded(expanded === job.id ? null : job.id)} aria-expanded={expanded === job.id}>
                  {expanded === job.id ? "Hide details" : "Details"}
                </Button>
                {active ? <Button onClick={() => void cancel(job)}>Cancel</Button> : null}
                {writable && !active ? <Button onClick={() => void reprocess(job)}>Process again</Button> : null}
              </div>
              {writable && job.status === "needs_input" ? <NeedsInputForm job={job} apiBase={apiBase} onDone={() => void jobs.reload()} /> : null}
              {expanded === job.id ? <JobDetails job={job} apiBase={apiBase} base={base} /> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
