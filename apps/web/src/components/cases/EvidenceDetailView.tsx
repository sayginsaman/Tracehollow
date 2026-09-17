"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";

import { collectionMode } from "@/lib/connectors";
import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { EvidenceDetail, EvidencePreview, Observation, Page } from "@/lib/workspace-types";

import { StatusBadge } from "../StatusBadge";
import { Button, EmptyState, ErrorNotice, Field, KeyValue, LoadingState, Mono, Notice, Section, SyntheticBadge, TextInput, formatBytes, humanize } from "../ui";
import { useCase } from "./CaseContext";
import { NotesPanel } from "./NotesPanel";
import { ProcessingJobsPanel } from "./ProcessingImports";

const INTEGRITY_LABELS: Record<string, string> = {
  verified: "Hash verified",
  evidence_file_missing: "File missing",
  evidence_size_mismatch: "Size mismatch",
  evidence_hash_mismatch: "Hash mismatch",
};

export function EvidenceDetailView({ evidenceId }: { evidenceId: string }) {
  const { apiBase, base, writable } = useCase();
  const detail = useResource<EvidenceDetail>(`${apiBase}/evidence/${evidenceId}`);
  const preview = useResource<EvidencePreview>(`${apiBase}/evidence/${evidenceId}/preview`);
  const observations = useResource<Page<Observation>>(`${apiBase}/observations?evidence_id=${evidenceId}&limit=20`);
  const [pretty, setPretty] = useState(true);
  const [jobsKey, setJobsKey] = useState(0);

  if (detail.state === "error" && !detail.data) return <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} />;
  if (!detail.data) return <LoadingState label="Loading evidence…" />;
  const { evidence, integrity } = detail.data;
  const previewData = preview.data;
  const shown = previewData ? (pretty && previewData.pretty_json ? previewData.pretty_json : previewData.text) : "";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link href={`${base}/evidence`} className="text-sm text-accent hover:underline">
          ← Evidence
        </Link>
        <h2 className="text-xl font-semibold">{evidence.title}</h2>
        {evidence.synthetic ? <SyntheticBadge /> : null}
        <StatusBadge tone={integrity.status === "verified" ? "ok" : "bad"} label={INTEGRITY_LABELS[integrity.status] ?? integrity.status} />
      </div>
      {integrity.status !== "verified" ? (
        <p role="alert" className="rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-sm text-bad">
          The stored bytes do not match this record. Do not rely on this evidence until the file is restored from a
          backup. The metadata below is kept for provenance.
        </p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Provenance">
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
              ["Import origin", evidence.import_origin ?? "—"],
              ["Source reference", evidence.source_reference ? <Mono key="ref">{evidence.source_reference}</Mono> : "—"],
              [
                "Source published",
                evidence.source_published_at
                  ? `${formatUtc(evidence.source_published_at)}${evidence.source_published_at_original ? ` (as supplied: ${evidence.source_published_at_original})` : ""}`
                  : "Unknown",
              ],
              [evidence.acquisition_method === "authorized_import" ? "Imported" : "Collected", formatUtc(evidence.collected_at)],
              ...collectionRows(evidence.collection_metadata),
              [
                "Derived from",
                evidence.derived_from_evidence_id ? (
                  <Link key="derived" href={`${base}/evidence/${evidence.derived_from_evidence_id}`} className="text-accent hover:underline">
                    Original snapshot
                  </Link>
                ) : (
                  "—"
                ),
              ],
              [
                "Derived records",
                detail.data.derived_evidence.length > 0 ? (
                  <span key="derived-list">
                    {detail.data.derived_evidence.map((id, index) => (
                      <span key={id}>
                        {index > 0 ? ", " : ""}
                        <Link href={`${base}/evidence/${id}`} className="text-accent hover:underline">
                          {id.slice(0, 8)}
                        </Link>
                      </span>
                    ))}
                  </span>
                ) : (
                  "—"
                ),
              ],
              ["Processed", formatUtc(evidence.created_at)],
              ["Connector", evidence.connector_id ? `${evidence.connector_id} ${evidence.connector_version}` : "—"],
              [
                "Execution",
                evidence.query_run_id ? (
                  <Link key="run" href={`${base}/runs/${evidence.query_run_id}`} className="text-accent hover:underline">
                    View run{evidence.page_index !== null ? ` (page ${evidence.page_index + 1})` : ""}
                  </Link>
                ) : (
                  "—"
                ),
              ],
            ]}
          />
        </Section>
        <Section title="Content metadata">
          <KeyValue
            items={[
              ["Kind", evidence.kind.toUpperCase()],
              ["Content type", evidence.content_type],
              ["Size", `${formatBytes(evidence.size_bytes)} (${evidence.size_bytes} bytes)`],
              ["SHA-256", <Mono key="sha">{evidence.sha256}</Mono>],
              ["Integrity checked", formatUtc(integrity.checked_at)],
              [
                "AI index",
                detail.data.index
                  ? `${humanize(detail.data.index.status)}${detail.data.index.status === "indexed" ? ` (${detail.data.index.chunk_count} passage(s))` : ""}${detail.data.index.error_detail ? ` — ${detail.data.index.error_detail}` : ""}`
                  : "Not tracked",
              ],
              ["Original filename", evidence.original_filename ?? "—"],
              ["Description", evidence.description || "—"],
            ]}
          />
          <p className="mt-3 text-xs text-muted">
            SHA-256 detects changes to stored bytes; it does not prove authorship or authenticity.
          </p>
          {detail.data.duplicate_of.length > 0 ? (
            <p className="mt-2 text-xs text-muted">
              Identical bytes also exist as{" "}
              {detail.data.duplicate_of.map((id, index) => (
                <span key={id}>
                  {index > 0 ? ", " : ""}
                  <Link href={`${base}/evidence/${id}`} className="text-accent hover:underline">
                    {id.slice(0, 8)}
                  </Link>
                </span>
              ))}
              .
            </p>
          ) : null}
        </Section>
      </div>

      <Section
        title="Preview"
        description="Shown as inert plain text. Imported HTML or scripts are never rendered or executed."
        actions={
          <>
            {previewData?.pretty_json ? (
              <Button onClick={() => setPretty(!pretty)} aria-pressed={pretty}>
                {pretty ? "Show original text" : "Show formatted JSON"}
              </Button>
            ) : null}
            <a
              href={`${apiBase}/evidence/${evidenceId}/content`}
              download
              className="inline-flex items-center rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:bg-canvas"
            >
              Download original
            </a>
          </>
        }
      >
        {preview.state === "error" ? <ErrorNotice error={preview.error} onRetry={() => void preview.reload()} /> : null}
        {preview.state === "loading" && !previewData ? <LoadingState label="Loading preview…" /> : null}
        {previewData ? (
          <>
            {previewData.truncated ? (
              <p className="mb-2 text-xs text-warn">
                Preview shows the first {formatBytes(previewData.preview_bytes)} of {formatBytes(previewData.size_bytes)}.
                Download the original for the complete content.
              </p>
            ) : null}
            {previewData.previewable === false ? (
              <Notice>{previewData.note ?? "Binary original: it is not decoded or rendered here."}</Notice>
            ) : null}
            {previewData.previewable === false ? null : (
            <pre
              tabIndex={0}
              aria-label="Evidence content preview"
              className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-canvas p-3 font-mono text-xs"
            >
              {shown}
            </pre>
            )}
          </>
        ) : null}
      </Section>

      {["pdf", "archive"].includes(detail.data.evidence.kind) || detail.data.evidence.collection_metadata.import_format ? (
        <Section
          title="Processing"
          description="Records derived from this original by the worker. The original itself is never changed."
          actions={
            writable && detail.data.evidence.kind === "pdf" ? (
              <StartDocumentProcessing apiBase={apiBase} evidenceId={evidenceId} onStarted={() => setJobsKey((value) => value + 1)} />
            ) : null
          }
        >
          <ProcessingJobsPanel key={jobsKey} apiBase={apiBase} base={base} evidenceId={evidenceId} writable={writable} />
        </Section>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Linked entities">
          {detail.data.linked_entities.length === 0 ? <EmptyState>Not linked to any entity.</EmptyState> : null}
          <ul className="space-y-1 text-sm">
            {detail.data.linked_entities.map((link) => (
              <li key={link.link_id}>
                <Link href={`${base}/entities/${link.entity_id}`} className="text-accent hover:underline">
                  {link.display_name}
                </Link>{" "}
                <span className="text-xs text-muted">({humanize(link.entity_type)})</span>
              </li>
            ))}
          </ul>
        </Section>
        <Section title="Relationships referencing this evidence">
          {detail.data.linked_relationships.length === 0 ? <EmptyState>No relationship references it.</EmptyState> : null}
          <ul className="space-y-1 text-sm">
            {detail.data.linked_relationships.map((link) => (
              <li key={link.reference_id}>
                <span className="font-mono text-xs">{link.predicate}</span>{" "}
                <span className="text-xs text-muted">({link.stance})</span>
              </li>
            ))}
          </ul>
          <Link href={`${base}/relationships`} className="mt-2 inline-block text-xs text-accent hover:underline">
            Open relationships
          </Link>
        </Section>
      </div>

      {detail.data.observation_count > 0 ? (
        <Section title={`Observations from this evidence (${detail.data.observation_count})`}>
          {observations.state === "error" ? <ErrorNotice error={observations.error} /> : null}
          <ul className="space-y-2 text-sm">
            {observations.data?.items.map((observation) => (
              <li key={observation.id} className="rounded-md border border-line p-2">
                <div className="text-xs text-muted">
                  {humanize(observation.observation_type)} · source object <Mono>{observation.source_object_id ?? "—"}</Mono>
                  {observation.entity_id ? (
                    <>
                      {" · "}
                      <Link href={`${base}/entities/${observation.entity_id}`} className="text-accent hover:underline">
                        entity
                      </Link>
                    </>
                  ) : null}
                </div>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs">
                  {JSON.stringify(observation.payload, null, 2)}
                </pre>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <NotesPanel subject={{ evidence_id: evidenceId }} title="Notes on this evidence" />
      {evidence.acquisition_method === "authorized_import" && writable ? (
        <EvidenceDeletion evidenceId={evidenceId} title={evidence.title} />
      ) : null}
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
    <Section
      title="Delete this evidence"
      description="Removes the stored original, its index passages and vectors, entity links, relationship references and notes about it. AI answers that cited it will show that the source was deleted."
    >
      <form onSubmit={remove} className="space-y-3">
        {error ? (
          <p role="alert" className="text-sm text-bad">
            {error}
          </p>
        ) : null}
        <Field label={`Type the evidence title to confirm: ${title}`} htmlFor="confirm-evidence-title">
          <TextInput id="confirm-evidence-title" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" />
        </Field>
        <Button type="submit" variant="danger" disabled={busy || confirmation !== title}>
          {busy ? "Deleting…" : "Delete evidence"}
        </Button>
      </form>
    </Section>
  );
}

function collectionRows(metadata: Record<string, unknown>): [string, ReactNode][] {
  const rows: [string, ReactNode][] = [];
  if (typeof metadata.final_url === "string" && metadata.final_url !== metadata.requested_url) {
    rows.push(["Requested URL", <Mono key="requested">{String(metadata.requested_url ?? "")}</Mono>]);
  }
  if (typeof metadata.http_status === "number") rows.push(["HTTP status", String(metadata.http_status)]);
  if (Array.isArray(metadata.redirects) && metadata.redirects.length > 0) rows.push(["Redirects", String(metadata.redirects.length)]);
  if (metadata.truncated === true) rows.push(["Truncated", "Yes: the response exceeded the size limit"]);
  if (typeof metadata.decoded_with === "string") rows.push(["Character set", metadata.decoded_with]);
  if (typeof metadata.engine === "string") rows.push(["Engine", `${metadata.engine} ${String(metadata.engine_version ?? "")}`]);
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
    <span className="flex items-center gap-2">
      {error ? <span role="alert" className="text-xs text-bad">{error}</span> : null}
      <Button onClick={() => void start()} disabled={busy} aria-busy={busy}>
        Extract text
      </Button>
    </span>
  );
}
