"use client";

import Link from "next/link";

import { formatUtc } from "@/lib/messages";
import { useResource } from "@/lib/session-context";
import type { CitationDetail, Passage } from "@/lib/workspace-types";

import { Button, ErrorNotice, KeyValue, LoadingState, Mono, SyntheticBadge, humanize } from "../ui";
import { useCase } from "../cases/CaseContext";

const PASSAGE_STATUS: Record<string, string> = {
  source_deleted: "The cited evidence was deleted after this answer was generated. Its content is no longer available.",
  integrity_failed: "The stored evidence file failed its integrity check, so the passage is not shown.",
  evidence_changed: "The evidence record no longer matches the version that was cited.",
  location_not_found: "The cited location could not be found in the original evidence.",
  reindexed: "The evidence was re-indexed after this answer; the passage below is re-read from the original.",
};

/** Renders a passage exactly as stored. All text is rendered as text, never as markup. */
export function PassageView({ passage, highlightLabel }: { passage: Passage; highlightLabel: string }) {
  const { base } = useCase();
  return (
    <div className="space-y-3 text-sm">
      {passage.status !== "available" ? (
        <p role="alert" className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-warn">
          {PASSAGE_STATUS[passage.status] ?? humanize(passage.status)}
        </p>
      ) : null}
      {passage.evidence_id ? (
        <KeyValue
          items={[
            [
              "Evidence",
              <span key="evidence" className="inline-flex flex-wrap items-center gap-2">
                <Link href={`${base}/evidence/${passage.evidence_id}`} className="text-accent hover:underline">
                  {passage.evidence_title}
                </Link>
                {passage.synthetic ? <SyntheticBadge /> : null}
              </span>,
            ],
            ["Acquisition", passage.acquisition_method ? humanize(passage.acquisition_method) : "—"],
            ["Collected", formatUtc(passage.collected_at)],
            ["Source published", passage.source_published_at_original ?? formatUtc(passage.source_published_at)],
            ["Integrity", passage.integrity === "verified" ? "SHA-256 verified before display" : (passage.integrity ?? "—")],
          ]}
        />
      ) : null}
      {passage.passage !== null ? (
        <figure>
          <figcaption className="mb-1 text-xs text-muted">
            {highlightLabel} (characters {passage.char_start}–{passage.char_end} of the original)
          </figcaption>
          <pre
            aria-label="Cited passage in context"
            className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-canvas p-3 font-mono text-xs"
          >
            {passage.before ? <span className="text-muted">…{passage.before}</span> : null}
            <mark className="rounded bg-warn-bg px-0.5 text-ink">{passage.passage}</mark>
            {passage.after ? <span className="text-muted">{passage.after}…</span> : null}
          </pre>
        </figure>
      ) : null}
      {passage.json_pointer ? (
        <figure>
          <figcaption className="mb-1 text-xs text-muted">
            JSON location <Mono>{passage.json_pointer}</Mono> in the original document
          </figcaption>
          {passage.json_value !== null ? (
            <pre
              aria-label="Cited JSON value"
              className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-canvas p-3 font-mono text-xs"
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
    </div>
  );
}

export function CitationPanel({ citationId, onClose }: { citationId: string; onClose: () => void }) {
  const { apiBase } = useCase();
  const detail = useResource<CitationDetail>(`${apiBase}/ai/citations/${citationId}`);
  const data = detail.data;
  return (
    <aside aria-label="Citation details" className="rounded-lg border border-accent/40 bg-surface">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="font-semibold">Citation {data?.label ?? ""}</h2>
        <Button onClick={onClose}>Close</Button>
      </div>
      <div className="px-4 py-3">
        {detail.state === "error" && !data ? <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} /> : null}
        {!data && detail.state === "loading" ? <LoadingState label="Opening the cited evidence…" /> : null}
        {data?.ref_type === "tool" ? (
          <div className="space-y-2 text-sm">
            <p>
              Database result from <Mono>{data.tool_result?.tool ?? data.tool_name}</Mono>. It covers the entire case, not
              only the excerpts shown to the model.
            </p>
            <pre
              aria-label="Database result"
              className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-canvas p-3 font-mono text-xs"
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
