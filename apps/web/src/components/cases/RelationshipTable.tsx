"use client";

import Link from "next/link";
import { useState } from "react";

import type { Relationship } from "@/lib/workspace-types";

import { StatusBadge } from "../StatusBadge";
import { EmptyState, OriginBadge, humanize } from "../ui";
import { useCase } from "./CaseContext";
import { RelationshipDetailPanel } from "./RelationshipDetailPanel";

export function reviewBadge(status: string) {
  const tone = status === "accepted" ? "ok" : status === "rejected" ? "bad" : status === "superseded" ? "warn" : "neutral";
  return <StatusBadge tone={tone} label={humanize(status)} />;
}

export function RelationshipTable({
  relationships,
  onChanged,
}: {
  relationships: Relationship[];
  onChanged?: () => void;
}) {
  const { base } = useCase();
  const [selected, setSelected] = useState<string | null>(null);

  if (relationships.length === 0) return <EmptyState>No relationships.</EmptyState>;
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Relationships</caption>
          <thead className="text-xs uppercase tracking-wide text-muted">
            <tr>
              <th scope="col" className="py-2 pr-3 font-medium">Source</th>
              <th scope="col" className="py-2 pr-3 font-medium">Predicate</th>
              <th scope="col" className="py-2 pr-3 font-medium">Target</th>
              <th scope="col" className="py-2 pr-3 font-medium">Origin</th>
              <th scope="col" className="py-2 pr-3 font-medium">Review</th>
              <th scope="col" className="py-2 font-medium">References</th>
            </tr>
          </thead>
          <tbody>
            {relationships.map((relationship) => (
              <tr
                key={relationship.id}
                className={`border-t border-line align-top ${selected === relationship.id ? "bg-canvas" : ""}`}
              >
                <td className="py-2 pr-3">
                  <Link href={`${base}/entities/${relationship.source.id}`} className="text-accent hover:underline">
                    {relationship.source.display_name}
                  </Link>
                </td>
                <td className="py-2 pr-3">
                  <button
                    type="button"
                    onClick={() => setSelected(selected === relationship.id ? null : relationship.id)}
                    aria-expanded={selected === relationship.id}
                    className="font-mono text-xs text-accent underline"
                  >
                    {relationship.predicate}
                  </button>
                </td>
                <td className="py-2 pr-3">
                  <Link href={`${base}/entities/${relationship.target.id}`} className="text-accent hover:underline">
                    {relationship.target.display_name}
                  </Link>
                </td>
                <td className="py-2 pr-3">
                  <OriginBadge origin={relationship.origin} />
                </td>
                <td className="py-2 pr-3">{reviewBadge(relationship.review_status)}</td>
                <td className="py-2">{relationship.reference_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected ? (
        <RelationshipDetailPanel
          relationshipId={selected}
          onClose={() => setSelected(null)}
          onChanged={onChanged}
        />
      ) : null}
    </div>
  );
}
