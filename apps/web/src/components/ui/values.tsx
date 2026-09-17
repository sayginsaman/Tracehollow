"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { formatUtc } from "@/lib/messages";

import { Button } from "./button";
import { cn } from "./cn";

/** A value read character by character: identifiers, hashes, raw values. */
export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-code break-all", className)}>{children}</span>;
}

/** Copies a value to the clipboard and confirms it in place. */
export function CopyButton({ value, label, className }: { value: string; label: string; className?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 1800);
    return () => window.clearTimeout(timer);
  }, [state]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setState("copied");
    } catch {
      setState("failed");
    }
  }

  const text = state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : label;
  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={text}
      title={text}
      className={cn(
        "inline-flex size-7 shrink-0 items-center justify-center rounded text-muted transition-colors hover:bg-sunken hover:text-ink",
        state === "copied" && "text-ok",
        className,
      )}
    >
      {state === "copied" ? <Check aria-hidden="true" className="size-3.5" /> : <Copy aria-hidden="true" className="size-3.5" />}
      <span aria-live="polite" className="sr-only">
        {state === "idle" ? "" : text}
      </span>
    </button>
  );
}

/**
 * A long value (URL, hash, filename). `truncate` keeps it on one line with the full value in the
 * tooltip; otherwise it wraps anywhere. Both offer copy.
 */
export function LongValue({
  value,
  mono = true,
  truncate = false,
  copyLabel,
  className,
}: {
  value: string;
  mono?: boolean;
  truncate?: boolean;
  copyLabel?: string;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex max-w-full min-w-0 items-start gap-1 align-top", className)}>
      <span
        title={truncate ? value : undefined}
        className={cn("min-w-0", mono && "font-mono text-code", truncate ? "truncate" : "break-all")}
      >
        {value}
      </span>
      {copyLabel ? <CopyButton value={value} label={copyLabel} className="-my-1" /> : null}
    </span>
  );
}

/** A UTC timestamp with its machine-readable value. */
export function Timestamp({ value, fallback = "Not recorded", className }: { value: string | null | undefined; fallback?: string; className?: string }) {
  if (!value) return <span className={cn("text-muted", className)}>{fallback}</span>;
  return (
    <time dateTime={value} className={cn("tabular-nums", className)}>
      {formatUtc(value)}
    </time>
  );
}

export function humanize(value: string): string {
  return value.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

/** "1 message", "3 messages". */
export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

/** Shows a secondary button that reveals a confirmation step before a destructive action. */
export function ConfirmAction({
  label,
  confirmLabel,
  message,
  onConfirm,
  busy = false,
  disabled = false,
}: {
  label: string;
  confirmLabel: string;
  message: ReactNode;
  onConfirm: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <Button size="sm" variant="danger-ghost" onClick={() => setAsking(true)} disabled={disabled}>
        {label}
      </Button>
    );
  }
  return (
    <span role="group" aria-label={label} className="inline-flex flex-wrap items-center gap-2 rounded-md border border-bad-line bg-bad-soft px-2 py-1 text-sm text-bad">
      <span>{message}</span>
      <Button size="sm" variant="danger" onClick={onConfirm} busy={busy} disabled={busy}>
        {confirmLabel}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setAsking(false)} disabled={busy}>
        Keep
      </Button>
    </span>
  );
}
