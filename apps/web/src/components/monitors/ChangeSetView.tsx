"use client";

import Link from "next/link";
import { useState } from "react";

import { changeCountsText } from "@/lib/monitoring";
import { useResource } from "@/lib/session-context";
import type { ChangeEvent, ChangeKind, ChangeSet, ChangeSetDetail, Page } from "@/lib/workspace-types";

import { useCase } from "../cases/CaseContext";
import { usePageCrumb } from "../shell/ShellContext";
import {
  CHANGE_KIND_LABELS,
  ChangeKindBadge,
  ChangeSetStatusBadge,
  DataTable,
  EmptyState,
  ErrorNotice,
  KeyValue,
  LoadingState,
  Mono,
  Notice,
  PageHeader,
  Pagination,
  Panel,
  SegmentedFilter,
  Td,
  Th,
  Timestamp,
  Tr,
  humanize,
} from "../ui";

const EVENT_PAGE = 50;
const KINDS: ChangeKind[] = ["new", "changed", "not_observed", "conflicting", "unknown"];

export const CHANGE_STATUS_TEXT: Record<string, string> = {
  baseline_established: "First comparable collection. It becomes the baseline; later runs are compared with it.",
  no_meaningful_change: "Compared with the previous comparable collection, nothing meaningful changed. Timestamps and counters are ignored.",
  changes_detected: "Compared with the previous comparable collection, some observations are new, changed or no longer observed.",
  unknown: "The comparison is incomplete, so absence cannot be judged. Nothing is reported as removed.",
  baseline_incompatible: "The previous collection used a different source version, input, parameters or scope, so the two cannot be compared. This run starts a new baseline.",
};

function EvidenceLink({ id, available, label }: { id: string | null; available: boolean | null; label: string }) {
  const { base } = useCase();
  if (!id) return null;
  if (available === false) return <span className="text-xs text-muted">{label}: removed</span>;
  return (
    <Link href={`${base}/evidence/${id}`} className="text-xs text-accent hover:underline">
      {label} evidence
    </Link>
  );
}

function ValueCell({ value }: { value: string | null }) {
  if (value === null) return <span className="text-muted">None</span>;
  return <span className="break-words">{value}</span>;
}

function EventsTable({ events }: { events: ChangeEvent[] }) {
  const { base } = useCase();
  return (
    <DataTable caption="Change events" minWidth="56rem">
      <thead>
        <tr>
          <Th>Change</Th>
          <Th>Observation</Th>
          <Th>Before</Th>
          <Th>After</Th>
          <Th>Records</Th>
        </tr>
      </thead>
      <tbody>
        {events.map((event) => (
          <Tr key={event.id}>
            <Td>
              <ChangeKindBadge kind={event.kind} />
            </Td>
            <Td className="max-w-[20rem]">
              <p className="text-ink">{humanize(event.observation_type)}</p>
              {event.source_object_id ? <Mono className="block break-all text-xs text-muted">{event.source_object_id}</Mono> : null}
              {event.field ? <p className="text-xs text-muted">Field: {humanize(event.field)}</p> : null}
              {event.note ? <p className="mt-1 max-w-[48ch] text-xs text-muted">{event.note}</p> : null}
            </Td>
            <Td className="max-w-[16rem] text-sm">
              <ValueCell value={event.previous_value} />
            </Td>
            <Td className="max-w-[16rem] text-sm">
              <ValueCell value={event.current_value} />
            </Td>
            <Td>
              <div className="flex flex-col gap-1">
                {event.entity_id ? (
                  <Link href={`${base}/entities/${event.entity_id}`} className="text-xs text-accent hover:underline">
                    Entity
                  </Link>
                ) : null}
                <EvidenceLink id={event.previous_evidence_id} available={event.previous_evidence_available} label="Before" />
                <EvidenceLink id={event.current_evidence_id} available={event.current_evidence_available} label="After" />
              </div>
            </Td>
          </Tr>
        ))}
      </tbody>
    </DataTable>
  );
}

