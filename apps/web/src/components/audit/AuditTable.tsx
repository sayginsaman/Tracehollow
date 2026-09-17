"use client";

import { useState } from "react";

import { useResource } from "@/lib/session-context";
import type { AuditEvent, Page } from "@/lib/workspace-types";

import {
  DataTable,
  EmptyState,
  ErrorNotice,
  Field,
  LoadingState,
  Mono,
  Pagination,
  SegmentedFilter,
  StatusBadge,
  Td,
  TextInput,
  Th,
  Timestamp,
  Toolbar,
  Tr,
  type Tone,
} from "../ui";

const PAGE = 50;
const OUTCOMES: { value: "all" | AuditEvent["outcome"]; label: string }[] = [
  { value: "all", label: "All" },
  { value: "succeeded", label: "Succeeded" },
  { value: "denied", label: "Denied" },
  { value: "failed", label: "Failed" },
];
const OUTCOME_TONES: Record<string, Tone> = { succeeded: "ok", denied: "warn", failed: "bad" };

/** Reads like "case.members.added" → "Case members added"; the raw action stays visible for filtering. */
export function describeAction(action: string): string {
  const text = action.replace(/[._]/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function detailText(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
    .join(" · ");
}

/** Paged audit events with outcome and action filters. `path` is the API collection without a query string. */
export function AuditTable({ path, showCase = false }: { path: string; showCase?: boolean }) {
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]["value"]>("all");
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const filter = action.trim().toLowerCase();
  const validAction = /^[a-z_.]*$/.test(filter);
  const query = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (outcome !== "all") query.set("outcome", outcome);
  if (filter && validAction) query.set("action", filter);
  const events = useResource<Page<AuditEvent>>(`${path}?${query.toString()}`);

  return (
    <div>
      <Toolbar label="Filter audit events" className="border-b border-line px-4 py-3">
        <SegmentedFilter
          label="Outcome"
          options={OUTCOMES}
          value={outcome}
          onChange={(value) => {
            setOutcome(value);
            setOffset(0);
          }}
        />
        <Field label="Action starts with" htmlFor="audit-action" className="w-full sm:w-64" error={validAction ? null : "Use lowercase letters, dots and underscores."}>
          <TextInput
            id="audit-action"
            value={action}
            placeholder="for example case.members"
            onChange={(event) => {
              setAction(event.target.value);
              setOffset(0);
            }}
          />
        </Field>
      </Toolbar>
      {events.state === "error" ? (
        <div className="p-4">
          <ErrorNotice error={events.error} onRetry={() => void events.reload()} />
        </div>
      ) : null}
      {!events.data && events.state === "loading" ? <LoadingState label="Loading audit events…" className="p-4" /> : null}
      {events.data && events.data.items.length === 0 ? (
        <div className="p-4">
          <EmptyState compact>No audit events match.</EmptyState>
        </div>
      ) : null}
      {events.data?.items.length ? (
        <DataTable caption="Audit events, newest first" minWidth="60rem">
          <thead>
            <tr>
              <Th>When</Th>
              <Th>Actor</Th>
              <Th>Action</Th>
              <Th>Outcome</Th>
              <Th>Object</Th>
              <Th>Request</Th>
            </tr>
          </thead>
          <tbody>
            {events.data.items.map((event) => (
              <Tr key={event.id}>
                <Td className="whitespace-nowrap text-sm">
                  <Timestamp value={event.occurred_at} />
                </Td>
                <Td className="text-sm">
                  <span className="text-ink">{event.actor_label}</span>
                  <span className="block text-xs text-muted">{event.actor_type === "user" ? "Account" : event.actor_type === "service" ? "Background work" : "System"}</span>
                </Td>
                <Td className="max-w-[22rem] text-sm">
                  <span className="text-ink">{describeAction(event.action)}</span>
                  <Mono className="block text-xs text-muted">{event.action}</Mono>
                  {Object.keys(event.details).length ? <span className="mt-1 block text-xs break-words text-muted">{detailText(event.details)}</span> : null}
                </Td>
                <Td>
                  <StatusBadge tone={OUTCOME_TONES[event.outcome] ?? "neutral"} label={event.outcome.charAt(0).toUpperCase() + event.outcome.slice(1)} />
                </Td>
                <Td className="text-sm">
                  {event.target_type ? <span className="text-ink">{event.target_type.replace(/_/g, " ")}</span> : <span className="text-muted">None</span>}
                  {event.target_id ? <Mono className="block text-xs break-all text-muted">{event.target_id}</Mono> : null}
                  {showCase && event.case_id ? (
                    <span className="block text-xs text-muted">
                      Case <Mono>{event.case_id.slice(0, 8)}</Mono>
                    </span>
                  ) : null}
                </Td>
                <Td>{event.correlation_id ? <Mono className="text-xs text-muted">{event.correlation_id.slice(0, 12)}</Mono> : null}</Td>
              </Tr>
            ))}
          </tbody>
        </DataTable>
      ) : null}
      {events.data ? (
        <div className="border-t border-line px-4 py-2">
          <Pagination total={events.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </div>
      ) : null}
    </div>
  );
}

export const AUDIT_LIMITS =
  "Audit events are written in the same database transaction as the change they describe. They are append-only by convention, not tamper-evident: anyone with direct database access could alter them. Credentials, evidence content and AI text are never recorded.";
