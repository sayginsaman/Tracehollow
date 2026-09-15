"use client";

import { useEffect, useRef } from "react";

import type { GraphData } from "@/lib/workspace-types";

/** Visual encoding: origin by line style, review status by colour and label suffix. */
export function edgeLabel(edge: GraphData["edges"][number]): string {
  const review = edge.review_status === "unreviewed" ? "" : ` [${edge.review_status}]`;
  return `${edge.predicate}${review}`;
}

export function GraphCanvas({
  graph,
  selectedEdgeId,
  onSelectEdge,
  onSelectNode,
}: {
  graph: GraphData;
  selectedEdgeId: string | null;
  onSelectEdge: (id: string) => void;
  onSelectNode: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<import("cytoscape").Core | null>(null);
  const handlers = useRef({ onSelectEdge, onSelectNode });

  useEffect(() => {
    handlers.current = { onSelectEdge, onSelectNode };
  }, [onSelectEdge, onSelectNode]);

  useEffect(() => {
    let disposed = false;
    void import("cytoscape").then(({ default: cytoscape }) => {
      if (disposed || !container.current) return;
      const styles = getComputedStyle(document.documentElement);
      const color = (name: string, fallback: string) => styles.getPropertyValue(name).trim() || fallback;
      const ink = color("--color-ink", "#1c1d1f");
      const muted = color("--color-muted", "#5b5e63");
      const accent = color("--color-accent", "#1f5f8b");
      instance.current?.destroy();
      instance.current = cytoscape({
        container: container.current,
        elements: [
          ...graph.nodes.map((node) => ({
            data: { id: node.id, label: node.label, type: node.entity_type, origin: node.origin },
          })),
          ...graph.edges.map((edge) => ({
            data: {
              id: edge.id,
              source: edge.source,
              target: edge.target,
              label: edgeLabel(edge),
              origin: edge.origin,
              review: edge.review_status,
            },
          })),
        ],
        layout: { name: graph.nodes.length > 40 ? "grid" : "cose", animate: false, fit: true, padding: 24 },
        wheelSensitivity: 0.3,
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "font-size": 10,
              color: ink,
              "text-valign": "bottom",
              "text-margin-y": 4,
              "background-color": accent,
              width: 18,
              height: 18,
              "text-wrap": "ellipsis",
              "text-max-width": "120px",
            },
          },
          { selector: 'node[origin = "observed"]', style: { shape: "round-rectangle", "background-color": muted } },
          {
            selector: "edge",
            style: {
              label: "data(label)",
              "font-size": 8,
              color: muted,
              width: 2,
              "curve-style": "bezier",
              "target-arrow-shape": "triangle",
              "line-color": muted,
              "target-arrow-color": muted,
            },
          },
          { selector: 'edge[origin = "observed"]', style: { "line-style": "dashed" } },
          { selector: 'edge[review = "accepted"]', style: { "line-color": "#1d6b3f", "target-arrow-color": "#1d6b3f" } },
          { selector: 'edge[review = "rejected"]', style: { "line-color": "#a3272b", "target-arrow-color": "#a3272b", opacity: 0.6 } },
          { selector: "edge:selected", style: { width: 4, "line-color": accent, "target-arrow-color": accent } },
        ],
      });
      instance.current.on("tap", "edge", (event) => handlers.current.onSelectEdge(event.target.id()));
      instance.current.on("tap", "node", (event) => handlers.current.onSelectNode(event.target.id()));
    });
    return () => {
      disposed = true;
      instance.current?.destroy();
      instance.current = null;
    };
  }, [graph]);

  useEffect(() => {
    const cy = instance.current;
    if (!cy) return;
    cy.elements().unselect();
    if (selectedEdgeId) cy.getElementById(selectedEdgeId).select();
  }, [selectedEdgeId]);

  return (
    <div
      ref={container}
      role="img"
      aria-label={`Relationship graph with ${graph.nodes.length} entities and ${graph.edges.length} relationships. Use the table below for keyboard access.`}
      className="h-[28rem] w-full rounded-md border border-line bg-canvas"
    />
  );
}
