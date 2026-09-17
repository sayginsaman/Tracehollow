"use client";

import { Download, Eye, FileOutput, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { apiRequest } from "@/lib/client-api";
import { describeError } from "@/lib/messages";
import { useResource, useSession } from "@/lib/session-context";
import type { ReportPreview } from "@/lib/workspace-types";

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
  TextInput,
  cn,
  formatBytes,
  humanize,
} from "../ui";
import { ViewerExportNotice } from "./CasePolicies";
import { useCase } from "./CaseContext";

interface SelectableItem {
  id: string;
  label: string;
  detail: string;
}

interface Selectable {
  entities: SelectableItem[];
  relationships: SelectableItem[];
  evidence: SelectableItem[];
  ai_answers: SelectableItem[];
  notes: SelectableItem[];
}

export interface ReportForm {
  title: string;
  includePurpose: boolean;
  entityIds: string[];
  relationshipIds: string[];
  evidenceIds: string[];
  aiAnswerIds: string[];
  noteIds: string[];
  compareEntityIds: string[];
  includeTimeline: boolean;
  includeCoverage: boolean;
  redactTerms: string;
  redactIdentifierTypes: string[];
}

export const EMPTY_REPORT: ReportForm = {
  title: "",
  includePurpose: true,
  entityIds: [],
  relationshipIds: [],
  evidenceIds: [],
  aiAnswerIds: [],
  noteIds: [],
  compareEntityIds: [],
  includeTimeline: false,
  includeCoverage: true,
  redactTerms: "",
  redactIdentifierTypes: [],
};

export const REDACTABLE_IDENTIFIER_TYPES = ["phone", "email", "name", "username", "ip", "url", "platform_id"];

/** The API request body for a report form; one redaction term per line. */
export function reportRequest(form: ReportForm): Record<string, unknown> {
  return {
    title: form.title.trim() || null,
    include_case_purpose: form.includePurpose,
    entity_ids: form.entityIds,
    relationship_ids: form.relationshipIds,
    evidence_ids: form.evidenceIds,
    ai_message_ids: form.aiAnswerIds,
    note_ids: form.noteIds,
    comparison_entity_ids: form.compareEntityIds.length >= 2 ? form.compareEntityIds : [],
    include_timeline: form.includeTimeline,
    include_coverage: form.includeCoverage,
    redact_terms: form.redactTerms
      .split("\n")
      .map((term) => term.trim())
      .filter((term) => term.length >= 2),
    redact_identifier_types: form.redactIdentifierTypes,
  };
}

function toggle(values: string[], id: string, max?: number): string[] {
  if (values.includes(id)) return values.filter((value) => value !== id);
  if (max !== undefined && values.length >= max) return values;
  return [...values, id];
}

function Picker({
  legend,
  items,
  selected,
  onChange,
  max,
  empty,
  tone,
}: {
  legend: string;
  items: SelectableItem[];
  selected: string[];
  onChange: (values: string[]) => void;
  max?: number;
  empty: string;
  tone?: "ai";
}) {
  const [filter, setFilter] = useState("");
  const shown = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase("tr");
    return needle ? items.filter((item) => `${item.label} ${item.detail}`.toLocaleLowerCase("tr").includes(needle)) : items;
  }, [items, filter]);
  return (
    <fieldset className={cn("min-w-0 rounded-md border border-line", tone === "ai" && "border-ai-line")}>
      <legend className="ml-2 px-1 text-sm font-medium text-ink">{legend}</legend>
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 pt-1">
        <p className="text-xs text-muted" aria-live="polite">
          {selected.length > 0 ? `${selected.length} selected` : "None selected"}
        </p>
        {selected.length > 0 ? (
          <button type="button" onClick={() => onChange([])} className="rounded text-xs text-accent hover:underline">
            Clear
          </button>
        ) : null}
      </div>
      {items.length > 8 ? (
        <div className="px-3 pt-2">
          <label className="sr-only" htmlFor={`picker-filter-${legend}`}>
            Filter {legend.toLowerCase()}
          </label>
          <TextInput id={`picker-filter-${legend}`} type="search" placeholder="Filter" value={filter} onChange={(event) => setFilter(event.target.value)} className="h-8" />
        </div>
      ) : null}
      {items.length === 0 ? <p className="px-3 py-2 text-sm text-muted">{empty}</p> : null}
      <div className="max-h-52 space-y-2 overflow-y-auto px-3 py-2.5">
        {shown.map((item) => (
          <ChoiceField
            key={item.id}
            checked={selected.includes(item.id)}
            disabled={max !== undefined && !selected.includes(item.id) && selected.length >= max}
            onChange={() => onChange(toggle(selected, item.id, max))}
            label={item.label}
            description={item.detail}
          />
        ))}
      </div>
    </fieldset>
  );
}

