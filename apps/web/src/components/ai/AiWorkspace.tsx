"use client";

import { MessageSquarePlus, RotateCw, Search } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { describeError, formatUtcShort } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import {
  TERMINAL_AI_RUN_STATUSES,
  type AiConversation,
  type AiMessage,
  type AiMode,
  type AiRun,
  type AiStatus,
  type CaseAi,
  type IndexItem,
  type Page,
  type Passage,
  type SearchResult,
} from "@/lib/workspace-types";

import { useCase } from "../cases/CaseContext";
import { RelationshipDetailPanel } from "../cases/RelationshipDetailPanel";
import {
  ActionError,
  AiGeneratedBadge,
  Button,
  ChoiceField,
  ErrorNotice,
  Field,
  FieldGroup,
  LoadingState,
  Mono,
  Notice,
  PageHeader,
  Panel,
  StatusBadge,
  SyntheticBadge,
  TextInput,
  Timestamp,
  cn,
  humanize,
  plural,
} from "../ui";
import { AiRunStatusBadge, ProcessingIndicator, STARTER_QUESTIONS, runErrorText, stageLabel } from "./AiShared";
import { AnswerView } from "./AnswerView";
import { CitationPanel, PassageView } from "./CitationPanel";

const POLL_MS = 2500;


const MODE_OPTIONS: { value: AiMode; label: string; description: string }[] = [
  { value: "local_only", label: "Local only", description: "Indexing and answers use local models. Nothing is sent to a cloud provider." },
  {
    value: "cloud_allowed",
    label: "Cloud allowed",
    description: "Individual requests may use the configured cloud provider; the question and retrieved excerpts leave this machine.",
  },
  { value: "disabled", label: "Off", description: "No indexing and no AI requests for this case." },
];

function usePolling(active: boolean, reload: () => Promise<void>) {
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void reload(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, reload]);
}

function SettingsPanel({ caseAi, status, onChanged }: { caseAi: CaseAi; status: AiStatus; onChanged: () => void }) {
  const { apiBase, writable } = useCase();
  const { mutate } = useSession();
  const [mode, setMode] = useState<AiMode>(caseAi.mode);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await mutate(`${apiBase}/ai/settings`, { method: "PATCH", body: { mode, acknowledge_cloud_processing: acknowledged } });
      setAcknowledged(false);
      onChanged();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Case AI settings" description="Changing this stops queued AI requests, and running ones at their next step.">
      <form onSubmit={save} className="space-y-4 text-sm">
        <FieldGroup legend="AI processing for this case" legendClassName="sr-only">
          <fieldset disabled={!writable || busy} className="space-y-3">
            {MODE_OPTIONS.map((option) => (
              <ChoiceField
                key={option.value}
                type="radio"
                name="ai-mode"
                value={option.value}
                checked={mode === option.value}
                onChange={() => setMode(option.value)}
                label={<span className="font-medium">{option.label}</span>}
                description={option.description}
              />
            ))}
          </fieldset>
        </FieldGroup>
        {mode === "cloud_allowed" && caseAi.mode !== "cloud_allowed" ? (
          <label className="flex items-start gap-2.5 rounded-md border border-warn-line bg-warn-soft p-3 text-warn">
            <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} className="mt-0.5 shrink-0" />
            <span>
              I understand that questions and retrieved evidence excerpts from this case may be sent to {status.cloud_provider}
              {status.cloud_configured ? "" : " (not configured on this installation)"} when a request is sent to the cloud.
            </span>
          </label>
        ) : null}
        <ActionError message={error} />
        {writable ? (
          <Button type="submit" variant="primary" disabled={busy || mode === caseAi.mode || (mode === "cloud_allowed" && !acknowledged)} busy={busy}>
            Save AI setting
          </Button>
        ) : null}
      </form>
    </Panel>
  );
}

