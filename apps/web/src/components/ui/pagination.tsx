"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "./button";
import { cn } from "./cn";

export function Pagination({
  total,
  limit,
  offset,
  onChange,
  className,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
  className?: string;
}) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  const first = offset + 1;
  const last = Math.min(offset + limit, total);
  return (
    <nav aria-label="Pagination" className={cn("flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm", className)}>
      <span className="text-muted tabular-nums">
        {first} to {last} of {total} · page {page} of {pages}
      </span>
      <span className="flex gap-2">
        <Button size="sm" icon={ChevronLeft} disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button size="sm" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          Next
          <ChevronRight aria-hidden="true" className="size-4" />
        </Button>
      </span>
    </nav>
  );
}
