"use client";

import { CircleCheck, CircleX, Download, ScanText, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";

import { collectionMode, observationLabel } from "@/lib/connectors";
import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { EvidenceDetail, EvidencePreview, Observation, Page } from "@/lib/workspace-types";

import { usePageCrumb } from "../shell/ShellContext";
import {
  ActionError,
  Button,
  ButtonLink,
  Disclosure,
  ErrorNotice,
  Field,
  KeyValue,
  LoadingState,
  LongValue,
  Mono,
  PageHeader,
  Panel,
  ProvenanceBadge,
  StatusBadge,
  SyntheticBadge,
  TextInput,
  Timestamp,
  cn,
  formatBytes,
  humanize,
  plural,
} from "../ui";
import { useCase } from "./CaseContext";
import { NotesPanel } from "./NotesPanel";
import { ProcessingJobsPanel } from "./ProcessingImports";

const INTEGRITY_LABELS: Record<string, string> = {
  verified: "Hash verified",
  evidence_file_missing: "File missing",
  evidence_size_mismatch: "Size mismatch",
  evidence_hash_mismatch: "Hash mismatch",
};

/** Text shown line by line with CSS line numbers, so the element's text stays exactly the content. */
function NumberedText({ text, mono, label }: { text: string; mono: boolean; label: string }) {
  const lines = text.split("\n");
  // A final line break does not start another line; its "\n" stays on the previous line's text.
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  const lastIndex = text.endsWith("\n") ? lines.length : lines.length - 1;
  return (
    <pre
      tabIndex={0}
      aria-label={label}
      className={cn(
        "max-h-[36rem] overflow-auto rounded-md border border-line bg-sunken py-3 whitespace-pre-wrap break-words text-ink [counter-reset:line]",
        mono ? "font-mono text-code" : "font-sans text-read",
      )}
    >
      {lines.map((line, index) => (
        <span
          key={index}
          className="block pr-3 pl-14 [counter-increment:line] before:absolute before:-ml-12 before:w-9 before:text-right before:font-mono before:text-xs before:leading-[inherit] before:text-subtle before:content-[counter(line)] before:select-none"
        >
          {index < lastIndex ? `${line}\n` : line}
        </span>
      ))}
    </pre>
  );
}

export function EvidenceDetailView({ evidenceId }: { evidenceId: string }) {
  const { apiBase, base, writable } = useCase();
  const detail = useResource<EvidenceDetail>(`${apiBase}/evidence/${evidenceId}`);
  const preview = useResource<EvidencePreview>(`${apiBase}/evidence/${evidenceId}/preview`);
  const observations = useResource<Page<Observation>>(`${apiBase}/observations?evidence_id=${evidenceId}&limit=20`);
  const [pretty, setPretty] = useState(true);
  const [jobsKey, setJobsKey] = useState(0);
  usePageCrumb(detail.data?.evidence.title);

  if (detail.state === "error" && !detail.data) return <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} />;
  if (!detail.data) return <LoadingState label="Loading evidence…" rows={6} />;
  const { evidence, integrity } = detail.data;
  const previewData = preview.data;
  const showPretty = Boolean(pretty && previewData?.pretty_json);
  const shown = previewData ? (showPretty && previewData.pretty_json ? previewData.pretty_json : previewData.text) : "";
  const verified = integrity.status === "verified";
  const processable = ["pdf", "archive"].includes(evidence.kind) || Boolean(evidence.collection_metadata.import_format);

  return (
    <div className="space-y-6">
      <PageHeader
        title={evidence.title}
        meta={
          <>
            <ProvenanceBadge evidence={evidence} />
            {evidence.synthetic && evidence.acquisition_method !== "synthetic_fixture" ? <SyntheticBadge /> : null}
            <StatusBadge tone={verified ? "ok" : "bad"} icon={verified ? CircleCheck : CircleX} label={INTEGRITY_LABELS[integrity.status] ?? integrity.status} />
            <span>
              {evidence.kind.toUpperCase()} · {formatBytes(evidence.size_bytes)}
            </span>
          </>
        }
        actions={
          <ButtonLink href={`${apiBase}/evidence/${evidenceId}/content`} download external icon={Download}>
            Download original
          </ButtonLink>
        }
      />

      {!verified ? (
        <p role="alert" className="flex gap-2 rounded-md border border-bad-line bg-bad-soft px-3 py-2.5 text-sm text-bad">
          <CircleX aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          The stored bytes do not match this record. Do not rely on this evidence until the file is restored from a backup. The metadata
          below is kept for provenance.
        </p>
      ) : null}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem] xl:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="min-w-0 space-y-6">
          <Panel
            title="Content"
            description="Shown as inert plain text with line numbers. Imported HTML or scripts are never rendered or executed."
            actions={
              previewData?.pretty_json ? (
                <Button size="sm" onClick={() => setPretty(!pretty)} aria-pressed={showPretty}>
                  {showPretty ? "Show original text" : "Show formatted JSON"}
                </Button>
              ) : null
            }
          >
            {preview.state === "error" ? <ErrorNotice error={preview.error} onRetry={() => void preview.reload()} /> : null}
            {preview.state === "loading" && !previewData ? <LoadingState label="Loading preview…" rows={6} /> : null}
            {previewData ? (
              <div className="space-y-3">
                {previewData.truncated ? (
                  <p className="text-sm text-warn">
                    Showing the first {formatBytes(previewData.preview_bytes)} of {formatBytes(previewData.size_bytes)}. Download the original
                    for the complete content.
                  </p>
                ) : null}
                {previewData.previewable === false ? (
                  <p className="rounded-md bg-sunken px-3 py-2.5 text-sm text-muted">
                    {previewData.note ?? "Binary original: it is not decoded or rendered here."}
                  </p>
                ) : (
                  <NumberedText text={shown} mono={evidence.kind !== "text"} label="Evidence content preview" />
                )}
              </div>
            ) : null}
          </Panel>

          {detail.data.observation_count > 0 ? (
            <Panel
              title="Observations from this evidence"
              description={`${plural(detail.data.observation_count, "observation")}: source-specific facts read from this record.`}
              flush
            >
              {observations.state === "error" ? (
                <div className="p-4">
                  <ErrorNotice error={observations.error} />
                </div>
              ) : null}
              <ul className="divide-y divide-line">
                {observations.data?.items.map((observation) => {
                  const label = observationLabel(observation);
                  return (
                    <li key={observation.id} className="space-y-2 px-4 py-3 text-sm">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="min-w-0 font-medium break-words text-ink">{label.primary}</span>
                        <span className="text-xs text-muted">{humanize(observation.observation_type)}</span>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                        {observation.source_object_id ? (
                          <span>
                            Source object <Mono>{observation.source_object_id}</Mono>
                          </span>
                        ) : null}
                        {observation.entity_id ? (
                          <Link href={`${base}/entities/${observation.entity_id}`} className="text-accent hover:underline">
                            Open entity
                          </Link>
                        ) : null}
                      </div>
                      <Disclosure summary="Recorded payload">
                        <pre className="max-h-64 overflow-auto font-mono text-code whitespace-pre-wrap break-words text-ink">
                          {JSON.stringify(observation.payload, null, 2)}
                        </pre>
                      </Disclosure>
                    </li>
                  );
                })}
              </ul>
            </Panel>
          ) : null}

          {processable ? (
            <Panel
              title="Processing"
              description="Records derived from this original by the worker. The original itself is never changed."
              actions={
                writable && evidence.kind === "pdf" ? (
                  <StartDocumentProcessing apiBase={apiBase} evidenceId={evidenceId} onStarted={() => setJobsKey((value) => value + 1)} />
                ) : null
              }
            >
              <ProcessingJobsPanel key={jobsKey} apiBase={apiBase} base={base} evidenceId={evidenceId} writable={writable} />
            </Panel>
          ) : null}

          <NotesPanel subject={{ evidence_id: evidenceId }} title="Notes on this evidence" />
        </div>

        <div className="min-w-0 space-y-6 lg:sticky lg:top-20">
          <Panel title="Provenance">
            <KeyValue
              items={[
                [
                  "Acquisition",
                  evidence.synthetic
                    ? "Synthetic fixture (not a real source)"
                    : evidence.acquisition_method === "connector_collection"
                      ? `Collected by a connector: ${collectionMode(evidence.collection_mode ?? "").label}${evidence.access_category === "credentialed" ? " (using a stored credential)" : ""}`
                      : humanize(evidence.acquisition_method),
                ],
                ...(evidence.import_origin ? ([["Import origin", evidence.import_origin]] as [string, ReactNode][]) : []),
                [
                  "Source reference",
                  evidence.source_reference ? <LongValue key="ref" value={evidence.source_reference} copyLabel="Copy source reference" /> : "Not recorded",
                ],
                [
                  "Source published",
                  evidence.source_published_at ? (
                    <span key="published">
                      <Timestamp value={evidence.source_published_at} />
                      {evidence.source_published_at_original ? (
                        <span className="block text-xs text-muted">As supplied: {evidence.source_published_at_original}</span>
                      ) : null}
                    </span>
                  ) : (
                    "Unknown"
                  ),
                ],
                [evidence.acquisition_method === "authorized_import" ? "Imported" : "Collected", <Timestamp key="collected" value={evidence.collected_at} />],
                ...collectionRows(evidence.collection_metadata),
                ...(evidence.derived_from_evidence_id
                  ? ([
                      [
                        "Derived from",
                        <Link key="derived" href={`${base}/evidence/${evidence.derived_from_evidence_id}`} className="text-accent hover:underline">
                          Original snapshot
                        </Link>,
                      ],
                    ] as [string, ReactNode][])
                  : []),
                ...(detail.data.derived_evidence.length > 0
                  ? ([
                      [
                        "Derived records",
                        <span key="derived-list" className="flex flex-wrap gap-x-2">
                          {detail.data.derived_evidence.map((id, index) => (
                            <Link key={id} href={`${base}/evidence/${id}`} className="text-accent hover:underline">
                              Record {index + 1}
                            </Link>
                          ))}
                        </span>,
                      ],
                    ] as [string, ReactNode][])
                  : []),
                ...(evidence.connector_id
                  ? ([["Connector", <Mono key="connector">{`${evidence.connector_id} ${evidence.connector_version ?? ""}`}</Mono>]] as [string, ReactNode][])
                  : []),
                ...(evidence.query_run_id
                  ? ([
                      [
                        "Execution",
                        <Link key="run" href={`${base}/runs/${evidence.query_run_id}`} className="text-accent hover:underline">
                          View run{evidence.page_index !== null ? ` (page ${evidence.page_index + 1})` : ""}
                        </Link>,
                      ],
                    ] as [string, ReactNode][])
                  : []),
                ["Record created", <Timestamp key="processed" value={evidence.created_at} />],
              ]}
            />
          </Panel>

          <Panel title="Integrity and content">
            <KeyValue
              items={[
                ["SHA-256", <LongValue key="sha" value={evidence.sha256} copyLabel="Copy SHA-256" />],
                ["Integrity checked", <Timestamp key="checked" value={integrity.checked_at} />],
                ["Content type", <Mono key="type">{evidence.content_type}</Mono>],
                ["Size", `${formatBytes(evidence.size_bytes)} (${evidence.size_bytes} bytes)`],
                ...(evidence.original_filename
                  ? ([["Original filename", <LongValue key="filename" value={evidence.original_filename} mono={false} />]] as [string, ReactNode][])
                  : []),
                [
                  "AI index",
                  detail.data.index
                    ? `${humanize(detail.data.index.status)}${detail.data.index.status === "indexed" ? ` (${plural(detail.data.index.chunk_count, "passage")})` : ""}${detail.data.index.error_detail ? `: ${detail.data.index.error_detail}` : ""}`
                    : "Not tracked",
                ],
                ...(evidence.description ? ([["Description", evidence.description]] as [string, ReactNode][]) : []),
              ]}
            />
            <p className="mt-3 text-xs text-muted">SHA-256 detects changes to stored bytes; it does not prove authorship or authenticity.</p>
            {detail.data.duplicate_of.length > 0 ? (
              <p className="mt-2 text-xs text-muted">
                Identical bytes also exist as{" "}
                {detail.data.duplicate_of.map((id, index) => (
                  <span key={id}>
                    {index > 0 ? ", " : ""}
                    <Link href={`${base}/evidence/${id}`} className="text-accent hover:underline">
                      another record
                    </Link>
                  </span>
                ))}
                .
              </p>
            ) : null}
          </Panel>

          <Panel title="Linked records">
            <div className="space-y-4 text-sm">
              <div>
                <p className="text-xs font-medium text-muted">Entities</p>
                {detail.data.linked_entities.length === 0 ? <p className="mt-1 text-muted">Not linked to any entity.</p> : null}
                <ul className="mt-1 space-y-1">
                  {detail.data.linked_entities.map((link) => (
                    <li key={link.link_id}>
                      <Link href={`${base}/entities/${link.entity_id}`} className="break-words text-accent hover:underline">
                        {link.display_name}
                      </Link>{" "}
                      <span className="text-xs text-muted">{humanize(link.entity_type)}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-xs font-medium text-muted">Relationships citing it</p>
                {detail.data.linked_relationships.length === 0 ? <p className="mt-1 text-muted">No relationship references it.</p> : null}
                <ul className="mt-1 space-y-1">
                  {detail.data.linked_relationships.map((link) => (
                    <li key={link.reference_id}>
                      <Mono>{link.predicate}</Mono> <span className="text-xs text-muted">{link.stance}</span>
                    </li>
                  ))}
                </ul>
                {detail.data.linked_relationships.length > 0 ? (
                  <Link href={`${base}/relationships`} className="mt-1 inline-block text-xs text-accent hover:underline">
                    Open relationships
                  </Link>
                ) : null}
              </div>
            </div>
          </Panel>

          {evidence.acquisition_method === "authorized_import" && writable ? <EvidenceDeletion evidenceId={evidenceId} title={evidence.title} /> : null}
        </div>
      </div>
    </div>
  );
}

function EvidenceDeletion({ evidenceId, title }: { evidenceId: string; title: string }) {
  const { apiBase, base, refreshCase } = useCase();
  const { mutate } = useSession();
  const router = useRouter();
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function remove(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await mutate(`${apiBase}/evidence/${evidenceId}/deletion`, { body: { confirm_title: confirmation } });
      await refreshCase();
      router.push(`${base}/evidence`);
    } catch (caught) {
      setError(describeError(caught));
      setBusy(false);
    }
  }

  return (
    <Disclosure summary="Delete this evidence" className="bg-surface">
      <form onSubmit={remove} className="space-y-3 text-sm">
        <p className="text-muted">
          Removes the stored original, its index passages and vectors, entity links, relationship references and notes about it. AI answers
          that cited it will show that the source was deleted.
        </p>
        <ActionError message={error} />
        <Field label={`Type the evidence title to confirm: ${title}`} htmlFor="confirm-evidence-title">
          <TextInput id="confirm-evidence-title" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" />
        </Field>
        <Button type="submit" variant="danger" icon={Trash2} disabled={busy || confirmation !== title} busy={busy}>
          {busy ? "Deleting…" : "Delete evidence"}
        </Button>
      </form>
    </Disclosure>
  );
}

function collectionRows(metadata: Record<string, unknown>): [string, ReactNode][] {
  const rows: [string, ReactNode][] = [];
  if (typeof metadata.final_url === "string" && metadata.final_url !== metadata.requested_url && metadata.requested_url) {
    rows.push(["Requested URL", <LongValue key="requested" value={String(metadata.requested_url)} copyLabel="Copy requested URL" />]);
  }
  if (typeof metadata.final_url === "string") {
    rows.push(["Final URL", <LongValue key="final" value={metadata.final_url} copyLabel="Copy final URL" />]);
  }
  if (typeof metadata.http_status === "number") rows.push(["HTTP status", String(metadata.http_status)]);
  if (Array.isArray(metadata.redirects) && metadata.redirects.length > 0) rows.push(["Redirects", String(metadata.redirects.length)]);
  if (metadata.truncated === true) rows.push(["Truncated", "Yes: the response exceeded the size limit"]);
  if (typeof metadata.decoded_with === "string") rows.push(["Character set", metadata.decoded_with]);
  if (typeof metadata.provider === "string") rows.push(["Provider", metadata.provider]);
  if (typeof metadata.text_origin === "string") rows.push(["Text origin", humanize(metadata.text_origin)]);
  if (metadata.engine && typeof metadata.engine === "object") {
    const engine = metadata.engine as { name?: string; version?: string; languages?: string[] };
    rows.push(["OCR engine", `${engine.name ?? "OCR"} ${engine.version ?? ""}${engine.languages?.length ? ` (${engine.languages.join(", ")})` : ""}`]);
  } else if (typeof metadata.engine === "string") {
    rows.push(["Engine", `${metadata.engine} ${String(metadata.engine_version ?? "")}`]);
  }
  if (Array.isArray(metadata.page_map) && metadata.page_map.length > 0) rows.push(["Pages", String(metadata.page_map.length)]);
  return rows;
}

function StartDocumentProcessing({ apiBase, evidenceId, onStarted }: { apiBase: string; evidenceId: string; onStarted: () => void }) {
  const { mutate } = useSession();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await mutate(`${apiBase}/evidence/${evidenceId}/processing`, { body: { job_type: "document_text", ocr: "if_needed" } });
      onStarted();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex flex-wrap items-center gap-2">
      {error ? (
        <span role="alert" className="text-xs text-bad">
          {error}
        </span>
      ) : null}
      <Button size="sm" icon={ScanText} onClick={() => void start()} disabled={busy} busy={busy}>
        Extract text
      </Button>
    </span>
  );
}
