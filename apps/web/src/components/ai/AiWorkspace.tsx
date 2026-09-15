"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { describeError, formatUtc } from "@/lib/messages";
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

import { StatusBadge } from "../StatusBadge";
import { Button, EmptyState, ErrorNotice, Field, LoadingState, Mono, Notice, Section, SyntheticBadge, TextInput, humanize } from "../ui";
import { useCase } from "../cases/CaseContext";
import { RelationshipDetailPanel } from "../cases/RelationshipDetailPanel";
import { AiRunStatusBadge, ProcessingIndicator, runErrorText, stageLabel } from "./AiShared";
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
    <Section title="Case AI settings" description="Changing this setting stops queued AI requests and running ones at their next step.">
      <form onSubmit={save} className="space-y-3 text-sm">
        <fieldset disabled={!writable || busy} className="space-y-2">
          <legend className="sr-only">AI processing for this case</legend>
          {MODE_OPTIONS.map((option) => (
            <label key={option.value} className="flex items-start gap-2">
              <input type="radio" name="ai-mode" value={option.value} checked={mode === option.value} onChange={() => setMode(option.value)} className="mt-1" />
              <span>
                <span className="font-medium">{option.label}</span>
                <span className="block text-muted">{option.description}</span>
              </span>
            </label>
          ))}
          {mode === "cloud_allowed" && caseAi.mode !== "cloud_allowed" ? (
            <label className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn-bg p-2 text-warn">
              <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} className="mt-1" />
              <span>
                I understand that questions and retrieved evidence excerpts from this case may be sent to {status.cloud_provider}
                {status.cloud_configured ? "" : " (not configured on this installation)"} when a request is sent to the cloud.
              </span>
            </label>
          ) : null}
        </fieldset>
        {error ? (
          <p role="alert" className="text-bad">
            {error}
          </p>
        ) : null}
        {writable ? (
          <Button type="submit" variant="primary" disabled={busy || mode === caseAi.mode || (mode === "cloud_allowed" && !acknowledged)}>
            Save AI setting
          </Button>
        ) : null}
      </form>
    </Section>
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
    <Section title="Model providers" actions={<Button onClick={() => void check()}>Check now</Button>}>
      {status.synthetic ? (
        <Notice tone="warn">
          This installation uses the synthetic fixture provider. Answers come from keyword rules and are labelled synthetic; they
          are not language-model results.
        </Notice>
      ) : null}
      {status.checks.length === 0 ? <EmptyState>No provider check has run yet.</EmptyState> : null}
      <ul className="space-y-2 text-sm">
        {status.checks.map((check) => (
          <li key={check.provider} className="rounded-md border border-line p-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{humanize(check.provider)}</span>
              <span className="text-xs text-muted">{humanize(check.location)}</span>
              {check.location === "cloud" ? (
                <StatusBadge tone={check.configured ? "neutral" : "warn"} label={check.configured ? "Key configured (not contacted)" : "No API key"} />
              ) : (
                <StatusBadge tone={check.reachable ? (check.error_code ? "warn" : "ok") : "bad"} label={check.reachable ? (check.error_code ? humanize(check.error_code) : "Reachable") : "Unreachable"} />
              )}
            </div>
            {check.location !== "cloud" ? (
              <p className="mt-1 text-xs text-muted">
                Generation <Mono>{check.generation_model}</Mono> {check.generation_model_available ? "installed" : "not installed"} ·
                embeddings <Mono>{check.embedding_model}</Mono> {check.embedding_model_available ? "installed" : "not installed"} · checked{" "}
                {formatUtc(check.checked_at)}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
      {message ? <p className="mt-2 text-xs text-muted">{message}</p> : null}
    </Section>
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
    <Section
      title="Evidence index"
      description="Evidence text is split into passages with exact source locations and embedded with the local embedding model."
    >
      <div className="flex flex-wrap gap-2" aria-label="Index status counts">
        {rows.map(([label, value, tone]) => (
          <StatusBadge key={label} tone={value ? tone : "neutral"} label={`${label}: ${value}`} />
        ))}
        <span className="text-xs text-muted">of {counts.total} evidence record(s)</span>
      </div>
      {caseAi.embedding_profile ? (
        <p className="mt-2 text-xs text-muted">
          Embedding model <Mono>{caseAi.embedding_profile.model}</Mono> ({caseAi.embedding_profile.dimensions} dimensions,
          chunking v{caseAi.embedding_profile.chunking_version}){caseAi.embedding_profile.synthetic ? " — synthetic fixture embeddings" : ""}.
        </p>
      ) : (
        <p className="mt-2 text-xs text-muted">No embeddings have been created yet.</p>
      )}
      {counts.stale ? (
        <p className="mt-2 text-sm text-warn">
          {counts.stale} record(s) were indexed with an older configuration and may be missing from search until rebuilt.
        </p>
      ) : null}
      {failed.data && failed.data.items.length > 0 ? (
        <ul className="mt-3 space-y-1 text-sm">
          {failed.data.items.map((item) => (
            <li key={item.evidence_id} className="rounded-md border border-bad/30 bg-bad-bg px-2 py-1 text-bad">
              <Link href={`${base}/evidence/${item.evidence_id}`} className="underline">
                {item.title}
              </Link>
              : {item.error_detail ?? humanize(item.error_code ?? "failed")} (attempt {item.attempts})
            </li>
          ))}
        </ul>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 text-sm text-bad">
          {error}
        </p>
      ) : null}
      {writable && caseAi.mode !== "disabled" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={() => void act(`${apiBase}/ai/index/rebuild`, { scope: "failed" })} disabled={!counts.failed && !counts.canceled}>
            Retry failed and canceled
          </Button>
          <Button onClick={() => void act(`${apiBase}/ai/index/rebuild`, { scope: "stale" })} disabled={!counts.stale}>
            Rebuild stale
          </Button>
          <Button onClick={() => void act(`${apiBase}/ai/index/rebuild`, { scope: "all" })}>Rebuild all</Button>
          <Button variant="danger" onClick={() => void act(`${apiBase}/ai/index/cancel`)} disabled={!busyIndexing}>
            Cancel pending indexing
          </Button>
        </div>
      ) : null}
    </Section>
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
    <Section title="Search indexed evidence" description="Keyword and exact-identifier search across this case's indexed evidence.">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setOpenChunk(null);
          setSubmitted(query.trim() || null);
        }}
        className="flex flex-wrap items-end gap-2"
      >
        <div className="min-w-60 flex-1">
          <Field label="Search terms" htmlFor="ai-search">
            <TextInput id="ai-search" value={query} onChange={(event) => setQuery(event.target.value)} minLength={2} />
          </Field>
        </div>
        <Button type="submit" disabled={query.trim().length < 2}>
          Search
        </Button>
      </form>
      {results.state === "error" ? <ErrorNotice error={results.error} /> : null}
      {submitted && results.state === "loading" && !results.data ? <LoadingState label="Searching…" /> : null}
      {results.data && results.data.hits.length === 0 ? <EmptyState>No indexed passage matches these terms.</EmptyState> : null}
      {results.data?.coverage_notes.length ? (
        <ul className="mt-2 list-inside list-disc text-xs text-warn">
          {results.data.coverage_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      <ul className="mt-3 space-y-2">
        {results.data?.hits.map((hit) => (
          <li key={hit.chunk_id} className="rounded-md border border-line p-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="font-medium text-accent hover:underline" onClick={() => setOpenChunk(hit.chunk_id)}>
                {hit.evidence_title}
              </button>
              {hit.synthetic ? <SyntheticBadge /> : null}
              <span className="text-xs text-muted">matched by {hit.matched_by.map(humanize).join(", ")}</span>
            </div>
            <p className="mt-1 line-clamp-3 whitespace-pre-wrap break-words text-xs text-muted">{hit.snippet}</p>
            {openChunk === hit.chunk_id && passage.data ? (
              <div className="mt-2">
                <PassageView passage={passage.data} highlightLabel="Indexed passage" />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Section>
  );
}

function ConversationsPanel({ disabled }: { disabled: boolean }) {
  const { apiBase, base, writable } = useCase();
  const { mutate } = useSession();
  const router = useRouter();
  const conversations = useResource<Page<AiConversation>>(`${apiBase}/ai/conversations?limit=20`);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setError(null);
    try {
      const created = await mutate<AiConversation>(`${apiBase}/ai/conversations`, { body: {} });
      router.push(`${base}/ai/conversations/${created.id}`);
    } catch (caught) {
      setError(describeError(caught));
    }
  }

  return (
    <Section
      title="Conversations"
      actions={writable && !disabled ? <Button variant="primary" onClick={() => void start()}>New conversation</Button> : null}
    >
      {error ? (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      ) : null}
      {conversations.state === "error" ? <ErrorNotice error={conversations.error} /> : null}
      {conversations.data && conversations.data.items.length === 0 ? <EmptyState>No conversations yet.</EmptyState> : null}
      <ul className="divide-y divide-line text-sm">
        {conversations.data?.items.map((conversation) => (
          <li key={conversation.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <Link href={`${base}/ai/conversations/${conversation.id}`} className="font-medium text-accent hover:underline break-words">
              {conversation.title}
            </Link>
            <span className="text-xs text-muted">
              {conversation.message_count} message(s) · updated {formatUtc(conversation.updated_at)}
            </span>
          </li>
        ))}
      </ul>
    </Section>
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
    <Section
      title={title}
      description={
        kind === "summary"
          ? "A cited summary of the case evidence that keeps inferences, conflicts and coverage gaps visible."
          : "Suggested links between existing entities. Every suggestion stays unreviewed until an analyst accepts or rejects it."
      }
      actions={
        writable && !disabled ? (
          <Button onClick={() => void generate()} disabled={active}>
            {kind === "summary" ? "Generate summary" : "Suggest relationships"}
          </Button>
        ) : null
      }
    >
      {error ? (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      ) : null}
      {latestRun && !TERMINAL_AI_RUN_STATUSES.includes(latestRun.status) ? (
        <p className="text-sm text-muted" aria-live="polite">
          <AiRunStatusBadge status={latestRun.status} /> {stageLabel(latestRun.stage)}…
        </p>
      ) : null}
      {latestRun && (latestRun.status === "failed" || latestRun.status === "canceled") ? (
        <p role="alert" className="text-sm text-bad">
          The latest request {latestRun.status}: {runErrorText(latestRun)}
        </p>
      ) : null}
      {outputs.data && outputs.data.items.length === 0 ? <EmptyState>Nothing generated yet.</EmptyState> : null}
      <div className={`grid gap-4 ${selectedCitation || reviewing ? "lg:grid-cols-2" : ""}`}>
        <div className="space-y-4">
          {outputs.data?.items.map((message) =>
            kind === "summary" ? (
              <article key={message.id} className="rounded-md border border-line p-3">
                <p className="mb-2 text-xs text-muted">Generated {formatUtc(message.created_at)}</p>
                <AnswerView
                  message={message}
                  run={message.ai_run_id ? runsById.get(message.ai_run_id) : undefined}
                  onOpenCitation={setSelectedCitation}
                  selectedCitation={selectedCitation}
                />
              </article>
            ) : (
              <article key={message.id} className="space-y-2 rounded-md border border-line p-3 text-sm">
                <p className="text-xs text-muted">Generated {formatUtc(message.created_at)}</p>
                {message.answer?.suggestions?.length ? null : (
                  <Notice>{message.answer?.server_notes?.join(" ") || "No suggestion could be supported by the evidence."}</Notice>
                )}
                <ul className="space-y-2">
                  {message.answer?.suggestions?.map((suggestion) => (
                    <li key={suggestion.relationship_id} className="rounded-md border border-line p-2">
                      <p>
                        <span className="font-medium">{suggestion.source.display_name}</span> <Mono>{suggestion.predicate}</Mono>{" "}
                        <span className="font-medium">{suggestion.target.display_name}</span>
                      </p>
                      <p className="text-xs text-muted break-words">{suggestion.rationale}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-1">
                        {suggestion.citations.map((citation) => (
                          <button
                            key={citation.citation_id}
                            type="button"
                            onClick={() => {
                              setReviewing(null);
                              setSelectedCitation(citation.citation_id);
                            }}
                            aria-label={`Open citation ${citation.label}`}
                            className="rounded border border-accent/40 px-1.5 py-0.5 font-mono text-xs text-accent"
                          >
                            [{citation.label}]
                          </button>
                        ))}
                        <Button
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
                    {message.answer.rejected.length} model suggestion(s) were discarded:{" "}
                    {message.answer.rejected.map((item) => humanize(item.reason)).join(", ")}.
                  </p>
                ) : null}
              </article>
            ),
          )}
        </div>
        {selectedCitation ? <CitationPanel citationId={selectedCitation} onClose={() => setSelectedCitation(null)} /> : null}
        {reviewing ? (
          <RelationshipDetailPanel relationshipId={reviewing} onClose={() => setReviewing(null)} onChanged={() => void outputs.reload()} />
        ) : null}
      </div>
    </Section>
  );
}

export function AiWorkspace() {
  const { apiBase } = useCase();
  const status = useResource<AiStatus>("/api/v1/ai/status");
  const caseAi = useResource<CaseAi>(`${apiBase}/ai`);

  if ((status.state === "error" && !status.data) || (caseAi.state === "error" && !caseAi.data)) {
    const error = status.state === "error" ? status.error : caseAi.state === "error" ? caseAi.error : null;
    return <ErrorNotice error={error} onRetry={() => void Promise.all([status.reload(), caseAi.reload()])} />;
  }
  if (!status.data || !caseAi.data) return <LoadingState label="Loading AI workspace…" />;
  const disabled = !status.data.enabled || caseAi.data.mode === "disabled";

  return (
    <div className="space-y-6">
      <ProcessingIndicator status={status.data} caseAi={caseAi.data} />
      {!status.data.enabled ? null : (
        <>
          <div className="grid gap-6 lg:grid-cols-2">
            <SettingsPanel key={caseAi.data.policy_version} caseAi={caseAi.data} status={status.data} onChanged={() => void caseAi.reload()} />
            <ProvidersPanel status={status.data} onChanged={() => void status.reload()} />
          </div>
          <IndexPanel caseAi={caseAi.data} onChanged={() => void caseAi.reload()} />
          <ConversationsPanel disabled={disabled} />
          <SearchPanel />
          <GeneratedPanel kind="summary" disabled={disabled} />
          <GeneratedPanel kind="suggestions" disabled={disabled} />
        </>
      )}
    </div>
  );
}
