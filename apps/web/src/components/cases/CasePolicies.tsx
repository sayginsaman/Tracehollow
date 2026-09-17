"use client";

import { Download, Eye, Plus, Share2, Trash2 } from "lucide-react";
import { useState } from "react";

import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { CaseBudgets, Page, RetentionJob, RetentionPolicy, RetentionPreview, StixReport } from "@/lib/workspace-types";

import { BudgetUsageList } from "../monitors/BudgetUsage";
import {
  ActionError,
  Button,
  ButtonLink,
  ChoiceField,
  ErrorNotice,
  Field,
  FieldGroup,
  IconButton,
  KeyValue,
  LoadingState,
  Notice,
  Panel,
  Select,
  StatusBadge,
  SubHeading,
  TextInput,
  Timestamp,
  formatBytes,
  humanize,
  plural,
  type Tone,
} from "../ui";
import { useCase } from "./CaseContext";

type BudgetRow = CaseBudgets["budgets"][number];
const PERIODS: BudgetRow["period"][] = ["day", "week", "month"];

export function BudgetsPanel() {
  const { apiBase, writable } = useCase();
  const { mutate } = useSession();
  const budgets = useResource<CaseBudgets>(`${apiBase}/budgets`);
  const [draft, setDraft] = useState<BudgetRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const rows = draft ?? budgets.data?.budgets ?? [];

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      await mutate(`${apiBase}/budgets`, { method: "PUT", body: { budgets: draft } });
      setDraft(null);
      await budgets.reload();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Collection budget"
      description="A ceiling on requests to external sources for the whole case, shared by manual runs and every monitor. Work stops when it is used up; collected pages are kept."
    >
      {budgets.state === "error" && !budgets.data ? <ErrorNotice error={budgets.error} onRetry={() => void budgets.reload()} /> : null}
      {!budgets.data && budgets.state === "loading" ? <LoadingState label="Loading budget…" /> : null}
      {budgets.data ? (
        <div className="space-y-5">
          {budgets.data.usage.length ? <BudgetUsageList usage={budgets.data.usage} /> : <p className="text-sm text-muted">No case budget is set. Monitors still have their own budgets and every run has request limits.</p>}
          {writable ? (
            <div className="space-y-3">
              {rows.map((row, index) => (
                <div key={index} className="grid items-end gap-2 sm:grid-cols-[10rem_9rem_10rem_auto]">
                  <Field label="Measure" htmlFor={`budget-metric-${index}`}>
                    <Select
                      id={`budget-metric-${index}`}
                      value={row.metric}
                      onChange={(event) => setDraft(rows.map((item, i) => (i === index ? { ...item, metric: event.target.value as BudgetRow["metric"] } : item)))}
                    >
                      <option value="requests">Requests</option>
                      <option value="provider_units">Provider units</option>
                    </Select>
                  </Field>
                  <Field label="Per" htmlFor={`budget-period-${index}`}>
                    <Select
                      id={`budget-period-${index}`}
                      value={row.period}
                      onChange={(event) => setDraft(rows.map((item, i) => (i === index ? { ...item, period: event.target.value as BudgetRow["period"] } : item)))}
                    >
                      {PERIODS.map((period) => (
                        <option key={period} value={period}>
                          {humanize(period)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Limit" htmlFor={`budget-limit-${index}`}>
                    <TextInput
                      id={`budget-limit-${index}`}
                      type="number"
                      min={0}
                      value={row.limit_units}
                      onChange={(event) => setDraft(rows.map((item, i) => (i === index ? { ...item, limit_units: Math.max(0, Number(event.target.value)) } : item)))}
                    />
                  </Field>
                  <IconButton icon={Trash2} label="Remove this limit" onClick={() => setDraft(rows.filter((_, i) => i !== index))} />
                </div>
              ))}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="ghost" icon={Plus} onClick={() => setDraft([...rows, { metric: "requests", period: "day", limit_units: 500 }])} disabled={rows.length >= 6}>
                  Add limit
                </Button>
                {draft ? (
                  <>
                    <Button size="sm" variant="primary" onClick={() => void save()} busy={busy} disabled={busy}>
                      Save budget
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setDraft(null)} disabled={busy}>
                      Discard changes
                    </Button>
                  </>
                ) : null}
              </div>
              <ActionError message={error} />
            </div>
          ) : null}
          <p className="max-w-[72ch] text-xs text-muted">{budgets.data.measurement} Lowering a limit below current use stops new requests until the next period.</p>
        </div>
      ) : null}
    </Panel>
  );
}

const JOB_TONES: Record<RetentionJob["status"], Tone> = { queued: "neutral", running: "neutral", completed: "ok", failed: "bad" };

function daysValue(value: string): number | null {
  const parsed = Number(value);
  return value.trim() && Number.isFinite(parsed) && parsed >= 1 ? Math.round(parsed) : null;
}

export function RetentionPanel() {
  const { apiBase, caseDetail, can } = useCase();
  const { mutate } = useSession();
  const policy = useResource<RetentionPolicy>(`${apiBase}/retention`);
  const jobs = useResource<Page<RetentionJob>>(`${apiBase}/retention/jobs?limit=5`);
  const [collected, setCollected] = useState<string | null>(null);
  const [imported, setImported] = useState<string | null>(null);
  const [preview, setPreview] = useState<RetentionPreview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const manage = can("retention.manage") && caseDetail.status === "active";

  if (policy.state === "error" && !policy.data) return <ErrorNotice error={policy.error} onRetry={() => void policy.reload()} />;
  if (!policy.data) return <LoadingState label="Loading retention…" />;
  const data = policy.data;
  const collectedValue = collected ?? (data.collected_results_max_age_days ? String(data.collected_results_max_age_days) : "");
  const importedValue = imported ?? (data.imported_evidence_max_age_days ? String(data.imported_evidence_max_age_days) : "");
  const rules = { collected_results_max_age_days: daysValue(collectedValue), imported_evidence_max_age_days: daysValue(importedValue) };
  const empty = rules.collected_results_max_age_days === null && rules.imported_evidence_max_age_days === null;

  async function act(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Panel
      title="Retention"
      description="Remove collected and imported evidence after a set age. Off by default. Removal is permanent in this installation and runs as a retryable background job."
    >
      <div className="space-y-5">
        <KeyValue
          compact
          items={[
            ["Status", data.active ? <StatusBadge key="status" tone="warn" label={`Active, version ${data.version}`} /> : <StatusBadge key="status" tone="neutral" label="Off" />],
            ["Collected results", data.collected_results_max_age_days ? `Removed after ${plural(data.collected_results_max_age_days, "day")}` : "Kept"],
            ["Imported evidence", data.imported_evidence_max_age_days ? `Removed after ${plural(data.imported_evidence_max_age_days, "day")}` : "Kept"],
            ["Last applied", <Timestamp key="applied" value={data.last_applied_at} fallback="Never" />],
            ...(data.monitor_rules.length
              ? ([
                  [
                    "Monitor rules",
                    data.monitor_rules
                      .map((rule) => `${rule.name}: ${[rule.keep_last_runs ? `keep last ${rule.keep_last_runs} runs` : null, rule.max_age_days ? `${rule.max_age_days} days` : null].filter(Boolean).join(", ")}`)
                      .join("; "),
                  ],
                ] as [string, React.ReactNode][])
              : []),
          ]}
        />

        {manage ? (
          <div className="space-y-4 border-t border-line pt-4">
            <FieldGroup legend="Rules" hint="Leave a rule empty to keep that kind of evidence. Monitor rules are set on each monitor.">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Remove collected results older than (days)" htmlFor="retention-collected">
                  <TextInput id="retention-collected" type="number" min={1} max={3650} value={collectedValue} onChange={(event) => { setCollected(event.target.value); setPreview(null); }} />
                </Field>
                <Field label="Remove imported evidence older than (days)" htmlFor="retention-imported">
                  <TextInput id="retention-imported" type="number" min={1} max={3650} value={importedValue} onChange={(event) => { setImported(event.target.value); setPreview(null); }} />
                </Field>
              </div>
            </FieldGroup>
            <Button icon={Eye} disabled={empty || busy !== null} busy={busy === "preview"} onClick={() => void act("preview", async () => setPreview(await mutate<RetentionPreview>(`${apiBase}/retention/preview`, { body: rules })))}>
              Preview what would be removed
            </Button>

            {preview ? (
              <div className="space-y-3 rounded-md border border-line bg-sunken p-3" aria-live="polite">
                <p className="text-sm font-medium text-ink">If applied now</p>
                <KeyValue
                  compact
                  items={[
                    ["Executions with results removed", String(preview.executions)],
                    ["Imported originals", String(preview.imported_originals)],
                    ["Evidence records", `${preview.evidence_records} (${formatBytes(preview.stored_bytes)})`],
                    ["Observations", String(preview.observations)],
                    ["Relationship references", String(preview.relationship_references)],
                    ["Indexed text passages", String(preview.index_chunks)],
                    ["AI citations marked as removed", String(preview.ai_citations_affected)],
                    ["Protected change baselines", String(preview.protected_baselines)],
                    ...(Object.keys(preview.deferred).length
                      ? ([["Waiting for active work", Object.entries(preview.deferred).map(([key, value]) => `${humanize(key)}: ${value}`).join(", ")]] as [string, React.ReactNode][])
                      : []),
                  ]}
                />
                <div>
                  <SubHeading>Not removed</SubHeading>
                  <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs text-muted">
                    {preview.not_removed.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
                <form
                  className="space-y-2 border-t border-line pt-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void act("activate", async () => {
                      await mutate(`${apiBase}/retention`, { method: "PUT", body: { ...rules, confirm_title: confirmation } });
                      setPreview(null);
                      setConfirmation("");
                      setCollected(null);
                      setImported(null);
                      await Promise.all([policy.reload(), jobs.reload()]);
                    });
                  }}
                >
                  <Field label={`Type the case title to activate: ${caseDetail.title}`} htmlFor="retention-confirm">
                    <TextInput id="retention-confirm" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" />
                  </Field>
                  <Button type="submit" variant="danger" busy={busy === "activate"} disabled={busy !== null || confirmation !== caseDetail.title}>
                    Activate and apply now
                  </Button>
                </form>
              </div>
            ) : null}

            {data.active ? (
              <div className="flex flex-wrap gap-2">
                <Button size="sm" busy={busy === "run"} disabled={busy !== null} onClick={() => void act("run", async () => { await mutate(`${apiBase}/retention/jobs`); await jobs.reload(); })}>
                  Apply now
                </Button>
                <Button size="sm" variant="ghost" busy={busy === "off"} disabled={busy !== null} onClick={() => void act("off", async () => { await mutate(`${apiBase}/retention`, { method: "DELETE" }); await policy.reload(); })}>
                  Turn retention off
                </Button>
              </div>
            ) : null}
            <ActionError message={error} />
          </div>
        ) : null}

        {jobs.data?.items.length ? (
          <div className="border-t border-line pt-4">
            <SubHeading>Recent retention jobs</SubHeading>
            <ul className="mt-2 space-y-2">
              {jobs.data.items.map((job) => (
                <li key={job.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <StatusBadge tone={JOB_TONES[job.status]} label={humanize(job.status)} />
                  <span className="text-muted">
                    {humanize(job.trigger)} · <Timestamp value={job.created_at} />
                  </span>
                  <span className="text-muted">
                    {Object.entries(job.removed)
                      .filter(([, value]) => Number(value) > 0)
                      .map(([key, value]) => `${humanize(key)}: ${String(value)}`)
                      .join(", ") || "Nothing removed"}
                  </span>
                  {job.progress_note ? <span className="basis-full text-xs text-muted">{job.progress_note}</span> : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="max-w-[72ch] text-xs text-muted">
          Retention does not reach backups, downloaded exports or reports, or webhook payloads already delivered. Removed evidence leaves a tombstone so runs, change
          sets and AI citations say it expired instead of breaking silently.
        </p>
      </div>
    </Panel>
  );
}

export function StixExportPanel() {
  const { apiBase } = useCase();
  const [sourceUrls, setSourceUrls] = useState(false);
  const [unreviewed, setUnreviewed] = useState(true);
  const [aiSuggestions, setAiSuggestions] = useState(false);
  const query = new URLSearchParams({
    include_source_urls: String(sourceUrls),
    include_unreviewed: String(unreviewed),
    include_ai_suggestions: String(aiSuggestions),
  }).toString();
  const report = useResource<StixReport>(`${apiBase}/exports/stix/report?${query}`);

  return (
    <div className="space-y-3">
      <SubHeading className="flex items-center gap-2">
        <Share2 aria-hidden="true" className="size-4 text-muted" />
        STIX 2.1 bundle
      </SubHeading>
      <p className="max-w-[72ch] text-sm text-muted">
        A subset for exchange with threat intelligence tools: domains, addresses, URLs, email addresses, platform accounts, organizations, relationships and observed
        data, with a Tracehollow provenance extension. It is not a full STIX implementation and attributes nothing.
      </p>
      <FieldGroup legend="Include">
        <div className="flex flex-col gap-2">
          <ChoiceField label="Relationships not yet reviewed" description="Marked as unreviewed in the provenance extension." checked={unreviewed} onChange={(event) => setUnreviewed(event.target.checked)} />
          <ChoiceField label="Source URLs of evidence" description="Off by default: URLs can reveal what was searched." checked={sourceUrls} onChange={(event) => setSourceUrls(event.target.checked)} />
          <ChoiceField label="AI-suggested relationships" description="Off by default. Included ones are labelled as suggestions." checked={aiSuggestions} onChange={(event) => setAiSuggestions(event.target.checked)} />
        </div>
      </FieldGroup>
      {report.state === "error" ? <ErrorNotice error={report.error} onRetry={() => void report.reload()} /> : null}
      {report.data ? (
        <div className="space-y-2 rounded-md border border-line bg-sunken p-3 text-sm" aria-live="polite">
          <p className="text-ink">
            {plural(report.data.objects, "STIX object")}:{" "}
            {Object.entries(report.data.counts)
              .map(([key, value]) => `${value} ${key}`)
              .join(", ") || "none"}
          </p>
          {Object.keys(report.data.excluded).length ? (
            <p className="text-muted">
              Not exported:{" "}
              {Object.entries(report.data.excluded)
                .map(([key, value]) => `${value} ${humanize(key).toLowerCase()}`)
                .join(", ")}
            </p>
          ) : null}
          {report.data.credential_values_removed ? <p className="text-warn">{plural(report.data.credential_values_removed, "value")} matching stored credentials removed.</p> : null}
          {report.data.truncated ? <p className="text-warn">The case exceeds the export size limit; the bundle is truncated.</p> : null}
          <details className="text-xs text-muted">
            <summary className="cursor-pointer">What is lost in STIX</summary>
            <ul className="mt-1 list-inside list-disc space-y-0.5">
              {report.data.lossy.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </details>
        </div>
      ) : (
        <LoadingState label="Counting what would be exported…" rows={1} />
      )}
      <ButtonLink href={`${apiBase}/exports/stix?${query}`} download external icon={Download}>
        Download STIX bundle
      </ButtonLink>
    </div>
  );
}

export function ViewerExportNotice() {
  return (
    <Notice tone="neutral" title="Exports are for analysts">
      Your role in this case is viewer. You can read the case and download single evidence files from their pages, but whole-case exports, reports and STIX bundles
      need analyst access.
    </Notice>
  );
}
