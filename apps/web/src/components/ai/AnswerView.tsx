"use client";

import { formatUtc } from "@/lib/messages";
import type { AiMessage, AiRun } from "@/lib/workspace-types";

import { KeyValue, Mono, Notice, humanize } from "../ui";
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
    <details className="rounded-md border border-line px-3 py-2 text-sm">
      <summary className="cursor-pointer text-muted">How this was produced</summary>
      <div className="mt-2 space-y-2">
        <KeyValue
          items={[
            ["Provider and model", run.provider ? `${run.provider} · ${run.model}` : "—"],
            ["Processing location", run.processing_location ? humanize(run.processing_location) : humanize(run.requested_location)],
            ["Prompt version", run.prompt_template_version ?? "—"],
            ["Tokens", tokens],
            ["Cost", "Unknown (Tracehollow does not estimate provider prices)"],
            ["Evidence passages retrieved", String(run.retrieval?.chunks?.length ?? 0)],
            ["Semantic search", semantic?.used ? "Used" : `Not used${semantic?.reason ? ` (${humanize(semantic.reason)})` : ""}`],
            ["Requested", formatUtc(run.queued_at)],
            ["Finished", formatUtc(run.finished_at)],
          ]}
        />
        {run.tool_calls.filter((call) => !call.rejected).length ? (
          <div>
            <p className="font-medium">Database lookups (entire case)</p>
            <ul className="list-inside list-disc text-xs">
              {run.tool_calls
                .filter((call) => !call.rejected)
                .map((call) => (
                  <li key={call.ref ?? call.tool}>
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
    </details>
  );
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
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Notice tone={status.tone}>{status.text}</Notice>
        {answer.synthetic_model ? <SyntheticModelBadge /> : null}
      </div>
      <ol className="space-y-2">
        {(answer.claims ?? []).map((claim, index) => (
          <li key={`${message.id}-${index}`} className="rounded-md border border-line p-3 text-sm">
            <div className="mb-1">
              <ClaimKindBadge kind={claim.kind} />
            </div>
            <p className="whitespace-pre-wrap break-words">{claim.text}</p>
            {claim.citations.length ? (
              <div className="mt-2 flex flex-wrap gap-1" aria-label="Citations">
                {claim.citations.map((citation) => (
                  <button
                    key={citation.citation_id}
                    type="button"
                    onClick={() => onOpenCitation(citation.citation_id)}
                    aria-pressed={selectedCitation === citation.citation_id}
                    aria-label={`Open citation ${citation.label}: ${citationTitle(message, citation.citation_id)}`}
                    title={citationTitle(message, citation.citation_id)}
                    className={`rounded border px-1.5 py-0.5 font-mono text-xs ${
                      selectedCitation === citation.citation_id
                        ? "border-accent bg-accent text-white dark:text-canvas"
                        : "border-accent/40 text-accent hover:bg-canvas"
                    }`}
                  >
                    [{citation.label}]
                  </button>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ol>
      {answer.limitations?.length ? (
        <div className="text-sm">
          <p className="font-medium">Limitations noted in the answer</p>
          <ul className="list-inside list-disc text-muted">
            {answer.limitations.map((item) => (
              <li key={item} className="break-words">
                {item}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {answer.coverage_notes?.length ? (
        <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-sm text-warn">
          <p className="font-medium">Coverage (from the database)</p>
          <ul className="list-inside list-disc">
            {answer.coverage_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {answer.server_notes?.length ? (
        <ul className="list-inside list-disc text-xs text-muted">
          {answer.server_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      {run ? <RunProvenance run={run} /> : null}
    </div>
  );
}
