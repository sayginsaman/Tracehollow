"use client";

import { Database, X } from "lucide-react";
import Link from "next/link";

import { formatUtc } from "@/lib/messages";
import { useResource } from "@/lib/session-context";
import type { CitationDetail, Passage } from "@/lib/workspace-types";

import { useCase } from "../cases/CaseContext";
import { ErrorNotice, IconButton, KeyValue, LoadingState, Mono, ProvenanceBadge, SyntheticBadge, humanize } from "../ui";

const PASSAGE_STATUS: Record<string, string> = {
  source_deleted: "The cited evidence was deleted after this answer was generated. Its content is no longer available.",
  source_expired: "The cited evidence was removed by the case retention policy after this answer was generated. The answer text remains, but this source can no longer be checked.",
  integrity_failed: "The stored evidence file failed its integrity check, so the passage is not shown.",
  evidence_changed: "The evidence record no longer matches the version that was cited.",
  location_not_found: "The cited location could not be found in the original evidence.",
  reindexed: "The evidence was re-indexed after this answer; the passage below is re-read from the original.",
};

/** Renders a passage exactly as stored. All text is rendered as text, never as markup. */
export function PassageView({ passage, highlightLabel }: { passage: Passage; highlightLabel: string }) {
  const { base } = useCase();
  return (
    <div className="space-y-4 text-sm">
      {passage.status !== "available" ? (
        <p role="alert" className="rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-warn">
          {PASSAGE_STATUS[passage.status] ?? humanize(passage.status)}
        </p>
      ) : null}
      {passage.passage !== null ? (
        <figure className="space-y-1.5">
          <figcaption className="text-xs text-muted">
            {highlightLabel} (characters {passage.char_start}–{passage.char_end} of the original)
          </figcaption>
          <pre
            tabIndex={0}
            aria-label="Cited passage in context"
            className="max-h-80 overflow-auto rounded-md border border-line bg-sunken p-3 font-sans text-read whitespace-pre-wrap break-words text-ink"
          >
            {passage.before ? <span className="text-muted">…{passage.before}</span> : null}
            <mark>{passage.passage}</mark>
            {passage.after ? <span className="text-muted">{passage.after}…</span> : null}
          </pre>
        </figure>
      ) : null}
      {passage.json_pointer ? (
        <figure className="space-y-1.5">
          <figcaption className="text-xs text-muted">
            JSON location <Mono>{passage.json_pointer}</Mono> in the original document
          </figcaption>
          {passage.json_value !== null ? (
            <pre
              tabIndex={0}
              aria-label="Cited JSON value"
              className="max-h-60 overflow-auto rounded-md border border-line bg-sunken p-3 font-mono text-code whitespace-pre-wrap break-words text-ink"
            >
              {passage.json_value}
            </pre>
          ) : null}
        </figure>
      ) : null}
      {passage.quote && passage.kind === "json" ? (
        <p className="text-xs text-muted">
          Quoted text: <Mono>{passage.quote}</Mono>
        </p>
      ) : null}
      {passage.evidence_id ? (
        <KeyValue
          compact
          items={[
            [
              "Evidence",
              <span key="evidence" className="inline-flex flex-wrap items-center gap-1.5">
                <Link href={`${base}/evidence/${passage.evidence_id}`} className="break-words text-accent hover:underline">
                  {passage.evidence_title}
                </Link>
                {passage.synthetic ? <SyntheticBadge /> : null}
              </span>,
            ],
            [
              "Acquisition",
              passage.acquisition_method ? <ProvenanceBadge key="acquisition" evidence={{ acquisition_method: passage.acquisition_method }} /> : "Not recorded",
            ],
            ["Collected", formatUtc(passage.collected_at)],
            ["Source published", passage.source_published_at_original ?? formatUtc(passage.source_published_at)],
            ["Integrity", passage.integrity === "verified" ? "SHA-256 verified before display" : (passage.integrity ?? "Not checked")],
          ]}
        />
      ) : null}
    </div>
  );
}

export function CitationPanel({ citationId, onClose }: { citationId: string; onClose: () => void }) {
  const { apiBase } = useCase();
  const detail = useResource<CitationDetail>(`${apiBase}/ai/citations/${citationId}`);
  const data = detail.data;
  return (
    <aside aria-label="Citation details" className="min-w-0 rounded-lg border border-line bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
        <h2 className="text-heading font-semibold text-ink">
          Citation <span className="font-mono">{data?.label ?? ""}</span>
        </h2>
        <IconButton icon={X} label="Close citation" onClick={onClose} />
      </div>
      <div className="p-4">
        {detail.state === "error" && !data ? <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} /> : null}
        {!data && detail.state === "loading" ? <LoadingState label="Opening the cited evidence…" rows={4} /> : null}
        {data?.ref_type === "tool" ? (
          <div className="space-y-2 text-sm">
            <p className="flex items-start gap-2 text-ink">
              <Database aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
              <span>
                Database result from <Mono>{data.tool_result?.tool ?? data.tool_name}</Mono>. It covers the entire case, not only the excerpts
                shown to the model.
              </span>
            </p>
            <pre
              tabIndex={0}
              aria-label="Database result"
              className="max-h-80 overflow-auto rounded-md border border-line bg-sunken p-3 font-mono text-code whitespace-pre-wrap break-words text-ink"
            >
              {JSON.stringify(data.tool_result, null, 2)}
            </pre>
          </div>
        ) : null}
        {data?.passage ? <PassageView passage={data.passage} highlightLabel="Supporting passage" /> : null}
      </div>
    </aside>
  );
}
