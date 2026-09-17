"use client";

import Link from "next/link";
import { useState } from "react";

import { formatUtc } from "@/lib/messages";
import { useResource } from "@/lib/session-context";
import type { Entity, Page, TimelineData, TimelineItem, TimelineSection } from "@/lib/workspace-types";

import { EmptyState, ErrorNotice, LoadingState, Pagination, Section, humanize } from "../ui";
import { useCase } from "./CaseContext";

const PAGE_SIZE = 50;

export const SECTION_LABELS: Record<TimelineSection, { title: string; description: string }> = {
  dated: {
    title: "On the UTC timeline",
    description: "Items with an event time, or a publication time the source reported. The basis is shown on each item.",
  },
  local_time_only: {
    title: "Local time only",
    description: "Wall-clock times without a timezone (for example a chat imported with an unknown timezone). Never mixed with UTC.",
  },
  undated: {
    title: "Collection time only",
    description: "Only the retrieval or import time is known. That is not when anything happened.",
  },
};

export const BASIS_LABELS: Record<TimelineItem["time_basis"], string> = {
  event_time: "Event time",
  source_published_at: "Published (per source)",
  local_time_without_timezone: "Local time, timezone unknown",
  collected_at_only: "Collected",
};

/** The time shown for an item, always paired with its basis. */
export function displayTime(item: TimelineItem): string {
  if (item.time) return formatUtc(item.time);
  if (item.local_time) return `${item.local_time.replace("T", " ")} (local)`;
  return formatUtc(item.collected_at);
}

export function TimelineView() {
  const { apiBase, base } = useCase();
  const [section, setSection] = useState<TimelineSection>("dated");
  const [offset, setOffset] = useState(0);
  const [entityId, setEntityId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const params = new URLSearchParams({ section, limit: String(PAGE_SIZE), offset: String(offset) });
  if (entityId) params.set("entity_id", entityId);
  if (section === "dated" && from) params.set("from", new Date(`${from}T00:00:00Z`).toISOString());
  if (section === "dated" && to) params.set("to", new Date(`${to}T23:59:59Z`).toISOString());
  const timeline = useResource<TimelineData>(`${apiBase}/timeline?${params.toString()}`);
  const entities = useResource<Page<Entity>>(`${apiBase}/entities?limit=100`);
  const counts = timeline.data?.sections;

  return (
    <div className="space-y-6">
      <Section
        title="Timeline"
        description="Built from observations. Publication and collection dates alone do not establish when something happened in the real world."
      >
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <div role="tablist" aria-label="Timeline sections" className="flex flex-wrap gap-1">
            {(Object.keys(SECTION_LABELS) as TimelineSection[]).map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={section === key}
                onClick={() => {
                  setSection(key);
                  setOffset(0);
                }}
                className={`rounded-md border px-3 py-1.5 text-sm ${section === key ? "border-accent bg-canvas font-medium" : "border-line"}`}
              >
                {SECTION_LABELS[key].title}
                {counts ? ` (${counts[key]})` : ""}
              </button>
            ))}
          </div>
          <div>
            <label htmlFor="timeline-entity" className="block text-xs text-muted">
              Entity
            </label>
            <select
              id="timeline-entity"
              value={entityId}
              onChange={(event) => {
                setEntityId(event.target.value);
                setOffset(0);
              }}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="">All observations</option>
              {entities.data?.items.map((entity) => (
                <option key={entity.id} value={entity.id}>
                  {entity.display_name}
                </option>
              ))}
            </select>
          </div>
          {section === "dated" ? (
            <>
              <div>
                <label htmlFor="timeline-from" className="block text-xs text-muted">
                  From (UTC date)
                </label>
                <input id="timeline-from" type="date" value={from} onChange={(event) => setFrom(event.target.value)} className="rounded-md border border-line bg-surface px-2 py-1 text-sm" />
              </div>
              <div>
                <label htmlFor="timeline-to" className="block text-xs text-muted">
                  To (UTC date)
                </label>
                <input id="timeline-to" type="date" value={to} onChange={(event) => setTo(event.target.value)} className="rounded-md border border-line bg-surface px-2 py-1 text-sm" />
              </div>
            </>
          ) : null}
        </div>
        <p className="mb-3 text-sm text-muted">{SECTION_LABELS[section].description}</p>
        {timeline.state === "error" ? <ErrorNotice error={timeline.error} onRetry={() => void timeline.reload()} /> : null}
        {timeline.state === "loading" && !timeline.data ? <LoadingState label="Loading timeline…" /> : null}
        {timeline.data && timeline.data.items.length === 0 ? <EmptyState>No observations in this section.</EmptyState> : null}
        {timeline.data && timeline.data.items.length > 0 ? (
          <>
            <ol className="space-y-3 border-l border-line pl-4">
              {timeline.data.items.map((item) => (
                <li key={item.observation_id} className="text-sm">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-mono text-xs">{displayTime(item)}</span>
                    <span className="rounded border border-line px-1.5 py-0.5 text-xs text-muted">{BASIS_LABELS[item.time_basis]}</span>
                    <span className="text-xs text-muted">{humanize(item.observation_type)}</span>
                  </div>
                  {item.timestamp_text ? <div className="text-xs text-muted">As written: {item.timestamp_text}</div> : null}
                  <p className="mt-1 whitespace-pre-wrap break-words">
                    {item.source_label ? <span className="font-medium">{item.source_label}: </span> : null}
                    {item.summary ?? ""}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-3 text-xs">
                    {item.entity_id ? (
                      <Link href={`${base}/entities/${item.entity_id}`} className="text-accent hover:underline">
                        {item.entity_name}
                      </Link>
                    ) : null}
                    {item.evidence_id ? (
                      <Link href={`${base}/evidence/${item.evidence_id}`} className="text-accent hover:underline">
                        {item.evidence_title}
                        {item.location?.line_start ? ` (line ${item.location.line_start})` : ""}
                      </Link>
                    ) : null}
                    {item.acquisition_method ? <span className="text-muted">{humanize(item.acquisition_method)}</span> : null}
                  </div>
                  {item.notes.length > 0 ? <p className="mt-1 text-xs text-warn">{item.notes.join(" ")}</p> : null}
                </li>
              ))}
            </ol>
            <Pagination total={timeline.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
          </>
        ) : null}
      </Section>
    </div>
  );
}
