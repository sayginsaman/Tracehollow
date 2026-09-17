"use client";

import { useEffect, useRef } from "react";

import type { GraphData } from "@/lib/workspace-types";

/** Visual encoding: origin by line style and node shape, review status by colour and label suffix. */
export function edgeLabel(edge: GraphData["edges"][number]): string {
  const review = edge.review_status === "unreviewed" ? "" : ` [${edge.review_status}]`;
  return `${edge.predicate}${review}`;
}

/** Resolves a theme token (OKLCH) to an rgb() string the graph renderer understands. */
function tokenColor(name: string, fallback: string): string {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    if (!value) return fallback;
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    const context = canvas.getContext("2d");
    if (!context) return fallback;
    context.fillStyle = fallback;
    context.fillStyle = value;
    context.fillRect(0, 0, 1, 1);
    const [r, g, b] = context.getImageData(0, 0, 1, 1).data;
    return `rgb(${r}, ${g}, ${b})`;
  } catch {
    return fallback;
  }
}

export function GraphCanvas({
  graph,
  selectedEdgeId,
  selectedNodeId,
  onSelectEdge,
  onSelectNode,
  onFocusNode,
}: {
  graph: GraphData;
  selectedEdgeId: string | null;
  selectedNodeId?: string | null;
  onSelectEdge: (id: string) => void;
  onSelectNode: (id: string) => void;
  onFocusNode?: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<import("cytoscape").Core | null>(null);
  const handlers = useRef({ onSelectEdge, onSelectNode, onFocusNode });
  // Set once the reader zooms or pans, so a later resize keeps their view instead of refitting.
  const adjusted = useRef(false);

  useEffect(() => {
    handlers.current = { onSelectEdge, onSelectNode, onFocusNode };
  }, [onSelectEdge, onSelectNode, onFocusNode]);

  useEffect(() => {
    let disposed = false;
    void import("cytoscape").then(({ default: cytoscape }) => {
      if (disposed || !container.current) return;
      const ink = tokenColor("--color-ink", "rgb(33, 30, 24)");
      const muted = tokenColor("--color-muted", "rgb(95, 90, 83)");
      const accent = tokenColor("--color-accent", "rgb(53, 91, 163)");
      const surface = tokenColor("--color-surface", "rgb(254, 253, 252)");
      const ok = tokenColor("--color-ok", "rgb(44, 109, 62)");
      const bad = tokenColor("--color-bad", "rgb(174, 46, 42)");
      const ai = tokenColor("--color-ai", "rgb(109, 76, 158)");
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
        // Labels count towards node size so layouts keep them apart; the zoom cap stops small
        // graphs from being magnified until labels collide.
        layout:
          graph.nodes.length > 40
            ? { name: "grid", animate: false, fit: true, padding: 40, avoidOverlap: true, nodeDimensionsIncludeLabels: true }
            : {
                name: "cose",
                animate: false,
                fit: true,
                padding: 48,
                randomize: false,
                nodeDimensionsIncludeLabels: true,
                idealEdgeLength: () => 110,
                nodeRepulsion: () => 14000,
                componentSpacing: 140,
                nodeOverlap: 32,
              },
        minZoom: 0.2,
        maxZoom: 1.6,
        wheelSensitivity: 0.3,
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "font-family": "IBM Plex Sans Variable, IBM Plex Sans, system-ui, sans-serif",
              "font-size": 13,
              "min-zoomed-font-size": 7,
              color: ink,
              "text-valign": "bottom",
              "text-margin-y": 6,
              "text-background-color": surface,
              "text-background-opacity": 0.85,
              "text-background-padding": "2px",
              "text-background-shape": "roundrectangle",
              "background-color": accent,
              "border-width": 2,
              "border-color": surface,
              width: 20,
              height: 20,
              // Identifiers carry meaning to the end, so long labels wrap instead of being cut short.
              "text-wrap": "wrap",
              "text-overflow-wrap": "anywhere",
              "text-max-width": "200px",
            },
          },
          { selector: 'node[origin = "observed"]', style: { shape: "round-rectangle", "background-color": muted } },
          { selector: 'node[origin = "ai_suggestion"]', style: { "background-color": ai } },
          { selector: "node:selected", style: { "border-color": accent, "border-width": 4, width: 24, height: 24 } },
          {
            selector: "edge",
            style: {
              label: "data(label)",
              "font-family": "IBM Plex Mono, ui-monospace, monospace",
              "font-size": 10,
              color: muted,
              "text-background-color": surface,
              "text-background-opacity": 0.9,
              "text-background-padding": "2px",
              "text-rotation": "autorotate",
              width: 1.5,
              "curve-style": "bezier",
              "target-arrow-shape": "triangle",
              "line-color": muted,
              "target-arrow-color": muted,
            },
          },
          { selector: 'edge[origin = "observed"]', style: { "line-style": "dashed" } },
          { selector: 'edge[review = "accepted"]', style: { "line-color": ok, "target-arrow-color": ok, width: 2 } },
          { selector: 'edge[review = "rejected"]', style: { "line-color": bad, "target-arrow-color": bad, opacity: 0.6 } },
          { selector: "edge:selected", style: { width: 3.5, "line-color": accent, "target-arrow-color": accent, color: accent } },
        ],
      });
      instance.current.on("tap", "edge", (event) => handlers.current.onSelectEdge(event.target.id()));
      instance.current.on("tap", "node", (event) => handlers.current.onSelectNode(event.target.id()));
      instance.current.on("dbltap", "node", (event) => handlers.current.onFocusNode?.(event.target.id()));
    });
    adjusted.current = false;
    return () => {
      disposed = true;
      instance.current?.destroy();
      instance.current = null;
    };
  }, [graph]);

  // Opening the inspector or resizing the window narrows the canvas; without this the graph keeps
  // its old dimensions and labels near the edge are cut off.
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const takeControl = () => {
      adjusted.current = true;
    };
    const onPointerMove = (event: PointerEvent) => {
      if (event.buttons !== 0) adjusted.current = true;
    };
    element.addEventListener("wheel", takeControl, { passive: true });
    element.addEventListener("pointermove", onPointerMove);
    if (typeof ResizeObserver === "undefined") {
      return () => {
        element.removeEventListener("wheel", takeControl);
        element.removeEventListener("pointermove", onPointerMove);
      };
    }
    const observer = new ResizeObserver(() => {
      const cy = instance.current;
      if (!cy) return;
      cy.resize();
      if (!adjusted.current) cy.fit(undefined, 48);
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      element.removeEventListener("wheel", takeControl);
      element.removeEventListener("pointermove", onPointerMove);
    };
  }, []);

  useEffect(() => {
    const cy = instance.current;
    if (!cy) return;
    cy.elements().unselect();
    if (selectedEdgeId) cy.getElementById(selectedEdgeId).select();
    if (selectedNodeId) cy.getElementById(selectedNodeId).select();
  }, [selectedEdgeId, selectedNodeId]);

  return (
    <div
      ref={container}
      role="img"
      aria-label={`Relationship graph with ${graph.nodes.length} entities and ${graph.edges.length} relationships. The table of edges below offers the same information for keyboard and screen reader use.`}
      className="h-[26rem] w-full bg-sunken/60 sm:h-[34rem] xl:h-[40rem]"
    />
  );
}