export function ChangeSetView({ changeSetId }: { changeSetId: string }) {
  const { apiBase, base } = useCase();
  const [kind, setKind] = useState<ChangeKind | null>(null);
  const [offset, setOffset] = useState(0);
  const detail = useResource<ChangeSetDetail>(
    `${apiBase}/change-sets/${changeSetId}?limit=${EVENT_PAGE}&offset=${offset}${kind ? `&kind=${kind}` : ""}`,
  );
  usePageCrumb(detail.data ? `${detail.data.connector_id} changes` : null);

  if (detail.state === "error" && !detail.data) return <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} />;
  if (!detail.data) return <LoadingState label="Loading changes…" rows={6} />;
  const data = detail.data;
  const total = Object.values(data.counts).reduce((sum, value) => sum + value, 0);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Changes since the previous collection"
        meta={
          <>
            <ChangeSetStatusBadge status={data.status} />
            <Mono>
              {data.connector_id} {data.connector_version}
            </Mono>
            <span>
              Compared <Timestamp value={data.created_at} />
            </span>
          </>
        }
      />

      <Notice tone={data.status === "unknown" || data.status === "baseline_incompatible" ? "warn" : "neutral"}>{CHANGE_STATUS_TEXT[data.status] ?? humanize(data.status)}</Notice>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-6">
          {data.limitations.length ? (
            <Panel title="Limits of this comparison">
              <ul className="max-w-[72ch] list-inside list-disc space-y-1 text-sm text-ink">
                {data.limitations.map((limitation) => (
                  <li key={limitation}>{limitation}</li>
                ))}
              </ul>
            </Panel>
          ) : null}

          <Panel title="Events" description="Each event links to the collections and evidence it came from. A change is an observation difference, not a claim about a person." flush>
            <div className="border-b border-line px-4 py-3">
              <SegmentedFilter
                label="Filter by change"
                options={[
                  { value: "all", label: `All (${total})` },
                  ...KINDS.filter((value) => data.counts[value]).map((value) => ({ value, label: `${CHANGE_KIND_LABELS[value]} (${data.counts[value]})` })),
                ]}
                value={kind ?? "all"}
                onChange={(value) => {
                  setKind(value === "all" ? null : value);
                  setOffset(0);
                }}
              />
            </div>
            {data.events.items.length === 0 ? (
              <div className="p-4">
                <EmptyState compact>
                  {data.status === "baseline_established"
                    ? "Nothing to compare yet. The next comparable run is compared with this one."
                    : "No events."}
                </EmptyState>
              </div>
            ) : (
              <EventsTable events={data.events.items} />
            )}
            {data.truncated ? (
              <p className="border-t border-line px-4 py-2 text-xs text-warn">Only the first events were recorded; the counts include every difference found.</p>
            ) : null}
            <div className="border-t border-line px-4 py-2">
              <Pagination total={data.events.total} limit={EVENT_PAGE} offset={offset} onChange={setOffset} />
            </div>
          </Panel>
        </div>

        <div className="min-w-0 space-y-6 lg:sticky lg:top-20">
          <Panel title="Compared collections">
            <KeyValue
              compact
              items={[
                ["This run", <Link key="current" href={`${base}/runs/${data.query_run_id}`} className="text-accent hover:underline">Open run</Link>],
                [
                  "Baseline",
                  data.baseline_query_run_id ? (
                    <Link key="baseline" href={`${base}/runs/${data.baseline_query_run_id}`} className="text-accent hover:underline">
                      Open baseline run
                    </Link>
                  ) : (
                    "None"
                  ),
                ],
                ["This coverage", data.coverage_complete ? "Complete within limits" : "Incomplete"],
                ["Baseline coverage", data.baseline_coverage_complete === null ? "Not applicable" : data.baseline_coverage_complete ? "Complete within limits" : "Incomplete"],
                ["Summary", changeCountsText(data.counts) || "No differences"],
                ...(data.monitor_id
                  ? ([["Monitor", <Link key="monitor" href={`${base}/monitors/${data.monitor_id}`} className="text-accent hover:underline">Open monitor</Link>]] as [string, React.ReactNode][])
                  : []),
              ]}
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}

/** Change sets produced for one run (shown on the run page). */
export function RunChanges({ runId }: { runId: string }) {
  const { apiBase, base } = useCase();
  const sets = useResource<Page<ChangeSet>>(`${apiBase}/change-sets?query_run_id=${runId}&limit=20`);
  if (!sets.data || sets.data.items.length === 0) return null;
  return (
    <Panel title="Changes since the previous collection" description="Compared per source with the previous comparable run of this saved query." flush>
      <ul className="divide-y divide-line">
        {sets.data.items.map((set) => (
          <li key={set.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
            <Link href={`${base}/changes/${set.id}`} className="inline-flex min-w-0 flex-wrap items-center gap-2 rounded hover:underline">
              <Mono className="text-ink">{set.connector_id}</Mono>
              <span className="sr-only">: </span>
              <ChangeSetStatusBadge status={set.status} />
            </Link>
            <span className="text-xs text-muted">{changeCountsText(set.counts)}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