export function ReportBuilder() {
  const { can } = useCase();
  if (!can("exports.create")) {
    return (
      <div className="space-y-6">
        <PageHeader title="Reports" description="Build a single HTML file from the records you choose, redact it, check the exact file in a preview, then download it." />
        <ViewerExportNotice />
      </div>
    );
  }
  return <ReportBuilderForm />;
}

function ReportBuilderForm() {
  const { apiBase, caseDetail } = useCase();
  const { session, handleAuthError } = useSession();
  const selectable = useResource<Selectable>(`${apiBase}/reports/selectable`);
  const [form, setForm] = useState<ReportForm>(EMPTY_REPORT);
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"preview" | "download" | null>(null);

  function update(patch: Partial<ReportForm>) {
    setForm((current) => ({ ...current, ...patch }));
    setPreview(null);
  }

  async function makePreview() {
    setBusy("preview");
    setError(null);
    try {
      setPreview(
        await apiRequest<ReportPreview>(`${apiBase}/reports/html/preview`, {
          method: "POST",
          body: reportRequest(form),
          csrfToken: session.csrf_token,
        }),
      );
    } catch (caught) {
      if (!handleAuthError(caught)) setError(describeError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function download() {
    setBusy("download");
    setError(null);
    try {
      const response = await fetch(`${apiBase}/reports/html`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token, Accept: "text/html" },
        body: JSON.stringify(reportRequest(form)),
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") ?? "";
      const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "tracehollow-report.html";
      const url = URL.createObjectURL(new Blob([blob], { type: "application/octet-stream" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("The report could not be downloaded. Check the preview for problems and try again.");
    } finally {
      setBusy(null);
    }
  }

  const selectedCount =
    form.entityIds.length + form.relationshipIds.length + form.evidenceIds.length + form.aiAnswerIds.length + form.noteIds.length + form.compareEntityIds.length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Reports"
        description="Build a single HTML file from the records you choose, redact it, check the exact file in a preview, then download it."
      />

      {selectable.state === "error" && !selectable.data ? <ErrorNotice error={selectable.error} onRetry={() => void selectable.reload()} /> : null}
      {!selectable.data && selectable.state !== "error" ? <LoadingState label="Loading case records…" rows={6} /> : null}

      {selectable.data ? (
        <>
          <Panel
            title="Build a report"
            description="Nothing is included unless you select it. It is generated on demand and not stored or published."
          >
            <div className="space-y-5">
              <div className="grid gap-5 md:grid-cols-2">
                <Field label="Report title" htmlFor="report-title" hint={`Defaults to the case title: ${caseDetail.title}`}>
                  <TextInput id="report-title" value={form.title} maxLength={200} onChange={(event) => update({ title: event.target.value })} />
                </Field>
                <FieldGroup legend="Case context">
                  <div className="space-y-2">
                    <ChoiceField checked={form.includePurpose} onChange={(event) => update({ includePurpose: event.target.checked })} label="Case purpose and scope" />
                    <ChoiceField
                      checked={form.includeCoverage}
                      onChange={(event) => update({ includeCoverage: event.target.checked })}
                      label="Collection dates and coverage gaps"
                      description="Failed, partial or blocked collections and processing jobs."
                    />
                    <ChoiceField checked={form.includeTimeline} onChange={(event) => update({ includeTimeline: event.target.checked })} label="Timeline" />
                  </div>
                </FieldGroup>
              </div>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                <Picker legend="Entities" items={selectable.data.entities} selected={form.entityIds} onChange={(entityIds) => update({ entityIds })} max={50} empty="No entities." />
                <Picker
                  legend="Relationships"
                  items={selectable.data.relationships}
                  selected={form.relationshipIds}
                  onChange={(relationshipIds) => update({ relationshipIds })}
                  max={100}
                  empty="No relationships."
                />
                <Picker legend="Evidence excerpts" items={selectable.data.evidence} selected={form.evidenceIds} onChange={(evidenceIds) => update({ evidenceIds })} max={100} empty="No evidence." />
                <Picker
                  legend="Compare entities (2-4)"
                  items={selectable.data.entities}
                  selected={form.compareEntityIds}
                  onChange={(compareEntityIds) => update({ compareEntityIds })}
                  max={4}
                  empty="No entities."
                />
                <Picker
                  legend="AI-generated answers"
                  items={selectable.data.ai_answers}
                  selected={form.aiAnswerIds}
                  onChange={(aiAnswerIds) => update({ aiAnswerIds })}
                  max={20}
                  empty="No AI answers."
                  tone="ai"
                />
                <Picker legend="Analyst notes" items={selectable.data.notes} selected={form.noteIds} onChange={(noteIds) => update({ noteIds })} max={100} empty="No notes." />
              </div>
              {form.compareEntityIds.length === 1 ? <Notice tone="warn">Select at least two entities to include a comparison.</Notice> : null}
            </div>
          </Panel>

          <Panel
            title="Redaction"
            description="Applied to every text in the report. Strings shaped like credentials are always removed, and stored credentials and sessions are never read."
          >
            <div className="grid gap-5 md:grid-cols-2">
              <Field label="Terms to redact" htmlFor="report-redact-terms" hint="One per line; matched case-insensitively. Terms shorter than two characters are ignored.">
                <TextArea id="report-redact-terms" rows={5} value={form.redactTerms} onChange={(event) => update({ redactTerms: event.target.value })} />
              </Field>
              <FieldGroup legend="Redact every known value of these identifier types">
                <div className="grid grid-cols-2 gap-2">
                  {REDACTABLE_IDENTIFIER_TYPES.map((type) => (
                    <ChoiceField
                      key={type}
                      checked={form.redactIdentifierTypes.includes(type)}
                      onChange={() => update({ redactIdentifierTypes: toggle(form.redactIdentifierTypes, type) })}
                      label={humanize(type)}
                    />
                  ))}
                </div>
              </FieldGroup>
            </div>
          </Panel>

          <Panel
            title="Review and download"
            description={`${selectedCount} record${selectedCount === 1 ? "" : "s"} selected. The download stays disabled until the current selection has been previewed.`}
            actions={
              <>
                <Button icon={Eye} onClick={() => void makePreview()} disabled={busy !== null} busy={busy === "preview"}>
                  Preview report
                </Button>
                <Button variant="primary" icon={Download} onClick={() => void download()} disabled={busy !== null || preview === null} busy={busy === "download"}>
                  Download HTML
                </Button>
              </>
            }
          >
            <div className="space-y-4">
              <ActionError message={error} />
              <p className="flex items-start gap-2 text-sm text-muted">
                <ShieldCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                The file contains no scripts and loads nothing when opened. Citations link to excerpts bundled in the file, so they work offline;
                AI-generated content and uncertainty labels are kept.
              </p>
              {preview === null ? (
                <div className="flex flex-col items-start gap-2 rounded-md bg-sunken px-4 py-6 text-sm text-muted">
                  <FileOutput aria-hidden="true" className="size-5" />
                  Preview the report to review exactly what it contains before downloading.
                </div>
              ) : (
                <div className="space-y-3">
                  <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink">
                    {Object.entries(preview.counts).map(([key, value]) => (
                      <li key={key}>
                        {humanize(key)}: {value}
                      </li>
                    ))}
                    <li className="text-muted">Size: {formatBytes(preview.size_bytes)}</li>
                    <li className="text-muted">Redactions: {preview.redactions_applied}</li>
                    <li className="text-muted">Credential-like values removed: {preview.credential_like_values_removed}</li>
                  </ul>
                  {preview.warnings.map((warning) => (
                    <Notice key={warning} tone="warn">
                      {warning}
                    </Notice>
                  ))}
                  <iframe
                    title="Report preview"
                    sandbox=""
                    srcDoc={preview.html}
                    referrerPolicy="no-referrer"
                    className="h-[44rem] w-full rounded-md border border-line bg-surface"
                  />
                </div>
              )}
            </div>
          </Panel>
        </>
      ) : null}
    </div>
  );
}
