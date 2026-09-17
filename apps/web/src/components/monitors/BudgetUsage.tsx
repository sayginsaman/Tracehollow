"use client";

import type { BudgetUsage } from "@/lib/workspace-types";
import { describeUsage, usageFraction } from "@/lib/monitoring";

import { StatusBadge, cn } from "../ui";

/** Current-period budget use with a bar for scanning and the numbers in words. */
export function BudgetUsageList({ usage, compact = false }: { usage: BudgetUsage[]; compact?: boolean }) {
  if (usage.length === 0) return <p className="text-sm text-muted">No budget applies.</p>;
  return (
    <ul className={cn(compact ? "space-y-2" : "space-y-3")}>
      {usage.map((item) => {
        const fraction = usageFraction(item);
        return (
          <li key={`${item.scope_type}-${item.metric}-${item.period}`} className="min-w-0">
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-sm">
              <span className="min-w-0 break-words text-ink">{describeUsage(item)}</span>
              {item.exhausted ? <StatusBadge tone="warn" label="Used up" /> : null}
            </div>
            <div aria-hidden="true" className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-sunken">
              <div
                className={cn("h-full rounded-full", item.exhausted ? "bg-warn" : "bg-accent")}
                style={{ width: `${Math.round(fraction * 100)}%` }}
              />
            </div>
            {!compact && item.denied_requests ? (
              <p className="mt-1 text-xs text-muted">{item.denied_requests} request(s) refused this period because the budget was used up.</p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
