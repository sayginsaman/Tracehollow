"use client";

import { Ban, CircleHelp, Equal, GitCompareArrows, History, Search, TriangleAlert, Waypoints } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { formatUtcShort } from "@/lib/messages";
import { useResource } from "@/lib/session-context";
import type { Comparison, Entity, Page } from "@/lib/workspace-types";

import {
  ChoiceField,
  DataTable,
  ErrorNotice,
  FieldGroup,
  LoadingState,
  Mono,
  Notice,
  OriginBadge,
  OutcomeBadge,
  PageHeader,
  Panel,
  ReviewBadge,
  StatusBadge,
  Td,
  TextInput,
  Th,
  Tr,
  humanize,
  plural,
} from "../ui";
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
  if (!values || values.length === 0) return "None";
  return values[0] === values[1] ? formatUtcShort(values[0]) : `${formatUtcShort(values[0])} to ${formatUtcShort(values[1])}`;
}

function SectionCount({ count }: { count: number }) {
  return <span className="rounded bg-sunken px-1.5 text-xs font-normal text-muted tabular-nums">{count}</span>;
}

export function CompareView({ initialSelection = [] }: { initialSelection?: string[] }) {
  const { apiBase, base } = useCase();
  const [selected, setSelected] = useState<string[]>(initialSelection.slice(0, MAX_COMPARED));
  const [filter, setFilter] = useState("");
  const entities = useResource<Page<Entity>>(`${apiBase}/entities?limit=100`);
  const comparison = useResource<Comparison>(comparisonPath(apiBase, selected));
  const names = new Map(comparison.data?.entities.map((entity) => [entity.id, entity.display_name]) ?? []);
  const result = selected.length >= 2 ? comparison.data : null;
  const shownEntities = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase("tr");
    const items = entities.data?.items ?? [];
    return needle ? items.filter((entity) => entity.display_name.toLocaleLowerCase("tr").includes(needle) || selected.includes(entity.id)) : items;
  }, [entities.data, filter, selected]);

  const shared = result?.identifiers.filter((item) => item.kind === "shared") ?? [];
  const onlyOne = result?.identifiers.filter((item) => item.kind === "only_one") ?? [];
  const differentIds = result?.identifiers.filter((item) => item.kind === "conflicting_platform_id") ?? [];
  const unknownCount = (result?.absences.length ?? 0) + (result?.unresolved.length ?? 0);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Compare entities"
        description="Choose two to four entities. The comparison only reads records; nothing is merged or linked. Shared values are leads to review, not evidence of identity."
      />

      <Panel title="Entities to compare" description={`${selected.length} of ${MAX_COMPARED} selected.`}>
        {entities.state === "error" ? <ErrorNotice error={entities.error} onRetry={() => void entities.reload()} /> : null}
        {!entities.data && entities.state !== "error" ? <LoadingState label="Loading entities…" rows={3} /> : null}
        {entities.data && entities.data.items.length < 2 ? <Notice>At least two entities are needed for a comparison.</Notice> : null}
        {entities.data && entities.data.items.length >= 2 ? (
          <div className="space-y-3">
            {entities.data.items.length > 12 ? (
              <div className="relative max-w-sm">
                <label htmlFor="compare-filter" className="sr-only">
                  Filter entities by name
                </label>
                <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
                <TextInput id="compare-filter" type="search" placeholder="Filter by name" value={filter} onChange={(event) => setFilter(event.target.value)} className="pl-8" />
              </div>
            ) : null}
            <FieldGroup legend="Entities to compare" legendClassName="sr-only">
              <div className="grid max-h-72 gap-x-4 gap-y-2 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">
                {shownEntities.map((entity) => (
                  <ChoiceField
                    key={entity.id}
                    checked={selected.includes(entity.id)}
                    disabled={!selected.includes(entity.id) && selected.length >= MAX_COMPARED}
                    onChange={() => setSelected(toggleSelection(selected, entity.id))}
                    label={entity.display_name}
                    description={humanize(entity.entity_type)}
                  />
                ))}
              </div>
            </FieldGroup>
          </div>
        ) : null}
      </Panel>

      {selected.length < 2 && entities.data && entities.data.items.length >= 2 ? (
        <p className="flex items-center gap-2 text-sm text-muted">
          <GitCompareArrows aria-hidden="true" className="size-4" />
          Select {selected.length === 0 ? "two entities" : "one more entity"} to see what they share, where they differ and what is unknown.
        </p>
      ) : null}
      {selected.length >= 2 && comparison.state === "error" ? <ErrorNotice error={comparison.error} onRetry={() => void comparison.reload()} /> : null}
      {selected.length >= 2 && comparison.state === "loading" && !comparison.data ? <LoadingState label="Comparing…" rows={5} /> : null}

      {result ? (
        <>
          <Notice>{result.merge_policy}</Notice>

          <Panel title="Side by side" flush>
            <DataTable caption="Compared entities" minWidth="52rem">
              <thead>
                <tr>
                  <Th>Entity</Th>
                  <Th className="text-right">Observations</Th>
                  <Th>Event times (UTC)</Th>
                  <Th>Collected (UTC)</Th>
                  <Th>Sources and collection outcomes</Th>
                </tr>
              </thead>
              <tbody>
                {result.entities.map((entity) => (
                  <Tr key={entity.id}>
                    <Td className="max-w-56">
                      <Link href={`${base}/entities/${entity.id}`} className="font-medium break-words text-accent hover:underline">
                        {entity.display_name}
                      </Link>
                      <div className="mt-1 flex flex-wrap gap-1">
                        <OriginBadge origin={entity.origin} />
                      </div>
                    </Td>
                    <Td className="text-right tabular-nums">{entity.observation_count}</Td>
                    <Td className="text-muted">{span(entity.event_time_span)}</Td>
                    <Td className="text-muted">{span(entity.collected_span)}</Td>
                    <Td>
                      {entity.coverage.length === 0 ? <span className="text-muted">No observations</span> : null}
                      <ul className="space-y-1.5">
                        {entity.coverage.map((source) => (
                          <li key={`${source.source}-${source.acquisition_method}`} className="space-y-1">
                            <span className="block text-ink">
                              <Mono>{source.source}</Mono> · {humanize(source.acquisition_method)} · {plural(source.observations, "observation")}
                            </span>
                            {source.connector_runs.length > 0 ? (
                              <span className="flex flex-wrap gap-1">
                                {source.connector_runs.map((run) => (
                                  <OutcomeBadge key={run.id} outcome={run.outcome} />
                                ))}
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </DataTable>
          </Panel>

          <div className="grid items-start gap-6 lg:grid-cols-2">
            <Panel
              title={
                <span className="flex items-center gap-2">
                  <Equal aria-hidden="true" className="size-4 text-muted" /> Shared identifiers <SectionCount count={shared.length} />
                </span>
              }
              description="The same normalized value on more than one entity. A lead to review, not proof that they are the same."
            >
              {shared.length === 0 ? <p className="text-sm text-muted">No identifier is shared.</p> : null}
              <ul className="space-y-2 text-sm">
                {shared.map((identifier) => (
                  <li key={`${identifier.identifier_type}-${identifier.platform}-${identifier.values.join()}`} className="space-y-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <StatusBadge tone="neutral" icon={Equal} label="Shared" />
                      {humanize(identifier.identifier_type)}
                      {identifier.platform ? <span className="text-muted">({identifier.platform})</span> : null}
                      <Mono className="text-ink">{identifier.values.join(" vs ")}</Mono>
                    </span>
                    <span className="block text-xs text-muted">{identifier.entity_ids.map((id) => names.get(id) ?? id).join(", ")}</span>
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel
              title={
                <span className="flex items-center gap-2">
                  <GitCompareArrows aria-hidden="true" className="size-4 text-muted" /> Differences <SectionCount count={onlyOne.length} />
                </span>
              }
              description="Identifiers only one of the compared entities holds."
            >
              {onlyOne.length === 0 ? <p className="text-sm text-muted">Every identifier is held by more than one entity.</p> : null}
              <ul className="space-y-2 text-sm">
                {onlyOne.map((identifier) => (
                  <li key={`${identifier.identifier_type}-${identifier.platform}-${identifier.values.join()}-${identifier.entity_ids.join()}`}>
                    <span className="flex flex-wrap items-center gap-2">
                      <StatusBadge tone="neutral" label="Only one" />
                      {humanize(identifier.identifier_type)}
                      {identifier.platform ? <span className="text-muted">({identifier.platform})</span> : null}
                      <Mono className="text-ink">{identifier.values.join(", ")}</Mono>
                    </span>
                    <span className="block text-xs text-muted">{identifier.entity_ids.map((id) => names.get(id) ?? id).join(", ")}</span>
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel
              title={
                <span className="flex items-center gap-2">
                  <TriangleAlert aria-hidden="true" className="size-4 text-warn" /> Conflicts{" "}
                  <SectionCount count={differentIds.length + result.conflicts.length} />
                </span>
              }
              description="Sources that disagree. Neither value is preferred; different stable IDs on one platform are different accounts even when names match."
            >
              {differentIds.length === 0 && result.conflicts.length === 0 ? <p className="text-sm text-muted">No conflicts in the recorded observations.</p> : null}
              <ul className="space-y-3 text-sm">
                {differentIds.map((identifier) => (
                  <li key={`ids-${identifier.identifier_type}-${identifier.platform}-${identifier.values.join()}`} className="space-y-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <StatusBadge tone="warn" label="Different stable IDs" />
                      {identifier.platform ? <span className="text-muted">{identifier.platform}</span> : null}
                      <Mono className="text-ink">{identifier.values.join(" vs ")}</Mono>
                    </span>
                    <span className="block text-xs text-muted">{identifier.entity_ids.map((id) => names.get(id) ?? id).join(", ")}</span>
                  </li>
                ))}
                {result.conflicts.map((conflict, index) => (
                  <li key={`${conflict.kind}-${conflict.field}-${index}`} className="space-y-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <StatusBadge tone="warn" label={humanize(conflict.kind)} />
                      <span className="text-ink">{conflict.field}:</span>
                      <span className="break-words text-ink">{conflict.values.join(" | ")}</span>
                    </span>
                    <span className="block text-xs text-muted">{conflict.note}</span>
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel
              title={
                <span className="flex items-center gap-2">
                  <Waypoints aria-hidden="true" className="size-4 text-muted" /> Relationships{" "}
                  <SectionCount count={result.relationships.length + result.shared_neighbours.length} />
                </span>
              }
              description="Links between the compared entities and neighbours they share."
            >
              {result.relationships.length === 0 && result.shared_neighbours.length === 0 ? (
                <p className="text-sm text-muted">No relationships between them or shared neighbours.</p>
              ) : null}
              <ul className="space-y-2 text-sm">
                {result.relationships.map((relationship) => (
                  <li key={relationship.id} className="flex flex-wrap items-center gap-1.5">
                    <span className="text-ink">{relationship.source_name}</span>
                    <Mono className="text-muted">{relationship.predicate}</Mono>
                    <span className="text-ink">{relationship.target_name}</span>
                    <OriginBadge origin={relationship.origin} />
                    <ReviewBadge status={relationship.review_status} />
                  </li>
                ))}
                {result.shared_neighbours.map((neighbour) => (
                  <li key={neighbour.entity_id}>
                    Shared neighbour{" "}
                    <Link href={`${base}/entities/${neighbour.entity_id}`} className="font-medium text-accent hover:underline">
                      {neighbour.display_name}
                    </Link>
                    <span className="block text-xs text-muted">
                      {Object.entries(neighbour.connections)
                        .map(([id, predicates]) => `${names.get(id) ?? id} (${predicates.join(", ")})`)
                        .join("; ")}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          </div>

          <Panel
            title={
              <span className="flex items-center gap-2">
                <History aria-hidden="true" className="size-4 text-muted" /> Changes over time <SectionCount count={result.changes.length} />
              </span>
            }
            description="Differences between successive collections of the same source item. The real-world change happened at an unknown time between them."
            flush
          >
            {result.changes.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No changes between collections.</p> : null}
            {result.changes.length > 0 ? (
              <DataTable caption="Changes between collections" minWidth="48rem">
                <thead>
                  <tr>
                    <Th>Entity</Th>
                    <Th>Field</Th>
                    <Th>Earlier</Th>
                    <Th>Later</Th>
                    <Th>Between collections (UTC)</Th>
                  </tr>
                </thead>
                <tbody>
                  {result.changes.map((change, index) => (
                    <Tr key={`${change.entity_id}-${change.field}-${index}`}>
                      <Td>{names.get(change.entity_id)}</Td>
                      <Td>
                        <Mono>{change.field}</Mono>
                      </Td>
                      <Td className="max-w-xs break-words">{change.previous ?? "None"}</Td>
                      <Td className="max-w-xs break-words">{change.current ?? "None"}</Td>
                      <Td className="whitespace-nowrap text-muted">
                        {formatUtcShort(change.previous_collected_at)} to {formatUtcShort(change.current_collected_at)}
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
            ) : null}
            {result.changes_truncated ? <p className="px-4 pb-3 text-xs text-warn">Only the first changes are shown.</p> : null}
          </Panel>

          <Panel
            title={
              <span className="flex items-center gap-2">
                <CircleHelp aria-hidden="true" className="size-4 text-muted" /> Unknowns and open questions <SectionCount count={unknownCount} />
              </span>
            }
            description="Items missing from a later collection, and shared values nobody has reviewed yet. An item missing from a failed or partial collection is unknown, not deleted."
          >
            {unknownCount === 0 ? <p className="text-sm text-muted">Nothing unresolved in the recorded observations.</p> : null}
            <ul className="space-y-3 text-sm">
              {result.absences.map((absence, index) => (
                <li key={`${absence.connector_id}-${index}`} className="space-y-1">
                  <span className="flex flex-wrap items-center gap-2">
                    {absence.interpretation === "unknown_later_collection_incomplete" ? (
                      <StatusBadge tone="warn" icon={CircleHelp} label="Unknown" />
                    ) : (
                      <StatusBadge tone="neutral" icon={Ban} label="Not observed later" />
                    )}
                    <span className="text-ink">{names.get(absence.entity_id)}</span>
                    <Mono className="text-muted">{absence.connector_id}</Mono>
                    <span className="text-muted">{plural(absence.items_total, "item")}</span>
                    <span className="inline-flex items-center gap-1 text-xs text-muted">
                      later run <OutcomeBadge outcome={absence.later_run_outcome} />
                    </span>
                  </span>
                  <span className="block text-xs text-muted">{absence.note}</span>
                </li>
              ))}
              {result.unresolved.map((item) => (
                <li key={item} className="flex items-start gap-2 text-ink">
                  <CircleHelp aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
                  {item}
                </li>
              ))}
            </ul>
          </Panel>
        </>
      ) : null}
    </div>
  );
}
