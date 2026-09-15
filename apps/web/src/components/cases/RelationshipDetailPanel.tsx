"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Evidence, Page, RelationshipDetail } from "@/lib/workspace-types";

import { Button, EmptyState, ErrorNotice, KeyValue, LoadingState, Mono, OriginBadge, Select, SyntheticBadge, TextInput, humanize } from "../ui";
import { useCase } from "./CaseContext";
import { reviewBadge } from "./RelationshipTable";

const REVIEW_STATES = ["unreviewed", "accepted", "rejected", "superseded"];

/** Edge inspector: origin, review state and the evidence behind a relationship. */
export function RelationshipDetailPanel({
  relationshipId,
  onClose,
  onChanged,
}: {
  relationshipId: string;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const { apiBase, base, writable } = useCase();
  const { mutate } = useSession();
  const detail = useResource<RelationshipDetail>(`${apiBase}/relationships/${relationshipId}`);
  const evidence = useResource<Page<Evidence>>(writable ? `${apiBase}/evidence?limit=100` : null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function perform(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await detail.reload();
      onChanged?.();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void perform(() =>
      mutate(`${apiBase}/relationships/${relationshipId}/review`, {
        body: { review_status: String(form.get("review_status")), rationale: String(form.get("rationale") ?? "") || null },
      }),
    );
  }

  function addReference(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void perform(() =>
      mutate(`${apiBase}/relationships/${relationshipId}/references`, {
        body: {
          evidence_id: String(form.get("evidence_id")),
          stance: String(form.get("stance")),
          note: String(form.get("note") ?? "") || null,
        },
      }),
    );
  }

  const data = detail.data;
  return (
    <aside
      aria-label="Relationship details"
      className="rounded-lg border border-accent/40 bg-surface p-4 shadow-sm"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">Relationship details</h3>
        <Button onClick={onClose}>Close</Button>
      </div>
      {detail.state === "error" && !data ? <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} /> : null}
      {!data ? <LoadingState /> : null}
      {data ? (
        <div className="space-y-4 text-sm">
          <p>
            <Link href={`${base}/entities/${data.source.id}`} className="font-medium text-accent hover:underline">
              {data.source.display_name}
            </Link>{" "}
            <span className="font-mono text-xs">{data.predicate}</span>{" "}
            <Link href={`${base}/entities/${data.target.id}`} className="font-medium text-accent hover:underline">
              {data.target.display_name}
            </Link>
          </p>
          <KeyValue
            items={[
              ["Origin", <OriginBadge key="origin" origin={data.origin} />],
              ["Review status", reviewBadge(data.review_status)],
              ["Valid from", formatUtc(data.valid_from)],
              ["Valid to", formatUtc(data.valid_to)],
              ["Description", data.description || "—"],
              [
                "Created by run",
                data.created_by_query_run_id ? (
                  <Link key="run" href={`${base}/runs/${data.created_by_query_run_id}`} className="text-accent hover:underline">
                    View execution
                  </Link>
                ) : (
                  "—"
                ),
              ],
            ]}
          />
          {error ? <p role="alert" className="text-bad">{error}</p> : null}

          <div>
            <h4 className="mb-1 font-medium">Supporting and contradicting references</h4>
            {data.references.length === 0 ? (
              <EmptyState>No references. This relationship is not yet supported by evidence.</EmptyState>
            ) : (
              <ul className="space-y-2">
                {data.references.map((reference) => (
                  <li key={reference.id} className="rounded-md border border-line p-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                          reference.stance === "supports" ? "bg-ok-bg text-ok" : "bg-bad-bg text-bad"
                        }`}
                      >
                        {reference.stance === "supports" ? "Supports" : "Contradicts"}
                      </span>
                      {reference.evidence_acquisition_method === "synthetic_fixture" ? <SyntheticBadge /> : null}
                      {reference.observation_type ? (
                        <span className="text-xs text-muted">Observation: {humanize(reference.observation_type)}</span>
                      ) : null}
                    </div>
                    {reference.evidence_id ? (
                      <div className="mt-1">
                        <Link href={`${base}/evidence/${reference.evidence_id}`} className="text-accent hover:underline">
                          {reference.evidence_title ?? "Evidence"}
                        </Link>{" "}
                        <span className="text-xs text-muted">
                          ({reference.evidence_acquisition_method ? humanize(reference.evidence_acquisition_method) : "unknown"})
                        </span>
                        {reference.evidence_sha256 ? (
                          <div className="text-xs text-muted">
                            SHA-256 <Mono>{reference.evidence_sha256}</Mono>
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                    {reference.observation_collected_at ? (
                      <div className="text-xs text-muted">Collected {formatUtc(reference.observation_collected_at)}</div>
                    ) : null}
                    {reference.note ? <p className="mt-1 text-xs">{reference.note}</p> : null}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <h4 className="mb-1 font-medium">Review history</h4>
            {data.decisions.length === 0 ? <EmptyState>No review decisions recorded.</EmptyState> : null}
            <ul className="space-y-1">
              {data.decisions.map((decision) => (
                <li key={decision.id} className="text-xs">
                  {formatUtc(decision.decided_at)}: {humanize(decision.previous_value)} → {humanize(decision.new_value)}
                  {decision.rationale ? <span className="text-muted"> — {decision.rationale}</span> : null}
                </li>
              ))}
            </ul>
          </div>

          {writable ? (
            <div className="grid gap-4 md:grid-cols-2">
              <form onSubmit={review} className="space-y-2">
                <h4 className="font-medium">Record a review decision</h4>
                <label htmlFor={`review-${relationshipId}`} className="sr-only">
                  Review status
                </label>
                <Select id={`review-${relationshipId}`} name="review_status" defaultValue={data.review_status}>
                  {REVIEW_STATES.map((state) => (
                    <option key={state} value={state}>
                      {humanize(state)}
                    </option>
                  ))}
                </Select>
                <label htmlFor={`rationale-${relationshipId}`} className="sr-only">
                  Rationale
                </label>
                <TextInput id={`rationale-${relationshipId}`} name="rationale" placeholder="Rationale (recommended)" maxLength={5000} />
                <Button type="submit" variant="primary" disabled={busy}>
                  Save decision
                </Button>
              </form>
              <form onSubmit={addReference} className="space-y-2">
                <h4 className="font-medium">Add an evidence reference</h4>
                <label htmlFor={`ref-evidence-${relationshipId}`} className="sr-only">
                  Evidence
                </label>
                <Select id={`ref-evidence-${relationshipId}`} name="evidence_id" required>
                  {(evidence.data?.items ?? []).map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title}
                    </option>
                  ))}
                </Select>
                <label htmlFor={`ref-stance-${relationshipId}`} className="sr-only">
                  Stance
                </label>
                <Select id={`ref-stance-${relationshipId}`} name="stance">
                  <option value="supports">Supports</option>
                  <option value="contradicts">Contradicts</option>
                </Select>
                <label htmlFor={`ref-note-${relationshipId}`} className="sr-only">
                  Note
                </label>
                <TextInput id={`ref-note-${relationshipId}`} name="note" placeholder="Note (optional)" maxLength={2000} />
                <Button type="submit" disabled={busy || (evidence.data?.items.length ?? 0) === 0}>
                  Add reference
                </Button>
              </form>
            </div>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}
