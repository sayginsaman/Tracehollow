"use client";

import { CircleAlert, CircleCheck, CircleX, Info, RotateCw, Sparkles, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { describeError } from "@/lib/messages";

import { Button } from "./button";
import { cn } from "./cn";

type NoticeTone = "neutral" | "ok" | "warn" | "bad" | "ai";

const NOTICE_CLASSES: Record<NoticeTone, string> = {
  neutral: "border-line bg-sunken text-ink",
  ok: "border-ok-line bg-ok-soft text-ok",
  warn: "border-warn-line bg-warn-soft text-warn",
  bad: "border-bad-line bg-bad-soft text-bad",
  ai: "border-ai-line bg-ai-soft text-ai",
};

const NOTICE_ICONS: Record<NoticeTone, LucideIcon> = {
  neutral: Info,
  ok: CircleCheck,
  warn: CircleAlert,
  bad: CircleX,
  ai: Sparkles,
};

/**
 * An inline message. Static explanations are plain text; set `live` for messages that appear in
 * response to an action so assistive technology announces them.
 */
export function Notice({
  tone = "neutral",
  title,
  children,
  live = false,
  icon,
  className,
  actions,
}: {
  tone?: NoticeTone;
  title?: ReactNode;
  children?: ReactNode;
  live?: boolean;
  icon?: LucideIcon;
  className?: string;
  actions?: ReactNode;
}) {
  const Icon = icon ?? NOTICE_ICONS[tone];
  return (
    <div role={live ? "status" : undefined} className={cn("flex gap-2.5 rounded-md border px-3 py-2.5 text-sm", NOTICE_CLASSES[tone], className)}>
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1 break-words">
        {title ? <p className="font-medium">{title}</p> : null}
        {children ? <div className={cn(title ? "mt-0.5" : "", tone === "neutral" ? "text-muted" : "")}>{children}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-start gap-2">{actions}</div> : null}
    </div>
  );
}

export function ErrorNotice({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={cn("flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-bad-line bg-bad-soft px-3 py-2.5 text-sm text-bad", className)}>
      <CircleX aria-hidden="true" className="size-4 shrink-0" />
      <span className="min-w-0 flex-1">{describeError(error)}</span>
      {onRetry ? (
        <Button size="sm" icon={RotateCw} onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

/** An inline error message for a failed action. */
export function ActionError({ message, className }: { message: string | null | undefined; className?: string }) {
  if (!message) return null;
  return (
    <p role="alert" className={cn("flex items-start gap-2 rounded-md border border-bad-line bg-bad-soft px-3 py-2 text-sm text-bad", className)}>
      <CircleX aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0 break-words">{message}</span>
    </p>
  );
}

/** Says what belongs here and, when there is one, the action that creates it. */
export function EmptyState({
  title,
  children,
  action,
  icon: Icon,
  compact = false,
  className,
}: {
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  icon?: LucideIcon;
  compact?: boolean;
  className?: string;
}) {
  if (compact) {
    return <p className={cn("py-1 text-sm text-muted", className)}>{children ?? title}</p>;
  }
  return (
    <div className={cn("flex flex-col items-start gap-2 rounded-md bg-sunken px-4 py-5 text-sm", className)}>
      {Icon ? <Icon aria-hidden="true" className="size-5 text-muted" /> : null}
      {title ? <p className="font-medium text-ink">{title}</p> : null}
      {children ? <div className="max-w-[72ch] text-muted">{children}</div> : null}
      {action ? <div className="mt-1 flex flex-wrap gap-2">{action}</div> : null}
    </div>
  );
}

/** Skeleton rows shaped like the content they replace, announced once. */
export function LoadingState({ label = "Loading…", rows = 3, className }: { label?: string; rows?: number; className?: string }) {
  const widths = ["w-11/12", "w-4/5", "w-2/3", "w-3/4", "w-1/2"];
  return (
    <div role="status" className={cn("space-y-2.5 py-1", className)}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} aria-hidden="true" className={cn("h-4 animate-pulse-soft rounded bg-sunken", widths[index % widths.length])} />
      ))}
    </div>
  );
}
