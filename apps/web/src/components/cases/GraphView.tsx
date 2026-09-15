"use client";

import Link from "next/link";
import { useState } from "react";

import { useResource } from "@/lib/session-context";
import type { Entity, GraphData, Page } from "@/lib/workspace-types";

import { EmptyState, ErrorNotice, LoadingState, Notice, OriginBadge, Section, humanize } from "../ui";
import { useCase } from "./CaseContext";
import { GraphCanvas } from "./GraphCanvas";
import { RelationshipDetailPanel } from "./RelationshipDetailPanel";
import { reviewBadge } from "./RelationshipTable";

export function GraphView() {
  const { apiBase, base } = useCase();
  const [focus, setFocus] = useState("");
  const [depth, setDepth] = useState(1);
  const [maxNodes, setMaxNodes] = useState(60);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);

  const params = new URLSearchParams({ depth: String(depth), max_nodes: String(maxNodes) });
  if (focus) params.set("focus_entity_id", focus);
  const graph = useResource<GraphData>(`${apiBase}/graph?${params.toString()}`);
  const entities = useResource<Page<Entity>>(`${apiBase}/entities?limit=100`);
  const data = graph.data;
  const labels = new Map(data?.nodes.map((node) => [node.id, node.label]) ?? []);

  return (
    <div className="space-y-6">
      <Section
        title="Relationship graph"
        description="A bounded view: the browser never loads the whole case. Select an edge or table row to see its origin and supporting evidence."
      >
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor="graph-focus" className="block text-xs text-muted">
              Focus entity
            </label>
            <select
              id="graph-focus"
              value={focus}
              onChange={(event) => {
                setFocus(event.target.value);
                setSelectedEdge(null);
              }}
              className="max-w-72 rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="">Most connected entities</option>
              {entities.data?.items.map((entity) => (
                <option key={entity.id} value={entity.id}>
                  {entity.display_name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="graph-depth" className="block text-xs text-muted">
              Expansion depth
            </label>
            <select
              id="graph-depth"
              value={depth}
              onChange={(event) => setDepth(Number(event.target.value))}
              disabled={!focus}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value={1}>1 hop</option>
              <option value={2}>2 hops</option>
            </select>
          </div>
          <div>
            <label htmlFor="graph-max-nodes" className="block text-xs text-muted">
              Maximum entities
            </label>
            <select
              id="graph-max-nodes"
              value={maxNodes}
              onChange={(event) => setMaxNodes(Number(event.target.value))}
              className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              {[25, 60, 100, 150].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
        </div>
        {graph.state === "error" ? <ErrorNotice error={graph.error} onRetry={() => void graph.reload()} /> : null}
        {graph.state === "loading" && !data ? <LoadingState label="Loading graph…" /> : null}
        {data && data.nodes.length === 0 ? <EmptyState>No entities to display yet.</EmptyState> : null}
        {data && data.truncated ? (
          <Notice tone="warn">
            Showing {data.nodes.length} of {data.total_entities} entities and {data.edges.length} of {data.total_relationships}{" "}
            relationships. Focus on an entity to explore further.
          </Notice>
        ) : null}
        {data && data.nodes.length > 0 ? (
          <div className="mt-3 space-y-2">
            <GraphCanvas
              graph={data}
              selectedEdgeId={selectedEdge}
              onSelectEdge={setSelectedEdge}
              onSelectNode={(id) => {
                setFocus(id);
                setSelectedEdge(null);
              }}
            />
            <p className="text-xs text-muted">
              Legend: solid line = analyst assertion, dashed line = observed; green = accepted, red = rejected, grey =
              unreviewed. Square nodes are observed entities. Click a node to focus on it.
            </p>
          </div>
        ) : null}
      </Section>

      {selectedEdge ? (
        <RelationshipDetailPanel
          relationshipId={selectedEdge}
          onClose={() => setSelectedEdge(null)}
          onChanged={() => void graph.reload()}
        />
      ) : null}

      {data && data.edges.length > 0 ? (
        <Section title="Edges in this view">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Relationships shown in the graph</caption>
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="py-2 pr-3 font-medium">Source</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Predicate</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Target</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Origin</th>
                  <th scope="col" className="py-2 font-medium">Review</th>
                </tr>
              </thead>
              <tbody>
                {data.edges.map((edge) => (
                  <tr key={edge.id} className={`border-t border-line ${selectedEdge === edge.id ? "bg-canvas" : ""}`}>
                    <td className="py-2 pr-3">
                      <Link href={`${base}/entities/${edge.source}`} className="text-accent hover:underline">
                        {labels.get(edge.source)}
                      </Link>
                    </td>
                    <td className="py-2 pr-3">
                      <button
                        type="button"
                        onClick={() => setSelectedEdge(edge.id)}
                        aria-pressed={selectedEdge === edge.id}
                        className="font-mono text-xs text-accent underline"
                      >
                        {edge.predicate}
                      </button>
                    </td>
                    <td className="py-2 pr-3">
                      <Link href={`${base}/entities/${edge.target}`} className="text-accent hover:underline">
                        {labels.get(edge.target)}
                      </Link>
                    </td>
                    <td className="py-2 pr-3">
                      <OriginBadge origin={edge.origin} />
                    </td>
                    <td className="py-2">{reviewBadge(edge.review_status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-muted">Types in view: {[...new Set(data.nodes.map((node) => humanize(node.entity_type)))].join(", ")}</p>
        </Section>
      ) : null}
    </div>
  );
}
