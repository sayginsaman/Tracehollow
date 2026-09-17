"use client";

import {
  ArrowDownToLine,
  Ban,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleX,
  Clock,
  Eye,
  FileText,
  FlaskConical,
  Gauge,
  GitCompareArrows,
  Globe,
  LockKeyhole,
  Minus,
  Paperclip,
  Pause,
  PlusCircle,
  Power,
  Radar,
  PenLine,
  ScanText,
  Sparkles,
  Upload,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "./cn";

export type Tone = "ok" | "warn" | "bad" | "neutral" | "ai";

const TONE_CLASSES: Record<Tone, string> = {
  ok: "border-ok-line bg-ok-soft text-ok",
  warn: "border-warn-line bg-warn-soft text-warn",
  bad: "border-bad-line bg-bad-soft text-bad",
  neutral: "border-line bg-sunken text-muted",
  ai: "border-ai-line bg-ai-soft text-ai",
};

const TONE_ICONS: Record<Tone, LucideIcon> = {
  ok: CircleCheck,
  warn: CircleAlert,
  bad: CircleX,
  neutral: CircleDashed,
  ai: Sparkles,
};

/** A status label: color, icon and words together, so status never depends on color alone. */
export function StatusBadge({
  tone,
  label,
  icon,
  title,
  className,
}: {
  tone: Tone;
  label: string;
  icon?: LucideIcon;
  title?: string;
  className?: string;
}) {
  const Icon = icon ?? TONE_ICONS[tone];
  return (
    <span
      title={title}
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded border px-1.5 py-px text-xs leading-4 font-medium whitespace-nowrap",
        TONE_CLASSES[tone],
        className,
      )}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {label}
    </span>
  );
}

/** A quiet outlined label for categories (tags, access methods, kinds). */
export function Tag({ children, icon: Icon, title, className }: { children: ReactNode; icon?: LucideIcon; title?: string; className?: string }) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded border border-line bg-surface px-1.5 py-px text-xs leading-4 text-muted",
        className,
      )}
    >
      {Icon ? <Icon aria-hidden="true" className="size-3.5 shrink-0" /> : null}
      <span className="truncate">{children}</span>
    </span>
  );
}

// -- query runs ----------------------------------------------------------------------------------

const RUN_STATUS: Record<string, { tone: Tone; label: string; icon: LucideIcon }> = {
  queued: { tone: "neutral", label: "Queued", icon: Clock },
  running: { tone: "neutral", label: "Running", icon: CircleDashed },
  completed: { tone: "ok", label: "Completed", icon: CircleCheck },
  partial: { tone: "warn", label: "Partial", icon: CircleAlert },
  failed: { tone: "bad", label: "Failed", icon: CircleX },
  canceled: { tone: "warn", label: "Canceled", icon: Ban },
};

export function RunStatusBadge({ status }: { status: string }) {
  const meta = RUN_STATUS[status] ?? { tone: "neutral" as Tone, label: humanizeLabel(status), icon: CircleDashed };
  return <StatusBadge tone={meta.tone} label={meta.label} icon={meta.icon} />;
}

const ACCESS_OUTCOMES = new Set(["authentication_required", "access_denied"]);

/**
 * Plain-language result of a run from its status and per-connector outcomes: completed with or
 * without findings, partial, access or configuration required, rate limited, failed, canceled.
 */
export function runOutcome(run: { status: string; connector_outcomes?: (string | null)[] }): {
  tone: Tone;
  label: string;
  icon: LucideIcon;
} {
  const outcomes = (run.connector_outcomes ?? []).filter((value): value is string => Boolean(value));
  switch (run.status) {
    case "queued":
      return { tone: "neutral", label: "Queued", icon: Clock };
    case "running":
      return { tone: "neutral", label: "Running", icon: CircleDashed };
    case "canceled":
      return { tone: "warn", label: "Canceled", icon: Ban };
    case "partial":
      return { tone: "warn", label: "Partial results", icon: CircleAlert };
    case "completed":
      if (outcomes.length > 0 && outcomes.every((outcome) => outcome === "no_findings")) {
        return { tone: "ok", label: "Completed, no findings", icon: CircleCheck };
      }
      return { tone: "ok", label: outcomes.includes("findings") ? "Completed with findings" : "Completed", icon: CircleCheck };
    case "failed":
      if (outcomes.some((outcome) => ACCESS_OUTCOMES.has(outcome))) {
        return { tone: "warn", label: "Access or setup required", icon: LockKeyhole };
      }
      if (outcomes.includes("rate_limited")) return { tone: "warn", label: "Rate limited", icon: Gauge };
      if (outcomes.includes("unsupported")) return { tone: "warn", label: "Not supported", icon: CircleHelp };
      return { tone: "bad", label: "Failed", icon: CircleX };
    default:
      return { tone: "neutral", label: humanizeLabel(run.status), icon: CircleDashed };
  }
}

