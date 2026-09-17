"use client";

import Link from "next/link";
import { useState } from "react";

import { formatUtc } from "@/lib/messages";
import { useResource } from "@/lib/session-context";
import type { Comparison, Entity, Page } from "@/lib/workspace-types";

import { EmptyState, ErrorNotice, LoadingState, Notice, OriginBadge, Section, humanize } from "../ui";
import { useCase } from "./CaseContext";

export const MAX_COMPARED = 4;

/** Toggle an entity in the selection, keeping at most MAX_COMPARED. */
export function toggleSelection(selected: string[], id: string): string[] {
  if (selected.includes(id)) return selected.filter((value) => value !== id);
  if (selected.length >= MAX_COMPARED) return selected;
  return [...selected, id];
}

export function comparisonPath(apiBase: string, ids: string[]): string | null {
  if (ids.length < 2) return null;
  const params = new URLSearchParams();
  for (const id of ids) params.append("entity_id", id);
  return `${apiBase}/entity-comparison?${params.toString()}`;
}

function span(values: string[] | null): string {
  if (!values || values.length === 0) return "none";
  return values[0] === values[1] ? formatUtc(values[0]) : `${formatUtc(values[0])} – ${formatUtc(values[1])}`;
}

export function CompareView() {
  const { apiBase, base } = useCase();
  const [selected, setSelected] = useState<string[]>([]);
  const entities = useResource<Page<Entity>>(`${apiBase}/entities?limit=100`);
  const comparison = useResource<Comparison>(comparisonPath(apiBase, selected));
  const names = new Map(comparison.data?.entities.map((entity) => [entity.id, entity.display_name]) ?? []);
  const result = selected.length >= 2 ? comparison.data : null;

  return (
    <div className="space-y-6">
      <Section title="Compare entities" description="Choose two to four entities. The comparison only reads records; nothing is merged or linked.">
        {entities.state === "error" ? <ErrorNotice error={entities.error} onRetry={() => void entities.reload()} /> : null}
        {entities.data && entities.data.items.length < 2 ? <EmptyState>At least two entities are needed.</EmptyState> : null}
        <fieldset className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          <legend className="sr-only">Entities to compare</legend>
          {entities.data?.items.map((entity) => (
            <label key={entity.id} className="inline-flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selected.includes(entity.id)}
                disabled={!selected.includes(entity.id) && selected.length >= MAX_COMPARED}
                onChange={() => setSelected(toggleSelection(selected, entity.id))}
              />
              {entity.display_name} <span className="text-xs text-muted">({humanize(entity.entity_type)})</span>
            </label>
          ))}
        </fieldset>
      </Section>

      {selected.length >= 2 && comparison.state === "error" ? <ErrorNotice error={comparison.error} onRetry={() => void comparison.reload()} /> : null}
      {selected.length >= 2 && comparison.state === "loading" && !comparison.data ? <LoadingState label="Comparing…" /> : null}
      {result ? (
        <>
          <Notice>{result.merge_policy}</Notice>
          <Section title="Side by side">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-muted">
                  <tr>
                    <th scope="col" className="py-2 pr-3 font-medium">Entity</th>
                    <th scope="col" className="py-2 pr-3 font-medium">Observations</th>
                    <th scope="col" className="py-2 pr-3 font-medium">Event times</th>
                    <th scope="col" className="py-2 pr-3 font-medium">Collected</th>
                    <th scope="col" className="py-2 font-medium">Sources and collection outcomes</th>
                  </tr>
                </thead>
                <tbody>
                  {result.entities.map((entity) => (
                    <tr key={entity.id} className="border-t border-line align-top">
                      <td className="py-2 pr-3">
                        <Link href={`${base}/entities/${entity.id}`} className="font-medium text-accent hover:underline">
                          {entity.display_name}
                        </Link>
                        <div>
                          <OriginBadge origin={entity.origin} />
                        </div>
                      </td>
                      <td className="py-2 pr-3">{entity.observation_count}</td>
                      <td className="py-2 pr-3 font-mono text-xs">{span(entity.event_time_span)}</td>
                      <td className="py-2 pr-3 font-mono text-xs">{span(entity.collected_span)}</td>
                      <td className="py-2 text-xs">
                        {entity.coverage.length === 0 ? "No observations" : null}
                        <ul>
                          {entity.coverage.map((source) => (
                            <li key={`${source.source}-${source.acquisition_method}`}>
                              {source.source} · {humanize(source.acquisition_method)} · {source.observations} observation
                              {source.observations === 1 ? "" : "s"}
                              {source.connector_runs.length > 0
                                ? ` · runs: ${source.connector_runs.map((run) => humanize(run.outcome ?? "pending")).join(", ")}`
                                : ""}
                            </li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Identifiers" description="Shared identifiers are leads to review, not proof that entities are the same.">
            <ul className="space-y-1 text-sm">
              {result.identifiers.map((identifier) => (
                <li key={`${identifier.kind}-${identifier.identifier_type}-${identifier.platform}-${identifier.values.join()}`}>
                  <span
                    className={`mr-2 rounded border px-1.5 py-0.5 text-xs ${
                      identifier.kind === "shared" ? "border-accent" : identifier.kind === "conflicting_platform_id" ? "border-warn text-warn" : "border-line text-muted"
                    }`}
                  >
                    {identifier.kind === "shared" ? "Shared" : identifier.kind === "conflicting_platform_id" ? "Different stable IDs" : "Only one"}
                  </span>
                  {humanize(identifier.identifier_type)}
                  {identifier.platform ? ` (${identifier.platform})` : ""}: <span className="font-mono text-xs">{identifier.values.join(" vs ")}</span>{" "}
                  <span className="text-muted">— {identifier.entity_ids.map((id) => names.get(id) ?? id).join(", ")}</span>
                </li>
              ))}
            </ul>
          </Section>

          <div className="grid gap-6 lg:grid-cols-2">
            <Section title="Relationships">
              {result.relationships.length === 0 && result.shared_neighbours.length === 0 ? <EmptyState>No relationships between them or shared neighbours.</EmptyState> : null}
              <ul className="space-y-1 text-sm">
                {result.relationships.map((relationship) => (
                  <li key={relationship.id}>
                    {relationship.source_name} <em>{relationship.predicate}</em> {relationship.target_name} <OriginBadge origin={relationship.origin} />{" "}
                    <span className={relationship.review_status === "unreviewed" ? "text-warn" : "text-muted"}>{humanize(relationship.review_status)}</span>
                  </li>
                ))}
                {result.shared_neighbours.map((neighbour) => (
                  <li key={neighbour.entity_id}>
                    Shared neighbour <span className="font-medium">{neighbour.display_name}</span>:{" "}
                    {Object.entries(neighbour.connections)
                      .map(([id, predicates]) => `${names.get(id) ?? id} (${predicates.join(", ")})`)
                      .join("; ")}
                  </li>
                ))}
              </ul>
            </Section>
            <Section title="Conflicts and unresolved questions">
              {result.conflicts.length === 0 && result.unresolved.length === 0 ? <EmptyState>None detected in the recorded observations.</EmptyState> : null}
              <ul className="space-y-2 text-sm">
                {result.conflicts.map((conflict, index) => (
                  <li key={`${conflict.kind}-${conflict.field}-${index}`}>
                    <span className="font-medium text-warn">{humanize(conflict.kind)}</span> · {conflict.field}: {conflict.values.join(" | ")}
                    <div className="text-xs text-muted">{conflict.note}</div>
                  </li>
                ))}
                {result.unresolved.map((item) => (
                  <li key={item} className="text-muted">
                    {item}
                  </li>
                ))}
              </ul>
            </Section>
          </div>

          <Section title="Changes over time" description="Differences between successive collections of the same source item. The real-world change happened at an unknown time between them.">
            {result.changes.length === 0 ? <EmptyState>No changes between collections.</EmptyState> : null}
            {result.changes.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th scope="col" className="py-2 pr-3 font-medium">Entity</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Field</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Earlier</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Later</th>
                      <th scope="col" className="py-2 font-medium">Between collections</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.changes.map((change, index) => (
                      <tr key={`${change.entity_id}-${change.field}-${index}`} className="border-t border-line align-top">
                        <td className="py-2 pr-3">{names.get(change.entity_id)}</td>
                        <td className="py-2 pr-3">{change.field}</td>
                        <td className="py-2 pr-3 break-all">{change.previous ?? "—"}</td>
                        <td className="py-2 pr-3 break-all">{change.current ?? "—"}</td>
                        <td className="py-2 font-mono text-xs">
                          {formatUtc(change.previous_collected_at)} → {formatUtc(change.current_collected_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {result.changes_truncated ? <p className="mt-2 text-xs text-warn">Only the first changes are shown.</p> : null}
              </div>
            ) : null}
          </Section>

          <Section title="Items not seen in a later collection" description="An item missing from a failed or partial collection is unknown, not deleted.">
            {result.absences.length === 0 ? <EmptyState>None.</EmptyState> : null}
            <ul className="space-y-2 text-sm">
              {result.absences.map((absence, index) => (
                <li key={`${absence.connector_id}-${index}`}>
                  <span className={absence.interpretation === "unknown_later_collection_incomplete" ? "font-medium text-warn" : "font-medium"}>
                    {absence.interpretation === "unknown_later_collection_incomplete" ? "Unknown" : "Not observed later"}
                  </span>{" "}
                  · {names.get(absence.entity_id)} · {absence.connector_id} · {absence.items_total} item{absence.items_total === 1 ? "" : "s"} (later run:{" "}
                  {humanize(absence.later_run_outcome ?? "pending")})
                  <div className="text-xs text-muted">{absence.note}</div>
                </li>
              ))}
            </ul>
          </Section>
        </>
      ) : null}
    </div>
  );
}
