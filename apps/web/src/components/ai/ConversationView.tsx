"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { describeError, formatUtc } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import {
  TERMINAL_AI_RUN_STATUSES,
  type AiRun,
  type AiStatus,
  type CaseAi,
  type ConversationDetail,
} from "@/lib/workspace-types";

import { Button, ErrorNotice, Field, LoadingState, Notice, Section, TextArea } from "../ui";
import { useCase } from "../cases/CaseContext";
import { AiRunStatusBadge, ProcessingIndicator, runErrorText, stageLabel } from "./AiShared";
import { AnswerView } from "./AnswerView";
import { CitationPanel } from "./CitationPanel";

export const AI_POLL_INTERVAL_MS = 2000;

function RunProgress({ run, onCancel, busy }: { run: AiRun; onCancel: () => void; busy: boolean }) {
  const active = !TERMINAL_AI_RUN_STATUSES.includes(run.status);
  const error = runErrorText(run);
  return (
    <div className="space-y-2 rounded-md border border-line p-3 text-sm" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <AiRunStatusBadge status={run.status} />
        {active ? <span className="text-muted">{stageLabel(run.stage)}…</span> : null}
        {active && run.stage === "generating" && run.processing_location === "local" ? (
          <span className="text-xs text-muted">Local models can take a minute or more.</span>
        ) : null}
        {active ? (
          <Button variant="danger" onClick={onCancel} disabled={busy || Boolean(run.cancel_requested_at)}>
            {run.cancel_requested_at ? "Cancellation requested" : "Cancel"}
          </Button>
        ) : null}
      </div>
      {run.status === "failed" || run.status === "canceled" ? (
        <p role={run.status === "failed" ? "alert" : undefined} className={run.status === "failed" ? "text-bad" : "text-muted"}>
          {error ?? "No answer was stored."}
          {run.error_code === "model_not_found" && run.error_detail ? ` ${run.error_detail}` : ""}
        </p>
      ) : null}
    </div>
  );
}

export function ConversationView({ conversationId }: { conversationId: string }) {
  const { apiBase, base, writable } = useCase();
  const { mutate } = useSession();
  const detail = useResource<ConversationDetail>(`${apiBase}/ai/conversations/${conversationId}`);
  const caseAi = useResource<CaseAi>(`${apiBase}/ai`);
  const status = useResource<AiStatus>("/api/v1/ai/status");
  const [question, setQuestion] = useState("");
  const [location, setLocation] = useState<"local" | "cloud">("local");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);

  const runs = detail.data?.runs ?? [];
  const active = runs.some((run) => !TERMINAL_AI_RUN_STATUSES.includes(run.status));
  const { reload } = detail;

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void reload(), AI_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [active, reload]);

  if (detail.state === "error" && !detail.data) return <ErrorNotice error={detail.error} onRetry={() => void reload()} />;
  if (!detail.data) return <LoadingState label="Loading conversation…" />;
  const data = detail.data;
  const aiOff = status.data?.enabled === false || caseAi.data?.mode === "disabled";
  const cloudChoice = caseAi.data?.mode === "cloud_allowed" && status.data?.cloud_configured;

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await mutate(`${apiBase}/ai/conversations/${conversationId}/questions`, {
        body: { question, location: cloudChoice ? location : "local" },
      });
      setQuestion("");
      await reload();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function cancel(runId: string) {
    setBusy(true);
    try {
      await mutate(`${apiBase}/ai/runs/${runId}/cancel`);
      await reload();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  const runsById = new Map(runs.map((run) => [run.id, run]));
  const questions = data.messages.filter((message) => message.role === "user");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link href={`${base}/ai`} className="text-sm text-accent hover:underline">
          ← AI workspace
        </Link>
        <h2 className="text-xl font-semibold break-words">{data.conversation.title}</h2>
      </div>
      <ProcessingIndicator status={status.data} caseAi={caseAi.data} />

      <div className={`grid gap-6 ${selectedCitation ? "lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]" : ""}`}>
        <div className="space-y-4">
          {questions.length === 0 ? (
            <Notice>
              Ask a question about this case. Answers cite the exact evidence passages or database results they rely on,
              and say when the evidence is insufficient.
            </Notice>
          ) : null}
          {questions.map((message) => {
            const run = message.ai_run_id ? runsById.get(message.ai_run_id) : undefined;
            const answer = data.messages.find((item) => item.role === "assistant" && item.ai_run_id === message.ai_run_id);
            return (
              <article key={message.id} className="space-y-3 rounded-lg border border-line bg-surface p-4">
                <header className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-medium whitespace-pre-wrap break-words">{message.content}</p>
                  <time className="text-xs text-muted" dateTime={message.created_at}>
                    {formatUtc(message.created_at)}
                  </time>
                </header>
                {answer ? (
                  <AnswerView
                    message={answer}
                    run={run}
                    onOpenCitation={setSelectedCitation}
                    selectedCitation={selectedCitation}
                  />
                ) : run ? (
                  <RunProgress run={run} onCancel={() => void cancel(run.id)} busy={busy} />
                ) : null}
              </article>
            );
          })}

          {writable ? (
            <Section title="Ask a question">
              {aiOff ? (
                <Notice>AI is turned off, so new questions cannot be asked. Existing answers remain readable.</Notice>
              ) : (
                <form onSubmit={ask} className="space-y-3">
                  <Field label="Question" htmlFor="ai-question" hint="Exact counts and dates are computed from the database for the whole case.">
                    <TextArea
                      id="ai-question"
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      rows={3}
                      maxLength={2000}
                      required
                      minLength={3}
                    />
                  </Field>
                  {cloudChoice ? (
                    <fieldset className="text-sm">
                      <legend className="font-medium">Process this question</legend>
                      <label className="mr-4 inline-flex items-center gap-2">
                        <input type="radio" name="ai-location" checked={location === "local"} onChange={() => setLocation("local")} />
                        Locally
                      </label>
                      <label className="inline-flex items-center gap-2">
                        <input type="radio" name="ai-location" checked={location === "cloud"} onChange={() => setLocation("cloud")} />
                        With the cloud provider (sends the question and retrieved excerpts)
                      </label>
                    </fieldset>
                  ) : null}
                  {error ? (
                    <p role="alert" className="text-sm text-bad">
                      {error}
                    </p>
                  ) : null}
                  <Button type="submit" variant="primary" disabled={busy || question.trim().length < 3}>
                    {busy ? "Sending…" : "Ask"}
                  </Button>
                </form>
              )}
            </Section>
          ) : null}
        </div>
        {selectedCitation ? (
          <div className="lg:sticky lg:top-4 lg:self-start">
            <CitationPanel citationId={selectedCitation} onClose={() => setSelectedCitation(null)} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
