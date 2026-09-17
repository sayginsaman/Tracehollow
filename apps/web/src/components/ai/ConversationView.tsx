"use client";

import { Ban, RotateCw, Send } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import { TERMINAL_AI_RUN_STATUSES, type AiRun, type AiStatus, type CaseAi, type ConversationDetail } from "@/lib/workspace-types";

import { useCase } from "../cases/CaseContext";
import { usePageCrumb } from "../shell/ShellContext";
import {
  ActionError,
  Button,
  ChoiceField,
  ErrorNotice,
  Field,
  FieldGroup,
  LoadingState,
  Notice,
  PageHeader,
  Panel,
  TextArea,
  Timestamp,
  cn,
  plural,
} from "../ui";
import { AiRunStatusBadge, ProcessingIndicator, STARTER_QUESTIONS, runErrorText, stageLabel } from "./AiShared";
import { AnswerView } from "./AnswerView";
import { CitationPanel } from "./CitationPanel";

export const AI_POLL_INTERVAL_MS = 2000;

function RunProgress({ run, onCancel, onAskAgain, busy }: { run: AiRun; onCancel?: () => void; onAskAgain?: () => void; busy: boolean }) {
  const active = !TERMINAL_AI_RUN_STATUSES.includes(run.status);
  const error = runErrorText(run);
  return (
    <div className="space-y-2 rounded-md border border-line bg-sunken/60 p-3 text-sm" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <AiRunStatusBadge status={run.status} />
        {active ? <span className="text-ink">{stageLabel(run.stage)}…</span> : null}
        {active && run.stage === "generating" && run.processing_location === "local" ? (
          <span className="text-xs text-muted">Local models can take a minute or more.</span>
        ) : null}
        {active && onCancel ? (
          <Button size="sm" variant="danger-ghost" icon={Ban} onClick={onCancel} disabled={busy || Boolean(run.cancel_requested_at)}>
            {run.cancel_requested_at ? "Cancellation requested" : "Cancel"}
          </Button>
        ) : null}
      </div>
      {run.status === "failed" || run.status === "canceled" ? (
        <p role={run.status === "failed" ? "alert" : undefined} className={run.status === "failed" ? "text-bad" : "text-muted"}>
          {error ?? "No answer was stored."}
          {run.error_code === "model_not_found" && run.error_detail ? ` ${run.error_detail}` : ""} Nothing was answered, so nothing was invented.
        </p>
      ) : null}
      {(run.status === "failed" || run.status === "canceled") && onAskAgain ? (
        <Button size="sm" icon={RotateCw} onClick={onAskAgain}>
          Ask again
        </Button>
      ) : null}
    </div>
  );
}

export function ConversationView({ conversationId }: { conversationId: string }) {
  const { apiBase, writable, can } = useCase();
  const { mutate } = useSession();
  const detail = useResource<ConversationDetail>(`${apiBase}/ai/conversations/${conversationId}`);
  const caseAi = useResource<CaseAi>(`${apiBase}/ai`);
  const status = useResource<AiStatus>("/api/v1/ai/status");
  const [question, setQuestion] = useState("");
  const [location, setLocation] = useState<"local" | "cloud">("local");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);
  usePageCrumb(detail.data?.conversation.title);

  const runs = detail.data?.runs ?? [];
  const active = runs.some((run) => !TERMINAL_AI_RUN_STATUSES.includes(run.status));
  const { reload } = detail;

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void reload(), AI_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [active, reload]);

  if (detail.state === "error" && !detail.data) return <ErrorNotice error={detail.error} onRetry={() => void reload()} />;
  if (!detail.data) return <LoadingState label="Loading conversation…" rows={5} />;
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
      <PageHeader title={data.conversation.title} meta={<span>{plural(questions.length, "question")}</span>} />
      <ProcessingIndicator status={status.data} caseAi={caseAi.data} />

      <div className={cn("grid items-start gap-6", selectedCitation ? "lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] xl:grid-cols-[minmax(0,1fr)_30rem]" : "max-w-4xl")}>
        <div className="min-w-0 space-y-5">
          {questions.length === 0 ? (
            <Notice>
              Ask a question about this case. Answers cite the exact evidence passages or database results they rely on, and say when the evidence
              is insufficient.
            </Notice>
          ) : null}
          {questions.map((message) => {
            const run = message.ai_run_id ? runsById.get(message.ai_run_id) : undefined;
            const answer = data.messages.find((item) => item.role === "assistant" && item.ai_run_id === message.ai_run_id);
            return (
              <article key={message.id} className="rounded-lg border border-line bg-surface">
                <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-4 py-3">
                  <h2 className="max-w-[72ch] text-read font-semibold whitespace-pre-wrap break-words text-ink">{message.content}</h2>
                  <span className="text-xs text-muted">
                    Asked <Timestamp value={message.created_at} />
                  </span>
                </header>
                <div className="p-4">
                  {answer ? (
                    <AnswerView message={answer} run={run} onOpenCitation={setSelectedCitation} selectedCitation={selectedCitation} />
                  ) : run ? (
                    <RunProgress
                      run={run}
                      onCancel={can("ai.request") ? () => void cancel(run.id) : undefined}
                      onAskAgain={
                        writable
                          ? () => {
                              setQuestion(message.content);
                              document.getElementById("ai-question")?.focus();
                            }
                          : undefined
                      }
                      busy={busy}
                    />
                  ) : null}
                </div>
              </article>
            );
          })}

          {writable ? (
            <Panel title="Ask a question">
              {aiOff ? (
                <Notice>AI is turned off, so new questions cannot be asked. Existing answers remain readable.</Notice>
              ) : (
                <form onSubmit={ask} className="space-y-4">
                  <Field label="Question" htmlFor="ai-question" hint="Exact counts and dates are computed from the database for the whole case.">
                    <TextArea
                      id="ai-question"
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      rows={3}
                      maxLength={2000}
                      required
                      minLength={3}
                      className="text-read"
                    />
                  </Field>
                  {questions.length === 0 ? (
                    <div>
                      <p className="text-xs font-medium text-muted">Suggestions</p>
                      <ul className="mt-1.5 flex flex-wrap gap-2">
                        {STARTER_QUESTIONS.map((starter) => (
                          <li key={starter}>
                            <button
                              type="button"
                              onClick={() => setQuestion(starter)}
                              className="rounded-md border border-line bg-surface px-2.5 py-1 text-left text-sm text-ink hover:border-accent/50 hover:bg-accent-soft"
                            >
                              {starter}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {cloudChoice ? (
                    <FieldGroup legend="Process this question">
                      <div className="flex flex-wrap gap-x-5 gap-y-2">
                        <ChoiceField type="radio" name="ai-location" checked={location === "local"} onChange={() => setLocation("local")} label="Locally" />
                        <ChoiceField
                          type="radio"
                          name="ai-location"
                          checked={location === "cloud"}
                          onChange={() => setLocation("cloud")}
                          label="With the cloud provider (sends the question and retrieved excerpts)"
                        />
                      </div>
                    </FieldGroup>
                  ) : null}
                  <ActionError message={error} />
                  <Button type="submit" variant="primary" icon={Send} disabled={busy || question.trim().length < 3} busy={busy}>
                    {busy ? "Sending…" : "Ask"}
                  </Button>
                </form>
              )}
            </Panel>
          ) : null}
        </div>
        {selectedCitation ? (
          <div className="min-w-0 lg:sticky lg:top-20">
            <CitationPanel citationId={selectedCitation} onClose={() => setSelectedCitation(null)} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
