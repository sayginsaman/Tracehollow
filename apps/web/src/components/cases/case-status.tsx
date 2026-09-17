"use client";

import { Archive, CircleCheck, CircleX, FlaskConical, LoaderCircle } from "lucide-react";

import { StatusBadge, Tag } from "../ui";

export function CaseStatusBadge({ status }: { status: string }) {
  switch (status) {
    case "active":
      return <StatusBadge tone="ok" icon={CircleCheck} label="Active" />;
    case "archived":
      return <StatusBadge tone="neutral" icon={Archive} label="Archived" />;
    case "deleting":
      return <StatusBadge tone="warn" icon={LoaderCircle} label="Deleting" />;
    case "deletion_failed":
      return <StatusBadge tone="bad" icon={CircleX} label="Deletion failed" />;
    default:
      return <StatusBadge tone="neutral" label={status} />;
  }
}

/** The tag scripts/seed_demo_workspace.py puts on synthetic demonstration cases. */
export const SYNTHETIC_DEMO_TAG = "synthetic-demo";

export function CaseTags({ tags, limit }: { tags: string[]; limit?: number }) {
  const shown = limit === undefined ? tags : tags.slice(0, limit);
  return (
    <>
      {shown.map((tag) =>
        tag === SYNTHETIC_DEMO_TAG ? (
          <StatusBadge key={tag} tone="warn" icon={FlaskConical} label="Synthetic demo" title="Synthetic demonstration data, not a real investigation" />
        ) : (
          <Tag key={tag}>{tag}</Tag>
        ),
      )}
      {limit !== undefined && tags.length > limit ? <span className="text-xs text-muted">+{tags.length - limit}</span> : null}
    </>
  );
}

export function parseTags(raw: string): string[] {
  return raw
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}