export function RunOutcomeBadge({ run }: { run: { status: string; connector_outcomes?: (string | null)[] } }) {
  const meta = runOutcome(run);
  return <StatusBadge tone={meta.tone} label={meta.label} icon={meta.icon} />;
}

const OUTCOMES: Record<string, { tone: Tone; label: string; icon: LucideIcon }> = {
  findings: { tone: "ok", label: "Findings", icon: CircleCheck },
  no_findings: { tone: "ok", label: "No findings", icon: CircleCheck },
  partial: { tone: "warn", label: "Partial", icon: CircleAlert },
  canceled: { tone: "warn", label: "Canceled", icon: Ban },
  rate_limited: { tone: "warn", label: "Rate limited", icon: Gauge },
  authentication_required: { tone: "warn", label: "Authentication required", icon: LockKeyhole },
  access_denied: { tone: "warn", label: "Access denied", icon: LockKeyhole },
  unsupported: { tone: "warn", label: "Unsupported", icon: CircleHelp },
  unavailable: { tone: "bad", label: "Unavailable", icon: CircleX },
  parse_error: { tone: "bad", label: "Parse error", icon: CircleX },
};

/** A single connector's outcome. */
export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <StatusBadge tone="neutral" label="Pending" icon={Clock} />;
  const meta = OUTCOMES[outcome] ?? { tone: "bad" as Tone, label: humanizeLabel(outcome), icon: CircleX };
  return <StatusBadge tone={meta.tone} label={meta.label} icon={meta.icon} />;
}

// -- provenance ----------------------------------------------------------------------------------

export function OriginBadge({ origin }: { origin: string }) {
  const meta: Record<string, { label: string; icon: LucideIcon; tone?: Tone; title: string }> = {
    observed: { label: "Observed", icon: Eye, title: "Reported by a source during collection or import" },
    analyst_assertion: { label: "Analyst assertion", icon: PenLine, title: "Recorded by an analyst" },
    deterministic_derivation: { label: "Derived", icon: FileText, title: "Derived by a fixed rule from other records" },
    ai_suggestion: { label: "AI suggestion", icon: Sparkles, tone: "ai", title: "Suggested by a model; needs analyst review" },
    imported: {
      label: "Imported",
      icon: ArrowDownToLine,
      title: "Received from another tool through an exchange file (STIX); not verified by Tracehollow",
    },
  };
  const item = meta[origin];
  if (!item) return <Tag>{origin}</Tag>;
  if (item.tone === "ai") return <StatusBadge tone="ai" label={item.label} icon={item.icon} title={item.title} />;
  return (
    <Tag icon={item.icon} title={item.title}>
      {item.label}
    </Tag>
  );
}

export function SyntheticBadge({ label = "Synthetic" }: { label?: string }) {
  return (
    <StatusBadge
      tone="warn"
      icon={FlaskConical}
      label={label}
      title="Generated by the synthetic fixture connector; not collected from any real source."
    />
  );
}

export function AiGeneratedBadge({ label = "AI-generated" }: { label?: string }) {
  return <StatusBadge tone="ai" label={label} icon={Sparkles} title="Written by a model from cited case material" />;
}

export interface ProvenanceSubject {
  acquisition_method: string;
  kind?: string;
  derived_from_evidence_id?: string | null;
  collection_metadata?: Record<string, unknown>;
}

/** Where a piece of material came from, in the fixed provenance vocabulary. */
export function evidenceProvenance(evidence: ProvenanceSubject): { label: string; icon: LucideIcon; synthetic: boolean; description: string } {
  const metadata = evidence.collection_metadata ?? {};
  if (evidence.acquisition_method === "synthetic_fixture") {
    return { label: "Synthetic fixture", icon: FlaskConical, synthetic: true, description: "Generated test data, not collected from a real source" };
  }
  if (metadata.text_origin === "ocr") {
    return { label: "OCR text", icon: ScanText, synthetic: false, description: "Text recognised from page images; may contain recognition errors" };
  }
  if (metadata.text_origin === "embedded_text_layer" || metadata.derivation === "whatsapp_chat_text") {
    return { label: "Extracted text", icon: FileText, synthetic: false, description: "Text extracted from an imported original" };
  }
  if (metadata.derivation === "whatsapp_attachment") {
    return { label: "Import attachment", icon: Paperclip, synthetic: false, description: "A file found inside an imported archive" };
  }
  if (evidence.acquisition_method === "connector_collection") {
    if (evidence.derived_from_evidence_id) {
      return { label: "Extracted from collection", icon: FileText, synthetic: false, description: "Derived from a collected original" };
    }
    return { label: "Collected", icon: Globe, synthetic: false, description: "Collected from a public or authorized source by a connector" };
  }
  if (evidence.acquisition_method === "authorized_import") {
    return { label: "Authorized import", icon: Upload, synthetic: false, description: "Provided by an analyst who is authorized to use it" };
  }
  return { label: humanizeLabel(evidence.acquisition_method), icon: FileText, synthetic: false, description: "" };
}

