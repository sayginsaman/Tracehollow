"use client";

import Link from "next/link";
import { useState } from "react";

import type { Relationship } from "@/lib/workspace-types";

import { DataTable, OriginBadge, ReviewBadge, Td, Th, Tr, cn } from "../ui";
import { useCase } from "./CaseContext";
import { RelationshipDetailPanel } from "./RelationshipDetailPanel";

export function reviewBadge(status: string) {
  return <ReviewBadge status={status} />;
}

export function RelationshipTable({
  relationships,
  onChanged,
  split = false,
}: {
  relationships: Relationship[];
  onChanged?: () => void;
  /** Show the selected relationship beside the table on wide screens instead of below it. */
  split?: boolean;
}) {
  const { base } = useCase();
  const [selected, setSelected] = useState<string | null>(null);

  if (relationships.length === 0) return <p className="text-sm text-muted">No relationships.</p>;
  return (
    <div className={cn("gap-4", selected && split ? "grid items-start xl:grid-cols-[minmax(0,1fr)_26rem]" : "space-y-4")}>
      <DataTable caption="Relationships" minWidth="44rem" className="rounded-md border border-line">
        <thead>
          <tr>
            <Th>Source</Th>
            <Th>Relationship</Th>
            <Th>Target</Th>
            <Th>Origin</Th>
            <Th>Review</Th>
            <Th className="text-right">References</Th>
          </tr>
        </thead>
        <tbody>
          {relationships.map((relationship) => {
            const open = selected === relationship.id;
            return (
              <Tr key={relationship.id} selected={open}>
                <Td className="max-w-56">
                  <Link href={`${base}/entities/${relationship.source.id}`} className="break-words text-accent hover:underline">
                    {relationship.source.display_name}
                  </Link>
                </Td>
                <Td>
                  <button
                    type="button"
                    onClick={() => setSelected(open ? null : relationship.id)}
                    aria-expanded={open}
                    title="Inspect origin, review history and evidence"
                    className="rounded border border-line bg-sunken px-1.5 py-0.5 font-mono text-code text-ink hover:border-accent hover:text-accent"
                  >
                    {relationship.predicate}
                  </button>
                </Td>
                <Td className="max-w-56">
                  <Link href={`${base}/entities/${relationship.target.id}`} className="break-words text-accent hover:underline">
                    {relationship.target.display_name}
                  </Link>
                </Td>
                <Td>
                  <OriginBadge origin={relationship.origin} />
                </Td>
                <Td>
                  <ReviewBadge status={relationship.review_status} />
                </Td>
                <Td className="text-right text-muted tabular-nums">{relationship.reference_count}</Td>
              </Tr>
            );
          })}
        </tbody>
      </DataTable>
      {selected ? (
        <div className={split ? "xl:sticky xl:top-20" : ""}>
          <RelationshipDetailPanel relationshipId={selected} onClose={() => setSelected(null)} onChanged={onChanged} />
        </div>
      ) : null}
    </div>
  );
}
