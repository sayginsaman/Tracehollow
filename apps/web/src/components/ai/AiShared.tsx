"use client";

import { Ban, CircleCheck, CircleDashed, CircleHelp, CircleX, Clock, Cloud, Database, FlaskConical, HardDrive, Lightbulb, PowerOff, Scale } from "lucide-react";
import type { ReactNode } from "react";

import type { AiRun, AiStatus, AnswerStatus, CaseAi, ClaimKind } from "@/lib/workspace-types";

import { StatusBadge, cn, type Tone } from "../ui";

/** Questions the evidence-grounded pipeline can answer or honestly decline; they start a conversation. */
export const STARTER_QUESTIONS = [
  "Which sources were collected, and which runs were partial or failed?",
  "What does the evidence say about who registered or owns the domains in this case?",
  "Which relationships in this case are still unreviewed?",
  "What happened, in date order, according to the evidence?",
  "Where do the sources in this case disagree?",
];

const CLAIM_LABELS: Record<ClaimKind, { label: string; tone: Tone; description: string; icon: typeof CircleCheck }> = {
  fact: { label: "Sourced", tone: "ok", description: "Stated by the cited evidence", icon: CircleCheck },
  count: { label: "Database count", tone: "ok", description: "Exact result of a database query over the entire case", icon: Database },
  inference: { label: "Inference", tone: "warn", description: "A conclusion beyond what the sources state", icon: Lightbulb },
  conflict: { label: "Conflict", tone: "warn", description: "The cited sources disagree", icon: Scale },
  insufficient: { label: "Insufficient evidence", tone: "neutral", description: "The case material does not answer this", icon: CircleHelp },
};

export function ClaimKindBadge({ kind }: { kind: ClaimKind }) {
  const meta = CLAIM_LABELS[kind] ?? CLAIM_LABELS.fact;
  return <StatusBadge tone={meta.tone} icon={meta.icon} label={meta.label} title={meta.description} />;
}

export function answerStatusText(status: AnswerStatus): { tone: "ok" | "warn" | "neutral"; text: string } {
  if (status === "answered") return { tone: "ok", text: "Answered from cited case material." };
  if (status === "partially_answered") {
    return { tone: "warn", text: "Partially answered: some parts could not be supported by the case material." };
  }
  return { tone: "warn", text: "Insufficient evidence: the case material does not support an answer." };
}

const STAGES: Record<string, string> = {
  queued: "Waiting for the AI worker",
  planning: "Choosing database lookups",
  running_tools: "Running database lookups",
  retrieving: "Searching the case evidence",
  generating: "Generating the answer with the model",
  validating: "Checking citations against the evidence",
  done: "Done",
};

export function stageLabel(stage: string): string {
  return STAGES[stage] ?? stage;
}

const ERROR_HELP: Record<string, string> = {
  model_unavailable:
    "The model service could not be reached. Start Ollama on the host and check TRACEHOLLOW_AI_OLLAMA_BASE_URL.",
  model_not_found: "The configured model is not installed. Pull it with Ollama (see the AI models guide).",
  provider_timeout: "The model did not answer within the time limit. Try a shorter question or a smaller model.",
  output_truncated: "The model reached its output limit before finishing. Try a more specific question.",
  invalid_model_output: "The model returned output that could not be read as a structured answer.",
  processing_policy_changed: "The case's AI processing setting changed after this request was made; nothing further was sent.",
  authorization_revoked: "Your access to this case changed while the request was running; no output was stored.",
  case_unavailable: "The case is being deleted or no longer exists.",
  ai_disabled: "AI features were turned off while this request was queued.",
  case_ai_disabled: "AI was turned off for this case while this request was queued.",
  canceled: "Canceled. Any model output that arrived afterwards was discarded.",
  worker_lost: "The AI worker stopped repeatedly while processing this request.",
  internal_error: "Tracehollow hit an internal error while processing this request.",
  provider_auth_failed: "The cloud provider rejected the configured API key.",
  provider_rate_limited: "The provider's rate limit was reached. Try again later.",
};

export function runErrorText(run: Pick<AiRun, "error_code" | "error_detail">): string | null {
  if (!run.error_code) return null;
  return ERROR_HELP[run.error_code] ?? run.error_detail ?? run.error_code;
}

export function AiRunStatusBadge({ status }: { status: AiRun["status"] }) {
  const meta: Record<AiRun["status"], { tone: Tone; label: string; icon: typeof Clock }> = {
    queued: { tone: "neutral", label: "Queued", icon: Clock },
    running: { tone: "neutral", label: "Running", icon: CircleDashed },
    completed: { tone: "ok", label: "Completed", icon: CircleCheck },
    failed: { tone: "bad", label: "Failed", icon: CircleX },
    canceled: { tone: "warn", label: "Canceled", icon: Ban },
  };
  const item = meta[status];
  return <StatusBadge tone={item.tone} icon={item.icon} label={item.label} />;
}

export function SyntheticModelBadge() {
  return (
    <StatusBadge
      tone="warn"
      icon={FlaskConical}
      label="Synthetic model"
      title="Produced by the deterministic synthetic fixture provider (keyword rules), not by a language model."
    />
  );
}

/** Where AI processing for this case happens, stated in plain language. */
export function ProcessingIndicator({ status, caseAi, className }: { status: AiStatus | null; caseAi: CaseAi | null; className?: string }) {
  let tone: "ok" | "warn" | "neutral" = "neutral";
  let Icon = CircleDashed;
  let body: ReactNode = "Loading AI status…";
  if (status && !status.enabled) {
    Icon = PowerOff;
    body = "AI features are disabled for this installation. Every other part of the workspace keeps working.";
  } else if (status && caseAi) {
    const local = status.synthetic
      ? "the synthetic fixture provider (keyword rules, not a language model)"
      : `local models on this machine (${status.local_generation_model}; embeddings ${status.local_embedding_model})`;
    if (caseAi.mode === "disabled") {
      Icon = PowerOff;
      body = "AI processing is turned off for this case. Nothing is indexed or sent to any model.";
    } else if (caseAi.mode === "local_only") {
      tone = "ok";
      Icon = HardDrive;
      body = <>Local processing only: this case is processed by {local}. It is never sent to a cloud provider.</>;
    } else {
      tone = "warn";
      Icon = Cloud;
      body = (
        <>
          Cloud processing allowed: requests default to {local}. When you choose cloud for a request, the question and retrieved evidence
          excerpts are sent to {status.cloud_provider} ({status.cloud_model ?? "model not set"}).
          {status.cloud_configured ? "" : " No cloud API key is configured, so cloud requests are refused."}
        </>
      );
    }
  }
  return (
    <div
      role="status"
      aria-label="AI processing location"
      className={cn(
        "flex gap-2.5 rounded-md border px-3 py-2.5 text-sm",
        tone === "ok" ? "border-ok-line bg-ok-soft text-ok" : tone === "warn" ? "border-warn-line bg-warn-soft text-warn" : "border-line bg-sunken text-muted",
        className,
      )}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0">{body}</span>
    </div>
  );
}