function ProvidersPanel({ status, onChanged }: { status: AiStatus; onChanged: () => void }) {
  const { mutate } = useSession();
  const [message, setMessage] = useState<string | null>(null);

  async function check() {
    try {
      await mutate("/api/v1/ai/provider-checks");
      setMessage("Check requested; results appear when the AI worker has run it.");
      window.setTimeout(onChanged, 3000);
    } catch (caught) {
      setMessage(describeError(caught));
    }
  }

  return (
    <Panel
      title="Model providers"
      flush
      actions={
        <Button size="sm" icon={RotateCw} onClick={() => void check()}>
          Check now
        </Button>
      }
    >
      <div className="space-y-3 p-4">
        {status.synthetic ? (
          <Notice tone="warn">
            This installation uses the synthetic fixture provider. Answers come from keyword rules and are labelled synthetic; they are not
            language-model results.
          </Notice>
        ) : null}
        {status.checks.length === 0 ? <p className="text-sm text-muted">No provider check has run yet.</p> : null}
        <ul className="space-y-3 text-sm">
          {status.checks.map((item) => (
            <li key={item.provider} className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-ink">{humanize(item.provider)}</span>
                <span className="text-xs text-muted">{humanize(item.location)}</span>
                {item.location === "cloud" ? (
                  <StatusBadge tone={item.configured ? "neutral" : "warn"} label={item.configured ? "Key configured (not contacted)" : "No API key"} />
                ) : (
                  <StatusBadge
                    tone={item.reachable ? (item.error_code ? "warn" : "ok") : "bad"}
                    label={item.reachable ? (item.error_code ? humanize(item.error_code) : "Reachable") : "Unreachable"}
                  />
                )}
              </div>
              {item.location !== "cloud" ? (
                <p className="text-xs text-muted">
                  Generation <Mono>{item.generation_model}</Mono> {item.generation_model_available ? "installed" : "not installed"} · embeddings{" "}
                  <Mono>{item.embedding_model}</Mono> {item.embedding_model_available ? "installed" : "not installed"} · checked{" "}
                  <Timestamp value={item.checked_at} />
                </p>
              ) : null}
            </li>
          ))}
        </ul>
        {message ? <p className="text-xs text-muted">{message}</p> : null}
      </div>
    </Panel>
  );
}

