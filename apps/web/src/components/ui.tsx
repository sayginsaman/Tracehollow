"use client";

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

import { describeError } from "@/lib/messages";

import { StatusBadge, type Tone } from "./StatusBadge";

export function Section({
  title,
  description,
  actions,
  children,
  id,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  const headingId = id ?? `section-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section aria-labelledby={headingId} className="rounded-lg border border-line bg-surface">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <h2 id={headingId} className="font-semibold">
            {title}
          </h2>
          {description ? <div className="mt-0.5 text-sm text-muted">{description}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </div>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <p role="status" className="py-2 text-sm text-muted">
      {label}
    </p>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="py-2 text-sm text-muted">{children}</p>;
}

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-sm text-bad">
      <span>{describeError(error)}</span>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="rounded border border-bad/40 px-2 py-0.5 text-xs font-medium">
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function Notice({ tone = "neutral", children }: { tone?: "ok" | "warn" | "neutral"; children: ReactNode }) {
  const classes = {
    ok: "border-ok/30 bg-ok-bg text-ok",
    warn: "border-warn/30 bg-warn-bg text-warn",
    neutral: "border-line bg-canvas text-muted",
  }[tone];
  return (
    <div role="status" className={`rounded-md border px-3 py-2 text-sm ${classes}`}>
      {children}
    </div>
  );
}

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const styles = {
    primary: "bg-accent text-white hover:bg-accent-strong dark:text-canvas",
    secondary: "border border-line bg-surface hover:bg-canvas",
    danger: "bg-bad text-white hover:opacity-90 dark:text-canvas",
  }[variant];
  return (
    <button
      type="button"
      {...props}
      aria-busy={props["aria-busy"]}
      className={`inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60 ${styles} ${className}`}
    />
  );
}

const fieldClasses =
  "mt-1 block w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-muted disabled:opacity-60";

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor: string }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="block text-sm font-medium">
        {label}
      </label>
      {children}
      {hint ? (
        <p id={`${htmlFor}-hint`} className="mt-1 text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${fieldClasses} ${props.className ?? ""}`} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${fieldClasses} ${props.className ?? ""}`} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${fieldClasses} ${props.className ?? ""}`} />;
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <nav aria-label="Pagination" className="mt-3 flex items-center justify-between text-sm">
      <span className="text-muted">
        Page {page} of {pages} · {total} total
      </span>
      <span className="flex gap-2">
        <Button disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          Next
        </Button>
      </span>
    </nav>
  );
}

const RUN_TONES: Record<string, Tone> = {
  queued: "neutral",
  running: "neutral",
  completed: "ok",
  partial: "warn",
  failed: "bad",
  canceled: "warn",
};

const RUN_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
  canceled: "Canceled",
};

export function RunStatusBadge({ status }: { status: string }) {
  return <StatusBadge tone={RUN_TONES[status] ?? "neutral"} label={RUN_LABELS[status] ?? status} />;
}

const OUTCOME_TONES: Record<string, Tone> = {
  findings: "ok",
  no_findings: "ok",
  partial: "warn",
  canceled: "warn",
  rate_limited: "warn",
};

export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <StatusBadge tone="neutral" label="Pending" />;
  return <StatusBadge tone={OUTCOME_TONES[outcome] ?? "bad"} label={humanize(outcome)} />;
}

export function OriginBadge({ origin }: { origin: string }) {
  const label = {
    observed: "Observed",
    analyst_assertion: "Analyst assertion",
    deterministic_derivation: "Derived",
    ai_suggestion: "AI suggestion",
  }[origin];
  return (
    <span className="inline-flex items-center rounded border border-line px-1.5 py-0.5 text-xs text-muted">
      {label ?? origin}
    </span>
  );
}

export function SyntheticBadge() {
  return (
    <span
      title="Generated by the synthetic fixture connector; not collected from any real source."
      className="inline-flex items-center gap-1 rounded border border-warn/40 bg-warn-bg px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wide text-warn"
    >
      Synthetic
    </span>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
      {items.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="font-medium text-muted">{key}</dt>
          <dd className="min-w-0 break-words">{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="break-all font-mono text-xs">{children}</span>;
}

export function humanize(value: string): string {
  return value.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}
