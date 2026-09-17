"use client";

import { useRef, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "./cn";

export interface TabItem<T extends string> {
  value: T;
  label: ReactNode;
  count?: number;
  /** Accessible name when the visible label is not plain text, for example "Local time only (3)". */
  ariaLabel?: string;
}

/**
 * Tabs that switch views within one page. Arrow keys move between tabs (automatic activation);
 * the panel is rendered by the caller with `tabPanelProps`.
 */
export function Tabs<T extends string>({
  label,
  items,
  value,
  onChange,
  idPrefix,
  className,
}: {
  label: string;
  items: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  idPrefix: string;
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = items.length - 1;
    const next =
      event.key === "ArrowRight" ? (index === last ? 0 : index + 1)
      : event.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : null;
    const target = next === null ? undefined : items[next];
    if (next === null || !target) return;
    event.preventDefault();
    onChange(target.value);
    refs.current[next]?.focus();
  }

  return (
    <div className={cn("relative overflow-x-auto", className)}>
      <div role="tablist" aria-label={label} className="flex min-w-max gap-1 border-b border-line">
        {items.map((item, index) => {
          const selected = item.value === value;
          return (
            <button
              key={item.value}
              ref={(element) => {
                refs.current[index] = element;
              }}
              type="button"
              role="tab"
              id={`${idPrefix}-tab-${item.value}`}
              aria-selected={selected}
              aria-label={item.ariaLabel}
              aria-controls={`${idPrefix}-panel`}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(item.value)}
              onKeyDown={(event) => onKeyDown(event, index)}
              className={cn(
                "-mb-px inline-flex h-10 items-center gap-2 border-b-2 px-3 text-sm whitespace-nowrap transition-colors",
                selected ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:border-line-strong/50 hover:text-ink",
              )}
            >
              {item.label}
              {item.count !== undefined ? (
                <span className={cn("rounded px-1.5 text-xs tabular-nums", selected ? "bg-accent-soft text-accent-strong" : "bg-sunken text-muted")}>
                  {item.count}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function tabPanelProps(idPrefix: string, value: string) {
  return { role: "tabpanel", id: `${idPrefix}-panel`, "aria-labelledby": `${idPrefix}-tab-${value}`, tabIndex: 0 } as const;
}

/** Mutually exclusive filter buttons (aria-pressed), for list filters rather than views. */
export function SegmentedFilter<T extends string>({
  label,
  options,
  value,
  onChange,
  className,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div role="group" aria-label={label} className={cn("inline-flex flex-wrap rounded-md border border-line bg-sunken p-0.5", className)}>
      {options.map((option) => {
        const pressed = option.value === value;
        return (
          <button
            key={option.label}
            type="button"
            aria-pressed={pressed}
            onClick={() => onChange(option.value)}
            className={cn(
              "h-8 rounded px-3 text-sm whitespace-nowrap transition-colors",
              pressed ? "bg-surface font-medium text-ink shadow-[0_0_0_1px_var(--color-line)]" : "text-muted hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
