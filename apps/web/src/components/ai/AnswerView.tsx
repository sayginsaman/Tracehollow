"use client";

import { CircleAlert, Info } from "lucide-react";

import type { AiMessage, AiRun } from "@/lib/workspace-types";

import { AiGeneratedBadge, Disclosure, KeyValue, Mono, Notice, StatusBadge, Timestamp, cn, humanize } from "../ui";
import { ClaimKindBadge, SyntheticModelBadge, answerStatusText } from "./AiShared";

function citationTitle(message: AiMessage, citationId: string): string {
  const summary = message.citations.find((item) => item.id === citationId);
  if (!summary) return "Open citation";
  if (summary.ref_type === "tool") return `Database result (${summary.tool_name ?? "query"})`;
  return summary.source_available ? `Evidence: ${summary.evidence_title ?? "unknown"}` : "Source deleted";
}

export function RunProvenance({ run }: { run: AiRun }) {
  const usage = run.usage ?? {};
  const tokens =
    usage.source === "provider_reported"
      ? `${usage.input_tokens ?? "?"} in / ${usage.output_tokens ?? "?"} out (reported by the provider)`
      : "Not reported by the provider";
  const removed = run.validation?.claims_removed ?? [];
  const rejectedTools = run.tool_calls.filter((call) => call.rejected);
  const semantic = run.retrieval?.retrievers?.semantic;
  return (
    <Disclosure summary="How this was produced">
      <div className="space-y-3 text-sm">
        <KeyValue
          compact
          items={[
            ["Provider and model", run.provider ? `${run.provider} · ${run.model}` : "Not recorded"],
            ["Processing location", run.processing_location ? humanize(run.processing_location) : humanize(run.requested_location)],
            ["Prompt version", run.prompt_template_version ?? "Not recorded"],
            ["Tokens", tokens],
            ["Cost", "Unknown (Tracehollow does not estimate provider prices)"],
            ["Evidence passages retrieved", String(run.retrieval?.chunks?.length ?? 0)],
            ["Semantic search", semantic?.used ? "Used" : `Not used${semantic?.reason ? ` (${humanize(semantic.reason)})` : ""}`],
            ["Requested", <Timestamp key="requested" value={run.queued_at} />],
            ["Finished", <Timestamp key="finished" value={run.finished_at} fallback="Not finished" />],
          ]}
        />
        {run.tool_calls.filter((call) => !call.rejected).length ? (
          <div>
            <p className="font-medium text-ink">Database lookups (entire case)</p>
            <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs text-muted">
              {run.tool_calls
                .filter((call) => !call.rejected)
                .map((call) => (
                  <li key={call.ref ?? call.tool} className="break-words">
                    <Mono>{call.ref}</Mono> {call.tool} <Mono>{JSON.stringify(call.arguments ?? {})}</Mono>
                    {typeof call.result?.count === "number" ? ` → ${call.result.count}` : ""}
                  </li>
                ))}
            </ul>
          </div>
        ) : null}
        {rejectedTools.length ? (
          <p className="text-xs text-muted">
            Ignored {rejectedTools.length} tool request(s) from the model: {rejectedTools.map((call) => `${call.tool} (${humanize(call.rejected ?? "")})`).join(", ")}.
          </p>
        ) : null}
        {removed.length ? (
          <p className="text-xs text-muted">
            Removed {removed.length} unverifiable statement(s): {removed.map((item) => humanize(item.reason)).join(", ")}.
          </p>
        ) : null}
      </div>
    </Disclosure>
  );
}

function differenceLabel(type: string): string {
  if (type === "change_over_time") return "Change over time";
  if (type === "undetermined") return "Difference, cause unknown";
  return "Sources disagree";
}

function differenceHint(type: string): string {
  if (type === "change_over_time") return "The records give different periods for these values, so this is a change over time.";
  if (type === "undetermined") return "The records give no period and were published at different times, so it cannot be told whether this is a change or a disagreement.";
  return "The records cover the same time or give none, so they disagree.";
}

export function AnswerView({
  message,
  run,
  onOpenCitation,
  selectedCitation,
}: {
  message: AiMessage;
  run: AiRun | undefined;
  onOpenCitation: (citationId: string) => void;
  selectedCitation: string | null;
}) {
  const answer = message.answer;
  if (!answer) return <p className="text-sm text-muted">{message.content}</p>;
  const status = answerStatusText(answer.status);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <AiGeneratedBadge />
        {answer.synthetic_model ? <SyntheticModelBadge /> : null}
      </div>
      <Notice tone={status.tone}>{status.text}</Notice>
      <ol className="space-y-3">
        {(answer.claims ?? []).map((claim, index) => (
          <li key={`${message.id}-${index}`} className="space-y-2 rounded-md border border-line bg-surface p-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <ClaimKindBadge kind={claim.kind} />
              {claim.answers_question === false ? (
                <StatusBadge
                  tone="neutral"
                  icon={Info}
                  title={
                    claim.applicability === "other_subject"
                      ? "This statement is about another subject than the question."
                      : claim.applicability === "other_period"
                        ? "This statement is about another period than the question names."
                        : "This statement does not answer the question that was asked."
                  }
                  label={claim.applicability === "other_subject" ? "Other subject" : claim.applicability === "other_period" ? "Other period" : "Context"}
                />
              ) : null}
              {claim.difference_type ? (
                <StatusBadge
                  tone={claim.difference_type === "disagreement" ? "warn" : "neutral"}
                  icon={CircleAlert}
                  title={differenceHint(claim.difference_type)}
                  label={differenceLabel(claim.difference_type)}
                />
              ) : null}
            </div>
            <p className="max-w-[72ch] text-read whitespace-pre-wrap break-words text-ink">{claim.text}</p>
            {claim.citations.length ? (
              <div className="flex flex-wrap items-center gap-1.5" aria-label="Citations" role="group">
                {claim.citations.map((citation) => {
                  const selected = selectedCitation === citation.citation_id;
                  return (
                    <button
                      key={citation.citation_id}
                      type="button"
                      onClick={() => onOpenCitation(citation.citation_id)}
                      aria-pressed={selected}
                      aria-label={`Open citation ${citation.label}: ${citationTitle(message, citation.citation_id)}`}
                      title={citationTitle(message, citation.citation_id)}
                      className={cn(
                        "inline-flex h-7 items-center rounded border px-1.5 font-mono text-code transition-colors",
                        selected ? "border-accent bg-accent text-on-accent" : "border-accent/40 bg-surface text-accent hover:bg-accent-soft",
                      )}
                    >
                      [{citation.label}]
                    </button>
                  );
                })}
              </div>
            ) : null}
          </li>
        ))}
      </ol>
      {answer.limitations?.length ? (
        <div className="text-sm">
          <p className="font-medium text-ink">Limitations noted in the answer</p>
          <ul className="mt-1 list-inside list-disc space-y-0.5 text-muted">
            {answer.limitations.map((item) => (
              <li key={item} className="break-words">
                {item}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {answer.coverage_notes?.length ? (
        <Notice tone="warn" title="Coverage (from the database)">
          <ul className="list-inside list-disc space-y-0.5">
            {answer.coverage_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
      {answer.server_notes?.length ? (
        <ul className="list-inside list-disc space-y-0.5 text-xs text-muted">
          {answer.server_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      {run ? <RunProvenance run={run} /> : null}
    </div>
  );
}
