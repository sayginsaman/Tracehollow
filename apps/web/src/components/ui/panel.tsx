"use client";

import { ChevronRight } from "lucide-react";
import { useId, type ReactNode } from "react";

import { cn } from "./cn";

/** The single page heading with its description, metadata and page-level actions. */
export function PageHeader({
  title,
  description,
  meta,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("flex flex-wrap items-start justify-between gap-x-6 gap-y-3", className)}>
      <div className="min-w-0 flex-1 basis-80">
        <h1 className="text-title font-semibold break-words text-ink">{title}</h1>
        {meta ? <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">{meta}</div> : null}
        {description ? <div className="mt-1.5 max-w-[72ch] text-sm text-muted">{description}</div> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

/** A titled section of a page: surface, hairline border, header row. Never nested. */
export function Panel({
  title,
  description,
  actions,
  children,
  id,
  flush = false,
  className,
  headingLevel = 2,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  id?: string;
  /** No inner padding, for tables and lists that run edge to edge. */
  flush?: boolean;
  className?: string;
  headingLevel?: 2 | 3;
}) {
  const generated = useId();
  const headingId = id ?? `panel-${generated}`;
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section aria-labelledby={headingId} className={cn("min-w-0 rounded-lg border border-line bg-surface", className)}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1 basis-64">
          <Heading id={headingId} className="text-heading font-semibold text-ink">
            {title}
          </Heading>
          {description ? <div className="mt-0.5 max-w-[72ch] text-sm text-muted">{description}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children !== undefined ? <div className={flush ? "" : "p-4"}>{children}</div> : null}
    </section>
  );
}

/** A heading for a group inside a panel. */
export function SubHeading({ children, className, id }: { children: ReactNode; className?: string; id?: string }) {
  return (
    <h3 id={id} className={cn("text-sm font-semibold text-ink", className)}>
      {children}
    </h3>
  );
}

/** Progressive disclosure for secondary detail. The summary says what is inside. */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  className,
}: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  return (
    <details open={defaultOpen} className={cn("group rounded-md border border-line", className)}>
      <summary className="flex min-h-9 items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-ink select-none hover:bg-sunken">
        <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted transition-transform group-open:rotate-90" />
        <span className="min-w-0">{summary}</span>
      </summary>
      <div className="border-t border-line px-3 py-3">{children}</div>
    </details>
  );
}

/** Label and value pairs. Values wrap; long identifiers should use LongValue. */
export function KeyValue({
  items,
  className,
  compact = false,
}: {
  items: [string, ReactNode][];
  className?: string;
  compact?: boolean;
}) {
  return (
    <dl
      className={cn(
        "grid grid-cols-1 gap-x-6 text-sm sm:grid-cols-[minmax(7rem,max-content)_minmax(0,1fr)]",
        compact ? "gap-y-1.5" : "gap-y-2.5",
        className,
      )}
    >
      {items.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-muted">{key}</dt>
          <dd className="min-w-0 break-words text-ink max-sm:mb-1.5">{value ?? "Not recorded"}</dd>
        </div>
      ))}
    </dl>
  );
}