export function ProvenanceBadge({ evidence }: { evidence: ProvenanceSubject }) {
  const meta = evidenceProvenance(evidence);
  if (meta.synthetic) return <SyntheticBadge label={meta.label} />;
  return (
    <Tag icon={meta.icon} title={meta.description}>
      {meta.label}
    </Tag>
  );
}

export function VerificationBadge({ status }: { status: "synthetic" | "fixture_tested" | "live_verified" }) {
  if (status === "live_verified") return <StatusBadge tone="ok" label="Live-verified" />;
  if (status === "synthetic") return <SyntheticBadge label="Synthetic: no live source" />;
  return <StatusBadge tone="warn" icon={FlaskConical} label="Fixture-tested: not live-verified" />;
}

export function ReviewBadge({ status }: { status: string }) {
  const meta: Record<string, { tone: Tone; icon: LucideIcon }> = {
    accepted: { tone: "ok", icon: CircleCheck },
    rejected: { tone: "bad", icon: CircleX },
    superseded: { tone: "warn", icon: Ban },
    unreviewed: { tone: "neutral", icon: CircleDashed },
  };
  const item = meta[status] ?? { tone: "neutral" as Tone, icon: CircleDashed };
  return <StatusBadge tone={item.tone} icon={item.icon} label={humanizeLabel(status)} />;
}

function humanizeLabel(value: string): string {
  return value.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());
}

// -- Phase 5: roles, monitors, change detection ----------------------------------------------------

export function RoleBadge({ role }: { role: string }) {
  const labels: Record<string, { label: string; title: string; icon: LucideIcon }> = {
    administrator: { label: "Administrator", title: "Manages accounts and system settings", icon: UserRound },
    analyst: { label: "Analyst", title: "Can work on the case", icon: PenLine },
    viewer: { label: "Viewer", title: "Can read the case only", icon: Eye },
    none: { label: "No access", title: "The account is deactivated", icon: Ban },
  };
  const meta = labels[role] ?? { label: humanizeLabel(role), title: role, icon: UserRound };
  return (
    <Tag icon={meta.icon} title={meta.title}>
      {meta.label}
    </Tag>
  );
}

const MONITOR_STATUS: Record<string, { tone: Tone; label: string; icon: LucideIcon }> = {
  enabled: { tone: "ok", label: "Enabled", icon: Radar },
  paused: { tone: "warn", label: "Paused", icon: Pause },
  disabled: { tone: "neutral", label: "Disabled", icon: Power },
};

export function MonitorStatusBadge({ status }: { status: string }) {
  const meta = MONITOR_STATUS[status] ?? { tone: "neutral" as Tone, label: humanizeLabel(status), icon: CircleDashed };
  return <StatusBadge tone={meta.tone} label={meta.label} icon={meta.icon} />;
}

const CHANGE_SET_STATUS: Record<string, { tone: Tone; label: string; icon: LucideIcon; title: string }> = {
  baseline_established: {
    tone: "neutral",
    label: "Baseline",
    icon: CircleDashed,
    title: "No comparable earlier collection: later collections are compared with this one.",
  },
  no_meaningful_change: {
    tone: "ok",
    label: "No meaningful change",
    icon: CircleCheck,
    title: "Compared with a compatible, complete baseline: nothing meaningful differs within the recorded scope.",
  },
  changes_detected: { tone: "warn", label: "Changes", icon: GitCompareArrows, title: "Items were new, changed, conflicting or no longer observed." },
  unknown: {
    tone: "warn",
    label: "Unknown",
    icon: CircleHelp,
    title: "The collection was incomplete: missing items are unknown, never reported as removed.",
  },
  baseline_incompatible: {
    tone: "neutral",
    label: "Not comparable",
    icon: Ban,
    title: "The earlier collection used a different version, input, parameters or scope; this one starts a new baseline.",
  },
};

export function ChangeSetStatusBadge({ status }: { status: string }) {
  const meta = CHANGE_SET_STATUS[status] ?? { tone: "neutral" as Tone, label: humanizeLabel(status), icon: CircleDashed, title: status };
  return <StatusBadge tone={meta.tone} label={meta.label} icon={meta.icon} title={meta.title} />;
}

const CHANGE_KIND: Record<string, { tone: Tone; label: string; icon: LucideIcon }> = {
  new: { tone: "neutral", label: "New", icon: PlusCircle },
  changed: { tone: "warn", label: "Changed", icon: GitCompareArrows },
  not_observed: { tone: "neutral", label: "No longer observed", icon: Minus },
  conflicting: { tone: "bad", label: "Conflicting", icon: CircleAlert },
  unknown: { tone: "warn", label: "Unknown", icon: CircleHelp },
};

export function ChangeKindBadge({ kind }: { kind: string }) {
  const meta = CHANGE_KIND[kind] ?? { tone: "neutral" as Tone, label: humanizeLabel(kind), icon: CircleDashed };
  return <StatusBadge tone={meta.tone} label={meta.label} icon={meta.icon} />;
}

export const CHANGE_KIND_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(CHANGE_KIND).map(([key, value]) => [key, value.label]),
);
