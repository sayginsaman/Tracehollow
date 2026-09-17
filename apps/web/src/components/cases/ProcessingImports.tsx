"use client";

import { Ban, ChevronDown, Clock, FileText, LoaderCircle, MessageSquareText, RotateCw } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { ImportAccepted, Page, ProcessingJob, ProcessingJobDetail } from "@/lib/workspace-types";

import {
  ActionError,
  Button,
  ChoiceField,
  ErrorNotice,
  Field,
  FieldGroup,
  FormError,
  LoadingState,
  Notice,
  Select,
  StatusBadge,
  TextInput,
  Timestamp,
  cn,
  humanize,
  plural,
  type Tone,
} from "../ui";

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

const STATUS: Record<string, { tone: Tone; label: string; icon?: typeof Clock }> = {
  queued: { tone: "neutral", label: "Queued", icon: Clock },
  running: { tone: "neutral", label: "Processing", icon: LoaderCircle },
  needs_input: { tone: "warn", label: "Needs your input", icon: MessageSquareText },
  completed: { tone: "ok", label: "Completed" },
  partial: { tone: "warn", label: "Completed with gaps" },
  failed: { tone: "bad", label: "Failed" },
  canceled: { tone: "warn", label: "Canceled", icon: Ban },
};

export function ProcessingStatusBadge({ status }: { status: string }) {
  const meta = STATUS[status] ?? { tone: "neutral" as Tone, label: humanize(status) };
  return <StatusBadge tone={meta.tone} label={meta.label} icon={meta.icon} />;
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

/** Plain-language facts about how a job interpreted its input, for the details view. */
export function jobFacts(job: ProcessingJob): string[] {
  const result = job.result ?? {};
  const facts: string[] = [];
  if (job.job_type === "whatsapp_export") {
    if (result.timezone === "unknown" || (job.options.timezone === "unknown" && "messages" in result)) {
      facts.push(
        "Timezone unknown: message times are kept as local wall-clock times and appear under Local time only on the timeline, never on the UTC timeline.",
      );
    } else if (typeof result.timezone === "string") {
      facts.push(`Times converted to UTC from the exporting phone's timezone, ${result.timezone}.`);
    }
    const order = result.date_order as { order?: string; basis?: string; explanation?: string } | undefined;
    if (order?.order) {
      facts.push(`Date order: ${DATE_ORDER_LABELS[order.order] ?? humanize(order.order)}${order.basis ? ` (${humanize(order.basis).toLowerCase()})` : ""}. ${order.explanation ?? ""}`.trim());
    }
    const attachments = result.attachments as Record<string, number> | undefined;
    if (attachments) {
      facts.push(
        `Attachments: ${attachments.present ?? 0} included, ${attachments.missing ?? 0} referenced but missing from the import, ${attachments.omitted_by_export ?? 0} omitted by the export.`,
      );
    }
  } else {
    const ocr = result.ocr as { status?: string; mode?: string; engine?: string; engine_version?: string; pages_processed?: number } | undefined;
    if (ocr?.status === "unavailable") facts.push("OCR is not available in this worker, so pages without a text layer produced no text.");
    else if (ocr?.status === "completed") facts.push(`OCR ran with ${ocr.engine ?? "the OCR engine"} ${ocr.engine_version ?? ""} on ${plural(ocr.pages_processed ?? 0, "page")}; recognised text may contain errors.`.replace("  ", " "));
    else if (ocr?.status === "not_needed") facts.push("Every page had a text layer, so OCR was not needed.");
    else if (ocr?.status === "off") facts.push("OCR was turned off for this document.");
  }
  return facts;
}

function ImportForm({
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
  children: ReactNode;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  saving: boolean;
  error: string | null;
  accepted: ImportAccepted | null;
  base: string;
  submitLabel: string;
}) {
  return (
    <form onSubmit={onSubmit} className="space-y-5" aria-label={title}>
      <FormError message={error} />
      {accepted ? (
        <Notice tone="ok" live>
          Stored{" "}
          <Link href={`${base}/evidence/${accepted.evidence.id}`} className="font-medium underline">
            {accepted.evidence.title}
          </Link>{" "}
          (SHA-256 {accepted.evidence.sha256.slice(0, 12)}…) and queued processing. Progress appears under Processing jobs.
          {accepted.filename_sanitized ? " The filename was reduced to a safe display name." : ""}
        </Notice>
      ) : null}
      {children}
      <Button type="submit" variant="primary" disabled={saving} busy={saving}>
        {saving ? "Uploading…" : submitLabel}
      </Button>
    </form>
  );
}

/** Where the material came from: required origin, optional title and reference. */
export function ProvenanceFields({ prefix, referenceHint = "Recorded only; never fetched." }: { prefix: string; referenceHint?: string }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Field
        label="Import origin (required)"
        htmlFor={`${prefix}-origin`}
        hint="Who provided it, how it was obtained and your authorization."
        className="md:col-span-2"
      >
        <TextInput id={`${prefix}-origin`} name="import_origin" required minLength={3} maxLength={2000} />
      </Field>
      <Field label="Title" htmlFor={`${prefix}-title`} hint="Defaults to the file name.">
        <TextInput id={`${prefix}-title`} name="title" maxLength={300} />
      </Field>
      <Field label="Source reference" htmlFor={`${prefix}-reference`} hint={referenceHint}>
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
    <ImportForm
      title="Import a WhatsApp chat export"
      onSubmit={(event) => void submit(event)}
      saving={saving}
      error={error}
      accepted={accepted}
      base={base}
      submitLabel="Import chat export"
    >
      <Field label="Export file" htmlFor="whatsapp-file" hint="The chat .txt file, or the .zip exported with media.">
        <TextInput id="whatsapp-file" type="file" accept=".txt,.zip,text/plain,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      </Field>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <Field
            label="Timezone of the exporting phone"
            htmlFor="whatsapp-timezone"
            hint="Unknown keeps times as local wall-clock times, off the UTC timeline."
          >
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
          {timezone === "other" ? (
            <Field label="IANA timezone" htmlFor="whatsapp-timezone-custom" hint="For example Asia/Baku.">
              <TextInput id="whatsapp-timezone-custom" value={customTimezone} onChange={(event) => setCustomTimezone(event.target.value)} maxLength={64} />
            </Field>
          ) : null}
        </div>
        <Field label="Date order" htmlFor="whatsapp-date-order" hint="Exports do not say whether 03/04 is 3 April or 4 March. If the dates never settle it, processing stops and asks you.">
          <Select id="whatsapp-date-order" value={dateOrder} onChange={(event) => setDateOrder(event.target.value)}>
            {Object.entries(DATE_ORDER_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <ProvenanceFields prefix="whatsapp" />
    </ImportForm>
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
    <ImportForm
      title="Import a PDF document"
      onSubmit={(event) => void submit(event)}
      saving={saving}
      error={error}
      accepted={accepted}
      base={base}
      submitLabel="Import document"
    >
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="PDF file" htmlFor="document-file" hint="A PDF you are authorized to use.">
          <TextInput id="document-file" type="file" accept=".pdf,application/pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        </Field>
        <Field label="OCR" htmlFor="document-ocr" hint="OCR needs an OCR-enabled worker; otherwise the job says so and keeps the embedded text.">
          <Select id="document-ocr" value={ocr} onChange={(event) => setOcr(event.target.value)}>
            <option value="if_needed">Only for pages without a text layer</option>
            <option value="always">Every page</option>
            <option value="off">Off</option>
          </Select>
        </Field>
      </div>
      <ProvenanceFields prefix="document" />
    </ImportForm>
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
    <form onSubmit={(event) => void submit(event)} className="mt-3 space-y-3 rounded-md border border-warn-line bg-warn-soft p-3 text-sm">
      <div>
        <p className="font-medium text-ink">{request.question}</p>
        <p className="mt-0.5 text-muted">{request.explanation}</p>
      </div>
      {request.samples.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-muted">Dates as written in the export:</span>
          {request.samples.map((sample) => (
            <code key={sample} className="rounded border border-line bg-surface px-1.5 font-mono text-code text-ink">
              {sample}
            </code>
          ))}
        </div>
      ) : null}
      <FieldGroup legend="Date order" legendClassName="sr-only">
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {request.choices.map((value) => (
            <ChoiceField
              key={value}
              type="radio"
              name={`date-order-${job.id}`}
              value={value}
              checked={choice === value}
              onChange={() => setChoice(value)}
              label={DATE_ORDER_LABELS[value] ?? humanize(value)}
            />
          ))}
        </div>
      </FieldGroup>
      {error ? (
        <p role="alert" className="text-bad">
          {error}
        </p>
      ) : null}
      <Button type="submit" variant="primary" disabled={saving} busy={saving}>
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
  const facts = jobFacts(job);
  return (
    <div className="mt-3 space-y-3 rounded-md border border-line bg-sunken/60 p-3 text-sm">
      {facts.length > 0 ? (
        <ul className="space-y-1 text-ink">
          {facts.map((fact) => (
            <li key={fact} className="max-w-[72ch]">
              {fact}
            </li>
          ))}
        </ul>
      ) : null}
      {gaps.length > 0 ? (
        <div>
          <p className="font-medium text-warn">Gaps in the result</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {limitations.length > 0 ? (
        <div>
          <p className="font-medium text-ink">Limitations</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted">
            {limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {detail.state === "error" ? <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} /> : null}
      {!detail.data && detail.state === "loading" ? <LoadingState label="Loading derived records…" rows={2} /> : null}
      {detail.data ? (
        <div>
          <p className="text-muted">
            {plural(detail.data.observation_count, "observation")} · {plural(detail.data.derived_evidence_total, "derived record")}
          </p>
          {detail.data.derived_evidence.length > 0 ? (
            <ul className="mt-2 space-y-1">
              {detail.data.derived_evidence.slice(0, 20).map((item) => (
                <li key={item.id} className="flex flex-wrap items-center gap-2">
                  <Link href={`${base}/evidence/${item.id}`} className="break-words text-accent hover:underline">
                    {item.title}
                  </Link>
                  <span className="text-xs text-muted">{item.kind}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export const JOB_POLL_INTERVAL_MS = 3000;

export function ProcessingJobsPanel({
  apiBase,
  base,
  evidenceId,
  writable,
  onSettled,
}: {
  apiBase: string;
  base: string;
  evidenceId?: string;
  writable: boolean;
  /** Called when running jobs finish, so views can show the records they derived. */
  onSettled?: () => void;
}) {
  const { mutate } = useSession();
  const params = new URLSearchParams({ limit: "25" });
  if (evidenceId) params.set("evidence_id", evidenceId);
  const jobs = useResource<Page<ProcessingJob>>(`${apiBase}/processing-jobs?${params.toString()}`);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const running = Boolean(jobs.data?.items.some((job) => job.status === "queued" || job.status === "running"));
  const { reload } = jobs;
  const wasRunning = useRef(false);

  // Poll while the worker has queued or running jobs; jobs waiting for input wait for the analyst.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void reload(), JOB_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [running, reload]);

  useEffect(() => {
    if (wasRunning.current && !running) onSettled?.();
    wasRunning.current = running;
  }, [running, onSettled]);

  async function act(job: ProcessingJob, action: "cancel" | "reprocess") {
    setActionError(null);
    setBusy(job.id);
    try {
      if (action === "cancel") await mutate(`${apiBase}/processing-jobs/${job.id}/cancel`, {});
      else await mutate(`${apiBase}/evidence/${job.evidence_id}/processing`, { body: { job_type: job.job_type, ...job.options } });
      await jobs.reload();
    } catch (caught) {
      setActionError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">
          {jobs.data
            ? `${plural(jobs.data.total, "job")}.${running ? " Updating while the worker processes them." : ""}`
            : "Chat exports and PDFs are processed in the background worker."}
        </p>
        <Button size="sm" icon={RotateCw} onClick={() => void jobs.reload()} busy={jobs.state === "loading" && Boolean(jobs.data)}>
          Refresh
        </Button>
      </div>
      <ActionError message={actionError} />
      {jobs.state === "error" ? <ErrorNotice error={jobs.error} onRetry={() => void jobs.reload()} /> : null}
      {jobs.state === "loading" && !jobs.data ? <LoadingState label="Loading processing jobs…" /> : null}
      {jobs.data && jobs.data.items.length === 0 ? (
        <p className="text-sm text-muted">No processing jobs. WhatsApp exports and PDFs you import are processed here.</p>
      ) : null}
      {jobs.data && jobs.data.items.length > 0 ? (
        <ul className="divide-y divide-line rounded-md border border-line">
          {jobs.data.items.map((job) => {
            const active = ["queued", "running", "needs_input"].includes(job.status);
            const open = expanded === job.id;
            return (
              <li key={job.id} id={`job-${job.id}`} className="px-3 py-3">
                <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
                  <div className="flex min-w-0 flex-1 basis-64 items-start gap-2.5">
                    <FileText aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-ink">{job.job_type === "whatsapp_export" ? "WhatsApp export" : "PDF document"}</span>
                        <ProcessingStatusBadge status={job.status} />
                      </div>
                      {jobSummary(job) ? <p className="text-sm text-muted">{jobSummary(job)}</p> : null}
                    </div>
                  </div>
                  <span className="text-xs text-muted">
                    <Timestamp value={job.finished_at ?? job.created_at} />
                  </span>
                </div>
                {job.error_detail ? (
                  <p className={cn("mt-2 max-w-[72ch] text-sm", job.status === "failed" ? "text-bad" : "text-warn")}>{job.error_detail}</p>
                ) : null}
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {!evidenceId ? (
                    <Link href={`${base}/evidence/${job.evidence_id}`} className="rounded px-1 text-sm text-accent hover:underline">
                      Open original
                    </Link>
                  ) : null}
                  <Button size="sm" variant="ghost" onClick={() => setExpanded(open ? null : job.id)} aria-expanded={open}>
                    <ChevronDown aria-hidden="true" className={cn("size-4 transition-transform", open && "rotate-180")} />
                    {open ? "Hide details" : "Details"}
                  </Button>
                  {active && writable ? (
                    <Button size="sm" variant="ghost" icon={Ban} onClick={() => void act(job, "cancel")} disabled={busy === job.id}>
                      Cancel
                    </Button>
                  ) : null}
                  {writable && !active ? (
                    <Button size="sm" variant="ghost" icon={RotateCw} onClick={() => void act(job, "reprocess")} disabled={busy === job.id}>
                      Process again
                    </Button>
                  ) : null}
                </div>
                {writable && job.status === "needs_input" ? <NeedsInputForm job={job} apiBase={apiBase} onDone={() => void jobs.reload()} /> : null}
                {open ? <JobDetails job={job} apiBase={apiBase} base={base} /> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

