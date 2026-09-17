"use client";

import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from "react";

import { cn } from "./cn";

/**
 * A data table in its own horizontal scroll container, so wide content never scrolls the page.
 * The caption is required for screen readers and may be visually hidden.
 */
export function DataTable({
  caption,
  captionVisible = false,
  children,
  className,
  minWidth,
}: {
  caption: string;
  captionVisible?: boolean;
  children: ReactNode;
  className?: string;
  /** Minimum table width before the container scrolls, for example "44rem". */
  minWidth?: string;
}) {
  return (
    // relative: visually hidden (absolutely positioned) cell content must stay inside the scroll container.
    <div className={cn("relative w-full overflow-x-auto", className)}>
      <table className="w-full border-collapse text-left text-sm" style={minWidth ? { minWidth } : undefined}>
        <caption className={captionVisible ? "px-4 py-2 text-left text-xs text-muted" : "sr-only"}>{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function Th({ className, children, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      scope="col"
      {...props}
      className={cn("border-b border-line bg-sunken px-4 py-2 text-xs font-medium whitespace-nowrap text-muted first:pl-4", className)}
    >
      {children}
    </th>
  );
}

export function Td({ className, children, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td {...props} className={cn("border-b border-line px-4 py-2.5 align-top", className)}>
      {children}
    </td>
  );
}

export function Tr({
  className,
  selected = false,
  children,
  ...props
}: HTMLAttributes<HTMLTableRowElement> & { selected?: boolean }) {
  return (
    <tr
      {...props}
      aria-selected={selected || undefined}
      className={cn("transition-colors last:[&>td]:border-b-0 hover:bg-sunken/60", selected && "bg-accent-soft hover:bg-accent-soft", className)}
    >
      {children}
    </tr>
  );
}

/** A filter and search row above a list or table. */
export function Toolbar({ children, className, label }: { children: ReactNode; className?: string; label?: string }) {
  return (
    <div role={label ? "group" : undefined} aria-label={label} className={cn("flex flex-wrap items-end gap-3", className)}>
      {children}
    </div>
  );
}