function IndexPanel({ caseAi, onChanged }: { caseAi: CaseAi; onChanged: () => void }) {
  const { apiBase, base, writable } = useCase();
  const { mutate } = useSession();
  const failed = useResource<Page<IndexItem>>(`${apiBase}/ai/index?status=failed&limit=10`);
  const [error, setError] = useState<string | null>(null);
  const counts = caseAi.index;
  const busyIndexing = counts.pending + counts.indexing > 0;
  usePolling(busyIndexing, async () => {
    onChanged();
    await failed.reload();
  });

  async function act(path: string, body?: unknown) {
    setError(null);
    try {
      await mutate(path, { body });
      onChanged();
      await failed.reload();
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  const rows: [string, number, "ok" | "warn" | "bad" | "neutral"][] = [
    ["Indexed", counts.indexed, "ok"],
    ["Pending", counts.pending, "neutral"],
    ["Indexing", counts.indexing, "neutral"],
    ["Stale", counts.stale, "warn"],
    ["Failed", counts.failed, "bad"],
    ["Canceled", counts.canceled, "warn"],
  ];
  return (
    <Panel title="Evidence index" description="Evidence text is split into passages with exact source locations and embedded with the local embedding model.">
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-1.5" aria-label="Index status counts" role="group">
          {rows.map(([label, value, tone]) => (
            <StatusBadge key={label} tone={value ? tone : "neutral"} label={`${label}: ${value}`} />
          ))}
        </div>
        <p className="text-xs text-muted">Of {plural(counts.total, "evidence record")}.</p>
        {caseAi.embedding_profile ? (
          <p className="text-xs text-muted">
            Embedding model <Mono>{caseAi.embedding_profile.model}</Mono> ({caseAi.embedding_profile.dimensions} dimensions, chunking v
            {caseAi.embedding_profile.chunking_version}){caseAi.embedding_profile.synthetic ? ", synthetic fixture embeddings" : ""}.
          </p>
        ) : (
          <p className="text-xs text-muted">No embeddings have been created yet.</p>
        )}
        {counts.stale ? (
          <p className="text-warn">{counts.stale} record(s) were indexed with an older configuration and may be missing from search until rebuilt.</p>
        ) : null}
        {failed.data && failed.data.items.length > 0 ? (
          <ul className="space-y-1">
            {failed.data.items.map((item) => (
              <li key={item.evidence_id} className="rounded-md border border-bad-line bg-bad-soft px-2.5 py-1.5 text-bad">
                <Link href={`${base}/evidence/${item.evidence_id}`} className="font-medium underline">
                  {item.title}
                </Link>
                : {item.error_detail ?? humanize(item.error_code ?? "failed")} (attempt {item.attempts})
              </li>
            ))}
          </ul>
        ) : null}
        <ActionError message={error} />
        {writable && caseAi.mode !== "disabled" ? (
          <div className="flex flex-wrap gap-2 border-t border-line pt-3">
            <Button size="sm" onClick={() => void act(`${apiBase}/ai/index/rebuild`, { scope: "failed" })} disabled={!counts.failed && !counts.canceled}>
              Retry failed and canceled
            </Button>
            <Button size="sm" onClick={() => void act(`${apiBase}/ai/index/rebuild`, { scope: "stale" })} disabled={!counts.stale}>
              Rebuild stale
            </Button>
            <Button size="sm" onClick={() => void act(`${apiBase}/ai/index/rebuild`, { scope: "all" })}>
              Rebuild all
            </Button>
            <Button size="sm" variant="danger-ghost" onClick={() => void act(`${apiBase}/ai/index/cancel`)} disabled={!busyIndexing}>
              Cancel pending indexing
            </Button>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function SearchPanel() {
  const { apiBase } = useCase();
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [openChunk, setOpenChunk] = useState<string | null>(null);
  const results = useResource<SearchResult>(submitted ? `${apiBase}/ai/search?q=${encodeURIComponent(submitted)}` : null);
  const passage = useResource<Passage>(openChunk ? `${apiBase}/ai/chunks/${openChunk}` : null);

  return (
    <Panel title="Search indexed evidence" description="Keyword and exact-identifier search across this case's indexed evidence. No model is involved.">
      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setOpenChunk(null);
          setSubmitted(query.trim() || null);
        }}
        className="flex flex-wrap items-end gap-2"
      >
        <Field label="Search terms" htmlFor="ai-search" className="min-w-60 flex-1">
          <TextInput id="ai-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} minLength={2} />
        </Field>
        <Button type="submit" icon={Search} disabled={query.trim().length < 2}>
          Search
        </Button>
      </form>
      {results.state === "error" ? <ErrorNotice error={results.error} className="mt-3" /> : null}
      {submitted && results.state === "loading" && !results.data ? <LoadingState label="Searching…" className="mt-3" /> : null}
      {results.data && results.data.hits.length === 0 ? <p className="mt-3 text-sm text-muted">No indexed passage matches these terms.</p> : null}
      {results.data?.coverage_notes.length ? (
        <ul className="mt-3 list-inside list-disc text-xs text-warn">
          {results.data.coverage_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      <ul className="mt-3 divide-y divide-line">
        {results.data?.hits.map((hit) => (
          <li key={hit.chunk_id} className="space-y-1.5 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="text-left font-medium text-accent hover:underline"
                aria-expanded={openChunk === hit.chunk_id}
                onClick={() => setOpenChunk(openChunk === hit.chunk_id ? null : hit.chunk_id)}
              >
                {hit.evidence_title}
              </button>
              {hit.synthetic ? <SyntheticBadge /> : null}
              <span className="text-xs text-muted">matched by {hit.matched_by.map(humanize).join(", ")}</span>
            </div>
            <p className="line-clamp-3 max-w-[72ch] whitespace-pre-wrap break-words text-muted">{hit.snippet}</p>
            {openChunk === hit.chunk_id && passage.data ? <PassageView passage={passage.data} highlightLabel="Indexed passage" /> : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function AskPanel({ disabled }: { disabled: boolean }) {
  const { apiBase, base, writable } = useCase();
  const { mutate } = useSession();
  const router = useRouter();
  const conversations = useResource<Page<AiConversation>>(`${apiBase}/ai/conversations?limit=20`);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  async function start(question?: string) {
    setError(null);
    setStarting(question ?? "new");
    try {
      const created = await mutate<AiConversation>(`${apiBase}/ai/conversations`, { body: {} });
      if (question) {
        await mutate(`${apiBase}/ai/conversations/${created.id}/questions`, { body: { question, location: "local" } });
      }
      router.push(`${base}/ai/conversations/${created.id}`);
    } catch (caught) {
      setError(describeError(caught));
      setStarting(null);
    }
  }

  return (
    <Panel
      title="Questions and conversations"
      description="Answers cite the exact evidence passages or database results they rely on, and say when the evidence is insufficient."
      actions={
        writable && !disabled ? (
          <Button variant="primary" icon={MessageSquarePlus} onClick={() => void start()} disabled={starting !== null} busy={starting === "new"}>
            New conversation
          </Button>
        ) : null
      }
      flush
    >
      <ActionError message={error} className="m-4" />
      {writable && !disabled ? (
        <div className="border-b border-line px-4 py-3">
          <p className="text-xs font-medium text-muted">Start with a common question</p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {STARTER_QUESTIONS.map((question) => (
              <li key={question}>
                <button
                  type="button"
                  onClick={() => void start(question)}
                  disabled={starting !== null}
                  className={cn(
                    "rounded-md border border-line bg-surface px-2.5 py-1.5 text-left text-sm text-ink transition-colors hover:border-accent/50 hover:bg-accent-soft disabled:opacity-55",
                    starting === question && "border-accent",
                  )}
                >
                  {question}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {conversations.state === "error" ? (
        <div className="p-4">
          <ErrorNotice error={conversations.error} />
        </div>
      ) : null}
      {!conversations.data && conversations.state !== "error" ? <LoadingState label="Loading conversations…" className="p-4" /> : null}
      {conversations.data && conversations.data.items.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No conversations yet.</p> : null}
      <ul className="divide-y divide-line">
        {conversations.data?.items.map((conversation) => (
          <li key={conversation.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
            <Link href={`${base}/ai/conversations/${conversation.id}`} className="min-w-0 font-medium break-words text-accent hover:underline">
              {conversation.title}
            </Link>
            <span className="text-xs text-muted tabular-nums">
              {plural(conversation.message_count, "message")} · updated {formatUtcShort(conversation.updated_at)}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function GeneratedPanel({ kind, disabled }: { kind: "summary" | "suggestions"; disabled: boolean }) {
  const { apiBase, writable } = useCase();
  const { mutate } = useSession();
  const outputs = useResource<Page<AiMessage>>(`${apiBase}/ai/outputs?kind=${kind}&limit=5`);
  const runType = kind === "summary" ? "summary" : "relationship_suggestions";
  const runs = useResource<Page<AiRun>>(`${apiBase}/ai/runs?run_type=${runType}&limit=5`);
  const [error, setError] = useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const active = runs.data?.items.some((run) => !TERMINAL_AI_RUN_STATUSES.includes(run.status)) ?? false;
  usePolling(active, async () => {
    await runs.reload();
    await outputs.reload();
  });

  async function generate() {
    setError(null);
    try {
      await mutate(`${apiBase}/ai/${kind === "summary" ? "summaries" : "relationship-suggestions"}`, { body: { location: "local" } });
      await runs.reload();
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  const title = kind === "summary" ? "Case summaries" : "Relationship suggestions";
  const latestRun = runs.data?.items[0];
  const runsById = new Map((runs.data?.items ?? []).map((run) => [run.id, run]));
  return (
    <Panel
      title={title}
      description={
        kind === "summary"
          ? "A cited summary of the case evidence that keeps inferences, conflicts and coverage gaps visible."
          : "Suggested links between existing entities. Every suggestion stays unreviewed until an analyst accepts or rejects it."
      }
      actions={
        writable && !disabled ? (
          <Button size="sm" onClick={() => void generate()} disabled={active}>
            {kind === "summary" ? "Generate summary" : "Suggest relationships"}
          </Button>
        ) : null
      }
    >
      <div className="space-y-4">
        <ActionError message={error} />
        {latestRun && !TERMINAL_AI_RUN_STATUSES.includes(latestRun.status) ? (
          <p className="flex flex-wrap items-center gap-2 text-sm text-muted" aria-live="polite">
            <AiRunStatusBadge status={latestRun.status} /> {stageLabel(latestRun.stage)}…
          </p>
        ) : null}
        {latestRun && (latestRun.status === "failed" || latestRun.status === "canceled") ? (
          <p role="alert" className="text-sm text-bad">
            The latest request {latestRun.status}: {runErrorText(latestRun)}
          </p>
        ) : null}
        {outputs.data && outputs.data.items.length === 0 ? <p className="text-sm text-muted">Nothing generated yet.</p> : null}
        <div className={cn("grid items-start gap-4", (selectedCitation || reviewing) && "xl:grid-cols-2")}>
          <div className="min-w-0 space-y-4">
            {outputs.data?.items.map((message) =>
              kind === "summary" ? (
                <article key={message.id} className="space-y-2 rounded-md border border-line p-3">
                  <p className="text-xs text-muted">
                    Generated <Timestamp value={message.created_at} />
                  </p>
                  <AnswerView
                    message={message}
                    run={message.ai_run_id ? runsById.get(message.ai_run_id) : undefined}
                    onOpenCitation={setSelectedCitation}
                    selectedCitation={selectedCitation}
                  />
                </article>
              ) : (
                <article key={message.id} className="space-y-3 rounded-md border border-line p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                    <AiGeneratedBadge />
                    Generated <Timestamp value={message.created_at} />
                  </div>
                  {message.answer?.suggestions?.length ? null : (
                    <Notice>{message.answer?.server_notes?.join(" ") || "No suggestion could be supported by the evidence."}</Notice>
                  )}
                  <ul className="space-y-2">
                    {message.answer?.suggestions?.map((suggestion) => (
                      <li key={suggestion.relationship_id} className="space-y-1.5 rounded-md border border-line p-3">
                        <p className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium text-ink">{suggestion.source.display_name}</span> <Mono className="text-muted">{suggestion.predicate}</Mono>{" "}
                          <span className="font-medium text-ink">{suggestion.target.display_name}</span>
                        </p>
                        <p className="max-w-[72ch] break-words text-muted">{suggestion.rationale}</p>
                        <div className="flex flex-wrap items-center gap-1.5">
                          {suggestion.citations.map((citation) => (
                            <button
                              key={citation.citation_id}
                              type="button"
                              onClick={() => {
                                setReviewing(null);
                                setSelectedCitation(citation.citation_id);
                              }}
                              aria-label={`Open citation ${citation.label}`}
                              className="inline-flex h-7 items-center rounded border border-accent/40 px-1.5 font-mono text-code text-accent hover:bg-accent-soft"
                            >
                              [{citation.label}]
                            </button>
                          ))}
                          <Button
                            size="sm"
                            onClick={() => {
                              setSelectedCitation(null);
                              setReviewing(suggestion.relationship_id);
                            }}
                          >
                            Review
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                  {message.answer?.rejected?.length ? (
                    <p className="text-xs text-muted">
                      {message.answer.rejected.length} model suggestion(s) were discarded: {message.answer.rejected.map((item) => humanize(item.reason)).join(", ")}.
                    </p>
                  ) : null}
                </article>
              ),
            )}
          </div>
          {selectedCitation ? <CitationPanel citationId={selectedCitation} onClose={() => setSelectedCitation(null)} /> : null}
          {reviewing ? <RelationshipDetailPanel relationshipId={reviewing} onClose={() => setReviewing(null)} onChanged={() => void outputs.reload()} /> : null}
        </div>
      </div>
    </Panel>
  );
}

export function AiWorkspace() {
  const { apiBase } = useCase();
  const status = useResource<AiStatus>("/api/v1/ai/status");
  const caseAi = useResource<CaseAi>(`${apiBase}/ai`);

  const header = (
    <PageHeader
      title="AI workspace"
      description="Ask questions about this case. Answers are AI-generated, cite exact passages or database results, and say when the evidence does not support an answer. No confidence percentages are invented."
    />
  );

  if ((status.state === "error" && !status.data) || (caseAi.state === "error" && !caseAi.data)) {
    const error = status.state === "error" ? status.error : caseAi.state === "error" ? caseAi.error : null;
    return (
      <div className="space-y-6">
        {header}
        <ErrorNotice error={error} onRetry={() => void Promise.all([status.reload(), caseAi.reload()])} />
      </div>
    );
  }
  if (!status.data || !caseAi.data) {
    return (
      <div className="space-y-6">
        {header}
        <LoadingState label="Loading AI workspace…" rows={5} />
      </div>
    );
  }
  const disabled = !status.data.enabled || caseAi.data.mode === "disabled";

  return (
    <div className="space-y-6">
      {header}
      <ProcessingIndicator status={status.data} caseAi={caseAi.data} />
      {!status.data.enabled ? null : (
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem] xl:grid-cols-[minmax(0,1fr)_24rem]">
          <div className="min-w-0 space-y-6">
            <AskPanel disabled={disabled} />
            <GeneratedPanel kind="summary" disabled={disabled} />
            <GeneratedPanel kind="suggestions" disabled={disabled} />
            <SearchPanel />
          </div>
          <div className="min-w-0 space-y-6">
            <IndexPanel caseAi={caseAi.data} onChanged={() => void caseAi.reload()} />
            <SettingsPanel key={caseAi.data.policy_version} caseAi={caseAi.data} status={status.data} onChanged={() => void caseAi.reload()} />
            <ProvidersPanel status={status.data} onChanged={() => void status.reload()} />
          </div>
        </div>
      )}
    </div>
  );
}
