"use client";

import { Crosshair, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";

import { useResource } from "@/lib/session-context";
import type { Entity, GraphData, Page } from "@/lib/workspace-types";

import {
  Button,
  DataTable,
  ErrorNotice,
  IconButton,
  KeyValue,
  LoadingState,
  Notice,
  OriginBadge,
  PageHeader,
  Panel,
  ReviewBadge,
  Select,
  Td,
  Th,
  Toolbar,
  Tr,
  humanize,
  plural,
} from "../ui";
import { useCase } from "./CaseContext";
import { GraphCanvas } from "./GraphCanvas";
import { RelationshipDetailPanel } from "./RelationshipDetailPanel";

function Legend() {
  return (
    <ul aria-label="Graph legend" className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted">
      <li className="flex items-center gap-2">
        <span aria-hidden="true" className="size-3 rounded-full bg-accent" /> Analyst entity
      </li>
      <li className="flex items-center gap-2">
        <span aria-hidden="true" className="size-3 rounded-sm bg-muted" /> Observed entity
      </li>
      <li className="flex items-center gap-2">
        <span aria-hidden="true" className="h-0 w-6 border-t-2 border-muted" /> Analyst assertion
      </li>
      <li className="flex items-center gap-2">
        <span aria-hidden="true" className="h-0 w-6 border-t-2 border-dashed border-muted" /> Observed link
      </li>
      <li className="flex items-center gap-2">
        <span aria-hidden="true" className="h-0 w-6 border-t-2 border-ok" /> Accepted
      </li>
      <li className="flex items-center gap-2">
        <span aria-hidden="true" className="h-0 w-6 border-t-2 border-bad" /> Rejected
      </li>
    </ul>
  );
}

export function GraphView() {
  const { apiBase, base } = useCase();
  const [focus, setFocus] = useState("");
  const [depth, setDepth] = useState(1);
  const [maxNodes, setMaxNodes] = useState(60);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const params = new URLSearchParams({ depth: String(depth), max_nodes: String(maxNodes) });
  if (focus) params.set("focus_entity_id", focus);
  const graph = useResource<GraphData>(`${apiBase}/graph?${params.toString()}`);
  const entities = useResource<Page<Entity>>(`${apiBase}/entities?limit=100`);
  const data = graph.data;
  const labels = new Map(data?.nodes.map((node) => [node.id, node.label]) ?? []);
  const node = data?.nodes.find((item) => item.id === selectedNode) ?? null;
  const degree = node && data ? data.edges.filter((edge) => edge.source === node.id || edge.target === node.id).length : 0;

  const selectEdge = useCallback((id: string) => {
    setSelectedNode(null);
    setSelectedEdge(id);
  }, []);
  const selectNode = useCallback((id: string) => {
    setSelectedEdge(null);
    setSelectedNode(id);
  }, []);
  const focusOn = useCallback((id: string) => {
    setFocus(id);
    setSelectedEdge(null);
    setSelectedNode(null);
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Relationship graph"
        description="A bounded view: the browser never loads the whole case. Select a node or edge to inspect it; double-click a node to center the graph on it. The table below lists the same edges for keyboard use."
      />

      <div className={selectedEdge || node ? "grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_24rem]" : ""}>
        <Panel title="Graph" flush>
          <div className="border-b border-line px-4 py-3">
            <Toolbar>
              <div className="min-w-0 flex-1 basis-56">
                <label htmlFor="graph-focus" className="mb-1.5 block text-xs font-medium text-muted">
                  Focus entity
                </label>
                <Select id="graph-focus" value={focus} onChange={(event) => focusOn(event.target.value)}>
                  <option value="">Most connected entities</option>
                  {entities.data?.items.map((entity) => (
                    <option key={entity.id} value={entity.id}>
                      {entity.display_name}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="w-32">
                <label htmlFor="graph-depth" className="mb-1.5 block text-xs font-medium text-muted">
                  Expansion depth
                </label>
                <Select id="graph-depth" value={depth} onChange={(event) => setDepth(Number(event.target.value))} disabled={!focus}>
                  <option value={1}>1 hop</option>
                  <option value={2}>2 hops</option>
                </Select>
              </div>
              <div className="w-32">
                <label htmlFor="graph-max-nodes" className="mb-1.5 block text-xs font-medium text-muted">
                  Maximum entities
                </label>
                <Select id="graph-max-nodes" value={maxNodes} onChange={(event) => setMaxNodes(Number(event.target.value))}>
                  {[25, 60, 100, 150].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </div>
              {focus ? (
                <Button variant="ghost" icon={X} onClick={() => focusOn("")}>
                  Clear focus
                </Button>
              ) : null}
            </Toolbar>
          </div>
          {graph.state === "error" ? (
            <div className="p-4">
              <ErrorNotice error={graph.error} onRetry={() => void graph.reload()} />
            </div>
          ) : null}
          {graph.state === "loading" && !data ? <LoadingState label="Loading graph…" rows={6} className="p-4" /> : null}
          {data && data.nodes.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted">No entities to display yet. Entities and relationships from runs, imports and analysts appear here.</p>
          ) : null}
          {data && data.truncated ? (
            <div className="border-b border-line p-4">
              <Notice tone="warn">
                Showing {data.nodes.length} of {data.total_entities} entities and {data.edges.length} of {data.total_relationships}{" "}
                relationships. Focus on an entity to explore further.
              </Notice>
            </div>
          ) : null}
          {data && data.nodes.length > 0 ? (
            <>
              <GraphCanvas
                graph={data}
                selectedEdgeId={selectedEdge}
                selectedNodeId={selectedNode}
                onSelectEdge={selectEdge}
                onSelectNode={selectNode}
                onFocusNode={focusOn}
              />
              <div className="border-t border-line px-4 py-3">
                <Legend />
              </div>
            </>
          ) : null}
        </Panel>

        {selectedEdge ? (
          <div className="xl:sticky xl:top-20">
            <RelationshipDetailPanel relationshipId={selectedEdge} onClose={() => setSelectedEdge(null)} onChanged={() => void graph.reload()} />
          </div>
        ) : null}
        {node ? (
          <aside aria-label="Entity details" className="min-w-0 rounded-lg border border-line bg-surface xl:sticky xl:top-20">
            <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
              <h2 className="text-heading font-semibold break-words text-ink">{node.label}</h2>
              <IconButton icon={X} label="Close entity details" onClick={() => setSelectedNode(null)} />
            </div>
            <div className="space-y-4 p-4 text-sm">
              <KeyValue
                compact
                items={[
                  ["Type", humanize(node.entity_type)],
                  ["Origin", <OriginBadge key="origin" origin={node.origin} />],
                  ["In this view", plural(degree, "relationship")],
                ]}
              />
              <div className="flex flex-wrap gap-2">
                <Button icon={Crosshair} onClick={() => focusOn(node.id)}>
                  Center graph here
                </Button>
                <Link href={`${base}/entities/${node.id}`} className="inline-flex h-9 items-center rounded-md px-2 text-sm text-accent hover:underline">
                  Open entity
                </Link>
              </div>
            </div>
          </aside>
        ) : null}
      </div>

      {data && data.edges.length > 0 ? (
        <Panel title="Edges in this view" description={`Types in view: ${[...new Set(data.nodes.map((item) => humanize(item.entity_type)))].join(", ")}.`} flush>
          <DataTable caption="Relationships shown in the graph" minWidth="44rem">
            <thead>
              <tr>
                <Th>Source</Th>
                <Th>Relationship</Th>
                <Th>Target</Th>
                <Th>Origin</Th>
                <Th>Review</Th>
              </tr>
            </thead>
            <tbody>
              {data.edges.map((edge) => (
                <Tr key={edge.id} selected={selectedEdge === edge.id}>
                  <Td>
                    <Link href={`${base}/entities/${edge.source}`} className="break-words text-accent hover:underline">
                      {labels.get(edge.source)}
                    </Link>
                  </Td>
                  <Td>
                    <button
                      type="button"
                      onClick={() => selectEdge(edge.id)}
                      aria-pressed={selectedEdge === edge.id}
                      className="rounded border border-line bg-sunken px-1.5 py-0.5 font-mono text-code text-ink hover:border-accent hover:text-accent"
                    >
                      {edge.predicate}
                    </button>
                  </Td>
                  <Td>
                    <Link href={`${base}/entities/${edge.target}`} className="break-words text-accent hover:underline">
                      {labels.get(edge.target)}
                    </Link>
                  </Td>
                  <Td>
                    <OriginBadge origin={edge.origin} />
                  </Td>
                  <Td>
                    <ReviewBadge status={edge.review_status} />
                  </Td>
                </Tr>
              ))}
            </tbody>
          </DataTable>
        </Panel>
      ) : null}
    </div>
  );
}
