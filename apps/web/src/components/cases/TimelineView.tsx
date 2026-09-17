"use client";

import { CalendarDays, Info } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { formatUtc, formatUtcDayLabel } from "@/lib/messages";
import { useResource } from "@/lib/session-context";
import type { Entity, Page, TimelineData, TimelineItem, TimelineSection } from "@/lib/workspace-types";

import {
  ErrorNotice,
  LoadingState,
  Notice,
  PageHeader,
  Pagination,
  Panel,
  ProvenanceBadge,
  Select,
  Tabs,
  Tag,
  TextInput,
  Toolbar,
  humanize,
  tabPanelProps,
} from "../ui";
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

/** The day an item belongs to, in the same basis as its time: UTC, local wall clock or collection. */
export function dayLabel(item: TimelineItem): string {
  const iso = item.time ?? (item.local_time ? `${item.local_time.slice(0, 10)}T00:00:00Z` : item.collected_at);
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Unknown date";
  const label = formatUtcDayLabel(date);
  if (item.time) return `${label} (UTC)`;
  if (item.local_time) return `${label} (local date)`;
  return `${label} (collected, UTC)`;
}

function clock(item: TimelineItem): string {
  if (item.time) return `${item.time.slice(11, 16)} UTC`;
  if (item.local_time) return `${item.local_time.slice(11, 16)} local`;
  return `${item.collected_at.slice(11, 16)} UTC`;
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

  const groups: { day: string; items: TimelineItem[] }[] = [];
  for (const item of timeline.data?.items ?? []) {
    const day = dayLabel(item);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.items.push(item);
    else groups.push({ day, items: [item] });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Timeline"
        description="Built from observations. Publication and collection dates alone do not establish when something happened in the real world, so the three kinds of time are kept apart."
      />

      <Panel title="Observations over time" flush>
        <Tabs
          label="Timeline sections"
          idPrefix="timeline-section"
          value={section}
          className="px-2 pt-1"
          onChange={(value) => {
            setSection(value);
            setOffset(0);
          }}
          items={(Object.keys(SECTION_LABELS) as TimelineSection[]).map((key) => ({
            value: key,
            label: SECTION_LABELS[key].title,
            count: counts?.[key],
            ariaLabel: counts ? `${SECTION_LABELS[key].title} (${counts[key]})` : undefined,
          }))}
        />
        <div {...tabPanelProps("timeline-section", section)} className="outline-none">
          <div className="space-y-3 border-b border-line px-4 py-3">
            <p className="flex items-start gap-2 text-sm text-muted">
              <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {SECTION_LABELS[section].description}
            </p>
            <Toolbar>
              <div className="min-w-0 flex-1 basis-56">
                <label htmlFor="timeline-entity" className="mb-1.5 block text-xs font-medium text-muted">
                  Entity
                </label>
                <Select
                  id="timeline-entity"
                  value={entityId}
                  onChange={(event) => {
                    setEntityId(event.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">All observations</option>
                  {entities.data?.items.map((entity) => (
                    <option key={entity.id} value={entity.id}>
                      {entity.display_name}
                    </option>
                  ))}
                </Select>
              </div>
              {section === "dated" ? (
                <>
                  <div className="w-44">
                    <label htmlFor="timeline-from" className="mb-1.5 block text-xs font-medium text-muted">
                      From (UTC date)
                    </label>
                    <TextInput id="timeline-from" type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
                  </div>
                  <div className="w-44">
                    <label htmlFor="timeline-to" className="mb-1.5 block text-xs font-medium text-muted">
                      To (UTC date)
                    </label>
                    <TextInput id="timeline-to" type="date" value={to} onChange={(event) => setTo(event.target.value)} />
                  </div>
                </>
              ) : null}
            </Toolbar>
          </div>

          {timeline.state === "error" ? (
            <div className="p-4">
              <ErrorNotice error={timeline.error} onRetry={() => void timeline.reload()} />
            </div>
          ) : null}
          {timeline.state === "loading" && !timeline.data ? <LoadingState label="Loading timeline…" rows={6} className="p-4" /> : null}
          {timeline.data && timeline.data.items.length === 0 ? (
            <div className="p-4">
              <Notice>No observations in this section{entityId || from || to ? " for these filters" : ""}.</Notice>
            </div>
          ) : null}
          {groups.length > 0 ? (
            <div className="divide-y divide-line">
              {groups.map((group) => (
                <section key={group.day} aria-label={group.day} className="px-4 py-4">
                  <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
                    <CalendarDays aria-hidden="true" className="size-4 text-muted" />
                    {group.day}
                  </h3>
                  <ol className="space-y-4">
                    {group.items.map((item) => (
                      <li key={item.observation_id} className="grid gap-x-4 gap-y-1 sm:grid-cols-[7rem_minmax(0,1fr)]">
                        <div className="text-sm text-muted tabular-nums sm:pt-0.5" title={displayTime(item)}>
                          <span className="sr-only">{displayTime(item)}</span>
                          <span aria-hidden="true">{clock(item)}</span>
                        </div>
                        <div className="min-w-0 space-y-1.5 border-l border-line pl-4">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <Tag>{BASIS_LABELS[item.time_basis]}</Tag>
                            <span className="text-xs text-muted">{humanize(item.observation_type)}</span>
                          </div>
                          {item.timestamp_text ? <p className="text-xs text-muted">As written: {item.timestamp_text}</p> : null}
                          <p className="max-w-[72ch] text-read whitespace-pre-wrap break-words text-ink">
                            {item.source_label ? <span className="font-medium">{item.source_label}: </span> : null}
                            {item.summary ?? ""}
                          </p>
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                            {item.entity_id ? (
                              <Link href={`${base}/entities/${item.entity_id}`} className="text-accent hover:underline">
                                {item.entity_name}
                              </Link>
                            ) : null}
                            {item.evidence_id ? (
                              <Link href={`${base}/evidence/${item.evidence_id}`} className="break-words text-accent hover:underline">
                                {item.evidence_title}
                                {item.location?.line_start ? ` (line ${item.location.line_start})` : ""}
                              </Link>
                            ) : null}
                            {item.acquisition_method ? <ProvenanceBadge evidence={{ acquisition_method: item.acquisition_method }} /> : null}
                          </div>
                          {item.notes.length > 0 ? <p className="max-w-[72ch] text-xs text-warn">{item.notes.join(" ")}</p> : null}
                        </div>
                      </li>
                    ))}
                  </ol>
                </section>
              ))}
            </div>
          ) : null}
          {timeline.data ? (
            <Pagination total={timeline.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} className="border-t border-line" />
          ) : null}
        </div>
      </Panel>
    </div>
  );
}
