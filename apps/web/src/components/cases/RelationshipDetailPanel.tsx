"use client";

import { ArrowRight, CircleCheck, CircleX, X } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { Evidence, Page, RelationshipDetail } from "@/lib/workspace-types";

import {
  ActionError,
  Button,
  CopyButton,
  Disclosure,
  ErrorNotice,
  Field,
  IconButton,
  KeyValue,
  LoadingState,
  Mono,
  OriginBadge,
  ProvenanceBadge,
  ReviewBadge,
  Select,
  StatusBadge,
  SubHeading,
  TextInput,
  Timestamp,
  humanize,
} from "../ui";
import { useCase } from "./CaseContext";

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
  const headingId = useId();
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
    <aside aria-label="Relationship details" className="min-w-0 rounded-lg border border-line bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
        <h2 id={headingId} className="text-heading font-semibold text-ink">
          Relationship details
        </h2>
        <IconButton icon={X} label="Close relationship details" onClick={onClose} />
      </div>
      <div className="space-y-5 p-4 text-sm">
        {detail.state === "error" && !data ? <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} /> : null}
        {!data && detail.state !== "error" ? <LoadingState label="Loading relationship…" rows={4} /> : null}
        {data ? (
          <>
            <div className="space-y-2">
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-read">
                <Link href={`${base}/entities/${data.source.id}`} className="font-medium break-words text-accent hover:underline">
                  {data.source.display_name}
                </Link>
                <span className="inline-flex items-center gap-1 text-muted">
                  <ArrowRight aria-hidden="true" className="size-4" />
                  <Mono className="text-ink">{data.predicate}</Mono>
                  <ArrowRight aria-hidden="true" className="size-4" />
                </span>
                <Link href={`${base}/entities/${data.target.id}`} className="font-medium break-words text-accent hover:underline">
                  {data.target.display_name}
                </Link>
              </p>
              <div className="flex flex-wrap gap-1.5">
                <OriginBadge origin={data.origin} />
                <ReviewBadge status={data.review_status} />
              </div>
            </div>
            <KeyValue
              compact
              items={[
                ["Valid from", <Timestamp key="from" value={data.valid_from} fallback="Not stated" />],
                ["Valid to", <Timestamp key="to" value={data.valid_to} fallback="Not stated" />],
                ["Description", data.description || "None"],
                [
                  "Created by run",
                  data.created_by_query_run_id ? (
                    <Link key="run" href={`${base}/runs/${data.created_by_query_run_id}`} className="text-accent hover:underline">
                      View execution
                    </Link>
                  ) : (
                    "Recorded by an analyst"
                  ),
                ],
              ]}
            />
            <ActionError message={error} />

            <section className="space-y-2">
              <SubHeading>Supporting and contradicting references</SubHeading>
              {data.references.length === 0 ? (
                <p className="rounded-md bg-warn-soft px-3 py-2 text-warn">No references. This relationship is not yet supported by evidence.</p>
              ) : (
                <ul className="space-y-2">
                  {data.references.map((reference) => (
                    <li key={reference.id} className="space-y-1.5 rounded-md border border-line p-3">
                      <div className="flex flex-wrap items-center gap-1.5">
                        {reference.stance === "supports" ? (
                          <StatusBadge tone="ok" icon={CircleCheck} label="Supports" />
                        ) : (
                          <StatusBadge tone="bad" icon={CircleX} label="Contradicts" />
                        )}
                        {reference.evidence_acquisition_method ? <ProvenanceBadge evidence={{ acquisition_method: reference.evidence_acquisition_method }} /> : null}
                        {reference.observation_type ? <span className="text-xs text-muted">Observation: {humanize(reference.observation_type)}</span> : null}
                      </div>
                      {reference.evidence_id ? (
                        <Link href={`${base}/evidence/${reference.evidence_id}`} className="block font-medium break-words text-accent hover:underline">
                          {reference.evidence_title ?? "Evidence"}
                        </Link>
                      ) : null}
                      {reference.evidence_sha256 ? (
                        <p className="flex items-center gap-1 text-xs text-muted">
                          SHA-256 <Mono>{reference.evidence_sha256.slice(0, 16)}…</Mono>
                          <CopyButton value={reference.evidence_sha256} label="Copy SHA-256" />
                        </p>
                      ) : null}
                      {reference.observation_collected_at ? (
                        <p className="text-xs text-muted">
                          Collected <Timestamp value={reference.observation_collected_at} />
                        </p>
                      ) : null}
                      {reference.note ? <p className="text-sm text-ink">{reference.note}</p> : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="space-y-2">
              <SubHeading>Review history</SubHeading>
              {data.decisions.length === 0 ? <p className="text-muted">No review decisions recorded.</p> : null}
              <ol className="space-y-2">
                {data.decisions.map((decision) => (
                  <li key={decision.id} className="border-l border-line pl-3">
                    <span className="font-medium text-ink">
                      {humanize(decision.previous_value)} → {humanize(decision.new_value)}
                    </span>
                    <span className="block text-xs text-muted">
                      <Timestamp value={decision.decided_at} />
                    </span>
                    {decision.rationale ? <span className="mt-0.5 block text-sm text-ink">{decision.rationale}</span> : null}
                  </li>
                ))}
              </ol>
            </section>

            {writable ? (
              <div className="space-y-3 border-t border-line pt-4">
                <form onSubmit={review} className="space-y-3">
                  <SubHeading>Record a review decision</SubHeading>
                  <Field label="Review status" htmlFor={`review-${relationshipId}`}>
                    <Select id={`review-${relationshipId}`} name="review_status" defaultValue={data.review_status}>
                      {REVIEW_STATES.map((state) => (
                        <option key={state} value={state}>
                          {humanize(state)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Rationale" htmlFor={`rationale-${relationshipId}`} hint="Recommended: what in the evidence supports this decision.">
                    <TextInput id={`rationale-${relationshipId}`} name="rationale" maxLength={5000} />
                  </Field>
                  <Button type="submit" variant="primary" disabled={busy} busy={busy}>
                    Save decision
                  </Button>
                </form>
                <Disclosure summary="Add an evidence reference">
                  <form onSubmit={addReference} className="space-y-3">
                    <Field label="Evidence" htmlFor={`ref-evidence-${relationshipId}`}>
                      <Select id={`ref-evidence-${relationshipId}`} name="evidence_id" required>
                        {(evidence.data?.items ?? []).map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.title}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Stance" htmlFor={`ref-stance-${relationshipId}`}>
                      <Select id={`ref-stance-${relationshipId}`} name="stance">
                        <option value="supports">Supports</option>
                        <option value="contradicts">Contradicts</option>
                      </Select>
                    </Field>
                    <Field label="Note (optional)" htmlFor={`ref-note-${relationshipId}`}>
                      <TextInput id={`ref-note-${relationshipId}`} name="note" maxLength={2000} />
                    </Field>
                    <Button type="submit" disabled={busy || (evidence.data?.items.length ?? 0) === 0}>
                      Add reference
                    </Button>
                  </form>
                </Disclosure>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </aside>
  );
}
